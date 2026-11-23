"""Qt Widgets player shared by the maker preview and exported game."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, QRectF, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .engine import StoryEngine, StoryError
from .project import resolve_asset_path
from .saves import list_save_slots, load_game, save_game
from .text_pages import grapheme_ends


class Stage(QWidget):
    """Paint the 1920×1080 story canvas without stretching source images."""

    clicked = Signal()

    def __init__(self, game: dict, root: Path):
        super().__init__()
        self.game = game
        self.root = root
        self.assets = {asset["id"]: asset for asset in game["assets"]}
        self.images: dict[str, QImage] = {}
        self.presentation: dict = {}
        self.setMinimumSize(480, 270)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def image(self, asset_id: str) -> QImage:
        if asset_id not in self.images:
            asset = self.assets[asset_id]
            path = resolve_asset_path(self.root, asset["path"])
            self.images[asset_id] = QImage(str(path))
        return self.images[asset_id]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        scale = min(self.width() / 1920, self.height() / 1080)
        painter.translate((self.width() - 1920 * scale) / 2, (self.height() - 1080 * scale) / 2)
        painter.scale(scale, scale)
        background_id = self.presentation.get("background_asset_id")
        if background_id:
            image = self.image(background_id)
            if not image.isNull():
                factor = min(1920 / image.width(), 1080 / image.height())
                width, height = image.width() * factor, image.height() * factor
                painter.drawImage(QRectF((1920 - width) / 2, (1080 - height) / 2, width, height), image)
        defaults = {"left": (480, 1), "center": (960, 2), "right": (1440, 3)}
        sprites = self.presentation.get("sprites", {})
        ordered = sorted(
            sprites.items(),
            key=lambda pair: (
                (pair[1].get("placement") or {}).get("z", defaults[pair[0]][1]),
                list(defaults).index(pair[0]),
            ),
        )
        for slot, sprite in ordered:
            image = self.image(sprite["asset_id"])
            if image.isNull():
                continue
            placement = sprite.get("placement") or {}
            factor = (864 / image.height()) * placement.get("scale_pct", 100) / 100
            width, height = image.width() * factor, image.height() * factor
            x = placement.get("x", defaults[slot][0])
            y = placement.get("y", 1040)
            painter.drawImage(QRectF(x - width / 2, y - height, width, height), image)


def _theme_image(game: dict, root: Path, part: str) -> QImage:
    image_id = game["theme"][part]["image_asset_id"]
    if image_id is None:
        return QImage()
    asset = next(asset for asset in game["assets"] if asset["id"] == image_id)
    return QImage(str(resolve_asset_path(root, asset["path"])))


def _paint_theme(painter: QPainter, bounds: QRectF, style: dict, image: QImage) -> None:
    painter.fillRect(bounds, QColor(style["background_color"]))
    if not image.isNull():
        factor = min(bounds.width() / image.width(), bounds.height() / image.height())
        width, height = image.width() * factor, image.height() * factor
        painter.drawImage(QRectF(
            bounds.x() + (bounds.width() - width) / 2,
            bounds.y() + (bounds.height() - height) / 2,
            width, height,
        ), image)


class ThemedFrame(QFrame):
    clicked = Signal()

    def __init__(self, game: dict, root: Path, part: str):
        super().__init__()
        self.theme_style = game["theme"][part]
        self.image = _theme_image(game, root, part)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        _paint_theme(painter, QRectF(self.rect()), self.theme_style, self.image)


class ThemedButton(QPushButton):
    def __init__(self, text: str, game: dict, root: Path, part: str):
        super().__init__(text)
        self.theme_style = game["theme"][part]
        self.image = _theme_image(game, root, part)
        self.setMinimumHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = QRectF(self.rect())
        _paint_theme(painter, bounds, self.theme_style, self.image)
        if self._hovered and self.isEnabled():
            painter.fillRect(bounds, QColor(255, 255, 255, 28))
        border = QColor("#67B3E8") if self._hovered else QColor(255, 255, 255, 70)
        painter.setPen(border)
        painter.drawRect(bounds.adjusted(0.5, 0.5, -0.5, -0.5))
        color = QColor(self.theme_style["text_color"])
        if not self.isEnabled():
            color.setAlpha(110)
        painter.setPen(color)
        font = painter.font()
        font.setPixelSize(self.theme_style["font_size_px"])
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class PlayerWindow(QMainWindow):
    def __init__(
        self,
        game: dict,
        root: Path,
        start_node_id: str | None = None,
        initial_variables: dict[str, int | bool] | None = None,
        auto_start: bool = False,
    ):
        super().__init__()
        self.game = game
        self.root = root
        self.start_node_id = start_node_id
        self.initial_variables = initial_variables
        self.engine: StoryEngine | None = None
        self.setWindowTitle(game["title"])
        self.resize(1200, 720)
        self.assets = {asset["id"]: asset for asset in game["assets"]}
        self.characters = {character["id"]: character["name"] for character in game["characters"]}
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)

        title = QWidget()
        title.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            "stop:0 #111625, stop:0.6 #1B2438, stop:1 #0E121D);"
        )
        title_layout = QVBoxLayout(title)
        title_layout.setSpacing(16)
        title_layout.addStretch(2)
        heading = QLabel(game["title"])
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet(
            "font-size: 46px; font-weight: bold; color: #F4F7FB; background: transparent; padding-bottom: 18px;"
        )
        title_layout.addWidget(heading)
        for label, action in (
            ("开始游戏", self.start_new_game),
            ("读取存档", self.show_title_load),
            ("退出", self.close),
        ):
            row = QHBoxLayout()
            row.addStretch()
            button = ThemedButton(label, game, root, "menu")
            button.setFixedWidth(300)
            button.setMinimumHeight(46)
            button.clicked.connect(action)
            row.addWidget(button)
            row.addStretch()
            title_layout.addLayout(row)
        title_layout.addStretch(3)
        self.pages.addWidget(title)

        story = QWidget()
        story_layout = QVBoxLayout(story)
        story_layout.setContentsMargins(0, 0, 0, 0)
        story_layout.setSpacing(0)
        self.stage = Stage(game, root)
        self.stage.clicked.connect(self.advance)
        story_layout.addWidget(self.stage, 1)

        # Visual-novel overlay directly on top of the full-window 16:9 Stage
        overlay = QVBoxLayout(self.stage)
        overlay.setContentsMargins(36, 24, 36, 20)
        overlay.setSpacing(12)
        overlay.addStretch(3)

        self.choice_area = QWidget()
        self.choice_area.setStyleSheet("background: transparent;")
        self.choice_area.setMaximumWidth(720)
        self.choice_layout = QVBoxLayout(self.choice_area)
        self.choice_layout.setSpacing(10)
        choice_row = QHBoxLayout()
        choice_row.addStretch()
        choice_row.addWidget(self.choice_area, 1)
        choice_row.addStretch()
        overlay.addLayout(choice_row)
        overlay.addStretch(1)

        self.dialogue_panel = ThemedFrame(game, root, "dialogue")
        self.dialogue_panel.clicked.connect(self.advance)
        self.dialogue_panel.setMinimumHeight(185)
        dialogue_layout = QVBoxLayout(self.dialogue_panel)
        dialogue_layout.setContentsMargins(24, 16, 24, 12)
        dialogue_layout.setSpacing(6)
        self.speaker = QLabel()
        self.speaker.setTextFormat(Qt.TextFormat.PlainText)
        dialogue_layout.addWidget(self.speaker)
        self.text = QLabel()
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.text.setMinimumHeight(86)
        dialogue_layout.addWidget(self.text, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()
        self.advance_button = ThemedButton("继续", game, root, "menu")
        self.advance_button.setFixedSize(92, 32)
        self.advance_button.clicked.connect(self.advance)
        actions.addWidget(self.advance_button)
        self.save_button = ThemedButton("保存", game, root, "menu")
        self.save_button.setFixedSize(82, 32)
        self.save_button.clicked.connect(self.show_save_menu)
        actions.addWidget(self.save_button)
        load_button = ThemedButton("读取", game, root, "menu")
        load_button.setFixedSize(82, 32)
        load_button.clicked.connect(self.show_load_menu)
        actions.addWidget(load_button)
        title_button = ThemedButton("返回标题", game, root, "menu")
        title_button.setFixedSize(112, 32)
        title_button.clicked.connect(self.return_to_title)
        actions.addWidget(title_button)
        dialogue_layout.addLayout(actions)
        overlay.addWidget(self.dialogue_panel)
        self.pages.addWidget(story)

        self.video = QVideoWidget()
        self.pages.addWidget(self.video)
        self.current_bgm_asset_id: str | None = None
        self.current_credits_asset_id: str | None = None
        self.bgm = QMediaPlayer(self)
        bgm_output = QAudioOutput(self.bgm)
        self.bgm.setAudioOutput(bgm_output)
        self.bgm.errorOccurred.connect(self.bgm_error)
        self.credits = QMediaPlayer(self)
        credits_output = QAudioOutput(self.credits)
        self.credits.setAudioOutput(credits_output)
        self.credits.setVideoOutput(self.video)
        self.credits.mediaStatusChanged.connect(self.video_status_changed)
        self.credits.errorOccurred.connect(self.media_error)
        self.effects: list[QMediaPlayer] = []
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.reveal_character)
        self.revealed = 0
        self.current_text = ""
        self.apply_theme()
        if auto_start:
            self.start_new_game()

    def asset_url(self, asset_id: str) -> QUrl:
        path = resolve_asset_path(self.root, self.assets[asset_id]["path"])
        return QUrl.fromLocalFile(str(path))

    def apply_theme(self):
        theme = self.game["theme"]
        for widget, part in ((self.text, "dialogue"), (self.speaker, "dialogue")):
            style = theme[part]
            widget.setStyleSheet(
                f"color: {style['text_color']}; background-color: transparent; "
                f"font-size: {style['font_size_px']}px;"
            )

    def dialogue_page_capacity(self) -> int:
        """Return max visible graphemes per page, bounded by 80 and dialog box capacity."""
        font_size = self.game["theme"]["dialogue"]["font_size_px"]
        width = max(self.text.width(), self.width() - 60, 320)
        height = max(self.text.height(), self.text.minimumHeight(), 100)
        font = self.text.font()
        font.setPixelSize(font_size)
        metrics = QFontMetrics(font)
        char_width = max(metrics.horizontalAdvance("汉"), font_size)
        line_height = max(metrics.lineSpacing(), int(font_size * 1.25))
        cols = max(1, width // char_width)
        rows = max(1, height // line_height)
        return min(80, max(1, cols * rows))

    def keyPressEvent(self, event):
        if (
            self.pages.currentIndex() == 1
            and event.key() in {Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter}
        ):
            self.advance()
            return
        super().keyPressEvent(event)

    def start_new_game(self):
        self.bgm.stop()
        self.credits.stop()
        self.current_bgm_asset_id = None
        self.current_credits_asset_id = None
        self.engine = StoryEngine(
            self.game,
            start_node_id=self.start_node_id,
            initial_variables=self.initial_variables,
        )
        self.pages.setCurrentIndex(1)
        self.refresh()

    def return_to_title(self):
        self.timer.stop()
        self.bgm.stop()
        self.credits.stop()
        self.current_bgm_asset_id = None
        self.current_credits_asset_id = None
        for effect in self.effects:
            effect.stop()
            effect.deleteLater()
        self.effects.clear()
        self.pages.setCurrentIndex(0)

    def _action(self, operation, reveal_immediately: bool = False):
        try:
            operation()
            self.refresh(reveal_immediately=reveal_immediately)
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, "无法继续", str(error))

    def advance(self):
        if self.engine is None or self.engine.phase != "waiting_advance":
            return
        clusters = grapheme_ends(self.current_text)
        if self.revealed < len(clusters):
            self.revealed = len(clusters)
            self.timer.stop()
            self.text.setText(self.current_text)
            return
        capacity = self.dialogue_page_capacity()
        self._action(lambda: self.engine.advance(max_chars=capacity))

    def choose(self, choice_id: str):
        self._action(lambda: self.engine.select(choice_id))

    def reveal_character(self):
        boundaries = grapheme_ends(self.current_text)
        self.revealed += 1
        if self.revealed >= len(boundaries):
            self.timer.stop()
            self.text.setText(self.current_text)
        else:
            self.text.setText(self.current_text[:boundaries[self.revealed - 1]])

    def refresh(self, reveal_immediately: bool = False):
        if self.engine is None:
            return
        self.play_commands()
        self.stage.presentation = self.engine.presentation
        self.stage.update()
        while self.choice_layout.count():
            item = self.choice_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        phase = self.engine.phase
        self.timer.stop()
        self.advance_button.setEnabled(phase == "waiting_advance")
        self.save_button.setEnabled(phase in {"waiting_advance", "waiting_choice", "stalled"})
        if phase == "waiting_advance":
            item = self.engine.nodes[self.engine.node_id]["items"][self.engine.item_index]
            self.speaker.setText(self.characters.get(item.get("character_id"), ""))
            self.current_text = self.engine.current_page(max_chars=self.dialogue_page_capacity())
            if reveal_immediately:
                self.revealed = len(grapheme_ends(self.current_text))
                self.text.setText(self.current_text)
            else:
                self.revealed = 0
                self.text.clear()
                self.timer.start()
        else:
            self.speaker.clear()
            if self.engine.error:
                self.text.setText("没有可进入的后继")
            elif phase == "stalled" and not self.engine.nodes[self.engine.node_id].get("choices"):
                self.text.setText("已到达当前分支终点（该节点未设为结局，可返回标题）")
            else:
                self.text.setText("")
        if phase in {"waiting_choice", "stalled"}:
            choices = self.engine.nodes[self.engine.node_id].get("choices", [])
            for choice in choices:
                button = ThemedButton(choice["text"], self.game, self.root, "choice")
                button.clicked.connect(
                    lambda checked=False, selected=choice["id"]: self.choose(selected)
                )
                self.choice_layout.addWidget(button)
            self.choice_area.setVisible(bool(choices))
        else:
            self.choice_area.hide()
        if phase == "finished":
            self.text.setText("故事结束，可返回标题")

    def _asset_label(self, asset_id: str | None) -> str:
        if asset_id and asset_id in self.assets:
            return self.assets[asset_id]["path"]
        return asset_id or "未知素材"

    def play_commands(self):
        for command in self.engine.take_commands():
            kind = command["type"]
            if kind == "play_bgm":
                self.current_bgm_asset_id = command["asset_id"]
                self.bgm.setSource(self.asset_url(command["asset_id"]))
                self.bgm.setLoops(
                    QMediaPlayer.Loops.Infinite if command["loop"] else QMediaPlayer.Loops.Once
                )
                self.bgm.audioOutput().setVolume(command["volume_pct"] / 100)
                self.bgm.play()
            elif kind == "stop_bgm":
                self.current_bgm_asset_id = None
                self.bgm.stop()
            elif kind == "play_sfx":
                asset_id = command["asset_id"]
                effect = QMediaPlayer(self)
                output = QAudioOutput(effect)
                output.setVolume(command["volume_pct"] / 100)
                effect.setAudioOutput(output)
                effect.setSource(self.asset_url(asset_id))
                self.effects.append(effect)
                effect.mediaStatusChanged.connect(
                    lambda status, player=effect: self.remove_effect(player, status)
                )
                effect.errorOccurred.connect(
                    lambda err, detail, aid=asset_id: self.sfx_error(aid, detail)
                )
                effect.play()
            elif kind == "play_credits":
                self.bgm.stop()
                self.current_bgm_asset_id = None
                self.current_credits_asset_id = command["asset_id"]
                self.credits.setSource(self.asset_url(command["asset_id"]))
                self.pages.setCurrentWidget(self.video)
                self.credits.play()

    def remove_effect(self, player: QMediaPlayer, status):
        if status in {QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia}:
            if player in self.effects:
                self.effects.remove(player)
                player.deleteLater()

    def bgm_error(self, error, detail):
        QMessageBox.warning(
            self, "背景音乐播放失败",
            f"素材：{self._asset_label(self.current_bgm_asset_id)}\n{detail}",
        )

    def sfx_error(self, asset_id: str, detail: str):
        QMessageBox.warning(
            self, "音效播放失败",
            f"素材：{self._asset_label(asset_id)}\n{detail}",
        )

    def video_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.engine:
            if self.engine.phase == "playing_credits":
                self.engine.media_finished()
                self.pages.setCurrentIndex(1)
                self.refresh()

    def media_error(self, error, detail):
        QMessageBox.warning(
            self, "片尾播放失败",
            f"素材：{self._asset_label(self.current_credits_asset_id)}\n{detail}",
        )
        if self.engine and self.engine.phase == "playing_credits":
            self.engine.media_finished()
            self.pages.setCurrentIndex(1)
            self.refresh()

    def _slot_label(self, info: dict) -> str:
        if not info["exists"]:
            return f"档位 {info['slot']} · 空"
        if not info["compatible"]:
            return f"档位 {info['slot']} · {info['error'] or '无法读取'}"
        node_name = info["node_name"] or "未知节点"
        saved_at = (info["saved_at"] or "")[:19].replace("T", " ")
        return f"档位 {info['slot']} · {node_name} · {saved_at}"

    def show_save_menu(self):
        menu = QMenu(self)
        for info in list_save_slots(self.game):
            slot = info["slot"]
            menu.addAction(
                self._slot_label(info),
                lambda checked=False, selected=slot: self._action(
                    lambda: save_game(self.engine, self.game, selected)
                ),
            )
        menu.exec(self.save_button.mapToGlobal(self.save_button.rect().bottomLeft()))

    def show_load_menu(self):
        menu = QMenu(self)
        for info in list_save_slots(self.game):
            slot = info["slot"]
            action = menu.addAction(
                self._slot_label(info),
                lambda checked=False, selected=slot: self._action(
                    lambda: self.load_slot(selected), reveal_immediately=True
                ),
            )
            action.setEnabled(info["exists"])
        menu.exec(self.mapToGlobal(self.rect().center()))

    def show_title_load(self):
        self.show_load_menu()

    def load_slot(self, slot: int):
        self.bgm.stop()
        self.credits.stop()
        replacement = load_game(self.game, slot)
        self.engine = replacement
        self.pages.setCurrentIndex(1)


def main() -> int:
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    with (root / "game.json").open(encoding="utf-8") as stream:
        game = json.load(stream)
    app = QApplication(sys.argv)
    window = PlayerWindow(game, root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""Maker-side import, search, preview, and reference-safe removal of media."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .project import import_asset, resolve_asset_path


EXTENSION_KIND = {
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".txt": "text", ".mp3": "bgm", ".wav": "sfx", ".mp4": "video",
}


def asset_references(project: dict, asset_id: str) -> list[str]:
    references = []
    for character in project["characters"]:
        if character.get("reference_art_id") == asset_id:
            references.append(f"角色 {character['name']} 设定立绘")
    for document in project.get("documents", []):
        if document.get("source_asset_id") == asset_id:
            references.append(f"资料 {document['title']}")
    for node in project["nodes"]:
        for item in node["items"]:
            if item.get("asset_id") == asset_id:
                references.append(f"节点 {node['name']} 演出")
        if (node.get("ending") or {}).get("credits_video_asset_id") == asset_id:
            references.append(f"节点 {node['name']} 片尾")
    for part, style in project.get("theme", {}).items():
        if isinstance(style, dict) and style.get("image_asset_id") == asset_id:
            references.append(f"主题 {part}")
    return references


class AssetPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.player = QMediaPlayer(self)
        audio = QAudioOutput(self.player)
        self.player.setAudioOutput(audio)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(["全部", "image", "text", "bgm", "sfx", "video"])
        self.kind.currentTextChanged.connect(self.refresh)
        controls.addWidget(self.kind)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索素材名称")
        self.query.textChanged.connect(self.refresh)
        controls.addWidget(self.query, 1)
        for caption, action in (
            ("导入", self.import_file), ("移除", self.remove_asset), ("播放", self.play),
        ):
            button = QPushButton(caption)
            button.clicked.connect(action)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.preview)
        layout.addWidget(self.list, 1)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(180)
        layout.addWidget(self.image)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(180)
        self.video.hide()
        self.player.setVideoOutput(self.video)
        layout.addWidget(self.video)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)

    def selected_asset(self) -> dict | None:
        row = self.list.currentItem()
        if row is None or self.window.project is None:
            return None
        asset_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (asset for asset in self.window.project["assets"] if asset["id"] == asset_id), None
        )

    def refresh(self, *_):
        selected = self.selected_asset()
        selected_id = selected["id"] if selected else None
        self.list.blockSignals(True)
        self.list.clear()
        if self.window.project:
            for asset in self.window.project["assets"]:
                if self.kind.currentText() != "全部" and asset["kind"] != self.kind.currentText():
                    continue
                if self.query.text().casefold() not in asset["name"].casefold():
                    continue
                row = QListWidgetItem(f"{asset['name']} · {asset['kind']}")
                row.setData(Qt.ItemDataRole.UserRole, asset["id"])
                self.list.addItem(row)
                if asset["id"] == selected_id:
                    self.list.setCurrentItem(row)
        self.list.blockSignals(False)
        self.preview()

    def import_file(self):
        if not self.window.require_project():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "导入素材")
        if not filename:
            return
        source = Path(filename)
        suggested = EXTENSION_KIND.get(source.suffix.lower())
        if suggested is None:
            QMessageBox.warning(self, "格式", "首版支持 PNG、JPEG、TXT、MP3、WAV、MP4")
            return
        kinds = ["image", "text", "bgm", "sfx", "video"]
        selected, accepted = QInputDialog.getItem(
            self, "素材用途", "类型", kinds, kinds.index(suggested), editable=False,
        )
        if not accepted:
            return
        name, accepted = QInputDialog.getText(self, "素材名称", "名称", text=source.stem)
        if not accepted or not name.strip():
            return
        try:
            record = import_asset(
                self.window.root, self.window.project, source, selected, name.strip(),
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导入失败", str(error))
            return
        self.window.mark_dirty()
        self.refresh()
        for index in range(self.list.count()):
            row = self.list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == record["id"]:
                self.list.setCurrentItem(row)
                break

    def remove_asset(self):
        asset = self.selected_asset()
        if asset is None:
            return
        references = asset_references(self.window.project, asset["id"])
        if references:
            QMessageBox.warning(self, "素材仍在使用", "\n".join(references))
            return
        self.player.stop()
        self.window.project["assets"].remove(asset)
        self.window.mark_dirty()
        self.refresh()

    def preview(self, *_):
        self.player.stop()
        self.video.hide()
        self.image.clear()
        asset = self.selected_asset()
        if asset is None:
            self.details.clear()
            return
        path = resolve_asset_path(self.window.root, asset["path"])
        self.details.setText(f"{asset['name']}\n{asset['path']}\nSHA-256 {asset['sha256']}")
        if asset["kind"] == "image":
            pixmap = QPixmap(str(path))
            self.image.setPixmap(pixmap.scaled(
                640, 240, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        elif asset["kind"] == "text":
            try:
                self.details.setText(path.read_text(encoding="utf-8")[:1000])
            except OSError as error:
                self.details.setText(str(error))
        elif asset["kind"] == "video":
            self.video.show()

    def play(self):
        asset = self.selected_asset()
        if asset is None or asset["kind"] not in {"bgm", "sfx", "video"}:
            return
        path = resolve_asset_path(self.window.root, asset["path"])
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

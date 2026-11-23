"""Qt Widgets maker for editing a movable visual-novel project."""

from __future__ import annotations

import math
import sys
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGraphicsScene, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QTabWidget, QVBoxLayout, QWidget,
)

from .branch_check import BranchCheckCache, NodeCheckState, referenced_variable_ids
from .condition_parser import format_condition, parse_condition
from .editing import ProjectEditor
from .export import export_game
from .maker_assets import AssetPanel, AssetPickerDialog
from .maker_characters import CharacterPanel
from .maker_graph import InteractiveGraphView, StoryEdgeItem, StoryNodeItem
from .maker_style import MAKER_STYLESHEET
from .maker_text import TextPanel
from .maker_theme import ThemePanel
from .player import PlayerWindow
from .project import create_project, load_project, resolve_asset_path, save_project
from .runtime_data import compile_game

ITEM_TYPE_LABELS = {
    "dialogue": "对话",
    "narration": "旁白",
    "set_background": "设置背景",
    "show_sprite": "显示立绘",
    "hide_sprite": "隐藏立绘",
    "play_bgm": "播放背景音乐",
    "stop_bgm": "停止背景音乐",
    "play_sfx": "播放音效",
}


class LiveStagePreview(QWidget):
    """Real-time preview of the selected node's visual state up to the active step."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root: Path | None = None
        self.assets: dict[str, dict] = {}
        self.images: dict[tuple[str, str], QImage] = {}
        self.background_id: str | None = None
        self.sprites: dict[str, dict] = {}
        self.speaker: str = ""
        self.dialogue_text: str = ""
        self.choices: list[str] = []
        self.status_hint: str = "选择节点或演出条目即可实时预览画面"
        self.setMinimumSize(480, 270)

    def clear_cache(self):
        self.images.clear()

    def _load_image(self, asset_id: str) -> QImage:
        if self.root is None or asset_id not in self.assets:
            return QImage()
        asset = self.assets[asset_id]
        cache_key = (asset_id, asset.get("sha256", ""))
        if cache_key not in self.images:
            try:
                path = resolve_asset_path(self.root, asset["path"])
                self.images[cache_key] = QImage(str(path))
            except (OSError, ValueError):
                return QImage()
        return self.images[cache_key]

    def set_state(
        self,
        root: Path | None,
        project: dict | None,
        background_id: str | None,
        sprites: dict[str, dict],
        speaker: str = "",
        dialogue_text: str = "",
        status_hint: str = "",
        choices: list[str] | None = None,
    ):
        self.root = root
        self.assets = {a["id"]: a for a in project["assets"]} if project else {}
        self.background_id = background_id
        self.sprites = sprites
        self.speaker = speaker
        self.dialogue_text = dialogue_text
        self.choices = choices or []
        self.status_hint = status_hint
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#12151C"))
        scale = min(self.width() / 1920, self.height() / 1080)
        ox = (self.width() - 1920 * scale) / 2
        oy = (self.height() - 1080 * scale) / 2
        painter.save()
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        painter.fillRect(QRectF(0, 0, 1920, 1080), QColor("#1A1F2B"))

        if self.background_id:
            image = self._load_image(self.background_id)
            if not image.isNull():
                factor = min(1920 / image.width(), 1080 / image.height())
                w, h = image.width() * factor, image.height() * factor
                painter.drawImage(QRectF((1920 - w) / 2, (1080 - h) / 2, w, h), image)

        defaults = {"left": (480, 1), "center": (960, 2), "right": (1440, 3)}
        ordered = sorted(
            self.sprites.items(),
            key=lambda pair: (
                (pair[1].get("placement") or {}).get("z", defaults.get(pair[0], (960, 2))[1]),
                list(defaults).index(pair[0]) if pair[0] in defaults else 1,
            ),
        )
        for slot, sprite in ordered:
            image = self._load_image(sprite["asset_id"])
            if image.isNull():
                continue
            placement = sprite.get("placement") or {}
            factor = (864 / image.height()) * placement.get("scale_pct", 100) / 100
            w, h = image.width() * factor, image.height() * factor
            x = placement.get("x", defaults.get(slot, (960, 2))[0])
            y = placement.get("y", 1040)
            painter.drawImage(QRectF(x - w / 2, y - h, w, h), image)

        if self.dialogue_text or self.speaker:
            box = QRectF(120, 760, 1680, 260)
            painter.setBrush(QColor(18, 24, 38, 215))
            painter.setPen(QPen(QColor(145, 190, 235, 200), 4))
            painter.drawRoundedRect(box, 18, 18)
            font = painter.font()
            font.setPixelSize(38)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#8BD3FF"))
            if self.speaker:
                painter.drawText(QRectF(160, 780, 1600, 56), Qt.AlignmentFlag.AlignLeft, self.speaker)
            font.setBold(False)
            font.setPixelSize(34)
            painter.setFont(font)
            painter.setPen(QColor("#F4F7FB"))
            text_y = 845 if self.speaker else 800
            painter.drawText(
                QRectF(160, text_y, 1600, 160),
                Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft,
                self.dialogue_text,
            )
        if self.choices:
            font = painter.font()
            font.setPixelSize(32)
            painter.setFont(font)
            total_h = len(self.choices) * 92
            start_y = max(160.0, (720.0 - total_h) / 2)
            for idx, choice_text in enumerate(self.choices[:5]):
                rect = QRectF(560, start_y + idx * 92, 800, 72)
                painter.setBrush(QColor(32, 48, 74, 225))
                painter.setPen(QPen(QColor("#67B3E8"), 3))
                painter.drawRoundedRect(rect, 14, 14)
                painter.setPen(QColor("#F4F7FB"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, choice_text)
        painter.restore()

        if self.status_hint:
            hint_rect = QRectF(8, 6, self.width() - 16, 22)
            painter.setPen(QColor("#9BB0C9"))
            painter.drawText(hint_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.status_hint)


class LivePreviewWindow(QWidget):
    """Independent floating window for real-time story stage preview."""

    def __init__(self, maker: "MakerWindow"):
        super().__init__(maker, Qt.WindowType.Window)
        self.maker = maker
        self.setWindowTitle("实时画面预览 · GalGame Maker")
        self.resize(860, 540)
        self.setStyleSheet(MAKER_STYLESHEET)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.step_label = QLabel("未选中节点")
        self.step_label.setProperty("sectionTitle", True)
        top.addWidget(self.step_label, 1)
        prev_btn = QPushButton("◀ 上一步")
        prev_btn.clicked.connect(lambda: self.maker.step_live_preview(-1))
        top.addWidget(prev_btn)
        next_btn = QPushButton("下一步 ▶")
        next_btn.clicked.connect(lambda: self.maker.step_live_preview(1))
        top.addWidget(next_btn)
        layout.addLayout(top)
        self.stage = LiveStagePreview()
        layout.addWidget(self.stage, 1)


class ConditionDialog(QDialog):
    """Helper dialog for editing branch conditions with node choices and is/is not/not."""

    def __init__(
        self,
        title: str,
        initial: str,
        variables: list[dict],
        choices: list[dict],
        allow_empty: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 260)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "支持：choice is \"选项\"、choice is not \"选项\"、not、and、or、括号、"
            "变量比较（==, !=, >, >=, <, <=, is, is not）。"
            + ("单一无选项后继可留空表示自动衔接。" if allow_empty else "")
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.input = QLineEdit(initial)
        self.input.setPlaceholderText('例如：choice is "左" and not 已结束')
        layout.addWidget(self.input)

        if choices:
            choice_row = QHBoxLayout()
            choice_row.addWidget(QLabel("本节点选项："))
            for choice in choices:
                btn_is = QPushButton(f'is "{choice["text"]}"')
                btn_is.clicked.connect(
                    lambda _=False, t=choice["text"]: self._insert(f'choice is "{t}"')
                )
                choice_row.addWidget(btn_is)
                btn_not = QPushButton(f'is not "{choice["text"]}"')
                btn_not.clicked.connect(
                    lambda _=False, t=choice["text"]: self._insert(f'choice is not "{t}"')
                )
                choice_row.addWidget(btn_not)
            choice_row.addStretch()
            layout.addLayout(choice_row)

        if variables:
            var_row = QHBoxLayout()
            var_row.addWidget(QLabel("变量："))
            for var in variables[:8]:
                btn = QPushButton(f"{var['name']} ({var['type']})")
                snippet = f"{var['name']} is true" if var["type"] == "bool" else f"{var['name']} >= 1"
                btn.clicked.connect(lambda _=False, s=snippet: self._insert(s))
                var_row.addWidget(btn)
            var_row.addStretch()
            layout.addLayout(var_row)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("逻辑："))
        for op in ("and", "or", "not", "is", "is not", "(", ")"):
            btn = QPushButton(op)
            btn.clicked.connect(lambda _=False, tok=op: self._insert(tok))
            op_row.addWidget(btn)
        op_row.addStretch()
        layout.addLayout(op_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _insert(self, snippet: str):
        current = self.input.text()
        sep = " " if current and not current.endswith((" ", "(")) else ""
        self.input.setText(current + sep + snippet)
        self.input.setFocus()

    def expression(self) -> str:
        return self.input.text()


class BranchCheckWorker(QThread):
    """Run one time-budgeted Z3 branch-check batch off the UI thread."""

    batch_finished = Signal(dict, dict)

    def __init__(
        self,
        snapshot: dict,
        pair_cache: dict,
        node_ids: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.snapshot = snapshot
        self.pair_cache = pair_cache
        self.node_ids = node_ids

    def run(self):
        worker_cache = BranchCheckCache()
        worker_cache.load_pairs(self.pair_cache)
        states = worker_cache.check_batch(
            self.snapshot,
            node_ids=self.node_ids,
            pair_timeout_ms=2000,
            batch_budget_ms=10000,
        )
        self.batch_finished.emit(states, worker_cache.snapshot_pairs())


class MakerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.root: Path | None = None
        self.project: dict | None = None
        self.editor: ProjectEditor | None = None
        self.dirty = False
        self.preview_window: PlayerWindow | None = None
        self.branch_cache = BranchCheckCache()
        self._branch_worker: BranchCheckWorker | None = None
        self._branch_recheck_needed = False
        self.branch_check_timer = QTimer(self)
        self.branch_check_timer.setSingleShot(True)
        self.branch_check_timer.setInterval(300)
        self.branch_check_timer.timeout.connect(self._start_background_branch_check)
        self.setWindowTitle("GalGame Maker")
        self.resize(1360, 860)
        self.setStyleSheet(MAKER_STYLESHEET)
        self.live_preview_window = LivePreviewWindow(self)
        self.live_preview = self.live_preview_window.stage
        self._make_menu()
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._make_story_tab(), "剧情")
        self.asset_panel = AssetPanel(self)
        tabs.addTab(self.asset_panel, "素材")
        self.character_panel = CharacterPanel(self)
        tabs.addTab(self.character_panel, "角色与关系")
        self.text_panel = TextPanel(self)
        tabs.addTab(self.text_panel, "文本与设定")
        self.theme_panel = ThemePanel(self)
        tabs.addTab(self.theme_panel, "界面主题")
        self.tabs = tabs
        self.branch_status_label = QLabel("分支检查：未打开项目")
        self.statusBar().addPermanentWidget(self.branch_status_label)
        self.update_title()

    def _make_menu(self):
        file_menu = self.menuBar().addMenu("项目")
        for label, handler in (
            ("新建", self.new_project),
            ("打开", self.open_project),
            ("保存", self.save),
            ("实时画面预览窗口", self.open_live_preview),
            ("运行预览", self.preview),
            ("调试预览", self.preview_debug),
            ("生成片尾报幕", self.generate_credits),
            ("导出游戏", self.export),
        ):
            action = QAction(label, self)
            action.triggered.connect(handler)
            file_menu.addAction(action)

    def _make_story_tab(self):
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        for label, handler in (
            ("新建节点", self.add_node),
            ("改名", self.rename_node),
            ("复制", self.copy_node),
            ("删除", self.delete_node),
            ("连接节点", self.connect_nodes),
            ("设为开场", self.set_start),
            ("设为/取消结局", self.set_ending),
            ("片尾视频", self.set_credits_video),
            ("全局变量", self.manage_variables),
            ("检查分支", self.retry_branch_check),
            ("实时预览窗口", self.open_live_preview),
            ("运行预览", self.preview),
            ("调试预览", self.preview_debug),
        ):
            button = QPushButton(label)
            if label == "设为/取消结局":
                button.setToolTip("没有后继连接的终点（叶节点）可设为结局；播完演出后自动结束故事")
            if label in {"实时预览窗口", "运行预览"}:
                button.setProperty("accent", True)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch()
        outer.addLayout(toolbar)

        split = QSplitter()
        outer.addWidget(split, 1)

        graph_box = QWidget()
        graph_layout = QVBoxLayout(graph_box)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        graph_tip = QLabel("剧情节点图（空白处拖拽平移 · 滚轮缩放 · 从节点右侧橙点拖到目标节点快捷连线）")
        graph_tip.setProperty("sectionTitle", True)
        graph_layout.addWidget(graph_tip)
        self.scene = QGraphicsScene()
        self.graph = InteractiveGraphView(self.scene, dark=True)
        graph_layout.addWidget(self.graph, 1)
        split.addWidget(graph_box)

        side = QWidget()
        side.setMinimumWidth(360)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(6, 0, 0, 0)
        nodes_title = QLabel("剧情节点")
        nodes_title.setProperty("sectionTitle", True)
        side_layout.addWidget(nodes_title)
        self.nodes_list = QListWidget()
        self.nodes_list.setMaximumHeight(165)
        self.nodes_list.currentItemChanged.connect(self.on_node_selected)
        side_layout.addWidget(self.nodes_list)

        self.inspector_tabs = QTabWidget()
        side_layout.addWidget(self.inspector_tabs, 1)

        # Tab 1: Playback items only (no choices mixed in)
        items_tab = QWidget()
        items_layout = QVBoxLayout(items_tab)
        items_layout.addWidget(QLabel("点击演出条目可在「实时预览窗口」中查看该步画面"))
        self.items_list = QListWidget()
        self.items_list.currentRowChanged.connect(self.update_live_preview)
        items_layout.addWidget(self.items_list, 1)
        item_actions = QHBoxLayout()
        for label, handler in (
            ("添加", self.add_item), ("编辑", self.edit_item),
            ("上移", lambda: self.move_item(-1)), ("下移", lambda: self.move_item(1)),
            ("删除", self.delete_item),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            item_actions.addWidget(button)
        insert_button = QPushButton("插入")
        insert_menu = QMenu(insert_button)
        insert_menu.addAction("插入到选中条目前", lambda: self.insert_item(0))
        insert_menu.addAction("插入到选中条目后", lambda: self.insert_item(1))
        insert_button.setMenu(insert_menu)
        item_actions.insertWidget(1, insert_button)
        items_layout.addLayout(item_actions)
        media_actions = QHBoxLayout()
        for label, handler in (
            ("关联对白语音", self.set_dialogue_voice),
            ("切换立绘", self.switch_sprite),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            media_actions.addWidget(button)
        items_layout.addLayout(media_actions)
        self.inspector_tabs.addTab(items_tab, "播放顺序")

        # Tab 2: Node properties — choices (local choice variable) + node-local variables + globals
        vars_tab = QWidget()
        vars_layout = QVBoxLayout(vars_tab)
        choice_title = QLabel("分支选项（本节点局部 choice 变量）")
        choice_title.setProperty("sectionTitle", True)
        vars_layout.addWidget(choice_title)
        self.choices_list = QListWidget()
        self.choices_list.setMaximumHeight(130)
        vars_layout.addWidget(self.choices_list)
        choice_actions = QHBoxLayout()
        for label, handler in (
            ("添加选项", self.add_choice),
            ("编辑选项", self.edit_choice),
            ("删除选项", self.remove_choice),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            choice_actions.addWidget(button)
        vars_layout.addLayout(choice_actions)

        local_title = QLabel("本节点局部变量")
        local_title.setProperty("sectionTitle", True)
        vars_layout.addWidget(local_title)
        self.local_vars_list = QListWidget()
        self.local_vars_list.setMaximumHeight(130)
        vars_layout.addWidget(self.local_vars_list)
        local_actions = QHBoxLayout()
        for label, handler in (
            ("新建局部", self.add_local_variable),
            ("编辑局部", self.edit_local_variable),
            ("删除局部", self.delete_local_variable),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            local_actions.addWidget(button)
        vars_layout.addLayout(local_actions)

        global_title = QLabel("项目全局变量")
        global_title.setProperty("sectionTitle", True)
        vars_layout.addWidget(global_title)
        self.global_vars_list = QListWidget()
        vars_layout.addWidget(self.global_vars_list, 1)
        manage_global_btn = QPushButton("管理全部变量（全局 / 局部）")
        manage_global_btn.clicked.connect(self.manage_variables)
        vars_layout.addWidget(manage_global_btn)
        self.inspector_tabs.addTab(vars_tab, "变量与选项")

        # Tab 3: Outgoing edges
        edges_tab = QWidget()
        edges_layout = QVBoxLayout(edges_tab)
        self.leaf_hint = QLabel()
        self.leaf_hint.setWordWrap(True)
        self.leaf_hint.setStyleSheet("color: #E0A447; font-size: 12px;")
        edges_layout.addWidget(self.leaf_hint)
        self.edges_list = QListWidget()
        edges_layout.addWidget(self.edges_list, 1)
        edge_actions = QHBoxLayout()
        for label, handler in (
            ("条件", self.edit_edge_condition),
            ("添加变更", self.add_edge_effect),
            ("移除变更", self.remove_edge_effect),
            ("删除连接", self.delete_edge),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            edge_actions.addWidget(button)
        edges_layout.addLayout(edge_actions)
        self.inspector_tabs.addTab(edges_tab, "后继连接")

        split.addWidget(side)
        split.setSizes([900, 420])
        return container

    def open_live_preview(self):
        self.update_live_preview()
        self.live_preview_window.show()
        self.live_preview_window.raise_()
        self.live_preview_window.activateWindow()

    def step_live_preview(self, delta: int):
        count = self.items_list.count()
        if count == 0:
            return
        current = self.items_list.currentRow()
        if current < 0:
            target = 0 if delta > 0 else count - 1
        else:
            target = max(0, min(count - 1, current + delta))
        self.items_list.setCurrentRow(target)

    def update_title(self):
        title = self.project["title"] if self.project else "未打开项目"
        self.setWindowTitle(f"GalGame Maker · {title}{' *' if self.dirty else ''}")

    def mark_dirty(self):
        self.dirty = True
        self.update_title()
        if self.project is not None:
            self.schedule_branch_check()

    def require_project(self) -> bool:
        if self.project is not None:
            return True
        QMessageBox.information(self, "项目", "请先新建或打开项目")
        return False

    def _change_project(self) -> bool:
        if self.dirty:
            result = QMessageBox.question(
                self, "未保存修改", "当前项目有未保存的修改，是否先保存？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if result == QMessageBox.StandardButton.Cancel:
                return False
            if result == QMessageBox.StandardButton.Yes and not self.save():
                return False
        return True

    def new_project(self):
        if not self._change_project():
            return
        root = QFileDialog.getExistingDirectory(self, "选择新项目文件夹的上级目录")
        if not root:
            return
        title, accepted = QInputDialog.getText(self, "新建项目", "作品标题")
        if not accepted or not title.strip():
            return
        folder, accepted = QInputDialog.getText(self, "新建项目", "项目文件夹名称", text=title)
        if not accepted or not folder.strip() or folder in {".", ".."} or Path(folder).name != folder:
            return
        try:
            project_root = Path(root) / folder
            project = create_project(project_root, title.strip())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "创建失败", str(error))
            return
        self.set_project(project_root, project)

    def open_project(self):
        if not self._change_project():
            return
        folder = QFileDialog.getExistingDirectory(self, "打开项目文件夹")
        if not folder:
            return
        try:
            project = load_project(folder)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "打开失败", str(error))
            return
        self.set_project(Path(folder), project)

    def set_project(self, root: Path, project: dict):
        self._stop_branch_worker()
        self.branch_cache.clear()
        self.live_preview.clear_cache()
        self.root = root
        self.project = project
        self.editor = ProjectEditor(project)
        self.dirty = False
        self.update_title()
        self.refresh_story()
        self.asset_panel.refresh()
        self.character_panel.refresh()
        self.text_panel.refresh()
        self.theme_panel.load_part()
        self.schedule_branch_check()

    def _stop_branch_worker(self):
        self.branch_check_timer.stop()
        self._branch_recheck_needed = False
        if self._branch_worker is not None:
            if self._branch_worker.isRunning():
                self._branch_worker.wait(3000)
            self._branch_worker = None

    def schedule_branch_check(self):
        if self.project is None:
            self.branch_status_label.setText("分支检查：未打开项目")
            return
        self.branch_check_timer.start()
        self.update_branch_status_label()

    def retry_branch_check(self):
        if not self.require_project():
            return
        self._start_background_branch_check(force_all=True)

    def _start_background_branch_check(self, force_all: bool = False):
        if self.project is None:
            return
        if self._branch_worker is not None and self._branch_worker.isRunning():
            self._branch_recheck_needed = True
            return
        pending_ids = []
        for node in self.project["nodes"]:
            state = self.branch_cache.get_node_state(self.project, node["id"])
            if force_all or state.status in {"pending", "incomplete"}:
                pending_ids.append(node["id"])
        if not pending_ids:
            self.update_branch_status_label()
            return
        self.branch_status_label.setText(f"分支检查中（待查 {len(pending_ids)} 节点）...")
        worker = BranchCheckWorker(
            deepcopy(self.project),
            self.branch_cache.snapshot_pairs(),
            node_ids=pending_ids,
            parent=self,
        )
        worker.batch_finished.connect(self._on_branch_batch_finished)
        self._branch_worker = worker
        worker.start()

    def _on_branch_batch_finished(self, states: dict[str, NodeCheckState], pairs: dict):
        self._branch_worker = None
        if self.project is None:
            return
        self.branch_cache.merge_node_states(self.project, states, pairs)
        self.update_branch_status_label()
        self.draw_graph()
        still_pending = any(
            self.branch_cache.get_node_state(self.project, node["id"]).status == "pending"
            for node in self.project["nodes"]
        )
        if self._branch_recheck_needed or still_pending:
            self._branch_recheck_needed = False
            self.branch_check_timer.start()

    def update_branch_status_label(self):
        if self.project is None:
            self.branch_status_label.setText("分支检查：未打开项目")
            return
        nodes = self.project["nodes"]
        if not nodes:
            self.branch_status_label.setText("分支检查：无节点")
            return
        conflicts = 0
        incomplete = 0
        pending = 0
        for node in nodes:
            status = self.branch_cache.get_node_state(self.project, node["id"]).status
            if status == "conflict":
                conflicts += 1
            elif status == "incomplete":
                incomplete += 1
            elif status == "pending":
                pending += 1
        if conflicts:
            self.branch_status_label.setText(f"分支检查：{conflicts} 个节点存在冲突")
        elif incomplete:
            self.branch_status_label.setText(f"分支检查：{incomplete} 个节点未完成")
        elif pending:
            self.branch_status_label.setText(f"分支检查：待检查 {pending} 节点")
        else:
            self.branch_status_label.setText(f"分支检查：全部通过（{len(nodes)} 节点）")

    def save(self) -> bool:
        if not self.require_project():
            return False
        try:
            save_project(self.root, self.project)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "保存失败", str(error))
            return False
        self.dirty = False
        self.update_title()
        return True

    def current_node(self) -> dict | None:
        item = self.nodes_list.currentItem()
        if item is None or self.editor is None:
            return None
        return self.editor.node(item.data(Qt.ItemDataRole.UserRole))

    def select_node(self, node_id: str):
        for index in range(self.nodes_list.count()):
            item = self.nodes_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == node_id:
                self.nodes_list.setCurrentItem(item)
                return

    def refresh_story(self, selected_id: str | None = None):
        self.nodes_list.blockSignals(True)
        self.nodes_list.clear()
        if self.project:
            for node in self.project["nodes"]:
                label = node["name"]
                if node["id"] == self.project["start_node_id"]:
                    label += " · 开场"
                if node.get("ending") is not None:
                    label += " · 结局"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, node["id"])
                self.nodes_list.addItem(item)
        self.nodes_list.blockSignals(False)
        self.draw_graph()
        if selected_id:
            self.select_node(selected_id)
        elif self.nodes_list.count():
            self.nodes_list.setCurrentRow(0)
        else:
            self.items_list.clear()

    def draw_graph(self):
        self.scene.clear()
        self._node_shapes: dict[str, StoryNodeItem] = {}
        if not self.project:
            return
        positions = self.project.get("editor", {}).get("node_positions", {})
        outgoing_counts = {node["id"]: 0 for node in self.project["nodes"]}
        for edge in self.project["edges"]:
            if edge["from_node_id"] in outgoing_counts:
                outgoing_counts[edge["from_node_id"]] += 1
        current = self.current_node()
        current_id = current["id"] if current else None
        for index, node in enumerate(self.project["nodes"]):
            point = positions.get(node["id"], {"x": index % 4 * 280, "y": index // 4 * 155})
            state = self.branch_cache.get_node_state(self.project, node["id"])
            is_start = node["id"] == self.project.get("start_node_id")
            is_ending = node.get("ending") is not None
            is_unended_leaf = (not is_ending) and outgoing_counts.get(node["id"], 0) == 0
            shape = StoryNodeItem(
                self, node, point["x"], point["y"],
                status=state.status,
                is_start=is_start,
                is_ending=is_ending,
                is_unended_leaf=is_unended_leaf,
            )
            if node["id"] == current_id:
                shape.setSelected(True)
            self.scene.addItem(shape)
            self._node_shapes[node["id"]] = shape
        nodes_by_id = {n["id"]: n for n in self.project["nodes"]}
        for edge in self.project["edges"]:
            source = self._node_shapes.get(edge["from_node_id"])
            target = self._node_shapes.get(edge["to_node_id"])
            if source is None or target is None:
                continue
            src_node = nodes_by_id.get(edge["from_node_id"], {})
            cond = edge.get("condition")
            if cond is None:
                label = "自动衔接"
            else:
                try:
                    label = format_condition(cond, self.project["variables"], src_node.get("choices", []))
                except (KeyError, TypeError, ValueError):
                    label = "条件失效"
            src_state = self.branch_cache.get_node_state(self.project, edge["from_node_id"])
            is_conflict = (
                src_state.status == "conflict"
                and src_state.conflict_edges is not None
                and edge["id"] in src_state.conflict_edges
            )
            wire = StoryEdgeItem(source, target, label=label, conflict=is_conflict)
            self.scene.addItem(wire)
        bounds = self.scene.itemsBoundingRect().adjusted(-200, -200, 200, 200)
        if not bounds.isEmpty():
            self.scene.setSceneRect(bounds)

    def _format_item_label(self, item: dict) -> str:
        kind = item["type"]
        prefix = ITEM_TYPE_LABELS.get(kind, kind)
        assets = {a["id"]: a["name"] for a in self.project["assets"]} if self.project else {}
        chars = {c["id"]: c["name"] for c in self.project["characters"]} if self.project else {}
        if kind == "dialogue":
            speaker = chars.get(item.get("character_id"), "未知角色")
            voice_id = item.get("voice_asset_id")
            voice = f" [语音：{assets.get(voice_id, '缺失素材')}]" if voice_id else ""
            return f"{prefix} · {speaker}：{item['text'][:36]}{voice}"
        if kind == "narration":
            return f"{prefix} · {item['text'][:42]}"
        if kind == "set_background":
            aid = item.get("asset_id")
            return f"{prefix} · {assets.get(aid, '清除背景') if aid else '清除背景'}"
        if kind == "show_sprite":
            aid = item.get("asset_id")
            slot = item.get("slot", "center")
            return f"{prefix} [{slot}] · {assets.get(aid, '缺失素材')}"
        if kind == "hide_sprite":
            return f"{prefix} [{item.get('slot', 'center')}]"
        if kind in {"play_bgm", "play_sfx"}:
            aid = item.get("asset_id")
            return f"{prefix} · {assets.get(aid, '缺失素材')}"
        return prefix

    def on_node_selected(self, current, previous, *, item_row: int | None = None):
        self.items_list.blockSignals(True)
        self.items_list.clear()
        node = self.current_node()
        if node is None:
            self.items_list.blockSignals(False)
            self.choices_list.clear()
            self.local_vars_list.clear()
            self.global_vars_list.clear()
            self.edges_list.clear()
            self.leaf_hint.clear()
            self.live_preview.set_state(self.root, self.project, None, {}, status_hint="未选中节点")
            self.live_preview_window.step_label.setText("未选中节点")
            return
        for item in node["items"]:
            row = QListWidgetItem(self._format_item_label(item))
            row.setData(Qt.ItemDataRole.UserRole, item["id"])
            self.items_list.addItem(row)
        self.items_list.blockSignals(False)
        if self.items_list.count():
            self.items_list.setCurrentRow(self.items_list.count() - 1 if item_row is None else item_row)
        else:
            self.update_live_preview()
        self.refresh_node_variables(node)
        self.refresh_edges(node)

    def _available_variables_for_node(self, node_id: str) -> list[dict]:
        if self.project is None:
            return []
        return [
            var for var in self.project["variables"]
            if var.get("scope", "global") == "global" or var.get("node_id") == node_id
        ]

    def refresh_node_variables(self, node: dict | None = None):
        self.choices_list.clear()
        self.local_vars_list.clear()
        self.global_vars_list.clear()
        if self.project is None:
            return
        target = node if node is not None else self.current_node()
        if target is not None:
            for choice in target.get("choices", []):
                row = QListWidgetItem(f"choice is \"{choice['text']}\"")
                row.setData(Qt.ItemDataRole.UserRole, choice["id"])
                self.choices_list.addItem(row)
            for var in self.project["variables"]:
                if var.get("scope") == "local" and var.get("node_id") == target["id"]:
                    row = QListWidgetItem(f"{var['name']} ({var['type']}) = {var['initial_value']}")
                    row.setData(Qt.ItemDataRole.UserRole, var["id"])
                    self.local_vars_list.addItem(row)
        for var in self.project["variables"]:
            if var.get("scope", "global") == "global":
                row = QListWidgetItem(f"{var['name']} ({var['type']}) = {var['initial_value']}")
                row.setData(Qt.ItemDataRole.UserRole, var["id"])
                self.global_vars_list.addItem(row)

    def update_live_preview(self, *_):
        node = self.current_node()
        if node is None or self.project is None:
            self.live_preview.set_state(self.root, self.project, None, {})
            self.live_preview_window.step_label.setText("未选中节点")
            return
        items = node["items"]
        row_idx = self.items_list.currentRow()
        if row_idx < 0 or row_idx >= len(items):
            limit = len(items)
        else:
            limit = row_idx + 1
        bg_id = None
        sprites: dict[str, dict] = {}
        speaker = ""
        text = ""
        chars = {c["id"]: c["name"] for c in self.project["characters"]}
        for item in items[:limit]:
            kind = item["type"]
            if kind == "set_background":
                bg_id = item.get("asset_id")
            elif kind == "show_sprite":
                sprites[item["slot"]] = {
                    "asset_id": item["asset_id"],
                    "placement": item.get("placement"),
                }
            elif kind == "hide_sprite":
                sprites.pop(item["slot"], None)
            elif kind == "dialogue":
                speaker = chars.get(item.get("character_id"), "")
                text = item["text"]
            elif kind == "narration":
                speaker = ""
                text = item["text"]
        show_choices = [c["text"] for c in node.get("choices", [])] if limit == len(items) else []
        hint = f"节点「{node['name']}」· 第 {limit}/{len(items)} 步"
        if not items:
            hint = f"节点「{node['name']}」（暂无演出条目）"
        self.live_preview_window.step_label.setText(hint)
        self.live_preview.set_state(
            self.root, self.project, bg_id, sprites,
            speaker=speaker, dialogue_text=text, status_hint=hint, choices=show_choices,
        )

    def refresh_edges(self, node: dict):
        selected = self.current_edge()
        selected_id = selected["id"] if selected else None
        self.edges_list.clear()
        names = {entry["id"]: entry["name"] for entry in self.project["nodes"]}
        for edge in self.project["edges"]:
            if edge["from_node_id"] != node["id"]:
                continue
            condition = edge["condition"]
            try:
                expression = (
                    format_condition(condition, self.project["variables"], node.get("choices", []))
                    if condition is not None else "自动衔接"
                )
            except (KeyError, TypeError, ValueError):
                expression = "条件引用失效"
            effect_count = len(edge.get("effects", []))
            suffix = f" · {effect_count} 项变量变化" if effect_count else ""
            row = QListWidgetItem(
                f"{names.get(edge['to_node_id'], '缺失节点')} · {expression}{suffix}"
            )
            row.setData(Qt.ItemDataRole.UserRole, edge["id"])
            self.edges_list.addItem(row)
            if edge["id"] == selected_id:
                self.edges_list.setCurrentItem(row)
        if self.edges_list.count() == 0 and node.get("ending") is None:
            self.leaf_hint.setText(
                "提示：此节点暂无后继（终点节点），可点「设为/取消结局」设为结局，或从节点右侧橙点拖线连接下一节点。"
            )
        elif node.get("ending") is not None:
            self.leaf_hint.setText("当前节点已设为结局（播完演出后结束故事或播放片尾）。")
        else:
            self.leaf_hint.clear()

    def current_edge(self) -> dict | None:
        row = self.edges_list.currentItem()
        if row is None or self.project is None:
            return None
        edge_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (edge for edge in self.project["edges"] if edge["id"] == edge_id), None,
        )

    def add_node(self):
        if not self.require_project():
            return
        name, accepted = QInputDialog.getText(self, "新建节点", "节点名称")
        if accepted and name.strip():
            node = self.editor.add_node(name.strip())
            self.mark_dirty()
            self.refresh_story(node["id"])

    def copy_node(self):
        node = self.current_node()
        if node is None:
            return
        duplicate = self.editor.copy_node(node["id"])
        positions = self.project.setdefault("editor", {}).setdefault("node_positions", {})
        orig_pos = positions.get(node["id"])
        if orig_pos is None and hasattr(self, "_node_shapes") and node["id"] in self._node_shapes:
            shape = self._node_shapes[node["id"]]
            orig_pos = {"x": round(shape.x()), "y": round(shape.y())}
        if orig_pos is not None:
            positions[duplicate["id"]] = {"x": orig_pos["x"] + 250, "y": orig_pos["y"] + 40}
        self.mark_dirty()
        self.refresh_story(duplicate["id"])
        if hasattr(self, "_node_shapes") and duplicate["id"] in self._node_shapes:
            self.graph.ensureVisible(self._node_shapes[duplicate["id"]])

    def rename_node(self):
        node = self.current_node()
        if node is None:
            return
        name, accepted = QInputDialog.getText(
            self, "节点改名", "名称", text=node["name"],
        )
        if accepted and name.strip():
            node["name"] = name.strip()
            self.mark_dirty()
            self.refresh_story(node["id"])

    def delete_node(self):
        node = self.current_node()
        if node is None:
            return
        answer = QMessageBox.question(
            self, "删除节点", f"删除「{node['name']}」及连接？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        node_id = node["id"]
        self.project["nodes"].remove(node)
        self.project["edges"] = [
            edge for edge in self.project["edges"]
            if edge["from_node_id"] != node_id and edge["to_node_id"] != node_id
        ]
        if self.project["start_node_id"] == node_id:
            self.project["start_node_id"] = None
        self.project.get("editor", {}).get("node_positions", {}).pop(node_id, None)
        self.mark_dirty()
        self.refresh_story()
        self.text_panel.refresh()

    def quick_connect_nodes(self, source_id: str, target_id: str):
        if self.project is None or self.editor is None:
            return
        source = self.editor.node(source_id)
        target = self.editor.node(target_id)
        if source.get("ending") is not None:
            QMessageBox.warning(self, "无法连接", "结局节点不能有后继")
            return
        available_vars = self._available_variables_for_node(source["id"])
        dialog = ConditionDialog(
            f"连接条件：{source['name']} → {target['name']}",
            "",
            available_vars,
            source.get("choices", []),
            allow_empty=True,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        expression = dialog.expression()
        try:
            condition = None
            if expression.strip():
                condition = parse_condition(
                    expression, available_vars, source.get("choices", []),
                )
            edge = self.editor.add_edge(source["id"], target["id"], condition)
        except ValueError as error:
            QMessageBox.warning(self, "无法连接", str(error))
            return
        self.mark_dirty()
        self.draw_graph()
        self.select_node(source["id"])
        self.refresh_edges(source)
        for index in range(self.edges_list.count()):
            row = self.edges_list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == edge["id"]:
                self.edges_list.setCurrentItem(row)
                break

    def connect_nodes(self):
        source = self.current_node()
        if source is None:
            return
        if source.get("ending") is not None:
            QMessageBox.warning(self, "无法连接", "结局节点不能有后继")
            return
        targets = [node for node in self.project["nodes"] if node["id"] != source["id"]]
        if not targets:
            return
        labels = [f"{node['name']} [{node['id'][:8]}]" for node in targets]
        selected, accepted = QInputDialog.getItem(self, "连接节点", "目标节点", labels, editable=False)
        if not accepted:
            return
        target = targets[labels.index(selected)]
        self.quick_connect_nodes(source["id"], target["id"])

    def edit_edge_condition(self):
        node = self.current_node()
        edge = self.current_edge()
        if node is None or edge is None:
            return
        try:
            current = (
                format_condition(edge["condition"], self.project["variables"], node.get("choices", []))
                if edge["condition"] is not None else ""
            )
        except (KeyError, TypeError, ValueError):
            current = ""
        expression, accepted = QInputDialog.getText(
            self, "连接条件", "条件表达式", text=current,
        )
        if not accepted:
            return
        try:
            condition = (
                parse_condition(expression, self.project["variables"], node.get("choices", []))
                if expression.strip() else None
            )
        except ValueError as error:
            QMessageBox.warning(self, "条件错误", str(error))
            return
        edge["condition"] = condition
        self.mark_dirty()
        self.refresh_edges(node)

    def add_edge_effect(self):
        node = self.current_node()
        edge = self.current_edge()
        if node is None or edge is None:
            return
        variables = self.project["variables"]
        if not variables:
            QMessageBox.information(self, "变量", "请先创建变量")
            return
        labels = [f"{variable['name']} [{variable['id'][:8]}]" for variable in variables]
        selected, accepted = QInputDialog.getItem(
            self, "连接变量变化", "变量", labels, editable=False,
        )
        if not accepted:
            return
        variable = variables[labels.index(selected)]
        operations = ["set", "add", "subtract"] if variable["type"] == "int" else ["set"]
        operation, accepted = QInputDialog.getItem(
            self, "连接变量变化", "操作", operations, editable=False,
        )
        if not accepted:
            return
        if variable["type"] == "int":
            value, accepted = self.integer_input("连接变量变化", "值")
        else:
            selected, accepted = QInputDialog.getItem(
                self, "连接变量变化", "值", ["false", "true"], editable=False,
            )
            value = selected == "true"
        if accepted:
            edge.setdefault("effects", []).append({
                "variable_id": variable["id"], "op": operation, "value": value,
            })
            self.mark_dirty()
            self.refresh_edges(node)

    def remove_edge_effect(self):
        node = self.current_node()
        edge = self.current_edge()
        if node is None or edge is None or not edge.get("effects"):
            return
        names = {variable["id"]: variable["name"] for variable in self.project["variables"]}
        labels = [
            f"{index + 1}. {names.get(effect['variable_id'], '缺失变量')} "
            f"{effect['op']} {effect['value']}"
            for index, effect in enumerate(edge["effects"])
        ]
        selected, accepted = QInputDialog.getItem(
            self, "移除变量变化", "变更", labels, editable=False,
        )
        if accepted:
            edge["effects"].pop(labels.index(selected))
            self.mark_dirty()
            self.refresh_edges(node)

    def delete_edge(self):
        node = self.current_node()
        edge = self.current_edge()
        if node is None or edge is None:
            return
        self.project["edges"].remove(edge)
        self.mark_dirty()
        self.refresh_edges(node)
        self.draw_graph()

    def set_start(self):
        node = self.current_node()
        if node:
            self.project["start_node_id"] = node["id"]
            self.mark_dirty()
            self.refresh_story(node["id"])

    def set_ending(self):
        node = self.current_node()
        if node is None:
            return
        if node.get("ending") is not None:
            answer = QMessageBox.question(
                self, "取消结局", f"取消「{node['name']}」的结局标记？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                node["ending"] = None
                self.mark_dirty()
                self.refresh_story(node["id"])
            return
        if any(edge["from_node_id"] == node["id"] for edge in self.project["edges"]):
            QMessageBox.warning(self, "无法设置结局", "结局节点不能有后继")
            return
        if node.get("choices"):
            QMessageBox.warning(self, "无法设置结局", "结局节点不能有选项")
            return
        node["ending"] = {"credits_video_asset_id": None}
        self.mark_dirty()
        self.refresh_story(node["id"])

    def set_credits_video(self):
        node = self.current_node()
        if node is None:
            return
        if node.get("ending") is None:
            QMessageBox.information(self, "片尾", "请先将叶节点设为结局")
            return
        labels, assets = self._asset_options("video")
        options = ["不播放片尾", *labels]
        selected, accepted = QInputDialog.getItem(
            self, "片尾视频", "素材", options, editable=False,
        )
        if accepted:
            node["ending"]["credits_video_asset_id"] = (
                None if selected == "不播放片尾" else assets[labels.index(selected)]["id"]
            )
            self.mark_dirty()

    def manage_variables(self):
        if not self.require_project():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("变量管理")
        dialog.resize(460, 360)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        layout.addWidget(listing, 1)

        def reload_list():
            listing.clear()
            node_names = {n["id"]: n["name"] for n in self.project["nodes"]}
            for var in self.project["variables"]:
                scope = var.get("scope", "global")
                tag = (
                    f"[局部 · {node_names.get(var.get('node_id'), '未知节点')}]"
                    if scope == "local" else "[全局]"
                )
                row = QListWidgetItem(
                    f"{tag} {var['name']} ({var['type']}) = {var['initial_value']}"
                )
                row.setData(Qt.ItemDataRole.UserRole, var["id"])
                listing.addItem(row)

        def selected_var() -> dict | None:
            row = listing.currentItem()
            if row is None:
                return None
            vid = row.data(Qt.ItemDataRole.UserRole)
            return next((v for v in self.project["variables"] if v["id"] == vid), None)

        def on_add():
            self.add_variable()
            reload_list()

        def on_edit():
            var = selected_var()
            if var is None:
                return
            name, accepted = QInputDialog.getText(dialog, "编辑变量", "名称", text=var["name"])
            if not accepted or not name.strip():
                return
            cleaned = name.strip()
            if not cleaned.isidentifier() or cleaned in {"choice", "true", "false"}:
                QMessageBox.warning(dialog, "变量", "变量名须是可用于条件表达式的标识符")
                return
            if any(v["name"] == cleaned and v["id"] != var["id"] for v in self.project["variables"]):
                QMessageBox.warning(dialog, "变量", "变量名称已存在")
                return
            if var["type"] == "int":
                raw, accepted = QInputDialog.getText(
                    dialog, "初始值", cleaned, text=str(var["initial_value"]),
                )
                if not accepted:
                    return
                try:
                    value: int | bool = int(raw)
                except ValueError:
                    QMessageBox.warning(dialog, "初始值", "请输入整数")
                    return
            else:
                selected, accepted = QInputDialog.getItem(
                    dialog, "初始值", cleaned, ["false", "true"],
                    1 if var["initial_value"] else 0, editable=False,
                )
                if not accepted:
                    return
                value = selected == "true"
            var["name"] = cleaned
            var["initial_value"] = value
            self.mark_dirty()
            if self.current_node() is not None:
                self.refresh_edges(self.current_node())
            reload_list()

        def on_delete():
            var = selected_var()
            if var is None:
                return
            vid = var["id"]
            refs: list[str] = []
            node_names = {n["id"]: n["name"] for n in self.project["nodes"]}
            for edge in self.project["edges"]:
                source_name = node_names.get(edge["from_node_id"], edge["from_node_id"])
                if vid in referenced_variable_ids(edge.get("condition")):
                    refs.append(f"节点 {source_name} 连接条件")
                if any(eff.get("variable_id") == vid for eff in edge.get("effects", [])):
                    refs.append(f"节点 {source_name} 连接变量变化")
            if refs:
                QMessageBox.warning(dialog, "变量仍在使用", "\n".join(refs))
                return
            self.project["variables"].remove(var)
            self.mark_dirty()
            self.refresh_node_variables()
            reload_list()

        buttons = QHBoxLayout()
        for caption, handler in (("新建", on_add), ("编辑", on_edit), ("删除", on_delete)):
            btn = QPushButton(caption)
            btn.clicked.connect(handler)
            buttons.addWidget(btn)
        layout.addLayout(buttons)
        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(dialog.accept)
        layout.addWidget(close_box)
        reload_list()
        dialog.exec()

    def add_variable(self):
        if not self.require_project():
            return
        name, accepted = QInputDialog.getText(self, "新建变量", "变量名称")
        if not accepted or not name.strip():
            return
        if not name.isidentifier() or name in {"choice", "true", "false"}:
            QMessageBox.warning(self, "变量", "变量名须是可用于条件表达式的标识符")
            return
        if any(variable["name"] == name for variable in self.project["variables"]):
            QMessageBox.warning(self, "变量", "变量名称已存在")
            return
        kind, accepted = QInputDialog.getItem(self, "变量类型", "类型", ["int", "bool"], editable=False)
        if not accepted:
            return
        if kind == "int":
            value, accepted = self.integer_input("初始值", name)
        else:
            selected, accepted = QInputDialog.getItem(
                self, "初始值", name, ["false", "true"], editable=False,
            )
            value = selected == "true"
        if accepted:
            from .project import new_id
            self.project["variables"].append({
                "id": new_id(), "name": name.strip(), "type": kind,
                "initial_value": value, "scope": "global",
            })
            self.mark_dirty()
            self.refresh_node_variables()

    def add_local_variable(self):
        node = self.current_node()
        if node is None:
            return
        name, accepted = QInputDialog.getText(self, "新建节点局部变量", "变量名称")
        if not accepted or not name.strip():
            return
        cleaned = name.strip()
        if not cleaned.isidentifier() or cleaned in {"choice", "true", "false"}:
            QMessageBox.warning(self, "局部变量", "变量名须是可用于条件表达式的标识符")
            return
        if any(v["name"] == cleaned for v in self.project["variables"]):
            QMessageBox.warning(self, "局部变量", "变量名称已存在")
            return
        kind, accepted = QInputDialog.getItem(self, "变量类型", "类型", ["int", "bool"], editable=False)
        if not accepted:
            return
        if kind == "int":
            value, accepted = self.integer_input("初始值", cleaned)
        else:
            selected, accepted = QInputDialog.getItem(
                self, "初始值", cleaned, ["false", "true"], editable=False,
            )
            value = selected == "true"
        if accepted:
            from .project import new_id
            self.project["variables"].append({
                "id": new_id(), "name": cleaned, "type": kind,
                "initial_value": value, "scope": "local", "node_id": node["id"],
            })
            self.mark_dirty()
            self.refresh_node_variables(node)

    def edit_local_variable(self):
        node = self.current_node()
        row = self.local_vars_list.currentItem()
        if node is None or row is None:
            return
        vid = row.data(Qt.ItemDataRole.UserRole)
        var = next((v for v in self.project["variables"] if v["id"] == vid), None)
        if var is None:
            return
        name, accepted = QInputDialog.getText(self, "编辑局部变量", "名称", text=var["name"])
        if not accepted or not name.strip():
            return
        cleaned = name.strip()
        if not cleaned.isidentifier() or cleaned in {"choice", "true", "false"}:
            QMessageBox.warning(self, "局部变量", "变量名须是可用于条件表达式的标识符")
            return
        if any(v["name"] == cleaned and v["id"] != var["id"] for v in self.project["variables"]):
            QMessageBox.warning(self, "局部变量", "变量名称已存在")
            return
        if var["type"] == "int":
            raw, accepted = QInputDialog.getText(self, "初始值", cleaned, text=str(var["initial_value"]))
            if not accepted:
                return
            try:
                value: int | bool = int(raw)
            except ValueError:
                QMessageBox.warning(self, "初始值", "请输入整数")
                return
        else:
            selected, accepted = QInputDialog.getItem(
                self, "初始值", cleaned, ["false", "true"],
                1 if var["initial_value"] else 0, editable=False,
            )
            if not accepted:
                return
            value = selected == "true"
        var["name"] = cleaned
        var["initial_value"] = value
        self.mark_dirty()
        self.refresh_node_variables(node)
        self.refresh_edges(node)

    def delete_local_variable(self):
        node = self.current_node()
        row = self.local_vars_list.currentItem()
        if node is None or row is None:
            return
        vid = row.data(Qt.ItemDataRole.UserRole)
        var = next((v for v in self.project["variables"] if v["id"] == vid), None)
        if var is None:
            return
        refs: list[str] = []
        for edge in self.project["edges"]:
            if vid in referenced_variable_ids(edge.get("condition")):
                refs.append("连接条件")
            if any(eff.get("variable_id") == vid for eff in edge.get("effects", [])):
                refs.append("连接变量变化")
        if refs:
            QMessageBox.warning(self, "变量仍在使用", "该局部变量正被以下位置引用：\n" + "\n".join(set(refs)))
            return
        self.project["variables"].remove(var)
        self.mark_dirty()
        self.refresh_node_variables(node)

    def integer_input(self, title: str, prompt: str) -> tuple[int, bool]:
        raw, accepted = QInputDialog.getText(self, title, prompt, text="0")
        if not accepted:
            return 0, False
        try:
            return int(raw), True
        except ValueError:
            QMessageBox.warning(self, title, "请输入整数")
            return 0, False

    def add_choice(self):
        node = self.current_node()
        if node is None:
            return
        if node.get("ending") is not None:
            QMessageBox.warning(self, "选项", "结局节点不能有选项")
            return
        text, accepted = QInputDialog.getText(self, "添加选项", "选项文字")
        if accepted and text.strip():
            from .project import new_id
            node.setdefault("choices", []).append({"id": new_id(), "text": text.strip()})
            self.mark_dirty()
            self.refresh_node_variables(node)
            self.draw_graph()
            self.update_live_preview()

    def edit_choice(self):
        node = self.current_node()
        row = self.choices_list.currentItem()
        if node is None or row is None:
            return
        choice_id = row.data(Qt.ItemDataRole.UserRole)
        choice = next((c for c in node.get("choices", []) if c["id"] == choice_id), None)
        if choice is None:
            return
        text, accepted = QInputDialog.getText(self, "编辑选项", "选项文字", text=choice["text"])
        if accepted and text.strip():
            choice["text"] = text.strip()
            self.mark_dirty()
            self.refresh_node_variables(node)
            self.refresh_edges(node)
            self.draw_graph()
            self.update_live_preview()

    def remove_choice(self):
        node = self.current_node()
        row = self.choices_list.currentItem()
        if node is None or row is None:
            return
        choice_id = row.data(Qt.ItemDataRole.UserRole)
        choices = node.get("choices", [])
        node["choices"] = [choice for choice in choices if choice["id"] != choice_id]
        if len(node["choices"]) != len(choices):
            self.mark_dirty()
            self.refresh_node_variables(node)
            self.draw_graph()
            self.update_live_preview()

    def _asset_options(self, kind: str) -> tuple[list[str], list[dict]]:
        assets = [asset for asset in self.project["assets"] if asset["kind"] == kind]
        return [f"{asset['name']} [{asset['id'][:8]}]" for asset in assets], assets

    def _pick_asset(
        self,
        required_kind: str,
        title: str,
        default_tag: str | None = None,
        allow_none_label: str | None = None,
        current_asset_id: str | None = None,
        *,
        sprite_variants: bool = False,
    ) -> tuple[str | None, bool]:
        has_any = any(asset["kind"] == required_kind for asset in self.project["assets"])
        if not has_any and allow_none_label is None:
            QMessageBox.information(self, "素材", f"请先在素材页导入 {required_kind} 类型的素材")
            return None, False
        dialog = AssetPickerDialog(
            self.project, self.root, required_kind,
            title=title, default_tag=default_tag,
            allow_none_label=allow_none_label,
            current_asset_id=current_asset_id,
            parent=self,
            sprite_variants=sprite_variants,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None, False
        return dialog.selected_asset_id, True

    def generate_credits(self):
        if not self.require_project():
            return
        from .maker_credits import CreditsDialog

        dialog = CreditsDialog(self, self.root, self.project)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.mark_dirty()
        self.tabs.setCurrentWidget(self.asset_panel)
        self.asset_panel.select_asset(dialog.asset_id, "video")

    def configure_item_asset(self, item_type, field, kind, title, **picker_options):
        node = self.current_node()
        row = self.items_list.currentRow()
        if node is None or row < 0:
            return
        item = node["items"][row]
        if item["type"] != item_type:
            QMessageBox.information(self, title, f"请先选中「{ITEM_TYPE_LABELS[item_type]}」条目")
            return
        asset_id, accepted = self._pick_asset(
            kind, title, current_asset_id=item.get(field), **picker_options,
        )
        if not accepted:
            return
        if asset_id is None:
            item.pop(field, None)
        else:
            item[field] = asset_id
        self.mark_dirty()
        self.items_list.item(row).setText(self._format_item_label(item))
        self.update_live_preview()

    def set_dialogue_voice(self):
        self.configure_item_asset(
            "dialogue", "voice_asset_id", "voice", "关联对白语音",
            allow_none_label="无语音",
        )

    def switch_sprite(self):
        self.configure_item_asset(
            "show_sprite", "asset_id", "image", "切换立绘", sprite_variants=True,
        )

    def insert_item(self, offset: int):
        row = self.items_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "插入演出", "请先选中一条演出。空节点可用「添加」。")
            return
        self.add_item(index=row + offset)

    def add_item(self, *, index: int | None = None):
        node = self.current_node()
        if node is None:
            return
        kinds = [
            "dialogue", "narration", "set_background", "show_sprite",
            "hide_sprite", "play_bgm", "stop_bgm", "play_sfx",
        ]
        kind_labels = [f"{ITEM_TYPE_LABELS[k]} ({k})" for k in kinds]
        selected_label, accepted = QInputDialog.getItem(
            self, "添加演出", "类型", kind_labels, editable=False,
        )
        if not accepted:
            return
        kind = kinds[kind_labels.index(selected_label)]
        fields = {}
        if kind in {"dialogue", "narration"}:
            text, accepted = QInputDialog.getMultiLineText(self, "文本", "正文")
            if not accepted or not text:
                return
            fields["text"] = text
            if kind == "dialogue":
                characters = self.project["characters"]
                if not characters:
                    QMessageBox.information(self, "角色", "请先创建角色")
                    return
                labels = [character["name"] for character in characters]
                selected, accepted = QInputDialog.getItem(
                    self, "发言角色", "角色", labels, editable=False,
                )
                if not accepted:
                    return
                fields["character_id"] = characters[labels.index(selected)]["id"]
        if kind in {"set_background", "show_sprite", "play_bgm", "play_sfx"}:
            required_kind = "image" if kind in {"set_background", "show_sprite"} else (
                "bgm" if kind == "play_bgm" else "sfx"
            )
            default_tag = "背景" if kind == "set_background" else ("立绘" if kind == "show_sprite" else None)
            allow_none = "清除背景" if kind == "set_background" else None
            asset_id, accepted = self._pick_asset(
                required_kind, f"选择{ITEM_TYPE_LABELS[kind]}素材",
                default_tag=default_tag, allow_none_label=allow_none,
                sprite_variants=kind == "show_sprite",
            )
            if not accepted:
                return
            fields["asset_id"] = asset_id
            if kind == "play_bgm":
                loop_choice, accepted = QInputDialog.getItem(
                    self, "循环播放", "模式", ["循环", "单次"], editable=False,
                )
                if not accepted:
                    return
                volume, accepted = QInputDialog.getInt(self, "音量", "音量百分比", 100, 0, 100)
                if not accepted:
                    return
                fields["loop"] = loop_choice == "循环"
                fields["volume_pct"] = volume
            elif kind == "play_sfx":
                volume, accepted = QInputDialog.getInt(self, "音效音量", "音量百分比", 100, 0, 100)
                if not accepted:
                    return
                fields["volume_pct"] = volume
        if kind in {"show_sprite", "hide_sprite"}:
            slot, accepted = QInputDialog.getItem(
                self, "立绘槽位", "槽位", ["left", "center", "right"], editable=False,
            )
            if not accepted:
                return
            fields["slot"] = slot
            if kind == "show_sprite":
                scale, accepted = QInputDialog.getInt(
                    self, "立绘缩放", "百分比", 100, 25, 200,
                )
                if not accepted:
                    return
                defaults = {"left": (480, 1), "center": (960, 2), "right": (1440, 3)}
                x, z = defaults[slot]
                fields["placement"] = {
                    "x": x, "y": 1040, "scale_pct": scale, "z": z,
                }
        self.editor.add_item(node["id"], kind, index=index, **fields)
        self.mark_dirty()
        self.on_node_selected(None, None, item_row=index)
        self.draw_graph()

    def edit_item(self):
        node = self.current_node()
        row = self.items_list.currentItem()
        if node is None or row is None:
            return
        item_id = row.data(Qt.ItemDataRole.UserRole)
        item = next((entry for entry in node["items"] if entry["id"] == item_id), None)
        if item is None:
            return
        kind = item["type"]
        if kind in {"dialogue", "narration"}:
            text, accepted = QInputDialog.getMultiLineText(
                self, "编辑文本", "正文", item["text"],
            )
            if not accepted or not text:
                return
            if kind == "dialogue" and self.project["characters"]:
                characters = self.project["characters"]
                labels = [character["name"] for character in characters]
                current_idx = next(
                    (i for i, c in enumerate(characters) if c["id"] == item.get("character_id")),
                    0,
                )
                selected, accepted = QInputDialog.getItem(
                    self, "发言角色", "角色", labels, current_idx, editable=False,
                )
                if not accepted:
                    return
                item["character_id"] = characters[labels.index(selected)]["id"]
            item["text"] = text
            self.text_panel.refresh()
        elif kind in {"set_background", "show_sprite", "play_bgm", "play_sfx"}:
            asset_kind = "image" if kind in {"set_background", "show_sprite"} else (
                "bgm" if kind == "play_bgm" else "sfx"
            )
            default_tag = "背景" if kind == "set_background" else ("立绘" if kind == "show_sprite" else None)
            allow_none = "清除背景" if kind == "set_background" else None
            asset_id, accepted = self._pick_asset(
                asset_kind, f"更换{ITEM_TYPE_LABELS[kind]}素材",
                default_tag=default_tag, allow_none_label=allow_none,
                current_asset_id=item.get("asset_id"),
                sprite_variants=kind == "show_sprite",
            )
            if not accepted:
                return
            if kind == "show_sprite":
                slots = ["left", "center", "right"]
                slot_idx = slots.index(item["slot"]) if item.get("slot") in slots else 1
                slot, accepted = QInputDialog.getItem(
                    self, "立绘槽位", "槽位", slots, slot_idx, editable=False,
                )
                if not accepted:
                    return
                placement = item.get("placement") or {}
                scale, accepted = QInputDialog.getInt(
                    self, "立绘缩放", "百分比", placement.get("scale_pct", 100), 25, 200,
                )
                if not accepted:
                    return
                defaults = {"left": (480, 1), "center": (960, 2), "right": (1440, 3)}
                x, z = defaults[slot]
                item["slot"] = slot
                item["placement"] = {"x": x, "y": 1040, "scale_pct": scale, "z": z}
            elif kind == "play_bgm":
                loop_choice, accepted = QInputDialog.getItem(
                    self, "循环播放", "模式", ["循环", "单次"],
                    0 if item.get("loop", True) else 1, editable=False,
                )
                if not accepted:
                    return
                volume, accepted = QInputDialog.getInt(
                    self, "音量", "音量百分比", item.get("volume_pct", 100), 0, 100,
                )
                if not accepted:
                    return
                item["loop"] = loop_choice == "循环"
                item["volume_pct"] = volume
            elif kind == "play_sfx":
                volume, accepted = QInputDialog.getInt(
                    self, "音效音量", "音量百分比", item.get("volume_pct", 100), 0, 100,
                )
                if not accepted:
                    return
                item["volume_pct"] = volume
            item["asset_id"] = asset_id
        elif kind == "hide_sprite":
            selected, accepted = QInputDialog.getItem(
                self, "立绘槽位", "槽位", ["left", "center", "right"],
                ["left", "center", "right"].index(item["slot"]), editable=False,
            )
            if not accepted:
                return
            item["slot"] = selected
        else:
            return
        self.mark_dirty()
        self.on_node_selected(None, None)

    def move_item(self, offset: int):
        node = self.current_node()
        row = self.items_list.currentItem()
        if node is None or row is None:
            return
        item_id = row.data(Qt.ItemDataRole.UserRole)
        if any(item["id"] == item_id for item in node["items"]):
            self.editor.move_item(node["id"], item_id, offset)
            self.mark_dirty()
            self.on_node_selected(None, None)

    def delete_item(self):
        node = self.current_node()
        row = self.items_list.currentItem()
        if node is None or row is None:
            return
        item_id = row.data(Qt.ItemDataRole.UserRole)
        before = len(node["items"])
        node["items"] = [item for item in node["items"] if item["id"] != item_id]
        if len(node["items"]) != before:
            self.mark_dirty()
            self.on_node_selected(None, None)
            self.text_panel.refresh()

    def preview(self):
        if not self.require_project():
            return
        try:
            game = compile_game(self.project, self.root, branch_cache=self.branch_cache)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法预览", str(error))
            return
        self.preview_window = PlayerWindow(game, self.root, autosave=False)
        self.preview_window.show()

    def preview_debug(self):
        if not self.require_project():
            return
        if not self.project["nodes"]:
            QMessageBox.information(self, "调试预览", "请先创建剧情节点")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("调试预览设置")
        form = QFormLayout(dialog)
        node_box = QComboBox()
        current = self.current_node()
        current_idx = 0
        for index, node in enumerate(self.project["nodes"]):
            node_box.addItem(node["name"], node["id"])
            if current is not None and node["id"] == current["id"]:
                current_idx = index
        node_box.setCurrentIndex(current_idx)
        form.addRow("起始节点", node_box)
        var_widgets: dict[str, tuple[str, QWidget]] = {}
        for var in self.project["variables"]:
            if var["type"] == "int":
                spin = QSpinBox()
                spin.setRange(-1000000, 1000000)
                spin.setValue(var["initial_value"])
                form.addRow(f"变量 {var['name']}", spin)
                var_widgets[var["id"]] = ("int", spin)
            else:
                box = QCheckBox("true")
                box.setChecked(var["initial_value"])
                form.addRow(f"变量 {var['name']}", box)
                var_widgets[var["id"]] = ("bool", box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        start_id = node_box.currentData()
        test_vars: dict[str, int | bool] = {}
        for vid, (kind, widget) in var_widgets.items():
            test_vars[vid] = widget.value() if kind == "int" else widget.isChecked()
        try:
            game = compile_game(self.project, self.root, branch_cache=self.branch_cache)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法预览", str(error))
            return
        self.preview_window = PlayerWindow(
            game, self.root,
            start_node_id=start_id,
            initial_variables=test_vars,
            auto_start=True,
            autosave=False,
        )
        self.preview_window.show()

    def export(self):
        if not self.require_project():
            return
        target = QFileDialog.getExistingDirectory(self, "选择导出目录的上级文件夹")
        if not target:
            return
        name, accepted = QInputDialog.getText(
            self, "导出游戏", "导出目录名称", text=self.project["title"],
        )
        if not accepted or not name.strip() or name in {".", ".."} or Path(name).name != name:
            return
        if getattr(sys, "frozen", False):
            player = Path(sys._MEIPASS) / "player_bundle"
        else:
            player = Path(__file__).resolve().parent.parent / "dist" / "GalGamePlayer"
        try:
            export_game(
                self.project, self.root, Path(target) / name, player,
                branch_cache=self.branch_cache,
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        QMessageBox.information(self, "导出完成", str(Path(target) / name))

    def closeEvent(self, event):
        if self._change_project():
            self._stop_branch_worker()
            self.asset_panel.stop_media()
            self.live_preview_window.close()
            event.accept()
        else:
            event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    window = MakerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

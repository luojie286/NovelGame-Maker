"""Qt Widgets maker for editing a movable visual-novel project."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QAction, QColor, QPen
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QGraphicsItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsTextItem, QGraphicsView, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .condition_parser import parse_condition
from .editing import ProjectEditor
from .export import export_game
from .maker_assets import AssetPanel
from .player import PlayerWindow
from .project import create_project, load_project, save_project
from .runtime_data import compile_game


class NodeShape(QGraphicsRectItem):
    def __init__(self, window: "MakerWindow", node: dict, x: int, y: int):
        super().__init__(0, 0, 180, 78)
        self.window = window
        self.node = node
        self.setPos(x, y)
        self.setBrush(QColor("#283747"))
        self.setPen(QPen(QColor("#69819A"), 2))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        caption = QGraphicsTextItem(node["name"], self)
        caption.setDefaultTextColor(QColor("white"))
        caption.setTextWidth(168)
        caption.setPos(6, 18)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        positions = self.window.project.setdefault("editor", {}).setdefault("node_positions", {})
        positions[self.node["id"]] = {"x": round(self.x()), "y": round(self.y())}
        self.window.mark_dirty()
        QTimer.singleShot(0, self.window.draw_graph)

    def mouseDoubleClickEvent(self, event):
        self.window.select_node(self.node["id"])
        super().mouseDoubleClickEvent(event)


class MakerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.root: Path | None = None
        self.project: dict | None = None
        self.editor: ProjectEditor | None = None
        self.dirty = False
        self.preview_window: PlayerWindow | None = None
        self.setWindowTitle("GalGame Maker")
        self.resize(1280, 820)
        self._make_menu()
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._make_story_tab(), "剧情")
        self.asset_panel = AssetPanel(self)
        tabs.addTab(self.asset_panel, "素材")
        self.tabs = tabs
        self.update_title()

    def _make_menu(self):
        file_menu = self.menuBar().addMenu("项目")
        for label, handler in (
            ("新建", self.new_project),
            ("打开", self.open_project),
            ("保存", self.save),
            ("预览", self.preview),
            ("导出游戏", self.export),
        ):
            action = QAction(label, self)
            action.triggered.connect(handler)
            file_menu.addAction(action)

    def _make_story_tab(self):
        container = QWidget()
        outer = QVBoxLayout(container)
        toolbar = QHBoxLayout()
        for label, handler in (
            ("新建节点", self.add_node),
            ("复制节点", self.copy_node),
            ("连接节点", self.connect_nodes),
            ("设为开场", self.set_start),
            ("设置结局", self.set_ending),
            ("新建变量", self.add_variable),
            ("预览", self.preview),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        outer.addLayout(toolbar)
        split = QSplitter()
        outer.addWidget(split, 1)
        self.scene = QGraphicsScene()
        self.graph = QGraphicsView(self.scene)
        split.addWidget(self.graph)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("剧情节点"))
        self.nodes_list = QListWidget()
        self.nodes_list.currentItemChanged.connect(self.on_node_selected)
        side_layout.addWidget(self.nodes_list)
        side_layout.addWidget(QLabel("播放顺序"))
        self.items_list = QListWidget()
        side_layout.addWidget(self.items_list)
        item_actions = QHBoxLayout()
        for label, handler in (
            ("添加", self.add_item), ("编辑", self.edit_item),
            ("上移", lambda: self.move_item(-1)), ("下移", lambda: self.move_item(1)),
            ("删除", self.delete_item),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            item_actions.addWidget(button)
        side_layout.addLayout(item_actions)
        choices = QHBoxLayout()
        for label, handler in (("添加选项", self.add_choice), ("删除选项", self.remove_choice)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            choices.addWidget(button)
        side_layout.addLayout(choices)
        split.addWidget(side)
        split.setSizes([850, 360])
        return container

    def update_title(self):
        title = self.project["title"] if self.project else "未打开项目"
        self.setWindowTitle(f"GalGame Maker · {title}{' *' if self.dirty else ''}")

    def mark_dirty(self):
        self.dirty = True
        self.update_title()

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
        self.root = root
        self.project = project
        self.editor = ProjectEditor(project)
        self.dirty = False
        self.update_title()
        self.refresh_story()
        self.asset_panel.refresh()

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
        if not self.project:
            return
        shapes = {}
        positions = self.project.get("editor", {}).get("node_positions", {})
        for index, node in enumerate(self.project["nodes"]):
            point = positions.get(node["id"], {"x": index % 4 * 240, "y": index // 4 * 145})
            shape = NodeShape(self, node, point["x"], point["y"])
            self.scene.addItem(shape)
            shapes[node["id"]] = shape
        for edge in self.project["edges"]:
            source = shapes.get(edge["from_node_id"])
            target = shapes.get(edge["to_node_id"])
            if source is None or target is None:
                continue
            start = source.scenePos() + QPointF(180, 39)
            end = target.scenePos() + QPointF(0, 39)
            line = self.scene.addLine(start.x(), start.y(), end.x(), end.y(), QPen(QColor("#7DA9C7"), 2))
            line.setZValue(-1)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))

    def on_node_selected(self, current, previous):
        self.items_list.clear()
        node = self.current_node()
        if node is None:
            return
        for item in node["items"]:
            label = item["type"]
            if item["type"] in {"dialogue", "narration"}:
                label += f" · {item['text'][:48]}"
            elif item.get("asset_id"):
                label += f" · {item['asset_id']}"
            row = QListWidgetItem(label)
            row.setData(Qt.ItemDataRole.UserRole, item["id"])
            self.items_list.addItem(row)
        for choice in node.get("choices", []):
            row = QListWidgetItem(f"选项 · {choice['text']}")
            row.setData(Qt.ItemDataRole.UserRole, choice["id"])
            self.items_list.addItem(row)

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
        self.mark_dirty()
        self.refresh_story(duplicate["id"])

    def connect_nodes(self):
        source = self.current_node()
        if source is None:
            return
        targets = [node for node in self.project["nodes"] if node["id"] != source["id"]]
        if not targets:
            return
        labels = [f"{node['name']} [{node['id'][:8]}]" for node in targets]
        selected, accepted = QInputDialog.getItem(self, "连接节点", "目标节点", labels, editable=False)
        if not accepted:
            return
        target = targets[labels.index(selected)]
        expression, accepted = QInputDialog.getText(
            self, "连接条件", "条件表达式，单一无选项后继可留空",
        )
        if not accepted:
            return
        try:
            condition = None
            if expression.strip():
                condition = parse_condition(
                    expression, self.project["variables"], source.get("choices", []),
                )
            self.editor.add_edge(source["id"], target["id"], condition)
        except ValueError as error:
            QMessageBox.warning(self, "无法连接", str(error))
            return
        self.mark_dirty()
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
        if any(edge["from_node_id"] == node["id"] for edge in self.project["edges"]):
            QMessageBox.warning(self, "无法设置结局", "结局节点不能有后继")
            return
        if node.get("choices"):
            QMessageBox.warning(self, "无法设置结局", "结局节点不能有选项")
            return
        node["ending"] = {"credits_video_asset_id": None}
        self.mark_dirty()
        self.refresh_story(node["id"])

    def add_variable(self):
        if not self.require_project():
            return
        name, accepted = QInputDialog.getText(self, "新建变量", "变量名称")
        if not accepted or not name.strip():
            return
        if any(variable["name"] == name for variable in self.project["variables"]):
            QMessageBox.warning(self, "变量", "变量名称已存在")
            return
        kind, accepted = QInputDialog.getItem(self, "变量类型", "类型", ["int", "bool"], editable=False)
        if not accepted:
            return
        if kind == "int":
            value, accepted = QInputDialog.getInt(self, "初始值", name)
        else:
            selected, accepted = QInputDialog.getItem(
                self, "初始值", name, ["false", "true"], editable=False,
            )
            value = selected == "true"
        if accepted:
            from .project import new_id
            self.project["variables"].append({
                "id": new_id(), "name": name.strip(), "type": kind, "initial_value": value,
            })
            self.mark_dirty()

    def add_choice(self):
        node = self.current_node()
        if node is None:
            return
        text, accepted = QInputDialog.getText(self, "添加选项", "选项文字")
        if accepted and text.strip():
            from .project import new_id
            node.setdefault("choices", []).append({"id": new_id(), "text": text.strip()})
            self.mark_dirty()
            self.on_node_selected(None, None)

    def remove_choice(self):
        node = self.current_node()
        row = self.items_list.currentItem()
        if node is None or row is None:
            return
        choice_id = row.data(Qt.ItemDataRole.UserRole)
        choices = node.get("choices", [])
        node["choices"] = [choice for choice in choices if choice["id"] != choice_id]
        if len(node["choices"]) != len(choices):
            self.mark_dirty()
            self.on_node_selected(None, None)

    def _asset_options(self, kind: str) -> tuple[list[str], list[dict]]:
        assets = [asset for asset in self.project["assets"] if asset["kind"] == kind]
        return [f"{asset['name']} [{asset['id'][:8]}]" for asset in assets], assets

    def add_item(self):
        node = self.current_node()
        if node is None:
            return
        kinds = [
            "dialogue", "narration", "set_background", "show_sprite",
            "hide_sprite", "play_bgm", "stop_bgm", "play_sfx",
        ]
        kind, accepted = QInputDialog.getItem(self, "添加演出", "类型", kinds, editable=False)
        if not accepted:
            return
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
            labels, assets = self._asset_options(required_kind)
            if kind == "set_background":
                labels = ["清除背景", *labels]
            if not labels:
                QMessageBox.information(self, "素材", "请先导入对应类型的素材")
                return
            selected, accepted = QInputDialog.getItem(
                self, "选择素材", "素材", labels, editable=False,
            )
            if not accepted:
                return
            fields["asset_id"] = None if selected == "清除背景" else assets[
                labels.index(selected) - (1 if kind == "set_background" else 0)
            ]["id"]
        if kind in {"show_sprite", "hide_sprite"}:
            slot, accepted = QInputDialog.getItem(
                self, "立绘槽位", "槽位", ["left", "center", "right"], editable=False,
            )
            if not accepted:
                return
            fields["slot"] = slot
        self.editor.add_item(node["id"], kind, **fields)
        self.mark_dirty()
        self.on_node_selected(None, None)

    def edit_item(self):
        node = self.current_node()
        row = self.items_list.currentItem()
        if node is None or row is None:
            return
        item_id = row.data(Qt.ItemDataRole.UserRole)
        item = next((entry for entry in node["items"] if entry["id"] == item_id), None)
        if item is None or item["type"] not in {"dialogue", "narration"}:
            return
        text, accepted = QInputDialog.getMultiLineText(
            self, "编辑文本", "正文", item["text"],
        )
        if accepted and text:
            item["text"] = text
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

    def preview(self):
        if not self.require_project():
            return
        try:
            game = compile_game(self.project, self.root)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法预览", str(error))
            return
        self.preview_window = PlayerWindow(game, self.root)
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
            export_game(self.project, self.root, Path(target) / name, player)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        QMessageBox.information(self, "导出完成", str(Path(target) / name))

    def closeEvent(self, event):
        if self._change_project():
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

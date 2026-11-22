"""Qt Widgets maker for editing a movable visual-novel project."""

from __future__ import annotations

import math
import sys
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QGraphicsItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsTextItem, QGraphicsView, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .branch_check import BranchCheckCache, NodeCheckState
from .condition_parser import format_condition, parse_condition
from .editing import ProjectEditor
from .export import export_game
from .maker_assets import AssetPanel
from .maker_characters import CharacterPanel
from .maker_text import TextPanel
from .maker_theme import ThemePanel
from .player import PlayerWindow
from .project import create_project, load_project, save_project
from .runtime_data import compile_game


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


class NodeShape(QGraphicsRectItem):
    def __init__(
        self,
        window: "MakerWindow",
        node: dict,
        x: int,
        y: int,
        status: str = "pass",
        is_start: bool = False,
        is_ending: bool = False,
        is_unended_leaf: bool = False,
    ):
        super().__init__(0, 0, 180, 78)
        self.window = window
        self.node = node
        self.setPos(x, y)
        border = "#69819A"
        fill = "#283747"
        badges: list[str] = []
        if is_start:
            border = "#4EA8DE"
            badges.append("开场")
        if is_ending:
            border = "#52B788"
            badges.append("结局")
        elif is_unended_leaf:
            border = "#D4A373"
            badges.append("未设结局")
        if status == "conflict":
            border = "#E05A47"
            fill = "#4A2525"
            badges.append("冲突")
        elif status == "incomplete":
            border = "#E0A447"
            fill = "#473A22"
            badges.append("未完成")
        elif status in {"pending", "checking"}:
            badges.append("待检查")
        self.setBrush(QColor(fill))
        self.setPen(QPen(QColor(border), 2))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        text = node["name"]
        if badges:
            text += f"\n[{' · '.join(badges)}]"
        caption = QGraphicsTextItem(text, self)
        caption.setDefaultTextColor(QColor("white"))
        caption.setTextWidth(168)
        caption.setPos(6, 12)

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
        self.branch_cache = BranchCheckCache()
        self._branch_worker: BranchCheckWorker | None = None
        self._branch_recheck_needed = False
        self.branch_check_timer = QTimer(self)
        self.branch_check_timer.setSingleShot(True)
        self.branch_check_timer.setInterval(300)
        self.branch_check_timer.timeout.connect(self._start_background_branch_check)
        self.setWindowTitle("GalGame Maker")
        self.resize(1280, 820)
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
            ("节点改名", self.rename_node),
            ("复制节点", self.copy_node),
            ("删除节点", self.delete_node),
            ("连接节点", self.connect_nodes),
            ("设为开场", self.set_start),
            ("设置结局", self.set_ending),
            ("片尾视频", self.set_credits_video),
            ("新建变量", self.add_variable),
            ("检查分支", self.retry_branch_check),
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
        side_layout.addWidget(QLabel("后继连接"))
        self.edges_list = QListWidget()
        side_layout.addWidget(self.edges_list)
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
        side_layout.addLayout(edge_actions)
        split.addWidget(side)
        split.setSizes([850, 360])
        return container

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
        if not self.project:
            return
        shapes = {}
        positions = self.project.get("editor", {}).get("node_positions", {})
        outgoing_counts = {node["id"]: 0 for node in self.project["nodes"]}
        for edge in self.project["edges"]:
            if edge["from_node_id"] in outgoing_counts:
                outgoing_counts[edge["from_node_id"]] += 1
        for index, node in enumerate(self.project["nodes"]):
            point = positions.get(node["id"], {"x": index % 4 * 240, "y": index // 4 * 145})
            state = self.branch_cache.get_node_state(self.project, node["id"])
            is_start = node["id"] == self.project.get("start_node_id")
            is_ending = node.get("ending") is not None
            is_unended_leaf = (not is_ending) and outgoing_counts.get(node["id"], 0) == 0
            shape = NodeShape(
                self, node, point["x"], point["y"],
                status=state.status,
                is_start=is_start,
                is_ending=is_ending,
                is_unended_leaf=is_unended_leaf,
            )
            self.scene.addItem(shape)
            shapes[node["id"]] = shape
        for edge in self.project["edges"]:
            source = shapes.get(edge["from_node_id"])
            target = shapes.get(edge["to_node_id"])
            if source is None or target is None:
                continue
            start = source.scenePos() + QPointF(180, 39)
            end = target.scenePos() + QPointF(0, 39)
            line_pen = QPen(QColor("#7DA9C7"), 2)
            line = self.scene.addLine(start.x(), start.y(), end.x(), end.y(), line_pen)
            line.setZValue(-1)
            dx, dy = end.x() - start.x(), end.y() - start.y()
            distance = math.hypot(dx, dy)
            if distance > 0:
                ux, uy = dx / distance, dy / distance
                base_x, base_y = end.x() - ux * 12, end.y() - uy * 12
                arrow = QPolygonF([
                    end,
                    QPointF(base_x - uy * 6, base_y + ux * 6),
                    QPointF(base_x + uy * 6, base_y - ux * 6),
                ])
                arrow_item = self.scene.addPolygon(arrow, line_pen, QColor("#7DA9C7"))
                arrow_item.setZValue(-1)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))

    def on_node_selected(self, current, previous):
        self.items_list.clear()
        node = self.current_node()
        if node is None:
            self.edges_list.clear()
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
        self.refresh_edges(node)

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
        self.mark_dirty()
        self.refresh_story(duplicate["id"])

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
            edge = self.editor.add_edge(source["id"], target["id"], condition)
        except ValueError as error:
            QMessageBox.warning(self, "无法连接", str(error))
            return
        self.mark_dirty()
        self.draw_graph()
        self.refresh_edges(source)
        for index in range(self.edges_list.count()):
            row = self.edges_list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == edge["id"]:
                self.edges_list.setCurrentItem(row)
                break

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
                "id": new_id(), "name": name.strip(), "type": kind, "initial_value": value,
            })
            self.mark_dirty()

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
        if item is None:
            return
        kind = item["type"]
        if kind in {"dialogue", "narration"}:
            text, accepted = QInputDialog.getMultiLineText(
                self, "编辑文本", "正文", item["text"],
            )
            if not accepted or not text:
                return
            item["text"] = text
            self.text_panel.refresh()
        elif kind in {"set_background", "show_sprite", "play_bgm", "play_sfx"}:
            asset_kind = "image" if kind in {"set_background", "show_sprite"} else (
                "bgm" if kind == "play_bgm" else "sfx"
            )
            labels, assets = self._asset_options(asset_kind)
            if kind == "set_background":
                labels = ["清除背景", *labels]
            if not labels:
                return
            selected, accepted = QInputDialog.getItem(
                self, "更换素材", "素材", labels, editable=False,
            )
            if not accepted:
                return
            asset_id = None if selected == "清除背景" else assets[
                labels.index(selected) - (1 if kind == "set_background" else 0)
            ]["id"]
            if kind == "show_sprite":
                placement = item.get("placement") or {}
                scale, accepted = QInputDialog.getInt(
                    self, "立绘缩放", "百分比", placement.get("scale_pct", 100), 25, 200,
                )
                if not accepted:
                    return
                item.setdefault("placement", {})["scale_pct"] = scale
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

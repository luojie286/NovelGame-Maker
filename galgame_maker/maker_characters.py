"""Character records and their directed or undirected relationship graph."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)


class CharacterPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        outer = QVBoxLayout(self)
        split = QSplitter()
        outer.addWidget(split)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("角色"))
        self.characters = QListWidget()
        self.characters.currentItemChanged.connect(self.selected_character_changed)
        left_layout.addWidget(self.characters)
        char_actions = QHBoxLayout()
        add_button = QPushButton("新建角色")
        add_button.clicked.connect(self.add_character)
        char_actions.addWidget(add_button)
        delete_button = QPushButton("删除角色")
        delete_button.clicked.connect(self.delete_character)
        char_actions.addWidget(delete_button)
        left_layout.addLayout(char_actions)
        form = QFormLayout()
        self.name = QLineEdit()
        self.description = QTextEdit()
        self.art = QComboBox()
        form.addRow("姓名", self.name)
        form.addRow("介绍", self.description)
        form.addRow("设定识别图", self.art)
        left_layout.addLayout(form)
        update_button = QPushButton("保存角色设置")
        update_button.clicked.connect(self.update_character)
        left_layout.addWidget(update_button)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("人物关系"))
        self.scene = QGraphicsScene()
        self.graph = QGraphicsView(self.scene)
        right_layout.addWidget(self.graph, 1)
        self.relations = QListWidget()
        right_layout.addWidget(self.relations)
        relation_actions = QHBoxLayout()
        for caption, action in (
            ("新建关系", self.add_relation),
            ("编辑关系", self.edit_relation),
            ("删除关系", self.remove_relation),
        ):
            button = QPushButton(caption)
            button.clicked.connect(action)
            relation_actions.addWidget(button)
        right_layout.addLayout(relation_actions)
        split.addWidget(right)
        split.setSizes([390, 780])

    def selected_character(self) -> dict | None:
        row = self.characters.currentItem()
        if row is None or self.window.project is None:
            return None
        character_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (character for character in self.window.project["characters"]
             if character["id"] == character_id),
            None,
        )

    def selected_relation(self) -> dict | None:
        row = self.relations.currentItem()
        if row is None or self.window.project is None:
            return None
        relation_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (relation for relation in self.window.project.get("relationships", [])
             if relation["id"] == relation_id),
            None,
        )

    def refresh(self):
        self.characters.blockSignals(True)
        self.characters.clear()
        if self.window.project:
            for character in self.window.project["characters"]:
                row = QListWidgetItem(character["name"])
                row.setData(Qt.ItemDataRole.UserRole, character["id"])
                self.characters.addItem(row)
        self.characters.blockSignals(False)
        if self.characters.count():
            self.characters.setCurrentRow(0)
        else:
            self.name.clear()
            self.description.clear()
            self.art.clear()
        self.refresh_relations()

    def selected_character_changed(self, current, previous):
        character = self.selected_character()
        if character is None:
            return
        self.name.setText(character["name"])
        self.description.setPlainText(character.get("description", ""))
        self.art.clear()
        self.art.addItem("无", None)
        for asset in self.window.project["assets"]:
            if asset["kind"] == "image":
                self.art.addItem(asset["name"], asset["id"])
        index = self.art.findData(character.get("reference_art_id"))
        self.art.setCurrentIndex(max(0, index))

    def add_character(self):
        if not self.window.require_project():
            return
        name, accepted = QInputDialog.getText(self, "新建角色", "姓名")
        if not accepted or not name.strip():
            return
        character = self.window.editor.add_character(name.strip())
        self.window.mark_dirty()
        self.refresh()
        for index in range(self.characters.count()):
            row = self.characters.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == character["id"]:
                self.characters.setCurrentItem(row)
                break

    def delete_character(self):
        character = self.selected_character()
        if character is None:
            return
        char_id = character["id"]
        references: list[str] = []
        for node in self.window.project["nodes"]:
            for item in node["items"]:
                if item.get("character_id") == char_id:
                    references.append(f"节点 {node['name']} 对白")
        for relation in self.window.project.get("relationships", []):
            if relation["from_character_id"] == char_id or relation["to_character_id"] == char_id:
                references.append(f"人物关系 {relation['label']}")
        if references:
            QMessageBox.warning(self, "角色仍在使用", "\n".join(references))
            return
        self.window.project["characters"].remove(character)
        self.window.mark_dirty()
        self.refresh()

    def update_character(self):
        character = self.selected_character()
        if character is None or not self.name.text().strip():
            return
        character["name"] = self.name.text().strip()
        character["description"] = self.description.toPlainText()
        character["reference_art_id"] = self.art.currentData()
        self.window.mark_dirty()
        self.characters.currentItem().setText(character["name"])
        self.refresh_relations()

    def refresh_relations(self):
        self.relations.clear()
        self.scene.clear()
        if not self.window.project:
            return
        characters = self.window.project["characters"]
        positions = {}
        count = max(1, len(characters))
        for index, character in enumerate(characters):
            angle = 2 * math.pi * index / count
            x = 320 + math.cos(angle) * 230
            y = 250 + math.sin(angle) * 170
            positions[character["id"]] = (x, y)
            self.scene.addEllipse(x - 38, y - 38, 76, 76, QPen(QColor("#466E89"), 2), QColor("#243A48"))
            label = self.scene.addText(character["name"])
            label.setDefaultTextColor(QColor("white"))
            label.setPos(x - 28, y - 12)
        by_id = {character["id"]: character for character in characters}
        for relation in self.window.project.get("relationships", []):
            source_id = relation["from_character_id"]
            target_id = relation["to_character_id"]
            if source_id not in positions or target_id not in positions:
                continue
            x1, y1 = positions[source_id]
            x2, y2 = positions[target_id]
            line = self.scene.addLine(x1, y1, x2, y2, QPen(QColor("#B49B6B"), 2))
            line.setZValue(-1)
            if relation.get("directed", True):
                distance = math.hypot(x2 - x1, y2 - y1)
                if distance:
                    along_x, along_y = (x2 - x1) / distance, (y2 - y1) / distance
                    tip_x, tip_y = x2 - along_x * 40, y2 - along_y * 40
                    wing_x, wing_y = tip_x - along_x * 14, tip_y - along_y * 14
                    arrow = QPolygonF([
                        QPointF(tip_x, tip_y),
                        QPointF(wing_x - along_y * 7, wing_y + along_x * 7),
                        QPointF(wing_x + along_y * 7, wing_y - along_x * 7),
                    ])
                    self.scene.addPolygon(arrow, QPen(QColor("#B49B6B")), QColor("#B49B6B"))
            label = self.scene.addText(relation["label"])
            label.setDefaultTextColor(QColor("#3C2D13"))
            label.setPos((x1 + x2) / 2, (y1 + y2) / 2)
            arrow = "→" if relation.get("directed", True) else "↔"
            row = QListWidgetItem(
                f"{by_id[source_id]['name']} {arrow} {by_id[target_id]['name']} · {relation['label']}"
            )
            row.setData(Qt.ItemDataRole.UserRole, relation["id"])
            self.relations.addItem(row)
        self.scene.setSceneRect(0, 0, 650, 500)

    def add_relation(self):
        if not self.window.require_project():
            return
        characters = self.window.project["characters"]
        if len(characters) < 2:
            QMessageBox.information(self, "人物关系", "请先创建至少两名角色")
            return
        labels = [character["name"] for character in characters]
        source, accepted = QInputDialog.getItem(self, "人物关系", "起点", labels, editable=False)
        if not accepted:
            return
        target, accepted = QInputDialog.getItem(self, "人物关系", "终点", labels, editable=False)
        if not accepted:
            return
        direction, accepted = QInputDialog.getItem(
            self, "人物关系", "方向", ["有向", "无向"], editable=False,
        )
        if not accepted:
            return
        relation = self.window.editor.add_relationship(
            characters[labels.index(source)]["id"], characters[labels.index(target)]["id"],
            directed=direction == "有向",
        )
        self.window.mark_dirty()
        self.refresh_relations()
        for index in range(self.relations.count()):
            row = self.relations.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == relation["id"]:
                self.relations.setCurrentItem(row)
                break

    def edit_relation(self):
        relation = self.selected_relation()
        if relation is None:
            return
        label, accepted = QInputDialog.getText(
            self, "编辑关系", "描述", text=relation["label"],
        )
        if not accepted:
            return
        direction, accepted = QInputDialog.getItem(
            self, "编辑关系", "方向", ["有向", "无向"],
            0 if relation.get("directed", True) else 1,
            editable=False,
        )
        if not accepted:
            return
        relation["label"] = label
        relation["directed"] = direction == "有向"
        self.window.mark_dirty()
        self.refresh_relations()

    def remove_relation(self):
        relation = self.selected_relation()
        if relation is None:
            return
        self.window.project["relationships"].remove(relation)
        self.window.mark_dirty()
        self.refresh_relations()

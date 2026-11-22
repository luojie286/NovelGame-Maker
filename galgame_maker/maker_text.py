"""Maker text documents, live excerpts, and local content search."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from .editing import EditError
from .project import new_id


class TextPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        outer = QVBoxLayout(self)
        search_bar = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索角色、节点、对白、资料与素材")
        self.query.returnPressed.connect(self.search)
        search_bar.addWidget(self.query)
        search_button = QPushButton("搜索")
        search_button.clicked.connect(self.search)
        search_bar.addWidget(search_button)
        outer.addLayout(search_bar)
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self.open_result)
        outer.addWidget(self.results)

        split = QSplitter()
        outer.addWidget(split, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("剧本与设定"))
        self.documents = QListWidget()
        self.documents.currentItemChanged.connect(self.document_selected)
        left_layout.addWidget(self.documents)
        document_buttons = QHBoxLayout()
        for caption, action in (
            ("新建", self.add_document), ("保存正文", self.update_document),
        ):
            button = QPushButton(caption)
            button.clicked.connect(action)
            document_buttons.addWidget(button)
        left_layout.addLayout(document_buttons)
        self.document_title = QLineEdit()
        self.document_title.setPlaceholderText("标题")
        left_layout.addWidget(self.document_title)
        self.document_text = QTextEdit()
        left_layout.addWidget(self.document_text)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("文本摘录"))
        self.excerpts = QListWidget()
        self.excerpts.currentItemChanged.connect(self.excerpt_selected)
        right_layout.addWidget(self.excerpts)
        excerpt_buttons = QHBoxLayout()
        for caption, action in (
            ("收录对白", self.add_excerpt),
            ("重新绑定", self.rebind_excerpt),
            ("移除", self.remove_excerpt),
        ):
            button = QPushButton(caption)
            button.clicked.connect(action)
            excerpt_buttons.addWidget(button)
        right_layout.addLayout(excerpt_buttons)
        self.excerpt_text = QTextEdit()
        self.excerpt_text.setReadOnly(True)
        right_layout.addWidget(self.excerpt_text)
        split.addWidget(right)
        split.setSizes([580, 580])

    def selected_document(self) -> dict | None:
        row = self.documents.currentItem()
        if row is None or self.window.project is None:
            return None
        document_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (document for document in self.window.project.get("documents", [])
             if document["id"] == document_id), None,
        )

    def selected_excerpt(self) -> dict | None:
        row = self.excerpts.currentItem()
        if row is None or self.window.project is None:
            return None
        excerpt_id = row.data(Qt.ItemDataRole.UserRole)
        return next(
            (excerpt for excerpt in self.window.project.get("excerpts", [])
             if excerpt["id"] == excerpt_id), None,
        )

    def refresh(self):
        self.documents.clear()
        self.excerpts.clear()
        if self.window.project is None:
            self.document_title.clear()
            self.document_text.clear()
            self.excerpt_text.clear()
            return
        for document in self.window.project.get("documents", []):
            row = QListWidgetItem(f"{document['title']} · {document['kind']}")
            row.setData(Qt.ItemDataRole.UserRole, document["id"])
            self.documents.addItem(row)
        for excerpt in self.window.project.get("excerpts", []):
            text = self.window.editor.excerpt_text(excerpt)
            label = (text or "原文已删除或移动").replace("\n", " ")[:72]
            row = QListWidgetItem(label)
            row.setData(Qt.ItemDataRole.UserRole, excerpt["id"])
            self.excerpts.addItem(row)
        if self.documents.count():
            self.documents.setCurrentRow(0)
        if self.excerpts.count():
            self.excerpts.setCurrentRow(0)

    def document_selected(self, current, previous):
        document = self.selected_document()
        if document is None:
            self.document_title.clear()
            self.document_text.clear()
            return
        self.document_title.setText(document["title"])
        self.document_text.setPlainText(document["text"])

    def excerpt_selected(self, current, previous):
        excerpt = self.selected_excerpt()
        if excerpt is None:
            self.excerpt_text.clear()
            return
        text = self.window.editor.excerpt_text(excerpt)
        self.excerpt_text.setPlainText(text if text is not None else "原文已删除或移动")

    def add_document(self):
        if not self.window.require_project():
            return
        kind, accepted = QInputDialog.getItem(
            self, "新建资料", "类型", ["script", "setting"], editable=False,
        )
        if not accepted:
            return
        title, accepted = QInputDialog.getText(self, "新建资料", "标题")
        if not accepted or not title.strip():
            return
        document = {
            "id": new_id(), "kind": kind, "title": title.strip(),
            "text": "", "source_asset_id": None,
        }
        self.window.project.setdefault("documents", []).append(document)
        self.window.mark_dirty()
        self.refresh()
        for index in range(self.documents.count()):
            row = self.documents.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == document["id"]:
                self.documents.setCurrentItem(row)
                break

    def update_document(self):
        document = self.selected_document()
        if document is None or not self.document_title.text().strip():
            return
        document["title"] = self.document_title.text().strip()
        document["text"] = self.document_text.toPlainText()
        self.window.mark_dirty()
        self.documents.currentItem().setText(f"{document['title']} · {document['kind']}")

    def choose_text_items(self) -> tuple[str, list[str]] | None:
        nodes = [
            node for node in self.window.project["nodes"]
            if any(item["type"] in {"dialogue", "narration"} for item in node["items"])
        ]
        if not nodes:
            QMessageBox.information(self, "文本摘录", "请先在节点中写对白或旁白")
            return None
        labels = [f"{node['name']} [{node['id'][:8]}]" for node in nodes]
        selected, accepted = QInputDialog.getItem(
            self, "选择来源节点", "节点", labels, editable=False,
        )
        if not accepted:
            return None
        node = nodes[labels.index(selected)]
        dialog = QDialog(self)
        dialog.setWindowTitle("选择连续的文本条目")
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        listing.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for item in node["items"]:
            if item["type"] in {"dialogue", "narration"}:
                row = QListWidgetItem(item["text"][:80])
                row.setData(Qt.ItemDataRole.UserRole, item["id"])
                listing.addItem(row)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        selected_ids = [
            listing.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(listing.count()) if listing.item(index).isSelected()
        ]
        return node["id"], selected_ids

    def add_excerpt(self):
        if not self.window.require_project():
            return
        selection = self.choose_text_items()
        if selection is None:
            return
        try:
            self.window.editor.add_excerpt(*selection)
        except EditError as error:
            QMessageBox.warning(self, "无法收录", str(error))
            return
        self.window.mark_dirty()
        self.refresh()

    def rebind_excerpt(self):
        excerpt = self.selected_excerpt()
        if excerpt is None:
            return
        selection = self.choose_text_items()
        if selection is None:
            return
        try:
            temporary = self.window.editor.add_excerpt(*selection)
        except EditError as error:
            QMessageBox.warning(self, "无法绑定", str(error))
            return
        self.window.project["excerpts"].remove(temporary)
        excerpt["node_id"], excerpt["item_ids"] = selection
        self.window.mark_dirty()
        self.refresh()

    def remove_excerpt(self):
        excerpt = self.selected_excerpt()
        if excerpt is None:
            return
        self.window.project["excerpts"].remove(excerpt)
        self.window.mark_dirty()
        self.refresh()

    def search(self):
        self.results.clear()
        if self.window.project is None or not self.query.text().strip():
            return
        for result in self.window.editor.search(self.query.text()):
            row = QListWidgetItem(f"{result.kind} · {result.label}")
            row.setData(Qt.ItemDataRole.UserRole, (result.kind, result.entity_id, result.node_id))
            self.results.addItem(row)
        for excerpt in self.window.project.get("excerpts", []):
            text = self.window.editor.excerpt_text(excerpt)
            if text is not None and self.query.text().casefold() in text.casefold():
                row = QListWidgetItem(f"摘录 · {text[:72]}")
                row.setData(Qt.ItemDataRole.UserRole, ("excerpt", excerpt["id"], excerpt["node_id"]))
                self.results.addItem(row)

    def open_result(self, row: QListWidgetItem):
        kind, entity_id, node_id = row.data(Qt.ItemDataRole.UserRole)
        if kind in {"node", "text"}:
            self.window.tabs.setCurrentIndex(0)
            self.window.select_node(node_id or entity_id)
            if kind == "text":
                for index in range(self.window.items_list.count()):
                    item = self.window.items_list.item(index)
                    if item.data(Qt.ItemDataRole.UserRole) == entity_id:
                        self.window.items_list.setCurrentItem(item)
                        break
        elif kind == "excerpt":
            for index in range(self.excerpts.count()):
                item = self.excerpts.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == entity_id:
                    self.excerpts.setCurrentItem(item)
                    break
        elif kind == "asset":
            self.window.tabs.setCurrentWidget(self.window.asset_panel)
            self.window.asset_panel.kind.setCurrentText("全部")
            self.window.asset_panel.query.clear()
            for index in range(self.window.asset_panel.list.count()):
                item = self.window.asset_panel.list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == entity_id:
                    self.window.asset_panel.list.setCurrentItem(item)
                    break
        elif kind == "character":
            self.window.tabs.setCurrentWidget(self.window.character_panel)
            for index in range(self.window.character_panel.characters.count()):
                item = self.window.character_panel.characters.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == entity_id:
                    self.window.character_panel.characters.setCurrentItem(item)
                    break
        elif kind == "document":
            for index in range(self.documents.count()):
                item = self.documents.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == entity_id:
                    self.documents.setCurrentItem(item)
                    break

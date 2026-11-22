"""Editor controls for the player's version-one classic theme."""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .runtime_data import DEFAULT_STYLE


class ThemePanel(QWidget):
    PARTS = {"对白框": "dialogue", "选项按钮": "choice", "菜单按钮": "menu"}

    def __init__(self, window):
        super().__init__()
        self.window = window
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("成品游戏界面"))
        self.part = QComboBox()
        self.part.addItems(self.PARTS)
        self.part.currentTextChanged.connect(self.load_part)
        outer.addWidget(self.part)
        form = QFormLayout()
        self.image = QComboBox()
        form.addRow("背景图片", self.image)
        self.text_color = QLineEdit()
        form.addRow("文字颜色", self.text_color)
        self.background_color = QLineEdit()
        form.addRow("底色", self.background_color)
        self.font_size = QSpinBox()
        self.font_size.setRange(12, 72)
        form.addRow("字号", self.font_size)
        outer.addLayout(form)
        actions = QHBoxLayout()
        for caption, handler in (
            ("选择文字颜色", lambda: self.pick_color(self.text_color)),
            ("选择底色", lambda: self.pick_color(self.background_color)),
            ("应用", self.apply),
            ("恢复默认", self.reset),
        ):
            button = QPushButton(caption)
            button.clicked.connect(handler)
            actions.addWidget(button)
        outer.addLayout(actions)
        outer.addStretch()

    def current_part(self) -> str:
        return self.PARTS[self.part.currentText()]

    def load_part(self, *_):
        self.image.clear()
        self.image.addItem("使用底色", None)
        if self.window.project is None:
            return
        for asset in self.window.project["assets"]:
            if asset["kind"] == "image":
                self.image.addItem(asset["name"], asset["id"])
        part = self.current_part()
        style = DEFAULT_STYLE.copy()
        if part == "dialogue":
            style["font_size_px"] = 32
        style.update(self.window.project.get("theme", {}).get(part, {}))
        self.image.setCurrentIndex(max(0, self.image.findData(style["image_asset_id"])))
        self.text_color.setText(style["text_color"])
        self.background_color.setText(style["background_color"])
        self.font_size.setValue(style["font_size_px"])

    def pick_color(self, field: QLineEdit):
        selected = QColorDialog.getColor(parent=self)
        if selected.isValid():
            field.setText(selected.name().upper())

    def apply(self):
        if not self.window.require_project():
            return
        colors = (self.text_color.text(), self.background_color.text())
        if any(re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None for color in colors):
            QMessageBox.warning(self, "主题颜色", "请输入 #RRGGBB 格式的颜色")
            return
        part = self.current_part()
        default_size = 32 if part == "dialogue" else 28
        proposed = {
            "image_asset_id": self.image.currentData(),
            "text_color": colors[0].upper(),
            "font_size_px": self.font_size.value(),
            "background_color": colors[1].upper(),
        }
        defaults = {**DEFAULT_STYLE, "font_size_px": default_size}
        overrides = {key: value for key, value in proposed.items() if value != defaults[key]}
        theme = self.window.project.setdefault("theme", {})
        theme["version"] = 1
        theme["layout_id"] = "classic"
        if overrides:
            theme[part] = overrides
        else:
            theme.pop(part, None)
        self.window.mark_dirty()

    def reset(self):
        if not self.window.require_project():
            return
        self.window.project.setdefault("theme", {}).pop(self.current_part(), None)
        self.window.mark_dirty()
        self.load_part()

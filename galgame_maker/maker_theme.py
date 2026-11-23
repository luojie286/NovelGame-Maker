"""Editor controls for the player's version-one classic theme."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from .project import resolve_asset_path
from .runtime_data import DEFAULT_STYLE


class ThemePanel(QWidget):
    PARTS = {"对白框": "dialogue", "选项按钮": "choice", "菜单按钮": "menu"}

    def __init__(self, window):
        super().__init__()
        self.window = window
        outer = QVBoxLayout(self)
        split = QSplitter()
        outer.addWidget(split, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        title = QLabel("成品游戏界面样式")
        title.setProperty("sectionTitle", True)
        left_layout.addWidget(title)
        self.part = QComboBox()
        self.part.addItems(self.PARTS)
        self.part.currentTextChanged.connect(self.load_part)
        left_layout.addWidget(self.part)
        form = QFormLayout()
        self.image = QComboBox()
        self.image.currentIndexChanged.connect(self.update_preview)
        form.addRow("背景图片", self.image)
        self.text_color = QLineEdit()
        self.text_color.textChanged.connect(self.update_preview)
        form.addRow("文字颜色", self.text_color)
        self.background_color = QLineEdit()
        self.background_color.textChanged.connect(self.update_preview)
        form.addRow("底色", self.background_color)
        self.font_size = QSpinBox()
        self.font_size.setRange(12, 72)
        self.font_size.valueChanged.connect(self.update_preview)
        form.addRow("字号", self.font_size)
        left_layout.addLayout(form)
        actions = QHBoxLayout()
        for caption, handler in (
            ("选择文字颜色", lambda: self.pick_color(self.text_color)),
            ("选择底色", lambda: self.pick_color(self.background_color)),
            ("应用", self.apply),
            ("恢复默认", self.reset),
        ):
            button = QPushButton(caption)
            if caption == "应用":
                button.setProperty("accent", True)
            button.clicked.connect(handler)
            actions.addWidget(button)
        left_layout.addLayout(actions)
        left_layout.addStretch()
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        pv_title = QLabel("实时样式预览")
        pv_title.setProperty("sectionTitle", True)
        right_layout.addWidget(pv_title)
        self.preview_box = QFrame()
        self.preview_box.setMinimumHeight(200)
        box_layout = QVBoxLayout(self.preview_box)
        self.preview_img = QLabel()
        self.preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_layout.addWidget(self.preview_img)
        self.preview_sample = QLabel("示例文字：黄昏的光线洒在教室窗台上。")
        self.preview_sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_sample.setWordWrap(True)
        box_layout.addWidget(self.preview_sample)
        right_layout.addWidget(self.preview_box)
        right_layout.addStretch()
        split.addWidget(right)
        split.setSizes([520, 520])

    def current_part(self) -> str:
        return self.PARTS[self.part.currentText()]

    def update_preview(self, *_):
        bg = self.background_color.text()
        fg = self.text_color.text()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", bg):
            bg = "#20242C"
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", fg):
            fg = "#FFFFFF"
        size = self.font_size.value()
        self.preview_box.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid #4EA8DE; border-radius: 10px; }}"
        )
        self.preview_sample.setStyleSheet(
            f"color: {fg}; background: transparent; font-size: {min(size, 42)}px;"
        )
        asset_id = self.image.currentData()
        if asset_id and self.window.project and self.window.root:
            asset = next((a for a in self.window.project["assets"] if a["id"] == asset_id), None)
            if asset:
                pix = QPixmap(str(resolve_asset_path(self.window.root, asset["path"])))
                if not pix.isNull():
                    self.preview_img.setPixmap(pix.scaled(
                        360, 110, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                    self.preview_img.show()
                    return
        self.preview_img.clear()
        self.preview_img.hide()

    def load_part(self, *_):
        self.image.blockSignals(True)
        self.image.clear()
        self.image.addItem("使用底色", None)
        if self.window.project is None:
            self.image.blockSignals(False)
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
        self.image.blockSignals(False)
        self.text_color.setText(style["text_color"])
        self.background_color.setText(style["background_color"])
        self.font_size.setValue(style["font_size_px"])
        self.update_preview()

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

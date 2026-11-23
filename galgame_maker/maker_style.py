"""Modern dark studio stylesheet for GalGame Maker."""

from __future__ import annotations

MAKER_STYLESHEET = """
QMainWindow, QDialog, QWidget {
    background-color: #181B22;
    color: #E6EDF7;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QMenuBar {
    background-color: #13161C;
    color: #D8E1EE;
    border-bottom: 1px solid #282F3D;
    padding: 2px 6px;
}
QMenuBar::item {
    padding: 5px 12px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #2A3242;
}

QMenu {
    background-color: #202530;
    border: 1px solid #323B4E;
    padding: 4px;
}
QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #2F4F6F;
}

QTabWidget::pane {
    border: 1px solid #2A3140;
    background-color: #1B1F28;
    border-radius: 6px;
}
QTabBar::tab {
    background-color: #151820;
    color: #94A3B8;
    padding: 8px 18px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    border: 1px solid #252B38;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #232936;
    color: #F4F7FB;
    font-weight: bold;
    border-color: #3A465C;
}
QTabBar::tab:hover:!selected {
    background-color: #1E232E;
    color: #CBD5E1;
}

QPushButton {
    background-color: #262D3B;
    color: #E6EDF7;
    border: 1px solid #374155;
    border-radius: 6px;
    padding: 6px 12px;
    min-height: 22px;
}
QPushButton:hover {
    background-color: #313B4D;
    border-color: #4EA8DE;
}
QPushButton:pressed {
    background-color: #1F2633;
}
QPushButton:disabled {
    background-color: #1C2029;
    color: #5C677D;
    border-color: #262C38;
}
QPushButton[accent="true"] {
    background-color: #246894;
    border-color: #4EA8DE;
    color: #FFFFFF;
    font-weight: bold;
}
QPushButton[accent="true"]:hover {
    background-color: #2E7EB0;
}

QLineEdit, QTextEdit, QSpinBox, QComboBox {
    background-color: #12151C;
    color: #E6EDF7;
    border: 1px solid #30384A;
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: #2E6F9E;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #4EA8DE;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #1E232E;
    color: #E6EDF7;
    border: 1px solid #374155;
    selection-background-color: #2A567A;
}

QListWidget, QTreeWidget {
    background-color: #14171F;
    color: #E6EDF7;
    border: 1px solid #2A3140;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QListWidget::item, QTreeWidget::item {
    padding: 5px 6px;
    border-radius: 5px;
}
QListWidget::item:hover, QTreeWidget::item:hover {
    background-color: #222836;
}
QListWidget::item:selected, QTreeWidget::item:selected {
    background-color: #274B6B;
    color: #FFFFFF;
}

QSplitter::handle {
    background-color: #242A36;
    width: 4px;
    height: 4px;
}
QSplitter::handle:hover {
    background-color: #4EA8DE;
}

QLabel[sectionTitle="true"] {
    color: #9EC5E6;
    font-weight: bold;
    font-size: 13px;
    padding-top: 2px;
}

QStatusBar {
    background-color: #13161C;
    color: #9FB2C9;
    border-top: 1px solid #282F3D;
}

QSlider::groove:horizontal {
    height: 6px;
    background: #283040;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #4EA8DE;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #4EA8DE;
    border-radius: 3px;
}

QScrollBar:vertical, QScrollBar:horizontal {
    background: #14171F;
    width: 10px;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #333C4E;
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover {
    background: #4A5770;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}
"""

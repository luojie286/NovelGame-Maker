import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_maker.maker import MakerWindow
from galgame_maker.project import create_project, import_asset


class ThemePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_choice_theme_and_restore_default(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "短篇")
            source = Path(folder) / "choice.png"
            source.write_bytes(b"synthetic image")
            asset = import_asset(root, project, source, "image", "选项背景")
            window = MakerWindow()
            window.set_project(root, project)
            window.theme_panel.part.setCurrentText("选项按钮")
            image_index = window.theme_panel.image.findData(asset["id"])
            window.theme_panel.image.setCurrentIndex(image_index)
            window.theme_panel.font_size.setValue(36)
            window.theme_panel.text_color.setText("#abcDEF")
            window.theme_panel.apply()
            style = project["theme"]["choice"]
            self.assertEqual(style["image_asset_id"], asset["id"])
            self.assertEqual(style["font_size_px"], 36)
            self.assertEqual(style["text_color"], "#ABCDEF")
            window.theme_panel.reset()
            self.assertNotIn("choice", project["theme"])
            window.dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()

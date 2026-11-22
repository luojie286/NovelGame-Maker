import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_maker.maker import MakerWindow
from galgame_maker.project import create_project


class TextPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_excerpt_refresh_and_search_locate_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "短篇")
            window = MakerWindow()
            window.set_project(root, project)
            node = window.editor.add_node("开场")
            line = window.editor.add_item(node["id"], "narration", text="夜色已深")
            excerpt = window.editor.add_excerpt(node["id"], [line["id"]])
            window.refresh_story(node["id"])
            window.text_panel.refresh()
            self.assertEqual(window.text_panel.excerpt_text.toPlainText(), "夜色已深")
            line["text"] = "夜色将明"
            window.text_panel.refresh()
            self.assertEqual(window.text_panel.excerpt_text.toPlainText(), "夜色将明")
            window.text_panel.query.setText("将明")
            window.text_panel.search()
            self.assertGreaterEqual(window.text_panel.results.count(), 1)
            node["items"].clear()
            window.text_panel.refresh()
            self.assertEqual(window.text_panel.excerpt_text.toPlainText(), "原文已删除或移动")
            window.close()


if __name__ == "__main__":
    unittest.main()

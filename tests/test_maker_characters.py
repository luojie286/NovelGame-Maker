import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_maker.maker import MakerWindow
from galgame_maker.project import create_project


class CharacterPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_character_fields_and_relation_graph(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "短篇")
            window = MakerWindow()
            window.set_project(root, project)
            first = window.editor.add_character("甲", "主角")
            second = window.editor.add_character("乙")
            relation = window.editor.add_relationship(first["id"], second["id"], directed=False)
            relation["label"] = "同伴"
            window.character_panel.refresh()
            self.assertEqual(window.character_panel.characters.count(), 2)
            self.assertEqual(window.character_panel.relations.count(), 1)
            self.assertIn("↔", window.character_panel.relations.item(0).text())
            self.assertIn("同伴", window.character_panel.relations.item(0).text())
            self.assertGreater(len(window.character_panel.scene.items()), 0)
            window.close()

    def test_delete_character_blocks_when_referenced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "短篇")
            window = MakerWindow()
            window.set_project(root, project)
            hero = window.editor.add_character("主角")
            extra = window.editor.add_character("路人")
            node = window.editor.add_node("开场")
            window.editor.add_item(node["id"], "dialogue", character_id=hero["id"], text="你好")
            window.character_panel.refresh()
            window.character_panel.characters.setCurrentRow(0)
            with patch("galgame_maker.maker_characters.QMessageBox.warning") as warn:
                window.character_panel.delete_character()
            warn.assert_called_once()
            self.assertEqual(len(project["characters"]), 2)
            window.character_panel.characters.setCurrentRow(1)
            window.character_panel.delete_character()
            self.assertEqual([c["id"] for c in project["characters"]], [hero["id"]])
            window.dirty = False
            window.close()

    def test_save_character_shows_feedback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "短篇")
            window = MakerWindow()
            window.set_project(root, project)
            window.editor.add_character("林晴")
            window.character_panel.refresh()
            window.character_panel.name.setText("林晴改")
            window.character_panel.update_character()
            self.assertIn("已保存", window.character_panel.save_feedback.text())
            window.dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()

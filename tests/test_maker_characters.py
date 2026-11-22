import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

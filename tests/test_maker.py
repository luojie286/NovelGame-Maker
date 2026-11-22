import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_maker.maker import MakerWindow
from galgame_maker.project import create_project, load_project, new_id


class MakerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_project_story_preview_and_save_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "测试短篇")
            window = MakerWindow()
            window.set_project(root, project)
            node = window.editor.add_node("开场")
            window.editor.add_item(node["id"], "narration", text="第一句")
            node["ending"] = {"credits_video_asset_id": None}
            window.mark_dirty()
            window.refresh_story(node["id"])
            self.assertEqual(window.nodes_list.count(), 1)
            self.assertEqual(window.items_list.count(), 1)
            self.assertIn("*", window.windowTitle())
            self.assertTrue(window.save())
            self.assertEqual(load_project(root)["nodes"][0]["items"][0]["text"], "第一句")
            window.preview()
            self.assertIsNotNone(window.preview_window)
            self.assertEqual(window.preview_window.game["title"], "测试短篇")
            window.preview_window.close()
            window.close()

    def test_edge_condition_editing_uses_current_variable_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "测试短篇")
            window = MakerWindow()
            window.set_project(root, project)
            start = window.editor.add_node("开始")
            end = window.editor.add_node("结束")
            variable = {"id": new_id(), "name": "好感度", "type": "int", "initial_value": 0}
            project["variables"].append(variable)
            edge = window.editor.add_edge(start["id"], end["id"], {
                "kind": "variable", "variable_id": variable["id"], "op": "gt", "value": 50,
            })
            window.refresh_story(start["id"])
            window.edges_list.setCurrentRow(0)
            self.assertIn("好感度", window.edges_list.item(0).text())
            with patch(
                "galgame_maker.maker.QInputDialog.getText",
                return_value=("好感度 >= 70", True),
            ):
                window.edit_edge_condition()
            self.assertEqual(edge["condition"]["op"], "ge")
            self.assertEqual(edge["condition"]["value"], 70)
            window.dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()

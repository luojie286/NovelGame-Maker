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

    def test_copy_node_places_shape_nearby_and_edge_follows_drag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "节点图测试")
            window = MakerWindow()
            window.set_project(root, project)
            n1 = window.editor.add_node("开场")
            n2 = window.editor.add_node("后续")
            project.setdefault("editor", {}).setdefault("node_positions", {})[n1["id"]] = {"x": -300, "y": 150}
            project["editor"]["node_positions"][n2["id"]] = {"x": 0, "y": 150}
            window.editor.add_edge(n1["id"], n2["id"], None)
            window.refresh_story(n1["id"])

            # Moving node updates attached StoryEdgeItem immediately
            shape1 = window._node_shapes[n1["id"]]
            edge_item = shape1.edges[0]
            before_start = edge_item.path().pointAtPercent(0.0)
            shape1.setPos(-200, 220)
            after_start = edge_item.path().pointAtPercent(0.0)
            self.assertNotEqual(before_start, after_start)

            # Copy node places duplicate right beside original
            window.select_node(n2["id"])
            window.copy_node()
            self.assertEqual(len(project["nodes"]), 3)
            dup = project["nodes"][2]
            self.assertIn(dup["id"], window._node_shapes)
            self.assertEqual(project["editor"]["node_positions"][dup["id"]], {"x": 250, "y": 190})
            window.dirty = False
            window.close()

    def test_live_preview_tracks_selected_playback_step(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "实时预览测试")
            bg = Path(folder) / "bg.png"
            bg.write_bytes(b"fake")
            sp = Path(folder) / "char.png"
            sp.write_bytes(b"fake")
            from galgame_maker.project import import_asset
            bg_rec = import_asset(root, project, bg, "image", "黄昏教室")
            sp_rec = import_asset(root, project, sp, "image", "林晴立绘")
            window = MakerWindow()
            window.set_project(root, project)
            node = window.editor.add_node("序章")
            window.editor.add_item(node["id"], "set_background", asset_id=bg_rec["id"])
            window.editor.add_item(
                node["id"], "show_sprite", asset_id=sp_rec["id"],
                slot="center", placement={"x": 960, "y": 1040, "scale_pct": 100, "z": 2},
            )
            window.refresh_story(node["id"])
            # Playback list shows readable asset names instead of UUIDs
            self.assertIn("黄昏教室", window.items_list.item(0).text())
            self.assertIn("林晴立绘", window.items_list.item(1).text())

            # Step 0 has background only; step 1 has background + sprite
            window.items_list.setCurrentRow(0)
            self.assertEqual(window.live_preview.background_id, bg_rec["id"])
            self.assertEqual(window.live_preview.sprites, {})
            window.items_list.setCurrentRow(1)
            self.assertIn("center", window.live_preview.sprites)
            self.assertEqual(window.live_preview.sprites["center"]["asset_id"], sp_rec["id"])
            window.dirty = False
            window.close()

    def test_choices_and_local_variables_separated_from_playback_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 项目"
            project = create_project(root, "节点变量测试")
            window = MakerWindow()
            window.set_project(root, project)
            n1 = window.editor.add_node("分支节点")
            n2 = window.editor.add_node("其他节点")
            window.editor.add_item(n1["id"], "narration", text="请选择")
            n1["choices"].append({"id": new_id(), "text": "同意"})
            project["variables"].append({
                "id": new_id(), "name": "全局标记", "type": "bool",
                "initial_value": False, "scope": "global",
            })
            project["variables"].append({
                "id": new_id(), "name": "本节点计数", "type": "int",
                "initial_value": 1, "scope": "local", "node_id": n1["id"],
            })
            window.refresh_story(n1["id"])
            # Playback list ONLY contains narration item, NOT choices
            self.assertEqual(window.items_list.count(), 1)
            self.assertEqual(window.choices_list.count(), 1)
            self.assertEqual(window.local_vars_list.count(), 1)
            self.assertEqual(window.global_vars_list.count(), 1)
            # Independent preview window tracks choices at final step
            self.assertEqual(window.live_preview.choices, ["同意"])
            # Other node sees global variable but not n1's local variable
            n2_vars = window._available_variables_for_node(n2["id"])
            self.assertEqual([v["name"] for v in n2_vars], ["全局标记"])
            window.dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()

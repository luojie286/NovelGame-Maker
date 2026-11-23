import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from galgame_maker.player import PlayerWindow, ThemedFrame
from galgame_maker.saves import AUTO_SLOT, list_save_slots, load_game, save_game


def game():
    style = {
        "image_asset_id": None, "text_color": "#FFFFFF",
        "font_size_px": 28, "background_color": "#20242C",
    }
    return {
        "game_id": "00000000-0000-4000-8000-000000000001",
        "title": "测试作品",
        "start_node_id": "start",
        "nodes": [{
            "id": "start",
            "items": [{"id": "line", "type": "narration", "text": "测试对白"}],
            "ending": {"credits_video_asset_id": None},
        }],
        "edges": [], "variables": [], "assets": [], "characters": [],
        "theme": {
            "version": 1, "layout_id": "classic",
            "dialogue": {**style, "font_size_px": 32},
            "choice": style.copy(), "menu": style.copy(),
        },
    }


class PlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_title_story_and_advance(self):
        with tempfile.TemporaryDirectory() as folder:
            window = PlayerWindow(game(), Path(folder), autosave=False)
            window.start_new_game()
            self.assertEqual(window.engine.phase, "waiting_advance")
            self.assertEqual(window.pages.currentIndex(), 1)
            window.advance()
            self.assertEqual(window.text.text(), "测试对白")
            window.advance()
            self.assertEqual(window.engine.phase, "finished")
            window.return_to_title()
            self.assertEqual(window.pages.currentIndex(), 0)
            window.close()

    def test_playback_modes_stop_at_choice_and_autosave_follows_branch(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"LOCALAPPDATA": folder}):
            root = Path(folder)
            data = game()
            data["save_compatibility_id"] = "compat-1"
            node = data["nodes"][0]
            ending = node.pop("ending")
            node["choices"] = [{"id": "yes", "text": "继续"}]
            data["nodes"].append({
                "id": "next", "items": [{"id": "line2", "type": "narration", "text": "结局"}],
                "ending": ending,
            })
            data["edges"] = [{
                "id": "edge", "from_node_id": "start", "to_node_id": "next",
                "condition": {"kind": "choice", "op": "eq", "choice_id": "yes"},
            }]
            for mode in ("auto", "skip"):
                with self.subTest(mode=mode):
                    window = PlayerWindow(data, root)
                    try:
                        window.start_new_game()
                        save_game(window.engine, data, 1, root)
                        window.toggle_mode(mode)
                        self.assertTrue(window.mode_timer.isActive())
                        window.timed_advance()
                        self.assertEqual(window.engine.phase, "waiting_choice")
                        self.assertIsNone(window.mode)
                        self.assertFalse(window.mode_timer.isActive())
                        self.assertEqual(load_game(data, AUTO_SLOT, root).phase, "waiting_choice")
                        window.choose("yes")
                        saved = load_game(data, AUTO_SLOT, root)
                        self.assertEqual(saved.node_id, "next")
                        self.assertEqual(saved.choices, {"start": "yes"})
                        self.assertEqual(load_game(data, 1, root).node_id, "start")
                    finally:
                        window.close()

    def test_theme_image_keeps_aspect_with_background_bars(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "assets").mkdir()
            source = QImage(20, 20, QImage.Format.Format_RGB32)
            source.fill(QColor("#FF0000"))
            source.save(str(root / "assets" / "dialogue.png"))
            themed_game = game()
            themed_game["assets"] = [{
                "id": "dialogue-image", "kind": "image", "path": "assets/dialogue.png",
                "sha256": "0" * 64,
            }]
            themed_game["theme"]["dialogue"]["image_asset_id"] = "dialogue-image"
            themed_game["theme"]["dialogue"]["background_color"] = "#000000"
            frame = ThemedFrame(themed_game, root, "dialogue")
            frame.resize(200, 100)
            rendered = frame.grab().toImage()
            self.assertEqual(rendered.pixelColor(10, 50), QColor("#000000"))
            self.assertEqual(rendered.pixelColor(100, 50), QColor("#FF0000"))
            frame.close()

    def test_restore_reveals_full_page_immediately_and_lists_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = game()
            data["save_compatibility_id"] = "compat-1"
            data["nodes"][0]["name"] = "开场节点"
            window = PlayerWindow(data, root, autosave=False)
            window.start_new_game()
            save_game(window.engine, data, 1, local_appdata=root)
            slots = list_save_slots(data, local_appdata=root)
            self.assertTrue(slots[0]["exists"] and slots[0]["compatible"])
            self.assertEqual(slots[0]["node_name"], "开场节点")
            self.assertFalse(slots[1]["exists"])

            window.refresh(reveal_immediately=True)
            self.assertFalse(window.timer.isActive())
            self.assertEqual(window.text.text(), "测试对白")
            window.close()

    def test_preview_starts_at_selected_node_with_custom_variables(self):
        with tempfile.TemporaryDirectory() as folder:
            data = game()
            data["variables"] = [{"id": "v1", "name": "flag", "type": "bool", "initial_value": False}]
            data["nodes"].append({
                "id": "second",
                "name": "第二节点",
                "items": [{"id": "line2", "type": "narration", "text": "调试起点"}],
                "ending": {"credits_video_asset_id": None},
            })
            window = PlayerWindow(
                data, Path(folder),
                start_node_id="second",
                initial_variables={"v1": True},
                auto_start=True,
                autosave=False,
            )
            self.assertEqual(window.pages.currentIndex(), 1)
            self.assertEqual(window.engine.node_id, "second")
            self.assertTrue(window.engine.variables_state["v1"])
            window.close()


if __name__ == "__main__":
    unittest.main()

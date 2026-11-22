import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from galgame_maker.player import PlayerWindow, ThemedFrame


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
            window = PlayerWindow(game(), Path(folder))
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


if __name__ == "__main__":
    unittest.main()

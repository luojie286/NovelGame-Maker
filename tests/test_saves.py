import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from galgame_maker.engine import StoryEngine, StoryError
from galgame_maker.saves import SaveError, load_game, save_game, save_path


def game():
    return {
        "game_id": "00000000-0000-4000-8000-000000000001",
        "save_compatibility_id": "a" * 64,
        "start_node_id": "start",
        "nodes": [{
            "id": "start",
            "items": [{"id": "line", "type": "narration", "text": "等待推进"}],
            "ending": {"credits_video_asset_id": None},
        }],
        "edges": [],
        "variables": [{"id": "count", "type": "int", "initial_value": 3}],
    }


class SaveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.local_appdata = Path(temporary.name)
        self.game = game()
        self.engine = StoryEngine(self.game)

    def test_save_and_reload_after_reopen(self):
        path = save_game(self.engine, self.game, 2, self.local_appdata)
        self.assertEqual(path, save_path(self.game, 2, self.local_appdata))
        content = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(content["phase"], "waiting_advance")
        self.assertEqual(content["variables"]["count"], 3)
        self.assertTrue(content["saved_at"].endswith("Z"))
        reloaded = load_game(self.game, 2, self.local_appdata)
        self.assertEqual(reloaded.snapshot()["item_id"], "line")
        self.assertEqual(reloaded.variables_state["count"], 3)
        reloaded.advance()
        self.assertEqual(reloaded.phase, "finished")

    def test_incompatible_game_keeps_save_file(self):
        path = save_game(self.engine, self.game, 1, self.local_appdata)
        previous = path.read_bytes()
        changed_game = {**self.game, "save_compatibility_id": "b" * 64}
        with self.assertRaisesRegex(SaveError, "不同的剧情版本"):
            load_game(changed_game, 1, self.local_appdata)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.engine.phase, "waiting_advance")

    def test_failed_replace_preserves_previous_slot(self):
        path = save_game(self.engine, self.game, 3, self.local_appdata)
        previous = path.read_bytes()
        with patch("galgame_maker.project.os.replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                save_game(self.engine, self.game, 3, self.local_appdata)
        self.assertEqual(path.read_bytes(), previous)

    def test_rejects_invalid_slot_and_forbidden_phase(self):
        with self.assertRaises(ValueError):
            save_game(self.engine, self.game, True, self.local_appdata)
        self.engine.advance()
        with self.assertRaises(StoryError):
            save_game(self.engine, self.game, 1, self.local_appdata)


if __name__ == "__main__":
    unittest.main()

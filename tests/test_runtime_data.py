import tempfile
import unittest
from pathlib import Path

from galgame_maker.engine import StoryEngine
from galgame_maker.project import create_project, import_asset, new_id
from galgame_maker.runtime_data import CompileError, compile_game


class RuntimeDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "中文 项目"
        self.project = create_project(self.root, "短篇")
        self.start_id = new_id()
        self.ending_id = new_id()
        self.a_id = new_id()
        self.b_id = new_id()
        self.variable_id = new_id()
        self.project["start_node_id"] = self.start_id
        self.project["variables"] = [{
            "id": self.variable_id, "name": "好感度", "type": "int", "initial_value": 0,
        }]
        self.project["nodes"] = [
            {"id": self.start_id, "name": "开场", "items": [
                {"id": new_id(), "type": "narration", "text": "选择路线"},
            ], "choices": [
                {"id": self.a_id, "text": "A"}, {"id": self.b_id, "text": "B"},
            ]},
            {"id": self.ending_id, "name": "结局", "items": [],
             "ending": {"credits_video_asset_id": None}},
        ]
        self.project["edges"] = [
            {"id": new_id(), "from_node_id": self.start_id, "to_node_id": self.ending_id,
             "condition": {"kind": "choice", "op": "eq", "choice_id": self.a_id},
             "effects": [{"variable_id": self.variable_id, "op": "add", "value": 1}]},
            {"id": new_id(), "from_node_id": self.start_id, "to_node_id": self.ending_id,
             "condition": {"kind": "choice", "op": "eq", "choice_id": self.b_id},
             "effects": []},
        ]

    def test_compile_and_play_same_snapshot(self):
        source = self.root.parent / "secret.png"
        source.write_bytes(b"private character reference")
        unused = import_asset(self.root, self.project, source, "image", "设定识别图")
        game = compile_game(self.project, self.root)
        self.assertEqual(game["assets"], [])
        self.assertNotIn(unused["id"], str(game))
        self.assertNotIn("editor", game)
        self.assertNotIn("relationships", game)
        engine = StoryEngine(game)
        engine.advance()
        engine.select(self.a_id)
        self.assertEqual(engine.variables_state[self.variable_id], 1)
        self.assertEqual(engine.phase, "finished")

    def test_conflicting_edges_block_compilation(self):
        self.project["edges"][1]["condition"] = {
            "kind": "variable", "variable_id": self.variable_id, "op": "ge", "value": 0,
        }
        with self.assertRaisesRegex(CompileError, "conflict"):
            compile_game(self.project, self.root)

    def test_reclassifying_music_keeps_existing_playback_and_save_compatibility(self):
        source = self.root.parent / "song.mp3"
        source.write_bytes(b"synthetic audio")
        asset = import_asset(self.root, self.project, source, "bgm", "片尾曲")
        self.project["nodes"][0]["items"].insert(0, {
            "id": new_id(), "type": "play_bgm", "asset_id": asset["id"],
        })
        original = compile_game(self.project, self.root)
        asset["kind"] = "song"
        updated = compile_game(self.project, self.root)
        self.assertEqual(updated["assets"][0]["kind"], "song")
        self.assertEqual(original["save_compatibility_id"], updated["save_compatibility_id"])
        self.assertEqual(StoryEngine(original).take_commands(), StoryEngine(updated).take_commands())

    def test_theme_change_preserves_save_compatibility(self):
        original = compile_game(self.project, self.root)
        self.project["theme"] = {"choice": {"font_size_px": 40}}
        changed = compile_game(self.project, self.root)
        self.assertEqual(original["save_compatibility_id"], changed["save_compatibility_id"])
        self.assertNotEqual(original["build_id"], changed["build_id"])
        source = self.root.parent / "theme.png"
        source.write_bytes(b"image bytes")
        image = import_asset(self.root, self.project, source, "image", "选项背景")
        self.project["theme"]["choice"]["image_asset_id"] = image["id"]
        with_image = compile_game(self.project, self.root)
        self.assertEqual(original["save_compatibility_id"], with_image["save_compatibility_id"])
        self.assertEqual(len(with_image["assets"]), 1)

    def test_display_names_preserve_save_compatibility(self):
        original = compile_game(self.project, self.root)
        self.project["title"] = "新作品名"
        self.project["nodes"][0]["name"] = "改名后的开场"
        self.project["nodes"][0]["choices"][0]["text"] = "新选项标签"
        changed = compile_game(self.project, self.root)
        self.assertEqual(original["save_compatibility_id"], changed["save_compatibility_id"])
        self.assertNotEqual(original["build_id"], changed["build_id"])

    def test_invalid_theme_is_rejected(self):
        self.project["theme"] = {"dialogue": {"font_size_px": True}}
        with self.assertRaisesRegex(CompileError, "font size"):
            compile_game(self.project, self.root)

    def test_bad_effect_and_cycle_block_compilation(self):
        self.project["edges"][0]["effects"][0]["value"] = True
        with self.assertRaisesRegex(CompileError, "effect type mismatch"):
            compile_game(self.project, self.root)
        self.project["edges"][0]["effects"][0]["value"] = 1
        self.project["edges"].append({
            "id": new_id(), "from_node_id": self.ending_id, "to_node_id": self.start_id,
            "condition": None, "effects": [],
        })
        with self.assertRaisesRegex(CompileError, "cycle"):
            compile_game(self.project, self.root)


if __name__ == "__main__":
    unittest.main()

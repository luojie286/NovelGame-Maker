import json
import shutil
import tempfile
import unittest
from pathlib import Path

from galgame_maker.editing import ProjectEditor
from galgame_maker.engine import StoryEngine
from galgame_maker.export import export_game
from galgame_maker.project import load_project
from galgame_maker.runtime_data import compile_game


EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "short_story"


class ExampleStoryTests(unittest.TestCase):
    def test_two_routes_shared_node_and_ending(self):
        project = load_project(EXAMPLE)
        game = compile_game(project, EXAMPLE)
        self.assertEqual(len(project["characters"]), 2)
        self.assertEqual(len(project["relationships"]), 1)
        self.assertEqual(len(project["excerpts"]), 1)
        self.assertEqual(len(game["nodes"]), 7)
        choices = project["nodes"][1]["choices"]
        variable_id = project["variables"][0]["id"]
        for selected, score, credits in (
            (choices[0]["id"], 1, True),
            (choices[1]["id"], -1, False),
        ):
            with self.subTest(choice=selected):
                engine = StoryEngine(game)
                visited = []
                for _ in range(20):
                    visited.append(engine.node_id)
                    if engine.phase == "waiting_advance":
                        engine.advance()
                    elif engine.phase == "waiting_choice":
                        engine.select(selected)
                    elif engine.phase == "playing_credits":
                        self.assertTrue(credits)
                        self.assertEqual(engine.take_commands()[-1]["type"], "play_credits")
                        engine.media_finished()
                    elif engine.phase == "finished":
                        break
                    else:
                        self.fail(f"unexpected story phase: {engine.phase}")
                self.assertEqual(engine.phase, "finished")
                self.assertIn(project["nodes"][4]["id"], visited)
                self.assertEqual(engine.variables_state[variable_id], score)

    def test_moved_project_exports_only_referenced_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            moved = destination / "迁移 项目"
            shutil.copytree(EXAMPLE, moved)
            project = load_project(moved)
            game = compile_game(project, moved)
            excerpt = ProjectEditor(project).excerpt_text(project["excerpts"][0])
            self.assertIn("钟楼的灯", excerpt)
            player = destination / "player"
            player.mkdir()
            (player / "GalGamePlayer.exe").write_bytes(b"synthetic player")
            exported = export_game(project, moved, destination / "成品 游戏", player)
            self.assertEqual(game["build_id"], exported["build_id"])
            self.assertTrue((destination / "成品 游戏" / "GalGamePlayer.exe").exists())
            self.assertEqual(
                len(list((destination / "成品 游戏" / "assets").iterdir())),
                len(game["assets"]),
            )
            manifest = json.loads((moved / "asset-sources.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest), len(project["assets"]))


if __name__ == "__main__":
    unittest.main()

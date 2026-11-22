import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from galgame_maker.export import ExportError, export_game
from galgame_maker.project import create_project, import_asset, new_id


class ExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "中文 项目"
        self.project = create_project(self.root, "短篇")
        self.player = self.base / "player"
        self.player.mkdir()
        (self.player / "GalGame.exe").write_bytes(b"synthetic player")
        self.target = self.base / "导出 游戏"
        source = self.base / "bgm.mp3"
        source.write_bytes(b"first media bytes")
        self.asset = import_asset(self.root, self.project, source, "bgm", "音乐")
        node_id = new_id()
        self.project["start_node_id"] = node_id
        self.project["nodes"] = [{
            "id": node_id, "name": "结局",
            "items": [{"id": new_id(), "type": "play_bgm", "asset_id": self.asset["id"]}],
            "ending": {"credits_video_asset_id": None},
        }]

    def test_export_copies_only_runtime_assets(self):
        unused_source = self.base / "reference.png"
        unused_source.write_bytes(b"private art")
        unused = import_asset(self.root, self.project, unused_source, "image", "识别图")
        game = export_game(self.project, self.root, self.target, self.player)
        self.assertTrue((self.target / "GalGame.exe").is_file())
        self.assertTrue((self.target / "game.json").is_file())
        self.assertEqual(len(game["assets"]), 1)
        self.assertEqual((self.target / self.asset["path"]).read_bytes(), b"first media bytes")
        self.assertFalse((self.target / unused["path"]).exists())

    def test_tampered_media_keeps_previous_export(self):
        export_game(self.project, self.root, self.target, self.player)
        previous = (self.target / "game.json").read_bytes()
        asset_file = self.root / self.asset["path"]
        timestamp = asset_file.stat().st_mtime_ns
        asset_file.write_bytes(b"changed-media-byte")
        os.utime(asset_file, ns=(timestamp, timestamp))
        with self.assertRaisesRegex(ExportError, "content changed"):
            export_game(self.project, self.root, self.target, self.player)
        self.assertEqual((self.target / "game.json").read_bytes(), previous)
        self.assertEqual((self.target / self.asset["path"]).read_bytes(), b"first media bytes")

    def test_timestamp_only_change_can_export(self):
        asset_file = self.root / self.asset["path"]
        os.utime(asset_file, None)
        game = export_game(self.project, self.root, self.target, self.player)
        self.assertEqual(game["assets"][0]["sha256"], hashlib.sha256(asset_file.read_bytes()).hexdigest())

    def test_failed_publish_restores_previous_export(self):
        export_game(self.project, self.root, self.target, self.player)
        previous = (self.target / "game.json").read_bytes()
        original_replace = os.replace

        def fail_new_publish(source, destination):
            if Path(source).name.startswith(f".{self.target.name}.new.") and Path(destination) == self.target:
                raise OSError("simulated publish failure")
            return original_replace(source, destination)

        with patch("galgame_maker.export.os.replace", side_effect=fail_new_publish):
            with self.assertRaisesRegex(OSError, "simulated publish failure"):
                export_game(self.project, self.root, self.target, self.player)
        self.assertEqual((self.target / "game.json").read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()

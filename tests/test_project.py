import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from galgame_maker.project import (
    ProjectFormatError,
    create_project,
    import_asset,
    load_project,
    replace_asset,
    resolve_asset_path,
    save_project,
    validate_project,
    verify_assets,
)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "中文 项目"
        self.data = create_project(self.root, "测试作品")

    def test_import_and_move_project(self):
        source = Path(self.temporary.name) / "music.mp3"
        source.write_bytes(b"synthetic test audio")
        record = import_asset(self.root, self.data, source, "bgm", "主题曲")
        save_project(self.root, self.data)
        moved = Path(self.temporary.name) / "搬迁 项目"
        self.root.rename(moved)
        loaded = load_project(moved)
        self.assertEqual(loaded["assets"][0], record)
        self.assertEqual(record["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(resolve_asset_path(moved, record["path"]).read_bytes(), source.read_bytes())

    def test_rejects_unsafe_paths_and_bad_data(self):
        for relative in ("assets/../outside", "../assets/file", "C:/assets/file", "assets\\file"):
            with self.subTest(relative=relative):
                with self.assertRaises(ProjectFormatError):
                    resolve_asset_path(self.root, relative)
        self.data["nodes"] = [{"id": self.data["project_id"]}]
        with self.assertRaisesRegex(ProjectFormatError, "duplicate entity ID"):
            validate_project(self.data, self.root)

    def test_rejects_duplicate_json_key_and_future_version(self):
        project_file = self.root / "project.json"
        project_file.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaisesRegex(ProjectFormatError, "duplicate JSON key"):
            load_project(self.root)
        self.data["schema_version"] = 2
        project_file.write_text(json.dumps(self.data), encoding="utf-8")
        with self.assertRaisesRegex(ProjectFormatError, "schema_version"):
            load_project(self.root)

    def test_failed_save_preserves_previous_snapshot(self):
        project_file = self.root / "project.json"
        previous = project_file.read_bytes()
        self.data["title"] = "新标题"
        with patch("galgame_maker.project.os.replace", side_effect=OSError("replace denied")):
            with self.assertRaises(OSError):
                save_project(self.root, self.data)
        self.assertEqual(project_file.read_bytes(), previous)
        self.assertEqual(self.data["title"], "新标题")

    def test_failed_import_leaves_asset_list_unchanged(self):
        source = Path(self.temporary.name) / "missing.png"
        with self.assertRaises(FileNotFoundError):
            import_asset(self.root, self.data, source, "image", "立绘")
        self.assertEqual(self.data["assets"], [])

    @unittest.skipUnless(os.name == "nt", "Windows symlink behavior")
    def test_rejects_asset_symlink_escape(self):
        outside = Path(self.temporary.name) / "outside.png"
        outside.write_bytes(b"data")
        link = self.root / "assets" / "link.png"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaises(ProjectFormatError):
            resolve_asset_path(self.root, "assets/link.png")

    def test_replace_asset_preserves_id_and_verify_detects_tampering(self):
        first = Path(self.temporary.name) / "v1.png"
        first.write_bytes(b"image-v1")
        record = import_asset(self.root, self.data, first, "image", "背景")
        original_id = record["id"]
        original_path = record["path"]

        second = Path(self.temporary.name) / "v2.png"
        second.write_bytes(b"image-v2-updated")
        updated = replace_asset(self.root, self.data, original_id, second)
        self.assertEqual(updated["id"], original_id)
        self.assertNotEqual(updated["path"], original_path)
        self.assertEqual(updated["sha256"], hashlib.sha256(b"image-v2-updated").hexdigest())
        self.assertEqual(verify_assets(self.root, self.data), [])

        # Tamper with file bytes -> verify_assets detects mismatch
        resolve_asset_path(self.root, updated["path"]).write_bytes(b"corrupted")
        issues = verify_assets(self.root, self.data)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue"], "mismatch")

        # Delete file -> verify_assets detects missing
        resolve_asset_path(self.root, updated["path"]).unlink()
        issues = verify_assets(self.root, self.data)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue"], "missing")


if __name__ == "__main__":
    unittest.main()

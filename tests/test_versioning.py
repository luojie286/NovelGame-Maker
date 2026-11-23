import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from galgame_maker.editing import ProjectEditor
from galgame_maker.maker import MakerWindow
from galgame_maker.project import (
    create_project,
    import_asset,
    load_project,
    replace_asset,
    resolve_asset_path,
    save_project,
)
from galgame_maker.versioning import (
    VersionIntegrityError,
    create_version,
    delete_version,
    diff_projects,
    diff_versions,
    list_versions,
    prune_unused_assets,
    restore_version,
    verify_version,
)


class VersioningCoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "中文 项目"
        self.data = create_project(self.root, "版本测试作品")

    def test_save_boundary_records_snapshot_and_deduplicates_unchanged(self):
        save_project(self.root, self.data, record_version=True, version_label="首次保存")
        versions = list_versions(self.root)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].label, "首次保存")

        # Repeated save with unchanged content skips duplicate snapshot.
        save_project(self.root, self.data, record_version=True, version_label="重复保存")
        self.assertEqual(len(list_versions(self.root)), 1)

        # Modify project -> creates a second version.
        self.data["title"] = "版本测试作品 v2"
        save_project(self.root, self.data, record_version=True, version_label="第二次保存")
        versions = list_versions(self.root)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].label, "第二次保存")

    def test_content_hash_dedup_replace_restore_and_gc(self):
        img1 = Path(self.temporary.name) / "bg_v1.png"
        img1.write_bytes(b"PNG-DATA-VERSION-1")
        asset = import_asset(self.root, self.data, img1, "image", "教室背景")
        asset_id = asset["id"]
        hash_v1 = asset["sha256"]
        save_project(self.root, self.data, record_version=True, version_label="v1")
        v1 = list_versions(self.root)[0]

        # Another version with same asset bytes reuses the exact same object blob.
        self.data["title"] = "v1.1"
        save_project(self.root, self.data, record_version=True, version_label="v1.1")
        v1_1 = list_versions(self.root)[0]
        objects_dir = self.root / ".versions" / "objects"
        blobs_before = [p for p in objects_dir.rglob("*") if p.is_file()]
        self.assertEqual(len(blobs_before), 1)
        self.assertEqual(blobs_before[0].name, hash_v1)

        # Replace asset bytes -> preserves stable asset ID, stores second blob.
        img2 = Path(self.temporary.name) / "bg_v2.png"
        img2.write_bytes(b"PNG-DATA-VERSION-2-UPDATED")
        updated = replace_asset(self.root, self.data, asset_id, img2)
        hash_v2 = updated["sha256"]
        self.assertNotEqual(hash_v1, hash_v2)
        save_project(self.root, self.data, record_version=True, version_label="v2")
        v2 = list_versions(self.root)[0]

        blobs_after = {p.name for p in objects_dir.rglob("*") if p.is_file()}
        self.assertEqual(blobs_after, {hash_v1, hash_v2})

        # Clean orphan live file from assets/; v1's blob remains safely in .versions/objects/.
        report = prune_unused_assets(self.root, current_project=self.data, clean_orphan_assets=True)
        self.assertEqual(report.removed_objects, 0)
        self.assertEqual(report.removed_orphan_assets, 1)

        # Move the entire project folder to a new path with Chinese + spaces, then restore v1!
        moved = Path(self.temporary.name) / "迁移后的 项目"
        self.root.rename(moved)
        self.assertEqual(verify_version(moved, v1.version_id), [])
        restored = restore_version(moved, v1.version_id, backup_current=True)
        self.assertEqual(restored["title"], "版本测试作品")
        self.assertEqual(restored["assets"][0]["id"], asset_id)
        self.assertEqual(restored["assets"][0]["sha256"], hash_v1)
        self.assertEqual(
            resolve_asset_path(moved, restored["assets"][0]["path"]).read_bytes(),
            b"PNG-DATA-VERSION-1",
        )

        # Deleting v1 still retains hash_v1 because v1.1 references it.
        del1 = delete_version(moved, v1.version_id, prune=True)
        self.assertEqual(del1.removed_objects, 0)

        # Deleting v1.1 removes hash_v1 blob (v2 and auto-backup reference hash_v2).
        del2 = delete_version(moved, v1_1.version_id, prune=True)
        self.assertEqual(del2.removed_objects, 1)
        self.assertGreater(del2.freed_bytes, 0)
        remaining_blobs = {
            p.name for p in (moved / ".versions" / "objects").rglob("*") if p.is_file()
        }
        self.assertEqual(remaining_blobs, {hash_v2})
        self.assertIsNotNone(v2)

    def test_diff_by_stable_object_id(self):
        editor = ProjectEditor(self.data)
        node = editor.add_node("序章")
        line = editor.add_item(node["id"], "dialogue", text="你好，世界")
        char = editor.add_character("林晓", "主角")
        img = Path(self.temporary.name) / "a.png"
        img.write_bytes(b"img-a")
        asset = import_asset(self.root, self.data, img, "image", "立绘A")
        save_project(self.root, self.data, record_version=True, version_label="基线")
        v1 = list_versions(self.root)[0]

        # Rename node & edit item & replace asset & add new node & remove character
        node["name"] = "第一章"
        line["text"] = "你好，新世界"
        img_b = Path(self.temporary.name) / "b.png"
        img_b.write_bytes(b"img-b")
        replace_asset(self.root, self.data, asset["id"], img_b)
        node2 = editor.add_node("第二章")
        self.data["characters"].remove(char)
        save_project(self.root, self.data, record_version=True, version_label="修改版")
        v2 = list_versions(self.root)[0]

        diff = diff_versions(self.root, v1.version_id, v2.version_id)
        self.assertFalse(diff.is_empty)

        node_changes = {c.entity_id: c for c in diff.by_collection("nodes")}
        self.assertEqual(node_changes[node["id"]].change_type, "modified")
        self.assertTrue(any("改名" in d for d in node_changes[node["id"]].details))
        self.assertEqual(node_changes[node2["id"]].change_type, "added")

        asset_changes = {c.entity_id: c for c in diff.by_collection("assets")}
        self.assertEqual(asset_changes[asset["id"]].change_type, "modified")
        self.assertIn("文件内容已替换", asset_changes[asset["id"]].details)

        char_changes = {c.entity_id: c for c in diff.by_collection("characters")}
        self.assertEqual(char_changes[char["id"]].change_type, "removed")

        # Identical diff is empty.
        self.assertTrue(diff_projects(self.data, self.data).is_empty)

    def test_restore_blocks_on_corrupted_asset_or_invalid_format(self):
        img = Path(self.temporary.name) / "art.png"
        img.write_bytes(b"valid-bytes")
        asset = import_asset(self.root, self.data, img, "image", "封面")
        save_project(self.root, self.data, record_version=True, version_label="良好版本")
        good = list_versions(self.root)[0]

        # Advance current project so we can verify failed restore doesn't touch project.json.
        self.data["title"] = "当前最新标题"
        save_project(self.root, self.data)
        live_before = (self.root / "project.json").read_bytes()

        # Corrupt BOTH the blob and live asset file -> verify & restore must fail!
        blob = self.root / ".versions" / "objects" / asset["sha256"][:2] / asset["sha256"]
        blob.write_bytes(b"tampered-blob")
        resolve_asset_path(self.root, asset["path"]).write_bytes(b"tampered-live")

        issues = verify_version(self.root, good.version_id)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].scope, "asset")
        with self.assertRaises(VersionIntegrityError):
            restore_version(self.root, good.version_id)
        self.assertEqual((self.root / "project.json").read_bytes(), live_before)

        # Corrupt snapshot format (unsupported schema_version) -> must also block!
        snap_path = self.root / ".versions" / "snapshots" / f"{good.version_id}.json"
        raw = json.loads(snap_path.read_text(encoding="utf-8"))
        raw["schema_version"] = 99
        snap_path.write_text(json.dumps(raw), encoding="utf-8")
        issues = verify_version(self.root, good.version_id)
        self.assertEqual(issues[0].scope, "format")
        with self.assertRaises(VersionIntegrityError):
            restore_version(self.root, good.version_id)
        self.assertEqual(load_project(self.root)["title"], "当前最新标题")


class VersionPanelUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_version_panel_create_diff_verify_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "版本界面测试"
            project = create_project(root, "初始标题")
            window = MakerWindow()
            window.set_project(root, project)

            # Save records an automatic snapshot.
            self.assertTrue(window.save())
            self.assertEqual(window.version_panel.versions_list.count(), 1)

            # Edit title and create a named manual snapshot.
            window.project["title"] = "改后标题"
            window.mark_dirty()
            with patch(
                "galgame_maker.maker_versions.QInputDialog.getText",
                return_value=("第一章完成", True),
            ):
                window.version_panel.create_snapshot()
            self.assertEqual(window.version_panel.versions_list.count(), 2)

            # Compare older version (row 1) with current project.
            window.version_panel.versions_list.setCurrentRow(1)
            window.version_panel.compare_with_current()
            self.assertIn("初始标题 -> 改后标题", window.version_panel.detail_view.toPlainText())

            # Verify selected version passes.
            window.version_panel.verify_selected()
            self.assertIn("完整性校验通过", window.version_panel.detail_view.toPlainText())

            # Restore older version through UI.
            with (
                patch(
                    "galgame_maker.maker_versions.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch("galgame_maker.maker_versions.QMessageBox.information"),
            ):
                window.version_panel.restore_selected()
            self.assertEqual(window.project["title"], "初始标题")
            window.dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()

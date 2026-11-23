import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from galgame_maker.editing import ProjectEditor
from galgame_maker.maker import MakerWindow
from galgame_maker.project import create_project, import_asset, new_id
from galgame_maker.search_index import ProjectSearchIndex


class SearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "索引 测试"
        self.project = create_project(self.root, "星尘物语")
        self.db_path = self.root / ".cache" / "search_index.sqlite"
        self.index = ProjectSearchIndex(self.db_path)
        self.addCleanup(self.index.close)

    def test_incremental_sync_by_data_version_and_entity_diff(self):
        editor = ProjectEditor(self.project, search_index=self.index)
        node = editor.add_node("天台相遇")
        line1 = editor.add_item(node["id"], "dialogue", text="今晚的星光格外明亮。")
        line2 = editor.add_item(node["id"], "narration", text="微风吹过校园的走廊。")

        # First sync indexes node + 2 lines = 3 docs.
        s1 = self.index.sync(self.project, data_version=1)
        self.assertEqual((s1.added, s1.updated, s1.deleted), (3, 0, 0))
        self.assertFalse(s1.skipped)

        # Same data_version skips work completely.
        s2 = self.index.sync(self.project, data_version=1)
        self.assertTrue(s2.skipped)

        # Modify only 1 line and advance version -> only 1 row updated!
        line1["text"] = "今晚的星光与银河格外明亮。"
        s3 = self.index.sync(self.project, data_version=2)
        self.assertEqual((s3.added, s3.updated, s3.deleted), (0, 1, 0))

        # Delete 1 line -> only 1 row deleted!
        node["items"].remove(line2)
        s4 = self.index.sync(self.project, data_version=3)
        self.assertEqual((s4.added, s4.updated, s4.deleted), (0, 0, 1))
        self.assertEqual(self.index.document_count(), 2)

    def test_chinese_queries_short_long_multiterm_and_excerpt_invalidation(self):
        editor = ProjectEditor(self.project, search_index=self.index)
        node = editor.add_node("旧图书馆")
        line = editor.add_item(node["id"], "dialogue", text="我们在旧图书馆发现了失落的手稿。")
        excerpt = editor.add_excerpt(node["id"], [line["id"]])
        char = editor.add_character("苏晚晴", "天文社副社长，喜欢观测流星雨")
        self.project.setdefault("documents", []).append(
            {
                "id": new_id(),
                "kind": "setting",
                "title": "世界观设定",
                "text": "星历2099年，人类在轨道空间站建立了第一座学院。",
                "source_asset_id": None,
            }
        )
        img = Path(self.temporary.name) / "sky.png"
        img.write_bytes(b"sky")
        asset = import_asset(self.root, self.project, img, "image", "夜空背景")
        asset["tags"] = ["室外", "星空"]

        self.index.sync(self.project, data_version=1)

        # 3+ char Chinese query via FTS5 trigram
        hits = self.index.search("失落的手稿")
        kinds = {(h.kind, h.entity_id) for h in hits}
        self.assertIn(("text", line["id"]), kinds)
        self.assertIn(("excerpt", excerpt["id"]), kinds)

        # 2-char Chinese query via short-term fallback
        hits_short = self.index.search("流星")
        self.assertEqual([h.entity_id for h in hits_short], [char["id"]])

        # 1-char Chinese query
        hits_one = self.index.search("轨")
        self.assertEqual(len(hits_one), 1)
        self.assertEqual(hits_one[0].kind, "document")

        # Asset tag search + kind filter
        hits_asset = self.index.search("星空", kinds={"asset"})
        self.assertEqual([h.entity_id for h in hits_asset], [asset["id"]])

        # Multi-term mixed length + special FTS5 punctuation does not error
        hits_multi = self.index.search("轨道空间站 学院")
        self.assertEqual(len(hits_multi), 1)
        self.assertEqual(self.index.search('NOT (foo "bar" -baz*)'), [])

        # Invalidate excerpt by deleting source item -> excerpt paused from search!
        node["items"].remove(line)
        self.index.sync(self.project, data_version=2)
        self.assertEqual(self.index.search("失落的手稿"), [])

    def test_corrupted_or_missing_sqlite_rebuilds_transparently(self):
        editor = ProjectEditor(self.project, search_index=self.index)
        node = editor.add_node("序幕")
        line = editor.add_item(node["id"], "narration", text="钟声在午夜准时响起。")
        self.index.sync(self.project, data_version=1)
        self.index.close()

        # Corrupt the derived SQLite file on disk.
        self.db_path.write_bytes(b"NOT A VALID SQLITE DATABASE" * 16)

        recovered = ProjectSearchIndex(self.db_path)
        self.addCleanup(recovered.close)
        stats = recovered.sync(self.project, data_version=1)
        self.assertGreaterEqual(stats.added, 2)
        hits = recovered.search("午夜准时")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].entity_id, line["id"])

    def test_large_project_chinese_search_latency(self):
        # Build a realistic large project (~4,500+ indexed entries).
        nodes = []
        for n_idx in range(200):
            items = []
            for i_idx in range(20):
                text = f"第{n_idx}章第{i_idx}段：晨雾笼罩着海边小镇，海鸥掠过防波堤。"
                if n_idx == 137 and i_idx == 14:
                    text = "隐藏彩蛋：在古老灯塔的地下室里藏着星辰罗盘的秘密钥匙。"
                items.append({"id": new_id(), "type": "dialogue", "text": text})
            nodes.append(
                {
                    "id": new_id(),
                    "name": f"剧情节点_{n_idx}",
                    "items": items,
                    "choices": [],
                    "ending": None,
                }
            )
        documents = [
            {
                "id": new_id(),
                "kind": "setting",
                "title": f"世界观条目_{idx}",
                "text": f"关于海港城市历史沿革与气象记录的第{idx}份档案。",
                "source_asset_id": None,
            }
            for idx in range(100)
        ]
        self.project["nodes"] = nodes
        self.project["documents"] = documents

        self.index.sync(self.project, data_version=1)
        self.assertEqual(self.index.document_count(), 200 * 21 + 100)

        # Warm query + timed FTS5 trigram query should complete in milliseconds.
        started = time.perf_counter()
        hits = self.index.search("星辰罗盘的秘密")
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].kind, "text")
        self.assertLess(elapsed_ms, 50.0, f"FTS5 search took too long: {elapsed_ms:.2f} ms")


class TextPanelFTS5UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_text_panel_kind_filter_and_rebuild_button(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "检索界面"
            project = create_project(root, "检索测试")
            window = MakerWindow()
            window.set_project(root, project)

            node = window.editor.add_node("风铃神社")
            window.editor.add_item(node["id"], "dialogue", text="风铃在夏日的晚风中轻响。")
            window.project.setdefault("documents", []).append(
                {
                    "id": new_id(),
                    "kind": "setting",
                    "title": "风铃传说",
                    "text": "关于夏日风铃的古老传说。",
                    "source_asset_id": None,
                }
            )
            window.mark_dirty()
            window.text_panel.refresh()

            # Rebuild index via UI button handler
            window.text_panel.rebuild_index()
            self.assertIn("已重建", window.text_panel.search_status.text())

            # Search all kinds -> hits node, text, and document
            window.text_panel.query.setText("风铃")
            window.text_panel.search()
            self.assertGreaterEqual(window.text_panel.results.count(), 3)

            # Filter to only "剧本与设定"
            idx = window.text_panel.kind_filter.findText("剧本与设定")
            window.text_panel.kind_filter.setCurrentIndex(idx)
            self.assertEqual(window.text_panel.results.count(), 1)
            self.assertTrue(window.text_panel.results.item(0).text().startswith("设定 ·"))

            window.dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()

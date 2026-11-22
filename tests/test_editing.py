import unittest

from galgame_maker.editing import EditError, ProjectEditor
from galgame_maker.project import new_id


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            "start_node_id": None, "nodes": [], "edges": [],
            "assets": [], "characters": [],
        }
        self.editor = ProjectEditor(self.project)

    def test_copy_and_shared_node_keep_distinct_semantics(self):
        first = self.editor.add_node("开场")
        self.assertEqual(self.project["start_node_id"], first["id"])
        item = self.editor.add_item(first["id"], "narration", text="原句")
        first["choices"].append({"id": new_id(), "text": "进入"})
        copy = self.editor.copy_node(first["id"])
        self.assertNotEqual(copy["id"], first["id"])
        self.assertNotEqual(copy["items"][0]["id"], item["id"])
        self.assertNotEqual(copy["choices"][0]["id"], first["choices"][0]["id"])
        shared = self.editor.add_node("汇合")
        self.editor.add_edge(first["id"], shared["id"])
        self.editor.add_edge(copy["id"], shared["id"])
        self.assertEqual(len(self.project["edges"]), 2)
        with self.assertRaisesRegex(EditError, "cycle"):
            self.editor.add_edge(shared["id"], first["id"])

    def test_excerpt_tracks_text_and_invalidates_after_delete(self):
        node = self.editor.add_node("节点")
        first = self.editor.add_item(node["id"], "narration", text="第一句")
        second = self.editor.add_item(node["id"], "narration", text="第二句")
        excerpt = self.editor.add_excerpt(node["id"], [first["id"], second["id"]])
        second["text"] = "改过的句子"
        self.assertEqual(self.editor.excerpt_text(excerpt), "第一句\n改过的句子")
        self.editor.move_item(node["id"], second["id"], -1)
        self.assertEqual(self.editor.excerpt_text(excerpt), "改过的句子\n第一句")
        node["items"].remove(first)
        self.assertIsNone(self.editor.excerpt_text(excerpt))
        with self.assertRaises(EditError):
            self.editor.add_excerpt(node["id"], [first["id"]])

    def test_relationship_single_record_and_search_location(self):
        first = self.editor.add_character("甲")
        second = self.editor.add_character("乙")
        relation = self.editor.add_relationship(first["id"], second["id"], directed=False)
        relation["label"] = "同伴"
        self.assertEqual(len(self.project["relationships"]), 1)
        self.assertEqual(self.project["relationships"][0]["label"], "同伴")
        node = self.editor.add_node("开场")
        line = self.editor.add_item(node["id"], "narration", text="寻找线索")
        result = self.editor.search("线索")
        self.assertEqual(result[0].entity_id, line["id"])
        self.assertEqual(result[0].node_id, node["id"])


if __name__ == "__main__":
    unittest.main()

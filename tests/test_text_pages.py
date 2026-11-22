import unittest

from galgame_maker.engine import StoryEngine, StoryError
from galgame_maker.text_pages import grapheme_ends, page_end


class TextPageTests(unittest.TestCase):
    def test_cluster_boundaries_stay_together(self):
        text = "A\u0301" + "👩‍🚀" + "🇯🇵" + "好"
        ends = grapheme_ends(text)
        self.assertEqual([text[start:end] for start, end in zip([0, *ends[:-1]], ends)], [
            "A\u0301", "👩‍🚀", "🇯🇵", "好",
        ])
        self.assertEqual(page_end(text, 0, 2), len("A\u0301👩‍🚀"))

    def test_engine_saves_second_page_without_replaying_sfx(self):
        text = "界" * 80 + "👩‍🚀" + "終" * 5
        game = {
            "start_node_id": "start",
            "nodes": [{
                "id": "start",
                "items": [
                    {"id": "sound", "type": "play_sfx", "asset_id": "sfx"},
                    {"id": "line", "type": "narration", "text": text},
                ],
                "ending": {"credits_video_asset_id": None},
            }],
            "edges": [],
            "variables": [],
        }
        engine = StoryEngine(game)
        self.assertEqual(len(grapheme_ends(engine.current_page())), 80)
        engine.take_commands()
        engine.advance()
        self.assertEqual(engine.current_page(), "👩‍🚀" + "終" * 5)
        snapshot = engine.snapshot()
        self.assertEqual(snapshot["page_start_offset"], 80)
        restored = StoryEngine(game)
        restored.take_commands()
        restored.restore(snapshot)
        self.assertEqual(restored.current_page(), engine.current_page())
        self.assertEqual(restored.take_commands(), [])
        restored.advance()
        self.assertEqual(restored.phase, "finished")

    def test_restore_rejects_mid_cluster_offset(self):
        text = "A\u0301" + "more"
        game = {
            "start_node_id": "start",
            "nodes": [{"id": "start", "items": [
                {"id": "line", "type": "narration", "text": text},
            ]}],
            "edges": [], "variables": [],
        }
        engine = StoryEngine(game)
        snapshot = engine.snapshot()
        snapshot["page_start_offset"] = 1
        with self.assertRaisesRegex(StoryError, "offset"):
            engine.restore(snapshot)


if __name__ == "__main__":
    unittest.main()

import unittest

from galgame_maker.engine import StoryEngine, StoryError


def game():
    return {
        "start_node_id": "start",
        "nodes": [
            {"id": "start", "items": [
                {"id": "sound", "type": "play_sfx", "asset_id": "click"},
                {"id": "line", "type": "narration", "text": "选择路线"},
            ], "choices": [
                {"id": "a", "text": "A"}, {"id": "b", "text": "B"},
            ]},
            {"id": "route_a", "items": [], "choices": []},
            {"id": "route_b", "items": [], "choices": []},
            {"id": "shared", "items": [
                {"id": "shared_line", "type": "narration", "text": "汇合"},
            ], "choices": [], "ending": {"credits_video_asset_id": None}},
        ],
        "edges": [
            {"id": "edge_a", "from_node_id": "start", "to_node_id": "route_a",
             "condition": {"kind": "choice", "op": "eq", "choice_id": "a"},
             "effects": [{"variable_id": "score", "op": "add", "value": 1}]},
            {"id": "edge_b", "from_node_id": "start", "to_node_id": "route_b",
             "condition": {"kind": "choice", "op": "eq", "choice_id": "b"},
             "effects": [{"variable_id": "score", "op": "add", "value": 2}]},
            {"id": "join_a", "from_node_id": "route_a", "to_node_id": "shared",
             "condition": None, "effects": []},
            {"id": "join_b", "from_node_id": "route_b", "to_node_id": "shared",
             "condition": None, "effects": []},
        ],
        "variables": [{"id": "score", "type": "int", "initial_value": 0}],
    }


class EngineTests(unittest.TestCase):
    def test_voice_stops_on_page_change_and_history_survives_restore(self):
        story = game()
        item = story["nodes"][0]["items"][1]
        item.update(type="dialogue", text="测试语音", voice_asset_id="voice")
        engine = StoryEngine(story)
        self.assertEqual(engine.take_commands()[-1], {"type": "play_voice", "asset_id": "voice"})
        engine.advance(max_chars=2)
        self.assertEqual(engine.take_commands(), [{"type": "stop_voice"}])
        self.assertEqual(engine.current_page(), "语音")
        engine.advance()
        self.assertEqual(engine.take_commands(), [])
        engine.select("b")
        snapshot = engine.snapshot()
        restored = StoryEngine(story)
        restored.restore(snapshot)
        self.assertEqual(restored.take_commands(), [])
        self.assertEqual([restored.history_item(entry)["text"] for entry in restored.history], [
            "测试语音", "B", "汇合",
        ])
        snapshot.pop("history")
        restored.restore(snapshot)
        self.assertEqual(restored.history, [{"node_id": "shared", "item_id": "shared_line"}])

    def test_shared_node_and_effect_once_after_restore(self):
        for selected, expected in (("a", 1), ("b", 2)):
            with self.subTest(selected=selected):
                engine = StoryEngine(game())
                self.assertEqual(engine.phase, "waiting_advance")
                self.assertEqual(engine.take_commands(), [{"type": "play_sfx", "asset_id": "click", "volume_pct": 100}])
                engine.advance()
                self.assertEqual(engine.phase, "waiting_choice")
                engine.select(selected)
                self.assertEqual(engine.node_id, "shared")
                self.assertEqual(engine.variables_state["score"], expected)
                snapshot = engine.snapshot()
                restored = StoryEngine(game())
                restored.take_commands()
                restored.restore(snapshot)
                self.assertEqual(restored.take_commands(), [])
                self.assertEqual(restored.variables_state["score"], expected)
                restored.advance()
                self.assertEqual(restored.phase, "finished")
                self.assertEqual(restored.variables_state["score"], expected)

    def test_no_match_stalls_and_allows_reselection(self):
        story = game()
        story["edges"][0]["condition"] = {"op": "and", "args": [
            {"kind": "choice", "op": "eq", "choice_id": "a"},
            {"kind": "variable", "variable_id": "score", "op": "gt", "value": 0},
        ]}
        engine = StoryEngine(story)
        engine.advance()
        engine.select("a")
        self.assertEqual(engine.phase, "stalled")
        self.assertEqual(engine.error, "NO_MATCHING_BRANCH")
        self.assertEqual(engine.variables_state["score"], 0)
        snapshot = engine.snapshot()
        self.assertEqual(snapshot["choices"]["start"], "a")
        restored = StoryEngine(story)
        restored.restore(snapshot)
        restored.select("b")
        self.assertEqual(restored.choices["start"], "b")
        self.assertEqual(restored.variables_state["score"], 2)

    def test_invalid_second_effect_does_not_partially_commit(self):
        story = game()
        story["edges"][0]["effects"].append(
            {"variable_id": "score", "op": "set", "value": True}
        )
        engine = StoryEngine(story)
        engine.advance()
        with self.assertRaises(StoryError):
            engine.select("a")
        self.assertEqual(engine.node_id, "start")
        self.assertEqual(engine.variables_state["score"], 0)
        self.assertEqual(engine.phase, "waiting_choice")

    def test_save_is_forbidden_during_credits(self):
        story = game()
        story["nodes"][-1]["ending"]["credits_video_asset_id"] = "video"
        engine = StoryEngine(story)
        engine.advance()
        engine.select("b")
        engine.advance()
        self.assertEqual(engine.phase, "playing_credits")
        with self.assertRaises(StoryError):
            engine.snapshot()
        self.assertEqual(engine.take_commands()[-1], {"type": "play_credits", "asset_id": "video"})
        engine.media_finished()
        self.assertEqual(engine.phase, "finished")

    def test_rejects_cycle(self):
        story = game()
        story["edges"].append({
            "id": "cycle", "from_node_id": "shared", "to_node_id": "start",
            "condition": None, "effects": [],
        })
        with self.assertRaisesRegex(StoryError, "cycle"):
            StoryEngine(story)

    def test_long_automatic_chain_uses_no_recursive_calls(self):
        nodes = [{"id": str(index), "items": [], "choices": []} for index in range(1100)]
        nodes[-1]["ending"] = {"credits_video_asset_id": None}
        edges = [
            {"id": str(index), "from_node_id": str(index), "to_node_id": str(index + 1),
             "condition": None, "effects": []}
            for index in range(1099)
        ]
        engine = StoryEngine({
            "start_node_id": "0", "nodes": nodes, "edges": edges, "variables": [],
        })
        self.assertEqual(engine.node_id, "1099")
        self.assertEqual(engine.phase, "finished")


if __name__ == "__main__":
    unittest.main()

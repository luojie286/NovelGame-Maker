import unittest
from unittest.mock import patch

from galgame_maker import branch_check
from galgame_maker.branch_check import (
    BranchCheckCache,
    OverlapResult,
    node_check_key,
)
from galgame_maker.project import new_id


def _sample_project():
    var_id = new_id()
    n1, n2, n3 = new_id(), new_id(), new_id()
    c1, c2 = new_id(), new_id()
    e1, e2 = new_id(), new_id()
    project = {
        "schema_version": 1,
        "project_id": new_id(),
        "title": "缓存测试",
        "start_node_id": n1,
        "variables": [{"id": var_id, "name": "score", "type": "int", "initial_value": 0}],
        "assets": [],
        "characters": [],
        "nodes": [
            {
                "id": n1,
                "name": "开场",
                "items": [{"id": new_id(), "type": "narration", "text": "hello"}],
                "choices": [{"id": c1, "text": "左"}, {"id": c2, "text": "右"}],
                "ending": None,
            },
            {"id": n2, "name": "左路", "items": [], "choices": [], "ending": {"credits_video_asset_id": None}},
            {"id": n3, "name": "右路", "items": [], "choices": [], "ending": {"credits_video_asset_id": None}},
        ],
        "edges": [
            {
                "id": e1,
                "from_node_id": n1,
                "to_node_id": n2,
                "condition": {"kind": "variable", "variable_id": var_id, "op": "gt", "value": 5},
                "effects": [],
            },
            {
                "id": e2,
                "from_node_id": n1,
                "to_node_id": n3,
                "condition": {"kind": "variable", "variable_id": var_id, "op": "le", "value": 5},
                "effects": [],
            },
        ],
    }
    return project, n1, n2, n3, var_id


class BranchCacheTests(unittest.TestCase):
    def test_unrelated_edits_preserve_node_key_and_cache(self):
        project, n1, _, _, _ = _sample_project()
        cache = BranchCheckCache()
        first = cache.check_node(project, n1)
        self.assertEqual(first.status, "pass")
        self.assertEqual(first.checked_pairs, 1)

        # Rename node, edit dialogue, move position, change initial value
        project["nodes"][0]["name"] = "改名后的开场"
        project["nodes"][0]["items"][0]["text"] = "更新后的旁白"
        project["variables"][0]["initial_value"] = 99
        project["editor"] = {"node_positions": {n1: {"x": 120, "y": 240}}}

        with patch.object(branch_check, "check_overlap", side_effect=AssertionError("should reuse cache")):
            second = cache.check_node(project, n1)
        self.assertEqual(second.status, "pass")
        self.assertEqual(second.node_key, first.node_key)

    def test_condition_change_invalidates_and_detects_conflict(self):
        project, n1, _, _, _ = _sample_project()
        cache = BranchCheckCache()
        self.assertEqual(cache.check_node(project, n1).status, "pass")

        # Make second edge overlap at score == 6
        project["edges"][1]["condition"]["value"] = 10
        state = cache.check_node(project, n1)
        self.assertEqual(state.status, "conflict")
        self.assertIsNotNone(state.conflict_edges)
        self.assertIsNotNone(state.result)
        self.assertEqual(state.result.status, "conflict")

    def test_incomplete_result_is_not_cached_as_pass_and_can_retry(self):
        project, n1, _, _, _ = _sample_project()
        cache = BranchCheckCache()
        with patch.object(
            branch_check, "check_overlap",
            return_value=OverlapResult("incomplete", reason="timeout"),
        ):
            state = cache.check_node(project, n1)
        self.assertEqual(state.status, "incomplete")
        self.assertEqual(state.reason, "timeout")

        # Retrying without mock should solve and pass
        retried = cache.check_node(project, n1)
        self.assertEqual(retried.status, "pass")

    def test_batch_budget_pauses_and_resumes_remaining_pairs(self):
        project, n1, n2, n3, var_id = _sample_project()
        n4 = new_id()
        project["nodes"].append({
            "id": n4, "name": "中路", "items": [], "choices": [],
            "ending": {"credits_video_asset_id": None},
        })
        project["edges"][0]["condition"] = {"kind": "variable", "variable_id": var_id, "op": "lt", "value": 0}
        project["edges"][1]["condition"] = {"kind": "variable", "variable_id": var_id, "op": "eq", "value": 0}
        project["edges"].append({
            "id": new_id(), "from_node_id": n1, "to_node_id": n4,
            "condition": {"kind": "variable", "variable_id": var_id, "op": "gt", "value": 0},
            "effects": [],
        })
        cache = BranchCheckCache()
        # Expire deadline immediately -> returns pending with 0 checked pairs
        state = cache.check_node(project, n1, deadline_monotonic=0.0)
        self.assertEqual(state.status, "pending")
        self.assertEqual(state.total_pairs, 3)

        # Now run without deadline -> completes all 3 pairs
        final = cache.check_node(project, n1)
        self.assertEqual(final.status, "pass")
        self.assertEqual(final.checked_pairs, 3)

    def test_variable_retype_changes_node_key(self):
        project, n1, _, _, _ = _sample_project()
        variables = {v["id"]: v for v in project["variables"]}
        key_int = node_check_key(project["nodes"][0], project["edges"], variables)
        project["variables"][0]["type"] = "bool"
        variables = {v["id"]: v for v in project["variables"]}
        key_bool = node_check_key(project["nodes"][0], project["edges"], variables)
        self.assertNotEqual(key_int, key_bool)


if __name__ == "__main__":
    unittest.main()

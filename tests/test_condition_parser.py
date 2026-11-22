import unittest

from galgame_maker.condition_parser import (
    ConditionSyntaxError, format_condition, parse_condition,
)
from galgame_maker.conditions import ConditionError, evaluate_condition


VARIABLES = [
    {"id": "affection-id", "name": "好感度", "type": "int"},
    {"id": "met-id", "name": "已见面", "type": "bool"},
]
CHOICES = [
    {"id": "a-id", "text": "A"},
    {"id": "b-id", "text": "B"},
]


class ParserTests(unittest.TestCase):
    def test_nested_expression_keeps_precedence_and_ids(self):
        tree = parse_condition(
            '好感度 > 50 and (choice == "A" or choice == "B")',
            VARIABLES, CHOICES,
        )
        self.assertEqual(tree["op"], "and")
        self.assertEqual(tree["args"][0]["variable_id"], "affection-id")
        self.assertEqual(tree["args"][1]["op"], "or")
        self.assertEqual(tree["args"][1]["args"][0]["choice_id"], "a-id")
        self.assertTrue(evaluate_condition(tree, {"affection-id": 51}, "b-id"))
        self.assertFalse(evaluate_condition(tree, {"affection-id": 50}, "a-id"))

    def test_bool_literal_negative_integer_and_bare_bool(self):
        tree = parse_condition("已见面 == true or 好感度 < -1", VARIABLES, CHOICES)
        self.assertTrue(evaluate_condition(tree, {"met-id": True, "affection-id": 5}, None))
        self.assertTrue(evaluate_condition(tree, {"met-id": False, "affection-id": -2}, None))
        self.assertEqual(
            parse_condition("已见面", VARIABLES, CHOICES),
            {"kind": "boolean", "variable_id": "met-id"},
        )

    def test_rejects_code_calls_and_chained_comparisons(self):
        for expression in ("__import__('os')", "好感度 > 1 > 0", "好感度 + 1 > 0"):
            with self.subTest(expression=expression):
                with self.assertRaises(ConditionSyntaxError):
                    parse_condition(expression, VARIABLES, CHOICES)

    def test_reports_character_position_after_chinese_name(self):
        with self.assertRaises(ConditionSyntaxError) as captured:
            parse_condition("好感度 > 1 and unknown", VARIABLES, CHOICES)
        self.assertEqual(captured.exception.position, 12)

    def test_rejects_wrong_type_and_ambiguous_choice(self):
        with self.assertRaises(ConditionError):
            parse_condition("好感度 == true", VARIABLES, CHOICES)
        with self.assertRaises(ConditionError):
            parse_condition('choice == "A"', VARIABLES, [CHOICES[0], {"id": "other", "text": "A"}])

    def test_format_uses_current_names_and_roundtrips(self):
        tree = parse_condition(
            '好感度 > 50 and (choice == "A" or choice == "B")',
            VARIABLES, CHOICES,
        )
        renamed = [{**VARIABLES[0], "name": "信任值"}, VARIABLES[1]]
        expression = format_condition(tree, renamed, CHOICES)
        self.assertIn("信任值", expression)
        self.assertEqual(parse_condition(expression, renamed, CHOICES), tree)

    def test_is_is_not_and_not_operators_parse_evaluate_and_check_overlap(self):
        from galgame_maker.branch_check import check_overlap

        c_is = parse_condition('choice is "A"', VARIABLES, CHOICES)
        c_is_not = parse_condition('choice is not "A"', VARIABLES, CHOICES)
        self.assertTrue(evaluate_condition(c_is, {"affection-id": 0, "met-id": False}, "a-id"))
        self.assertFalse(evaluate_condition(c_is_not, {"affection-id": 0, "met-id": False}, "a-id"))
        self.assertTrue(evaluate_condition(c_is_not, {"affection-id": 0, "met-id": False}, "b-id"))

        defs = {v["id"]: v for v in VARIABLES}
        self.assertEqual(check_overlap(c_is, c_is_not, defs, {"a-id", "b-id"}).status, "pass")

        neg = parse_condition("not (已见面 is true and 好感度 >= 10)", VARIABLES, CHOICES)
        pos = parse_condition("已见面 is true and 好感度 >= 10", VARIABLES, CHOICES)
        self.assertEqual(check_overlap(neg, pos, defs, {"a-id", "b-id"}).status, "pass")
        self.assertTrue(evaluate_condition(neg, {"affection-id": 5, "met-id": True}, "a-id"))
        self.assertFalse(evaluate_condition(neg, {"affection-id": 12, "met-id": True}, "a-id"))
        formatted = format_condition(neg, VARIABLES, CHOICES)
        self.assertEqual(parse_condition(formatted, VARIABLES, CHOICES), neg)


if __name__ == "__main__":
    unittest.main()

import unittest

from galgame_maker.branch_check import check_overlap
from galgame_maker.conditions import ConditionError, evaluate_condition, validate_condition


AFFECTION = "affection"
MET = "met"
DEFINITIONS = {
    AFFECTION: {"type": "int"},
    MET: {"type": "bool"},
}
CHOICES = {"choice_a", "choice_b"}


def compare(variable_id, operation, value):
    return {
        "kind": "variable", "variable_id": variable_id,
        "op": operation, "value": value,
    }


def choice(choice_id):
    return {"kind": "choice", "op": "eq", "choice_id": choice_id}


class ConditionTests(unittest.TestCase):
    def test_overlap_reports_reproducible_witness(self):
        first = compare(AFFECTION, "gt", 50)
        second = compare(AFFECTION, "gt", 70)
        result = check_overlap(first, second, DEFINITIONS, CHOICES)
        self.assertEqual(result.status, "conflict")
        self.assertTrue(evaluate_condition(first, result.variables, result.choice_id))
        self.assertTrue(evaluate_condition(second, result.variables, result.choice_id))

    def test_boundary_is_exclusive(self):
        low = compare(AFFECTION, "lt", 50)
        high = compare(AFFECTION, "ge", 50)
        self.assertEqual(check_overlap(low, high, DEFINITIONS, CHOICES).status, "pass")
        self.assertFalse(evaluate_condition(low, {AFFECTION: 50}, None))
        self.assertTrue(evaluate_condition(high, {AFFECTION: 50}, None))

    def test_choice_is_exactly_one_legal_option(self):
        left = {"op": "and", "args": [choice("choice_a"), compare(AFFECTION, "gt", 50)]}
        right = {"op": "and", "args": [choice("choice_b"), compare(AFFECTION, "gt", 50)]}
        self.assertEqual(check_overlap(left, right, DEFINITIONS, CHOICES).status, "pass")

    def test_parentheses_change_precedence(self):
        met = compare(MET, "eq", True)
        affection_and_a = {"op": "and", "args": [compare(AFFECTION, "gt", 50), choice("choice_a")]}
        first = {"op": "or", "args": [met, affection_and_a]}
        second = {"op": "and", "args": [
            {"op": "or", "args": [met, compare(AFFECTION, "gt", 50)]},
            choice("choice_a"),
        ]}
        values = {MET: True, AFFECTION: 0}
        self.assertTrue(evaluate_condition(first, values, "choice_b"))
        self.assertFalse(evaluate_condition(second, values, "choice_b"))

    def test_rejects_bool_as_int_and_foreign_choice(self):
        with self.assertRaises(ConditionError):
            validate_condition(compare(AFFECTION, "eq", True), DEFINITIONS, CHOICES)
        with self.assertRaises(ConditionError):
            validate_condition(compare(MET, "gt", True), DEFINITIONS, CHOICES)
        with self.assertRaises(ConditionError):
            validate_condition(choice("foreign"), DEFINITIONS, CHOICES)


if __name__ == "__main__":
    unittest.main()

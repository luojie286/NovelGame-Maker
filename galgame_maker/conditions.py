"""Shared, typed condition-tree rules for the editor and story engine."""

from __future__ import annotations

import operator
from typing import Any


INT_OPERATORS = {"eq", "ne", "gt", "ge", "lt", "le"}
BOOL_OPERATORS = {"eq", "ne"}
COMPARISONS = {
    "eq": operator.eq, "ne": operator.ne, "gt": operator.gt,
    "ge": operator.ge, "lt": operator.lt, "le": operator.le,
}


class ConditionError(ValueError):
    """A condition references invalid data or uses incompatible types."""


def _variable(definitions: dict[str, dict[str, Any]], variable_id: Any) -> dict[str, Any]:
    if not isinstance(variable_id, str) or variable_id not in definitions:
        raise ConditionError(f"unknown variable ID: {variable_id}")
    definition = definitions[variable_id]
    if definition.get("type") not in {"int", "bool"}:
        raise ConditionError(f"unsupported variable type: {variable_id}")
    return definition


def validate_condition(
    tree: Any,
    definitions: dict[str, dict[str, Any]],
    choice_ids: set[str],
) -> None:
    """Validate each leaf before evaluation or translation to a solver."""
    if not isinstance(tree, dict):
        raise ConditionError("condition must be an object")
    if tree.get("op") == "not":
        if set(tree) != {"op", "arg"}:
            raise ConditionError("not condition has invalid fields")
        validate_condition(tree["arg"], definitions, choice_ids)
        return
    if tree.get("op") == "and" or tree.get("op") == "or":
        if set(tree) != {"op", "args"}:
            raise ConditionError("logical condition has invalid fields")
        arguments = tree["args"]
        if not isinstance(arguments, list) or len(arguments) < 2:
            raise ConditionError("and/or requires at least two conditions")
        for argument in arguments:
            validate_condition(argument, definitions, choice_ids)
        return

    kind = tree.get("kind")
    if kind == "boolean":
        if set(tree) != {"kind", "variable_id"}:
            raise ConditionError("boolean condition has invalid fields")
        if _variable(definitions, tree["variable_id"])["type"] != "bool":
            raise ConditionError("bare condition requires a bool variable")
        return
    if kind == "choice":
        if set(tree) != {"kind", "op", "choice_id"} or tree["op"] not in {"eq", "ne"}:
            raise ConditionError("choice comparison must use eq or ne")
        if not isinstance(tree["choice_id"], str) or tree["choice_id"] not in choice_ids:
            raise ConditionError(f"choice does not belong to source node: {tree['choice_id']}")
        return
    if kind == "variable":
        if set(tree) != {"kind", "variable_id", "op", "value"}:
            raise ConditionError("variable comparison has invalid fields")
        definition = _variable(definitions, tree["variable_id"])
        expected = int if definition["type"] == "int" else bool
        if type(tree["value"]) is not expected:
            raise ConditionError(f"comparison value has wrong type: {tree['variable_id']}")
        allowed = INT_OPERATORS if expected is int else BOOL_OPERATORS
        if tree["op"] not in allowed:
            raise ConditionError(f"comparison operator is invalid: {tree['op']}")
        return
    raise ConditionError(f"unsupported condition kind: {kind}")


def evaluate_condition(
    tree: dict[str, Any],
    values: dict[str, int | bool],
    choice_id: str | None,
) -> bool:
    """Evaluate a validated tree against the pre-transition state."""
    operator = tree.get("op")
    if operator == "not":
        return not evaluate_condition(tree["arg"], values, choice_id)
    if operator == "and":
        return all(evaluate_condition(child, values, choice_id) for child in tree["args"])
    if operator == "or":
        return any(evaluate_condition(child, values, choice_id) for child in tree["args"])
    kind = tree["kind"]
    if kind == "boolean":
        return values[tree["variable_id"]]
    if kind == "choice":
        return (choice_id == tree["choice_id"]) if operator == "eq" else (choice_id != tree["choice_id"])
    left = values[tree["variable_id"]]
    right = tree["value"]
    return COMPARISONS[operator](left, right)

"""Parse the editor's small expression language into stable-ID condition trees."""

from __future__ import annotations

import ast
from typing import Any

from .conditions import ConditionError, validate_condition


COMPARISON_NAMES = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Gt: "gt",
    ast.GtE: "ge",
    ast.Lt: "lt",
    ast.LtE: "le",
}


class ConditionSyntaxError(ConditionError):
    def __init__(self, message: str, position: int):
        super().__init__(f"{message} at character {position + 1}")
        self.position = position


def parse_condition(
    source: str,
    variables: list[dict[str, Any]],
    choices: list[dict[str, Any]],
) -> dict[str, Any]:
    """Accept comparisons, and/or, parentheses, and bare bool variables.

    Python's AST supplies precedence and source positions. We translate only
    explicitly supported nodes, never evaluate source code, and store IDs
    instead of mutable display names.
    """
    if not isinstance(source, str) or not source.strip():
        raise ConditionSyntaxError("condition is empty", 0)
    names: dict[str, dict[str, Any]] = {}
    for variable in variables:
        name = variable["name"]
        if name in names or name in {"choice", "true", "false"}:
            raise ConditionError(f"ambiguous or reserved variable name: {name}")
        names[name] = variable
    labels: dict[str, str] = {}
    for choice in choices:
        label = choice["text"]
        if label in labels:
            raise ConditionError(f"ambiguous choice label: {label}")
        labels[label] = choice["id"]

    try:
        root = ast.parse(source, mode="eval")
    except SyntaxError as error:
        line = (error.lineno or 1) - 1
        previous = source.splitlines(keepends=True)[:line]
        position = sum(len(part) for part in previous) + max(0, (error.offset or 1) - 1)
        raise ConditionSyntaxError("invalid expression", position) from error

    lines = source.splitlines(keepends=True)

    def position(node: ast.AST) -> int:
        """AST column offsets count UTF-8 bytes, while the editor counts characters."""
        line = node.lineno - 1
        prefix = lines[line].encode("utf-8")[:node.col_offset]
        return sum(len(part) for part in lines[:line]) + len(prefix.decode("utf-8"))

    def reject(node: ast.AST, message: str) -> None:
        raise ConditionSyntaxError(message, position(node))

    def value(node: ast.AST) -> int | bool | str:
        if isinstance(node, ast.Constant) and type(node.value) in {int, bool, str}:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant) and type(node.operand.value) is int:
                return -node.operand.value
        if isinstance(node, ast.Name) and node.id in {"true", "false"}:
            return node.id == "true"
        reject(node, "comparison value must be a literal")

    def convert(node: ast.AST) -> dict[str, Any]:
        if isinstance(node, ast.BoolOp):
            if not isinstance(node.op, (ast.And, ast.Or)):
                reject(node, "unsupported logical operator")
            return {
                "op": "and" if isinstance(node.op, ast.And) else "or",
                "args": [convert(part) for part in node.values],
            }
        if isinstance(node, ast.Name):
            variable = names.get(node.id)
            if variable is None or variable["type"] != "bool":
                reject(node, "bare condition requires a bool variable")
            return {"kind": "boolean", "variable_id": variable["id"]}
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                reject(node, "chained comparisons are unsupported")
            if not isinstance(node.left, ast.Name):
                reject(node, "left side must be a variable or choice")
            operation = COMPARISON_NAMES.get(type(node.ops[0]))
            if operation is None:
                reject(node, "unsupported comparison operator")
            if node.left.id == "choice":
                label = value(node.comparators[0])
                if operation != "eq" or not isinstance(label, str) or label not in labels:
                    reject(node, "choice must equal a known option label")
                return {"kind": "choice", "op": "eq", "choice_id": labels[label]}
            variable = names.get(node.left.id)
            if variable is None:
                reject(node.left, "unknown variable")
            return {
                "kind": "variable", "variable_id": variable["id"],
                "op": operation, "value": value(node.comparators[0]),
            }
        reject(node, "unsupported expression")

    tree = convert(root.body)
    definitions = {variable["id"]: variable for variable in variables}
    validate_condition(tree, definitions, {choice["id"] for choice in choices})
    return tree

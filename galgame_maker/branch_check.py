"""Editor-side Z3 proof that two outgoing connections cannot both match."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import z3

from .conditions import COMPARISONS, validate_condition


@dataclass(frozen=True)
class OverlapResult:
    status: str  # "pass", "conflict", or "incomplete"
    variables: dict[str, int | bool] | None = None
    choice_id: str | None = None
    reason: str | None = None


def check_overlap(
    first: dict[str, Any],
    second: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    choice_ids: set[str],
    timeout_ms: int = 2000,
) -> OverlapResult:
    """Check legal typed values and one current choice for simultaneous truth.

    The proof deliberately considers all legal variable values. Reaching a value
    through earlier story paths is a separate concern from branch exclusivity.
    """
    validate_condition(first, definitions, choice_ids)
    validate_condition(second, definitions, choice_ids)
    ordered_choices = sorted(choice_ids)
    symbols = {
        variable_id: (
            z3.Int(f"variable_{variable_id}")
            if definition["type"] == "int"
            else z3.Bool(f"variable_{variable_id}")
        )
        for variable_id, definition in definitions.items()
    }
    current_choice = z3.Int("current_choice")

    def expression(tree: dict[str, Any]) -> z3.BoolRef:
        operator = tree.get("op")
        if operator == "and":
            return z3.And(*(expression(child) for child in tree["args"]))
        if operator == "or":
            return z3.Or(*(expression(child) for child in tree["args"]))
        kind = tree["kind"]
        if kind == "boolean":
            return symbols[tree["variable_id"]]
        if kind == "choice":
            return current_choice == ordered_choices.index(tree["choice_id"])
        left = symbols[tree["variable_id"]]
        right = tree["value"]
        return COMPARISONS[operator](left, right)

    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    if ordered_choices:
        solver.add(current_choice >= 0, current_choice < len(ordered_choices))
    solver.add(expression(first), expression(second))
    outcome = solver.check()
    if outcome == z3.unsat:
        return OverlapResult("pass")
    if outcome == z3.unknown:
        return OverlapResult("incomplete", reason=solver.reason_unknown())

    model = solver.model()
    sample = {}
    for variable_id, definition in definitions.items():
        value = model.eval(symbols[variable_id], model_completion=True)
        sample[variable_id] = value.as_long() if definition["type"] == "int" else z3.is_true(value)
    choice_id = None
    if ordered_choices:
        choice_id = ordered_choices[model.eval(current_choice, model_completion=True).as_long()]
    return OverlapResult("conflict", sample, choice_id)

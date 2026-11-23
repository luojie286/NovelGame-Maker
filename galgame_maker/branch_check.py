"""Editor-side Z3 proof that two outgoing connections cannot both match."""

from __future__ import annotations

import hashlib
import itertools
import json
import time
from dataclasses import dataclass
from typing import Any

import z3

from .conditions import COMPARISONS, ConditionError, validate_condition

CHECKER_VERSION = 1


@dataclass(frozen=True)
class OverlapResult:
    status: str  # "pass", "conflict", or "incomplete"
    variables: dict[str, int | bool] | None = None
    choice_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class NodeCheckState:
    node_id: str
    node_key: str
    status: str  # "pending", "checking", "pass", "conflict", or "incomplete"
    conflict_edges: tuple[str, str] | None = None
    result: OverlapResult | None = None
    checked_pairs: int = 0
    total_pairs: int = 0
    reason: str | None = None


def referenced_variable_ids(tree: dict[str, Any] | None) -> set[str]:
    """Collect all variable IDs referenced by a condition tree."""
    if not isinstance(tree, dict):
        return set()
    if tree.get("op") == "not":
        return referenced_variable_ids(tree.get("arg"))
    if tree.get("op") in {"and", "or"}:
        found: set[str] = set()
        for child in tree.get("args", []):
            found.update(referenced_variable_ids(child))
        return found
    if tree.get("kind") in {"boolean", "variable"} and isinstance(tree.get("variable_id"), str):
        return {tree["variable_id"]}
    return set()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def pair_check_key(
    first: dict[str, Any],
    second: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
    choice_ids: set[str],
) -> str:
    """Build a deterministic cache key for one pair of branch conditions."""
    used_vars = referenced_variable_ids(first) | referenced_variable_ids(second)
    var_types = sorted(
        (var_id, definitions[var_id].get("type"))
        for var_id in used_vars
        if var_id in definitions
    )
    pair = sorted((_canonical_json(first), _canonical_json(second)))
    return _digest({
        "checker_version": CHECKER_VERSION,
        "choices": sorted(choice_ids),
        "pair": pair,
        "variables": var_types,
    })


def node_check_key(
    node: dict[str, Any],
    outgoing_edges: list[dict[str, Any]],
    definitions: dict[str, dict[str, Any]],
) -> str:
    """Build a deterministic cache key for all outgoing branches of one node.

    Display names, dialogue text, canvas positions, theme, assets, and variable
    initial values are intentionally excluded so unrelated edits keep cache hits.
    """
    choice_ids = sorted(choice["id"] for choice in node.get("choices", []))
    used_vars: set[str] = set()
    edge_specs = []
    for edge in sorted(outgoing_edges, key=lambda entry: entry["id"]):
        condition = edge.get("condition")
        used_vars.update(referenced_variable_ids(condition))
        edge_specs.append({
            "id": edge["id"],
            "to": edge["to_node_id"],
            "condition": condition,
        })
    var_types = sorted(
        (var_id, definitions[var_id].get("type"))
        for var_id in used_vars
        if var_id in definitions
    )
    return _digest({
        "checker_version": CHECKER_VERSION,
        "choices": choice_ids,
        "edges": edge_specs,
        "variables": var_types,
    })


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
        if operator == "not":
            return z3.Not(expression(tree["arg"]))
        if operator == "and":
            return z3.And(*(expression(child) for child in tree["args"]))
        if operator == "or":
            return z3.Or(*(expression(child) for child in tree["args"]))
        kind = tree["kind"]
        if kind == "boolean":
            return symbols[tree["variable_id"]]
        if kind == "choice":
            idx = ordered_choices.index(tree["choice_id"])
            return (current_choice == idx) if operator == "eq" else (current_choice != idx)
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


class BranchCheckCache:
    """Session-scoped incremental cache for per-node and per-pair Z3 proofs."""

    def __init__(self) -> None:
        self._pair_results: dict[str, OverlapResult] = {}
        self._node_states: dict[str, NodeCheckState] = {}

    def clear(self) -> None:
        self._pair_results.clear()
        self._node_states.clear()

    def _project_maps(
        self, project: dict[str, Any]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        nodes = {node["id"]: node for node in project.get("nodes", [])}
        edges_by_source: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
        for edge in project.get("edges", []):
            source = edge.get("from_node_id")
            if source in edges_by_source:
                edges_by_source[source].append(edge)
        variables = {variable["id"]: variable for variable in project.get("variables", [])}
        return nodes, edges_by_source, variables

    def get_node_state(self, project: dict[str, Any], node_id: str) -> NodeCheckState:
        nodes, edges_by_source, variables = self._project_maps(project)
        node = nodes[node_id]
        outgoing = edges_by_source[node_id]
        key = node_check_key(node, outgoing, variables)
        cached = self._node_states.get(node_id)
        if cached is not None and cached.node_key == key:
            return cached
        total_pairs = len(outgoing) * (len(outgoing) - 1) // 2
        return NodeCheckState(
            node_id=node_id,
            node_key=key,
            status="pending",
            total_pairs=total_pairs,
        )

    def check_node(
        self,
        project: dict[str, Any],
        node_id: str,
        pair_timeout_ms: int = 2000,
        deadline_monotonic: float | None = None,
    ) -> NodeCheckState:
        nodes, edges_by_source, variables = self._project_maps(project)
        node = nodes[node_id]
        outgoing = sorted(edges_by_source[node_id], key=lambda entry: entry["id"])
        key = node_check_key(node, outgoing, variables)
        cached = self._node_states.get(node_id)
        if cached is not None and cached.node_key == key and cached.status in {"pass", "conflict"}:
            return cached

        choices = node.get("choices", [])
        choice_ids = {choice["id"] for choice in choices}
        total_pairs = len(outgoing) * (len(outgoing) - 1) // 2

        if len(outgoing) <= 1:
            if len(outgoing) == 1:
                condition = outgoing[0].get("condition")
                if condition is None:
                    if choice_ids:
                        state = NodeCheckState(
                            node_id=node_id, node_key=key, status="incomplete",
                            reason=f"edge {outgoing[0]['id']} requires a condition",
                        )
                        self._node_states[node_id] = state
                        return state
                else:
                    try:
                        validate_condition(condition, variables, choice_ids)
                    except ConditionError as error:
                        state = NodeCheckState(
                            node_id=node_id, node_key=key, status="incomplete",
                            reason=str(error),
                        )
                        self._node_states[node_id] = state
                        return state
            state = NodeCheckState(
                node_id=node_id, node_key=key, status="pass",
                checked_pairs=0, total_pairs=0,
            )
            self._node_states[node_id] = state
            return state

        for edge in outgoing:
            condition = edge.get("condition")
            if condition is None:
                state = NodeCheckState(
                    node_id=node_id, node_key=key, status="incomplete",
                    total_pairs=total_pairs,
                    reason=f"edge {edge['id']} requires a condition",
                )
                self._node_states[node_id] = state
                return state
            try:
                validate_condition(condition, variables, choice_ids)
            except ConditionError as error:
                state = NodeCheckState(
                    node_id=node_id, node_key=key, status="incomplete",
                    total_pairs=total_pairs,
                    reason=str(error),
                )
                self._node_states[node_id] = state
                return state

        checked_pairs = 0
        for first, second in itertools.combinations(outgoing, 2):
            pkey = pair_check_key(first["condition"], second["condition"], variables, choice_ids)
            result = self._pair_results.get(pkey)
            if result is None:
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    state = NodeCheckState(
                        node_id=node_id, node_key=key, status="pending",
                        checked_pairs=checked_pairs, total_pairs=total_pairs,
                        reason="batch_budget_exhausted",
                    )
                    self._node_states[node_id] = state
                    return state
                result = check_overlap(
                    first["condition"], second["condition"], variables, choice_ids,
                    timeout_ms=pair_timeout_ms,
                )
                if result.status in {"pass", "conflict"}:
                    self._pair_results[pkey] = result
            if result.status == "conflict":
                state = NodeCheckState(
                    node_id=node_id, node_key=key, status="conflict",
                    conflict_edges=(first["id"], second["id"]),
                    result=result,
                    checked_pairs=checked_pairs + 1,
                    total_pairs=total_pairs,
                )
                self._node_states[node_id] = state
                return state
            if result.status == "incomplete":
                state = NodeCheckState(
                    node_id=node_id, node_key=key, status="incomplete",
                    conflict_edges=(first["id"], second["id"]),
                    result=result,
                    checked_pairs=checked_pairs,
                    total_pairs=total_pairs,
                    reason=result.reason,
                )
                self._node_states[node_id] = state
                return state
            checked_pairs += 1

        state = NodeCheckState(
            node_id=node_id, node_key=key, status="pass",
            checked_pairs=total_pairs, total_pairs=total_pairs,
        )
        self._node_states[node_id] = state
        return state

    def check_batch(
        self,
        project: dict[str, Any],
        node_ids: list[str] | None = None,
        pair_timeout_ms: int = 2000,
        batch_budget_ms: int = 10000,
    ) -> dict[str, NodeCheckState]:
        """Check target nodes (or all nodes) within a cumulative batch budget."""
        nodes, _, _ = self._project_maps(project)
        targets = [nid for nid in (node_ids if node_ids is not None else list(nodes)) if nid in nodes]
        # Remove stale node entries for deleted nodes
        for existing_id in list(self._node_states):
            if existing_id not in nodes:
                del self._node_states[existing_id]
        deadline = time.monotonic() + (batch_budget_ms / 1000.0) if batch_budget_ms > 0 else None
        results: dict[str, NodeCheckState] = {}
        for node_id in targets:
            results[node_id] = self.check_node(
                project, node_id, pair_timeout_ms=pair_timeout_ms, deadline_monotonic=deadline,
            )
        return results

    def snapshot_pairs(self) -> dict[str, OverlapResult]:
        return self._pair_results.copy()

    def load_pairs(self, pairs: dict[str, OverlapResult]) -> None:
        self._pair_results.update(pairs)

    def merge_node_states(
        self,
        project: dict[str, Any],
        states: dict[str, NodeCheckState],
        pairs: dict[str, OverlapResult],
    ) -> int:
        """Merge background results whose node keys still match the live project."""
        self._pair_results.update(pairs)
        nodes, edges_by_source, variables = self._project_maps(project)
        accepted = 0
        for node_id, state in states.items():
            if node_id not in nodes:
                continue
            current_key = node_check_key(nodes[node_id], edges_by_source[node_id], variables)
            if state.node_key == current_key:
                self._node_states[node_id] = state
                accepted += 1
        return accepted

    def check_pair(
        self,
        first: dict[str, Any],
        second: dict[str, Any],
        definitions: dict[str, dict[str, Any]],
        choice_ids: set[str],
        timeout_ms: int = 2000,
    ) -> OverlapResult:
        """Check one pair of conditions while reusing cached pass/conflict proofs."""
        pkey = pair_check_key(first, second, definitions, choice_ids)
        cached = self._pair_results.get(pkey)
        if cached is not None:
            return cached
        result = check_overlap(first, second, definitions, choice_ids, timeout_ms=timeout_ms)
        if result.status in {"pass", "conflict"}:
            self._pair_results[pkey] = result
        return result

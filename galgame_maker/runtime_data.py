"""Compile an editable project snapshot into a checked player snapshot."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections import defaultdict, deque
from copy import deepcopy
from pathlib import Path
from typing import Any

from .branch_check import check_overlap
from .conditions import ConditionError, validate_condition
from .project import validate_project


class CompileError(ValueError):
    """A project snapshot cannot be previewed or exported."""


DEFAULT_STYLE = {
    "image_asset_id": None,
    "text_color": "#FFFFFF",
    "font_size_px": 28,
    "background_color": "#20242C",
}


def _canonical_digest(value: dict[str, Any]) -> str:
    canonical = deepcopy(value)
    for collection in ("nodes", "edges", "variables", "assets", "characters"):
        if collection in canonical:
            canonical[collection].sort(key=lambda entry: entry["id"])
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compile_game(project: dict[str, Any], directory: Path | str) -> dict[str, Any]:
    """Use the same whole-graph validation for preview and published builds."""
    validate_project(project, directory)
    nodes = {node["id"]: node for node in project["nodes"]}
    variables = {variable["id"]: variable for variable in project["variables"]}
    assets = {asset["id"]: asset for asset in project["assets"]}
    characters = {character["id"]: character for character in project["characters"]}
    if project["start_node_id"] not in nodes:
        raise CompileError("start_node_id must reference a node")

    for variable in variables.values():
        kind = variable.get("type")
        value = variable.get("initial_value")
        if kind not in {"int", "bool"} or type(value) is not (int if kind == "int" else bool):
            raise CompileError(f"invalid initial value for variable {variable['id']}")
    used_assets: set[str] = set()
    story_assets: set[str] = set()
    used_characters: set[str] = set()

    def require_asset(asset_id: Any, kind: str, location: str, story: bool = True) -> None:
        if not isinstance(asset_id, str) or asset_id not in assets or assets[asset_id]["kind"] != kind:
            raise CompileError(f"{location} references missing {kind} asset: {asset_id}")
        used_assets.add(asset_id)
        if story:
            story_assets.add(asset_id)

    edges_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in nodes}
    for edge in project["edges"]:
        source = edge.get("from_node_id")
        target = edge.get("to_node_id")
        if source not in nodes or target not in nodes:
            raise CompileError(f"edge {edge['id']} references missing node")
        edges_by_source[source].append(edge)
        indegree[target] += 1
    ready = deque(node_id for node_id, count in indegree.items() if count == 0)
    visited = 0
    while ready:
        source = ready.popleft()
        visited += 1
        for edge in edges_by_source[source]:
            target = edge["to_node_id"]
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(nodes):
        raise CompileError("story graph contains a cycle")

    for node in nodes.values():
        node_id = node["id"]
        choices = node.get("choices", [])
        if not isinstance(choices, list):
            raise CompileError(f"node {node_id} choices must be an array")
        choice_ids = {choice["id"] for choice in choices}
        if len(choice_ids) != len(choices):
            raise CompileError(f"node {node_id} has duplicate choice IDs")
        for choice in choices:
            if not isinstance(choice.get("text"), str) or not choice["text"].strip():
                raise CompileError(f"node {node_id} has empty choice text")
        outgoing = edges_by_source[node_id]
        if node.get("ending") is not None:
            if outgoing or choices:
                raise CompileError(f"ending node {node_id} must be a leaf without choices")
            video = node["ending"].get("credits_video_asset_id")
            if video is not None:
                require_asset(video, "video", f"node {node_id} ending")
        for item in node["items"]:
            location = f"node {node_id} item {item['id']}"
            kind = item["type"]
            if kind == "dialogue":
                if item.get("character_id") not in characters:
                    raise CompileError(f"{location} references missing character")
                used_characters.add(item["character_id"])
            if kind in {"dialogue", "narration"}:
                if not isinstance(item.get("text"), str) or not item["text"]:
                    raise CompileError(f"{location} has empty text")
            elif kind in {"set_background", "show_sprite"}:
                asset_id = item.get("asset_id")
                if asset_id is not None:
                    require_asset(asset_id, "image", location)
                elif kind == "show_sprite":
                    raise CompileError(f"{location} requires an image")
            elif kind == "play_bgm":
                require_asset(item.get("asset_id"), "bgm", location)
            elif kind == "play_sfx":
                require_asset(item.get("asset_id"), "sfx", location)
            elif kind not in {"hide_sprite", "stop_bgm"}:
                raise CompileError(f"{location} has unsupported type {kind}")
        for edge in outgoing:
            condition = edge.get("condition")
            if condition is None:
                if choices or len(outgoing) != 1:
                    raise CompileError(f"edge {edge['id']} requires a condition")
            else:
                try:
                    validate_condition(condition, variables, choice_ids)
                except ConditionError as error:
                    raise CompileError(f"edge {edge['id']}: {error}") from error
            for effect in edge.get("effects", []):
                variable_id = effect.get("variable_id")
                if variable_id not in variables:
                    raise CompileError(f"edge {edge['id']} references missing variable")
                kind = variables[variable_id]["type"]
                value = effect.get("value")
                operation = effect.get("op")
                if type(value) is not (int if kind == "int" else bool):
                    raise CompileError(f"edge {edge['id']} effect type mismatch")
                if operation != "set" and not (kind == "int" and operation in {"add", "subtract"}):
                    raise CompileError(f"edge {edge['id']} invalid effect operation")
        for first, second in itertools.combinations(outgoing, 2):
            result = check_overlap(
                first["condition"], second["condition"], variables, choice_ids,
            )
            if result.status != "pass":
                raise CompileError(
                    f"node {node_id} edges {first['id']} and {second['id']}: "
                    f"branch check {result.status}; {result.variables or result.reason}"
                )

    theme_input = project.get("theme", {})
    if not isinstance(theme_input, dict):
        raise CompileError("theme must be an object")
    if set(theme_input) - {"version", "layout_id", "dialogue", "choice", "menu"}:
        raise CompileError("theme has unknown fields")
    if theme_input.get("version", 1) != 1 or theme_input.get("layout_id", "classic") != "classic":
        raise CompileError("unsupported theme version or layout")
    theme = {"version": 1, "layout_id": "classic"}
    for part in ("dialogue", "choice", "menu"):
        style = DEFAULT_STYLE.copy()
        if part == "dialogue":
            style["font_size_px"] = 32
        overrides = theme_input.get(part, {})
        if not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_STYLE):
            raise CompileError(f"theme {part} has invalid fields")
        style.update(overrides)
        if any(
            not isinstance(style[key], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", style[key])
            for key in ("text_color", "background_color")
        ):
            raise CompileError(f"theme {part} has invalid color")
        size = style["font_size_px"]
        if type(size) is not int or not 12 <= size <= 72:
            raise CompileError(f"theme {part} has invalid font size")
        image_id = style["image_asset_id"]
        if image_id is not None:
            require_asset(image_id, "image", f"theme {part}", story=False)
        theme[part] = style

    game = {
        "schema_version": 1,
        "game_id": project["project_id"],
        "title": project["title"],
        "start_node_id": project["start_node_id"],
        "nodes": deepcopy(project["nodes"]),
        "edges": deepcopy(project["edges"]),
        "variables": deepcopy(project["variables"]),
        "assets": [
            {key: asset[key] for key in ("id", "kind", "path", "sha256")}
            for asset in project["assets"] if asset["id"] in used_assets
        ],
        "characters": [
            {"id": character["id"], "name": character["name"]}
            for character in project["characters"] if character["id"] in used_characters
        ],
        "theme": theme,
    }
    compatibility = {
        key: game[key] for key in ("schema_version", "start_node_id", "edges", "variables")
    }
    compatibility["nodes"] = [
        {
            "id": node["id"],
            "items": node["items"],
            "choices": [{"id": choice["id"]} for choice in node.get("choices", [])],
            "ending": node.get("ending"),
        }
        for node in game["nodes"]
    ]
    compatibility["assets"] = [
        {"id": asset["id"], "kind": asset["kind"]}
        for asset in game["assets"] if asset["id"] in story_assets
    ]
    game["save_compatibility_id"] = _canonical_digest(compatibility)
    game["build_id"] = _canonical_digest(game)
    return game

"""Three atomic manual save slots for the exported Windows player."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import StoryEngine
from .project import _atomic_json, _unique_object


class SaveError(ValueError):
    """A save belongs to another game or has an unsupported format."""


def save_path(game: dict[str, Any], slot: int, local_appdata: Path | str | None = None) -> Path:
    if type(slot) is not int or slot not in {1, 2, 3}:
        raise ValueError("manual save slot must be 1, 2, or 3")
    root = Path(local_appdata) if local_appdata is not None else Path(os.environ["LOCALAPPDATA"])
    return root / "GalGameMaker" / "Saves" / game["game_id"] / f"{slot}.json"


def save_game(
    engine: StoryEngine,
    game: dict[str, Any],
    slot: int,
    local_appdata: Path | str | None = None,
) -> Path:
    path = save_path(game, slot, local_appdata)
    snapshot = engine.snapshot()
    save = {
        "schema_version": 1,
        "game_id": game["game_id"],
        "save_compatibility_id": game["save_compatibility_id"],
        "slot": slot,
        "saved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **snapshot,
    }
    _atomic_json(path, save)
    return path


def load_game(
    game: dict[str, Any],
    slot: int,
    local_appdata: Path | str | None = None,
) -> StoryEngine:
    """Construct a replacement engine only after the entire save is valid."""
    path = save_path(game, slot, local_appdata)
    with path.open(encoding="utf-8") as stream:
        save = json.load(stream, object_pairs_hook=_unique_object)
    if type(save.get("schema_version")) is not int or save["schema_version"] != 1:
        raise SaveError("unsupported save schema_version")
    if save.get("game_id") != game["game_id"]:
        raise SaveError("save belongs to another game")
    if save.get("save_compatibility_id") != game["save_compatibility_id"]:
        raise SaveError("此存档属于不同的剧情版本")
    if save.get("slot") != slot:
        raise SaveError("save slot does not match its file")
    replacement = StoryEngine(game)
    replacement.take_commands()
    replacement.restore(save)
    return replacement


def list_save_slots(
    game: dict[str, Any],
    local_appdata: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Inspect manual slots 1..3 for menu labels without mutating saves."""
    node_names = {node["id"]: node.get("name", "") for node in game.get("nodes", [])}
    slots: list[dict[str, Any]] = []
    for slot in (1, 2, 3):
        path = save_path(game, slot, local_appdata)
        if not path.is_file():
            slots.append({
                "slot": slot, "exists": False, "compatible": False,
                "saved_at": None, "node_name": None, "error": None,
            })
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                save = json.load(stream, object_pairs_hook=_unique_object)
        except (OSError, ValueError) as error:
            slots.append({
                "slot": slot, "exists": True, "compatible": False,
                "saved_at": None, "node_name": None, "error": str(error),
            })
            continue
        compatible = (
            type(save.get("schema_version")) is int
            and save.get("schema_version") == 1
            and save.get("game_id") == game["game_id"]
            and save.get("save_compatibility_id") == game["save_compatibility_id"]
            and save.get("slot") == slot
        )
        slots.append({
            "slot": slot,
            "exists": True,
            "compatible": compatible,
            "saved_at": save.get("saved_at"),
            "node_name": node_names.get(save.get("node_id")) if compatible else None,
            "error": None if compatible else "不兼容的剧情版本",
        })
    return slots

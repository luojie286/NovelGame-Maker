"""Publish a checked game snapshot beside a prepackaged Windows player."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .branch_check import BranchCheckCache
from .project import resolve_asset_path
from .runtime_data import compile_game


class ExportError(ValueError):
    """A source asset or publishing step prevented a valid export."""


def _remove_private_directory(path: Path, parent: Path) -> None:
    """Only remove a temporary directory created beside this exact target."""
    if path.resolve().parent != parent.resolve():
        raise ExportError(f"unsafe temporary export path: {path}")
    if path.exists():
        shutil.rmtree(path)


def export_game(
    project: dict[str, Any],
    project_directory: Path | str,
    target_directory: Path | str,
    player_bundle: Path | str,
    branch_cache: BranchCheckCache | None = None,
) -> dict[str, Any]:
    """Build fully in a sibling directory, then replace the published game.

    Source bytes are read once per asset. Their digest must match the editable
    snapshot before any new directory becomes visible at the target path.
    """
    source_root = Path(project_directory).resolve()
    target = Path(target_directory).resolve()
    player = Path(player_bundle).resolve()
    if not player.is_dir():
        raise FileNotFoundError(player)
    if target.exists() and not target.is_dir():
        raise ExportError(f"target is not a directory: {target}")
    if target == source_root or source_root.is_relative_to(target):
        raise ExportError("export target cannot contain the editable project")
    game = compile_game(project, source_root, branch_cache=branch_cache)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.new.", dir=parent))
    backup: Path | None = None
    try:
        shutil.copytree(player, staging, dirs_exist_ok=True)
        for asset in game["assets"]:
            source = resolve_asset_path(source_root, asset["path"])
            destination = staging / Path(*asset["path"].split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with source.open("rb") as input_file, destination.open("wb") as output:
                while chunk := input_file.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != asset["sha256"]:
                raise ExportError(f"asset content changed: {asset['id']}")
        with (staging / "game.json").open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(game, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        if target.exists():
            backup = parent / f".{target.name}.old.{uuid.uuid4()}"
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except OSError:
            if backup is not None:
                os.replace(backup, target)
                backup = None
            raise
        if backup is not None:
            _remove_private_directory(backup, parent)
        return game
    finally:
        _remove_private_directory(staging, parent)

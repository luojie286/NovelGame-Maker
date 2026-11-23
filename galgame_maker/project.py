"""Versioned, movable project folders and owned media files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


PROJECT_VERSION = 1
ASSET_KINDS = {"image", "text", "bgm", "sfx", "video"}
ROOT_REQUIRED = {
    "schema_version", "project_id", "title", "start_node_id",
    "nodes", "edges", "variables", "assets", "characters",
}
ROOT_OPTIONAL = {"theme", "relationships", "documents", "excerpts", "editor"}
ENTITY_COLLECTIONS = (
    "nodes", "edges", "variables", "assets", "characters",
    "relationships", "documents", "excerpts",
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class ProjectFormatError(ValueError):
    """A project file violates its versioned data contract."""


def new_id() -> str:
    return str(uuid.uuid4())


def _uuid(value: Any, location: str) -> None:
    if not isinstance(value, str):
        raise ProjectFormatError(f"{location} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ProjectFormatError(f"{location} must be a UUID string") from error
    if str(parsed) != value:
        raise ProjectFormatError(f"{location} must use canonical UUID spelling")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def resolve_asset_path(directory: Path | str, relative: str) -> Path:
    """Resolve an owned asset, rejecting lexical escapes and symlink escapes."""
    if not isinstance(relative, str) or not relative:
        raise ProjectFormatError("asset path must be a nonempty string")
    posix = PurePosixPath(relative)
    windows = PureWindowsPath(relative)
    if (
        "\\" in relative
        or windows.drive
        or windows.root
        or ":" in relative
        or posix.is_absolute()
        or len(posix.parts) < 2
        or posix.parts[0] != "assets"
        or any(part in {".", ".."} for part in relative.split("/"))
    ):
        raise ProjectFormatError(f"unsafe asset path: {relative}")
    root = Path(directory).resolve()
    assets_root = (root / "assets").resolve()
    if not assets_root.is_relative_to(root):
        raise ProjectFormatError("assets directory escapes the project")
    target = (root / Path(*posix.parts)).resolve()
    if not target.is_relative_to(assets_root) or target == assets_root:
        raise ProjectFormatError(f"asset path escapes the project: {relative}")
    return target


def validate_project(data: Any, directory: Path | str) -> None:
    if not isinstance(data, dict):
        raise ProjectFormatError("project root must be an object")
    missing = ROOT_REQUIRED - data.keys()
    unknown = data.keys() - ROOT_REQUIRED - ROOT_OPTIONAL
    if missing:
        raise ProjectFormatError(f"missing project field: {sorted(missing)[0]}")
    if unknown:
        raise ProjectFormatError(f"unknown project field: {sorted(unknown)[0]}")
    if type(data["schema_version"]) is not int or data["schema_version"] != PROJECT_VERSION:
        raise ProjectFormatError(f"unsupported project schema_version: {data['schema_version']}")
    _uuid(data["project_id"], "project_id")
    if not isinstance(data["title"], str) or not data["title"].strip():
        raise ProjectFormatError("title must be a nonempty string")
    if data["start_node_id"] is not None:
        _uuid(data["start_node_id"], "start_node_id")

    seen_ids = {data["project_id"]}
    for collection in ENTITY_COLLECTIONS:
        records = data.get(collection, [])
        if not isinstance(records, list):
            raise ProjectFormatError(f"{collection} must be an array")
        for index, record in enumerate(records):
            location = f"{collection}[{index}]"
            if not isinstance(record, dict) or "id" not in record:
                raise ProjectFormatError(f"{location}.id is required")
            _uuid(record["id"], f"{location}.id")
            if record["id"] in seen_ids:
                raise ProjectFormatError(f"duplicate entity ID at {location}")
            seen_ids.add(record["id"])

    for index, asset in enumerate(data["assets"]):
        location = f"assets[{index}]"
        required = {"id", "name", "kind", "path", "sha256"}
        if required - asset.keys() or asset.keys() - required - {"tags"}:
            raise ProjectFormatError(f"{location} has missing or unknown fields")
        if not isinstance(asset["name"], str) or not asset["name"].strip():
            raise ProjectFormatError(f"{location}.name must be nonempty")
        if not isinstance(asset["kind"], str) or asset["kind"] not in ASSET_KINDS:
            raise ProjectFormatError(f"{location}.kind is invalid")
        if not isinstance(asset["sha256"], str) or not SHA256_PATTERN.fullmatch(asset["sha256"]):
            raise ProjectFormatError(f"{location}.sha256 is invalid")
        if not isinstance(asset.get("tags", []), list) or any(
            not isinstance(tag, str) for tag in asset.get("tags", [])
        ):
            raise ProjectFormatError(f"{location}.tags must be strings")
        resolve_asset_path(directory, asset["path"])


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_project(directory: Path | str, title: str) -> dict[str, Any]:
    root = Path(directory)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"project directory is not empty: {root}")
    data = {
        "schema_version": PROJECT_VERSION,
        "project_id": new_id(),
        "title": title,
        "start_node_id": None,
        "nodes": [],
        "edges": [],
        "variables": [],
        "assets": [],
        "characters": [],
    }
    validate_project(data, root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(exist_ok=True)
    _atomic_json(root / "project.json", data)
    return data


def load_project(directory: Path | str) -> dict[str, Any]:
    root = Path(directory)
    with (root / "project.json").open(encoding="utf-8") as stream:
        data = json.load(stream, object_pairs_hook=_unique_object)
    validate_project(data, root)
    return data


def save_project(
    directory: Path | str,
    data: dict[str, Any],
    *,
    record_version: bool = False,
    version_label: str = "保存快照",
    skip_if_unchanged: bool = True,
) -> None:
    root = Path(directory)
    if not (root / "project.json").is_file():
        raise FileNotFoundError(root / "project.json")
    validate_project(data, root)
    _atomic_json(root / "project.json", data)
    if record_version:
        from .versioning import create_version

        create_version(
            root,
            data,
            label=version_label,
            auto=True,
            skip_if_unchanged=skip_if_unchanged,
        )


def import_asset(
    directory: Path | str,
    data: dict[str, Any],
    source: Path | str,
    kind: str,
    name: str,
) -> dict[str, Any]:
    """Copy and hash once, publish bytes, then expose the new record in memory.

    The caller saves the project snapshot after this returns. If that later save
    fails, the unreferenced new file can be retried or cleaned without damaging
    the prior project snapshot.
    """
    root = Path(directory)
    validate_project(data, root)
    if kind not in ASSET_KINDS:
        raise ValueError(f"unsupported asset kind: {kind}")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("asset name must be nonempty")
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    asset_id = new_id()
    suffix = source_path.suffix.lower()
    relative = f"assets/{asset_id}{suffix}"
    destination = resolve_asset_path(root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{asset_id}.",
            suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            with source_path.open("rb") as input_file:
                while chunk := input_file.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        record = {
            "id": asset_id, "name": name, "kind": kind,
            "path": relative, "sha256": digest.hexdigest(),
        }
        data["assets"].append(record)
        return record
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def replace_asset(
    directory: Path | str,
    data: dict[str, Any],
    asset_id: str,
    source: Path | str,
) -> dict[str, Any]:
    """Replace an asset's file bytes while preserving its stable ID and references."""
    root = Path(directory)
    validate_project(data, root)
    record = next((asset for asset in data["assets"] if asset["id"] == asset_id), None)
    if record is None:
        raise ValueError(f"unknown asset: {asset_id}")
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    file_id = new_id()
    suffix = source_path.suffix.lower()
    relative = f"assets/{file_id}{suffix}"
    destination = resolve_asset_path(root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{file_id}.",
            suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            with source_path.open("rb") as input_file:
                while chunk := input_file.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        record["path"] = relative
        record["sha256"] = digest.hexdigest()
        return record
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_assets(directory: Path | str, data: dict[str, Any]) -> list[dict[str, str]]:
    """Recompute SHA-256 for every asset in the project and return any issues."""
    root = Path(directory)
    issues: list[dict[str, str]] = []
    for asset in data.get("assets", []):
        try:
            path = resolve_asset_path(root, asset["path"])
        except ProjectFormatError as error:
            issues.append({
                "asset_id": asset["id"], "name": asset["name"],
                "path": asset.get("path", ""), "issue": str(error),
            })
            continue
        if not path.is_file():
            issues.append({
                "asset_id": asset["id"], "name": asset["name"],
                "path": asset["path"], "issue": "missing",
            })
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != asset["sha256"]:
            issues.append({
                "asset_id": asset["id"], "name": asset["name"],
                "path": asset["path"], "issue": "mismatch",
            })
    return issues

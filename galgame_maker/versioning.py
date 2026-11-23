"""Built-in project version management with content-addressed asset retention.

Version data is stored inside ``<project_root>/.versions/`` so the entire
project folder remains self-contained and movable, while game exports ignore it:

    .versions/
      index.json                  # Version catalog (schema_version + entries)
      snapshots/<version_id>.json # Full validated project.json snapshot
      objects/<sha[:2]>/<sha>     # Content-addressed deduplicated asset blobs

All writes use temp-file + fsync + os.replace atomicity. Before restoring any
historical version, snapshot format and every referenced asset's SHA-256 are
verified; restore then writes through the normal project save/load boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project import (
    ENTITY_COLLECTIONS,
    ProjectFormatError,
    _atomic_json,
    _unique_object,
    load_project,
    new_id,
    resolve_asset_path,
    save_project,
    validate_project,
)

VERSION_STORE_SCHEMA = 1
VERSIONS_DIRNAME = ".versions"

COLLECTION_LABELS = {
    "nodes": "剧情节点",
    "edges": "节点连接",
    "variables": "变量",
    "assets": "素材",
    "characters": "角色",
    "relationships": "人物关系",
    "documents": "剧本与设定",
    "excerpts": "文本摘录",
}


class VersionError(ValueError):
    """Base error for built-in project version management."""


class VersionIntegrityError(VersionError):
    """Raised when a version snapshot or its referenced assets fail verification."""

    def __init__(self, message: str, issues: list[VersionIntegrityIssue] | None = None):
        super().__init__(message)
        self.issues: list[VersionIntegrityIssue] = issues or []


@dataclass(frozen=True)
class VersionRecord:
    version_id: str
    created_at: str
    label: str
    auto: bool
    project_sha256: str
    asset_hashes: tuple[str, ...]
    stats: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.version_id,
            "created_at": self.created_at,
            "label": self.label,
            "auto": self.auto,
            "project_sha256": self.project_sha256,
            "asset_hashes": list(self.asset_hashes),
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VersionRecord:
        return cls(
            version_id=str(raw["id"]),
            created_at=str(raw["created_at"]),
            label=str(raw.get("label", "")),
            auto=bool(raw.get("auto", False)),
            project_sha256=str(raw.get("project_sha256", "")),
            asset_hashes=tuple(str(h) for h in raw.get("asset_hashes", [])),
            stats={str(k): int(v) for k, v in raw.get("stats", {}).items()},
        )


@dataclass(frozen=True)
class VersionIntegrityIssue:
    scope: str          # "snapshot" | "format" | "asset"
    detail: str
    asset_id: str | None = None
    asset_name: str | None = None
    expected_sha256: str | None = None


@dataclass(frozen=True)
class EntityChange:
    collection: str
    entity_id: str
    change_type: str    # "added" | "removed" | "modified"
    label: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectDiff:
    root_changes: tuple[str, ...] = ()
    entity_changes: tuple[EntityChange, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.root_changes and not self.entity_changes

    def by_collection(self, collection: str) -> list[EntityChange]:
        return [c for c in self.entity_changes if c.collection == collection]

    def summary_lines(self) -> list[str]:
        if self.is_empty:
            return ["两个版本内容完全一致。"]
        lines: list[str] = []
        for change in self.root_changes:
            lines.append(f"[项目属性] {change}")
        op_labels = {"added": "新增", "removed": "删除", "modified": "修改"}
        for change in self.entity_changes:
            col_name = COLLECTION_LABELS.get(change.collection, change.collection)
            op = op_labels.get(change.change_type, change.change_type)
            suffix = f"（{'；'.join(change.details)}）" if change.details else ""
            lines.append(
                f"[{col_name}] {op} {change.label} [{change.entity_id[:8]}]{suffix}"
            )
        return lines


@dataclass(frozen=True)
class CleanupReport:
    removed_objects: int = 0
    removed_orphan_assets: int = 0
    freed_bytes: int = 0


def _versions_root(directory: Path | str) -> Path:
    return Path(directory) / VERSIONS_DIRNAME


def _index_path(directory: Path | str) -> Path:
    return _versions_root(directory) / "index.json"


def _snapshot_path(directory: Path | str, version_id: str) -> Path:
    return _versions_root(directory) / "snapshots" / f"{version_id}.json"


def _object_path(directory: Path | str, sha256_hex: str) -> Path:
    return _versions_root(directory) / "objects" / sha256_hex[:2] / sha256_hex


def _canonical_json_sha256(data: dict[str, Any]) -> str:
    encoded = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    """Stream-copy ``source`` to ``destination`` atomically while verifying SHA-256."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            with source.open("rb") as input_file:
                while chunk := input_file.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise VersionIntegrityError(
                f"素材内容校验失败（期望 {expected_sha256[:12]}，实际 {actual[:12]}）：{source}"
            )
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_index(directory: Path | str) -> list[VersionRecord]:
    path = _index_path(directory)
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8") as stream:
            raw = json.load(stream, object_pairs_hook=_unique_object)
    except (OSError, ValueError, ProjectFormatError) as error:
        raise VersionError(f"版本索引损坏：{error}") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != VERSION_STORE_SCHEMA:
        raise VersionError("不支持的版本索引格式")
    entries = raw.get("versions", [])
    if not isinstance(entries, list):
        raise VersionError("版本索引条目格式无效")
    return [VersionRecord.from_dict(item) for item in entries]


def _write_index(directory: Path | str, records: list[VersionRecord]) -> None:
    payload = {
        "schema_version": VERSION_STORE_SCHEMA,
        "versions": [record.to_dict() for record in records],
    }
    _atomic_json(_index_path(directory), payload)


def _project_stats(data: dict[str, Any]) -> dict[str, int]:
    return {
        "nodes": len(data.get("nodes", [])),
        "edges": len(data.get("edges", [])),
        "assets": len(data.get("assets", [])),
        "characters": len(data.get("characters", [])),
        "documents": len(data.get("documents", [])),
        "excerpts": len(data.get("excerpts", [])),
    }


def list_versions(directory: Path | str) -> list[VersionRecord]:
    """Return recorded versions ordered from newest to oldest."""
    return list(reversed(_read_index(directory)))


def load_version_snapshot(directory: Path | str, version_id: str) -> dict[str, Any]:
    """Load and format-validate a stored version snapshot without restoring it."""
    root = Path(directory)
    path = _snapshot_path(root, version_id)
    if not path.is_file():
        raise VersionError(f"找不到版本快照：{version_id}")
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream, object_pairs_hook=_unique_object)
    except (OSError, ValueError, ProjectFormatError) as error:
        raise VersionIntegrityError(
            f"版本快照 JSON 格式错误：{error}",
            [VersionIntegrityIssue("snapshot", str(error))],
        ) from error
    try:
        validate_project(data, root)
    except ProjectFormatError as error:
        raise VersionIntegrityError(
            f"版本快照数据结构校验失败：{error}",
            [VersionIntegrityIssue("format", str(error))],
        ) from error
    return data


def create_version(
    directory: Path | str,
    data: dict[str, Any],
    *,
    label: str = "",
    auto: bool = False,
    skip_if_unchanged: bool = False,
) -> VersionRecord:
    """Persist a validated project snapshot and deduplicated asset blobs.

    If ``skip_if_unchanged`` is True and the latest recorded version has the
    exact same canonical JSON digest (and its snapshot still exists), the
    existing latest ``VersionRecord`` is returned without duplicating it.
    """
    root = Path(directory)
    validate_project(data, root)
    project_digest = _canonical_json_sha256(data)
    records = _read_index(root)

    if (
        skip_if_unchanged
        and records
        and records[-1].project_sha256 == project_digest
        and _snapshot_path(root, records[-1].version_id).is_file()
    ):
        return records[-1]

    # Archive every referenced asset into content-addressed .versions/objects/
    asset_hashes: list[str] = []
    seen_hashes: set[str] = set()
    for asset in data.get("assets", []):
        sha = asset["sha256"]
        if sha not in seen_hashes:
            seen_hashes.add(sha)
            asset_hashes.append(sha)
        blob = _object_path(root, sha)
        if blob.is_file():
            # Verify existing blob matches expected hash; re-copy if corrupted.
            if _sha256_file(blob) == sha:
                continue
        live_path = resolve_asset_path(root, asset["path"])
        if not live_path.is_file():
            raise VersionIntegrityError(
                f"无法创建版本：素材文件缺失 ({asset['name']} -> {asset['path']})",
                [
                    VersionIntegrityIssue(
                        "asset",
                        f"素材文件缺失：{asset['path']}",
                        asset_id=asset["id"],
                        asset_name=asset["name"],
                        expected_sha256=sha,
                    )
                ],
            )
        _atomic_copy_verified(live_path, blob, sha)

    version_id = new_id()
    snapshot_file = _snapshot_path(root, version_id)
    _atomic_json(snapshot_file, data)

    record = VersionRecord(
        version_id=version_id,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        label=label.strip() or ("自动保存快照" if auto else "手动快照"),
        auto=auto,
        project_sha256=project_digest,
        asset_hashes=tuple(sorted(asset_hashes)),
        stats=_project_stats(data),
    )
    records.append(record)
    _write_index(root, records)
    return record


def verify_version(directory: Path | str, version_id: str) -> list[VersionIntegrityIssue]:
    """Validate snapshot format and verify every referenced asset's content hash."""
    root = Path(directory)
    issues: list[VersionIntegrityIssue] = []
    try:
        snapshot = load_version_snapshot(root, version_id)
    except VersionIntegrityError as error:
        return list(error.issues) or [VersionIntegrityIssue("snapshot", str(error))]
    except VersionError as error:
        return [VersionIntegrityIssue("snapshot", str(error))]

    for asset in snapshot.get("assets", []):
        sha = asset["sha256"]
        blob = _object_path(root, sha)
        live_path = resolve_asset_path(root, asset["path"])

        blob_ok = blob.is_file() and _sha256_file(blob) == sha
        live_ok = live_path.is_file() and _sha256_file(live_path) == sha

        if not blob_ok and not live_ok:
            if blob.is_file() or live_path.is_file():
                detail = f"素材内容哈希不匹配：{asset['name']} ({asset['path']})"
            else:
                detail = f"素材内容缺失：{asset['name']} ({asset['path']})"
            issues.append(
                VersionIntegrityIssue(
                    scope="asset",
                    detail=detail,
                    asset_id=asset["id"],
                    asset_name=asset["name"],
                    expected_sha256=sha,
                )
            )
    return issues


def restore_version(
    directory: Path | str,
    version_id: str,
    *,
    backup_current: bool = True,
) -> dict[str, Any]:
    """Verify integrity & format, restore missing assets, then load via project boundary.

    Returns the reloaded validated project dict. If any format or asset
    integrity check fails, raises ``VersionIntegrityError`` without modifying
    ``project.json`` or ``assets/``.
    """
    root = Path(directory)
    issues = verify_version(root, version_id)
    if issues:
        summary = "；".join(issue.detail for issue in issues[:3])
        raise VersionIntegrityError(f"恢复前校验未通过：{summary}", issues)

    snapshot = load_version_snapshot(root, version_id)

    # Optionally record a safety snapshot of the current valid project before overwriting.
    if backup_current and (root / "project.json").is_file():
        try:
            current = load_project(root)
            create_version(
                root,
                current,
                label="恢复前自动备份",
                auto=True,
                skip_if_unchanged=True,
            )
        except (OSError, ValueError, VersionError):
            # Do not block recovery if the current working tree is broken and the
            # user is explicitly restoring a known-good historical version.
            pass

    # Materialize any missing or diverged live asset files from content-addressed store.
    for asset in snapshot.get("assets", []):
        sha = asset["sha256"]
        live_path = resolve_asset_path(root, asset["path"])
        if live_path.is_file() and _sha256_file(live_path) == sha:
            continue
        blob = _object_path(root, sha)
        _atomic_copy_verified(blob, live_path, sha)

    # Commit through the normal save & load boundaries so every invariant is checked.
    save_project(root, snapshot)
    return load_project(root)


def delete_version(
    directory: Path | str,
    version_id: str,
    *,
    prune: bool = True,
) -> CleanupReport:
    """Delete a version snapshot and optionally garbage-collect unreferenced blobs."""
    root = Path(directory)
    records = _read_index(root)
    remaining = [record for record in records if record.version_id != version_id]
    if len(remaining) == len(records):
        raise VersionError(f"找不到版本：{version_id}")
    _write_index(root, remaining)
    _snapshot_path(root, version_id).unlink(missing_ok=True)
    if prune:
        return prune_unused_assets(root, clean_orphan_assets=False)
    return CleanupReport()


def prune_unused_assets(
    directory: Path | str,
    *,
    current_project: dict[str, Any] | None = None,
    clean_orphan_assets: bool = True,
) -> CleanupReport:
    """Garbage-collect unreferenced content-addressed blobs and orphaned asset files.

    - Blobs in ``.versions/objects/`` are retained as long as ANY remaining
      version snapshot references their SHA-256.
    - When ``clean_orphan_assets`` is True, files in ``assets/`` that are not
      referenced by the current project are removed (historical versions keep
      their own content-addressed copies in ``.versions/objects/``).
    """
    root = Path(directory)
    records = _read_index(root)

    referenced_hashes: set[str] = set()
    for record in records:
        snap_file = _snapshot_path(root, record.version_id)
        if snap_file.is_file():
            try:
                snap = load_version_snapshot(root, record.version_id)
                for asset in snap.get("assets", []):
                    referenced_hashes.add(asset["sha256"])
                continue
            except VersionError:
                pass
        referenced_hashes.update(record.asset_hashes)

    removed_objects = 0
    freed_bytes = 0
    objects_root = _versions_root(root) / "objects"
    if objects_root.is_dir():
        for bucket in list(objects_root.iterdir()):
            if not bucket.is_dir():
                continue
            for blob in list(bucket.iterdir()):
                if not blob.is_file():
                    continue
                if blob.name not in referenced_hashes:
                    try:
                        freed_bytes += blob.stat().st_size
                        blob.unlink()
                        removed_objects += 1
                    except OSError:
                        pass
            try:
                if not any(bucket.iterdir()):
                    bucket.rmdir()
            except OSError:
                pass

    removed_orphans = 0
    if clean_orphan_assets:
        project_data = current_project
        if project_data is None and (root / "project.json").is_file():
            try:
                project_data = load_project(root)
            except (OSError, ValueError):
                project_data = None
        if project_data is not None:
            live_paths: set[Path] = set()
            for asset in project_data.get("assets", []):
                try:
                    live_paths.add(resolve_asset_path(root, asset["path"]).resolve())
                except ProjectFormatError:
                    pass
            assets_dir = root / "assets"
            if assets_dir.is_dir():
                for candidate in list(assets_dir.rglob("*")):
                    if candidate.is_file() and candidate.resolve() not in live_paths:
                        try:
                            freed_bytes += candidate.stat().st_size
                            candidate.unlink()
                            removed_orphans += 1
                        except OSError:
                            pass

    return CleanupReport(
        removed_objects=removed_objects,
        removed_orphan_assets=removed_orphans,
        freed_bytes=freed_bytes,
    )


def _entity_label(collection: str, record: dict[str, Any]) -> str:
    if collection in {"nodes", "assets", "characters", "variables"}:
        return str(record.get("name") or record["id"][:8])
    if collection == "documents":
        return str(record.get("title") or record["id"][:8])
    if collection == "relationships":
        return str(record.get("label") or "relationship")
    if collection == "edges":
        src = str(record.get("from_node_id", ""))[:8]
        dst = str(record.get("to_node_id", ""))[:8]
        return f"{src} -> {dst}"
    if collection == "excerpts":
        return f"摘录 ({len(record.get('item_ids', []))} 条)"
    return str(record["id"][:8])


def _describe_entity_modification(
    collection: str, old: dict[str, Any], new: dict[str, Any]
) -> tuple[str, ...]:
    details: list[str] = []
    if collection == "nodes":
        if old.get("name") != new.get("name"):
            details.append(f"改名：{old.get('name')} -> {new.get('name')}")
        old_items = {item["id"]: item for item in old.get("items", [])}
        new_items = {item["id"]: item for item in new.get("items", [])}
        added_i = len(new_items.keys() - old_items.keys())
        removed_i = len(old_items.keys() - new_items.keys())
        mod_i = sum(
            1 for key in (old_items.keys() & new_items.keys())
            if old_items[key] != new_items[key]
        )
        if added_i or removed_i or mod_i:
            parts = []
            if added_i:
                parts.append(f"+{added_i}")
            if removed_i:
                parts.append(f"-{removed_i}")
            if mod_i:
                parts.append(f"~{mod_i}")
            details.append(f"演出条目 {'/'.join(parts)}")
        elif [i["id"] for i in old.get("items", [])] != [i["id"] for i in new.get("items", [])]:
            details.append("演出条目顺序调整")

        old_choices = {c["id"]: c for c in old.get("choices", [])}
        new_choices = {c["id"]: c for c in new.get("choices", [])}
        if old_choices != new_choices:
            details.append("分支选项变更")
        if old.get("ending") != new.get("ending"):
            details.append("结局设置变更")
    elif collection == "assets":
        if old.get("sha256") != new.get("sha256"):
            details.append("文件内容已替换")
        if old.get("name") != new.get("name"):
            details.append(f"改名：{old.get('name')} -> {new.get('name')}")
        if old.get("tags", []) != new.get("tags", []):
            details.append("分类标签变更")
    elif collection == "edges":
        if old.get("condition") != new.get("condition"):
            details.append("条件变更")
        if old.get("effects") != new.get("effects"):
            details.append("变量变更列表更新")
    elif collection == "documents":
        if old.get("title") != new.get("title"):
            details.append(f"标题：{old.get('title')} -> {new.get('title')}")
        if old.get("text") != new.get("text"):
            details.append("正文内容变更")
    elif collection == "characters":
        if old.get("name") != new.get("name"):
            details.append(f"姓名：{old.get('name')} -> {new.get('name')}")
        if old.get("description") != new.get("description"):
            details.append("介绍变更")
        if old.get("reference_art_id") != new.get("reference_art_id"):
            details.append("识别图变更")
    elif collection == "variables":
        if old.get("name") != new.get("name"):
            details.append(f"名称：{old.get('name')} -> {new.get('name')}")
        if old.get("type") != new.get("type"):
            details.append(f"类型：{old.get('type')} -> {new.get('type')}")
        if old.get("initial") != new.get("initial"):
            details.append(f"初值：{old.get('initial')} -> {new.get('initial')}")
    return tuple(details)


def diff_projects(old_data: dict[str, Any], new_data: dict[str, Any]) -> ProjectDiff:
    """Compare two project snapshots by stable object ID."""
    root_changes: list[str] = []
    if old_data.get("title") != new_data.get("title"):
        root_changes.append(f"标题：{old_data.get('title')} -> {new_data.get('title')}")
    if old_data.get("start_node_id") != new_data.get("start_node_id"):
        root_changes.append("开场节点已变更")
    if old_data.get("theme") != new_data.get("theme"):
        root_changes.append("界面主题已变更")

    entity_changes: list[EntityChange] = []
    for collection in ENTITY_COLLECTIONS:
        old_records = {rec["id"]: rec for rec in old_data.get(collection, [])}
        new_records = {rec["id"]: rec for rec in new_data.get(collection, [])}

        for rec in new_data.get(collection, []):
            entity_id = rec["id"]
            if entity_id not in old_records:
                entity_changes.append(
                    EntityChange(
                        collection=collection,
                        entity_id=entity_id,
                        change_type="added",
                        label=_entity_label(collection, rec),
                    )
                )
        for rec in old_data.get(collection, []):
            entity_id = rec["id"]
            if entity_id not in new_records:
                entity_changes.append(
                    EntityChange(
                        collection=collection,
                        entity_id=entity_id,
                        change_type="removed",
                        label=_entity_label(collection, rec),
                    )
                )
        for rec in new_data.get(collection, []):
            entity_id = rec["id"]
            if entity_id in old_records and old_records[entity_id] != rec:
                details = _describe_entity_modification(collection, old_records[entity_id], rec)
                entity_changes.append(
                    EntityChange(
                        collection=collection,
                        entity_id=entity_id,
                        change_type="modified",
                        label=_entity_label(collection, rec),
                        details=details,
                    )
                )

    return ProjectDiff(
        root_changes=tuple(root_changes),
        entity_changes=tuple(entity_changes),
    )


def diff_versions(
    directory: Path | str, old_version_id: str, new_version_id: str
) -> ProjectDiff:
    """Compare two stored versions by stable object ID."""
    old_data = load_version_snapshot(directory, old_version_id)
    new_data = load_version_snapshot(directory, new_version_id)
    return diff_projects(old_data, new_data)


def diff_with_current(
    directory: Path | str, version_id: str, current_data: dict[str, Any]
) -> ProjectDiff:
    """Compare a stored version snapshot against an in-memory project snapshot."""
    old_data = load_version_snapshot(directory, version_id)
    return diff_projects(old_data, current_data)

"""Rebuildable local SQLite FTS5 derived search index for visual-novel projects.

The editable ``project.json`` remains the single source of truth. This index is
strictly derived data: if missing, corrupted, or schema-mismatched it is
automatically rebuilt from the in-memory project snapshot.

Incremental updates compare per-entity content fingerprints and track a
project data version token so unchanged snapshots incur zero writes.

For CJK/Chinese substring matching, SQLite FTS5's built-in ``trigram``
tokenizer indexes arbitrary 3+ character substrings without requiring an external
segmenter. Queries containing terms shorter than 3 characters (common in
Chinese, e.g. 1-2 character names or keywords) transparently fall back to
SQLite ``instr()`` substring filtering over the indexed table, or combine FTS5
candidate narrowing with ``instr()`` for multi-term queries.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INDEX_SCHEMA_VERSION = "1"

# Ordered priority for tie-breaking when ranks are equal.
KIND_ORDER = {
    "asset": 0,
    "character": 1,
    "document": 2,
    "node": 3,
    "text": 4,
    "excerpt": 5,
}


@dataclass(frozen=True)
class IndexedSearchResult:
    kind: str
    entity_id: str
    node_id: str | None
    label: str
    snippet: str = ""


@dataclass(frozen=True)
class SyncStats:
    added: int = 0
    updated: int = 0
    deleted: int = 0
    rebuilt: bool = False
    skipped: bool = False


def _excerpt_text(project: dict[str, Any], excerpt: dict[str, Any]) -> str | None:
    """Resolve live excerpt text; returns None if any source item/node is missing."""
    node = next((n for n in project.get("nodes", []) if n["id"] == excerpt.get("node_id")), None)
    if node is None:
        return None
    current = {
        item["id"]: item
        for item in node.get("items", [])
        if item.get("type") in {"dialogue", "narration"}
    }
    item_ids = excerpt.get("item_ids", [])
    if not item_ids or any(item_id not in current for item_id in item_ids):
        return None
    selected = set(item_ids)
    return "\n".join(
        item.get("text", "")
        for item in node.get("items", [])
        if item["id"] in selected
    )


def _content_hash(
    kind: str,
    entity_id: str,
    node_id: str | None,
    label: str,
    title: str,
    body: str,
) -> str:
    payload = json.dumps(
        [kind, entity_id, node_id, label, title, body],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def extract_documents(project: dict[str, Any]) -> dict[str, tuple[str, str, str | None, str, str, str, str]]:
    """Extract all searchable entities from a project snapshot.

    Returns a mapping of ``doc_key -> (kind, entity_id, node_id, label, title, body, content_hash)``.
    Invalidated excerpts (whose source node or text item was removed) are skipped
    so they are paused from search as specified in the architecture contract.
    """
    docs: dict[str, tuple[str, str, str | None, str, str, str, str]] = {}

    def _put(kind: str, entity_id: str, node_id: str | None, label: str, title: str, body: str) -> None:
        doc_key = f"{kind}:{entity_id}"
        digest = _content_hash(kind, entity_id, node_id, label, title, body)
        docs[doc_key] = (kind, entity_id, node_id, label, title, body, digest)

    for asset in project.get("assets", []):
        name = str(asset.get("name", ""))
        kind_tag = str(asset.get("kind", ""))
        tags = " ".join(str(t) for t in asset.get("tags", []))
        body = f"{kind_tag} {tags}".strip()
        _put("asset", asset["id"], None, name, name, body)

    for character in project.get("characters", []):
        name = str(character.get("name", ""))
        description = str(character.get("description", ""))
        _put("character", character["id"], None, name, name, description)

    for document in project.get("documents", []):
        title = str(document.get("title", ""))
        text = str(document.get("text", ""))
        _put("document", document["id"], None, title, title, text)

    for node in project.get("nodes", []):
        node_id = node["id"]
        node_name = str(node.get("name", ""))
        _put("node", node_id, None, node_name, node_name, "")
        for item in node.get("items", []):
            if item.get("type") in {"dialogue", "narration"}:
                text = str(item.get("text", ""))
                _put("text", item["id"], node_id, text[:80], "", text)

    for excerpt in project.get("excerpts", []):
        text = _excerpt_text(project, excerpt)
        if text is None:
            continue
        label = text.replace("\n", " ")[:72]
        _put("excerpt", excerpt["id"], excerpt.get("node_id"), label, label, text)

    return docs


def _fts5_phrase(term: str) -> str:
    """Wrap a literal term as an FTS5 phrase so operators/punctuation stay literal."""
    escaped = term.replace('"', '""')
    return f'"{escaped}"'


class ProjectSearchIndex:
    """Local SQLite FTS5 derived search index with incremental versioned sync."""

    def __init__(self, db_path: Path | str | None = None):
        """Open or create the derived index.

        Pass ``db_path=None`` for an in-memory index (used when editing an
        unsaved or synthetic project dict without a folder on disk).
        """
        self.db_path: Path | None = Path(db_path) if db_path is not None else None
        self._conn: sqlite3.Connection | None = None
        self._finalizer: weakref.finalize | None = None
        self._open_and_init()

    @staticmethod
    def _close_connection(conn: sqlite3.Connection | None) -> None:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _connect(self) -> sqlite3.Connection:
        if self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        else:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS search_docs (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_key TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                node_id TEXT,
                label TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_search_docs_kind ON search_docs(kind);

            CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
                title,
                body,
                content='search_docs',
                content_rowid='rowid',
                tokenize='trigram'
            );

            CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
                INSERT INTO search_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
            END;

            CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
                INSERT INTO search_fts(search_fts, rowid, title, body)
                VALUES ('delete', old.rowid, old.title, old.body);
            END;

            CREATE TRIGGER IF NOT EXISTS search_docs_au AFTER UPDATE ON search_docs BEGIN
                INSERT INTO search_fts(search_fts, rowid, title, body)
                VALUES ('delete', old.rowid, old.title, old.body);
                INSERT INTO search_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
            END;
            """
        )

    def _open_and_init(self) -> None:
        self.close()
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            self._create_schema(conn)
            schema = self._get_meta(conn, "index_schema_version")
            if schema is not None and schema != INDEX_SCHEMA_VERSION:
                conn.close()
                conn = None
                self._reset_db_file()
                conn = self._connect()
                self._create_schema(conn)
            self._set_meta(conn, "index_schema_version", INDEX_SCHEMA_VERSION)
            conn.commit()
            self._conn = conn
            self._finalizer = weakref.finalize(self, self._close_connection, conn)
        except sqlite3.DatabaseError:
            if conn is not None:
                self._close_connection(conn)
                conn = None
            self._reset_db_file()
            conn = self._connect()
            self._create_schema(conn)
            self._set_meta(conn, "index_schema_version", INDEX_SCHEMA_VERSION)
            conn.commit()
            self._conn = conn
            self._finalizer = weakref.finalize(self, self._close_connection, conn)

    def _reset_db_file(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
        if self.db_path is not None:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{self.db_path}{suffix}")
                candidate.unlink(missing_ok=True)

    @staticmethod
    def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def close(self) -> None:
        if self._finalizer is not None:
            self._finalizer.detach()
            self._finalizer = None
        if self._conn is not None:
            self._close_connection(self._conn)
            self._conn = None

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._open_and_init()
        assert self._conn is not None
        return self._conn

    def data_version(self) -> str | None:
        conn = self._ensure_conn()
        return self._get_meta(conn, "data_version")

    def document_count(self) -> int:
        conn = self._ensure_conn()
        row = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()
        return int(row[0]) if row else 0

    def rebuild(self, project: dict[str, Any], data_version: str | int | None = None) -> SyncStats:
        """Drop and recreate the index from scratch from the authoritative project."""
        self._reset_db_file()
        self._open_and_init()
        stats = self.sync(project, data_version=data_version)
        return SyncStats(
            added=stats.added,
            updated=stats.updated,
            deleted=stats.deleted,
            rebuilt=True,
            skipped=False,
        )

    def sync(self, project: dict[str, Any], data_version: str | int | None = None) -> SyncStats:
        """Incrementally synchronize the SQLite FTS5 index with ``project``.

        If ``data_version`` is provided and matches the stored version for the
        same ``project_id``, synchronization is skipped in O(1). Otherwise only
        inserted, modified, or deleted entities are written inside one transaction.
        """
        try:
            return self._sync_inner(project, data_version)
        except sqlite3.DatabaseError:
            return self.rebuild(project, data_version=data_version)

    def _sync_inner(self, project: dict[str, Any], data_version: str | int | None) -> SyncStats:
        conn = self._ensure_conn()
        project_id = str(project.get("project_id", ""))
        version_str = str(data_version) if data_version is not None else None
        stored_project_id = self._get_meta(conn, "project_id")
        stored_version = self._get_meta(conn, "data_version")

        if (
            version_str is not None
            and stored_project_id == project_id
            and stored_version == version_str
        ):
            return SyncStats(skipped=True)

        desired = extract_documents(project)
        rebuilt = False

        with conn:
            if stored_project_id is not None and stored_project_id != project_id:
                conn.execute("DELETE FROM search_docs")
                rebuilt = True

            existing = {
                row[0]: row[1]
                for row in conn.execute("SELECT doc_key, content_hash FROM search_docs")
            }

            to_delete = [key for key in existing if key not in desired]
            to_insert = [
                (key, *desired[key])
                for key in desired
                if key not in existing
            ]
            to_update = [
                (*desired[key], key)
                for key in desired
                if key in existing and existing[key] != desired[key][6]
            ]

            if to_delete:
                conn.executemany(
                    "DELETE FROM search_docs WHERE doc_key = ?",
                    [(key,) for key in to_delete],
                )
            if to_insert:
                conn.executemany(
                    "INSERT INTO search_docs "
                    "(doc_key, kind, entity_id, node_id, label, title, body, content_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    to_insert,
                )
            if to_update:
                conn.executemany(
                    "UPDATE search_docs SET "
                    "kind = ?, entity_id = ?, node_id = ?, label = ?, "
                    "title = ?, body = ?, content_hash = ? "
                    "WHERE doc_key = ?",
                    to_update,
                )

            self._set_meta(conn, "project_id", project_id)
            if version_str is not None:
                self._set_meta(conn, "data_version", version_str)
            else:
                # Clear explicit version token when synced without one so future
                # versioned syncs do not falsely skip.
                conn.execute("DELETE FROM meta WHERE key = 'data_version'")

        return SyncStats(
            added=len(to_insert),
            updated=len(to_update),
            deleted=len(to_delete),
            rebuilt=rebuilt,
            skipped=False,
        )

    def search(
        self,
        query: str,
        *,
        kinds: set[str] | None = None,
        include_excerpts: bool = True,
        limit: int = 200,
    ) -> list[IndexedSearchResult]:
        """Search indexed entities using FTS5 trigram + short-term substring fallback."""
        cleaned = query.strip()
        if not cleaned:
            return []

        conn = self._ensure_conn()
        tokens = [tok for tok in cleaned.split() if tok]
        if not tokens:
            return []

        fts_tokens = [tok for tok in tokens if len(tok) >= 3]
        short_tokens = [tok for tok in tokens if len(tok) < 3]

        where_clauses: list[str] = []
        params: list[Any] = []

        if kinds is not None:
            allowed = sorted(kinds)
            if not allowed:
                return []
            placeholders = ", ".join("?" for _ in allowed)
            where_clauses.append(f"d.kind IN ({placeholders})")
            params.extend(allowed)
        elif not include_excerpts:
            where_clauses.append("d.kind != 'excerpt'")

        for short in short_tokens:
            folded = short.casefold()
            where_clauses.append("(instr(lower(d.title), ?) > 0 OR instr(lower(d.body), ?) > 0)")
            params.extend([folded, folded])

        if fts_tokens:
            match_expr = " AND ".join(_fts5_phrase(tok) for tok in fts_tokens)
            sql = (
                "SELECT d.kind, d.entity_id, d.node_id, d.label, bm25(search_fts) AS rank "
                "FROM search_fts "
                "JOIN search_docs d ON d.rowid = search_fts.rowid "
                "WHERE search_fts MATCH ?"
            )
            query_params: list[Any] = [match_expr]
            if where_clauses:
                sql += " AND " + " AND ".join(where_clauses)
                query_params.extend(params)
            sql += " ORDER BY rank ASC, d.rowid ASC LIMIT ?"
            query_params.append(limit)
            rows = conn.execute(sql, query_params).fetchall()
        else:
            sql = "SELECT d.kind, d.entity_id, d.node_id, d.label, 0.0 AS rank FROM search_docs d"
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            sql += " ORDER BY d.rowid ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()

        # Preserve deterministic ordering: FTS5 rank first (when used), then kind/rowid.
        return [
            IndexedSearchResult(
                kind=row[0],
                entity_id=row[1],
                node_id=row[2],
                label=row[3],
            )
            for row in rows
        ]

"""Editing operations that keep project IDs and cross references stable."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import new_id
from .search_index import ProjectSearchIndex, SyncStats


class EditError(ValueError):
    """An edit would violate project relationships or story graph rules."""


@dataclass(frozen=True)
class SearchResult:
    kind: str
    entity_id: str
    node_id: str | None
    label: str


class ProjectEditor:
    def __init__(
        self,
        project: dict[str, Any],
        *,
        search_index: ProjectSearchIndex | None = None,
        index_path: Path | str | None = None,
    ):
        self.project = project
        self._data_version = 0
        self._owns_index = search_index is None
        self._index_path: Path | None = Path(index_path) if index_path is not None else None
        self._search_index: ProjectSearchIndex | None = search_index

    @property
    def search_index(self) -> ProjectSearchIndex:
        if self._search_index is None:
            self._search_index = ProjectSearchIndex(self._index_path)
        return self._search_index

    def bump_version(self) -> int:
        """Advance the project data version so incremental search indexing re-syncs."""
        self._data_version += 1
        return self._data_version

    @property
    def data_version(self) -> int:
        return self._data_version

    def sync_search_index(self, *, force_version: bool = False) -> SyncStats:
        """Synchronize the derived SQLite FTS5 index with the current project."""
        version = self._data_version if force_version else None
        return self.search_index.sync(self.project, data_version=version)

    def rebuild_search_index(self) -> SyncStats:
        """Rebuild the derived SQLite FTS5 index from scratch."""
        return self.search_index.rebuild(self.project, data_version=self._data_version)

    def close(self) -> None:
        if self._owns_index and self._search_index is not None:
            self._search_index.close()
            self._search_index = None

    def node(self, node_id: str) -> dict[str, Any]:
        for node in self.project["nodes"]:
            if node["id"] == node_id:
                return node
        raise EditError(f"unknown node: {node_id}")

    def add_node(self, name: str) -> dict[str, Any]:
        if not name.strip():
            raise EditError("node name is required")
        node = {"id": new_id(), "name": name, "items": [], "choices": [], "ending": None}
        self.project["nodes"].append(node)
        if self.project["start_node_id"] is None:
            self.project["start_node_id"] = node["id"]
        return node

    def copy_node(self, node_id: str) -> dict[str, Any]:
        original = self.node(node_id)
        duplicate = deepcopy(original)
        duplicate["id"] = new_id()
        duplicate["name"] = f"{original['name']} 副本"
        for item in duplicate["items"]:
            item["id"] = new_id()
        for choice in duplicate.get("choices", []):
            choice["id"] = new_id()
        self.project["nodes"].append(duplicate)
        return duplicate

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        condition: dict[str, Any] | None = None,
        effects: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.node(source_id)
        self.node(target_id)
        if source_id == target_id:
            raise EditError("story connections cannot form a cycle")
        successors: dict[str, list[str]] = {}
        for edge in self.project["edges"]:
            successors.setdefault(edge["from_node_id"], []).append(edge["to_node_id"])
        pending = [target_id]
        visited = set()
        while pending:
            current = pending.pop()
            if current == source_id:
                raise EditError("story connections cannot form a cycle")
            if current not in visited:
                visited.add(current)
                pending.extend(successors.get(current, []))
        edge = {
            "id": new_id(), "from_node_id": source_id, "to_node_id": target_id,
            "condition": condition, "effects": deepcopy(effects or []),
        }
        self.project["edges"].append(edge)
        return edge

    def add_item(self, node_id: str, item_type: str, **fields: Any) -> dict[str, Any]:
        node = self.node(node_id)
        item = {"id": new_id(), "type": item_type, **fields}
        node["items"].append(item)
        return item

    def move_item(self, node_id: str, item_id: str, offset: int) -> None:
        items = self.node(node_id)["items"]
        source = next(
            (index for index, item in enumerate(items) if item["id"] == item_id), None
        )
        if source is None:
            raise EditError(f"unknown item: {item_id}")
        target = source + offset
        if not 0 <= target < len(items):
            return
        items.insert(target, items.pop(source))

    def add_character(self, name: str, description: str = "") -> dict[str, Any]:
        if not name.strip():
            raise EditError("character name is required")
        character = {
            "id": new_id(), "name": name, "description": description,
            "reference_art_id": None,
        }
        self.project["characters"].append(character)
        return character

    def add_relationship(
        self, source_id: str, target_id: str, directed: bool = True,
        label: str = "relationship",
    ) -> dict[str, Any]:
        known = {character["id"] for character in self.project["characters"]}
        if source_id not in known or target_id not in known:
            raise EditError("relationship references an unknown character")
        relation = {
            "id": new_id(), "from_character_id": source_id,
            "to_character_id": target_id, "directed": directed, "label": label,
        }
        self.project.setdefault("relationships", []).append(relation)
        return relation

    def add_excerpt(self, node_id: str, item_ids: list[str]) -> dict[str, Any]:
        node = self.node(node_id)
        text_ids = [
            item["id"] for item in node["items"]
            if item["type"] in {"dialogue", "narration"}
        ]
        if not item_ids or len(set(item_ids)) != len(item_ids):
            raise EditError("excerpt requires distinct text items")
        indexes = [text_ids.index(item_id) if item_id in text_ids else -1 for item_id in item_ids]
        if -1 in indexes or indexes != list(range(indexes[0], indexes[0] + len(indexes))):
            raise EditError("excerpt items must be consecutive text")
        excerpt = {"id": new_id(), "node_id": node_id, "item_ids": item_ids.copy()}
        self.project.setdefault("excerpts", []).append(excerpt)
        return excerpt

    def excerpt_text(self, excerpt: dict[str, Any]) -> str | None:
        try:
            node = self.node(excerpt["node_id"])
        except EditError:
            return None
        current = {
            item["id"]: item for item in node["items"]
            if item["type"] in {"dialogue", "narration"}
        }
        if any(item_id not in current for item_id in excerpt["item_ids"]):
            return None
        selected = set(excerpt["item_ids"])
        return "\n".join(item["text"] for item in node["items"] if item["id"] in selected)

    def search(
        self,
        query: str,
        *,
        kinds: set[str] | None = None,
        include_excerpts: bool = False,
        limit: int = 200,
        use_version_cache: bool = False,
    ) -> list[SearchResult]:
        if not query.strip():
            return []
        version = self._data_version if use_version_cache else None
        self.search_index.sync(self.project, data_version=version)
        hits = self.search_index.search(
            query,
            kinds=kinds,
            include_excerpts=include_excerpts,
            limit=limit,
        )
        return [
            SearchResult(hit.kind, hit.entity_id, hit.node_id, hit.label)
            for hit in hits
        ]

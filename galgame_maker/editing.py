"""Editing operations that keep project IDs and cross references stable."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .project import new_id


class EditError(ValueError):
    """An edit would violate project relationships or story graph rules."""


@dataclass(frozen=True)
class SearchResult:
    kind: str
    entity_id: str
    node_id: str | None
    label: str


class ProjectEditor:
    def __init__(self, project: dict[str, Any]):
        self.project = project

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

    def search(self, query: str) -> list[SearchResult]:
        needle = query.casefold().strip()
        if not needle:
            return []
        results = []
        for asset in self.project["assets"]:
            if needle in asset["name"].casefold():
                results.append(SearchResult("asset", asset["id"], None, asset["name"]))
        for character in self.project["characters"]:
            if needle in (character["name"] + character.get("description", "")).casefold():
                results.append(SearchResult("character", character["id"], None, character["name"]))
        for document in self.project.get("documents", []):
            if needle in (document["title"] + document["text"]).casefold():
                results.append(SearchResult("document", document["id"], None, document["title"]))
        for node in self.project["nodes"]:
            if needle in node["name"].casefold():
                results.append(SearchResult("node", node["id"], None, node["name"]))
            for item in node["items"]:
                if item["type"] in {"dialogue", "narration"} and needle in item["text"].casefold():
                    results.append(SearchResult("text", item["id"], node["id"], item["text"][:80]))
        return results

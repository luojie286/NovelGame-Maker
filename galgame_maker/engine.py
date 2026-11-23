"""Pure Python story state machine shared by preview and exported players."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any

from .conditions import evaluate_condition, validate_condition
from .text_pages import page_end, valid_page_start


class StoryError(ValueError):
    """A malformed story or action prevented a state transition."""


class StoryEngine:
    def __init__(
        self,
        game: dict[str, Any],
        start_node_id: str | None = None,
        initial_variables: dict[str, int | bool] | None = None,
    ):
        self.game = game
        self.nodes = {node["id"]: node for node in game["nodes"]}
        self.variables = {variable["id"]: variable for variable in game["variables"]}
        self.edges_by_source: dict[str, list[dict[str, Any]]] = {
            node_id: [] for node_id in self.nodes
        }
        if len(self.nodes) != len(game["nodes"]):
            raise StoryError("duplicate node ID")
        if len(self.variables) != len(game["variables"]):
            raise StoryError("duplicate variable ID")
        incoming = {node_id: 0 for node_id in self.nodes}
        for edge in game["edges"]:
            if edge["from_node_id"] not in self.nodes or edge["to_node_id"] not in self.nodes:
                raise StoryError(f"edge has a missing node: {edge['id']}")
            self.edges_by_source[edge["from_node_id"]].append(edge)
            incoming[edge["to_node_id"]] += 1
        ready = deque(node_id for node_id, count in incoming.items() if count == 0)
        visited = 0
        while ready:
            current = ready.popleft()
            visited += 1
            for edge in self.edges_by_source[current]:
                target = edge["to_node_id"]
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
        if visited != len(self.nodes):
            raise StoryError("story graph contains a cycle")
        entry_node = start_node_id if start_node_id is not None else game["start_node_id"]
        if entry_node not in self.nodes:
            raise StoryError("start node is missing")
        self.variables_state = {
            variable_id: definition["initial_value"]
            for variable_id, definition in self.variables.items()
        }
        if initial_variables is not None:
            for variable_id, value in initial_variables.items():
                if variable_id not in self.variables:
                    raise StoryError(f"unknown initial variable: {variable_id}")
                expected = int if self.variables[variable_id]["type"] == "int" else bool
                if type(value) is not expected:
                    raise StoryError(f"initial variable type mismatch: {variable_id}")
                self.variables_state[variable_id] = value
        self.choices: dict[str, str] = {}
        self.history: list[dict[str, str]] = []
        self.node_id = entry_node
        self.item_index = 0
        self.page_start_offset = 0
        self.phase = "starting"
        self.error: str | None = None
        self.presentation = {
            "background_asset_id": None,
            "sprites": {},
            "bgm": None,
            "line_item_id": None,
        }
        self.commands: list[dict[str, Any]] = []
        self._run_items()

    def _run_items(self) -> None:
        """Execute nonblocking actions once until the next player decision."""
        while True:
            node = self.nodes[self.node_id]
            items = node["items"]
            while self.item_index < len(items):
                item = items[self.item_index]
                kind = item["type"]
                if kind in {"dialogue", "narration"}:
                    self.presentation["line_item_id"] = item["id"]
                    self.phase = "waiting_advance"
                    self.history.append({"node_id": self.node_id, "item_id": item["id"]})
                    if item.get("voice_asset_id"):
                        self.commands.append({"type": "play_voice", "asset_id": item["voice_asset_id"]})
                    return
                if kind == "set_background":
                    self.presentation["background_asset_id"] = item["asset_id"]
                elif kind == "show_sprite":
                    self.presentation["sprites"][item["slot"]] = {
                        "asset_id": item["asset_id"],
                        "placement": item.get("placement"),
                    }
                elif kind == "hide_sprite":
                    self.presentation["sprites"].pop(item["slot"], None)
                elif kind == "play_bgm":
                    bgm = {
                        "asset_id": item["asset_id"],
                        "loop": item.get("loop", True),
                        "volume_pct": item.get("volume_pct", 100),
                    }
                    self.presentation["bgm"] = bgm
                    self.commands.append({"type": "play_bgm", **bgm})
                elif kind == "stop_bgm":
                    self.presentation["bgm"] = None
                    self.commands.append({"type": "stop_bgm"})
                elif kind == "play_sfx":
                    self.commands.append({
                        "type": "play_sfx", "asset_id": item["asset_id"],
                        "volume_pct": item.get("volume_pct", 100),
                    })
                else:
                    raise StoryError(f"unsupported item type: {kind}")
                self.item_index += 1

            self.presentation["line_item_id"] = None
            if node.get("choices"):
                self.phase = "waiting_choice"
                return
            if not self._finish_node(None):
                return

    def advance(self, max_chars: int = 80) -> None:
        if self.phase != "waiting_advance":
            raise StoryError("advance is unavailable in this phase")
        item = self.nodes[self.node_id]["items"][self.item_index]
        if self.page_start_offset == 0 and item.get("voice_asset_id"):
            self.commands.append({"type": "stop_voice"})
        limit = min(80, max(1, max_chars))
        next_offset = page_end(item["text"], self.page_start_offset, maximum=limit)
        if next_offset < len(item["text"]):
            self.page_start_offset = next_offset
            return
        self.item_index += 1
        self.page_start_offset = 0
        self.presentation["line_item_id"] = None
        self._run_items()

    def current_page(self, max_chars: int = 80) -> str:
        if self.phase != "waiting_advance":
            raise StoryError("no dialogue page is active")
        text = self.nodes[self.node_id]["items"][self.item_index]["text"]
        limit = min(80, max(1, max_chars))
        end = page_end(text, self.page_start_offset, maximum=limit)
        return text[self.page_start_offset:end]

    def select(self, choice_id: str) -> None:
        if self.phase not in {"waiting_choice", "stalled"}:
            raise StoryError("choice is unavailable in this phase")
        node = self.nodes[self.node_id]
        valid_ids = {choice["id"] for choice in node.get("choices", [])}
        if choice_id not in valid_ids:
            raise StoryError(f"invalid choice: {choice_id}")
        source_id = self.node_id
        entered_node = self._finish_node(choice_id)
        self.choices[source_id] = choice_id
        self.history.append({"node_id": source_id, "choice_id": choice_id})
        if entered_node:
            self._run_items()

    def _finish_node(self, choice_id: str | None) -> bool:
        node = self.nodes[self.node_id]
        edges = self.edges_by_source[self.node_id]
        if not edges:
            ending = node.get("ending")
            if ending is None:
                self.phase = "stalled"
                return False
            video = ending["credits_video_asset_id"]
            if video is None:
                self.phase = "finished"
            else:
                self.phase = "playing_credits"
                self.commands.append({"type": "play_credits", "asset_id": video})
            return False

        choice_ids = {choice["id"] for choice in node.get("choices", [])}
        matches = []
        for edge in edges:
            condition = edge["condition"]
            if condition is None:
                if len(edges) != 1 or choice_ids:
                    raise StoryError(f"invalid unconditional edge: {edge['id']}")
                matches.append(edge)
                continue
            validate_condition(condition, self.variables, choice_ids)
            if evaluate_condition(condition, self.variables_state, choice_id):
                matches.append(edge)
        if not matches:
            self.phase = "stalled"
            self.error = "NO_MATCHING_BRANCH"
            return False
        if len(matches) > 1:
            raise StoryError("multiple outgoing edges matched")

        edge = matches[0]
        next_values = self.variables_state.copy()
        for effect in edge.get("effects", []):
            variable_id = effect["variable_id"]
            if variable_id not in self.variables:
                raise StoryError(f"unknown variable in effect: {variable_id}")
            definition = self.variables[variable_id]
            value = effect["value"]
            expected = int if definition["type"] == "int" else bool
            if type(value) is not expected:
                raise StoryError(f"effect type mismatch: {variable_id}")
            operation = effect["op"]
            if operation == "set":
                next_values[variable_id] = value
            elif operation in {"add", "subtract"} and expected is int:
                next_values[variable_id] += value if operation == "add" else -value
            else:
                raise StoryError(f"invalid effect operation: {operation}")

        # Effects and target position commit together. Failed effects retain
        # the source position and all old variable values.
        self.variables_state = next_values
        self.node_id = edge["to_node_id"]
        self.item_index = 0
        self.page_start_offset = 0
        self.error = None
        self.phase = "starting"
        return True

    def media_finished(self) -> None:
        if self.phase != "playing_credits":
            raise StoryError("no credits video is playing")
        self.phase = "finished"

    def take_commands(self) -> list[dict[str, Any]]:
        commands = self.commands
        self.commands = []
        return commands

    def snapshot(self) -> dict[str, Any]:
        if self.phase not in {"waiting_advance", "waiting_choice", "stalled"}:
            raise StoryError("saving is unavailable in this phase")
        item_id = self.presentation["line_item_id"]
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "item_id": item_id,
            "variables": self.variables_state.copy(),
            "choices": self.choices.copy(),
            "history": deepcopy(self.history),
            "page_start_offset": self.page_start_offset if item_id is not None else None,
            "presentation": deepcopy(self.presentation),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore a stable wait point without replaying effects or one-shot media."""
        node_id = snapshot["node_id"]
        if node_id not in self.nodes:
            raise StoryError(f"save references missing node: {node_id}")
        phase = snapshot["phase"]
        if phase not in {"waiting_advance", "waiting_choice", "stalled"}:
            raise StoryError(f"invalid save phase: {phase}")
        item_id = snapshot["item_id"]
        items = self.nodes[node_id]["items"]
        if phase == "waiting_advance":
            indexes = [
                index for index, item in enumerate(items)
                if item["id"] == item_id and item["type"] in {"dialogue", "narration"}
            ]
            if len(indexes) != 1:
                raise StoryError(f"save references missing text item: {item_id}")
            index = indexes[0]
            offset = snapshot["page_start_offset"]
            text = items[index]["text"]
            if not valid_page_start(text, offset) or offset >= len(text):
                raise StoryError("save page offset is invalid")
        else:
            if item_id is not None:
                raise StoryError("save item_id must be null outside text")
            index = len(items)
            if snapshot["page_start_offset"] is not None:
                raise StoryError("save page offset must be null outside text")
            offset = 0
        values = snapshot["variables"]
        if set(values) != set(self.variables):
            raise StoryError("save variables do not match the game")
        for variable_id, value in values.items():
            expected = int if self.variables[variable_id]["type"] == "int" else bool
            if type(value) is not expected:
                raise StoryError(f"save variable type mismatch: {variable_id}")
        presentation = snapshot["presentation"]
        if presentation["line_item_id"] != item_id:
            raise StoryError("save presentation text is inconsistent")
        history = snapshot.get("history", [])
        if not isinstance(history, list):
            raise StoryError("save history must be an array")
        # Older saves have no history. Seed the currently displayed line so
        # it remains available in the backlog after the player moves on.
        if not history and item_id is not None:
            history = [{"node_id": node_id, "item_id": item_id}]
        for entry in history:
            self.history_item(entry)
        self.node_id = node_id
        self.item_index = index
        self.page_start_offset = offset
        self.phase = phase
        self.variables_state = values.copy()
        self.choices = snapshot["choices"].copy()
        self.history = deepcopy(history)
        self.presentation = deepcopy(presentation)
        self.error = "NO_MATCHING_BRANCH" if phase == "stalled" and self.edges_by_source[node_id] else None
        self.commands = []
        if presentation["bgm"] is not None:
            self.commands.append({"type": "play_bgm", **presentation["bgm"]})

    def history_item(self, entry: dict[str, str]) -> dict[str, Any]:
        """Resolve a backlog reference against the compatible compiled story."""
        if not isinstance(entry, dict) or not isinstance(entry.get("node_id"), str):
            raise StoryError("invalid history entry")
        node = self.nodes.get(entry["node_id"], {})
        if set(entry) == {"node_id", "item_id"}:
            for item in node.get("items", []):
                if item["id"] == entry["item_id"] and item["type"] in {"dialogue", "narration"}:
                    return item
        if set(entry) == {"node_id", "choice_id"}:
            for choice in node.get("choices", []):
                if choice["id"] == entry["choice_id"]:
                    return choice
        raise StoryError("save history references missing text")

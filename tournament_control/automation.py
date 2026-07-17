from __future__ import annotations

import json
import threading
from pathlib import Path

from .events import AppEvent, EventBus


DEFAULT_SCOREBOARD_STATE = {
    "version": 1,
    "activeTheme": "default",
    "mode": "automatic",
    "lastEvent": None,
}


class ScoreboardStateStore:
    """Presentation state exposed to current and future scoreboards."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def read(self) -> dict:
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as handle:
                    raw = json.load(handle)
                return {**DEFAULT_SCOREBOARD_STATE, **raw}
            except (OSError, json.JSONDecodeError, TypeError):
                return dict(DEFAULT_SCOREBOARD_STATE)

    def write(self, state: dict) -> None:
        payload = {**DEFAULT_SCOREBOARD_STATE, **state}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")


class AutomationEngine:
    """Maps app events to presentation state using an external rule file.

    The initial rules file is empty, so this engine does not alter the current
    scoreboard. A future UI can edit the schema without coupling Auto Monitor
    to HTML/CSS implementation details.
    """

    def __init__(
        self,
        event_bus: EventBus,
        rules_path: Path,
        state_store: ScoreboardStateStore,
    ) -> None:
        self.rules_path = Path(rules_path)
        self.state_store = state_store
        self._unsubscribe = event_bus.subscribe(self.handle)

    def close(self) -> None:
        self._unsubscribe()

    def _rules(self) -> list[dict]:
        try:
            with self.rules_path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
            rules = raw.get("rules", [])
            return rules if isinstance(rules, list) else []
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def handle(self, event: AppEvent) -> None:
        state = self.state_store.read()
        state["lastEvent"] = {
            "name": event.name,
            "source": event.source,
            "payload": event.payload,
            "occurredAt": event.occurred_at,
        }
        if state.get("mode") == "automatic":
            for rule in self._rules():
                if not rule.get("enabled", True) or rule.get("event") != event.name:
                    continue
                required = rule.get("payload", {})
                if not isinstance(required, dict) or any(
                    event.payload.get(key) != value for key, value in required.items()
                ):
                    continue
                action = rule.get("action", {})
                if action.get("type") == "set_theme" and action.get("theme"):
                    state["activeTheme"] = str(action["theme"])
        self.state_store.write(state)

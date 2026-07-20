from __future__ import annotations

import json
import threading
from pathlib import Path

from .events import AppEvent, EventBus
from .models import AppSettings


DEFAULT_SCOREBOARD_STATE = {
    "version": 2,
    "activeTheme": "legacy",
    "design": "legacy",
    "playerCamera": False,
    "scene": "in_game",
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

    def configure(self, design: str, player_camera: bool) -> dict:
        state = self.read()
        previous = (state.get("design"), bool(state.get("playerCamera")))
        state["design"] = "aya" if design == "aya" else "legacy"
        state["playerCamera"] = bool(player_camera)
        if state["design"] == "aya" and state["playerCamera"] and previous != ("aya", True):
            state["scene"] = "between_game"
        elif state["design"] != "aya" or not state["playerCamera"]:
            state["scene"] = "in_game"
        self._sync_theme(state)
        self.write(state)
        return state

    def set_presentation(self, *, scene: str | None = None, mode: str | None = None) -> dict:
        state = self.read()
        if scene in {"in_game", "between_game"}:
            state["scene"] = scene
        if mode in {"automatic", "manual"}:
            state["mode"] = mode
        self._sync_theme(state)
        self.write(state)
        return state

    @staticmethod
    def _sync_theme(state: dict) -> None:
        if state.get("design") != "aya":
            state["activeTheme"] = "legacy"
        elif not state.get("playerCamera"):
            state["activeTheme"] = "aya-in-game-no-camera"
        elif state.get("scene") == "between_game":
            state["activeTheme"] = "aya-between-game"
        else:
            state["activeTheme"] = "aya-in-game-with-camera"


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
        settings: AppSettings | None = None,
    ) -> None:
        self.rules_path = Path(rules_path)
        self.state_store = state_store
        self.settings = settings or AppSettings()
        self._timer_lock = threading.Lock()
        self._pending_timer: threading.Timer | None = None
        self._transition_generation = 0
        self._unsubscribe = event_bus.subscribe(self.handle)

    def close(self) -> None:
        self._unsubscribe()
        self._cancel_pending_transition()

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self._cancel_pending_transition()

    def cancel_pending_transition(self) -> None:
        self._cancel_pending_transition()

    def _cancel_pending_transition(self) -> None:
        with self._timer_lock:
            self._transition_generation += 1
            if self._pending_timer:
                self._pending_timer.cancel()
                self._pending_timer = None

    def _schedule_presentation(self, scene: str, delay: float) -> None:
        delay = max(0.0, float(delay))
        with self._timer_lock:
            self._transition_generation += 1
            generation = self._transition_generation
            if self._pending_timer:
                self._pending_timer.cancel()
                self._pending_timer = None
            timer = threading.Timer(delay, self._apply_scheduled_presentation, args=(scene, generation))
            timer.name = f"scoreboard-framework-{scene}"
            timer.daemon = True
            self._pending_timer = timer
            timer.start()

    def _apply_scheduled_presentation(self, scene: str, generation: int) -> None:
        with self._timer_lock:
            if generation != self._transition_generation:
                return
            self._pending_timer = None
        state = self.state_store.read()
        if (
            state.get("mode") == "automatic"
            and state.get("design") == "aya"
            and state.get("playerCamera")
        ):
            self.state_store.set_presentation(scene=scene)

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
        delayed_presentation: tuple[str, float] | None = None
        state["lastEvent"] = {
            "name": event.name,
            "source": event.source,
            "payload": event.payload,
            "occurredAt": event.occurred_at,
        }
        if state.get("mode") == "automatic":
            presentation_changed = False
            if state.get("design") == "aya" and state.get("playerCamera"):
                if event.name == "screen.vs":
                    delay = max(0.0, float(self.settings.transition_in_game_framework_delay))
                    if delay > 0:
                        delayed_presentation = ("in_game", delay)
                    else:
                        self._cancel_pending_transition()
                        state["scene"] = "in_game"
                        presentation_changed = True
                elif event.name == "screen.game_set":
                    delay = max(
                        0.0, float(self.settings.transition_between_game_framework_delay)
                    )
                    if delay > 0:
                        delayed_presentation = ("between_game", delay)
                    else:
                        self._cancel_pending_transition()
                        state["scene"] = "between_game"
                        presentation_changed = True
            if presentation_changed:
                self.state_store._sync_theme(state)
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
        if delayed_presentation:
            self._schedule_presentation(*delayed_presentation)

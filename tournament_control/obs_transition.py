from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .events import AppEvent, EventBus
from .models import AppSettings


class TransitionStateStore:
    """Thread-safe state and acknowledgement channel for transition.html."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._revision = 0
        self._transition_id = 0
        self._active = False
        self._label = ""
        self._acknowledged_id = 0
        self._acknowledged_at = 0.0

    def read(self) -> dict:
        with self._condition:
            return self._snapshot()

    def wait_for_change(self, after_revision: int, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._revision <= after_revision:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._snapshot()

    def start(self, label: str) -> int:
        with self._condition:
            self._transition_id += 1
            self._revision += 1
            self._active = True
            self._label = label
            self._acknowledged_id = 0
            self._acknowledged_at = 0.0
            self._condition.notify_all()
            return self._transition_id

    def finish(self, transition_id: int) -> None:
        with self._condition:
            if transition_id != self._transition_id or not self._active:
                return
            self._revision += 1
            self._active = False
            self._condition.notify_all()

    def acknowledge(self, transition_id: int) -> bool:
        with self._condition:
            if transition_id != self._transition_id or not self._active:
                return False
            if self._acknowledged_id != transition_id:
                self._acknowledged_id = transition_id
                self._acknowledged_at = time.monotonic()
                self._condition.notify_all()
            return True

    def wait_for_acknowledgement(
        self,
        transition_id: int,
        timeout: float,
        cancelled: Callable[[], bool],
    ) -> float | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._acknowledged_id != transition_id:
                if cancelled() or transition_id != self._transition_id:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)
            return self._acknowledged_at

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _snapshot(self) -> dict:
        return {
            "revision": self._revision,
            "transitionId": self._transition_id,
            "active": self._active,
            "label": self._label,
        }


class BrowserTransitionController:
    """Gate detector events behind the dedicated transition Browser Source."""

    ACK_TIMEOUT_SECONDS = 1.5

    def __init__(
        self,
        input_bus: EventBus,
        output_bus: EventBus,
        settings: AppSettings,
        presentation_state_store,
        transition_state_store: TransitionStateStore,
    ) -> None:
        self.output_bus = output_bus
        self.settings = settings
        self.presentation_state_store = presentation_state_store
        self.transition_state_store = transition_state_store
        self._condition = threading.Condition()
        self._pending: tuple[AppEvent | None, AppSettings, int] | None = None
        self._generation = 0
        self._closed = False
        self._log: Callable[[str], None] = lambda _line: None
        self._unsubscribe = input_bus.subscribe(self.handle)
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="browser-transition-worker",
            daemon=True,
        )
        self._worker.start()

    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        self._log = callback

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self._cancel_pending()

    def close(self) -> None:
        self._unsubscribe()
        with self._condition:
            self._closed = True
            self._generation += 1
            self._pending = None
            self._condition.notify_all()
        self.transition_state_store.wake()
        self._worker.join(timeout=2.0)

    def handle(self, event: AppEvent) -> None:
        if event.name not in {"screen.vs", "screen.game_set"}:
            self.output_bus.publish(event)
            return
        settings = self.settings
        if (
            self.presentation_state_store.read().get("mode") != "automatic"
            or not self._enabled_for_event(settings, event.name)
        ):
            self.output_bus.publish(event)
            return
        self._submit(event, settings)

    def preview_async(self, settings: AppSettings | None = None) -> None:
        self._submit(None, settings or self.settings)

    def _cancel_pending(self) -> None:
        with self._condition:
            self._generation += 1
            self._pending = None
            self._condition.notify_all()
        self.transition_state_store.wake()

    def _submit(self, event: AppEvent | None, settings: AppSettings) -> None:
        with self._condition:
            if self._closed:
                return
            self._generation += 1
            generation = self._generation
            self._pending = (event, settings, generation)
            self._condition.notify_all()
        self.transition_state_store.wake()

    @staticmethod
    def _enabled_for_event(settings: AppSettings, event_name: str) -> bool:
        if not settings.obs_transition_enabled:
            return False
        if event_name == "screen.vs":
            return bool(settings.obs_transition_vs_enabled)
        return bool(settings.obs_transition_game_set_enabled)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._closed and self._pending is None:
                    self._condition.wait()
                if self._closed:
                    return
                request = self._pending
                self._pending = None
            if request is not None:
                self._run_transition(*request)

    def _run_transition(
        self,
        event: AppEvent | None,
        settings: AppSettings,
        generation: int,
    ) -> None:
        label = "テスト" if event is None else ("VS" if event.name == "screen.vs" else "GAME SET")
        transition_id = self.transition_state_store.start(label)
        self._log(f"{label}トランジションの再生をBrowser Sourceへ要求しました。")
        try:
            acknowledged_at = self.transition_state_store.wait_for_acknowledgement(
                transition_id,
                self.ACK_TIMEOUT_SECONDS,
                lambda: self._cancelled(generation),
            )
            if acknowledged_at is None:
                if not self._cancelled(generation):
                    self._log(
                        "Transition Browser Sourceから応答がないため、通常の切り替えを続行します。"
                    )
                    if event is not None:
                        self.output_bus.publish(event)
                return

            self._log(f"{label}トランジションの再生開始を確認しました。")
            switch_at = acknowledged_at + max(
                0.0, float(settings.obs_transition_switch_delay)
            )
            finish_at = acknowledged_at + max(
                float(settings.obs_transition_duration),
                float(settings.obs_transition_switch_delay),
                0.1,
            )
            if not self._wait_until(switch_at, generation):
                return
            if event is not None:
                payload = dict(event.payload)
                payload["_transition_ready"] = True
                self.output_bus.publish(
                    AppEvent(event.name, event.source, payload, event.occurred_at)
                )
                self._log(f"{label}トランジション内で配置・デザインを切り替えます。")
            self._wait_until(finish_at, generation)
        finally:
            self.transition_state_store.finish(transition_id)

    def _cancelled(self, generation: int) -> bool:
        with self._condition:
            return self._closed or generation != self._generation

    def _wait_until(self, due_at: float, generation: int) -> bool:
        with self._condition:
            while True:
                if self._closed or generation != self._generation:
                    return False
                remaining = due_at - time.monotonic()
                if remaining <= 0:
                    return True
                self._condition.wait(timeout=remaining)

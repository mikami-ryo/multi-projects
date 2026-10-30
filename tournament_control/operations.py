from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from .models import AppSettings, Player
from .obs_layout import (
    apply_scene_layout,
    capture_scene_layout,
    layout_sources,
    obs_connection,
)
from .startgg import EventMatches, StartGGClient


class LatestTaskRunner:
    """Run one task at a time and retain only the newest pending request.

    UI pages can submit network or OBS work without creating an unbounded
    number of threads. Callbacks are expected to be Qt signal emitters, which
    are safe to invoke from this daemon worker.
    """

    def __init__(self, name: str) -> None:
        self._condition = threading.Condition()
        self._pending: tuple[Callable[[], object], Callable[[object], None], Callable[[str], None]] | None = None
        self._closed = False
        self._worker = threading.Thread(target=self._run, name=name, daemon=True)
        self._worker.start()

    def submit(
        self,
        operation: Callable[[], object],
        on_success: Callable[[object], None],
        on_failure: Callable[[str], None],
    ) -> None:
        with self._condition:
            if self._closed:
                return
            self._pending = (operation, on_success, on_failure)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._closed and self._pending is None:
                    self._condition.wait()
                if self._closed:
                    return
                task = self._pending
                self._pending = None
            if task is None:
                continue
            operation, on_success, on_failure = task
            try:
                result = operation()
            except Exception as exc:
                with self._condition:
                    closed = self._closed
                if not closed:
                    on_failure(str(exc))
            else:
                with self._condition:
                    closed = self._closed
                if not closed:
                    on_success(result)


class StartGGOperations:
    """Application operations used by the start.gg page."""

    @staticmethod
    def load_matches(
        token: str,
        event_slug: str,
        phase_group_id: str | None,
    ) -> EventMatches:
        return StartGGClient(token).event_matches(event_slug, phase_group_id)

    @staticmethod
    def load_players(token: str, set_id: str) -> list[Player]:
        return StartGGClient(token).set_players(set_id)


@dataclass(frozen=True)
class LayoutApplyResult:
    scene: str
    count: int


class ObsLayoutOperations:
    """OBS capture/apply operations independent from widget presentation."""

    @staticmethod
    def capture(settings: AppSettings) -> dict[str, dict]:
        return capture_scene_layout(
            obs_connection(settings),
            settings.obs_layout_scene_name,
            layout_sources(settings),
        )

    @staticmethod
    def apply(
        settings: AppSettings,
        scene: str,
        profile: dict[str, dict],
    ) -> LayoutApplyResult:
        count = apply_scene_layout(
            obs_connection(settings),
            settings.obs_layout_scene_name,
            layout_sources(settings),
            profile,
        )
        return LayoutApplyResult(scene=scene, count=count)

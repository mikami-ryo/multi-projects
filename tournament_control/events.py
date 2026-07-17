from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True, slots=True)
class AppEvent:
    """Stable boundary between detectors and future presentation actions."""

    name: str
    source: str
    payload: dict = field(default_factory=dict)
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


EventHandler = Callable[[AppEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._lock = threading.RLock()

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return unsubscribe

    def publish(self, event: AppEvent) -> None:
        with self._lock:
            handlers = tuple(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # Optional extensions must never interrupt tournament control.
                continue

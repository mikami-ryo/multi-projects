from __future__ import annotations

import threading
from collections.abc import Callable

from .events import AppEvent, EventBus
from .models import AppSettings


SOURCE_FIELDS = {
    "game": "obs_game_source_name",
    "player1": "obs_player1_source_name",
    "player2": "obs_player2_source_name",
}

WRITABLE_TRANSFORM_FIELDS = {
    "alignment",
    "boundsAlignment",
    "boundsHeight",
    "boundsType",
    "boundsWidth",
    "cropBottom",
    "cropLeft",
    "cropRight",
    "cropTop",
    "positionX",
    "positionY",
    "rotation",
    "scaleX",
    "scaleY",
}

SCENE_ITEM_ID_FIELD = "_sceneItemId"
SCENE_ITEM_INDEX_FIELD = "_sceneItemIndex"
SOURCE_NAME_FIELD = "_sourceName"
SOURCE_OCCURRENCE_FIELD = "_sourceOccurrence"


def obs_connection(settings: AppSettings) -> dict:
    return {
        "host": settings.obs_host,
        "port": settings.obs_port,
        "password": settings.obs_password,
    }


def layout_sources(settings: AppSettings) -> dict[str, str]:
    return {
        key: str(getattr(settings, field_name, "")).strip()
        for key, field_name in SOURCE_FIELDS.items()
        if str(getattr(settings, field_name, "")).strip()
    }


def _client(connection: dict):
    import obsws_python as obs

    return obs.ReqClient(
        host=str(connection.get("host", "localhost")),
        port=int(connection.get("port", 4455)),
        password=str(connection.get("password", "")),
        timeout=3.0,
    )


def sanitize_transform(transform: dict) -> dict:
    sanitized = {
        key: value
        for key, value in transform.items()
        if key in WRITABLE_TRANSFORM_FIELDS and isinstance(value, (str, int, float, bool))
    }
    # OBS commonly reports 0 for bounds dimensions when bounds are disabled,
    # but SetSceneItemTransform rejects values below 1 even in that mode.
    # Omitting invalid dimensions preserves OBS's current harmless value while
    # still applying the saved bounds type and all other transform properties.
    for field in ("boundsWidth", "boundsHeight"):
        value = sanitized.get(field)
        if isinstance(value, (int, float)) and value < 1:
            sanitized.pop(field)
    return sanitized


def _item_value(item: object, *names: str):
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return None


def _scene_items(client: object, scene_name: str) -> list[object]:
    """Return items for either a regular OBS scene or an OBS group."""
    try:
        response = client.get_scene_item_list(scene_name)
    except Exception as scene_error:
        get_group_items = getattr(client, "get_group_scene_item_list", None)
        if not get_group_items:
            raise scene_error
        response = get_group_items(scene_name)
    items = getattr(response, "scene_items", None)
    if items is None and isinstance(response, dict):
        items = response.get("sceneItems", response.get("scene_items"))
    return list(items or [])


def _scene_item_records(client: object, scene_name: str) -> list[dict]:
    records = []
    for item in _scene_items(client, scene_name):
        item_id = _item_value(item, "sceneItemId", "scene_item_id")
        source_name = _item_value(item, "sourceName", "source_name")
        item_index = _item_value(item, "sceneItemIndex", "scene_item_index")
        if item_id is None or source_name is None:
            continue
        records.append(
            {
                "sceneItemId": int(item_id),
                "sourceName": str(source_name),
                "sceneItemIndex": int(item_index) if item_index is not None else None,
            }
        )
    return records


def _matching_records(records: list[dict], source_name: str) -> list[dict]:
    matches = [item for item in records if item["sourceName"] == source_name]
    return sorted(
        matches,
        key=lambda item: (
            item["sceneItemIndex"] is None,
            item["sceneItemIndex"] if item["sceneItemIndex"] is not None else 0,
            item["sceneItemId"],
        ),
    )


def _source_occurrences(sources: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    occurrences = {}
    for key, source_name in sources.items():
        occurrence = counts.get(source_name, 0)
        occurrences[key] = occurrence
        counts[source_name] = occurrence + 1
    return occurrences


def _resolve_item_id(
    records: list[dict], source_name: str, transform: dict, occurrence: int
) -> int | None:
    saved_id = transform.get(SCENE_ITEM_ID_FIELD)
    if isinstance(saved_id, int):
        for item in records:
            if item["sceneItemId"] == saved_id and item["sourceName"] == source_name:
                return saved_id

    saved_occurrence = transform.get(SOURCE_OCCURRENCE_FIELD, occurrence)
    if not isinstance(saved_occurrence, int) or saved_occurrence < 0:
        saved_occurrence = occurrence
    matches = _matching_records(records, source_name)
    if saved_occurrence < len(matches):
        return matches[saved_occurrence]["sceneItemId"]
    return None


def capture_scene_layout(
    connection: dict,
    scene_name: str,
    sources: dict[str, str],
    *,
    client_factory: Callable[[dict], object] = _client,
) -> dict[str, dict]:
    scene_name = scene_name.strip()
    if not scene_name:
        raise ValueError("対象シーンまたはグループ名を入力してください。")
    if not sources:
        raise ValueError("少なくとも1つの対象ソース名を入力してください。")
    client = client_factory(connection)
    try:
        layout = {}
        records = _scene_item_records(client, scene_name)
        occurrences = _source_occurrences(sources)
        for key, source_name in sources.items():
            occurrence = occurrences[key]
            matches = _matching_records(records, source_name)
            if occurrence >= len(matches):
                raise ValueError(
                    f"OBS上のソース「{source_name}」は{occurrence + 1}個必要ですが、"
                    f"{len(matches)}個しかありません。"
                )
            item = matches[occurrence]
            item_id = item["sceneItemId"]
            response = client.get_scene_item_transform(scene_name, item_id)
            saved = sanitize_transform(dict(response.scene_item_transform))
            saved[SCENE_ITEM_ID_FIELD] = item_id
            saved[SCENE_ITEM_INDEX_FIELD] = item["sceneItemIndex"]
            saved[SOURCE_NAME_FIELD] = source_name
            saved[SOURCE_OCCURRENCE_FIELD] = occurrence
            layout[key] = saved
        return layout
    finally:
        disconnect = getattr(client, "disconnect", None)
        if disconnect:
            disconnect()


def apply_scene_layout(
    connection: dict,
    scene_name: str,
    sources: dict[str, str],
    layout: dict[str, dict],
    *,
    client_factory: Callable[[dict], object] = _client,
) -> int:
    scene_name = scene_name.strip()
    if not scene_name:
        raise ValueError("対象シーンまたはグループ名を入力してください。")
    if not layout:
        raise ValueError("このレイアウトはまだ保存されていません。")
    client = client_factory(connection)
    applied = 0
    try:
        records = _scene_item_records(client, scene_name)
        occurrences = _source_occurrences(sources)
        restored_items = []
        for key, transform in layout.items():
            source_name = sources.get(key, "").strip()
            if not source_name or not isinstance(transform, dict):
                continue
            item_id = _resolve_item_id(
                records,
                source_name,
                transform,
                occurrences.get(key, 0),
            )
            if item_id is None:
                continue
            client.set_scene_item_transform(
                scene_name,
                item_id,
                sanitize_transform(transform),
            )
            applied += 1
            item_index = transform.get(SCENE_ITEM_INDEX_FIELD)
            if isinstance(item_index, int) and item_index >= 0:
                restored_items.append((item_index, item_id))

        # Moving one item changes the indices of neighboring items. Restoring
        # from the bottom upwards produces the saved ordering deterministically.
        max_index = max(0, len(records) - 1)
        for item_index, item_id in sorted(restored_items):
            client.set_scene_item_index(
                scene_name,
                item_id,
                min(item_index, max_index),
            )
    finally:
        disconnect = getattr(client, "disconnect", None)
        if disconnect:
            disconnect()
    if applied == 0:
        raise ValueError("適用できるソース配置がありません。")
    return applied


class ObsLayoutController:
    """Applies saved OBS transforms in response to presentation events."""

    def __init__(self, event_bus: EventBus, settings: AppSettings, state_store) -> None:
        self.settings = settings
        self.state_store = state_store
        self._lock = threading.Lock()
        self._timer_lock = threading.Lock()
        self._pending_timer: threading.Timer | None = None
        self._transition_generation = 0
        self._log: Callable[[str], None] = lambda _line: None
        self._unsubscribe = event_bus.subscribe(self.handle)

    def close(self) -> None:
        self._unsubscribe()
        self._cancel_pending_transition()

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self._cancel_pending_transition()

    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        self._log = callback

    def handle(self, event: AppEvent) -> None:
        if self.state_store.read().get("mode") != "automatic":
            return
        if event.name == "screen.vs":
            self._schedule_apply("in_game", self.settings.transition_in_game_layout_delay)
        elif event.name == "screen.game_set":
            self._schedule_apply(
                "between_game", self.settings.transition_between_game_layout_delay
            )

    def _cancel_pending_transition(self) -> None:
        with self._timer_lock:
            self._transition_generation += 1
            if self._pending_timer:
                self._pending_timer.cancel()
                self._pending_timer = None

    def _schedule_apply(self, scene: str, delay: float) -> None:
        settings = self.settings
        if not self._enabled(settings):
            return
        delay = max(0.0, float(delay))
        with self._timer_lock:
            self._transition_generation += 1
            generation = self._transition_generation
            if self._pending_timer:
                self._pending_timer.cancel()
                self._pending_timer = None
            if delay <= 0:
                self._start_apply_thread(scene, settings)
                return
            self._log(f"OBS {self._scene_label(scene)}配置を{delay:g}秒後に切り替えます。")
            timer = threading.Timer(delay, self._run_scheduled_apply, args=(scene, settings, generation))
            timer.name = f"obs-layout-delay-{scene}"
            timer.daemon = True
            self._pending_timer = timer
            timer.start()

    def _run_scheduled_apply(
        self, scene: str, settings: AppSettings, generation: int
    ) -> None:
        with self._timer_lock:
            if generation != self._transition_generation:
                return
            self._pending_timer = None
        state = self.state_store.read()
        if state.get("mode") == "automatic" and self._enabled(settings):
            self._start_apply_thread(scene, settings)

    @staticmethod
    def _enabled(settings: AppSettings) -> bool:
        return bool(
            settings.obs_layout_enabled
            and settings.scoreboard_design == "aya"
            and settings.player_camera_enabled
        )

    @staticmethod
    def _scene_label(scene: str) -> str:
        return "試合間" if scene == "between_game" else "試合中"

    def _start_apply_thread(self, scene: str, settings: AppSettings) -> None:
        thread = threading.Thread(
            target=self._apply,
            args=(scene, settings),
            name=f"obs-layout-{scene}",
            daemon=True,
        )
        thread.start()

    def apply_async(self, scene: str) -> None:
        """Apply immediately for the manual fallback controls."""
        settings = self.settings
        if not self._enabled(settings):
            return
        self._cancel_pending_transition()
        self._start_apply_thread(scene, settings)

    def _apply(self, scene: str, settings: AppSettings) -> None:
        layout = (
            settings.obs_between_game_layout
            if scene == "between_game"
            else settings.obs_in_game_layout
        )
        label = self._scene_label(scene)
        if not layout:
            self._log(f"OBS {label}レイアウトは未保存のため、位置を変更しませんでした。")
            return
        with self._lock:
            try:
                count = apply_scene_layout(
                    obs_connection(settings),
                    settings.obs_layout_scene_name,
                    layout_sources(settings),
                    layout,
                )
                self._log(f"OBSを{label}レイアウトへ切り替えました（{count}ソース）。")
            except Exception as exc:
                self._log(f"OBSレイアウト切り替え失敗: {exc}")

from __future__ import annotations

import json
import threading
import time
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
SOURCE_WIDTH_FIELD = "_sourceWidth"
SOURCE_HEIGHT_FIELD = "_sourceHeight"
CANVAS_WIDTH_FIELD = "_canvasWidth"
CANVAS_HEIGHT_FIELD = "_canvasHeight"
SCENE_ITEM_ENABLED_FIELD = "_sceneItemEnabled"


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
        item_enabled = _item_value(item, "sceneItemEnabled", "scene_item_enabled")
        if item_id is None or source_name is None:
            continue
        records.append(
            {
                "sceneItemId": int(item_id),
                "sourceName": str(source_name),
                "sceneItemIndex": int(item_index) if item_index is not None else None,
                "sceneItemEnabled": bool(item_enabled) if item_enabled is not None else True,
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


def _video_canvas_size(client: object) -> tuple[float, float]:
    """Return OBS base canvas size, with the app's 1280x720 design as fallback."""
    getter = getattr(client, "get_video_settings", None)
    if getter:
        try:
            response = getter()
            width = _item_value(response, "baseWidth", "base_width")
            height = _item_value(response, "baseHeight", "base_height")
            if float(width) > 0 and float(height) > 0:
                return float(width), float(height)
        except (AttributeError, TypeError, ValueError):
            pass
        except Exception:
            # Layout capture should still work with older OBS/websocket versions.
            pass
    return 1280.0, 720.0


def _positive_number(value: object, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def layout_preview_entries(layout: dict[str, dict]) -> list[dict]:
    """Convert a saved OBS profile into canvas-normalized preview rectangles.

    Preview metadata is intentionally stored with underscore-prefixed keys, so
    older profiles remain valid and OBS never receives these values when the
    writable transform is restored.
    """
    entries = []
    for key, transform in layout.items():
        if not isinstance(transform, dict):
            continue
        canvas_width = _positive_number(transform.get(CANVAS_WIDTH_FIELD), 1280.0)
        canvas_height = _positive_number(transform.get(CANVAS_HEIGHT_FIELD), 720.0)
        scale_x = abs(_positive_number(transform.get("scaleX"), 1.0))
        scale_y = abs(_positive_number(transform.get("scaleY"), 1.0))
        source_width = _positive_number(
            transform.get(SOURCE_WIDTH_FIELD),
            canvas_width if key == "game" else canvas_width * 0.25,
        )
        source_height = _positive_number(
            transform.get(SOURCE_HEIGHT_FIELD),
            canvas_height if key == "game" else canvas_height * 0.25,
        )
        cropped_width = max(
            1.0,
            source_width
            - max(0.0, float(transform.get("cropLeft", 0) or 0))
            - max(0.0, float(transform.get("cropRight", 0) or 0)),
        )
        cropped_height = max(
            1.0,
            source_height
            - max(0.0, float(transform.get("cropTop", 0) or 0))
            - max(0.0, float(transform.get("cropBottom", 0) or 0)),
        )
        bounds_type = str(transform.get("boundsType", "OBS_BOUNDS_NONE"))
        if bounds_type != "OBS_BOUNDS_NONE":
            width = _positive_number(transform.get("boundsWidth"), cropped_width * scale_x)
            height = _positive_number(transform.get("boundsHeight"), cropped_height * scale_y)
        else:
            width = cropped_width * scale_x
            height = cropped_height * scale_y

        x = float(transform.get("positionX", 0) or 0)
        y = float(transform.get("positionY", 0) or 0)
        alignment = int(transform.get("alignment", 5) or 0)
        if alignment & 2:  # OBS_ALIGN_RIGHT
            x -= width
        elif not alignment & 1:  # centered horizontally
            x -= width / 2
        if alignment & 8:  # OBS_ALIGN_BOTTOM
            y -= height
        elif not alignment & 4:  # centered vertically
            y -= height / 2

        entries.append(
            {
                "key": key,
                "source": str(transform.get(SOURCE_NAME_FIELD, key)),
                "x": x / canvas_width,
                "y": y / canvas_height,
                "width": width / canvas_width,
                "height": height / canvas_height,
                "layer": int(transform.get(SCENE_ITEM_INDEX_FIELD, 0) or 0),
                "enabled": bool(transform.get(SCENE_ITEM_ENABLED_FIELD, True)),
            }
        )
    # OBS index 0 is visually uppermost; paint it last so overlaps match OBS.
    return sorted(entries, key=lambda entry: entry["layer"], reverse=True)


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
        canvas_width, canvas_height = _video_canvas_size(client)
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
            raw_transform = dict(response.scene_item_transform)
            saved = sanitize_transform(raw_transform)
            saved[SCENE_ITEM_ID_FIELD] = item_id
            saved[SCENE_ITEM_INDEX_FIELD] = item["sceneItemIndex"]
            saved[SOURCE_NAME_FIELD] = source_name
            saved[SOURCE_OCCURRENCE_FIELD] = occurrence
            saved[SOURCE_WIDTH_FIELD] = _positive_number(
                raw_transform.get("sourceWidth"), canvas_width
            )
            saved[SOURCE_HEIGHT_FIELD] = _positive_number(
                raw_transform.get("sourceHeight"), canvas_height
            )
            saved[CANVAS_WIDTH_FIELD] = canvas_width
            saved[CANVAS_HEIGHT_FIELD] = canvas_height
            saved[SCENE_ITEM_ENABLED_FIELD] = item["sceneItemEnabled"]
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
    client: object | None = None,
) -> int:
    scene_name = scene_name.strip()
    if not scene_name:
        raise ValueError("対象シーンまたはグループ名を入力してください。")
    if not layout:
        raise ValueError("このレイアウトはまだ保存されていません。")
    owns_client = client is None
    client = client or client_factory(connection)
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
        if owns_client:
            disconnect = getattr(client, "disconnect", None)
            if disconnect:
                disconnect()
    if applied == 0:
        raise ValueError("適用できるソース配置がありません。")
    return applied


class ObsLayoutController:
    """Applies the latest requested OBS layout without accumulating stale work."""

    def __init__(self, event_bus: EventBus, settings: AppSettings, state_store) -> None:
        self.settings = settings
        self.state_store = state_store
        self._condition = threading.Condition()
        self._pending_request: tuple[str, AppSettings, float, int, bool, bool] | None = None
        self._transition_generation = 0
        self._closed = False
        self._obs_client = None
        self._last_applied_signature: tuple | None = None
        self._log: Callable[[str], None] = lambda _line: None
        self._unsubscribe = event_bus.subscribe(self.handle)
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="obs-layout-worker",
            daemon=True,
        )
        self._worker.start()

    def close(self) -> None:
        self._unsubscribe()
        with self._condition:
            self._closed = True
            self._pending_request = None
            self._transition_generation += 1
            self._condition.notify_all()
        self._worker.join(timeout=2.0)
        if not self._worker.is_alive():
            self._disconnect_client()

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self._cancel_pending_transition()
        self._last_applied_signature = None

    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        self._log = callback

    def handle(self, event: AppEvent) -> None:
        if self.state_store.read().get("mode") != "automatic":
            return
        if event.name == "screen.vs":
            delay = 0.0 if event.payload.get("_transition_ready") else self.settings.transition_in_game_layout_delay
            self._schedule_apply(
                "in_game",
                delay,
                transition_active=bool(event.payload.get("_transition_ready")),
            )
        elif event.name == "screen.game_set":
            delay = 0.0 if event.payload.get("_transition_ready") else self.settings.transition_between_game_layout_delay
            self._schedule_apply(
                "between_game",
                delay,
                transition_active=bool(event.payload.get("_transition_ready")),
            )

    def _cancel_pending_transition(self) -> None:
        with self._condition:
            self._transition_generation += 1
            self._pending_request = None
            self._condition.notify_all()

    def _schedule_apply(
        self,
        scene: str,
        delay: float,
        *,
        automatic: bool = True,
        transition_active: bool = False,
    ) -> None:
        settings = self.settings
        if not self._enabled(settings):
            return
        delay = max(0.0, float(delay))
        with self._condition:
            self._transition_generation += 1
            generation = self._transition_generation
            self._pending_request = (
                scene,
                settings,
                time.monotonic() + delay,
                generation,
                automatic,
                transition_active,
            )
            self._condition.notify_all()
        if delay > 0:
            self._log(f"OBS {self._scene_label(scene)}配置を{delay:g}秒後に切り替えます。")

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

    def apply_async(self, scene: str) -> None:
        """Apply immediately for the manual fallback controls."""
        self._schedule_apply(scene, 0.0, automatic=False)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._closed and self._pending_request is None:
                    self._condition.wait()
                if self._closed:
                    return
                request = self._pending_request
                if request is None:
                    continue
                scene, settings, due_at, generation, automatic, transition_active = request
                remaining = due_at - time.monotonic()
                if remaining > 0:
                    self._condition.wait(timeout=remaining)
                    continue
                self._pending_request = None

            if automatic and self.state_store.read().get("mode") != "automatic":
                continue
            if not self._enabled(settings):
                continue
            signature = self._layout_signature(scene, settings)
            if automatic and signature == self._last_applied_signature:
                self._log(
                    f"OBS {self._scene_label(scene)}配置は適用済みのため切り替えを省略しました。"
                )
                continue

            started = time.monotonic()
            self._log(
                f"OBS {self._scene_label(scene)}配置の切り替えを開始します。"
                f" (request={generation})"
            )
            try:
                count = self._apply(scene, settings)
                if transition_active:
                    self._keep_transition_on_top(settings)
                self._last_applied_signature = signature
                elapsed = time.monotonic() - started
                self._log(
                    f"OBS {self._scene_label(scene)}配置へ切り替えました"
                    f"（{count}ソース / {elapsed:.2f}秒 / request={generation}）。"
                )
            except Exception as exc:
                elapsed = time.monotonic() - started
                self._log(
                    f"OBSレイアウト切り替え失敗（{elapsed:.2f}秒 / "
                    f"request={generation}）: {exc}"
                )

    def _keep_transition_on_top(self, settings: AppSettings) -> None:
        if self._obs_client is None:
            return
        source_name = str(settings.obs_transition_source_name).strip()
        scene_name = str(settings.obs_layout_scene_name).strip()
        if not source_name or not scene_name:
            return
        for item in _scene_items(self._obs_client, scene_name):
            name = _item_value(item, "sourceName", "source_name")
            item_id = _item_value(item, "sceneItemId", "scene_item_id")
            if str(name) == source_name and item_id is not None:
                self._obs_client.set_scene_item_index(scene_name, int(item_id), 0)
                return

    @staticmethod
    def _layout_signature(scene: str, settings: AppSettings) -> tuple:
        layout = (
            settings.obs_between_game_layout
            if scene == "between_game"
            else settings.obs_in_game_layout
        )
        serialized = json.dumps(layout, ensure_ascii=False, sort_keys=True, default=str)
        sources = tuple(sorted(layout_sources(settings).items()))
        return scene, settings.obs_layout_scene_name, sources, serialized

    def _disconnect_client(self) -> None:
        client, self._obs_client = self._obs_client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def _apply(self, scene: str, settings: AppSettings) -> int:
        layout = (
            settings.obs_between_game_layout
            if scene == "between_game"
            else settings.obs_in_game_layout
        )
        label = self._scene_label(scene)
        if not layout:
            raise ValueError(f"OBS {label}レイアウトがまだ保存されていません。")
        last_error = None
        for attempt in range(2):
            try:
                if self._obs_client is None:
                    self._obs_client = _client(obs_connection(settings))
                return apply_scene_layout(
                    obs_connection(settings),
                    settings.obs_layout_scene_name,
                    layout_sources(settings),
                    layout,
                    client=self._obs_client,
                )
            except Exception as exc:
                last_error = exc
                self._disconnect_client()
                if attempt == 0:
                    self._log("OBS接続を再作成してレイアウト切り替えを再試行します。")
        raise last_error or RuntimeError("OBSレイアウト切り替えに失敗しました。")

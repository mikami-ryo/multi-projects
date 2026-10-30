import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tournament_control.automation import ScoreboardStateStore
from tournament_control.events import AppEvent, EventBus
from tournament_control.models import AppSettings
from tournament_control.obs_layout import (
    CANVAS_HEIGHT_FIELD,
    CANVAS_WIDTH_FIELD,
    SCENE_ITEM_ID_FIELD,
    SCENE_ITEM_INDEX_FIELD,
    SOURCE_HEIGHT_FIELD,
    SOURCE_OCCURRENCE_FIELD,
    SOURCE_WIDTH_FIELD,
    ObsLayoutController,
    apply_scene_layout,
    capture_scene_layout,
    layout_preview_entries,
    sanitize_transform,
)


class FakeObsClient:
    def __init__(self):
        self.ids = {"Game": 10, "P1": 11, "P2": 12}
        self.scene_items = [
            {"sourceName": "Game", "sceneItemId": 10, "sceneItemIndex": 0},
            {"sourceName": "P1", "sceneItemId": 11, "sceneItemIndex": 1},
            {"sourceName": "P2", "sceneItemId": 12, "sceneItemIndex": 2},
        ]
        self.applied = []
        self.applied_indices = []
        self.disconnected = False

    def get_scene_item_id(self, scene_name, source_name):
        self.last_scene = scene_name
        return SimpleNamespace(scene_item_id=self.ids[source_name])

    def get_scene_item_transform(self, scene_name, item_id):
        return SimpleNamespace(
            scene_item_transform={
                "positionX": float(item_id),
                "positionY": 20.0,
                "scaleX": 1.0,
                "scaleY": 1.0,
                "cropLeft": 0,
                "sourceWidth": 1920.0,
            }
        )

    def get_scene_item_list(self, scene_name):
        self.last_scene = scene_name
        return SimpleNamespace(scene_items=self.scene_items)

    def set_scene_item_transform(self, scene_name, item_id, transform):
        self.applied.append((scene_name, item_id, transform))

    def set_scene_item_index(self, scene_name, item_id, item_index):
        self.applied_indices.append((scene_name, item_id, item_index))

    def disconnect(self):
        self.disconnected = True


class ObsLayoutTests(unittest.TestCase):
    def test_automatic_layout_transition_uses_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ScoreboardStateStore(Path(directory) / "state.json")
            state.configure("aya", True)
            bus = EventBus()
            settings = AppSettings(
                scoreboard_design="aya",
                player_camera_enabled=True,
                obs_layout_enabled=True,
                transition_in_game_layout_delay=0.04,
            )
            controller = ObsLayoutController(bus, settings, state)
            applied = []
            completed = threading.Event()

            def record(scene, _settings):
                applied.append(scene)
                completed.set()

            controller._apply = record
            bus.publish(AppEvent("screen.vs", "auto_monitor"))
            self.assertEqual(applied, [])
            self.assertTrue(completed.wait(0.2))
            self.assertEqual(applied, ["in_game"])
            controller.close()

    def test_media_transition_ready_event_applies_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ScoreboardStateStore(Path(directory) / "state.json")
            state.configure("aya", True)
            bus = EventBus()
            settings = AppSettings(
                scoreboard_design="aya",
                player_camera_enabled=True,
                obs_layout_enabled=True,
                transition_in_game_layout_delay=30.0,
            )
            controller = ObsLayoutController(bus, settings, state)
            applied = []
            completed = threading.Event()

            def record(scene, _settings):
                applied.append(scene)
                completed.set()
                return 1

            controller._apply = record
            bus.publish(
                AppEvent("screen.vs", "auto_monitor", {"_transition_ready": True})
            )
            self.assertTrue(completed.wait(0.2))
            self.assertEqual(applied, ["in_game"])
            controller.close()

    def test_layout_restore_keeps_active_transition_source_on_top(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ScoreboardStateStore(Path(directory) / "state.json")
            bus = EventBus()
            settings = AppSettings(
                obs_layout_scene_name="Broadcast",
                obs_transition_source_name="Aya Transition",
            )
            controller = ObsLayoutController(bus, settings, state)
            client = FakeObsClient()
            client.scene_items.append(
                {"sourceName": "Aya Transition", "sceneItemId": 42, "sceneItemIndex": 4}
            )
            controller._obs_client = client
            controller._keep_transition_on_top(settings)
            self.assertIn(("Broadcast", 42, 0), client.applied_indices)
            controller.close()

    def test_new_layout_transition_cancels_older_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ScoreboardStateStore(Path(directory) / "state.json")
            state.configure("aya", True)
            bus = EventBus()
            settings = AppSettings(
                scoreboard_design="aya",
                player_camera_enabled=True,
                obs_layout_enabled=True,
                transition_in_game_layout_delay=0.08,
                transition_between_game_layout_delay=0.01,
            )
            controller = ObsLayoutController(bus, settings, state)
            applied = []
            controller._apply = lambda scene, _settings: applied.append(scene)

            bus.publish(AppEvent("screen.vs", "auto_monitor"))
            bus.publish(AppEvent("screen.game_set", "auto_monitor"))
            time.sleep(0.12)
            self.assertEqual(applied, ["between_game"])
            controller.close()

    def test_many_pending_transitions_collapse_to_latest_request(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ScoreboardStateStore(Path(directory) / "state.json")
            state.configure("aya", True)
            bus = EventBus()
            settings = AppSettings(
                scoreboard_design="aya",
                player_camera_enabled=True,
                obs_layout_enabled=True,
                transition_in_game_layout_delay=0.04,
                transition_between_game_layout_delay=0.04,
            )
            controller = ObsLayoutController(bus, settings, state)
            applied = []

            def record(scene, _settings):
                applied.append(scene)
                return 1

            controller._apply = record
            for _index in range(50):
                bus.publish(AppEvent("screen.vs", "auto_monitor"))
                bus.publish(AppEvent("screen.game_set", "auto_monitor"))
            time.sleep(0.10)

            self.assertEqual(applied, ["between_game"])
            controller.close()

    def test_controller_reuses_one_obs_connection_across_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ScoreboardStateStore(Path(directory) / "state.json")
            state.configure("aya", True)
            bus = EventBus()
            settings = AppSettings(
                scoreboard_design="aya",
                player_camera_enabled=True,
                obs_layout_enabled=True,
                obs_layout_scene_name="Broadcast",
                obs_game_source_name="Game",
                obs_in_game_layout={"game": {"positionX": 10.0}},
                obs_between_game_layout={"game": {"positionX": 20.0}},
            )
            client = FakeObsClient()
            with patch("tournament_control.obs_layout._client", return_value=client) as factory:
                controller = ObsLayoutController(bus, settings, state)
                self.assertEqual(controller._apply("in_game", settings), 1)
                self.assertEqual(controller._apply("between_game", settings), 1)
                self.assertEqual(factory.call_count, 1)
                self.assertFalse(client.disconnected)
                controller.close()
                self.assertTrue(client.disconnected)

    def test_invalid_zero_bounds_are_omitted(self):
        transform = sanitize_transform(
            {
                "boundsType": "OBS_BOUNDS_NONE",
                "boundsWidth": 0.0,
                "boundsHeight": 0.5,
                "positionX": 120.0,
            }
        )
        self.assertEqual(transform["boundsType"], "OBS_BOUNDS_NONE")
        self.assertEqual(transform["positionX"], 120.0)
        self.assertNotIn("boundsWidth", transform)
        self.assertNotIn("boundsHeight", transform)

    def test_valid_bounds_are_preserved(self):
        transform = sanitize_transform(
            {"boundsWidth": 1280.0, "boundsHeight": 720.0}
        )
        self.assertEqual(transform["boundsWidth"], 1280.0)
        self.assertEqual(transform["boundsHeight"], 720.0)

    def test_capture_and_apply_layout_by_source_name(self):
        capture_client = FakeObsClient()
        sources = {"game": "Game", "player1": "P1", "player2": "P2"}
        layout = capture_scene_layout(
            {},
            "Broadcast",
            sources,
            client_factory=lambda _connection: capture_client,
        )
        self.assertEqual(layout["game"]["positionX"], 10.0)
        self.assertEqual(layout["game"][SCENE_ITEM_ID_FIELD], 10)
        self.assertEqual(layout["game"][SCENE_ITEM_INDEX_FIELD], 0)
        self.assertNotIn("sourceWidth", layout["game"])
        self.assertEqual(layout["game"][SOURCE_WIDTH_FIELD], 1920.0)
        self.assertEqual(layout["game"][SOURCE_HEIGHT_FIELD], 720.0)
        self.assertEqual(layout["game"][CANVAS_WIDTH_FIELD], 1280.0)
        self.assertEqual(layout["game"][CANVAS_HEIGHT_FIELD], 720.0)
        self.assertTrue(capture_client.disconnected)

        apply_client = FakeObsClient()
        count = apply_scene_layout(
            {},
            "Broadcast",
            sources,
            layout,
            client_factory=lambda _connection: apply_client,
        )
        self.assertEqual(count, 3)
        self.assertEqual([item[1] for item in apply_client.applied], [10, 11, 12])
        self.assertEqual(
            apply_client.applied_indices,
            [("Broadcast", 10, 0), ("Broadcast", 11, 1), ("Broadcast", 12, 2)],
        )
        self.assertTrue(apply_client.disconnected)

    def test_duplicate_source_names_are_captured_and_applied_individually(self):
        capture_client = FakeObsClient()
        capture_client.scene_items = [
            {"sourceName": "Game", "sceneItemId": 10, "sceneItemIndex": 0},
            {"sourceName": "Frame", "sceneItemId": 15, "sceneItemIndex": 1},
            {"sourceName": "Logo", "sceneItemId": 16, "sceneItemIndex": 2},
            {"sourceName": "Camera", "sceneItemId": 21, "sceneItemIndex": 3},
            {"sourceName": "Score", "sceneItemId": 17, "sceneItemIndex": 4},
            {"sourceName": "Camera", "sceneItemId": 22, "sceneItemIndex": 5},
        ]
        sources = {"game": "Game", "player1": "Camera", "player2": "Camera"}

        layout = capture_scene_layout(
            {},
            "Broadcast",
            sources,
            client_factory=lambda _connection: capture_client,
        )

        self.assertEqual(layout["player1"][SCENE_ITEM_ID_FIELD], 21)
        self.assertEqual(layout["player2"][SCENE_ITEM_ID_FIELD], 22)
        self.assertEqual(layout["player1"][SOURCE_OCCURRENCE_FIELD], 0)
        self.assertEqual(layout["player2"][SOURCE_OCCURRENCE_FIELD], 1)
        self.assertEqual(layout["player1"]["positionX"], 21.0)
        self.assertEqual(layout["player2"]["positionX"], 22.0)

        apply_client = FakeObsClient()
        apply_client.scene_items = list(capture_client.scene_items)
        count = apply_scene_layout(
            {},
            "Broadcast",
            sources,
            layout,
            client_factory=lambda _connection: apply_client,
        )

        self.assertEqual(count, 3)
        self.assertEqual([item[1] for item in apply_client.applied], [10, 21, 22])
        self.assertEqual(
            apply_client.applied_indices,
            [("Broadcast", 10, 0), ("Broadcast", 21, 3), ("Broadcast", 22, 5)],
        )

    def test_preview_entries_normalize_size_position_and_layer_order(self):
        layout = {
            "game": {
                "positionX": 0.0,
                "positionY": 0.0,
                "alignment": 5,
                "scaleX": 0.5,
                "scaleY": 0.5,
                SOURCE_WIDTH_FIELD: 1280.0,
                SOURCE_HEIGHT_FIELD: 720.0,
                CANVAS_WIDTH_FIELD: 1280.0,
                CANVAS_HEIGHT_FIELD: 720.0,
                SCENE_ITEM_INDEX_FIELD: 0,
            },
            "player1": {
                "positionX": 640.0,
                "positionY": 360.0,
                "alignment": 0,
                "scaleX": 1.0,
                "scaleY": 1.0,
                SOURCE_WIDTH_FIELD: 640.0,
                SOURCE_HEIGHT_FIELD: 360.0,
                CANVAS_WIDTH_FIELD: 1280.0,
                CANVAS_HEIGHT_FIELD: 720.0,
                SCENE_ITEM_INDEX_FIELD: 2,
            },
        }

        entries = layout_preview_entries(layout)
        self.assertEqual([entry["key"] for entry in entries], ["player1", "game"])
        game = entries[1]
        self.assertEqual((game["x"], game["y"]), (0.0, 0.0))
        self.assertEqual((game["width"], game["height"]), (0.5, 0.5))
        player = entries[0]
        self.assertEqual((player["x"], player["y"]), (0.25, 0.25))
        self.assertEqual((player["width"], player["height"]), (0.5, 0.5))

    def test_apply_falls_back_to_duplicate_occurrence_when_ids_changed(self):
        client = FakeObsClient()
        client.scene_items = [
            {"sourceName": "Camera", "sceneItemId": 31, "sceneItemIndex": 0},
            {"sourceName": "Camera", "sceneItemId": 32, "sceneItemIndex": 1},
        ]
        layout = {
            "player1": {
                "positionX": 100.0,
                SCENE_ITEM_ID_FIELD: 21,
                SOURCE_OCCURRENCE_FIELD: 0,
            },
            "player2": {
                "positionX": 200.0,
                SCENE_ITEM_ID_FIELD: 22,
                SOURCE_OCCURRENCE_FIELD: 1,
            },
        }

        count = apply_scene_layout(
            {},
            "Broadcast",
            {"player1": "Camera", "player2": "Camera"},
            layout,
            client_factory=lambda _connection: client,
        )

        self.assertEqual(count, 2)
        self.assertEqual([item[1] for item in client.applied], [31, 32])

    def test_apply_repairs_invalid_bounds_in_existing_saved_layout(self):
        client = FakeObsClient()
        count = apply_scene_layout(
            {},
            "Broadcast",
            {"game": "Game"},
            {
                "game": {
                    "boundsType": "OBS_BOUNDS_NONE",
                    "boundsWidth": 0.0,
                    "boundsHeight": 0.0,
                    "positionX": 42.0,
                }
            },
            client_factory=lambda _connection: client,
        )
        self.assertEqual(count, 1)
        applied = client.applied[0][2]
        self.assertNotIn("boundsWidth", applied)
        self.assertNotIn("boundsHeight", applied)
        self.assertEqual(applied["positionX"], 42.0)


if __name__ == "__main__":
    unittest.main()

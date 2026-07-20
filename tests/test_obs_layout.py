import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tournament_control.automation import ScoreboardStateStore
from tournament_control.events import AppEvent, EventBus
from tournament_control.models import AppSettings
from tournament_control.obs_layout import (
    ObsLayoutController,
    apply_scene_layout,
    capture_scene_layout,
    sanitize_transform,
)


class FakeObsClient:
    def __init__(self):
        self.ids = {"Game": 10, "P1": 11, "P2": 12}
        self.applied = []
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

    def set_scene_item_transform(self, scene_name, item_id, transform):
        self.applied.append((scene_name, item_id, transform))

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
        self.assertNotIn("sourceWidth", layout["game"])
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
        self.assertTrue(apply_client.disconnected)

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

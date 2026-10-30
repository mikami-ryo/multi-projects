import tempfile
import threading
import time
import unittest
from pathlib import Path

from tournament_control.automation import ScoreboardStateStore
from tournament_control.events import AppEvent, EventBus
from tournament_control.models import AppSettings
from tournament_control.obs_transition import (
    BrowserTransitionController,
    TransitionStateStore,
)


class BrowserTransitionTests(unittest.TestCase):
    def _presentation_state(self, root: Path) -> ScoreboardStateStore:
        state = ScoreboardStateStore(root / "presentation.json")
        state.write({"mode": "automatic"})
        return state

    @staticmethod
    def _wait_for_active(store: TransitionStateStore, previous_id: int = 0) -> dict:
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            state = store.read()
            if state["active"] and state["transitionId"] > previous_id:
                return state
            time.sleep(0.005)
        raise AssertionError("transition did not become active")

    def test_acknowledged_browser_playback_releases_event_at_switch_point(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_bus, output_bus = EventBus(), EventBus()
            transition_state = TransitionStateStore()
            settings = AppSettings(
                obs_transition_enabled=True,
                obs_transition_switch_delay=0.02,
                obs_transition_duration=0.06,
            )
            forwarded = []
            switched = threading.Event()
            output_bus.subscribe(lambda event: (forwarded.append(event), switched.set()))
            controller = BrowserTransitionController(
                input_bus,
                output_bus,
                settings,
                self._presentation_state(root),
                transition_state,
            )
            input_bus.publish(AppEvent("screen.vs", "test"))
            active = self._wait_for_active(transition_state)
            self.assertTrue(transition_state.acknowledge(active["transitionId"]))
            self.assertTrue(switched.wait(0.3))
            self.assertTrue(forwarded[0].payload["_transition_ready"])
            deadline = time.monotonic() + 0.3
            while transition_state.read()["active"] and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(transition_state.read()["active"])
            controller.close()

    def test_disabled_transition_preserves_immediate_existing_event_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_bus, output_bus = EventBus(), EventBus()
            forwarded = []
            output_bus.subscribe(forwarded.append)
            controller = BrowserTransitionController(
                input_bus,
                output_bus,
                AppSettings(obs_transition_enabled=False),
                self._presentation_state(root),
                TransitionStateStore(),
            )
            event = AppEvent("screen.game_set", "test")
            input_bus.publish(event)
            self.assertEqual(forwarded, [event])
            controller.close()

    def test_missing_browser_source_falls_back_to_normal_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_bus, output_bus = EventBus(), EventBus()
            forwarded = []
            completed = threading.Event()
            output_bus.subscribe(lambda event: (forwarded.append(event), completed.set()))
            controller = BrowserTransitionController(
                input_bus,
                output_bus,
                AppSettings(obs_transition_enabled=True),
                self._presentation_state(root),
                TransitionStateStore(),
            )
            controller.ACK_TIMEOUT_SECONDS = 0.03
            input_bus.publish(AppEvent("screen.game_set", "test"))
            self.assertTrue(completed.wait(0.3))
            self.assertEqual(forwarded[0].name, "screen.game_set")
            self.assertNotIn("_transition_ready", forwarded[0].payload)
            controller.close()

    def test_rapid_events_keep_only_latest_transition_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_bus, output_bus = EventBus(), EventBus()
            transition_state = TransitionStateStore()
            forwarded = []
            completed = threading.Event()

            def collect(event):
                forwarded.append(event.name)
                completed.set()

            output_bus.subscribe(collect)
            settings = AppSettings(
                obs_transition_enabled=True,
                obs_transition_switch_delay=0.01,
                obs_transition_duration=0.04,
            )
            controller = BrowserTransitionController(
                input_bus,
                output_bus,
                settings,
                self._presentation_state(root),
                transition_state,
            )
            input_bus.publish(AppEvent("screen.vs", "test"))
            first = self._wait_for_active(transition_state)
            input_bus.publish(AppEvent("screen.game_set", "test"))
            second = self._wait_for_active(transition_state, first["transitionId"])
            transition_state.acknowledge(second["transitionId"])
            self.assertTrue(completed.wait(0.3))
            time.sleep(0.06)
            controller.close()
            self.assertEqual(forwarded, ["screen.game_set"])


if __name__ == "__main__":
    unittest.main()

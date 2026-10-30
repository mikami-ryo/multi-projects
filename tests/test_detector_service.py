import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tournament_control.detector_service import DetectorService, MonitorState
from tournament_control.streamcontrol import StreamControlStore


MONITOR = {
    "vs_window_frames": 3,
    "vs_required_frames": 2,
    "consecutive_game_set_frames": 2,
    "consecutive_results_frames": 2,
    "consecutive_winner_frames": 2,
    "results_timeout_frames": 240,
    "clear_match_frames": 8,
}


def result(*, vs=False, game_set=False, results=False, winner="unknown"):
    return SimpleNamespace(
        is_vs=vs,
        is_game_set=game_set,
        is_results_screen=results,
        winner=winner,
    )


class DetectorServiceTests(unittest.TestCase):
    def make_service(self, root, store, events):
        return DetectorService(
            detector_dir=root,
            monitor_config_path=root / "monitor.json",
            streamcontrol_path=store.path,
            obs_host="localhost",
            obs_port=4455,
            obs_password="",
            source_name="game",
            write_scores=True,
            on_log=lambda _line: None,
            on_score=lambda: None,
            on_event=events.append,
        )

    def test_vs_uses_two_of_last_three_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            store = StreamControlStore(root / "streamcontrol.json")
            service = self.make_service(root, store, events)
            state = MonitorState()

            service._process(state, result(vs=True), MONITOR, store)
            service._process(state, result(vs=False), MONITOR, store)
            self.assertNotIn("screen.vs", [event.name for event in events])

            message = service._process(state, result(vs=True), MONITOR, store)

            self.assertIn("screen.vs", [event.name for event in events])
            self.assertEqual(message, "VS画面を検出しました。試合中表示へ切り替えます。")

    def test_single_vs_after_game_set_does_not_cancel_result_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            store = StreamControlStore(root / "streamcontrol.json")
            store.write({"bestOf": "bo3", "pScore1": "0", "pScore2": "0"})
            service = self.make_service(root, store, events)
            state = MonitorState()

            service._process(state, result(game_set=True), MONITOR, store)
            service._process(state, result(game_set=True), MONITOR, store)
            self.assertEqual(state.phase, "awaiting_results")

            service._process(state, result(vs=True), MONITOR, store)
            service._process(state, result(vs=False), MONITOR, store)
            self.assertEqual(state.phase, "awaiting_results")
            self.assertNotIn("screen.vs", [event.name for event in events])

            service._process(state, result(results=True, winner="left"), MONITOR, store)
            service._process(state, result(results=True, winner="left"), MONITOR, store)
            self.assertEqual(state.phase, "cooldown")
            self.assertEqual(store.read()["pScore1"], "1")
            self.assertIn("score.detected", [event.name for event in events])

    def test_vs_vote_after_game_set_cancels_result_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            store = StreamControlStore(root / "streamcontrol.json")
            store.write({"bestOf": "bo3", "pScore1": "0", "pScore2": "0"})
            service = self.make_service(root, store, events)
            state = MonitorState()

            service._process(state, result(game_set=True), MONITOR, store)
            service._process(state, result(game_set=True), MONITOR, store)
            self.assertEqual(state.phase, "awaiting_results")

            service._process(state, result(vs=True), MONITOR, store)
            service._process(state, result(vs=False), MONITOR, store)
            message = service._process(state, result(vs=True), MONITOR, store)

            self.assertEqual(state.phase, "in_match")
            self.assertIn("screen.vs", [event.name for event in events])
            self.assertIn("結果待機を終了", message)
            self.assertEqual(store.read()["pScore1"], "0")


if __name__ == "__main__":
    unittest.main()

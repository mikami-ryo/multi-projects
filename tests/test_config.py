import json
import tempfile
import unittest
from pathlib import Path

from tournament_control.config import SettingsStore
from tournament_control.models import AppSettings


class SettingsTests(unittest.TestCase):
    def test_token_round_trip_and_not_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            store.save(AppSettings(startgg_token="secret-token", stream_url="https://example.com"))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-token", raw)
            loaded = store.load()
            self.assertEqual(loaded.startgg_token, "secret-token")
            self.assertEqual(loaded.stream_url, "https://example.com")

    def test_obs_layout_profiles_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            settings = AppSettings(
                obs_layout_enabled=True,
                obs_layout_scene_name="Broadcast",
                obs_game_source_name="Game",
                obs_player1_source_name="P1",
                obs_player2_source_name="P2",
                obs_in_game_layout={"game": {"positionX": 10.0}},
                obs_between_game_layout={"game": {"positionX": 20.0}},
                transition_in_game_framework_delay=1.25,
                transition_in_game_layout_delay=1.5,
                transition_between_game_framework_delay=2.25,
                transition_between_game_layout_delay=2.5,
            )
            store.save(settings)
            loaded = store.load()
            self.assertTrue(loaded.obs_layout_enabled)
            self.assertEqual(loaded.obs_layout_scene_name, "Broadcast")
            self.assertEqual(loaded.obs_in_game_layout["game"]["positionX"], 10.0)
            self.assertEqual(loaded.obs_between_game_layout["game"]["positionX"], 20.0)
            self.assertEqual(loaded.transition_in_game_framework_delay, 1.25)
            self.assertEqual(loaded.transition_in_game_layout_delay, 1.5)
            self.assertEqual(loaded.transition_between_game_framework_delay, 2.25)
            self.assertEqual(loaded.transition_between_game_layout_delay, 2.5)


if __name__ == "__main__":
    unittest.main()

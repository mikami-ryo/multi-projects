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


if __name__ == "__main__":
    unittest.main()

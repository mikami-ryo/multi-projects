import json
import tempfile
import unittest
from pathlib import Path

from tournament_control.models import DEFAULT_ROUND_MAPPINGS, Match, Player
from tournament_control.streamcontrol import StreamControlStore


class StreamControlTests(unittest.TestCase):
    def test_apply_match_preserves_existing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "streamcontrol.json"
            path.write_text(json.dumps({"l3rd1a": "keep", "streamUrl": "https://old.example"}), encoding="utf-8")
            store = StreamControlStore(path)
            match = Match(
                set_id="42",
                identifier="A1",
                round_text="Winners Final",
                state=1,
                total_games=3,
                players=[
                    Player(entrant_id="1", prefix="TeamA", gamer_tag="PlayerOne"),
                    Player(entrant_id="2", gamer_tag="Opponent"),
                ],
            )
            payload = store.apply_match(
                match,
                winners_bo5_from="Winners Final",
                losers_bo5_from="Losers Final",
                round_mappings=DEFAULT_ROUND_MAPPINGS,
            )
            self.assertEqual(payload["pTeam1"], "TeamA")
            self.assertEqual(payload["pName1"], "PlayerOne")
            self.assertEqual(payload["stage"], "WF")
            self.assertEqual(payload["bestOf"], "bo5")
            self.assertEqual(payload["pScore1"], "0")
            self.assertEqual(payload["pScore2"], "0")
            self.assertEqual(payload["timestamp"], payload["sponsorts"])
            self.assertNotEqual(payload["timestamp"], "0")
            self.assertEqual(payload["l3rd1a"], "keep")
            self.assertEqual(payload["streamUrl"], "https://old.example")


if __name__ == "__main__":
    unittest.main()

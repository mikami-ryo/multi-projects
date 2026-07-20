import json
import tempfile
import unittest
from pathlib import Path

from tournament_control.models import DEFAULT_ROUND_MAPPINGS, Match, Player
from tournament_control.streamcontrol import ScoreLimitError, StreamControlStore


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

    def test_apply_match_clears_rep_and_discards_removed_overlay_note(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "streamcontrol.json"
            path.write_text(
                json.dumps({"pRep1": "old", "pRep2": "old", "overlayNote": "実況→A"}),
                encoding="utf-8",
            )
            store = StreamControlStore(path)
            match = Match(
                set_id="42",
                identifier="A1",
                round_text="Winners Final",
                state=1,
                total_games=3,
                players=[Player(entrant_id="1", gamer_tag="A"), Player(entrant_id="2", gamer_tag="B")],
            )
            payload = store.apply_match(
                match,
                winners_bo5_from="Winners Final",
                losers_bo5_from="Losers Final",
                round_mappings=DEFAULT_ROUND_MAPPINGS,
            )
            self.assertEqual(payload["pRep1"], "")
            self.assertEqual(payload["pRep2"], "")
            self.assertNotIn("overlayNote", payload)

    def test_score_limits_are_enforced_for_bo3_and_bo5(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StreamControlStore(Path(directory) / "streamcontrol.json")
            store.write({"bestOf": "bo3", "pScore1": "2", "pScore2": "1"})
            with self.assertRaises(ScoreLimitError):
                store.increment_score(2)
            store.write({"bestOf": "bo5", "pScore1": "3", "pScore2": "2"})
            with self.assertRaises(ScoreLimitError):
                store.increment_score(2)


if __name__ == "__main__":
    unittest.main()

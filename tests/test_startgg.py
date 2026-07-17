import unittest

from tournament_control.models import Match, Player
from tournament_control.startgg import StartGGClient, event_slug_from_url, parse_event_matches


class StartGGTests(unittest.TestCase):
    def test_event_slug_from_nested_url(self):
        url = "https://www.start.gg/tournament/sample/event/ultimate-singles/brackets/123/456"
        self.assertEqual(event_slug_from_url(url), "tournament/sample/event/ultimate-singles")

    def test_parse_players_keeps_prefix_separate(self):
        payload = {
            "event": {
                "id": "10",
                "name": "Ultimate Singles",
                "sets": {
                    "nodes": [
                        {
                            "id": "99",
                            "identifier": "A1",
                            "state": 1,
                            "fullRoundText": "Winners Final",
                            "totalGames": 5,
                            "slots": [
                                {
                                    "entrant": {
                                        "id": "1",
                                        "name": "TeamA | PlayerOne",
                                        "participants": [
                                            {
                                                "gamerTag": "PlayerOne",
                                                "prefix": "TeamA",
                                                "connectedAccounts": {"twitter": "player_one"},
                                            }
                                        ],
                                    }
                                },
                                {
                                    "entrant": {
                                        "id": "2",
                                        "name": "Opponent",
                                        "participants": [{"gamerTag": "Opponent", "prefix": ""}],
                                    }
                                },
                            ],
                        }
                    ]
                },
            }
        }
        result = parse_event_matches(payload)
        match = result.matches[0]
        self.assertEqual(match.players[0].prefix, "TeamA")
        self.assertEqual(match.players[0].gamer_tag, "PlayerOne")
        self.assertEqual(match.players[0].twitter, "player_one")

    def test_event_matches_collects_all_pages(self):
        client = StartGGClient("token")
        responses = {
            1: {
                "phase": {
                    "id": "10",
                    "name": "Finals",
                    "sets": {
                        "pageInfo": {"totalPages": 2},
                        "nodes": [{"id": "1", "identifier": "A1", "slots": []}],
                    },
                }
            },
            2: {
                "phase": {
                    "id": "10",
                    "name": "Finals",
                    "sets": {
                        "pageInfo": {"totalPages": 2},
                        "nodes": [{"id": "2", "identifier": "A2", "slots": []}],
                    },
                }
            },
        }

        def execute(_query, variables):
            if "eventSlug" in variables:
                return {
                    "event": {
                        "id": "100",
                        "name": "Singles",
                        "phases": [{"id": "10", "name": "Finals"}],
                    }
                }
            return responses[variables["page"]]

        client.execute = execute

        result = client.event_matches("tournament/sample/event/singles")

        self.assertEqual([match.set_id for match in result.matches], ["1", "2"])
        self.assertEqual([match.phase_id for match in result.matches], ["10", "10"])
        self.assertEqual([match.phase_name for match in result.matches], ["Finals", "Finals"])

    def test_only_resolved_unfinished_match_is_selectable(self):
        resolved = [Player(entrant_id="1", gamer_tag="A"), Player(entrant_id="2", gamer_tag="B")]
        waiting = [Player(entrant_id="1", gamer_tag="A"), Player()]

        self.assertTrue(Match("1", "A1", "WR1", 1, 0, resolved).selectable)
        self.assertTrue(Match("2", "A2", "WR1", 2, 0, resolved).selectable)
        self.assertFalse(Match("3", "A3", "WR1", 1, 0, waiting).selectable)
        self.assertFalse(Match("4", "A4", "WR1", 3, 0, resolved).selectable)


if __name__ == "__main__":
    unittest.main()

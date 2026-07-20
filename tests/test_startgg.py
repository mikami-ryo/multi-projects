import unittest

from tournament_control.models import DEFAULT_ROUND_MAPPINGS, Match, Player
from tournament_control.round_rules import display_round
from tournament_control.startgg import (
    StartGGClient,
    event_slug_from_url,
    parse_event_matches,
    phase_group_id_from_url,
)


class StartGGTests(unittest.TestCase):
    def test_event_slug_from_nested_url(self):
        url = "https://www.start.gg/tournament/sample/event/ultimate-singles/brackets/123/456"
        self.assertEqual(event_slug_from_url(url), "tournament/sample/event/ultimate-singles")
        self.assertEqual(phase_group_id_from_url(url), "456")

    def test_event_matches_excludes_preview_phase_groups_from_bracket_url(self):
        client = StartGGClient("token")
        nodes = [
            {
                "id": "105498556",
                "identifier": "B",
                "state": 1,
                "fullRoundText": "Winners Quarter-Final",
                "phaseGroup": {"id": "3391306"},
                "slots": [],
            },
            {
                "id": "105498558",
                "identifier": "D",
                "state": 1,
                "fullRoundText": "Winners Semi-Final",
                "phaseGroup": {"id": "3391306"},
                "slots": [],
            },
            {
                "id": "preview_3391307_1_1",
                "identifier": "A",
                "state": 1,
                "fullRoundText": "Winners Round 1",
                "phaseGroup": {"id": "3391307"},
                "slots": [],
            },
        ]

        def execute(_query, variables):
            if "eventSlug" in variables:
                return {
                    "event": {
                        "id": "100",
                        "name": "Singles",
                        "phases": [{"id": "2347473", "name": "予選"}],
                    }
                }
            return {
                "phase": {
                    "id": "2347473",
                    "name": "予選",
                    "sets": {
                        "pageInfo": {"totalPages": 1},
                        "nodes": nodes,
                    },
                }
            }

        client.execute = execute

        result = client.event_matches(
            "tournament/sample-event/event/ultimate-singles",
            "3391306",
        )

        self.assertEqual(
            [(match.set_id, match.round_text) for match in result.matches],
            [
                ("105498556", "Winners Quarter-Final"),
                ("105498558", "Winners Semi-Final"),
            ],
        )

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

    def test_round_robin_bracket_type_is_read_from_phase_group(self):
        payload = {
            "event": {
                "id": "10",
                "name": "Pools",
                "sets": {
                    "nodes": [
                        {
                            "id": "99",
                            "identifier": "A",
                            "state": 1,
                            "fullRoundText": "Round 1",
                            "phaseGroup": {
                                "id": "500",
                                "bracketType": "ROUND_ROBIN",
                            },
                            "slots": [],
                        }
                    ]
                },
            }
        }

        result = parse_event_matches(payload)

        self.assertEqual(result.bracket_type, "ROUND_ROBIN")
        self.assertEqual(result.matches[0].round_text, "Round 1")

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

    def test_round_names_are_not_inferred_from_double_elimination_structure(self):
        nodes = []
        for round_number, label in (
            (1, "Winners Round 1"),
            (2, "Winners Round 2"),
            (3, "Winners Round 3"),
            (-1, "Losers Round 1"),
            (-2, "Losers Round 2"),
            (-3, "Losers Round 3"),
            (-4, "Losers Round 4"),
        ):
            nodes.append(
                {
                    "id": str(round_number),
                    "identifier": str(round_number),
                    "state": 1,
                    "round": round_number,
                    "fullRoundText": label,
                    "phaseGroup": {
                        "id": "500",
                        "displayIdentifier": "Finals",
                        "bracketType": "DOUBLE_ELIMINATION",
                    },
                    "slots": [],
                }
            )
        payload = {"event": {"id": "1", "name": "Singles", "sets": {"nodes": nodes}}}

        result = parse_event_matches(payload)
        self.assertEqual(
            [match.round_text for match in result.matches],
            [label for _, label in (
                (1, "Winners Round 1"),
                (2, "Winners Round 2"),
                (3, "Winners Round 3"),
                (-1, "Losers Round 1"),
                (-2, "Losers Round 2"),
                (-3, "Losers Round 3"),
                (-4, "Losers Round 4"),
            )],
        )

    def test_different_full_round_text_is_not_merged_by_internal_round(self):
        phase_group = {"id": "500", "bracketType": "DOUBLE_ELIMINATION"}
        nodes = [
            {
                "id": "1",
                "state": 1,
                "round": 1,
                "fullRoundText": "Winners Round 1",
                "phaseGroup": phase_group,
                "slots": [],
            },
            {
                "id": "2",
                "state": 3,
                "round": 1,
                "fullRoundText": "Winners Quarter-Final",
                "phaseGroup": phase_group,
                "slots": [],
            },
        ]
        payload = {"event": {"id": "1", "name": "Singles", "sets": {"nodes": nodes}}}

        result = parse_event_matches(payload)

        self.assertEqual([match.round_text for match in result.matches], [
            "Winners Round 1",
            "Winners Quarter-Final",
        ])

    def test_small_bracket_keeps_startgg_winners_names(self):
        nodes = []
        for round_number, label in (
            (1, "Winners Quarter-Final"),
            (2, "Winners Semi-Final"),
            (-1, "Losers Round 1"),
            (-2, "Losers Semi-Final"),
        ):
            nodes.append(
                {
                    "id": str(round_number),
                    "state": 1,
                    "round": round_number,
                    "fullRoundText": label,
                    "phaseGroup": {
                        "id": "500",
                        "bracketType": "DOUBLE_ELIMINATION",
                    },
                    "slots": [],
                }
            )
        payload = {"event": {"id": "1", "name": "Singles", "sets": {"nodes": nodes}}}

        result = parse_event_matches(payload)
        names = {match.set_id: match.round_text for match in result.matches}

        self.assertEqual(names["1"], "Winners Quarter-Final")
        self.assertEqual(names["2"], "Winners Semi-Final")
        self.assertEqual(names["-1"], "Losers Round 1")
        self.assertEqual(names["-2"], "Losers Semi-Final")

    def test_tiny_bracket_can_start_at_winners_semifinal(self):
        payload = {
            "event": {
                "id": "1",
                "name": "Singles",
                "sets": {
                    "nodes": [
                        {
                            "id": "1",
                            "identifier": "A",
                            "state": 1,
                            "fullRoundText": "Winners Semi-Final",
                            "slots": [],
                        },
                        {
                            "id": "2",
                            "identifier": "B",
                            "state": 1,
                            "fullRoundText": "Winners Final",
                            "slots": [],
                        },
                    ]
                },
            }
        }

        result = parse_event_matches(payload)

        self.assertEqual(
            [match.round_text for match in result.matches],
            ["Winners Semi-Final", "Winners Final"],
        )

    def test_round_positions_work_from_tiny_to_large_brackets(self):
        cases = {
            "two_player": (
                ["Winners Final", "Losers Final", "Grand Final"],
                ["WF", "LF", "GF"],
            ),
            "four_player": (
                [
                    "Winners Semi-Final",
                    "Winners Final",
                    "Losers Semi-Final",
                    "Losers Final",
                    "Grand Final",
                ],
                ["WSF", "WF", "LSF", "LF", "GF"],
            ),
            "eight_player": (
                [
                    "Winners Quarter-Final",
                    "Winners Semi-Final",
                    "Winners Final",
                    "Losers Quarter-Final",
                    "Losers Semi-Final",
                    "Losers Final",
                    "Grand Final",
                ],
                ["WQF", "WSF", "WF", "LQF", "LSF", "LF", "GF"],
            ),
            "large_bracket": (
                [
                    "Winners Round 1",
                    "Winners Round 2",
                    "Winners Round 3",
                    "Winners Quarter-Final",
                    "Winners Semi-Final",
                    "Winners Final",
                    "Losers Round 1",
                    "Losers Round 2",
                    "Losers Round 3",
                    "Losers Quarter-Final",
                    "Losers Semi-Final",
                    "Losers Final",
                    "Grand Final",
                ],
                [
                    "WR1", "WR2", "WR3", "WQF", "WSF", "WF",
                    "LR1", "LR2", "LR3", "LQF", "LSF", "LF", "GF",
                ],
            ),
        }

        for case_name, (round_names, expected_display_names) in cases.items():
            with self.subTest(case=case_name):
                nodes = [
                    {
                        "id": str(index),
                        "identifier": str(index),
                        "state": 1,
                        "fullRoundText": round_name,
                        "slots": [],
                    }
                    for index, round_name in enumerate(round_names, start=1)
                ]
                payload = {
                    "event": {
                        "id": "1",
                        "name": "Singles",
                        "sets": {"nodes": nodes},
                    }
                }

                result = parse_event_matches(payload)
                parsed_names = [match.round_text for match in result.matches]
                display_names = [
                    display_round(name, DEFAULT_ROUND_MAPPINGS)
                    for name in parsed_names
                ]

                self.assertEqual(parsed_names, round_names)
                self.assertEqual(display_names, expected_display_names)
                self.assertEqual(len(set(parsed_names)), len(round_names))

    def test_losers_round_one_does_not_merge_with_losers_quarter_final(self):
        phase_group = {"id": "500", "bracketType": "DOUBLE_ELIMINATION"}
        nodes = [
            {
                "id": "1",
                "state": 1,
                "round": -1,
                "fullRoundText": "Losers Round 1",
                "phaseGroup": phase_group,
                "slots": [],
            },
            {
                "id": "2",
                "state": 1,
                "round": -1,
                "fullRoundText": "Losers Quarter-Final",
                "phaseGroup": phase_group,
                "slots": [],
            },
        ]
        payload = {"event": {"id": "1", "name": "Singles", "sets": {"nodes": nodes}}}

        result = parse_event_matches(payload)

        self.assertEqual(
            [match.round_text for match in result.matches],
            ["Losers Round 1", "Losers Quarter-Final"],
        )

    def test_only_resolved_unfinished_match_is_selectable(self):
        resolved = [Player(entrant_id="1", gamer_tag="A"), Player(entrant_id="2", gamer_tag="B")]
        waiting = [Player(entrant_id="1", gamer_tag="A"), Player()]

        self.assertTrue(Match("1", "A1", "WR1", 1, 0, resolved).selectable)
        self.assertTrue(Match("2", "A2", "WR1", 2, 0, resolved).selectable)
        self.assertFalse(Match("3", "A3", "WR1", 1, 0, waiting).selectable)
        self.assertFalse(Match("4", "A4", "WR1", 3, 0, resolved).selectable)


if __name__ == "__main__":
    unittest.main()

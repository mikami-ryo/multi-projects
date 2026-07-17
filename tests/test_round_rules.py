import unittest

from tournament_control.models import DEFAULT_ROUND_MAPPINGS
from tournament_control.round_rules import ALL_BO3, best_of, display_round, is_bo5


class RoundRuleTests(unittest.TestCase):
    def test_default_winners_threshold(self):
        self.assertEqual(best_of("Winners Semifinals", "Winners Final", "Losers Final"), 3)
        self.assertEqual(best_of("Winners Final", "Winners Final", "Losers Final"), 5)

    def test_default_losers_threshold(self):
        self.assertEqual(best_of("Losers Semifinals", "Winners Final", "Losers Final"), 3)
        self.assertEqual(best_of("Losers Final", "Winners Final", "Losers Final"), 5)

    def test_grand_final_is_always_bo5(self):
        self.assertTrue(is_bo5("Grand Final Reset", "Winners Final", "Losers Final"))

    def test_phase_can_be_all_bo3_including_grand_final(self):
        self.assertEqual(best_of("Grand Final", ALL_BO3, ALL_BO3), 3)

    def test_configurable_round_number_threshold(self):
        self.assertEqual(best_of("Winners Round 2", "Winners Round 3", "Losers Final"), 3)
        self.assertEqual(best_of("Winners Round 4", "Winners Round 3", "Losers Final"), 5)

    def test_display_mapping(self):
        self.assertEqual(display_round("Grand Final Reset", DEFAULT_ROUND_MAPPINGS), "GFR")
        self.assertEqual(display_round("Grand Finals Reset", DEFAULT_ROUND_MAPPINGS), "GFR")
        self.assertEqual(display_round("Winners Final", DEFAULT_ROUND_MAPPINGS), "WF")
        self.assertEqual(display_round("Pools Round 2", DEFAULT_ROUND_MAPPINGS), "Pools Round 2")

    def test_hyphenated_startgg_round_names_use_default_mapping(self):
        self.assertEqual(display_round("Winners Semi-Final", DEFAULT_ROUND_MAPPINGS), "WSF")
        self.assertEqual(display_round("Losers Quarter‐Final", DEFAULT_ROUND_MAPPINGS), "LQF")

    def test_numbered_rounds_get_default_short_names(self):
        self.assertEqual(display_round("Winners Round 4", DEFAULT_ROUND_MAPPINGS), "WR4")
        self.assertEqual(display_round("Losers Round 7", DEFAULT_ROUND_MAPPINGS), "LR7")

    def test_numbered_round_template_is_configurable(self):
        mappings = {"Winners Round {n}": "W{n}", "Losers Round {n}": "L{n}"}
        self.assertEqual(display_round("Winners Round 12", mappings), "W12")
        self.assertEqual(display_round("Losers Round 8", mappings), "L8")


if __name__ == "__main__":
    unittest.main()

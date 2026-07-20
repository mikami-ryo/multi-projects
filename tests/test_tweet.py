import unittest

from tournament_control.tweet import build_match_announcement, x_intent_url


class TweetTests(unittest.TestCase):
    def test_existing_tweet_format_is_preserved(self):
        text = build_match_announcement(
            {
                "stage": "WF",
                "pTeam1": "TeamA",
                "pName1": "PlayerOne",
                "pTwitter1": "player_one",
                "pTeam2": "",
                "pName2": "Opponent",
                "pTwitter2": "",
                "streamUrl": "https://example.com/live",
            }
        )
        self.assertEqual(
            text,
            "🍀WF🍀\nTeamA|PlayerOne (@player_one) vs Opponent\n(https://example.com/live)",
        )

    def test_intent_keeps_line_breaks_encoded(self):
        url = x_intent_url("🍀LSF🍀\nPlayerOne vs PlayerTwo\n(https://example.com)")
        self.assertEqual(url.count("%0A"), 2)


if __name__ == "__main__":
    unittest.main()

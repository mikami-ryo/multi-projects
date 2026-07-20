import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox
from PySide6.QtCore import Qt

from tournament_control.models import AppSettings, Match, Player
from tournament_control.round_rules import ALL_BO3
from tournament_control.ui import (
    APP_STYLE,
    ALL_ROUNDS_KEY,
    DetectorPage,
    SettingsPage,
    StreamControlPage,
    TweetDialog,
    matches_for_round_key,
    round_keys_for_matches,
)
from tournament_control.streamcontrol import StreamControlStore


class TweetDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_preserves_line_breaks(self):
        dialog = TweetDialog(
            {
                "stage": "LSF",
                "pName1": "PlayerOne",
                "pName2": "PlayerTwo",
                "streamUrl": "https://example.com/live/sample",
            }
        )
        self.assertEqual(
            dialog.preview.toPlainText(),
            "🍀LSF🍀\nPlayerOne vs PlayerTwo\n"
            "(https://example.com/live/sample)",
        )

    def test_settings_show_copyable_obs_url_and_canonical_round_fields(self):
        page = SettingsPage(AppSettings(scoreboard_port=9123))
        self.assertEqual(page.obs_url.text(), "http://127.0.0.1:9123/scoreboard.html")
        self.assertEqual(len(page.mapping_fields), 10)
        self.assertEqual(page.mapping_fields["Grand Final"].text(), "GF")
        self.assertEqual(page.mapping_fields["Winners Round {n}"].text(), "WR{n}")
        self.assertTrue(page.scroll_area.widgetResizable())
        self.assertEqual(
            page.scroll_area.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertGreaterEqual(page.mapping_fields["Grand Final"].minimumHeight(), 32)

        page.scoreboard_port.setValue(9456)
        self.assertEqual(page.obs_url.text(), "http://127.0.0.1:9456/scoreboard.html")

        page.scoreboard_design.setCurrentIndex(page.scoreboard_design.findData("aya"))
        page.player_camera.setCurrentIndex(page.player_camera.findData(True))
        saved = []
        page.saved.connect(saved.append)
        page.save()
        self.assertEqual(saved[0].scoreboard_design, "aya")
        self.assertTrue(saved[0].player_camera_enabled)

    def test_obs_management_page_is_vertically_scrollable(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = type("Paths", (), {"detector_dir": Path(directory)})()
            page = DetectorPage(
                AppSettings(
                    transition_in_game_framework_delay=1.25,
                    transition_in_game_layout_delay=1.5,
                    transition_between_game_framework_delay=2.25,
                    transition_between_game_layout_delay=2.5,
                ),
                paths,
            )
            self.assertTrue(page.scroll_area.widgetResizable())
            self.assertEqual(
                page.scroll_area.horizontalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            self.assertGreaterEqual(page.log.minimumHeight(), 180)
            payload = page.obs_settings_payload()
            self.assertEqual(payload["transition_in_game_framework_delay"], 1.25)
            self.assertEqual(payload["transition_in_game_layout_delay"], 1.5)
            self.assertEqual(payload["transition_between_game_framework_delay"], 2.25)
            self.assertEqual(payload["transition_between_game_layout_delay"], 2.5)

    def test_phase_bo5_choices_only_use_discovered_rounds(self):
        page = SettingsPage(AppSettings())
        page.update_phase_catalog(
            {
                "10": {
                    "name": "Finals",
                    "rounds": ["Winners Final", "Winners Round 3", "Losers Final"],
                }
            }
        )
        winners, losers = page.phase_controls["10"]
        self.assertEqual(
            [winners.itemData(index) for index in range(winners.count())],
            [ALL_BO3, "Winners Final", "Winners Round 3"],
        )
        self.assertEqual(
            [losers.itemData(index) for index in range(losers.count())],
            [ALL_BO3, "Losers Final"],
        )
        winners.setCurrentIndex(0)
        losers.setCurrentIndex(0)
        saved = []
        page.saved.connect(saved.append)
        page.save()
        self.assertEqual(saved[0].phase_bo5_rules["10"]["winners_from"], ALL_BO3)
        self.assertEqual(saved[0].phase_bo5_rules["10"]["losers_from"], ALL_BO3)

    def test_round_robin_uses_one_all_rounds_group(self):
        players = [
            Player(entrant_id="1", gamer_tag="A"),
            Player(entrant_id="2", gamer_tag="B"),
        ]
        matches = [
            Match("1", "A", "Round 1", 1, 0, players, phase_id="10"),
            Match("2", "B", "Round 2", 1, 0, players, phase_id="10"),
            Match("3", "C", "Round 3", 1, 0, players, phase_id="10"),
        ]

        self.assertEqual(
            round_keys_for_matches(matches, "ROUND_ROBIN"),
            [ALL_ROUNDS_KEY],
        )
        self.assertEqual(
            matches_for_round_key(matches, "ROUND_ROBIN", ALL_ROUNDS_KEY),
            matches,
        )

    def test_double_elimination_keeps_round_groups_separate(self):
        players = [
            Player(entrant_id="1", gamer_tag="A"),
            Player(entrant_id="2", gamer_tag="B"),
        ]
        matches = [
            Match("1", "A", "Winners Quarter-Final", 1, 0, players, phase_id="10"),
            Match("2", "B", "Winners Semi-Final", 1, 0, players, phase_id="10"),
        ]

        self.assertEqual(
            round_keys_for_matches(matches, "DOUBLE_ELIMINATION"),
            [
                ("10", "Winners Quarter-Final"),
                ("10", "Winners Semi-Final"),
            ],
        )

    def test_score_spinbox_buttons_increment_and_decrement(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StreamControlStore(Path(directory) / "streamcontrol.json")
            page = StreamControlPage(store, "http://127.0.0.1:8765/scoreboard.html")
            page.setStyleSheet(APP_STYLE)
            page.resize(1200, 500)
            page.show()
            self.app.processEvents()
            spin = page.fields["pScore1"]
            option = QStyleOptionSpinBox()
            spin.initStyleOption(option)
            up_rect = spin.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox,
                option,
                QStyle.SubControl.SC_SpinBoxUp,
                spin,
            )
            down_rect = spin.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox,
                option,
                QStyle.SubControl.SC_SpinBoxDown,
                spin,
            )
            QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=up_rect.center())
            self.assertEqual(spin.value(), 1)
            QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=down_rect.center())
            self.assertEqual(spin.value(), 0)
            page.close()


if __name__ == "__main__":
    unittest.main()

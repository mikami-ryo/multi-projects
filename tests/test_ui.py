import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from tournament_control.models import AppSettings
from tournament_control.round_rules import ALL_BO3
from tournament_control.ui import SettingsPage, TweetDialog


class TweetDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_preserves_line_breaks(self):
        dialog = TweetDialog(
            {
                "stage": "LSF",
                "pName1": "Levi",
                "pName2": "MiiBrawlerGOD",
                "streamUrl": "https://youtube.com/live/3PtpuoGyVSU?feature=share",
            }
        )
        self.assertEqual(
            dialog.preview.toPlainText(),
            "🍀LSF🍀\nLevi vs MiiBrawlerGOD\n"
            "(https://youtube.com/live/3PtpuoGyVSU?feature=share)",
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


if __name__ == "__main__":
    unittest.main()

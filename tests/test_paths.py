import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tournament_control.paths import AppPaths


class AppPathsTests(unittest.TestCase):
    def test_detector_monitor_config_is_copied_to_runtime_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "resources"
            data = root / "data"
            data.mkdir()
            detector = resources / "score_board" / "AutoScoreDetector"
            detector.mkdir(parents=True)
            media = resources / "media"
            media.mkdir(parents=True)
            (media / "Aya_transition.mp4").write_bytes(b"transition")
            (resources / "score_board" / "streamcontrol.json").write_text("{}", encoding="utf-8")
            (resources / "score_board" / "scoreboard.html").write_text("factory", encoding="utf-8")
            (resources / "score_board" / "scoreboard.css").write_text("css", encoding="utf-8")
            (detector / "obs_monitor_config.json").write_text('{"monitor": {}, "obs": {}}', encoding="utf-8")

            with (
                patch("tournament_control.paths.resource_root", return_value=resources),
                patch("tournament_control.paths.app_data_dir", return_value=data),
            ):
                paths = AppPaths.create()

            self.assertEqual(paths.detector_runtime_config, data / "obs_monitor_config.json")
            self.assertTrue(paths.detector_runtime_config.exists())
            self.assertIn('"monitor"', paths.detector_runtime_config.read_text(encoding="utf-8"))
            self.assertEqual(paths.scoreboard_assets, data / "scoreboard")
            self.assertEqual(
                (paths.scoreboard_assets / "scoreboard.html").read_text(encoding="utf-8"),
                "factory",
            )
            self.assertFalse((paths.scoreboard_assets / "AutoScoreDetector").exists())
            self.assertTrue(paths.automation_rules_file.exists())
            self.assertTrue(paths.scoreboard_state_file.exists())
            self.assertEqual(
                paths.transition_video_file,
                data / "scoreboard" / "media" / "Aya_transition.mp4",
            )
            self.assertEqual(paths.transition_video_file.read_bytes(), b"transition")

            (paths.scoreboard_assets / "scoreboard.html").write_text("custom", encoding="utf-8")
            with (
                patch("tournament_control.paths.resource_root", return_value=resources),
                patch("tournament_control.paths.app_data_dir", return_value=data),
            ):
                second = AppPaths.create()
            self.assertEqual(
                (second.scoreboard_assets / "scoreboard.html").read_text(encoding="utf-8"),
                "custom",
            )


if __name__ == "__main__":
    unittest.main()

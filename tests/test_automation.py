import json
import tempfile
import unittest
from pathlib import Path

from tournament_control.automation import AutomationEngine, ScoreboardStateStore
from tournament_control.events import AppEvent, EventBus


class AutomationTests(unittest.TestCase):
    def test_empty_rules_record_event_without_changing_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / "rules.json"
            rules.write_text('{"version": 1, "rules": []}', encoding="utf-8")
            state = ScoreboardStateStore(root / "state.json")
            state.write({"activeTheme": "default"})
            bus = EventBus()
            engine = AutomationEngine(bus, rules, state)

            bus.publish(AppEvent("screen.game_set", "auto_monitor"))

            current = state.read()
            self.assertEqual(current["activeTheme"], "default")
            self.assertEqual(current["lastEvent"]["name"], "screen.game_set")
            engine.close()

    def test_rule_can_change_theme_without_detector_coupling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / "rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "rules": [
                            {
                                "event": "score.detected",
                                "payload": {"winner": "left"},
                                "action": {"type": "set_theme", "theme": "results"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state = ScoreboardStateStore(root / "state.json")
            state.write({})
            bus = EventBus()
            engine = AutomationEngine(bus, rules, state)

            bus.publish(AppEvent("score.detected", "auto_monitor", {"winner": "left"}))

            self.assertEqual(state.read()["activeTheme"], "results")
            engine.close()

    def test_manual_mode_ignores_theme_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules = root / "rules.json"
            rules.write_text(
                '{"rules":[{"event":"screen.game_set","action":{"type":"set_theme","theme":"game"}}]}',
                encoding="utf-8",
            )
            state = ScoreboardStateStore(root / "state.json")
            state.write({"activeTheme": "custom", "mode": "manual"})
            bus = EventBus()
            engine = AutomationEngine(bus, rules, state)

            bus.publish(AppEvent("screen.game_set", "auto_monitor"))

            self.assertEqual(state.read()["activeTheme"], "custom")
            engine.close()


if __name__ == "__main__":
    unittest.main()

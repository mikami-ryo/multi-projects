import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from tournament_control.automation import ScoreboardStateStore
from tournament_control.obs_transition import TransitionStateStore
from tournament_control.scoreboard_server import ScoreboardServer
from tournament_control.streamcontrol import StreamControlStore


class ScoreboardServerTests(unittest.TestCase):
    def test_new_scoreboard_hides_unused_or_missing_image_layers(self):
        project_root = Path(__file__).resolve().parents[1]
        script = (project_root / "score_board" / "tournament-scoreboard.js").read_text(
            encoding="utf-8"
        )
        stylesheet = (
            project_root / "score_board" / "tournament-scoreboard.css"
        ).read_text(encoding="utf-8")
        self.assertIn('element.removeAttribute("src")', script)
        self.assertIn('element.onerror = () =>', script)
        self.assertRegex(stylesheet, r"\.full-layer\s*\{[^}]*display:\s*none;")

    def test_transition_browser_source_scales_video_to_its_viewport(self):
        project_root = Path(__file__).resolve().parents[1]
        stylesheet = (project_root / "score_board" / "transition.css").read_text(
            encoding="utf-8"
        )
        script = (project_root / "score_board" / "transition.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("width: 100vw", stylesheet)
        self.assertIn("height: 100vh", stylesheet)
        self.assertIn("object-fit: cover", stylesheet)
        self.assertIn('fetch("transition-ack"', script)

    def test_transition_browser_waits_for_media_end_before_hiding(self):
        project_root = Path(__file__).resolve().parents[1]
        script = (project_root / "score_board" / "transition.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('video.addEventListener("ended"', script)
        self.assertIn("if (video.ended || video.paused)", script)
        self.assertIn("schedulePlaybackSafetyTimeout", script)

    def test_serves_runtime_streamcontrol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scoreboard.html").write_text("scoreboard", encoding="utf-8")
            (root / "transition.html").write_text("transition", encoding="utf-8")
            media = root / "media"
            media.mkdir()
            (media / "Aya_transition.mp4").write_bytes(b"video")
            store = StreamControlStore(root / "runtime.json")
            store.write({"pName1": "PlayerOne"})
            state = ScoreboardStateStore(root / "scoreboard-state.json")
            state.write({"activeTheme": "default"})
            transition = TransitionStateStore()
            server = ScoreboardServer(root, store, 0, state, transition)
            server.start()
            try:
                port = server._server.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/streamcontrol.json") as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["pName1"], "PlayerOne")
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/scoreboard-state.json") as response:
                    presentation = json.loads(response.read().decode("utf-8"))
                self.assertEqual(presentation["activeTheme"], "default")
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/transition.html"
                ) as response:
                    self.assertEqual(response.read(), b"transition")
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/media/Aya_transition.mp4"
                ) as response:
                    self.assertEqual(response.read(), b"video")

                transition_id = transition.start("VS")
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/transition-state.json?after=-1"
                ) as response:
                    transition_payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(transition_payload["active"])
                self.assertEqual(transition_payload["transitionId"], transition_id)

                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/transition-ack",
                    data=json.dumps({"transitionId": transition_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    acknowledged = json.loads(response.read().decode("utf-8"))
                self.assertTrue(acknowledged["accepted"])
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from tournament_control.automation import ScoreboardStateStore
from tournament_control.scoreboard_server import ScoreboardServer
from tournament_control.streamcontrol import StreamControlStore


class ScoreboardServerTests(unittest.TestCase):
    def test_serves_runtime_streamcontrol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scoreboard.html").write_text("scoreboard", encoding="utf-8")
            store = StreamControlStore(root / "runtime.json")
            store.write({"pName1": "PlayerOne"})
            state = ScoreboardStateStore(root / "scoreboard-state.json")
            state.write({"activeTheme": "default"})
            server = ScoreboardServer(root, store, 0, state)
            server.start()
            try:
                port = server._server.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/streamcontrol.json") as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["pName1"], "PlayerOne")
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/scoreboard-state.json") as response:
                    presentation = json.loads(response.read().decode("utf-8"))
                self.assertEqual(presentation["activeTheme"], "default")
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()

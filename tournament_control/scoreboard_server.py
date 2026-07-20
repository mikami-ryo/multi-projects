from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .automation import ScoreboardStateStore
from .streamcontrol import StreamControlStore


class ScoreboardServer:
    def __init__(
        self,
        assets_dir: Path,
        store: StreamControlStore,
        port: int,
        state_store: ScoreboardStateStore | None = None,
    ):
        self.assets_dir = Path(assets_dir)
        self.store = store
        self.port = int(port)
        self.state_store = state_store
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def scoreboard_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/scoreboard.html"

    def start(self) -> None:
        assets_dir = self.assets_dir
        store = self.store
        state_store = self.state_store

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(assets_dir), **kwargs)

            def log_message(self, format: str, *args) -> None:
                return

            def do_GET(self):
                request_path = self.path.split("?", 1)[0]
                if request_path in {"/", "/scoreboard.html"}:
                    self.path = "/tournament-scoreboard.html"
                    return super().do_GET()
                if request_path == "/legacy-scoreboard.html":
                    self.path = "/scoreboard.html"
                    return super().do_GET()
                if request_path == "/streamcontrol.json":
                    body = json.dumps(store.read(), ensure_ascii=False, indent=2).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if request_path == "/scoreboard-state.json" and state_store is not None:
                    body = json.dumps(state_store.read(), ensure_ascii=False, indent=2).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="scoreboard-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self._server = None
        self._thread = None

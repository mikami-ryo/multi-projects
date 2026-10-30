from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .automation import ScoreboardStateStore
from .obs_transition import TransitionStateStore
from .streamcontrol import StreamControlStore


class ScoreboardServer:
    def __init__(
        self,
        assets_dir: Path,
        store: StreamControlStore,
        port: int,
        state_store: ScoreboardStateStore | None = None,
        transition_state_store: TransitionStateStore | None = None,
    ):
        self.assets_dir = Path(assets_dir)
        self.store = store
        self.port = int(port)
        self.state_store = state_store
        self.transition_state_store = transition_state_store
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def scoreboard_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/scoreboard.html"

    @property
    def transition_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/transition.html"

    def start(self) -> None:
        assets_dir = self.assets_dir
        store = self.store
        state_store = self.state_store
        transition_state_store = self.transition_state_store

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(assets_dir), **kwargs)

            def log_message(self, format: str, *args) -> None:
                return

            def send_json(self, payload: dict, status=HTTPStatus.OK) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                request = urlsplit(self.path)
                request_path = request.path
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
                if request_path == "/transition-state.json" and transition_state_store is not None:
                    query = parse_qs(request.query)
                    try:
                        after_revision = int(query.get("after", ["-1"])[0])
                    except (TypeError, ValueError):
                        after_revision = -1
                    self.send_json(
                        transition_state_store.wait_for_change(after_revision, timeout=20.0)
                    )
                    return
                super().do_GET()

            def do_POST(self):
                request_path = urlsplit(self.path).path
                if request_path != "/transition-ack" or transition_state_store is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 4096)
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    transition_id = int(payload.get("transitionId", 0))
                except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                accepted = transition_state_store.acknowledge(transition_id)
                self.send_json(
                    {"accepted": accepted},
                    HTTPStatus.OK if accepted else HTTPStatus.CONFLICT,
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="scoreboard-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self._server = None
        self._thread = None

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .events import AppEvent
from .streamcontrol import ScoreLimitError


@dataclass
class MonitorState:
    phase: str = "in_match"
    game_set_count: int = 0
    results_count: int = 0
    winner_streak_winner: str = "unknown"
    winner_streak_count: int = 0
    clear_count: int = 0
    frames_since_game_set: int = 0
    frame_index: int = 0
    vs_count: int = 0
    vs_history: list[bool] = field(default_factory=list)
    vs_clear_count: int = 0
    vs_latched: bool = False


def _reset(state: MonitorState) -> None:
    state.phase = "in_match"
    state.game_set_count = 0
    state.results_count = 0
    state.winner_streak_winner = "unknown"
    state.winner_streak_count = 0
    state.clear_count = 0
    state.frames_since_game_set = 0


class DetectorService:
    """Runs the existing OpenCV detector in a stoppable background thread."""

    def __init__(
        self,
        *,
        detector_dir: Path,
        monitor_config_path: Path,
        streamcontrol_path: Path,
        obs_host: str,
        obs_port: int,
        obs_password: str,
        source_name: str,
        write_scores: bool,
        on_log: Callable[[str], None],
        on_score: Callable[[], None],
        on_event: Callable[[AppEvent], None] | None = None,
    ):
        self.detector_dir = Path(detector_dir)
        self.monitor_config_path = Path(monitor_config_path)
        self.streamcontrol_path = Path(streamcontrol_path)
        self.obs_host = obs_host
        self.obs_port = int(obs_port)
        self.obs_password = obs_password
        self.source_name = source_name
        self.write_scores = write_scores
        self.on_log = on_log
        self.on_score = on_score
        self.on_event = on_event or (lambda event: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="score-detector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _emit(self, name: str, **payload) -> None:
        self.on_event(AppEvent(name=name, source="auto_monitor", payload=payload))

    def _run(self) -> None:
        client = None
        try:
            import cv2
            import obsws_python as obs

            from score_board.AutoScoreDetector.detect_result import (
                decode_data_url,
                detect_winner_from_image,
                load_config,
                render_debug_image,
            )
            from .streamcontrol import StreamControlStore

            del cv2, render_debug_image
            with self.monitor_config_path.open(encoding="utf-8") as handle:
                monitor_raw = json.load(handle)
            monitor = monitor_raw["monitor"]
            obs_cfg = monitor_raw["obs"]
            detector_config = load_config(self.detector_dir / "config.json")

            client = obs.ReqClient(
                host=self.obs_host,
                port=self.obs_port,
                password=self.obs_password,
                timeout=float(obs_cfg.get("timeout_seconds", 3.0)),
            )
            self._emit("detector.started", source_name=self.source_name)
            self.on_log(f"OBS接続: {self.obs_host}:{self.obs_port} / source={self.source_name}")
            state = MonitorState()
            store = StreamControlStore(self.streamcontrol_path)

            while not self._stop.is_set():
                state.frame_index += 1
                response = client.get_source_screenshot(
                    self.source_name,
                    obs_cfg.get("image_format", "jpg"),
                    int(obs_cfg.get("image_width", 1280)),
                    int(obs_cfg.get("image_height", 720)),
                    int(obs_cfg.get("image_compression_quality", 80)),
                )
                image = decode_data_url(response.image_data)
                result = detect_winner_from_image(image, detector_config)
                action = self._process(state, result, monitor, store)
                if action:
                    self.on_log(action)
                self._stop.wait(float(monitor.get("poll_interval_ms", 250)) / 1000.0)
        except Exception as exc:
            self._emit("detector.error", message=str(exc))
            self.on_log(f"Detector停止: {exc}")
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass
            self.on_log("Detectorを停止しました。")
            self._emit("detector.stopped")

    def _process(self, state, result, monitor: dict, store) -> str | None:
        vs_window_frames = max(2, int(monitor.get("vs_window_frames", 3)))
        vs_required_frames = min(
            vs_window_frames,
            max(2, int(monitor.get("vs_required_frames", 2))),
        )
        state.vs_history.append(bool(result.is_vs))
        del state.vs_history[:-vs_window_frames]
        state.vs_count = sum(state.vs_history)

        if result.is_vs:
            state.vs_clear_count = 0
        else:
            state.vs_clear_count += 1
            if state.vs_clear_count >= 2:
                state.vs_latched = False

        vs_detected = (
            len(state.vs_history) >= vs_required_frames
            and state.vs_count >= vs_required_frames
            and not state.vs_latched
        )
        if vs_detected and state.phase in {"in_match", "awaiting_results"}:
            was_awaiting_results = state.phase == "awaiting_results"
            state.vs_latched = True
            _reset(state)
            self._emit("screen.vs")
            if was_awaiting_results:
                return "次のVS画面を検出したため、結果待機を終了して試合中表示へ切り替えます。"
            return "VS画面を検出しました。試合中表示へ切り替えます。"

        if state.phase == "in_match":
            if result.is_game_set:
                state.game_set_count += 1
                if state.game_set_count >= int(monitor["consecutive_game_set_frames"]):
                    state.phase = "awaiting_results"
                    state.results_count = 0
                    state.frames_since_game_set = 0
                    self._emit("screen.game_set")
                    return "GAME SETを検出しました。結果画面を待機します。"
            else:
                state.game_set_count = 0
            return None

        if state.phase == "awaiting_results":
            if result.is_game_set:
                state.frames_since_game_set = 0
                return None
            if result.is_results_screen:
                state.results_count += 1
                if result.winner in {"left", "right"}:
                    if state.winner_streak_winner == result.winner:
                        state.winner_streak_count += 1
                    else:
                        state.winner_streak_winner = result.winner
                        state.winner_streak_count = 1
                else:
                    state.winner_streak_winner = "unknown"
                    state.winner_streak_count = 0

                if (
                    state.results_count >= int(monitor["consecutive_results_frames"])
                    and state.winner_streak_count >= int(monitor["consecutive_winner_frames"])
                    and state.winner_streak_winner in {"left", "right"}
                ):
                    winner = state.winner_streak_winner
                    state.phase = "cooldown"
                    state.clear_count = 0
                    self._emit(
                        "score.detected",
                        winner=winner,
                        player=1 if winner == "left" else 2,
                        score_written=self.write_scores,
                    )
                    if self.write_scores:
                        try:
                            store.increment_score(1 if winner == "left" else 2)
                        except ScoreLimitError as exc:
                            return f"スコアを更新しませんでした: {exc}"
                        self.on_score()
                        return f"勝者を検出し、P{1 if winner == 'left' else 2}のスコアを加算しました。"
                    return f"勝者を検出しました: {winner}（書き込みは無効）"
            else:
                state.frames_since_game_set += 1
                state.results_count = 0
                state.winner_streak_winner = "unknown"
                state.winner_streak_count = 0

            if state.frames_since_game_set >= int(monitor["results_timeout_frames"]):
                _reset(state)
                return "結果画面が見つからなかったため再待機します。"
            return None

        if state.phase == "cooldown":
            if not result.is_game_set and not result.is_results_screen:
                state.clear_count += 1
                if state.clear_count >= int(monitor["clear_match_frames"]):
                    _reset(state)
                    return "次のゲームを検出できる状態に戻りました。"
            else:
                state.clear_count = 0
            return None

        _reset(state)
        return None

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import obsws_python as obs
from obsws_python.error import OBSSDKError, OBSSDKRequestError

from detect_result import (
    DetectionResult,
    decode_data_url,
    detect_winner_from_image,
    increment_score,
    load_config,
    render_debug_image,
)


@dataclass
class MonitorState:
    phase: str = "in_match"
    game_set_count: int = 0
    results_count: int = 0
    winner_streak_winner: str = "unknown"
    winner_streak_count: int = 0
    clear_count: int = 0
    frames_since_game_set: int = 0
    last_increment_winner: str = ""
    last_increment_key: str = ""
    last_increment_value: str = ""
    last_increment_timestamp: int = 0
    frame_index: int = 0


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def fetch_obs_frame(client: obs.ReqClient, obs_config: dict):
    response = client.get_source_screenshot(
        obs_config["source_name"],
        obs_config["image_format"],
        int(obs_config["image_width"]),
        int(obs_config["image_height"]),
        int(obs_config["image_compression_quality"]),
    )
    return decode_data_url(response.image_data)


def save_state(path: Path, state: MonitorState, result: DetectionResult | None = None):
    payload = {"state": asdict(state)}
    if result is not None:
        payload["result"] = {
            "winner": result.winner,
            "is_game_set": result.is_game_set,
            "is_results_screen": result.is_results_screen,
            "left_score": result.left_metrics.score,
            "left_gold_ratio": result.left_metrics.gold_ratio,
            "left_blob_ratio": result.left_metrics.blob_ratio,
            "right_score": result.right_metrics.score,
            "right_gold_ratio": result.right_metrics.gold_ratio,
            "right_blob_ratio": result.right_metrics.blob_ratio,
            "scene_metrics": result.scene_metrics,
        }
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=4)
        handle.write("\n")


def reset_for_match(state: MonitorState):
    state.phase = "in_match"
    state.game_set_count = 0
    state.results_count = 0
    state.winner_streak_winner = "unknown"
    state.winner_streak_count = 0
    state.clear_count = 0
    state.frames_since_game_set = 0


def process_detection(
    state: MonitorState,
    result: DetectionResult,
    streamcontrol_path: Path,
    monitor_config: dict,
    write_scores: bool,
) -> str | None:
    if state.phase == "in_match":
        if result.is_game_set:
            state.game_set_count += 1
            if state.game_set_count >= int(monitor_config["consecutive_game_set_frames"]):
                state.phase = "awaiting_results"
                state.results_count = 0
                state.winner_streak_winner = "unknown"
                state.winner_streak_count = 0
                state.clear_count = 0
                state.frames_since_game_set = 0
                return "game_set_detected"
        else:
            state.game_set_count = 0
        return None

    if state.phase == "awaiting_results":
        if result.is_game_set:
            state.frames_since_game_set = 0
            state.results_count = 0
            state.winner_streak_winner = "unknown"
            state.winner_streak_count = 0
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
                state.results_count >= int(monitor_config["consecutive_results_frames"])
                and state.winner_streak_count >= int(monitor_config["consecutive_winner_frames"])
                and state.winner_streak_winner in {"left", "right"}
            ):
                winner = state.winner_streak_winner
                state.phase = "cooldown"
                state.clear_count = 0
                state.last_increment_winner = winner
                state.last_increment_timestamp = int(time.time())
                if write_scores:
                    key, value = increment_score(streamcontrol_path, winner)
                    state.last_increment_key = key
                    state.last_increment_value = value
                    return f"updated={key}:{value}"
                return f"detected={winner}"
        else:
            state.frames_since_game_set += 1
            state.results_count = 0
            state.winner_streak_winner = "unknown"
            state.winner_streak_count = 0

        if state.frames_since_game_set >= int(monitor_config["results_timeout_frames"]):
            reset_for_match(state)
            return "results_timeout"
        return None

    if state.phase == "cooldown":
        if not result.is_game_set and not result.is_results_screen:
            state.clear_count += 1
            if state.clear_count >= int(monitor_config["clear_match_frames"]):
                reset_for_match(state)
                return "rearmed"
        else:
            state.clear_count = 0
        return None

    reset_for_match(state)
    return "reset_unknown_phase"


def main():
    config_path = Path(__file__).with_name("obs_monitor_config.json")
    raw_config = load_json(config_path)
    obs_config = raw_config["obs"]
    monitor_config = raw_config["monitor"]
    paths_config = raw_config["paths"]
    verbose_frame_logs = bool(monitor_config.get("verbose_frame_logs", False))
    verbose_event_logs = bool(monitor_config.get("verbose_event_logs", False))
    save_debug_image = bool(monitor_config.get("save_debug_image", False))
    debug_image_interval_frames = int(monitor_config.get("debug_image_interval_frames", 1))
    save_state_json = bool(monitor_config.get("save_state_json", False))
    state_save_interval_frames = int(monitor_config.get("state_save_interval_frames", 1))

    base_dir = config_path.parent
    detector_config_path = resolve_path(base_dir, paths_config["detector_config_path"])
    streamcontrol_path = resolve_path(base_dir, paths_config["streamcontrol_path"])
    debug_output_path = resolve_path(base_dir, monitor_config["debug_output_path"])
    state_output_path = resolve_path(base_dir, monitor_config["state_output_path"])

    detector_config = load_config(detector_config_path)
    state = MonitorState()

    client = obs.ReqClient(
        host=obs_config["host"],
        port=int(obs_config["port"]),
        password=obs_config.get("password", ""),
        timeout=float(obs_config.get("timeout_seconds", 3.0)),
    )

    print(
        f"Connected to OBS WebSocket at {obs_config['host']}:{obs_config['port']} "
        f"source={obs_config['source_name']}"
    )
    print("Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            state.frame_index += 1
            image = fetch_obs_frame(client, obs_config)
            result = detect_winner_from_image(image, detector_config)
            action = process_detection(
                state=state,
                result=result,
                streamcontrol_path=streamcontrol_path,
                monitor_config=monitor_config,
                write_scores=bool(monitor_config["write_scores"]),
            )

            if verbose_frame_logs:
                print(
                    f"frame={state.frame_index} phase={state.phase} "
                    f"game_set={result.is_game_set}:{state.game_set_count} "
                    f"results={result.is_results_screen}:{state.results_count} "
                    f"winner={result.winner} streak={state.winner_streak_winner}:{state.winner_streak_count} "
                    f"clear={state.clear_count} timeout={state.frames_since_game_set}"
                )
            if action and (
                verbose_event_logs
                or action.startswith("updated=")
                or action.startswith("results_timeout")
            ):
                print(action, flush=True)

            if save_debug_image and state.frame_index % max(1, debug_image_interval_frames) == 0:
                debug_output_path.parent.mkdir(parents=True, exist_ok=True)
                debug_image = render_debug_image(
                    image,
                    result,
                    overlay_lines=[
                        f"phase={state.phase}",
                        f"game_set_count={state.game_set_count}",
                        f"results_count={state.results_count}",
                        f"winner_streak={state.winner_streak_winner}:{state.winner_streak_count}",
                        f"clear_count={state.clear_count}",
                        f"frames_since_game_set={state.frames_since_game_set}",
                    ],
                )
                cv2.imwrite(str(debug_output_path), debug_image)

            if save_state_json and state.frame_index % max(1, state_save_interval_frames) == 0:
                state_output_path.parent.mkdir(parents=True, exist_ok=True)
                save_state(state_output_path, state, result)

            time.sleep(float(monitor_config["poll_interval_ms"]) / 1000.0)
    except KeyboardInterrupt:
        print("Stopping OBS monitor.")
    except (OBSSDKError, OBSSDKRequestError, ConnectionError, TimeoutError) as exc:
        raise SystemExit(f"OBS monitor failed: {exc}") from exc
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()

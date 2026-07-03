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
    armed: bool = True
    streak_winner: str = "unknown"
    streak_count: int = 0
    clear_count: int = 0
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
            "left_score": result.left_metrics.score,
            "left_gold_ratio": result.left_metrics.gold_ratio,
            "left_blob_ratio": result.left_metrics.blob_ratio,
            "right_score": result.right_metrics.score,
            "right_gold_ratio": result.right_metrics.gold_ratio,
            "right_blob_ratio": result.right_metrics.blob_ratio,
        }
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=4)
        handle.write("\n")


def process_detection(
    state: MonitorState,
    result: DetectionResult,
    streamcontrol_path: Path,
    consecutive_winner_frames: int,
    clear_unknown_frames: int,
    write_scores: bool,
) -> str | None:
    winner = result.winner

    if state.armed:
        if winner in {"left", "right"}:
            if state.streak_winner == winner:
                state.streak_count += 1
            else:
                state.streak_winner = winner
                state.streak_count = 1
            state.clear_count = 0

            if state.streak_count >= consecutive_winner_frames:
                state.armed = False
                state.last_increment_winner = winner
                state.last_increment_timestamp = int(time.time())
                state.clear_count = 0
                if write_scores:
                    key, value = increment_score(streamcontrol_path, winner)
                    state.last_increment_key = key
                    state.last_increment_value = value
                    return f"updated={key}:{value}"
                return f"detected={winner}"
        else:
            state.streak_winner = "unknown"
            state.streak_count = 0
    else:
        if winner == "unknown":
            state.clear_count += 1
            if state.clear_count >= clear_unknown_frames:
                state.armed = True
                state.streak_winner = "unknown"
                state.streak_count = 0
                state.clear_count = 0
                return "rearmed"
        else:
            state.clear_count = 0

    return None


def main():
    config_path = Path(__file__).with_name("obs_monitor_config.json")
    raw_config = load_json(config_path)
    obs_config = raw_config["obs"]
    monitor_config = raw_config["monitor"]
    paths_config = raw_config["paths"]

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
    print("Press Ctrl+C to stop.")

    try:
        while True:
            state.frame_index += 1
            image = fetch_obs_frame(client, obs_config)
            result = detect_winner_from_image(image, detector_config)
            action = process_detection(
                state=state,
                result=result,
                streamcontrol_path=streamcontrol_path,
                consecutive_winner_frames=int(monitor_config["consecutive_winner_frames"]),
                clear_unknown_frames=int(monitor_config["clear_unknown_frames"]),
                write_scores=bool(monitor_config["write_scores"]),
            )

            print(
                f"frame={state.frame_index} winner={result.winner} armed={state.armed} "
                f"streak={state.streak_winner}:{state.streak_count} clear={state.clear_count}"
            )
            if action:
                print(action)

            if monitor_config.get("save_debug_image", True):
                debug_output_path.parent.mkdir(parents=True, exist_ok=True)
                debug_image = render_debug_image(
                    image,
                    result.left_bounds,
                    result.right_bounds,
                    result.left_metrics,
                    result.right_metrics,
                    result.winner,
                )
                cv2.imwrite(str(debug_output_path), debug_image)

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

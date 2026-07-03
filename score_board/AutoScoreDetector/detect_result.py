import argparse
import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        "Missing dependency. Install with: python -m pip install -r requirements.txt"
    ) from exc


@dataclass
class DetectionMetrics:
    gold_ratio: float
    blob_ratio: float
    score: float


@dataclass
class DetectionResult:
    winner: str
    left_metrics: DetectionMetrics
    right_metrics: DetectionMetrics
    left_bounds: tuple[int, int, int, int]
    right_bounds: tuple[int, int, int, int]


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_image_file(path: Path) -> np.ndarray:
    file_bytes = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return image


def decode_image_bytes(image_bytes: bytes) -> np.ndarray:
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode image bytes.")
    return image


def decode_data_url(data_url: str) -> np.ndarray:
    encoded = data_url
    if "," in data_url:
        _, encoded = data_url.split(",", 1)
    return decode_image_bytes(base64.b64decode(encoded))


def crop_normalized(image, roi):
    height, width = image.shape[:2]
    left, top, box_width, box_height = roi
    x1 = max(0, min(width, int(round(left * width))))
    y1 = max(0, min(height, int(round(top * height))))
    x2 = max(x1 + 1, min(width, int(round((left + box_width) * width))))
    y2 = max(y1 + 1, min(height, int(round((top + box_height) * height))))
    return image[y1:y2, x1:x2].copy(), (x1, y1, x2, y2)


def analyze_gold_presence(image, roi, config: dict) -> tuple[DetectionMetrics, np.ndarray, tuple[int, int, int, int]]:
    cropped, bounds = crop_normalized(image, roi)
    hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)

    lower = np.array(config["gold_hsv_lower"], dtype=np.uint8)
    upper = np.array(config["gold_hsv_upper"], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    roi_pixels = float(mask.shape[0] * mask.shape[1])
    gold_pixels = float(cv2.countNonZero(mask))
    gold_ratio = gold_pixels / roi_pixels if roi_pixels else 0.0

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    largest_blob = 0.0
    if contours:
        largest_blob = max(cv2.contourArea(contour) for contour in contours) / roi_pixels

    score = gold_ratio * 0.7 + largest_blob * 0.3
    return DetectionMetrics(gold_ratio=gold_ratio, blob_ratio=largest_blob, score=score), mask, bounds


def decide_winner(left_metrics: DetectionMetrics, right_metrics: DetectionMetrics, config: dict) -> str:
    min_gold_ratio = float(config["min_gold_ratio"])
    min_blob_ratio = float(config["min_blob_ratio"])
    winner_margin = float(config["winner_margin"])

    left_valid = left_metrics.gold_ratio >= min_gold_ratio and left_metrics.blob_ratio >= min_blob_ratio
    right_valid = right_metrics.gold_ratio >= min_gold_ratio and right_metrics.blob_ratio >= min_blob_ratio

    if left_valid and left_metrics.score >= right_metrics.score * winner_margin:
        return "left"
    if right_valid and right_metrics.score >= left_metrics.score * winner_margin:
        return "right"
    return "unknown"


def detect_winner_from_image(image: np.ndarray, config: dict) -> DetectionResult:
    left_metrics, _, left_bounds = analyze_gold_presence(image, config["left_rank_roi"], config)
    right_metrics, _, right_bounds = analyze_gold_presence(image, config["right_rank_roi"], config)
    winner = decide_winner(left_metrics, right_metrics, config)
    return DetectionResult(
        winner=winner,
        left_metrics=left_metrics,
        right_metrics=right_metrics,
        left_bounds=left_bounds,
        right_bounds=right_bounds,
    )


def render_debug_image(image, left_bounds, right_bounds, left_metrics, right_metrics, winner: str) -> np.ndarray:
    debug = image.copy()
    colors = {
        "left": (0, 215, 255),
        "right": (192, 192, 192),
        "neutral": (0, 255, 0),
    }

    cv2.rectangle(debug, left_bounds[:2], left_bounds[2:], colors["left"] if winner == "left" else colors["neutral"], 2)
    cv2.rectangle(debug, right_bounds[:2], right_bounds[2:], colors["right"] if winner == "right" else colors["neutral"], 2)

    cv2.putText(
        debug,
        f"L score={left_metrics.score:.4f} gold={left_metrics.gold_ratio:.4f}",
        (left_bounds[0], max(20, left_bounds[1] + 20)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        debug,
        f"R score={right_metrics.score:.4f} gold={right_metrics.gold_ratio:.4f}",
        (right_bounds[0], max(20, right_bounds[1] + 20)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        debug,
        f"winner={winner}",
        (20, max(30, image.shape[0] - 20)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return debug


def read_streamcontrol(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_streamcontrol(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=4)
        handle.write("\n")


def increment_score(streamcontrol_path: Path, winner: str) -> tuple[str, str]:
    if winner not in {"left", "right"}:
        raise ValueError("Winner must be left or right.")

    payload = read_streamcontrol(streamcontrol_path)
    key = "pScore1" if winner == "left" else "pScore2"

    try:
        current_score = int(str(payload.get(key, "0")).strip() or "0")
    except ValueError:
        current_score = 0

    payload[key] = str(current_score + 1)
    payload["timestamp"] = str(int(time.time()))
    write_streamcontrol(streamcontrol_path, payload)
    return key, payload[key]


def main():
    parser = argparse.ArgumentParser(description="Detect the winner side from a Smash Ultimate results screen.")
    parser.add_argument("--image", required=True, help="Path to a screenshot or captured frame.")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.json")),
        help="Path to detector config JSON.",
    )
    parser.add_argument(
        "--streamcontrol",
        default=str(Path(__file__).resolve().parent.parent / "streamcontrol.json"),
        help="Path to streamcontrol.json.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Increment the matching score in streamcontrol.json when left or right is detected.",
    )
    parser.add_argument(
        "--debug-output",
        help="Optional path for a debug image with ROI overlays and metrics.",
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    config_path = Path(args.config)
    streamcontrol_path = Path(args.streamcontrol)

    if not image_path.exists():
        raise SystemExit(f"Image not found: {image_path}")
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")

    config = load_config(config_path)
    try:
        image = read_image_file(image_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    result = detect_winner_from_image(image, config)

    print(f"winner={result.winner}")
    print(
        f"left_score={result.left_metrics.score:.6f} "
        f"left_gold_ratio={result.left_metrics.gold_ratio:.6f} "
        f"left_blob_ratio={result.left_metrics.blob_ratio:.6f}"
    )
    print(
        f"right_score={result.right_metrics.score:.6f} "
        f"right_gold_ratio={result.right_metrics.gold_ratio:.6f} "
        f"right_blob_ratio={result.right_metrics.blob_ratio:.6f}"
    )

    if args.debug_output:
        debug_path = Path(args.debug_output)
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_image = render_debug_image(
            image,
            result.left_bounds,
            result.right_bounds,
            result.left_metrics,
            result.right_metrics,
            result.winner,
        )
        cv2.imwrite(str(debug_path), debug_image)
        print(f"debug_output={debug_path}")

    if args.write:
        if result.winner == "unknown":
            raise SystemExit("Detection was unknown; refusing to update streamcontrol.json.")
        key, value = increment_score(streamcontrol_path, result.winner)
        print(f"updated={key}:{value}")


if __name__ == "__main__":
    main()

import argparse
import base64
import json
import os
import tempfile
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

_TEMPLATE_CACHE: dict[tuple[str, int, int], np.ndarray] = {}
_MASK_TEMPLATE_CACHE: dict[str, np.ndarray] = {}


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
    scene_flags: dict[str, bool]
    scene_metrics: dict[str, float]
    scene_bounds: dict[str, tuple[int, int, int, int]]

    @property
    def is_game_set(self) -> bool:
        return self.scene_flags.get("game_set", False)

    @property
    def is_results_screen(self) -> bool:
        return self.scene_flags.get("results_screen", False)


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    def resolve_template_value(raw_value):
        if isinstance(raw_value, str):
            template = Path(raw_value)
            return str(template if template.is_absolute() else (path.parent / template).resolve())
        if isinstance(raw_value, list):
            return [resolve_template_value(item) for item in raw_value]
        return raw_value

    for key in (
        "game_set_text_template_paths",
    ):
        if key in payload:
            payload[key] = resolve_template_value(payload[key])
    return payload


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


def crop_normalized(image: np.ndarray, roi: list[float] | tuple[float, float, float, float]):
    height, width = image.shape[:2]
    left, top, box_width, box_height = roi
    x1 = max(0, min(width, int(round(left * width))))
    y1 = max(0, min(height, int(round(top * height))))
    x2 = max(x1 + 1, min(width, int(round((left + box_width) * width))))
    y2 = max(y1 + 1, min(height, int(round((top + box_height) * height))))
    return image[y1:y2, x1:x2].copy(), (x1, y1, x2, y2)


def build_hsv_mask(hsv_image: np.ndarray, hsv_ranges: list[list[list[int]]]) -> np.ndarray:
    mask = np.zeros(hsv_image.shape[:2], dtype=np.uint8)
    for lower_raw, upper_raw in hsv_ranges:
        lower = np.array(lower_raw, dtype=np.uint8)
        upper = np.array(upper_raw, dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv_image, lower, upper))
    return mask


def analyze_color_ratio(
    image: np.ndarray,
    roi: list[float] | tuple[float, float, float, float],
    hsv_ranges: list[list[list[int]]],
    kernel_size: int = 3,
) -> tuple[float, np.ndarray, tuple[int, int, int, int]]:
    cropped, bounds = crop_normalized(image, roi)
    hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
    mask = build_hsv_mask(hsv, hsv_ranges)

    if kernel_size > 1:
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    roi_pixels = float(mask.shape[0] * mask.shape[1])
    ratio = float(cv2.countNonZero(mask)) / roi_pixels if roi_pixels else 0.0
    return ratio, mask, bounds


def analyze_dark_ratio(
    image: np.ndarray,
    roi: list[float] | tuple[float, float, float, float],
    value_threshold: int,
) -> tuple[float, np.ndarray, tuple[int, int, int, int]]:
    cropped, bounds = crop_normalized(image, roi)
    hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
    value_channel = hsv[:, :, 2]
    mask = np.where(value_channel <= value_threshold, 255, 0).astype(np.uint8)
    roi_pixels = float(mask.shape[0] * mask.shape[1])
    ratio = float(cv2.countNonZero(mask)) / roi_pixels if roi_pixels else 0.0
    return ratio, mask, bounds


def analyze_gold_presence(
    image: np.ndarray,
    roi: list[float] | tuple[float, float, float, float],
    config: dict,
) -> tuple[DetectionMetrics, np.ndarray, tuple[int, int, int, int]]:
    gold_ranges = [[config["gold_hsv_lower"], config["gold_hsv_upper"]]]
    gold_ratio, mask, bounds = analyze_color_ratio(image, roi, gold_ranges)

    roi_pixels = float(mask.shape[0] * mask.shape[1])
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    largest_blob = 0.0
    if contours:
        largest_blob = max(cv2.contourArea(contour) for contour in contours) / roi_pixels

    score = gold_ratio * 0.7 + largest_blob * 0.3
    return DetectionMetrics(gold_ratio=gold_ratio, blob_ratio=largest_blob, score=score), mask, bounds


def load_mask_template(template_path: str) -> np.ndarray:
    cached = _MASK_TEMPLATE_CACHE.get(template_path)
    if cached is not None:
        return cached

    template_image = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template_image is None:
        raise ValueError(f"Could not read game set text template: {template_path}")

    mask = np.where(template_image > 0, 255, 0).astype(np.uint8)
    _MASK_TEMPLATE_CACHE[template_path] = mask
    return mask


def load_edge_template(template_path: str, canny_low: int, canny_high: int) -> np.ndarray:
    cache_key = (template_path, canny_low, canny_high)
    cached = _TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    template_image = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template_image is None:
        raise ValueError(f"Could not read game set template: {template_path}")

    template_edges = cv2.Canny(template_image, canny_low, canny_high)
    _TEMPLATE_CACHE[cache_key] = template_edges
    return template_edges


def load_edge_templates(template_paths: list[str], canny_low: int, canny_high: int) -> list[np.ndarray]:
    return [load_edge_template(template_path, canny_low, canny_high) for template_path in template_paths]


def analyze_game_set_text_mask(
    image: np.ndarray,
    roi: list[float] | tuple[float, float, float, float],
    reference_size: list[int] | tuple[int, int],
    hsv_ranges: list[list[list[int]]],
    kernel_size: int,
) -> tuple[np.ndarray, tuple[int, int, int, int], tuple[int, int, int, int]]:
    cropped, bounds = crop_normalized(image, roi)
    hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
    mask = build_hsv_mask(hsv, hsv_ranges)

    if kernel_size > 1:
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    resized_mask = cv2.resize(
        mask,
        (int(reference_size[0]), int(reference_size[1])),
        interpolation=cv2.INTER_NEAREST,
    )

    non_zero = cv2.findNonZero(resized_mask)
    if non_zero is None:
        return resized_mask, bounds, bounds

    x, y, w, h = cv2.boundingRect(non_zero)
    scale_x = (bounds[2] - bounds[0]) / float(reference_size[0])
    scale_y = (bounds[3] - bounds[1]) / float(reference_size[1])
    text_bounds = (
        int(round(bounds[0] + x * scale_x)),
        int(round(bounds[1] + y * scale_y)),
        int(round(bounds[0] + (x + w) * scale_x)),
        int(round(bounds[1] + (y + h) * scale_y)),
    )
    return resized_mask, bounds, text_bounds


def compare_binary_masks(mask: np.ndarray, template_mask: np.ndarray) -> tuple[float, float]:
    mask_binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    template_binary = np.where(template_mask > 0, 255, 0).astype(np.uint8)

    intersection = cv2.countNonZero(cv2.bitwise_and(mask_binary, template_binary))
    union = cv2.countNonZero(cv2.bitwise_or(mask_binary, template_binary))
    iou = intersection / union if union else 0.0

    correlation = float(
        cv2.matchTemplate(
            mask_binary.astype(np.float32),
            template_binary.astype(np.float32),
            cv2.TM_CCOEFF_NORMED,
        )[0][0]
    )
    return iou, correlation


def detect_scene_flags(image: np.ndarray, config: dict) -> tuple[dict[str, bool], dict[str, float], dict[str, tuple[int, int, int, int]]]:
    scene_metrics: dict[str, float] = {}
    scene_bounds: dict[str, tuple[int, int, int, int]] = {}

    game_set_mask, game_set_search_bounds, game_set_match_bounds = analyze_game_set_text_mask(
        image,
        config["game_set_text_roi"],
        config["game_set_text_reference_size"],
        config["game_set_text_hsv_ranges"],
        int(config.get("game_set_text_kernel_size", 3)),
    )
    game_set_ratio = float(cv2.countNonZero(game_set_mask)) / float(game_set_mask.shape[0] * game_set_mask.shape[1])
    best_game_set_iou = 0.0
    best_game_set_correlation = -1.0
    best_game_set_search_bounds = (0, 0, 1, 1)
    best_game_set_match_bounds = (0, 0, 1, 1)
    for template_path in list(config["game_set_text_template_paths"]):
        template_mask = load_mask_template(template_path)
        game_set_iou, game_set_correlation = compare_binary_masks(game_set_mask, template_mask)
        if game_set_correlation > best_game_set_correlation:
            best_game_set_iou = game_set_iou
            best_game_set_correlation = game_set_correlation
            best_game_set_search_bounds = game_set_search_bounds
            best_game_set_match_bounds = game_set_match_bounds

    scene_metrics["game_set_mask_ratio"] = game_set_ratio
    scene_metrics["game_set_iou"] = best_game_set_iou
    scene_metrics["game_set_score"] = best_game_set_correlation
    scene_bounds["game_set_search"] = best_game_set_search_bounds
    scene_bounds["game_set_match"] = best_game_set_match_bounds

    results_kernel_size = int(config.get("scene_kernel_size", 3))
    dark_value_threshold = int(config.get("results_dark_value_threshold", 90))
    left_gold_ratio, _, left_digit_bounds = analyze_color_ratio(
        image,
        config["results_left_digit_roi"],
        [[config["gold_hsv_lower"], config["gold_hsv_upper"]]],
        results_kernel_size,
    )
    right_gold_ratio, _, right_digit_bounds = analyze_color_ratio(
        image,
        config["results_right_digit_roi"],
        [[config["gold_hsv_lower"], config["gold_hsv_upper"]]],
        results_kernel_size,
    )
    left_white_ratio, _, _ = analyze_color_ratio(
        image,
        config["results_left_digit_roi"],
        config["results_white_hsv_ranges"],
        results_kernel_size,
    )
    right_white_ratio, _, _ = analyze_color_ratio(
        image,
        config["results_right_digit_roi"],
        config["results_white_hsv_ranges"],
        results_kernel_size,
    )
    left_dark_ratio, _, _ = analyze_dark_ratio(
        image,
        config["results_left_digit_roi"],
        dark_value_threshold,
    )
    right_dark_ratio, _, _ = analyze_dark_ratio(
        image,
        config["results_right_digit_roi"],
        dark_value_threshold,
    )

    scene_metrics["results_left_gold_ratio"] = left_gold_ratio
    scene_metrics["results_right_gold_ratio"] = right_gold_ratio
    scene_metrics["results_left_white_ratio"] = left_white_ratio
    scene_metrics["results_right_white_ratio"] = right_white_ratio
    scene_metrics["results_left_dark_ratio"] = left_dark_ratio
    scene_metrics["results_right_dark_ratio"] = right_dark_ratio
    scene_bounds["results_left_digit"] = left_digit_bounds
    scene_bounds["results_right_digit"] = right_digit_bounds

    left_pair_valid = (
        left_gold_ratio >= float(config["results_left_winner_gold_min_ratio"])
        and left_dark_ratio >= float(config["results_left_winner_left_dark_min_ratio"])
        and right_dark_ratio >= float(config["results_left_winner_right_dark_min_ratio"])
        and right_white_ratio >= float(config["results_left_winner_right_white_min_ratio"])
        and right_gold_ratio <= float(config["results_left_winner_right_gold_max_ratio"])
    )
    right_pair_valid = (
        right_gold_ratio >= float(config["results_right_winner_right_gold_min_ratio"])
        and left_dark_ratio >= float(config["results_right_winner_left_dark_min_ratio"])
        and right_dark_ratio >= float(config["results_right_winner_right_dark_min_ratio"])
        and left_white_ratio >= float(config["results_right_winner_left_white_min_ratio"])
        and left_gold_ratio <= float(config["results_right_winner_left_gold_max_ratio"])
    )

    left_pair_score = (
        max(0.0, left_gold_ratio - float(config["results_left_winner_gold_min_ratio"]))
        + max(0.0, left_dark_ratio - float(config["results_left_winner_left_dark_min_ratio"]))
        + max(0.0, right_dark_ratio - float(config["results_left_winner_right_dark_min_ratio"]))
        + max(0.0, right_white_ratio - float(config["results_left_winner_right_white_min_ratio"]))
        + max(0.0, float(config["results_left_winner_right_gold_max_ratio"]) - right_gold_ratio)
    )
    right_pair_score = (
        max(0.0, right_gold_ratio - float(config["results_right_winner_right_gold_min_ratio"]))
        + max(0.0, left_dark_ratio - float(config["results_right_winner_left_dark_min_ratio"]))
        + max(0.0, right_dark_ratio - float(config["results_right_winner_right_dark_min_ratio"]))
        + max(0.0, left_white_ratio - float(config["results_right_winner_left_white_min_ratio"]))
        + max(0.0, float(config["results_right_winner_left_gold_max_ratio"]) - left_gold_ratio)
    )
    scene_metrics["left_result_pair_score"] = left_pair_score
    scene_metrics["right_result_pair_score"] = right_pair_score

    game_set = (
        game_set_ratio >= float(config["game_set_text_min_ratio"])
        and best_game_set_iou >= float(config["game_set_text_min_iou"])
        and best_game_set_correlation >= float(config["game_set_text_min_score"])
    )
    results_screen = left_pair_valid or right_pair_valid
    scene_metrics["results_winner_side"] = 1.0 if left_pair_valid else 2.0 if right_pair_valid else 0.0

    scene_flags = {
        "game_set": game_set,
        "results_screen": results_screen,
    }
    return scene_flags, scene_metrics, scene_bounds


def detect_winner_from_image(image: np.ndarray, config: dict) -> DetectionResult:
    left_metrics, _, left_bounds = analyze_gold_presence(image, config["left_rank_roi"], config)
    right_metrics, _, right_bounds = analyze_gold_presence(image, config["right_rank_roi"], config)
    scene_flags, scene_metrics, scene_bounds = detect_scene_flags(image, config)
    winner = "left" if scene_flags["results_screen"] and scene_metrics.get("results_winner_side") == 1.0 else "right" if scene_flags["results_screen"] and scene_metrics.get("results_winner_side") == 2.0 else "unknown"
    return DetectionResult(
        winner=winner,
        left_metrics=left_metrics,
        right_metrics=right_metrics,
        left_bounds=left_bounds,
        right_bounds=right_bounds,
        scene_flags=scene_flags,
        scene_metrics=scene_metrics,
        scene_bounds=scene_bounds,
    )


def render_debug_image(image: np.ndarray, result: DetectionResult, overlay_lines: list[str] | None = None) -> np.ndarray:
    debug = image.copy()
    winner_colors = {
        "left": (0, 215, 255),
        "right": (192, 192, 192),
        "neutral": (0, 255, 0),
    }

    cv2.rectangle(
        debug,
        result.left_bounds[:2],
        result.left_bounds[2:],
        winner_colors["left"] if result.winner == "left" else winner_colors["neutral"],
        2,
    )
    cv2.rectangle(
        debug,
        result.right_bounds[:2],
        result.right_bounds[2:],
        winner_colors["right"] if result.winner == "right" else winner_colors["neutral"],
        2,
    )

    scene_box_colors = {
        "game_set_search": (0, 180, 255),
        "game_set_match": (0, 255, 255),
        "results_left_digit": (0, 255, 0),
        "results_right_digit": (255, 128, 0),
    }
    for key, bounds in result.scene_bounds.items():
        cv2.rectangle(debug, bounds[:2], bounds[2:], scene_box_colors.get(key, (255, 255, 255)), 2)

    lines = [
        f"winner={result.winner}",
        (
            "left digit="
            f"G:{result.scene_metrics['results_left_gold_ratio']:.3f} "
            f"W:{result.scene_metrics['results_left_white_ratio']:.3f} "
            f"D:{result.scene_metrics['results_left_dark_ratio']:.3f}"
        ),
        (
            "right digit="
            f"G:{result.scene_metrics['results_right_gold_ratio']:.3f} "
            f"W:{result.scene_metrics['results_right_white_ratio']:.3f} "
            f"D:{result.scene_metrics['results_right_dark_ratio']:.3f}"
        ),
        (
            "game_set="
            f"{result.is_game_set} "
            f"score={result.scene_metrics['game_set_score']:.3f} "
            f"iou={result.scene_metrics['game_set_iou']:.3f} "
            f"ratio={result.scene_metrics['game_set_mask_ratio']:.3f}"
        ),
        (
            "results="
            f"{result.is_results_screen} "
            f"pair=({result.scene_metrics['left_result_pair_score']:.3f},"
            f"{result.scene_metrics['right_result_pair_score']:.3f})"
        ),
    ]
    if overlay_lines:
        lines.extend(overlay_lines)

    y = 24
    for line in lines:
        cv2.putText(
            debug,
            line,
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        y += 24

    return debug


def read_streamcontrol(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_streamcontrol(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="streamcontrol-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def next_trigger_value(payload: dict) -> str:
    now_value = int(time.time() * 1000)
    existing_values: list[int] = []
    for key in ("timestamp", "sponsorts"):
        raw_value = str(payload.get(key, "")).strip()
        if raw_value.isdigit():
            existing_values.append(int(raw_value))

    baseline = max(existing_values) if existing_values else 0
    return str(max(now_value, baseline + 1))


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
    trigger_value = next_trigger_value(payload)
    payload["timestamp"] = trigger_value
    payload["sponsorts"] = trigger_value
    write_streamcontrol(streamcontrol_path, payload)
    return key, payload[key]


def main():
    parser = argparse.ArgumentParser(
        description="Detect the winner side from a Smash Ultimate result flow frame."
    )
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
        help="Increment the matching score in streamcontrol.json when a result winner is detected.",
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
        f"game_set={result.is_game_set} "
        f"game_set_score={result.scene_metrics['game_set_score']:.6f} "
        f"game_set_iou={result.scene_metrics['game_set_iou']:.6f} "
        f"game_set_mask_ratio={result.scene_metrics['game_set_mask_ratio']:.6f}"
    )
    print(
        "results_screen="
        f"{result.is_results_screen} "
        f"left_gold_ratio={result.scene_metrics['results_left_gold_ratio']:.6f} "
        f"left_white_ratio={result.scene_metrics['results_left_white_ratio']:.6f} "
        f"left_dark_ratio={result.scene_metrics['results_left_dark_ratio']:.6f} "
        f"right_gold_ratio={result.scene_metrics['results_right_gold_ratio']:.6f} "
        f"right_white_ratio={result.scene_metrics['results_right_white_ratio']:.6f} "
        f"right_dark_ratio={result.scene_metrics['results_right_dark_ratio']:.6f} "
        f"left_pair_score={result.scene_metrics['left_result_pair_score']:.6f} "
        f"right_pair_score={result.scene_metrics['right_result_pair_score']:.6f}"
    )
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
        debug_image = render_debug_image(image, result)
        cv2.imwrite(str(debug_path), debug_image)
        print(f"debug_output={debug_path}")

    if args.write:
        if not result.is_results_screen or result.winner == "unknown":
            raise SystemExit("Detection was not a confirmed result winner; refusing to update streamcontrol.json.")
        key, value = increment_score(streamcontrol_path, result.winner)
        print(f"updated={key}:{value}")


if __name__ == "__main__":
    main()

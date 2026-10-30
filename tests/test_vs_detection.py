import unittest

import cv2
import numpy as np

from score_board.AutoScoreDetector.detect_result import analyze_vs_scene


VS_CONFIG = {
    "vs_text_roi": [0.32, 0.22, 0.36, 0.60],
    "vs_text_hsv_ranges": [[[0, 0, 190], [179, 75, 255]]],
    "vs_text_kernel_size": 1,
    "vs_text_min_ratio": 0.05,
    "vs_text_max_ratio": 0.80,
    "vs_text_min_blob_ratio": 0.015,
    "vs_text_max_blob_ratio": 0.75,
    "vs_text_max_shape_density": 0.90,
    "vs_left_context_roi": [0.02, 0.02, 0.40, 0.96],
    "vs_right_context_roi": [0.58, 0.02, 0.40, 0.96],
    "vs_red_hsv_ranges": [
        [[0, 70, 45], [14, 255, 255]],
        [[165, 70, 45], [179, 255, 255]],
    ],
    "vs_blue_hsv_ranges": [[[88, 55, 40], [138, 255, 255]]],
    "vs_context_kernel_size": 1,
    "vs_left_red_min_ratio": 0.10,
    "vs_right_blue_min_ratio": 0.10,
    "vs_context_min_dominance": 1.10,
}


def vs_frame(*, colored_context: bool = True, solid_text: bool = False):
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    if colored_context:
        image[:, :200] = (0, 0, 180)
        image[:, 200:] = (200, 35, 0)
    else:
        image[:] = (55, 55, 55)
    if solid_text:
        image[44:164, 128:272] = (255, 255, 255)
    else:
        cv2.putText(
            image,
            "VS",
            (138, 130),
            cv2.FONT_HERSHEY_SIMPLEX,
            2.4,
            (255, 255, 255),
            9,
            cv2.LINE_AA,
        )
    return image


class VsDetectionTests(unittest.TestCase):
    def test_separate_v_and_s_contours_are_combined(self):
        config = dict(VS_CONFIG)
        config["vs_text_min_blob_ratio"] = 0.09

        detected, metrics, _bounds = analyze_vs_scene(vs_frame(), config)

        self.assertTrue(detected)
        self.assertGreaterEqual(metrics["vs_text_blob_ratio"], 0.09)

    def test_vs_requires_red_left_and_blue_right_context(self):
        detected, metrics, _bounds = analyze_vs_scene(vs_frame(), VS_CONFIG)
        self.assertTrue(detected)
        self.assertEqual(metrics["vs_shape_valid"], 1.0)
        self.assertEqual(metrics["vs_context_valid"], 1.0)

        detected, metrics, _bounds = analyze_vs_scene(
            vs_frame(colored_context=False), VS_CONFIG
        )
        self.assertFalse(detected)
        self.assertEqual(metrics["vs_shape_valid"], 1.0)
        self.assertEqual(metrics["vs_context_valid"], 0.0)

    def test_solid_white_panel_is_not_accepted_as_vs_letters(self):
        detected, metrics, _bounds = analyze_vs_scene(
            vs_frame(solid_text=True), VS_CONFIG
        )
        self.assertFalse(detected)
        self.assertEqual(metrics["vs_shape_valid"], 0.0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.core import generate_threshold_pair_channels
from quick_sdf_blender.packing import PackingChannelSpec, PackingSource, pack_rgba16
from quick_sdf_blender.review import (
    review_current,
    review_onion_difference,
    review_threshold_rgba16,
    review_violation_heatmap,
)


ANGLES = np.asarray([-90.0, -45.0, 0.0, 45.0, 90.0])


def _review_texture(
    right: np.ndarray,
    right_angles: np.ndarray,
    left: np.ndarray,
    left_angles: np.ndarray,
) -> np.ndarray:
    channels = generate_threshold_pair_channels(
        right, right_angles, left, left_angles
    )
    return pack_rgba16(
        {
            PackingSource.RIGHT_THRESHOLD: channels[..., 0],
            PackingSource.LEFT_THRESHOLD: channels[..., 1],
        },
        (
            PackingChannelSpec(PackingSource.RIGHT_THRESHOLD),
            PackingChannelSpec(PackingSource.LEFT_THRESHOLD),
            PackingChannelSpec(PackingSource.CONSTANT, constant_value=0.0),
            PackingChannelSpec(PackingSource.CONSTANT, constant_value=1.0),
        ),
    )


class CurrentReviewTests(unittest.TestCase):
    def test_returns_nearest_mask_as_float_rgba(self) -> None:
        masks = np.zeros((5, 1, 2), dtype=np.uint8)
        masks[3, 0] = (255, 0)
        image = review_current(masks, ANGLES, 43.0)
        self.assertEqual(image.dtype, np.float32)
        self.assertEqual(image.shape, (1, 2, 4))
        np.testing.assert_array_equal(image[0, :, 0], [1.0, 0.0])
        np.testing.assert_array_equal(image[..., 3], 1.0)

    def test_nearest_tie_prefers_front(self) -> None:
        masks = np.zeros((5, 1, 1), dtype=bool)
        masks[2] = True
        self.assertEqual(float(review_current(masks, ANGLES, 22.5)[0, 0, 0]), 1.0)


class OnionReviewTests(unittest.TestCase):
    def test_marks_inward_and_outward_differences(self) -> None:
        masks = np.zeros((5, 1, 4), dtype=bool)
        # Positive side: pixel 1 changes at 45 (cyan), pixel 2 at 90 (magenta).
        masks[2:, 0, 3] = True
        masks[3:, 0, 1] = True
        masks[4:, 0, 2] = True
        image = review_onion_difference(masks, ANGLES, 45.0)
        np.testing.assert_array_equal(image[0, 0, :3], [0.0, 0.0, 0.0])
        np.testing.assert_array_equal(image[0, 1, :3], [0.0, 1.0, 1.0])
        np.testing.assert_array_equal(image[0, 2, :3], [1.0, 0.0, 1.0])
        np.testing.assert_array_equal(image[0, 3, :3], [1.0, 1.0, 1.0])

    def test_negative_side_uses_absolute_angle_order(self) -> None:
        masks = np.zeros((5, 1, 2), dtype=bool)
        masks[0:2, 0, 0] = True  # new at -45 relative to zero
        masks[0, 0, 1] = True  # next outward difference at -90
        image = review_onion_difference(masks, ANGLES, -45.0)
        np.testing.assert_array_equal(image[0, 0, :3], [0.0, 1.0, 1.0])
        np.testing.assert_array_equal(image[0, 1, :3], [1.0, 0.0, 1.0])


class ThresholdReviewTests(unittest.TestCase):
    def _texture(self) -> np.ndarray:
        texture = np.zeros((1, 4, 4), dtype=np.uint16)
        texture[..., 2] = 0
        texture[..., 3] = 65535
        texture[0, :, 0] = [0, 65535, 10000, 50000]
        texture[0, :, 1] = [65535, 0, 50000, 10000]
        return texture

    def test_positive_uses_red_with_liltoon_equation(self) -> None:
        image = review_threshold_rgba16(self._texture(), 30.0)
        np.testing.assert_array_equal(image[0, :, 0], [0.0, 1.0, 0.0, 1.0])
        np.testing.assert_array_equal(image[..., 3], 1.0)

    def test_negative_uses_green(self) -> None:
        image = review_threshold_rgba16(self._texture(), -30.0)
        np.testing.assert_array_equal(image[0, :, 0], [1.0, 0.0, 1.0, 0.0])

    def test_channel_endpoints_are_ordinary_liltoon_values(self) -> None:
        texture = np.zeros((1, 2, 4), dtype=np.uint16)
        texture[..., 3] = 65535
        texture[0, :, 0] = [0, 65535]
        np.testing.assert_array_equal(
            review_threshold_rgba16(texture, 0.0)[0, :, 0], [0.0, 1.0]
        )
        np.testing.assert_array_equal(
            review_threshold_rgba16(texture, 90.0)[0, :, 0], [1.0, 1.0]
        )

    def test_midpoint_matches_default_liltoon_border(self) -> None:
        texture = np.zeros((1, 2, 4), dtype=np.uint16)
        texture[..., 3] = 65535
        texture[0, :, 0] = [32767, 32768]
        np.testing.assert_array_equal(
            review_threshold_rgba16(texture, 45.0)[0, :, 0], [0.0, 1.0]
        )

    def test_eight_key_masks_round_trip_through_liltoon_equation(self) -> None:
        angles = np.arange(8, dtype=np.float64) * (90.0 / 7.0)
        y, x = np.indices((9, 11))
        right_transition = (x + 2 * y) % 8
        left_transition = (2 * x + y + 1) % 8
        right = np.arange(8)[:, None, None] >= right_transition[None, ...]
        left = np.arange(8)[:, None, None] >= left_transition[None, ...]
        texture = _review_texture(right, angles, left, angles)
        for index, angle in enumerate(angles):
            right_preview = review_threshold_rgba16(texture, float(angle))[..., 0] >= 0.5
            np.testing.assert_array_equal(right_preview, right[index])
            if index:
                left_preview = review_threshold_rgba16(texture, -float(angle))[..., 0] >= 0.5
                np.testing.assert_array_equal(left_preview, left[index])

    def test_requires_uint16_rgba(self) -> None:
        with self.assertRaises(TypeError):
            review_threshold_rgba16(np.zeros((1, 1, 4), np.float32), 0.0)
        with self.assertRaises(ValueError):
            review_threshold_rgba16(np.zeros((1, 1, 3), np.uint16), 0.0)


class ViolationReviewTests(unittest.TestCase):
    def test_colors_positive_negative_and_shared_violations(self) -> None:
        masks = np.ones((5, 1, 3), dtype=bool)
        masks[3, 0, 0] = False  # positive only
        masks[1, 0, 1] = False  # negative only
        masks[3, 0, 2] = False
        masks[1, 0, 2] = False  # both
        image = review_violation_heatmap(masks, ANGLES)
        np.testing.assert_array_equal(image[0, 0], [1.0, 0.0, 0.0, 1.0])
        np.testing.assert_array_equal(image[0, 1], [0.0, 0.0, 1.0, 1.0])
        np.testing.assert_array_equal(image[0, 2], [1.0, 0.0, 1.0, 1.0])

    def test_valid_stack_is_opaque_black(self) -> None:
        masks = np.zeros((5, 2, 2), dtype=bool)
        image = review_violation_heatmap(masks, ANGLES)
        np.testing.assert_array_equal(image[..., :3], 0.0)
        np.testing.assert_array_equal(image[..., 3], 1.0)

    def test_requires_one_zero_angle(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one 0"):
            review_violation_heatmap(
                np.zeros((3, 1, 1), dtype=bool), [-90.0, -45.0, 90.0]
            )


class InputValidationTests(unittest.TestCase):
    def test_rejects_bad_review_angle_and_stack_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "-90..90"):
            review_current(np.zeros((1, 1, 1), bool), [0.0], 91.0)
        with self.assertRaisesRegex(ValueError, "NxHxW"):
            review_current(np.zeros((1, 1), bool), [0.0], 0.0)


if __name__ == "__main__":
    unittest.main()

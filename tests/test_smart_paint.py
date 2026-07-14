from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.core import (
    generate_threshold_pair_channels,
    validate_side_monotonic,
)
from quick_sdf_blender.smart_paint import (
    affected_key_indices,
    apply_smart_stroke,
    apply_smart_transition_patch,
    apply_smart_transitions,
    gray8_transition_masks,
    iter_smart_transition_patches,
)


ANGLES = np.arange(0.0, 91.0, 15.0)


class SmartPaintTests(unittest.TestCase):
    def test_ranges_match_artist_light_shadow_rules(self):
        self.assertEqual(affected_key_indices(ANGLES, 3, True), (3, 4, 5, 6))
        self.assertEqual(affected_key_indices(ANGLES, 3, False), (0, 1, 2, 3))

    def test_random_strokes_cannot_break_monotonicity(self):
        rng = np.random.default_rng(42)
        masks = np.zeros((len(ANGLES), 12, 9), dtype=np.bool_)
        coverage = np.zeros_like(masks)
        for _ in range(250):
            active = int(rng.integers(0, len(ANGLES)))
            light = bool(rng.integers(0, 2))
            footprint = rng.random(masks.shape[1:]) > 0.92
            result = apply_smart_stroke(
                masks, coverage, ANGLES, active, footprint, paint_light=light
            )
            masks, coverage = result.masks, result.coverage
            self.assertTrue(validate_side_monotonic(masks, ANGLES).is_valid)

    def test_coverage_is_set_on_every_affected_key(self):
        masks = np.zeros((len(ANGLES), 4, 5), dtype=np.bool_)
        coverage = np.zeros_like(masks)
        footprint = np.zeros((4, 5), dtype=np.bool_)
        footprint[1:3, 2] = True
        result = apply_smart_stroke(
            masks, coverage, ANGLES, 4, footprint, paint_light=True
        )
        self.assertEqual(result.affected_indices, (4, 5, 6))
        self.assertTrue(np.all(result.coverage[4:, 1:3, 2]))
        self.assertFalse(np.any(result.coverage[:4]))

    def test_soft_native_stroke_propagates_only_threshold_crossings(self):
        masks = np.zeros((len(ANGLES), 2, 3), dtype=np.bool_)
        masks[:, 0, 1] = True
        coverage = np.zeros_like(masks)
        touched = np.zeros((2, 3), dtype=np.bool_)
        touched[0, :] = True
        became_light = np.zeros_like(touched)
        became_light[0, 0] = True
        became_shadow = np.zeros_like(touched)
        became_shadow[0, 1] = True

        result = apply_smart_transitions(
            masks,
            coverage,
            ANGLES,
            3,
            touched,
            became_light,
            became_shadow,
        )

        self.assertTrue(np.all(result.masks[3:, 0, 0]))
        self.assertFalse(np.any(result.masks[:4, 0, 1]))
        self.assertFalse(np.any(result.footprints[:3, 0, 0]))
        self.assertFalse(np.any(result.footprints[4:, 0, 1]))
        self.assertTrue(result.coverage[3, 0, 2])
        self.assertFalse(np.any(result.coverage[np.arange(len(ANGLES)) != 3, 0, 2]))
        self.assertTrue(validate_side_monotonic(result.masks, ANGLES).is_valid)

    def test_transition_pixels_must_have_been_touched(self):
        masks = np.zeros((len(ANGLES), 1, 1), dtype=np.bool_)
        coverage = np.zeros_like(masks)
        with self.assertRaisesRegex(ValueError, "part of the touched area"):
            apply_smart_transitions(
                masks,
                coverage,
                ANGLES,
                2,
                np.zeros((1, 1), dtype=np.bool_),
                np.ones((1, 1), dtype=np.bool_),
                np.zeros((1, 1), dtype=np.bool_),
            )

    def test_gray8_transition_masks_use_the_exact_binary_boundary(self):
        before = np.asarray([[0, 127, 128, 255, 10]], dtype=np.uint8)
        after = np.asarray([[1, 128, 127, 254, 10]], dtype=np.uint8)
        touched, light, shadow = gray8_transition_masks(before, after)
        np.testing.assert_array_equal(touched, [[True, True, True, True, False]])
        np.testing.assert_array_equal(light, [[False, True, False, False, False]])
        np.testing.assert_array_equal(shadow, [[False, False, True, False, False]])

    def test_streamed_patches_match_compatibility_stack_result(self):
        rng = np.random.default_rng(617)
        masks = rng.integers(0, 2, (len(ANGLES), 9, 11), dtype=np.uint8).astype(bool)
        # Start from a valid monotonic stack.
        masks = np.maximum.accumulate(masks, axis=0)
        coverage = rng.integers(0, 2, masks.shape, dtype=np.uint8).astype(bool)
        before = rng.integers(0, 256, masks.shape[1:], dtype=np.uint8)
        after = before.copy()
        after[2:7, 3:9] = 255 - after[2:7, 3:9]
        touched, became_light, became_shadow = gray8_transition_masks(before, after)
        expected = apply_smart_transitions(
            masks,
            coverage,
            ANGLES,
            3,
            touched,
            became_light,
            became_shadow,
        )

        streamed_masks = masks.copy()
        streamed_coverage = coverage.copy()
        streamed_footprints = np.zeros_like(masks)
        patches = list(
            iter_smart_transition_patches(
                len(ANGLES), 3, touched, became_light, became_shadow
            )
        )
        for patch in patches:
            result = apply_smart_transition_patch(
                streamed_masks[patch.index],
                streamed_coverage[patch.index],
                patch,
            )
            streamed_masks[patch.index] = result.mask
            streamed_coverage[patch.index] = result.coverage
            streamed_footprints[patch.index] = result.footprint
        np.testing.assert_array_equal(streamed_masks, expected.masks)
        np.testing.assert_array_equal(streamed_coverage, expected.coverage)
        np.testing.assert_array_equal(streamed_footprints, expected.footprints)
        self.assertEqual(tuple(patch.index for patch in patches), expected.affected_indices)


class ThresholdPairChannelTests(unittest.TestCase):
    def test_two_lanes_can_own_different_front_masks(self):
        right = np.zeros((len(ANGLES), 2, 2), dtype=np.bool_)
        left = np.zeros_like(right)
        right[:, 0, 0] = True
        left[:, 1, 1] = True
        channels = generate_threshold_pair_channels(right, ANGLES, left, ANGLES)
        self.assertEqual(channels.shape, (2, 2, 2))
        self.assertEqual(int(channels[0, 0, 0]), 65535)
        self.assertEqual(int(channels[0, 0, 1]), 0)
        self.assertEqual(int(channels[1, 1, 0]), 0)
        self.assertEqual(int(channels[1, 1, 1]), 65535)

    def test_irregular_angles_are_supported(self):
        angles = np.array([0.0, 22.5, 71.0, 90.0])
        masks = np.zeros((4, 1, 4), dtype=np.bool_)
        masks[1:, 0, 0] = True
        masks[2:, 0, 1] = True
        masks[3:, 0, 2] = True
        channels = generate_threshold_pair_channels(masks, angles, masks, angles)
        self.assertTrue(1 <= int(channels[0, 0, 0]) <= 65534)
        self.assertTrue(1 <= int(channels[0, 1, 0]) <= 65534)
        self.assertTrue(1 <= int(channels[0, 2, 0]) <= 65534)

    def test_each_lane_is_validated_independently(self):
        masks = np.zeros((len(ANGLES), 1, 1), dtype=np.bool_)
        masks[2, 0, 0] = True
        with self.assertRaisesRegex(ValueError, "right mask lane"):
            generate_threshold_pair_channels(
                masks, ANGLES, np.zeros_like(masks), ANGLES
            )


if __name__ == "__main__":
    unittest.main()

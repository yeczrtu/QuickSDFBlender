import unittest

import numpy as np

from quick_sdf_blender import native
from quick_sdf_blender.bake import bake_normal_sweep as reference_bake
from quick_sdf_blender.core import generate_threshold_rgba16


@unittest.skipUnless(native.available(), "Windows native core was not built")
class NativeCoreTests(unittest.TestCase):
    def test_version_two_pair_abi(self):
        self.assertGreaterEqual(native.version(), 3)

    def test_native_normal_bake_matches_reference(self):
        uvs = np.asarray(
            [
                [[0.05, 0.05], [0.95, 0.10], [0.15, 0.95]],
                [[0.95, 0.10], [0.90, 0.95], [0.15, 0.95]],
            ],
            dtype=np.float32,
        )
        normals = np.asarray(
            [
                [[0.0, -1.0, 0.0], [1.0, -1.0, 0.0], [0.0, -1.0, 1.0]],
                [[1.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, -1.0, 1.0]],
            ],
            dtype=np.float32,
        )
        angles = np.asarray([0.0, 30.0, 60.0, 90.0], dtype=np.float32)
        expected_masks, expected_occupancy = reference_bake(
            uvs, normals, angles, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), 31, 19
        )
        masks, occupancy = native.bake_normal_sweep(
            uvs, normals, angles, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), 31, 19
        )
        np.testing.assert_array_equal(occupancy, expected_occupancy)
        np.testing.assert_array_equal(masks, expected_masks)

    def test_native_bake_supports_negative_lane_and_empty_uv_space(self):
        uvs = np.asarray([[[0.2, 0.2], [0.8, 0.2], [0.2, 0.8]]], dtype=np.float32)
        normals = np.asarray([[[0.0, -1.0, 0.0]] * 3], dtype=np.float32)
        angles = np.asarray([0.0, -45.0, -90.0], dtype=np.float32)
        masks, occupancy = native.bake_normal_sweep(
            uvs, normals, angles, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), 17, 13
        )
        self.assertEqual(masks.shape, (3, 13, 17))
        self.assertTrue(np.all(masks[:, ~occupancy]))

    def test_threshold_pair_supports_distinct_zero_masks(self):
        angles = np.asarray([0.0, 45.0, 90.0], dtype=np.float32)
        right = np.zeros((3, 1, 3), dtype=np.uint8)
        left = np.zeros_like(right)
        right[:, 0, 0] = 1  # always Light on the right only
        left[:, 0, 1] = 1   # always Light on the left only
        right[2, 0, 2] = 1  # transition at the last interval
        left[1:, 0, 2] = 1  # earlier transition
        result = native.generate_threshold_pair(right, angles, left, angles)
        np.testing.assert_array_equal(result[0, 0], [0, 65535])
        np.testing.assert_array_equal(result[0, 1], [65535, 0])
        self.assertGreater(result[0, 2, 0], result[0, 2, 1])

    def test_threshold_pair_rejects_one_invalid_side(self):
        angles = [0.0, 90.0]
        right = np.zeros((2, 1, 1), dtype=np.uint8)
        left = right.copy()
        left[0] = 1
        with self.assertRaisesRegex(native.NativeCoreError, "left=1"):
            native.generate_threshold_pair(right, angles, left, angles)
    def test_constant_sentinels(self):
        angles = np.array([-90.0, 0.0, 90.0], dtype=np.float32)
        masks = np.zeros((3, 4, 6), dtype=np.uint8)
        result = native.generate_threshold(masks, angles)
        self.assertTrue(np.all(result == 65535))

        masks.fill(1)
        result = native.generate_threshold(masks, angles)
        self.assertTrue(np.all(result == 0))

    def test_distance_interpolated_transition(self):
        angles = np.array([-90.0, -45.0, 0.0, 45.0, 90.0], dtype=np.float32)
        masks = np.ones((5, 8, 8), dtype=np.uint8)
        masks[2] = 0
        result = native.generate_threshold(masks, angles)
        # Equal SDF magnitudes place the transition at 22.5 degrees (0.25).
        self.assertTrue(np.all(result[:, :, 0] == 16384))
        self.assertTrue(np.all(result[:, :, 1] == 16384))

    def test_non_monotonic_is_rejected(self):
        angles = np.array([-90.0, 0.0, 90.0], dtype=np.float32)
        masks = np.zeros((3, 2, 2), dtype=np.uint8)
        masks[1] = 1
        self.assertGreater(native.validate_monotonic(masks, angles), 0)
        with self.assertRaises(native.NativeCoreError):
            native.generate_threshold(masks, angles)

    def test_native_matches_reference(self):
        rng = np.random.default_rng(9182)
        angles = np.linspace(-90.0, 90.0, 13, dtype=np.float32)
        # A per-pixel switch index guarantees light expands from zero to each side.
        switches = rng.integers(1, 7, size=(2, 11, 9))
        masks = np.zeros((13, 11, 9), dtype=np.uint8)
        for distance in range(7):
            masks[6 + distance] = distance >= switches[0]
            masks[6 - distance] = distance >= switches[1]
        reference = generate_threshold_rgba16(masks, angles)
        channels = native.generate_threshold(masks, angles)
        np.testing.assert_array_equal(channels[..., 0], reference[..., 0])
        np.testing.assert_array_equal(channels[..., 1], reference[..., 1])

    def test_unsorted_angles_match_reference(self):
        angles = np.array([90.0, 0.0, -90.0], dtype=np.float32)
        masks = np.ones((3, 3, 5), dtype=np.uint8)
        masks[1] = 0
        reference = generate_threshold_rgba16(masks, angles)
        channels = native.generate_threshold(masks, angles)
        np.testing.assert_array_equal(channels, reference[..., :2])

    def test_endpoints_are_required(self):
        masks = np.ones((3, 2, 2), dtype=np.uint8)
        with self.assertRaises(ValueError):
            native.generate_threshold(masks, [-45.0, 0.0, 45.0])


if __name__ == "__main__":
    unittest.main()

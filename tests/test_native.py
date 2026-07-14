import unittest
import ctypes
import threading
import time

import numpy as np

from quick_sdf_blender import native
from quick_sdf_blender.bake import (
    bake_face_shadow_guide as reference_guide_bake,
    bake_normal_sweep as reference_bake,
)
from quick_sdf_blender.core import (
    generate_threshold_channels as reference_threshold_channels,
    generate_threshold_pair_channels as reference_threshold_pair,
    repair_side_monotonic as reference_repair,
)


def reference_signed_threshold_channels(
    masks: np.ndarray, angles: np.ndarray
) -> np.ndarray:
    positive, negative = reference_threshold_channels(masks, angles)
    return np.stack((positive, negative), axis=-1)


@unittest.skipUnless(native.available(), "Windows native core was not built")
class NativeCoreTests(unittest.TestCase):
    def test_version_five_liltoon_abi(self):
        self.assertEqual(native.version(), 5)
        self.assertTrue(native.native_threshold_available())
        self.assertTrue(native.native_guide_bake_available())
        self.assertTrue(native.native_repair_available())

    def test_native_monotonic_repair_matches_reference(self):
        rng = np.random.default_rng(403)
        masks = rng.random((7, 13, 11)) > 0.5
        base = rng.random(masks.shape) > 0.5
        coverage = rng.random(masks.shape) > 0.82
        expected = reference_repair(masks, base, coverage)
        actual = native.repair_side_monotonic(masks, base, coverage)
        np.testing.assert_array_equal(actual.masks, expected.masks)
        np.testing.assert_array_equal(actual.changed_mask, expected.changed_mask)
        np.testing.assert_array_equal(
            actual.transition_indices, expected.transition_indices
        )
        self.assertEqual(actual.changed_sample_count, expected.changed_sample_count)
        self.assertEqual(actual.changed_pixel_count, expected.changed_pixel_count)
        self.assertEqual(
            actual.protected_changed_sample_count,
            expected.protected_changed_sample_count,
        )
        self.assertEqual(
            actual.protected_changed_pixel_count,
            expected.protected_changed_pixel_count,
        )

    def test_native_repair_normalizes_supported_integer_and_memory_layouts(self):
        masks = np.asfortranarray(
            np.asarray([True, False, True, True], dtype=np.bool_)[:, None, None]
        )
        base = np.asarray(masks, dtype=np.uint8) * np.uint8(255)
        coverage = np.asarray(np.zeros_like(masks), dtype=np.uint16)
        expected = reference_repair(masks, base, coverage)
        actual = native.repair_side_monotonic(masks, base, coverage)
        np.testing.assert_array_equal(actual.masks, expected.masks)
        np.testing.assert_array_equal(actual.changed_mask, expected.changed_mask)
        np.testing.assert_array_equal(actual.transition_indices, expected.transition_indices)
        self.assertEqual(actual.transition_indices.dtype, np.int32)

    def test_native_repair_and_threshold_honor_cancel_flag(self):
        angles = np.linspace(0.0, 90.0, 7, dtype=np.float32)
        masks = np.zeros((7, 8, 8), dtype=np.bool_)
        base = masks.copy()
        coverage = masks.copy()
        cancelled = ctypes.c_int(1)
        with self.assertRaisesRegex(native.NativeCoreError, "cancelled"):
            native.repair_side_monotonic(
                masks, base, coverage, cancel_flag=cancelled
            )
        with self.assertRaisesRegex(native.NativeCoreError, "cancelled"):
            native.generate_threshold_pair(
                masks, angles, masks, angles, cancel_flag=cancelled
            )

    def test_running_native_threshold_cancels_cooperatively(self):
        size = 1024
        angles = np.linspace(0.0, 90.0, 7, dtype=np.float32)
        y, x = np.indices((size, size))
        right_transition = (x + y) % 8
        left_transition = (x * 3 + y) % 8
        right = np.arange(7)[:, None, None] >= right_transition[None, ...]
        left = np.arange(7)[:, None, None] >= left_transition[None, ...]
        cancel_flag = ctypes.c_int(0)
        outcome = []

        def generate():
            try:
                native.generate_threshold_pair(
                    right, angles, left, angles, cancel_flag=cancel_flag
                )
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=generate)
        started = time.perf_counter()
        worker.start()
        time.sleep(0.02)
        cancel_flag.value = 1
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(len(outcome), 1)
        self.assertRegex(str(outcome[0]), "cancelled")

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

    def test_native_face_shadow_guide_matches_reference(self):
        uvs = np.asarray(
            [
                [[0.05, 0.05], [0.95, 0.10], [0.15, 0.95]],
                [[0.95, 0.10], [0.90, 0.95], [0.15, 0.95]],
            ],
            dtype=np.float32,
        )
        normals = np.asarray(
            [
                [[0.8, -1.0, 0.0], [0.2, -1.0, 0.4], [-0.8, -1.0, 0.0]],
                [[0.2, -1.0, 0.4], [-0.8, -1.0, 0.0], [0.0, -1.0, -0.5]],
            ],
            dtype=np.float32,
        )
        angles = np.arange(8, dtype=np.float64) * (90.0 / 7.0)
        for side in ("RIGHT", "LEFT"):
            expected_masks, expected_occupancy = reference_guide_bake(
                uvs,
                normals,
                angles,
                (0.3, -1.0, 0.2),
                (0.0, 0.0, 1.0),
                side,
                50.0,
                29,
                23,
            )
            masks, occupancy = native.bake_face_shadow_guide(
                uvs,
                normals,
                angles,
                (0.3, -1.0, 0.2),
                (0.0, 0.0, 1.0),
                side,
                50.0,
                29,
                23,
            )
            np.testing.assert_array_equal(occupancy, expected_occupancy)
            np.testing.assert_array_equal(masks, expected_masks)
            self.assertTrue(np.all(masks[-1, occupancy]))

    def test_threshold_pair_supports_distinct_zero_masks(self):
        angles = np.asarray([0.0, 45.0, 90.0], dtype=np.float32)
        right = np.zeros((3, 1, 3), dtype=np.uint8)
        left = np.zeros_like(right)
        right[:, 0, 0] = 1  # always Light on the right only
        left[:, 0, 1] = 1   # always Light on the left only
        right[2, 0, 2] = 1  # transition at the last interval
        left[1:, 0, 2] = 1  # earlier transition
        result = native.generate_threshold_pair(right, angles, left, angles)
        np.testing.assert_array_equal(result[0, 0], [65535, 0])
        np.testing.assert_array_equal(result[0, 1], [0, 65535])
        self.assertLess(result[0, 2, 0], result[0, 2, 1])

    def test_threshold_pair_symbol_uses_abi_five_double_angles(self):
        angles = np.asarray([0.0, 45.0, 90.0], dtype=np.float64)
        right = np.zeros((3, 2, 3), dtype=np.uint8)
        left = np.zeros_like(right)
        right[1:, 0, :] = 1
        right[2, 1, :] = 1
        left[2, 0, :] = 1
        left[1:, 1, :] = 1
        expected = native.generate_threshold_pair(right, angles, left, angles)
        output = np.empty((2, 3, 2), dtype=np.uint16)
        right_violations = ctypes.c_int(0)
        left_violations = ctypes.c_int(0)
        dll = native._load()
        code = dll.qsdf_generate_threshold_pair(
            right.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            angles.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            3,
            left.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            angles.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            3,
            3,
            2,
            output.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
            ctypes.byref(right_violations),
            ctypes.byref(left_violations),
        )
        self.assertEqual(code, 0)
        np.testing.assert_array_equal(output, expected)

    def test_threshold_pair_rejects_one_invalid_side(self):
        angles = [0.0, 90.0]
        right = np.zeros((2, 1, 1), dtype=np.uint8)
        left = right.copy()
        left[0] = 1
        with self.assertRaisesRegex(native.NativeCoreError, "left=1"):
            native.generate_threshold_pair(right, angles, left, angles)
    def test_full_range_endpoint_values(self):
        angles = np.array([-90.0, 0.0, 90.0], dtype=np.float64)
        masks = np.zeros((3, 4, 6), dtype=np.uint8)
        result = native.generate_threshold(masks, angles)
        self.assertTrue(np.all(result == 0))

        masks.fill(1)
        result = native.generate_threshold(masks, angles)
        self.assertTrue(np.all(result == 65535))

    def test_distance_interpolated_transition(self):
        angles = np.array([-90.0, -45.0, 0.0, 45.0, 90.0], dtype=np.float64)
        masks = np.ones((5, 8, 8), dtype=np.uint8)
        masks[2] = 0
        result = native.generate_threshold(masks, angles)
        # Equal SDF magnitudes place the transition at u=.25, encoded as T=.75.
        self.assertTrue(np.all(result[:, :, 0] == 49151))
        self.assertTrue(np.all(result[:, :, 1] == 49151))

    def test_eight_fractional_keys_are_byte_exact_with_python(self):
        angles = np.arange(8, dtype=np.float64) * (90.0 / 7.0)
        y, x = np.indices((13, 17))
        right_transition = (x + 2 * y) % 9
        left_transition = (3 * x + y + 2) % 9
        right = np.arange(8)[:, None, None] >= right_transition[None, ...]
        left = np.arange(8)[:, None, None] >= left_transition[None, ...]
        expected = reference_threshold_pair(right, angles, left, angles)
        actual = native.generate_threshold_pair(right, angles, left, angles)
        np.testing.assert_array_equal(actual, expected)

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
        reference = reference_signed_threshold_channels(masks, angles)
        channels = native.generate_threshold(masks, angles)
        np.testing.assert_array_equal(channels, reference)

    def test_unsorted_angles_match_reference(self):
        angles = np.array([90.0, 0.0, -90.0], dtype=np.float32)
        masks = np.ones((3, 3, 5), dtype=np.uint8)
        masks[1] = 0
        reference = reference_signed_threshold_channels(masks, angles)
        channels = native.generate_threshold(masks, angles)
        np.testing.assert_array_equal(channels, reference)

    def test_endpoints_are_required(self):
        masks = np.ones((3, 2, 2), dtype=np.uint8)
        with self.assertRaises(ValueError):
            native.generate_threshold(masks, [-45.0, 0.0, 45.0])


if __name__ == "__main__":
    unittest.main()

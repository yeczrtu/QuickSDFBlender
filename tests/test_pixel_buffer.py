from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.pixel_buffer import (
    blender_float_rgba_to_top_down_gray8,
    blender_float_rgba_to_top_down_u8,
    top_down_gray8_to_blender_float_rgba,
)


class BlenderFloatRgbaConversionTests(unittest.TestCase):
    def test_converts_bottom_up_rows_to_contiguous_top_down_rgba8(self) -> None:
        # Blender row order: bottom-left, bottom-right, top-left, top-right.
        flat = np.asarray(
            [
                [0.0, 0.0, 0.0, 1.0],
                [1.0, 0.0, 0.0, 1.0],
                [0.0, 1.0, 0.0, 1.0],
                [0.0, 0.0, 1.0, 1.0],
            ],
            dtype=np.float32,
        ).reshape(-1)

        result = blender_float_rgba_to_top_down_u8(flat, 2, 2)

        expected = np.asarray(
            [
                [[0, 255, 0, 255], [0, 0, 255, 255]],
                [[0, 0, 0, 255], [255, 0, 0, 255]],
            ],
            dtype=np.uint8,
        )
        np.testing.assert_array_equal(result, expected)
        self.assertEqual(result.shape, (2, 2, 4))
        self.assertEqual(result.dtype, np.uint8)
        self.assertTrue(result.flags.c_contiguous)
        self.assertFalse(np.shares_memory(result, flat))

    def test_clips_and_uses_numpy_round_to_nearest_even(self) -> None:
        flat = np.asarray(
            [-1.0, 0.0, 0.5, 1.0, 2.0, 1.0 / 255.0, 254.0 / 255.0, 0.501],
            dtype=np.float32,
        )

        result = blender_float_rgba_to_top_down_u8(flat, 2, 1)

        np.testing.assert_array_equal(
            result,
            np.asarray([[[0, 0, 128, 255], [255, 1, 254, 128]]], dtype=np.uint8),
        )

    def test_matches_the_previous_full_copy_conversion(self) -> None:
        rng = np.random.default_rng(7031)
        blender_rows = rng.uniform(-0.25, 1.25, size=(5, 7, 4)).astype(np.float32)
        reference = np.rint(
            np.clip(np.flip(blender_rows, axis=0).copy(), 0.0, 1.0) * 255.0
        ).astype(np.uint8)

        result = blender_float_rgba_to_top_down_u8(blender_rows.reshape(-1), 7, 5)

        np.testing.assert_array_equal(result, reference)

    def test_rejects_invalid_dimensions_and_buffer_contract(self) -> None:
        valid = np.zeros(8, dtype=np.float32)
        for dimension in (0, -1):
            with self.subTest(dimension=dimension):
                with self.assertRaisesRegex(ValueError, "positive"):
                    blender_float_rgba_to_top_down_u8(valid.copy(), dimension, 1)
        for dimension in (True, 2.0, "2"):
            with self.subTest(dimension=dimension):
                with self.assertRaisesRegex(TypeError, "integer"):
                    blender_float_rgba_to_top_down_u8(valid.copy(), dimension, 1)

        with self.assertRaisesRegex(TypeError, "numpy array"):
            blender_float_rgba_to_top_down_u8([0.0] * 8, 2, 1)
        with self.assertRaisesRegex(TypeError, "float32"):
            blender_float_rgba_to_top_down_u8(valid.astype(np.float64), 2, 1)
        with self.assertRaisesRegex(ValueError, "one-dimensional"):
            blender_float_rgba_to_top_down_u8(valid.reshape(1, 8), 2, 1)
        with self.assertRaisesRegex(ValueError, "C-contiguous"):
            blender_float_rgba_to_top_down_u8(np.zeros(16, np.float32)[::2], 2, 1)
        read_only = valid.copy()
        read_only.flags.writeable = False
        with self.assertRaisesRegex(ValueError, "writable"):
            blender_float_rgba_to_top_down_u8(read_only, 2, 1)
        with self.assertRaisesRegex(ValueError, "expected 8"):
            blender_float_rgba_to_top_down_u8(np.zeros(4, np.float32), 2, 1)

    def test_direct_gray8_path_matches_rgba_red_without_rgba_result(self) -> None:
        rng = np.random.default_rng(773)
        blender_rows = rng.uniform(-0.1, 1.1, size=(9, 13, 4)).astype(np.float32)
        expected = blender_float_rgba_to_top_down_u8(
            blender_rows.copy().reshape(-1), 13, 9
        )[..., 0]
        result = blender_float_rgba_to_top_down_gray8(
            blender_rows.reshape(-1), 13, 9
        )
        np.testing.assert_array_equal(result, expected)
        self.assertEqual(result.shape, (9, 13))
        self.assertEqual(result.nbytes, 9 * 13)
        self.assertTrue(result.flags.c_contiguous)

    def test_gray8_upload_uses_reusable_bottom_up_float_buffer(self) -> None:
        gray = np.asarray([[0, 127, 255], [1, 128, 254]], dtype=np.uint8)
        reusable = np.full(gray.size * 4, np.nan, dtype=np.float32)
        result = top_down_gray8_to_blender_float_rgba(gray, out=reusable)
        self.assertIs(result, reusable)
        rgba = result.reshape(2, 3, 4)
        expected_gray = gray[::-1].astype(np.float32) / np.float32(255.0)
        np.testing.assert_allclose(rgba[..., 0], expected_gray, rtol=0.0, atol=1e-7)
        np.testing.assert_allclose(rgba[..., 1], expected_gray, rtol=0.0, atol=1e-7)
        np.testing.assert_allclose(rgba[..., 2], expected_gray, rtol=0.0, atol=1e-7)
        np.testing.assert_array_equal(rgba[..., 3], 1.0)

    def test_gray8_upload_validation(self) -> None:
        gray = np.zeros((2, 3), dtype=np.uint8)
        with self.assertRaisesRegex(TypeError, "uint8"):
            top_down_gray8_to_blender_float_rgba(gray.astype(np.float32))
        with self.assertRaisesRegex(ValueError, "two-dimensional"):
            top_down_gray8_to_blender_float_rgba(gray[..., None])
        with self.assertRaisesRegex(ValueError, "expected 24"):
            top_down_gray8_to_blender_float_rgba(
                gray,
                out=np.empty(23, dtype=np.float32),
            )


if __name__ == "__main__":
    unittest.main()

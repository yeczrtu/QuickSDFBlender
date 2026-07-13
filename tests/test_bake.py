from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.bake import (
    bake_normal_sweep,
    enforce_monotonic_expansion,
    light_directions,
    rasterize_uv_normals,
)


SQUARE_UVS = np.asarray(
    [
        [[0, 0], [1, 0], [1, 1]],
        [[0, 0], [1, 1], [0, 1]],
    ],
    dtype=np.float32,
)


class RasterizeUvNormalsTests(unittest.TestCase):
    def test_square_is_opaque_and_normals_are_normalized(self) -> None:
        normals = np.zeros((2, 3, 3), dtype=np.float32)
        normals[..., 1] = -2.0
        image, occupancy = rasterize_uv_normals(SQUARE_UVS, normals, 7, 5)
        self.assertEqual(image.shape, (5, 7, 3))
        self.assertTrue(np.all(occupancy))
        expected = np.broadcast_to([0.0, -1.0, 0.0], image.shape)
        np.testing.assert_allclose(image, expected, atol=1e-6)

    def test_empty_input_and_degenerate_triangle_are_safe(self) -> None:
        image, occupancy = rasterize_uv_normals(
            np.empty((0, 3, 2)), np.empty((0, 3, 3)), 3, 2
        )
        self.assertFalse(np.any(occupancy))
        self.assertFalse(np.any(image))
        degenerate = np.zeros((1, 3, 2))
        normals = np.tile([0.0, 0.0, 1.0], (1, 3, 1))
        _image, occupancy = rasterize_uv_normals(degenerate, normals, 4)
        self.assertFalse(np.any(occupancy))

    def test_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "corner_normals"):
            rasterize_uv_normals(np.zeros((1, 3, 2)), np.zeros((2, 3, 3)), 4)
        with self.assertRaisesRegex(ValueError, "non-zero"):
            rasterize_uv_normals(np.zeros((1, 3, 2)), np.zeros((1, 3, 3)), 4)


class NormalSweepTests(unittest.TestCase):
    def test_outside_is_light_and_sweep_is_monotonic(self) -> None:
        uvs = np.asarray([[[0, 0], [0.5, 0], [0, 1]]], dtype=np.float32)
        normal = np.asarray([1.0, 0.1, 0.0], dtype=np.float32)
        normals = np.tile(normal, (1, 3, 1))
        angles = [0.0, 30.0, 60.0, 90.0]
        masks, occupied = bake_normal_sweep(
            uvs, normals, angles, (0, -1, 0), (0, 0, 1), 8, 6
        )
        self.assertTrue(np.all(masks[:, ~occupied]))
        self.assertTrue(np.all(~masks[:-1] | masks[1:]))
        self.assertTrue(np.any(~masks[0, occupied]))
        self.assertTrue(np.any(masks[-1, occupied]))

    def test_signed_sides_expand_independently(self) -> None:
        raw = np.zeros((5, 1, 2), dtype=bool)
        raw[2, 0, 0] = True
        result = enforce_monotonic_expansion(raw, [-90, -45, 0, 45, 90])
        self.assertTrue(np.all(result[:, 0, 0]))
        self.assertFalse(np.any(result[:, 0, 1]))

    def test_view_forward_is_projected_off_up(self) -> None:
        angles, directions = light_directions([0, 90], (0, -1, 0.5), (0, 0, 1))
        np.testing.assert_array_equal(angles, [0, 90])
        np.testing.assert_allclose(directions[0], [0, -1, 0], atol=1e-6)
        np.testing.assert_allclose(directions[1], [1, 0, 0], atol=1e-6)


if __name__ == "__main__":
    unittest.main()

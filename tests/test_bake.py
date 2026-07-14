from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.bake import (
    bake_face_shadow_guide,
    bake_normal_sweep,
    enforce_monotonic_expansion,
    guide_light_directions,
    light_directions,
    rasterize_uv_normals,
    shadow_amount_cutoff,
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


class FaceShadowGuideTests(unittest.TestCase):
    def test_rear_oblique_side_and_front_oblique_directions_are_explicit(self) -> None:
        angles, right = guide_light_directions(
            [0.0, 45.0, 90.0], (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), "RIGHT"
        )
        _angles, left = guide_light_directions(
            angles, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), "LEFT"
        )
        root_three_over_two = np.sqrt(3.0) * 0.5
        np.testing.assert_allclose(right[0], [root_three_over_two, 0.5, 0.0], atol=1e-6)
        np.testing.assert_allclose(left[0], [-root_three_over_two, 0.5, 0.0], atol=1e-6)
        np.testing.assert_allclose(right[1], [1.0, 0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(left[1], [-1.0, 0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(right[-1], [root_three_over_two, -0.5, 0.0], atol=1e-6)
        np.testing.assert_allclose(left[-1], [-root_three_over_two, -0.5, 0.0], atol=1e-6)

    def test_continuous_forward_is_not_axis_quantized(self) -> None:
        _angles, directions = guide_light_directions(
            [0.0, 90.0], (1.0, -1.0, 0.3), (0.0, 0.0, 1.0), "RIGHT"
        )
        front = np.asarray([1.0, -1.0, 0.0]) / np.sqrt(2.0)
        side = np.cross(np.asarray([0.0, 0.0, 1.0]), front)
        expected = side * (np.sqrt(3.0) * 0.5) + front * 0.5
        np.testing.assert_allclose(directions[-1], expected, atol=1e-6)

    def test_eight_even_authoring_keys_map_to_liltoon_front_dot(self) -> None:
        angles = np.asarray([index * 90.0 / 7.0 for index in range(8)])
        returned, directions = guide_light_directions(
            angles, (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), "RIGHT"
        )
        np.testing.assert_array_equal(returned, angles)
        expected_front_dot = np.arange(8, dtype=np.float64) / 7.0 - 0.5
        front = np.asarray([0.0, -1.0, 0.0])
        np.testing.assert_allclose(directions @ front, expected_front_dot, atol=1e-6)
        np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1.0, atol=1e-6)

    def test_shadow_amount_maps_to_documented_cutoff(self) -> None:
        self.assertAlmostEqual(shadow_amount_cutoff(0.0), -0.15)
        self.assertAlmostEqual(shadow_amount_cutoff(50.0), 0.10)
        self.assertAlmostEqual(shadow_amount_cutoff(100.0), 0.35)
        with self.assertRaises(ValueError):
            shadow_amount_cutoff(101.0)

    def test_guide_is_non_uniform_and_monotonic(self) -> None:
        normals = np.asarray(
            [
                [[1.0, -1.0, 0.0], [0.0, -1.0, 0.5], [-1.0, -1.0, 0.0]],
                [[1.0, -1.0, 0.0], [-1.0, -1.0, 0.0], [0.0, -1.0, -0.5]],
            ],
            dtype=np.float32,
        )
        masks, occupancy = bake_face_shadow_guide(
            SQUARE_UVS,
            normals,
            [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0],
            (0.0, -1.0, 0.0),
            (0.0, 0.0, 1.0),
            "RIGHT",
            50.0,
            17,
            13,
        )
        self.assertTrue(np.all(~masks[:-1] | masks[1:]))
        self.assertTrue(np.any(masks[3, occupancy]))
        self.assertTrue(np.any(~masks[3, occupancy]))
        self.assertTrue(np.all(masks[-1, occupancy]))
        self.assertTrue(np.all(masks[:, ~occupancy]))

    def test_final_guide_stage_is_full_light_even_without_monotonic_enforcement(self) -> None:
        normals = np.zeros((2, 3, 3), dtype=np.float32)
        normals[..., 2] = 1.0
        masks, occupancy = bake_face_shadow_guide(
            SQUARE_UVS,
            normals,
            [0.0, 45.0, 90.0],
            (0.0, -1.0, 0.0),
            (0.0, 0.0, 1.0),
            "RIGHT",
            50.0,
            9,
            7,
            enforce_monotonic=False,
        )
        self.assertTrue(np.any(~masks[0, occupancy]))
        self.assertTrue(np.all(masks[-1, occupancy]))


if __name__ == "__main__":
    unittest.main()

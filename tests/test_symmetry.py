from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.symmetry import (
    IslandPair,
    SymmetryMode,
    analyze_symmetry,
    apply_symmetry_to_stack,
    mirror_side_layer,
    mirror_side_stack,
)


class StackSymmetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.angles = np.asarray([-90.0, -45.0, 0.0, 45.0, 90.0])
        self.stack = np.zeros((5, 3, 5), dtype=np.uint8)
        self.stack[3] = np.arange(15, dtype=np.uint8).reshape(3, 5)
        self.stack[4] = self.stack[3] + 30
        self.stack[0] = 201
        self.stack[1] = 202
        self.stack[2] = 77

    def test_overlapped_copies_positive_to_matching_negative(self) -> None:
        result = apply_symmetry_to_stack(self.stack, self.angles, "OVERLAPPED")
        np.testing.assert_array_equal(result[0], self.stack[4])
        np.testing.assert_array_equal(result[1], self.stack[3])
        np.testing.assert_array_equal(result[2:], self.stack[2:])
        self.assertEqual(result.dtype, self.stack.dtype)
        self.assertFalse(np.shares_memory(result, self.stack))

    def test_texture_mirror_reverses_only_u_axis(self) -> None:
        result = apply_symmetry_to_stack(
            self.stack, self.angles, SymmetryMode.TEXTURE_MIRROR
        )
        np.testing.assert_array_equal(result[0], self.stack[4, :, ::-1])
        np.testing.assert_array_equal(result[1], self.stack[3, :, ::-1])
        np.testing.assert_array_equal(result[2], self.stack[2])

    def test_independent_returns_unmodified_copy(self) -> None:
        result = apply_symmetry_to_stack(self.stack, self.angles, "independent")
        np.testing.assert_array_equal(result, self.stack)
        self.assertFalse(np.shares_memory(result, self.stack))

    def test_missing_positive_angle_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "no matching positive"):
            apply_symmetry_to_stack(
                np.zeros((3, 2, 2)), [-90.0, 0.0, 45.0], "OVERLAPPED"
            )


class IslandPairTests(unittest.TestCase):
    def test_bbox_local_mirror_resamples_into_target_island(self) -> None:
        angles = np.asarray([-45.0, 0.0, 45.0])
        stack = np.full((3, 6, 9), 99, dtype=np.int16)
        source = np.zeros((6, 9), dtype=bool)
        target = np.zeros((6, 9), dtype=bool)
        source[1:4, 1:5] = True  # 3 high x 4 wide
        target[2:6, 6:9] = True  # 4 high x 3 wide
        stack[2, 1:4, 1:5] = np.asarray(
            [[10, 11, 12, 13], [20, 21, 22, 23], [30, 31, 32, 33]]
        )

        result = apply_symmetry_to_stack(
            stack, angles, "ISLAND_PAIR", [(source, target)]
        )

        expected = np.asarray(
            [
                [13, 11, 10],
                [23, 21, 20],
                [23, 21, 20],
                [33, 31, 30],
            ]
        )
        np.testing.assert_array_equal(result[0, 2:6, 6:9], expected)
        self.assertTrue(np.all(result[0][~target] == 99))

    def test_multiple_pairs_and_mapping_form(self) -> None:
        angles = [-30.0, 0.0, 30.0]
        stack = np.zeros((3, 2, 6), dtype=np.uint8)
        stack[2, 0, 0:2] = [1, 2]
        stack[2, 1, 2:4] = [3, 4]
        first_source = np.zeros((2, 6), bool)
        first_target = np.zeros((2, 6), bool)
        second_source = np.zeros((2, 6), bool)
        second_target = np.zeros((2, 6), bool)
        first_source[0, 0:2] = True
        first_target[0, 4:6] = True
        second_source[1, 2:4] = True
        second_target[1, 0:2] = True

        result = apply_symmetry_to_stack(
            stack,
            angles,
            "ISLAND_PAIR",
            [
                IslandPair(first_source, first_target),
                {"source_mask": second_source, "target_mask": second_target},
            ],
        )
        np.testing.assert_array_equal(result[0, 0, 4:6], [2, 1])
        np.testing.assert_array_equal(result[0, 1, 0:2], [4, 3])

    def test_missing_or_overlapping_pairs_are_rejected(self) -> None:
        stack = np.zeros((3, 2, 2), dtype=bool)
        occupancy = np.ones((2, 2), dtype=bool)
        with self.assertRaisesRegex(ValueError, "requires island_pairs"):
            apply_symmetry_to_stack(stack, [-45, 0, 45], "ISLAND_PAIR")
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            apply_symmetry_to_stack(
                stack,
                [-45, 0, 45],
                "ISLAND_PAIR",
                [(occupancy, occupancy), (occupancy, occupancy)],
            )


class AutoAnalysisTests(unittest.TestCase):
    def test_detects_overlapped_layout_with_high_confidence(self) -> None:
        positive = np.zeros((4, 8), dtype=bool)
        positive[1:4, 0:3] = True
        analysis = analyze_symmetry(positive, positive.copy())
        self.assertEqual(analysis.suggested_mode, SymmetryMode.OVERLAPPED)
        self.assertEqual(analysis.overlap_score, 1.0)
        self.assertEqual(analysis.mirror_score, 0.0)
        self.assertEqual(analysis.confidence, 1.0)
        self.assertFalse(analysis.requires_confirmation)

    def test_detects_texture_mirror_and_auto_applies_it(self) -> None:
        positive = np.zeros((3, 7), dtype=np.uint8)
        positive[:, 0:2] = 255
        negative = positive[:, ::-1]
        analysis = analyze_symmetry(positive, negative)
        self.assertEqual(analysis.suggested_mode, SymmetryMode.TEXTURE_MIRROR)
        self.assertEqual(analysis.mirror_score, 1.0)
        self.assertEqual(analysis.overlap_score, 0.0)
        self.assertEqual(analysis.confidence, 1.0)

        stack = np.stack((negative, np.zeros_like(positive), positive))
        positive_values = np.zeros((3, 7), dtype=np.uint8)
        positive_values[:, 0:2] = [[1, 2], [3, 4], [5, 6]]
        stack[2] = positive_values
        result = apply_symmetry_to_stack(stack, [-45, 0, 45], "AUTO")
        np.testing.assert_array_equal(result[0], positive_values[:, ::-1])

    def test_symmetric_occupancy_is_flagged_as_ambiguous(self) -> None:
        occupancy = np.zeros((3, 7), dtype=bool)
        occupancy[:, 1:6] = True
        analysis = analyze_symmetry(occupancy, occupancy)
        self.assertEqual(analysis.suggested_mode, SymmetryMode.OVERLAPPED)
        self.assertEqual(analysis.overlap_score, 1.0)
        self.assertEqual(analysis.mirror_score, 1.0)
        self.assertEqual(analysis.confidence, 0.0)
        self.assertTrue(analysis.requires_confirmation)

    def test_unmatched_or_empty_occupancy_suggests_independent(self) -> None:
        positive = np.zeros((4, 8), bool)
        negative = np.zeros((4, 8), bool)
        positive[0, 0] = True
        negative[3, 3] = True
        analysis = analyze_symmetry(positive, negative)
        self.assertEqual(analysis.suggested_mode, SymmetryMode.INDEPENDENT)
        self.assertEqual(analysis.confidence, 1.0)

        empty = analyze_symmetry(np.zeros((2, 2)), np.zeros((2, 2)))
        self.assertEqual(empty.suggested_mode, SymmetryMode.INDEPENDENT)
        self.assertEqual(empty.confidence, 0.0)
        self.assertTrue(empty.requires_confirmation)

    def test_auto_uses_island_pair_fallback_when_global_layout_does_not_match(self) -> None:
        angles = [-45.0, 0.0, 45.0]
        stack = np.zeros((3, 3, 8), dtype=np.uint8)
        source = np.zeros((3, 8), bool)
        target = np.zeros((3, 8), bool)
        source[:, 0:2] = True
        target[:, 3:5] = True
        stack[2, :, 0:2] = [[1, 2], [3, 4], [5, 6]]
        result = apply_symmetry_to_stack(
            stack, angles, "AUTO", [(source, target)]
        )
        np.testing.assert_array_equal(
            result[0, :, 3:5], [[2, 1], [4, 3], [6, 5]]
        )

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "same shape"):
            analyze_symmetry(np.zeros((2, 2)), np.zeros((2, 3)))
        with self.assertRaisesRegex(ValueError, "unknown symmetry mode"):
            apply_symmetry_to_stack(np.zeros((3, 2, 2)), [-1, 0, 1], "radial")


class LiveSideMirrorTests(unittest.TestCase):
    def test_layer_texture_mirror_and_overlapped_alias(self) -> None:
        source = np.arange(24, dtype=np.uint8).reshape(3, 4, 2)
        np.testing.assert_array_equal(
            mirror_side_layer(source, "TEXTURE_MIRROR"), source[:, ::-1]
        )
        np.testing.assert_array_equal(
            mirror_side_layer(source, "OVERLAPPED_UV"), source
        )

    def test_stack_mirrors_every_angle_and_keeps_dtype(self) -> None:
        source = np.arange(2 * 3 * 5, dtype=np.float32).reshape(2, 3, 5)
        result = mirror_side_stack(source, SymmetryMode.TEXTURE_MIRROR)
        np.testing.assert_array_equal(result, source[:, :, ::-1])
        self.assertEqual(result.dtype, source.dtype)

    def test_island_pair_preserves_template_outside_target(self) -> None:
        source = np.arange(18, dtype=np.int16).reshape(3, 6)
        source_mask = np.zeros((3, 6), bool)
        target_mask = np.zeros((3, 6), bool)
        source_mask[:, 0:2] = True
        target_mask[:, 4:6] = True
        template = np.full((3, 6), -1, dtype=np.int16)
        result = mirror_side_layer(
            source, "ISLAND_PAIR", [(source_mask, target_mask)], template
        )
        np.testing.assert_array_equal(result[:, 4:6], source[:, 0:2][:, ::-1])
        self.assertTrue(np.all(result[:, :4] == -1))

    def test_auto_never_uses_paint_values_as_occupancy(self) -> None:
        source = np.ones((2, 3), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, "occupancy"):
            mirror_side_layer(source, "AUTO")


if __name__ == "__main__":
    unittest.main()

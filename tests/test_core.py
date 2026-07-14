from __future__ import annotations

import binascii
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import numpy as np

from quick_sdf_blender.core import (
    ALWAYS_LIGHT,
    ALWAYS_SHADOW,
    RangeScope,
    exact_edt,
    exact_signed_edt,
    generate_threshold_transitions,
    generate_threshold_pair_channels,
    guard_clip_proposal,
    interpolate_binary_masks,
    pack_lane_bits,
    range_target_indices,
    repair_side_monotonic,
    repair_packed_lane,
    unpack_lane_bits,
    validate_monotonic,
    validate_side_monotonic,
)
from quick_sdf_blender.packing import PackingChannelSpec, PackingSource, pack_rgba16
from quick_sdf_blender.png16 import (
    PNG_SIGNATURE,
    commit_png_temporary,
    encode_png_rgba16,
    write_png_rgba16,
    write_png_rgba16_temporary,
)


ANGLES = np.arange(-90.0, 91.0, 15.0)
SIDE_ANGLES = np.arange(0.0, 91.0, 15.0)


def threshold_pair_from_signed(stack: np.ndarray, angles: np.ndarray) -> np.ndarray:
    right_indices = np.flatnonzero(angles >= 0.0)
    left_indices = np.flatnonzero(angles <= 0.0)[::-1]
    return generate_threshold_pair_channels(
        stack[right_indices],
        angles[right_indices],
        stack[left_indices],
        np.abs(angles[left_indices]),
    )


def pack_golden_liltoon_channels(channels: np.ndarray) -> np.ndarray:
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


def brute_distance(features: np.ndarray) -> np.ndarray:
    points = np.argwhere(features)
    if not points.size:
        return np.full(features.shape, np.inf)
    result = np.empty(features.shape, dtype=np.float64)
    for y, x in np.ndindex(features.shape):
        result[y, x] = np.sqrt(np.min(np.sum((points - (y, x)) ** 2, axis=1)))
    return result


def decode_png(data: bytes) -> tuple[dict[str, int], np.ndarray]:
    assert data.startswith(PNG_SIGNATURE)
    position = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while position < len(data):
        size = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + size]
        checksum = struct.unpack_from(">I", data, position + 8 + size)[0]
        assert checksum == (binascii.crc32(kind + payload) & 0xFFFFFFFF)
        chunks.append((kind, payload))
        position += size + 12
    header = next(payload for kind, payload in chunks if kind == b"IHDR")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    raw = zlib.decompress(b"".join(payload for kind, payload in chunks if kind == b"IDAT"))
    stride = width * 8 + 1
    rows = []
    previous = np.zeros(width * 8, dtype=np.uint8)
    for y in range(height):
        filter_type = raw[y * stride]
        row = np.frombuffer(
            raw[y * stride + 1 : (y + 1) * stride], dtype=np.uint8
        ).copy()
        if filter_type == 2:
            row += previous
        else:
            assert filter_type == 0
        rows.append(row.tobytes())
        previous = row
    pixels = np.frombuffer(b"".join(rows), dtype=">u2").reshape(height, width, 4)
    return {
        "width": width,
        "height": height,
        "depth": depth,
        "color": color,
        "compression": compression,
        "filter": filtering,
        "interlace": interlace,
    }, pixels.astype(np.uint16)


class MonotonicRepairTests(unittest.TestCase):
    def test_valid_stack_is_bit_identical_and_idempotent(self) -> None:
        rng = np.random.default_rng(303)
        transitions = rng.integers(0, len(SIDE_ANGLES) + 1, size=(9, 11))
        masks = np.arange(len(SIDE_ANGLES))[:, None, None] >= transitions[None, ...]
        base = masks.copy()
        coverage = rng.random(masks.shape) > 0.95
        result = repair_side_monotonic(masks, base, coverage)
        np.testing.assert_array_equal(result.masks, masks)
        self.assertEqual(result.changed_sample_count, 0)
        second = repair_side_monotonic(result.masks, base, coverage)
        np.testing.assert_array_equal(second.masks, result.masks)
        self.assertEqual(second.changed_sample_count, 0)

    def test_valid_display_wins_even_when_base_and_coverage_are_unrelated(self) -> None:
        rng = np.random.default_rng(3303)
        transitions = rng.integers(0, len(SIDE_ANGLES) + 1, size=(8, 10))
        masks = np.arange(len(SIDE_ANGLES))[:, None, None] >= transitions[None, ...]
        base = rng.random(masks.shape) > 0.5
        coverage = rng.random(masks.shape) > 0.5
        result = repair_side_monotonic(masks, base, coverage)
        np.testing.assert_array_equal(result.masks, masks)
        self.assertEqual(result.changed_sample_count, 0)

    def test_complete_tie_chooses_the_lower_transition_index(self) -> None:
        masks = np.asarray([True, False], dtype=np.bool_)[:, None, None]
        result = repair_side_monotonic(masks, masks, np.zeros_like(masks))
        np.testing.assert_array_equal(result.masks[:, 0, 0], [True, True])
        self.assertEqual(int(result.transition_indices[0, 0]), 0)
        self.assertEqual(result.transition_indices.dtype, np.int32)

    def test_repair_enabled_export_matches_liltoon_png_golden(self) -> None:
        angles = np.asarray([0.0, 20.0, 55.0, 90.0], dtype=np.float64)
        right_transitions = np.asarray([[0, 1, 2, 4], [4, 3, 1, 0]])
        left_transitions = np.asarray([[4, 2, 3, 0], [1, 4, 0, 2]])
        right = np.arange(4)[:, None, None] >= right_transitions[None, ...]
        left = np.arange(4)[:, None, None] >= left_transitions[None, ...]
        coverage = np.indices(right.shape).sum(axis=0) % 3 == 0
        repaired_right = repair_side_monotonic(
            right, ~right, coverage
        ).masks
        repaired_left = repair_side_monotonic(
            left, np.roll(left, 1, axis=0), ~coverage
        ).masks
        channels = generate_threshold_pair_channels(
            repaired_right, angles, repaired_left, angles
        )
        rgba = pack_golden_liltoon_channels(channels)
        np.testing.assert_array_equal(repaired_right, right)
        np.testing.assert_array_equal(repaired_left, left)
        _, decoded = decode_png(encode_png_rgba16(rgba))
        np.testing.assert_array_equal(decoded, rgba)

    def test_coverage_protection_wins_before_total_and_base_cost(self) -> None:
        masks = np.asarray([True, False, True], dtype=np.bool_)[:, None, None]
        base = np.asarray([False, False, True], dtype=np.bool_)[:, None, None]
        coverage = np.zeros_like(masks)
        coverage[0, 0, 0] = True
        result = repair_side_monotonic(masks, base, coverage)
        np.testing.assert_array_equal(result.masks[:, 0, 0], [True, True, True])
        self.assertEqual(result.protected_changed_sample_count, 0)
        self.assertEqual(result.changed_sample_count, 1)

    def test_display_difference_is_protected_without_coverage(self) -> None:
        masks = np.asarray([True, False, True], dtype=np.bool_)[:, None, None]
        base = np.asarray([False, False, True], dtype=np.bool_)[:, None, None]
        result = repair_side_monotonic(masks, base, np.zeros_like(masks))
        np.testing.assert_array_equal(result.masks[:, 0, 0], [True, True, True])
        self.assertEqual(result.protected_changed_sample_count, 0)

    def test_base_breaks_equal_display_cost(self) -> None:
        masks = np.asarray([True, False], dtype=np.bool_)[:, None, None]
        shadow_coverage = np.asarray([False, True], dtype=np.bool_)[:, None, None]
        light_coverage = np.asarray([True, False], dtype=np.bool_)[:, None, None]
        result_light = repair_side_monotonic(
            masks, np.ones_like(masks), light_coverage
        )
        result_shadow = repair_side_monotonic(
            masks, np.zeros_like(masks), shadow_coverage
        )
        np.testing.assert_array_equal(result_light.masks[:, 0, 0], [True, True])
        np.testing.assert_array_equal(result_shadow.masks[:, 0, 0], [False, False])

    def test_random_invalid_stacks_are_repaired_without_mutating_inputs(self) -> None:
        rng = np.random.default_rng(9917)
        masks = rng.random((len(SIDE_ANGLES), 17, 13)) > 0.5
        base = rng.random(masks.shape) > 0.5
        coverage = rng.random(masks.shape) > 0.85
        originals = (masks.copy(), base.copy(), coverage.copy())
        result = repair_side_monotonic(masks, base, coverage)
        self.assertTrue(validate_side_monotonic(result.masks, SIDE_ANGLES).is_valid)
        np.testing.assert_array_equal(masks, originals[0])
        np.testing.assert_array_equal(base, originals[1])
        np.testing.assert_array_equal(coverage, originals[2])
        self.assertEqual(
            result.changed_pixel_count,
            int(np.count_nonzero(np.any(result.changed_mask, axis=0))),
        )

    def test_shape_validation(self) -> None:
        masks = np.zeros((3, 2, 2), dtype=np.bool_)
        with self.assertRaisesRegex(ValueError, "same shape"):
            repair_side_monotonic(masks, masks[:, :, :1], masks)


class PackedLaneTests(unittest.TestCase):
    def test_compact_repair_and_threshold_match_stack_reference(self) -> None:
        rng = np.random.default_rng(7001)
        angles = np.arange(8, dtype=np.float64) * (90.0 / 7.0)
        transitions = rng.integers(0, 9, size=(19, 23), dtype=np.uint8)
        display = np.arange(8)[:, None, None] >= transitions[None, ...]
        # Deliberately make base/coverage unrelated to exercise repair costs.
        base = rng.random(display.shape) > 0.48
        coverage = rng.random(display.shape) > 0.8
        lane = pack_lane_bits(display, angles, base, coverage)
        np.testing.assert_array_equal(unpack_lane_bits(lane.display_bits, 8), display)
        compact = repair_packed_lane(lane)
        reference = repair_side_monotonic(display, base, coverage)
        np.testing.assert_array_equal(
            compact.transition_indices, reference.transition_indices
        )
        np.testing.assert_array_equal(
            compact.changed_count,
            np.count_nonzero(reference.changed_mask, axis=0).astype(np.uint8),
        )
        self.assertEqual(compact.changed_sample_count, reference.changed_sample_count)
        threshold = generate_threshold_transitions(transitions, angles)
        pair = generate_threshold_pair_channels(display, angles, display, angles)
        np.testing.assert_array_equal(threshold, pair[..., 0])

    def test_compact_lane_rejects_more_than_sixteen_keys(self) -> None:
        stack = np.zeros((17, 1, 1), dtype=np.bool_)
        with self.assertRaisesRegex(ValueError, "at most 16"):
            pack_lane_bits(stack, np.linspace(0.0, 90.0, 17), stack, stack)


class ExactEdtTests(unittest.TestCase):
    def test_exact_against_brute_force_non_square(self) -> None:
        for shape in ((1, 5), (5, 1), (4, 7), (7, 4)):
            features = np.zeros(shape, dtype=bool)
            features[0, 0] = True
            features[-1, -1] = True
            if shape[0] > 2 and shape[1] > 2:
                features[2, 1] = True
            np.testing.assert_allclose(exact_edt(features), brute_distance(features), atol=1e-12)

    def test_circle_and_edges_against_brute_force(self) -> None:
        yy, xx = np.ogrid[:9, :11]
        features = (yy - 4) ** 2 + (xx - 5) ** 2 <= 5
        features[0, -1] = True
        np.testing.assert_allclose(exact_edt(features), brute_distance(features), atol=1e-12)

    def test_no_features_is_infinite(self) -> None:
        self.assertTrue(np.all(np.isinf(exact_edt(np.zeros((3, 4), dtype=bool)))))

    def test_signed_convention_and_constant_masks(self) -> None:
        mask = np.asarray([[False, True], [False, True]])
        signed = exact_signed_edt(mask)
        np.testing.assert_allclose(signed, [[1.0, -1.0], [1.0, -1.0]])
        self.assertTrue(np.all(np.isneginf(exact_signed_edt(np.ones((2, 3), dtype=bool)))))
        self.assertTrue(np.all(np.isposinf(exact_signed_edt(np.zeros((2, 3), dtype=bool)))))


class BinaryMaskInterpolationTests(unittest.TestCase):
    def test_exact_sdf_blend_moves_a_straight_boundary(self) -> None:
        first = np.asarray([[True, True, False, False, False, False, False]])
        second = np.asarray([[True, True, True, True, True, True, False]])
        np.testing.assert_array_equal(
            interpolate_binary_masks(first, second, 0.25),
            [[True, True, True, False, False, False, False]],
        )
        np.testing.assert_array_equal(
            interpolate_binary_masks(first, second, 0.5),
            [[True, True, True, True, False, False, False]],
        )

    def test_constant_masks_follow_live_preview_finite_distance_rule(self) -> None:
        shadow = np.zeros((3, 5), dtype=np.bool_)
        light = np.ones_like(shadow)
        self.assertFalse(np.any(interpolate_binary_masks(shadow, light, 0.49)))
        # The exact zero crossing is Light, matching Studio's ``<= 0`` rule.
        self.assertTrue(np.all(interpolate_binary_masks(shadow, light, 0.5)))
        self.assertTrue(np.all(interpolate_binary_masks(shadow, light, 0.51)))
        self.assertTrue(np.all(interpolate_binary_masks(light, light, 0.37)))
        self.assertFalse(np.any(interpolate_binary_masks(shadow, shadow, 0.63)))

    def test_endpoints_are_exact_contiguous_bool_copies(self) -> None:
        first = np.asarray([[0, 255, 0], [255, 0, 255]], dtype=np.uint8)[:, ::-1]
        second = np.asarray([[1, 0, 1], [0, 1, 0]], dtype=np.uint16)
        at_first = interpolate_binary_masks(first, second, 0.0)
        at_second = interpolate_binary_masks(first, second, 1.0)
        self.assertEqual(at_first.dtype, np.bool_)
        self.assertTrue(at_first.flags.c_contiguous)
        np.testing.assert_array_equal(at_first, first >= 0.5)
        np.testing.assert_array_equal(at_second, second >= 0.5)

    def test_validation_and_pre_cancel(self) -> None:
        mask = np.zeros((2, 3), dtype=np.bool_)
        with self.assertRaisesRegex(ValueError, "same shape"):
            interpolate_binary_masks(mask, mask[:, :2], 0.5)
        for factor in (-0.01, 1.01, np.nan, np.inf):
            with self.assertRaisesRegex(ValueError, "factor"):
                interpolate_binary_masks(mask, mask, factor)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            interpolate_binary_masks(mask, mask, 0.5, cancel_flag=True)


class MonotonicTests(unittest.TestCase):
    def test_valid_asymmetric_expansion(self) -> None:
        stack = np.zeros((len(ANGLES), 2, 3), dtype=bool)
        for index, angle in enumerate(ANGLES):
            stack[index, :, 0] = abs(angle) >= 30
            stack[index, :, 1] = (angle >= 60) if angle >= 0 else (abs(angle) >= 45)
        report = validate_monotonic(stack, ANGLES)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.violation_count, 0)

    def test_reports_each_side_and_transition(self) -> None:
        stack = np.ones((len(ANGLES), 2, 2), dtype=bool)
        zero = int(np.where(ANGLES == 0)[0][0])
        plus15 = int(np.where(ANGLES == 15)[0][0])
        minus15 = int(np.where(ANGLES == -15)[0][0])
        stack[plus15, 0, 0] = False
        stack[minus15, 1, 1] = False
        report = validate_monotonic(stack, ANGLES)
        self.assertFalse(report.is_valid)
        self.assertEqual(report.violation_count, 2)
        self.assertEqual(report.violation_pixel_count, 2)
        self.assertTrue(report.positive_violation_map[0, 0])
        self.assertTrue(report.negative_violation_map[1, 1])
        self.assertEqual(len(report.offending_transitions), 2)

    def test_guard_clips_only_offending_entries(self) -> None:
        before = np.zeros((len(ANGLES), 1, 2), dtype=bool)
        proposal = before.copy()
        zero = int(np.where(ANGLES == 0)[0][0])
        plus15 = int(np.where(ANGLES == 15)[0][0])
        plus30 = int(np.where(ANGLES == 30)[0][0])
        proposal[plus15, 0, 0] = True  # isolated Light: invalid and clipped
        proposal[plus30:, 0, 1] = True  # valid expansion: retained
        result = guard_clip_proposal(before, proposal, ANGLES)
        self.assertTrue(result.validation.is_valid)
        self.assertFalse(result.masks[plus15, 0, 0])
        self.assertTrue(result.clipped[plus15, 0, 0])
        self.assertTrue(np.all(result.masks[plus30:, 0, 1]))
        self.assertEqual(result.clipped_entry_count, 1)

    def test_rejects_invalid_guard_baseline(self) -> None:
        stack = np.ones((len(ANGLES), 1, 1), dtype=bool)
        stack[np.where(ANGLES == 15)[0][0], 0, 0] = False
        with self.assertRaises(ValueError):
            guard_clip_proposal(stack, stack, ANGLES)


class RangeTests(unittest.TestCase):
    def test_all_range_modes_on_positive_side(self) -> None:
        active = int(np.where(ANGLES == 30)[0][0])
        values = lambda scope: ANGLES[range_target_indices(ANGLES, active, scope)].tolist()
        self.assertEqual(values(RangeScope.CURRENT), [30.0])
        self.assertEqual(values("TOWARD_FRONT"), [0.0, 15.0, 30.0])
        self.assertEqual(values("TOWARD_SIDE"), [30.0, 45.0, 60.0, 75.0, 90.0])
        self.assertEqual(values("WHOLE_SIDE"), [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0])
        self.assertEqual(values("BOTH_SIDES"), ANGLES.tolist())

    def test_negative_and_zero_ranges(self) -> None:
        active = int(np.where(ANGLES == -30)[0][0])
        selected = ANGLES[range_target_indices(ANGLES, active, "TOWARD_FRONT")]
        self.assertEqual(selected.tolist(), [-30.0, -15.0, 0.0])
        zero = int(np.where(ANGLES == 0)[0][0])
        self.assertEqual(
            ANGLES[range_target_indices(ANGLES, zero, "TOWARD_FRONT")].tolist(), [0.0]
        )
        self.assertEqual(range_target_indices(ANGLES, zero, "TOWARD_SIDE").size, len(ANGLES))


class ThresholdTests(unittest.TestCase):
    def test_full_range_liltoon_values_and_channels(self) -> None:
        stack = np.zeros((len(ANGLES), 1, 3), dtype=bool)
        stack[:, 0, 0] = True  # always Light
        # pixel 1 stays Shadow; pixel 2 transitions differently by side
        for index, angle in enumerate(ANGLES):
            if angle >= 0:
                stack[index, 0, 2] = angle >= 30
            else:
                stack[index, 0, 2] = abs(angle) >= 60
        output = threshold_pair_from_signed(stack, ANGLES)
        self.assertEqual(output.dtype, np.uint16)
        self.assertEqual(output.shape, (1, 3, 2))
        np.testing.assert_array_equal(output[0, 0, :], [ALWAYS_LIGHT, ALWAYS_LIGHT])
        np.testing.assert_array_equal(output[0, 1, :], [ALWAYS_SHADOW, ALWAYS_SHADOW])
        self.assertTrue(1 <= int(output[0, 2, 0]) <= 65534)
        self.assertTrue(1 <= int(output[0, 2, 1]) <= 65534)
        # Earlier Light transitions have larger lilToon SDF values.
        self.assertGreater(output[0, 2, 0], output[0, 2, 1])

    def test_sdf_ratio_places_straight_boundary_halfway(self) -> None:
        angles = np.asarray([-90.0, 0.0, 90.0])
        stack = np.zeros((3, 1, 2), dtype=bool)
        stack[0] = True
        stack[2] = True
        output = threshold_pair_from_signed(stack, angles)
        # Adjacent black/white pixel centres have equal |SDF|, so transition is 45 degrees.
        expected = int(np.floor((1.0 - 0.5) * 65535 + 0.5))
        np.testing.assert_array_equal(output[0, :, 0], [expected, expected])
        np.testing.assert_array_equal(output[0, :, 1], [expected, expected])

    def test_non_monotonic_export_is_rejected(self) -> None:
        stack = np.ones((len(ANGLES), 1, 1), dtype=bool)
        stack[np.where(ANGLES == 15)[0][0], 0, 0] = False
        with self.assertRaisesRegex(ValueError, "not monotonic"):
            threshold_pair_from_signed(stack, ANGLES)

    def test_generation_requires_both_side_endpoints(self) -> None:
        masks = np.ones((3, 1, 1), dtype=np.bool_)
        with self.assertRaisesRegex(ValueError, "0 degree mask"):
            generate_threshold_pair_channels(
                masks, [15, 45, 90], masks, [0, 45, 90]
            )


class PngTests(unittest.TestCase):
    def test_byte_exact_rgba16_roundtrip(self) -> None:
        pixels = np.asarray(
            [[[0x0000, 0x0001, 0x00FF, 0xFFFF], [0x1234, 0xABCD, 0x8000, 0x7FFF]]],
            dtype=np.uint16,
        )
        header, decoded = decode_png(encode_png_rgba16(pixels, compress_level=9))
        self.assertEqual(
            header,
            {"width": 2, "height": 1, "depth": 16, "color": 6, "compression": 0, "filter": 0, "interlace": 0},
        )
        np.testing.assert_array_equal(decoded, pixels)

    def test_atomic_write_and_overwrite_policy(self) -> None:
        pixels = np.zeros((2, 2, 4), dtype=np.uint16)
        pixels[..., 3] = 65535
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "threshold.png"
            self.assertEqual(write_png_rgba16(path, pixels), path)
            _, decoded = decode_png(path.read_bytes())
            np.testing.assert_array_equal(decoded, pixels)
            with self.assertRaises(FileExistsError):
                write_png_rgba16(path, pixels)
            pixels[..., 0] = 42
            write_png_rgba16(path, pixels, overwrite=True)
            _, decoded = decode_png(path.read_bytes())
            np.testing.assert_array_equal(decoded, pixels)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_worker_temporary_can_be_revision_checked_before_publish(self) -> None:
        pixels = np.arange(4 * 7 * 4, dtype=np.uint16).reshape(4, 7, 4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "threshold.png"
            temporary = write_png_rgba16_temporary(path, pixels)
            self.assertTrue(temporary.exists())
            self.assertFalse(path.exists())
            commit_png_temporary(temporary, path)
            self.assertFalse(temporary.exists())
            _, decoded = decode_png(path.read_bytes())
            np.testing.assert_array_equal(decoded, pixels)

    def test_rejects_non_uint16_or_wrong_shape(self) -> None:
        with self.assertRaises(TypeError):
            encode_png_rgba16(np.zeros((2, 2, 4), dtype=np.uint8))
        with self.assertRaises(ValueError):
            encode_png_rgba16(np.zeros((2, 2, 3), dtype=np.uint16))


if __name__ == "__main__":
    unittest.main()

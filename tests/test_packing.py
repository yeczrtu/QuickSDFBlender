# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.packing import (
    PackingChannelSpec,
    PackingSource,
    pack_rgba16,
    quantize_unorm16,
)


class QuantizeUnorm16Tests(unittest.TestCase):
    def test_round_half_up_and_clamp_are_exact(self) -> None:
        values = np.asarray(
            [[-1.0, 0.0, 0.5, 1.0, 2.0, 1.0 / 65535.0]], dtype=np.float64
        )
        np.testing.assert_array_equal(
            quantize_unorm16(values),
            np.asarray([[0, 0, 32768, 65535, 65535, 1]], dtype=np.uint16),
        )

    def test_uint16_is_lossless_and_boolean_uses_endpoints(self) -> None:
        values = np.asarray([[0, 1, 32768, 65535]], dtype=np.uint16)
        np.testing.assert_array_equal(quantize_unorm16(values), values)
        np.testing.assert_array_equal(
            quantize_unorm16(np.asarray([[False, True]], dtype=np.bool_)),
            np.asarray([[0, 65535]], dtype=np.uint16),
        )

    def test_rejects_non_planes_non_numeric_and_non_finite_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "two-dimensional"):
            quantize_unorm16(np.zeros((1, 2, 3), dtype=np.float32))
        with self.assertRaisesRegex(TypeError, "real numeric"):
            quantize_unorm16(np.asarray([["white"]], dtype=object))
        for value in (np.nan, np.inf, -np.inf):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite"):
                quantize_unorm16(np.asarray([[value]], dtype=np.float64))


class PackRgba16Tests(unittest.TestCase):
    def test_liltoon_defaults_pack_thresholds_area_and_strength(self) -> None:
        right = np.asarray([[0, 1], [32768, 65535]], dtype=np.uint16)
        left = np.asarray([[65535, 32768], [1, 0]], dtype=np.uint16)
        area = np.asarray([[True, False], [True, False]], dtype=np.bool_)
        strength = np.asarray([[1.0, 0.5], [0.0, 0.25]], dtype=np.float64)
        specs = (
            PackingChannelSpec(PackingSource.RIGHT_THRESHOLD),
            PackingChannelSpec(PackingSource.LEFT_THRESHOLD),
            PackingChannelSpec(
                PackingSource.SDF_AREA,
                invert=True,
                auxiliary_mask_uuid="area-uuid",
            ),
            PackingChannelSpec(
                PackingSource.SHADOW_STRENGTH,
                auxiliary_mask_uuid="strength-uuid",
            ),
        )
        packed = pack_rgba16(
            {
                PackingSource.RIGHT_THRESHOLD: right,
                PackingSource.LEFT_THRESHOLD: left,
                "area-uuid": area,
                "strength-uuid": strength,
            },
            specs,
        )
        self.assertEqual(packed.dtype, np.uint16)
        self.assertEqual(packed.shape, (2, 2, 4))
        self.assertTrue(packed.flags.c_contiguous)
        np.testing.assert_array_equal(packed[..., 0], right)
        np.testing.assert_array_equal(packed[..., 1], left)
        np.testing.assert_array_equal(
            packed[..., 2], np.asarray([[0, 65535], [0, 65535]], dtype=np.uint16)
        )
        np.testing.assert_array_equal(
            packed[..., 3], np.asarray([[65535, 32768], [0, 16384]], dtype=np.uint16)
        )

    def test_channel_reordering_duplicate_sources_custom_mask_and_invert(self) -> None:
        right = np.asarray([[0, 1000, 65535]], dtype=np.uint16)
        custom = np.asarray([[0.0, 0.5, 1.0]], dtype=np.float64)
        packed = pack_rgba16(
            {
                PackingSource.RIGHT_THRESHOLD: right,
                "custom-uuid": custom,
            },
            (
                PackingChannelSpec(
                    PackingSource.CUSTOM_MASK,
                    invert=True,
                    auxiliary_mask_uuid="custom-uuid",
                ),
                PackingChannelSpec(PackingSource.RIGHT_THRESHOLD),
                PackingChannelSpec(PackingSource.RIGHT_THRESHOLD, invert=True),
                PackingChannelSpec(PackingSource.CONSTANT, constant_value=0.5),
            ),
        )
        np.testing.assert_array_equal(
            packed[0, :, 0], np.asarray([65535, 32767, 0], dtype=np.uint16)
        )
        np.testing.assert_array_equal(packed[0, :, 1], right[0])
        np.testing.assert_array_equal(
            packed[0, :, 2], np.asarray([65535, 64535, 0], dtype=np.uint16)
        )
        np.testing.assert_array_equal(
            packed[0, :, 3], np.asarray([32768, 32768, 32768], dtype=np.uint16)
        )

    def test_standard_mask_names_work_without_project_uuids(self) -> None:
        zeros = np.zeros((1, 2), dtype=np.uint16)
        packed = pack_rgba16(
            {
                PackingSource.SDF_AREA: np.asarray([[False, True]]),
                PackingSource.SHADOW_STRENGTH: np.asarray([[0.25, 0.75]]),
            },
            (
                PackingChannelSpec(PackingSource.SDF_AREA),
                PackingChannelSpec(PackingSource.SHADOW_STRENGTH),
                PackingChannelSpec(PackingSource.CONSTANT, constant_value=-1.0),
                PackingChannelSpec(PackingSource.CONSTANT, constant_value=2.0),
            ),
        )
        np.testing.assert_array_equal(
            packed,
            np.stack(
                (
                    np.asarray([[0, 65535]], dtype=np.uint16),
                    np.asarray([[16384, 49151]], dtype=np.uint16),
                    zeros,
                    np.full((1, 2), 65535, dtype=np.uint16),
                ),
                axis=-1,
            ),
        )

    def test_mapping_specs_and_explicit_constant_shape(self) -> None:
        packed = pack_rgba16(
            {},
            (
                {"source": "constant", "constant_value": 0.0},
                {"source": "CONSTANT", "constant_value": 1.0 / 65535.0},
                {"source": "CONSTANT", "constant_value": 0.5, "invert": True},
                {"source": PackingSource.CONSTANT, "constant_value": 1.0},
            ),
            shape=(2, 3),
        )
        np.testing.assert_array_equal(packed[0, 0], [0, 1, 32767, 65535])
        self.assertTrue(np.all(packed == packed[0, 0]))

    def test_all_constant_shape_can_be_inferred_from_an_unused_signal(self) -> None:
        packed = pack_rgba16(
            {"dimensions-only": np.zeros((3, 5), dtype=np.uint16)},
            (PackingChannelSpec(PackingSource.CONSTANT),) * 4,
        )
        self.assertEqual(packed.shape, (3, 5, 4))
        self.assertFalse(np.any(packed))

    def test_recipe_and_signal_validation_reports_actionable_errors(self) -> None:
        constant = PackingChannelSpec(PackingSource.CONSTANT)
        with self.assertRaisesRegex(ValueError, "exactly four"):
            pack_rgba16({}, (constant,) * 3, shape=(1, 1))
        with self.assertRaisesRegex(ValueError, "cannot be inferred"):
            pack_rgba16({}, (constant,) * 4)
        with self.assertRaisesRegex(ValueError, "unknown packing source"):
            pack_rgba16(
                {},
                ({"source": "NOT_A_SOURCE"}, constant, constant, constant),
                shape=(1, 1),
            )
        with self.assertRaisesRegex(ValueError, "missing signal"):
            pack_rgba16(
                {},
                (
                    PackingChannelSpec(PackingSource.RIGHT_THRESHOLD),
                    constant,
                    constant,
                    constant,
                ),
                shape=(1, 1),
            )
        with self.assertRaisesRegex(ValueError, "requires an auxiliary_mask_uuid"):
            pack_rgba16(
                {},
                (
                    PackingChannelSpec(PackingSource.CUSTOM_MASK),
                    constant,
                    constant,
                    constant,
                ),
                shape=(1, 1),
            )

    def test_mismatched_shapes_and_invalid_spec_fields_are_rejected(self) -> None:
        constant = PackingChannelSpec(PackingSource.CONSTANT)
        with self.assertRaisesRegex(ValueError, "expected"):
            pack_rgba16(
                {
                    PackingSource.RIGHT_THRESHOLD: np.zeros((1, 2), dtype=np.uint16),
                    PackingSource.LEFT_THRESHOLD: np.zeros((2, 1), dtype=np.uint16),
                },
                (
                    PackingChannelSpec(PackingSource.RIGHT_THRESHOLD),
                    PackingChannelSpec(PackingSource.LEFT_THRESHOLD),
                    constant,
                    constant,
                ),
            )
        with self.assertRaisesRegex(ValueError, "invert must be a boolean"):
            pack_rgba16(
                {},
                (
                    PackingChannelSpec(PackingSource.CONSTANT, invert="False"),
                    constant,
                    constant,
                    constant,
                ),
                shape=(1, 1),
            )


if __name__ == "__main__":
    unittest.main()

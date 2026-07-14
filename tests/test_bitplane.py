# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest

import numpy as np

from quick_sdf_blender.bitplane import (
    BitplaneCodec,
    BitplaneError,
    BitplaneRole,
    DecodedBitplaneCache,
    DEFAULT_CACHE_BYTE_BUDGET,
    HEADER_SIZE,
    decode_bitplane,
    decode_bitplane_packed,
    encode_bitplane,
    insert_bitplane_into_uint16,
    inspect_bitplane_header,
)


class BitplaneRoundTripTests(unittest.TestCase):
    def assert_round_trip(self, plane: np.ndarray, role: BitplaneRole) -> bytes:
        blob = encode_bitplane(plane, role)
        decoded = decode_bitplane(blob, expected_role=role)
        self.assertEqual(decoded.dtype, np.bool_)
        self.assertEqual(decoded.shape, plane.shape)
        self.assertTrue(decoded.flags.c_contiguous)
        self.assertTrue(decoded.flags.writeable)
        np.testing.assert_array_equal(decoded, plane)
        return blob

    def test_all_black_and_all_white(self) -> None:
        for value in (False, True):
            with self.subTest(value=value):
                plane = np.full((129, 257), value, dtype=np.bool_)
                blob = self.assert_round_trip(plane, BitplaneRole.BASE)
                self.assertEqual(inspect_bitplane_header(blob).codec, BitplaneCodec.ZLIB)

    def test_random_high_entropy_plane_uses_raw_payload(self) -> None:
        rng = np.random.default_rng(20260714)
        plane = rng.integers(0, 2, size=(64, 65), dtype=np.uint8).astype(np.bool_)
        blob = self.assert_round_trip(plane, BitplaneRole.COVERAGE)
        header = inspect_bitplane_header(blob)
        self.assertEqual(header.codec, BitplaneCodec.RAW)
        self.assertEqual(header.raw_size, (plane.size + 7) // 8)
        self.assertEqual(header.payload_size, header.raw_size)

    def test_non_byte_aligned_widths_round_trip_without_row_padding(self) -> None:
        rng = np.random.default_rng(81)
        for width in (1, 2, 7, 9, 13, 31):
            with self.subTest(width=width):
                plane = rng.integers(0, 2, size=(5, width), dtype=np.uint8).astype(np.bool_)
                blob = self.assert_round_trip(plane, BitplaneRole.COVERAGE)
                self.assertEqual(
                    inspect_bitplane_header(blob).raw_size,
                    (plane.shape[0] * width + 7) // 8,
                )

    def test_packing_is_top_down_c_order_and_little_bit_order(self) -> None:
        plane = np.asarray([[True, False, True, False, False, False, False, False]])
        blob = encode_bitplane(plane, "BASE")
        header = inspect_bitplane_header(blob)
        self.assertEqual(header.codec, BitplaneCodec.RAW)
        self.assertEqual(blob[HEADER_SIZE:], b"\x05")

    def test_input_and_role_validation(self) -> None:
        with self.assertRaisesRegex(TypeError, "NumPy"):
            encode_bitplane([[True]], BitplaneRole.BASE)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "two-dimensional"):
            encode_bitplane(np.zeros((1, 1, 1), dtype=np.bool_), BitplaneRole.BASE)
        with self.assertRaisesRegex(TypeError, "boolean dtype"):
            encode_bitplane(np.zeros((1, 1), dtype=np.uint8), BitplaneRole.BASE)
        with self.assertRaisesRegex(ValueError, "unknown bitplane role"):
            encode_bitplane(np.zeros((1, 1), dtype=np.bool_), "OTHER")

    def test_validated_packed_payload_and_direct_bitfield_insert(self) -> None:
        rng = np.random.default_rng(44)
        first = rng.integers(0, 2, (17, 19), dtype=np.uint8).astype(np.bool_)
        second = rng.integers(0, 2, (17, 19), dtype=np.uint8).astype(np.bool_)
        first_blob = encode_bitplane(first, BitplaneRole.BASE)
        second_blob = encode_bitplane(second, BitplaneRole.COVERAGE)
        header, packed = decode_bitplane_packed(first_blob, expected_role="BASE")
        self.assertEqual(header.shape, first.shape)
        self.assertEqual(len(packed), (first.size + 7) // 8)

        bitfield = np.zeros(first.shape, dtype=np.uint16)
        insert_bitplane_into_uint16(
            first_blob, 2, bitfield, expected_role=BitplaneRole.BASE, chunk_bytes=3
        )
        insert_bitplane_into_uint16(
            second_blob, 11, bitfield, expected_role=BitplaneRole.COVERAGE, chunk_bytes=5
        )
        np.testing.assert_array_equal((bitfield & (1 << 2)) != 0, first)
        np.testing.assert_array_equal((bitfield & (1 << 11)) != 0, second)

    def test_direct_bitfield_insert_validates_output(self) -> None:
        plane = np.zeros((3, 5), dtype=np.bool_)
        blob = encode_bitplane(plane, BitplaneRole.BASE)
        with self.assertRaisesRegex(ValueError, "0..15"):
            insert_bitplane_into_uint16(blob, 16, np.zeros((3, 5), np.uint16))
        with self.assertRaisesRegex(TypeError, "uint16"):
            insert_bitplane_into_uint16(blob, 0, np.zeros((3, 5), np.uint8))
        with self.assertRaisesRegex(ValueError, "does not match"):
            insert_bitplane_into_uint16(blob, 0, np.zeros((4, 5), np.uint16))


class BitplaneCorruptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plane = np.zeros((64, 67), dtype=np.bool_)
        self.blob = encode_bitplane(self.plane, BitplaneRole.BASE)

    def test_truncated_bad_magic_and_extra_payload_are_rejected(self) -> None:
        with self.assertRaisesRegex(BitplaneError, "shorter"):
            decode_bitplane(self.blob[:8])
        broken_magic = bytearray(self.blob)
        broken_magic[0] ^= 0xFF
        with self.assertRaisesRegex(BitplaneError, "magic"):
            decode_bitplane(broken_magic)
        with self.assertRaisesRegex(BitplaneError, "payload length"):
            decode_bitplane(self.blob + b"extra")

    def test_payload_corruption_and_role_mismatch_are_rejected(self) -> None:
        corrupt = bytearray(self.blob)
        corrupt[-1] ^= 0x40
        with self.assertRaisesRegex(BitplaneError, "corrupt|invalid|CRC32"):
            decode_bitplane(corrupt)
        with self.assertRaisesRegex(BitplaneError, "role is BASE"):
            decode_bitplane(self.blob, expected_role=BitplaneRole.COVERAGE)

    def test_raw_crc_corruption_is_rejected(self) -> None:
        rng = np.random.default_rng(13)
        random_plane = rng.integers(0, 2, (32, 33), dtype=np.uint8).astype(np.bool_)
        blob = bytearray(encode_bitplane(random_plane, BitplaneRole.COVERAGE))
        self.assertEqual(inspect_bitplane_header(blob).codec, BitplaneCodec.RAW)
        blob[-1] ^= 1
        with self.assertRaisesRegex(BitplaneError, "CRC32"):
            decode_bitplane(blob)

    def test_decode_safety_limit_is_enforced(self) -> None:
        with self.assertRaisesRegex(BitplaneError, "safety limit"):
            decode_bitplane(self.blob, max_raw_bytes=1)


class DecodedBitplaneCacheTests(unittest.TestCase):
    def test_default_budget_is_64_mib(self) -> None:
        self.assertEqual(DEFAULT_CACHE_BYTE_BUDGET, 64 * 1024 * 1024)
        self.assertEqual(DecodedBitplaneCache().byte_budget, DEFAULT_CACHE_BYTE_BUDGET)

    def test_cache_returns_read_only_plane_and_respects_revision(self) -> None:
        first = np.zeros((4, 5), dtype=np.bool_)
        second = first.copy()
        second[1, 2] = True
        cache = DecodedBitplaneCache(byte_budget=first.size * 2)
        first_blob = encode_bitplane(first, BitplaneRole.BASE)
        second_blob = encode_bitplane(second, BitplaneRole.BASE)

        resolved = cache.decode("angle-a", 1, first_blob, expected_role="BASE")
        self.assertFalse(resolved.flags.writeable)
        self.assertIs(resolved, cache.decode("angle-a", 1, first_blob))
        updated = cache.decode("angle-a", 2, second_blob)
        self.assertIsNot(resolved, updated)
        np.testing.assert_array_equal(updated, second)
        self.assertEqual(cache.entry_count, 2)

    def test_lru_evicts_and_invalidate_releases_bytes(self) -> None:
        plane = np.zeros((3, 5), dtype=np.bool_)
        blob = encode_bitplane(plane, BitplaneRole.COVERAGE)
        cache = DecodedBitplaneCache(byte_budget=plane.nbytes * 2)
        cache.decode("a", 1, blob)
        cache.decode("b", 1, blob)
        cache.decode("a", 1, blob)  # a is now most recently used.
        cache.decode("c", 1, blob)
        self.assertEqual(cache.entry_count, 2)
        cache.invalidate("a")
        self.assertEqual(cache.entry_count, 1)
        self.assertEqual(cache.bytes_used, plane.nbytes)
        cache.clear()
        self.assertEqual(cache.entry_count, 0)
        self.assertEqual(cache.bytes_used, 0)


if __name__ == "__main__":
    unittest.main()

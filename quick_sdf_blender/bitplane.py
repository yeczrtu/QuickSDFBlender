# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact, validated storage for Quick SDF binary image planes.

The serialized form is deliberately Blender-independent.  Pixels are read in
top-to-bottom C order, flattened, and packed least-significant-bit first.  A
small fixed-width header makes blobs self-describing while a CRC protects the
uncompressed packed pixels from damaged ``.blend`` custom properties.

Decoded planes are ordinary two-dimensional NumPy boolean arrays.  Callers
that repeatedly resolve project data may use :class:`DecodedBitplaneCache`;
cached arrays are read-only so one editor cannot accidentally change another
editor's source-of-truth data.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import IntEnum
import struct
from threading import RLock
from typing import Final
import zlib

import numpy as np


MAGIC: Final = b"QSDFBIT\0"
FORMAT_VERSION: Final = 1
DEFAULT_CACHE_BYTE_BUDGET: Final = 64 * 1024 * 1024
DEFAULT_MAX_RAW_BYTES: Final = 512 * 1024 * 1024
DEFAULT_INSERT_CHUNK_BYTES: Final = 256 * 1024


class BitplaneError(ValueError):
    """Raised when a serialized bitplane is malformed or corrupt."""


class BitplaneRole(IntEnum):
    """Semantic role of a binary plane stored on an angle key."""

    BASE = 1
    COVERAGE = 2


class BitplaneCodec(IntEnum):
    """Payload encoding used after packing pixels to one bit each."""

    RAW = 0
    ZLIB = 1


# magic, version, role, codec, reserved flags, width, height, raw bytes,
# payload bytes, CRC32 of the raw packed bytes.
_HEADER = struct.Struct("<8sBBBBIIQQI")
HEADER_SIZE: Final = _HEADER.size


@dataclass(frozen=True, slots=True)
class BitplaneHeader:
    """Validated metadata from a serialized bitplane header."""

    version: int
    role: BitplaneRole
    codec: BitplaneCodec
    width: int
    height: int
    raw_size: int
    payload_size: int
    crc32: int

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def pixel_count(self) -> int:
        return self.height * self.width


def _coerce_role(value: BitplaneRole | str | int) -> BitplaneRole:
    if isinstance(value, BitplaneRole):
        return value
    if isinstance(value, str):
        try:
            return BitplaneRole[value.upper()]
        except KeyError as error:
            raise ValueError(f"unknown bitplane role: {value!r}") from error
    if isinstance(value, bool):
        raise TypeError("bitplane role must not be a boolean")
    try:
        return BitplaneRole(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unknown bitplane role: {value!r}") from error


def _as_blob_view(blob: bytes | bytearray | memoryview) -> memoryview:
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise TypeError("bitplane blob must be bytes-like")
    try:
        view = memoryview(blob).cast("B")
    except (TypeError, ValueError) as error:
        raise TypeError("bitplane blob must be a contiguous byte buffer") from error
    return view


def inspect_bitplane_header(
    blob: bytes | bytearray | memoryview,
) -> BitplaneHeader:
    """Return validated structural metadata without decoding the payload.

    This checks the format, dimensions and exact payload length.  Payload
    decompression and the CRC are intentionally deferred to
    :func:`decode_bitplane`.
    """

    view = _as_blob_view(blob)
    if len(view) < HEADER_SIZE:
        raise BitplaneError("bitplane blob is shorter than its header")
    (
        magic,
        version,
        raw_role,
        raw_codec,
        flags,
        width,
        height,
        raw_size,
        payload_size,
        checksum,
    ) = _HEADER.unpack_from(view)
    if magic != MAGIC:
        raise BitplaneError("invalid bitplane magic")
    if version != FORMAT_VERSION:
        raise BitplaneError(f"unsupported bitplane format version: {version}")
    if flags != 0:
        raise BitplaneError("unsupported bitplane header flags")
    try:
        role = BitplaneRole(raw_role)
    except ValueError as error:
        raise BitplaneError(f"unknown bitplane role value: {raw_role}") from error
    try:
        codec = BitplaneCodec(raw_codec)
    except ValueError as error:
        raise BitplaneError(f"unknown bitplane codec value: {raw_codec}") from error
    if width <= 0 or height <= 0:
        raise BitplaneError("bitplane dimensions must be positive")

    expected_raw_size = (int(width) * int(height) + 7) // 8
    if raw_size != expected_raw_size:
        raise BitplaneError(
            f"bitplane raw size {raw_size} does not match dimensions "
            f"{width}x{height} ({expected_raw_size} bytes)"
        )
    if codec is BitplaneCodec.RAW and payload_size != raw_size:
        raise BitplaneError("raw bitplane payload size does not match raw size")
    if payload_size <= 0:
        raise BitplaneError("bitplane payload must not be empty")
    if len(view) != HEADER_SIZE + payload_size:
        raise BitplaneError("bitplane payload length does not match its header")
    return BitplaneHeader(
        version=int(version),
        role=role,
        codec=codec,
        width=int(width),
        height=int(height),
        raw_size=int(raw_size),
        payload_size=int(payload_size),
        crc32=int(checksum),
    )


def encode_bitplane(
    plane: np.ndarray,
    role: BitplaneRole | str | int,
) -> bytes:
    """Serialize a two-dimensional boolean plane.

    The packed payload uses zlib level 1 only when it is strictly smaller than
    the raw packed bytes.  This keeps high-entropy paint data cheap to encode
    while dramatically reducing uniform Base and Coverage planes.
    """

    if not isinstance(plane, np.ndarray):
        raise TypeError("bitplane must be a NumPy array")
    if plane.ndim != 2:
        raise ValueError(f"bitplane must be two-dimensional, got shape {plane.shape}")
    if plane.shape[0] <= 0 or plane.shape[1] <= 0:
        raise ValueError("bitplane dimensions must be positive")
    if plane.dtype != np.bool_:
        raise TypeError("bitplane must use the boolean dtype")
    height, width = (int(plane.shape[0]), int(plane.shape[1]))
    if width > np.iinfo(np.uint32).max or height > np.iinfo(np.uint32).max:
        raise ValueError("bitplane dimensions exceed the file format limit")
    semantic_role = _coerce_role(role)

    pixels = np.ascontiguousarray(plane).reshape(-1)
    raw = np.packbits(pixels, bitorder="little").tobytes(order="C")
    compressed = zlib.compress(raw, level=1)
    if len(compressed) < len(raw):
        codec = BitplaneCodec.ZLIB
        payload = compressed
    else:
        codec = BitplaneCodec.RAW
        payload = raw
    checksum = zlib.crc32(raw) & 0xFFFFFFFF
    header = _HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        int(semantic_role),
        int(codec),
        0,
        width,
        height,
        len(raw),
        len(payload),
        checksum,
    )
    return header + payload


def _decode_payload(
    view: memoryview,
    header: BitplaneHeader,
    *,
    max_raw_bytes: int,
) -> bytes:
    if header.raw_size > max_raw_bytes:
        raise BitplaneError(
            f"bitplane expands to {header.raw_size} bytes, above the "
            f"{max_raw_bytes} byte safety limit"
        )
    payload = view[HEADER_SIZE:]
    if header.codec is BitplaneCodec.RAW:
        raw = payload.tobytes()
    else:
        decompressor = zlib.decompressobj()
        try:
            raw = decompressor.decompress(payload, header.raw_size + 1)
        except zlib.error as error:
            raise BitplaneError("corrupt compressed bitplane payload") from error
        if (
            len(raw) != header.raw_size
            or not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
        ):
            raise BitplaneError("compressed bitplane payload has an invalid size")
    if len(raw) != header.raw_size:
        raise BitplaneError("decoded bitplane payload has an invalid size")
    if zlib.crc32(raw) & 0xFFFFFFFF != header.crc32:
        raise BitplaneError("bitplane CRC32 mismatch")

    used_bits = header.pixel_count & 7
    if used_bits and raw[-1] & ~((1 << used_bits) - 1):
        raise BitplaneError("bitplane has non-zero padding bits")
    return raw


def decode_bitplane(
    blob: bytes | bytearray | memoryview,
    *,
    expected_role: BitplaneRole | str | int | None = None,
    max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
) -> np.ndarray:
    """Deserialize a blob into a writable, contiguous boolean plane."""

    if isinstance(max_raw_bytes, bool) or not isinstance(max_raw_bytes, int):
        raise TypeError("max_raw_bytes must be an integer")
    if max_raw_bytes <= 0:
        raise ValueError("max_raw_bytes must be positive")
    view = _as_blob_view(blob)
    header = inspect_bitplane_header(view)
    if expected_role is not None:
        semantic_role = _coerce_role(expected_role)
        if header.role is not semantic_role:
            raise BitplaneError(
                f"bitplane role is {header.role.name}, expected {semantic_role.name}"
            )
    raw = _decode_payload(view, header, max_raw_bytes=max_raw_bytes)
    packed = np.frombuffer(raw, dtype=np.uint8)
    unpacked = np.unpackbits(
        packed,
        count=header.pixel_count,
        bitorder="little",
    )
    return np.ascontiguousarray(unpacked.reshape(header.shape), dtype=np.bool_)


def decode_bitplane_packed(
    blob: bytes | bytearray | memoryview,
    *,
    expected_role: BitplaneRole | str | int | None = None,
    max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
) -> tuple[BitplaneHeader, bytes]:
    """Validate a bitplane and return its original little-bit packed pixels.

    Export snapshot code can consume this representation without expanding a
    full boolean image.  The returned bytes are top-down row-major and have
    exactly ``header.raw_size`` bytes.
    """

    if isinstance(max_raw_bytes, bool) or not isinstance(max_raw_bytes, int):
        raise TypeError("max_raw_bytes must be an integer")
    if max_raw_bytes <= 0:
        raise ValueError("max_raw_bytes must be positive")
    view = _as_blob_view(blob)
    header = inspect_bitplane_header(view)
    if expected_role is not None:
        semantic_role = _coerce_role(expected_role)
        if header.role is not semantic_role:
            raise BitplaneError(
                f"bitplane role is {header.role.name}, expected {semantic_role.name}"
            )
    return header, _decode_payload(view, header, max_raw_bytes=max_raw_bytes)


def insert_bitplane_into_uint16(
    blob: bytes | bytearray | memoryview,
    bit_index: int,
    out: np.ndarray,
    *,
    expected_role: BitplaneRole | str | int | None = None,
    chunk_bytes: int = DEFAULT_INSERT_CHUNK_BYTES,
) -> BitplaneHeader:
    """OR one serialized plane into a uint16 per-pixel key bit field.

    Only a bounded lookup chunk is expanded.  This is the schema-6 bridge for
    ABI-7 ``PackedLane`` snapshots and avoids the decoded bitplane LRU.
    """

    if isinstance(bit_index, bool) or not isinstance(bit_index, (int, np.integer)):
        raise TypeError("bit_index must be an integer")
    bit_index = int(bit_index)
    if not 0 <= bit_index < 16:
        raise ValueError("bit_index must be in the range 0..15")
    if not isinstance(out, np.ndarray):
        raise TypeError("out must be a NumPy array")
    if out.ndim != 2 or out.dtype != np.uint16:
        raise TypeError("out must be a two-dimensional uint16 array")
    if not out.flags.c_contiguous or not out.flags.writeable:
        raise ValueError("out must be writable and C-contiguous")
    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int):
        raise TypeError("chunk_bytes must be an integer")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")

    header, raw = decode_bitplane_packed(blob, expected_role=expected_role)
    if out.shape != header.shape:
        raise ValueError(
            f"out shape {out.shape} does not match bitplane shape {header.shape}"
        )
    packed = np.frombuffer(raw, dtype=np.uint8)
    output = out.reshape(-1)
    lookup = (
        (np.arange(256, dtype=np.uint16)[:, None] >> np.arange(8, dtype=np.uint16))
        & np.uint16(1)
    )
    output_mask = np.uint16(1 << bit_index)
    for byte_start in range(0, packed.size, chunk_bytes):
        byte_end = min(packed.size, byte_start + chunk_bytes)
        pixel_start = byte_start * 8
        pixel_end = min(header.pixel_count, byte_end * 8)
        expanded = lookup[packed[byte_start:byte_end]].reshape(-1)
        output[pixel_start:pixel_end] |= (
            expanded[: pixel_end - pixel_start] * output_mask
        )
    return header


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    identifier: str
    plane: np.ndarray


class DecodedBitplaneCache:
    """Thread-safe byte-bounded LRU for unpacked boolean planes.

    Cache keys include the caller's stable image/key identifier, its revision,
    and the serialized CRC.  Returned arrays are read-only; use ``.copy()``
    before applying paint or rebuild operations.
    """

    def __init__(self, byte_budget: int = DEFAULT_CACHE_BYTE_BUDGET) -> None:
        if isinstance(byte_budget, bool) or not isinstance(byte_budget, int):
            raise TypeError("byte_budget must be an integer")
        if byte_budget < 0:
            raise ValueError("byte_budget must not be negative")
        self.byte_budget = byte_budget
        self._entries: OrderedDict[
            tuple[str, int, int, int, int, int], _CacheEntry
        ] = OrderedDict()
        self._bytes_used = 0
        self._lock = RLock()

    @property
    def bytes_used(self) -> int:
        with self._lock:
            return self._bytes_used

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes_used = 0

    def invalidate(self, identifier: str) -> None:
        """Remove every cached revision belonging to ``identifier``."""

        if not isinstance(identifier, str) or not identifier:
            raise TypeError("cache identifier must be a non-empty string")
        with self._lock:
            stale = [key for key, entry in self._entries.items() if entry.identifier == identifier]
            for key in stale:
                self._bytes_used -= self._entries.pop(key).plane.nbytes

    def decode(
        self,
        identifier: str,
        revision: int,
        blob: bytes | bytearray | memoryview,
        *,
        expected_role: BitplaneRole | str | int | None = None,
        max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
    ) -> np.ndarray:
        """Decode or resolve one immutable cached plane."""

        if not isinstance(identifier, str) or not identifier:
            raise TypeError("cache identifier must be a non-empty string")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise TypeError("cache revision must be an integer")
        header = inspect_bitplane_header(blob)
        key = (
            identifier,
            revision,
            header.crc32,
            int(header.role),
            header.width,
            header.height,
        )
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                if expected_role is not None and header.role is not _coerce_role(expected_role):
                    raise BitplaneError(
                        f"bitplane role is {header.role.name}, expected "
                        f"{_coerce_role(expected_role).name}"
                    )
                return cached.plane

        plane = decode_bitplane(
            blob,
            expected_role=expected_role,
            max_raw_bytes=max_raw_bytes,
        )
        plane.setflags(write=False)
        if plane.nbytes > self.byte_budget:
            return plane
        with self._lock:
            # Another worker may have decoded the same immutable revision while
            # this worker was outside the lock.
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached.plane
            while self._entries and self._bytes_used + plane.nbytes > self.byte_budget:
                _, evicted = self._entries.popitem(last=False)
                self._bytes_used -= evicted.plane.nbytes
            self._entries[key] = _CacheEntry(identifier=identifier, plane=plane)
            self._bytes_used += plane.nbytes
        return plane


__all__ = [
    "BitplaneCodec",
    "BitplaneError",
    "BitplaneHeader",
    "BitplaneRole",
    "DecodedBitplaneCache",
    "DEFAULT_CACHE_BYTE_BUDGET",
    "DEFAULT_INSERT_CHUNK_BYTES",
    "DEFAULT_MAX_RAW_BYTES",
    "FORMAT_VERSION",
    "HEADER_SIZE",
    "MAGIC",
    "decode_bitplane",
    "decode_bitplane_packed",
    "encode_bitplane",
    "insert_bitplane_into_uint16",
    "inspect_bitplane_header",
]

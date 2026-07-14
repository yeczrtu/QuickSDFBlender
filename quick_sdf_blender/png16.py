# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal, dependency-free RGBA16 PNG encoding and atomic file output."""

from __future__ import annotations

import binascii
import os
from pathlib import Path
import struct
import tempfile
from collections.abc import Iterator
from typing import BinaryIO, Sequence
import zlib

import numpy as np


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    if len(kind) != 4:
        raise ValueError("PNG chunk type must contain four bytes")
    checksum = binascii.crc32(kind)
    checksum = binascii.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _validated_rgba16(rgba: np.ndarray | Sequence[object]) -> np.ndarray:
    pixels = np.asarray(rgba)
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError(f"expected an HxWx4 array, got shape {pixels.shape}")
    if pixels.shape[0] <= 0 or pixels.shape[1] <= 0:
        raise ValueError("PNG dimensions must be non-zero")
    if pixels.dtype != np.uint16:
        raise TypeError("RGBA16 pixels must have dtype numpy.uint16")
    return np.ascontiguousarray(pixels)


_IDAT_TARGET_BYTES = 256 * 1024


def _filtered_scanlines(pixels: np.ndarray) -> Iterator[bytes]:
    """Yield PNG scanlines using None for row zero and Up thereafter."""

    previous: np.ndarray | None = None
    for row_index, row in enumerate(pixels):
        network = np.frombuffer(row.astype(">u2", copy=False).tobytes(), dtype=np.uint8)
        if row_index == 0:
            yield b"\0" + network.tobytes()
        else:
            # PNG filters operate on bytes modulo 256, not uint16 samples.
            filtered = np.subtract(network, previous, dtype=np.uint8)
            yield b"\2" + filtered.tobytes()
        previous = network


def _compressed_idat_payloads(
    pixels: np.ndarray, compress_level: int
) -> Iterator[bytes]:
    compressor = zlib.compressobj(compress_level)
    pending = bytearray()
    for scanline in _filtered_scanlines(pixels):
        pending.extend(compressor.compress(scanline))
        while len(pending) >= _IDAT_TARGET_BYTES:
            yield bytes(pending[:_IDAT_TARGET_BYTES])
            del pending[:_IDAT_TARGET_BYTES]
    pending.extend(compressor.flush())
    while pending:
        size = min(len(pending), _IDAT_TARGET_BYTES)
        yield bytes(pending[:size])
        del pending[:size]


def _write_png_stream(stream: BinaryIO, pixels: np.ndarray, compress_level: int) -> None:
    height, width, _channels = pixels.shape
    stream.write(PNG_SIGNATURE)
    header = struct.pack(">IIBBBBB", width, height, 16, 6, 0, 0, 0)
    stream.write(_chunk(b"IHDR", header))
    for payload in _compressed_idat_payloads(pixels, compress_level):
        stream.write(_chunk(b"IDAT", payload))
    stream.write(_chunk(b"IEND", b""))


def encode_png_rgba16(
    rgba: np.ndarray | Sequence[object], *, compress_level: int = 1
) -> bytes:
    """Encode an HxWx4 uint16 array as a non-interlaced RGBA16 PNG."""

    if compress_level < 0 or compress_level > 9:
        raise ValueError("compress_level must be between 0 and 9")
    pixels = _validated_rgba16(rgba)
    from io import BytesIO

    output = BytesIO()
    _write_png_stream(output, pixels, compress_level)
    return output.getvalue()


def write_png_rgba16_temporary(
    path: str | os.PathLike[str],
    rgba: np.ndarray | Sequence[object],
    *,
    compress_level: int = 1,
) -> Path:
    """Write and fsync a complete temporary PNG beside ``path``.

    The returned file is intentionally not published.  Export workers can
    return this small path token to Blender's main thread, which can re-check
    the project revision before calling :func:`commit_png_temporary`.
    """

    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixels = _validated_rgba16(rgba)
    if compress_level < 0 or compress_level > 9:
        raise ValueError("compress_level must be between 0 and 9")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            _write_png_stream(temporary, pixels, compress_level)
            temporary.flush()
            os.fsync(temporary.fileno())
        return Path(temporary_name)
    except BaseException:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        raise


def commit_png_temporary(
    temporary_path: str | os.PathLike[str],
    path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically publish a complete temporary PNG in the target directory."""

    temporary = Path(temporary_path)
    destination = Path(path).expanduser()
    if temporary.parent.resolve() != destination.parent.resolve():
        raise ValueError("temporary PNG must be in the destination directory")
    if overwrite:
        os.replace(temporary, destination)
    else:
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise FileExistsError(destination) from None
        os.unlink(temporary)
    return destination


def write_png_rgba16(
    path: str | os.PathLike[str],
    rgba: np.ndarray | Sequence[object],
    *,
    overwrite: bool = False,
    compress_level: int = 1,
) -> Path:
    """Atomically write RGBA16 PNG data in the target directory.

    Existing files are rejected unless ``overwrite`` is explicitly true.  The
    caller (the Blender operator) remains responsible for presenting the user
    confirmation UI before setting it.
    """

    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        temporary_path = write_png_rgba16_temporary(
            destination, rgba, compress_level=compress_level
        )
        result = commit_png_temporary(
            temporary_path, destination, overwrite=overwrite
        )
        temporary_path = None
        return result
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


__all__ = [
    "PNG_SIGNATURE",
    "commit_png_temporary",
    "encode_png_rgba16",
    "write_png_rgba16",
    "write_png_rgba16_temporary",
]

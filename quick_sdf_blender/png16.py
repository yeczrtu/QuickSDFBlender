# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal, dependency-free RGBA16 PNG encoding and atomic file output."""

from __future__ import annotations

import binascii
import os
from pathlib import Path
import struct
import tempfile
from typing import Sequence
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


def encode_png_rgba16(
    rgba: np.ndarray | Sequence[object], *, compress_level: int = 6
) -> bytes:
    """Encode an HxWx4 uint16 array as a non-interlaced RGBA16 PNG."""

    if compress_level < 0 or compress_level > 9:
        raise ValueError("compress_level must be between 0 and 9")
    pixels = _validated_rgba16(rgba)
    height, width, _ = pixels.shape
    # PNG stores 16-bit samples in network byte order.  Filter type zero keeps
    # the writer small and, for threshold maps, still compresses effectively.
    network = pixels.astype(">u2", copy=False).view(np.uint8).reshape(height, width * 8)
    raw = bytearray(height * (width * 8 + 1))
    stride = width * 8 + 1
    for y in range(height):
        start = y * stride
        raw[start] = 0
        raw[start + 1 : start + stride] = network[y].tobytes()

    header = struct.pack(">IIBBBBB", width, height, 16, 6, 0, 0, 0)
    compressed = zlib.compress(bytes(raw), level=compress_level)
    return b"".join(
        (PNG_SIGNATURE, _chunk(b"IHDR", header), _chunk(b"IDAT", compressed), _chunk(b"IEND", b""))
    )


def write_png_rgba16(
    path: str | os.PathLike[str],
    rgba: np.ndarray | Sequence[object],
    *,
    overwrite: bool = False,
    compress_level: int = 6,
) -> Path:
    """Atomically write RGBA16 PNG data in the target directory.

    Existing files are rejected unless ``overwrite`` is explicitly true.  The
    caller (the Blender operator) remains responsible for presenting the user
    confirmation UI before setting it.
    """

    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixels = _validated_rgba16(rgba)
    if compress_level < 0 or compress_level > 9:
        raise ValueError("compress_level must be between 0 and 9")
    height, width, _channels = pixels.shape
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(PNG_SIGNATURE)
            header = struct.pack(">IIBBBBB", width, height, 16, 6, 0, 0, 0)
            temporary.write(_chunk(b"IHDR", header))
            compressor = zlib.compressobj(compress_level)
            for row in pixels:
                # Converting one scanline at a time avoids the 128 MiB endian
                # and raw-buffer copies that a 4K RGBA16 image would otherwise
                # require.
                network_row = row.astype(">u2", copy=False).tobytes()
                payload = compressor.compress(b"\0" + network_row)
                if payload:
                    temporary.write(_chunk(b"IDAT", payload))
            payload = compressor.flush()
            if payload:
                temporary.write(_chunk(b"IDAT", payload))
            temporary.write(_chunk(b"IEND", b""))
            temporary.flush()
            os.fsync(temporary.fileno())
        if overwrite:
            os.replace(temporary_name, destination)
        else:
            # The temporary lives beside the destination, so a hard-link
            # reservation is an atomic create-if-absent operation on Windows.
            # It closes the exists()/replace() race without exposing a partial
            # PNG if another process creates the requested path concurrently.
            try:
                os.link(temporary_name, destination)
            except FileExistsError:
                raise FileExistsError(destination) from None
            os.unlink(temporary_name)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return destination


__all__ = ["encode_png_rgba16", "write_png_rgba16"]

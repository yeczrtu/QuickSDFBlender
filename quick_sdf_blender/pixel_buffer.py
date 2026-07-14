# SPDX-License-Identifier: GPL-3.0-or-later
"""Memory-conscious conversions for Blender image pixel buffers."""

from __future__ import annotations

import operator
from typing import Any

import numpy as np


def _positive_dimension(value: Any, name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{name} must be an integer")
    try:
        result = operator.index(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be an integer") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def blender_float_rgba_to_top_down_u8(
    flat: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    """Consume a Blender-order float buffer and return top-down RGBA8.

    ``Image.pixels.foreach_get`` returns normalized float32 RGBA samples with
    the bottom image row first.  The input must be a writable, contiguous 1D
    array owned by the caller.  It is deliberately quantized in place so a 4K
    snapshot needs only that float buffer, the returned uint8 image, and one
    temporary row instead of several full-resolution float intermediates.

    The input contents are unspecified after this call.
    """

    width = _positive_dimension(width, "width")
    height = _positive_dimension(height, "height")
    if not isinstance(flat, np.ndarray):
        raise TypeError("RGBA buffer must be a numpy array")
    if flat.dtype != np.float32:
        raise TypeError("RGBA buffer must use float32")
    if flat.ndim != 1:
        raise ValueError("RGBA buffer must be one-dimensional")
    if not flat.flags.c_contiguous:
        raise ValueError("RGBA buffer must be C-contiguous")
    if not flat.flags.writeable:
        raise ValueError("RGBA buffer must be writable")
    expected = width * height * 4
    if flat.size != expected:
        raise ValueError(
            f"RGBA buffer has {flat.size} samples; expected {expected} "
            f"for {width}x{height}"
        )

    np.clip(flat, np.float32(0.0), np.float32(1.0), out=flat)
    np.multiply(flat, np.float32(255.0), out=flat)
    np.rint(flat, out=flat)
    result = flat.astype(np.uint8).reshape(height, width, 4)

    # Reversing with ``result[::-1].copy()`` would allocate a second complete
    # RGBA8 image.  Swap rows in place to cap additional memory at one row.
    for top in range(height // 2):
        bottom = height - top - 1
        temporary = result[top].copy()
        result[top] = result[bottom]
        result[bottom] = temporary
    return result


def blender_float_rgba_to_top_down_gray8(
    flat: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    """Consume Blender float RGBA and return only its top-down R channel.

    Unlike taking ``[..., 0]`` from a converted RGBA8 image this never creates
    the four-byte-per-pixel intermediate.  The caller-owned float buffer is
    scratch and its red samples are quantized in place.
    """

    width = _positive_dimension(width, "width")
    height = _positive_dimension(height, "height")
    if not isinstance(flat, np.ndarray):
        raise TypeError("RGBA buffer must be a numpy array")
    if flat.dtype != np.float32:
        raise TypeError("RGBA buffer must use float32")
    if flat.ndim != 1:
        raise ValueError("RGBA buffer must be one-dimensional")
    if not flat.flags.c_contiguous:
        raise ValueError("RGBA buffer must be C-contiguous")
    if not flat.flags.writeable:
        raise ValueError("RGBA buffer must be writable")
    expected = width * height * 4
    if flat.size != expected:
        raise ValueError(
            f"RGBA buffer has {flat.size} samples; expected {expected} "
            f"for {width}x{height}"
        )

    red = flat[0::4]
    np.clip(red, np.float32(0.0), np.float32(1.0), out=red)
    np.multiply(red, np.float32(255.0), out=red)
    np.rint(red, out=red)
    result = red.astype(np.uint8).reshape(height, width)
    for top in range(height // 2):
        bottom = height - top - 1
        temporary = result[top].copy()
        result[top] = result[bottom]
        result[bottom] = temporary
    return result


def top_down_gray8_to_blender_float_rgba(
    gray: np.ndarray,
    *,
    out: np.ndarray | None = None,
) -> np.ndarray:
    """Expand gray8 into reusable bottom-up float RGBA for ``foreach_set``.

    Passing ``out`` lets a Studio session reuse one upload buffer across angle
    keys.  No RGBA8 or full-size floating grayscale intermediate is created.
    """

    if not isinstance(gray, np.ndarray):
        raise TypeError("gray image must be a numpy array")
    if gray.ndim != 2:
        raise ValueError("gray image must be two-dimensional")
    if gray.dtype != np.uint8:
        raise TypeError("gray image must use uint8")
    if gray.shape[0] <= 0 or gray.shape[1] <= 0:
        raise ValueError("gray image dimensions must be positive")
    height, width = (int(gray.shape[0]), int(gray.shape[1]))
    required = height * width * 4
    if out is None:
        out = np.empty(required, dtype=np.float32)
    elif not isinstance(out, np.ndarray):
        raise TypeError("out must be a numpy array or None")
    elif out.dtype != np.float32:
        raise TypeError("out must use float32")
    elif out.ndim != 1:
        raise ValueError("out must be one-dimensional")
    elif not out.flags.c_contiguous:
        raise ValueError("out must be C-contiguous")
    elif not out.flags.writeable:
        raise ValueError("out must be writable")
    elif out.size != required:
        raise ValueError(f"out has {out.size} samples; expected {required}")

    rgba = out.reshape(height, width, 4)
    bottom_up = gray[::-1]
    np.multiply(
        bottom_up,
        np.float32(1.0 / 255.0),
        out=rgba[..., 0],
        casting="unsafe",
    )
    rgba[..., 1] = rgba[..., 0]
    rgba[..., 2] = rgba[..., 0]
    rgba[..., 3] = np.float32(1.0)
    return out


__all__ = (
    "blender_float_rgba_to_top_down_gray8",
    "blender_float_rgba_to_top_down_u8",
    "top_down_gray8_to_blender_float_rgba",
)

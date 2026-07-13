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


__all__ = ("blender_float_rgba_to_top_down_u8",)

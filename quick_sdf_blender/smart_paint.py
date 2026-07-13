# SPDX-License-Identifier: GPL-3.0-or-later
"""Artist-facing monotonic paint rules used by Quick SDF Studio.

The public functions in this module are Blender-independent.  The Blender
operator captures a native Texture Paint stroke, derives its footprint, and
then calls :func:`apply_smart_stroke` so one gesture updates every required
angle without exposing propagation or guard terminology to the artist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class SmartStrokeResult:
    masks: np.ndarray
    coverage: np.ndarray
    affected_indices: tuple[int, ...]


def affected_key_indices(
    angles: Sequence[float] | np.ndarray,
    active_index: int,
    paint_light: bool,
) -> tuple[int, ...]:
    """Return the closure range shown behind the next stroke in the timeline."""

    values = np.asarray(angles, dtype=np.float64)
    if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("angles must be a non-empty finite one-dimensional array")
    if np.any(values < 0.0) or np.any(values > 90.0):
        raise ValueError("Smart Paint expects a 0..90 degree lane")
    if np.any(np.diff(values) <= 0.0):
        raise ValueError("angles must be strictly increasing")
    if isinstance(active_index, bool) or not 0 <= int(active_index) < values.size:
        raise IndexError("active_index is outside the angle lane")
    active = int(active_index)
    selection = range(active, values.size) if paint_light else range(0, active + 1)
    return tuple(int(index) for index in selection)


def apply_smart_stroke(
    mask_stack: np.ndarray,
    coverage_stack: np.ndarray,
    angles: Sequence[float] | np.ndarray,
    active_index: int,
    footprint: np.ndarray,
    *,
    paint_light: bool,
) -> SmartStrokeResult:
    """Apply one binary stroke while preserving front-to-side monotonicity.

    A Light stroke closes toward 90 degrees; a Shadow stroke closes toward
    zero.  Therefore a valid input lane cannot become invalid and no pixels
    need to be clipped after the artist releases the pen.
    """

    masks = np.asarray(mask_stack)
    coverage = np.asarray(coverage_stack)
    area = np.asarray(footprint, dtype=np.bool_)
    if masks.ndim != 3 or masks.dtype != np.bool_:
        raise TypeError("mask_stack must be a boolean (angle, height, width) array")
    if coverage.shape != masks.shape:
        raise ValueError("coverage_stack must match mask_stack")
    if coverage.dtype != np.bool_:
        raise TypeError("coverage_stack must be boolean")
    if area.shape != masks.shape[1:]:
        raise ValueError("footprint must match one mask image")
    indices = affected_key_indices(angles, active_index, paint_light)
    result = masks.copy()
    result_coverage = coverage.copy()
    for index in indices:
        result[index, area] = bool(paint_light)
        result_coverage[index, area] = True
    return SmartStrokeResult(result, result_coverage, indices)


__all__ = ["SmartStrokeResult", "affected_key_indices", "apply_smart_stroke"]

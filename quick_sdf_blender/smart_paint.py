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


@dataclass(frozen=True, slots=True)
class SmartTransitionResult:
    masks: np.ndarray
    coverage: np.ndarray
    footprints: np.ndarray
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


def apply_smart_transitions(
    mask_stack: np.ndarray,
    coverage_stack: np.ndarray,
    angles: Sequence[float] | np.ndarray,
    active_index: int,
    touched: np.ndarray,
    became_light: np.ndarray,
    became_shadow: np.ndarray,
) -> SmartTransitionResult:
    """Propagate only pixels that actually crossed the binary paint threshold.

    The active key keeps every native brush change as an override, including
    soft/low-strength values that stay on the same side of 0.5. Other keys are
    touched only where the native result crossed Shadow→Light or Light→Shadow.
    """

    masks = np.asarray(mask_stack)
    coverage = np.asarray(coverage_stack)
    values = np.asarray(angles, dtype=np.float64)
    if masks.ndim != 3 or masks.dtype != np.bool_:
        raise TypeError("mask_stack must be a boolean (angle, height, width) array")
    if coverage.shape != masks.shape:
        raise ValueError("coverage_stack must match mask_stack")
    if coverage.dtype != np.bool_:
        raise TypeError("coverage_stack must be boolean")
    if values.ndim != 1 or values.shape[0] != masks.shape[0]:
        raise ValueError("angles must contain one value per mask")
    if not np.all(np.isfinite(values)) or np.any(values < 0.0) or np.any(values > 90.0):
        raise ValueError("angles must be finite values in the range 0..90")
    if np.any(np.diff(values) <= 0.0):
        raise ValueError("angles must be strictly increasing")
    if isinstance(active_index, bool) or not 0 <= int(active_index) < masks.shape[0]:
        raise IndexError("active_index is outside the angle lane")

    shape = masks.shape[1:]
    areas = []
    for name, value in (
        ("touched", touched),
        ("became_light", became_light),
        ("became_shadow", became_shadow),
    ):
        area = np.asarray(value)
        if area.shape != shape:
            raise ValueError(f"{name} must match one mask image")
        if area.dtype != np.bool_:
            raise TypeError(f"{name} must be boolean")
        areas.append(area)
    touched_area, light_area, shadow_area = areas
    if np.any(light_area & shadow_area):
        raise ValueError("a pixel cannot become both Light and Shadow")
    if np.any((light_area | shadow_area) & ~touched_area):
        raise ValueError("threshold transitions must be part of the touched area")

    active = int(active_index)
    result = masks.copy()
    result_coverage = coverage.copy()
    footprints = np.zeros_like(masks, dtype=np.bool_)
    footprints[active] |= touched_area
    result_coverage[active, touched_area] = True

    if np.any(light_area):
        for index in range(active, masks.shape[0]):
            result[index, light_area] = True
            result_coverage[index, light_area] = True
            footprints[index] |= light_area
    if np.any(shadow_area):
        for index in range(0, active + 1):
            result[index, shadow_area] = False
            result_coverage[index, shadow_area] = True
            footprints[index] |= shadow_area

    affected = tuple(
        int(index) for index in range(masks.shape[0]) if np.any(footprints[index])
    )
    return SmartTransitionResult(result, result_coverage, footprints, affected)


__all__ = [
    "SmartStrokeResult", "SmartTransitionResult", "affected_key_indices",
    "apply_smart_stroke", "apply_smart_transitions",
]

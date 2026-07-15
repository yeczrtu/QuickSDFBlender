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


@dataclass(frozen=True, slots=True)
class SmartTransitionPatch:
    """One angle's zero-copy propagation instructions.

    The boolean arrays are borrowed from the stroke-level transition masks.
    They must not be mutated.  ``include_touched`` is true only for the active
    key, where soft brush changes that did not cross 0.5 still gain Coverage.
    """

    index: int
    became_light: np.ndarray | None
    became_shadow: np.ndarray | None
    touched: np.ndarray | None


@dataclass(frozen=True, slots=True)
class SmartKeyPatchResult:
    mask: np.ndarray
    coverage: np.ndarray
    footprint: np.ndarray


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


def gray8_transition_masks(
    before: np.ndarray,
    after: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return touched, Shadow-to-Light and Light-to-Shadow pixel masks."""

    first = np.asarray(before)
    second = np.asarray(after)
    if first.ndim != 2 or second.ndim != 2 or first.shape != second.shape:
        raise ValueError("before and after must be matching two-dimensional planes")
    if first.dtype != np.uint8 or second.dtype != np.uint8:
        raise TypeError("before and after must use uint8")
    touched = first != second
    first_light = first >= 128
    second_light = second >= 128
    became_light = touched & ~first_light & second_light
    became_shadow = touched & first_light & ~second_light
    return touched, became_light, became_shadow


def _validated_transition_areas(
    angle_count: int,
    active_index: int,
    touched: np.ndarray,
    became_light: np.ndarray,
    became_shadow: np.ndarray,
) -> tuple[int, np.ndarray, np.ndarray, np.ndarray]:
    if isinstance(angle_count, bool) or not isinstance(angle_count, (int, np.integer)):
        raise TypeError("angle_count must be an integer")
    if angle_count <= 0:
        raise ValueError("angle_count must be positive")
    if isinstance(active_index, bool) or not 0 <= int(active_index) < angle_count:
        raise IndexError("active_index is outside the angle lane")
    areas = []
    shape: tuple[int, int] | None = None
    for name, value in (
        ("touched", touched),
        ("became_light", became_light),
        ("became_shadow", became_shadow),
    ):
        area = np.asarray(value)
        if area.ndim != 2:
            raise ValueError(f"{name} must be a two-dimensional image")
        if area.dtype != np.bool_:
            raise TypeError(f"{name} must be boolean")
        if shape is None:
            shape = area.shape
        elif area.shape != shape:
            raise ValueError("transition masks must have matching shapes")
        areas.append(area)
    touched_area, light_area, shadow_area = areas
    if np.any(light_area & shadow_area):
        raise ValueError("a pixel cannot become both Light and Shadow")
    if np.any((light_area | shadow_area) & ~touched_area):
        raise ValueError("threshold transitions must be part of the touched area")
    return int(active_index), touched_area, light_area, shadow_area


def iter_smart_transition_patches(
    angle_count: int,
    active_index: int,
    touched: np.ndarray,
    became_light: np.ndarray,
    became_shadow: np.ndarray,
):
    """Yield propagation work one key at a time without allocating a stack."""

    active, touched_area, light_area, shadow_area = _validated_transition_areas(
        angle_count,
        active_index,
        touched,
        became_light,
        became_shadow,
    )
    has_touched = bool(np.any(touched_area))
    has_light = bool(np.any(light_area))
    has_shadow = bool(np.any(shadow_area))
    for index in range(angle_count):
        light = light_area if has_light and index >= active else None
        shadow = shadow_area if has_shadow and index <= active else None
        native_touched = touched_area if has_touched and index == active else None
        if light is not None or shadow is not None or native_touched is not None:
            yield SmartTransitionPatch(index, light, shadow, native_touched)


def apply_smart_transition_patch(
    mask: np.ndarray,
    coverage: np.ndarray,
    patch: SmartTransitionPatch,
) -> SmartKeyPatchResult:
    """Apply one streamed patch, allocating only this key's result planes."""

    source_mask = np.asarray(mask)
    source_coverage = np.asarray(coverage)
    if source_mask.ndim != 2 or source_mask.dtype != np.bool_:
        raise TypeError("mask must be a two-dimensional boolean plane")
    if source_coverage.shape != source_mask.shape or source_coverage.dtype != np.bool_:
        raise TypeError("coverage must be a matching boolean plane")
    if not isinstance(patch, SmartTransitionPatch):
        raise TypeError("patch must be a SmartTransitionPatch")
    result_mask = source_mask.copy()
    result_coverage = source_coverage.copy()
    footprint = np.zeros(source_mask.shape, dtype=np.bool_)
    for area, value in ((patch.became_light, True), (patch.became_shadow, False)):
        if area is None:
            continue
        if area.shape != source_mask.shape or area.dtype != np.bool_:
            raise ValueError("patch transition mask does not match the destination")
        result_mask[area] = value
        result_coverage[area] = True
        footprint |= area
    if patch.touched is not None:
        if patch.touched.shape != source_mask.shape or patch.touched.dtype != np.bool_:
            raise ValueError("patch touched mask does not match the destination")
        result_coverage[patch.touched] = True
        footprint |= patch.touched
    return SmartKeyPatchResult(result_mask, result_coverage, footprint)


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

    active = int(active_index)
    result = masks.copy()
    result_coverage = coverage.copy()
    footprints = np.zeros_like(masks, dtype=np.bool_)
    affected_list: list[int] = []
    for patch in iter_smart_transition_patches(
        masks.shape[0],
        active,
        touched,
        became_light,
        became_shadow,
    ):
        key_result = apply_smart_transition_patch(
            result[patch.index],
            result_coverage[patch.index],
            patch,
        )
        result[patch.index] = key_result.mask
        result_coverage[patch.index] = key_result.coverage
        footprints[patch.index] = key_result.footprint
        affected_list.append(patch.index)
    affected = tuple(affected_list)
    return SmartTransitionResult(result, result_coverage, footprints, affected)


__all__ = [
    "SmartKeyPatchResult",
    "SmartStrokeResult",
    "SmartTransitionPatch",
    "SmartTransitionResult",
    "affected_key_indices",
    "apply_smart_stroke",
    "apply_smart_transition_patch",
    "apply_smart_transitions",
    "gray8_transition_masks",
    "iter_smart_transition_patches",
]

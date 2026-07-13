"""Blender-independent image builders for Quick SDF review modes.

All functions return display-ready, straight-alpha ``float32`` RGBA arrays in
the normalized 0..1 range.  They deliberately avoid ``bpy`` so review images
can be regenerated on a worker thread and covered by ordinary unit tests.

Mask convention follows :mod:`quick_sdf_blender.core`: white/``True`` means
Light and black/``False`` means Shadow.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


_ANGLE_EPSILON = 1.0e-7
_CYAN = np.asarray((0.0, 1.0, 1.0), dtype=np.float32)
_MAGENTA = np.asarray((1.0, 0.0, 1.0), dtype=np.float32)


def _as_binary_stack(mask_stack: np.ndarray | Sequence[object]) -> np.ndarray:
    """Normalize bool, 0..1, 8-bit, or 16-bit masks to a contiguous stack."""

    values = np.asarray(mask_stack)
    if values.ndim != 3:
        raise ValueError(f"expected an NxHxW mask stack, got shape {values.shape}")
    if any(size <= 0 for size in values.shape):
        raise ValueError("mask dimensions must be non-zero")
    if values.dtype == np.bool_:
        return np.ascontiguousarray(values)
    if not np.issubdtype(values.dtype, np.number):
        raise TypeError("masks must contain boolean or numeric values")
    if not np.all(np.isfinite(values)):
        raise ValueError("masks must not contain NaN or infinity")

    if np.issubdtype(values.dtype, np.floating):
        threshold = 0.5
    else:
        minimum = int(np.min(values))
        maximum = int(np.max(values))
        if minimum >= 0 and maximum <= 1:
            threshold = 0.5
        elif minimum >= 0 and maximum <= 255:
            threshold = 127.0
        elif minimum >= 0 and maximum <= 65535:
            threshold = 32767.0
        else:
            raise ValueError("integer masks must use 0/1, 8-bit, or 16-bit values")
    return np.ascontiguousarray(values >= threshold)


def _validated_stack_and_angles(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    masks = _as_binary_stack(mask_stack)
    angle_values = np.asarray(angles, dtype=np.float64)
    if angle_values.ndim != 1 or angle_values.size != masks.shape[0]:
        raise ValueError(
            f"expected {masks.shape[0]} angles, got shape {angle_values.shape}"
        )
    if not np.all(np.isfinite(angle_values)):
        raise ValueError("angles must be finite")
    if np.any(angle_values < -90.0 - _ANGLE_EPSILON) or np.any(
        angle_values > 90.0 + _ANGLE_EPSILON
    ):
        raise ValueError("angles must be in the inclusive range -90..90")
    ordered = np.sort(angle_values)
    if ordered.size > 1 and np.any(np.diff(ordered) <= _ANGLE_EPSILON):
        raise ValueError("angles must be unique")
    return masks, angle_values


def _validated_review_angle(angle: float) -> float:
    value = float(angle)
    if not np.isfinite(value):
        raise ValueError("review angle must be finite")
    if value < -90.0 - _ANGLE_EPSILON or value > 90.0 + _ANGLE_EPSILON:
        raise ValueError("review angle must be in the inclusive range -90..90")
    return float(np.clip(value, -90.0, 90.0))


def _mask_rgba(mask: np.ndarray) -> np.ndarray:
    luminance = mask.astype(np.float32, copy=False)
    output = np.empty((*mask.shape, 4), dtype=np.float32)
    output[..., :3] = luminance[..., None]
    output[..., 3] = 1.0
    return output


def _nearest_index(angles: np.ndarray, angle: float) -> int:
    """Return a stable nearest index, preferring the angle nearer the front."""

    distance = np.abs(angles - angle)
    nearest = np.flatnonzero(np.isclose(distance, np.min(distance), atol=1e-12))
    if nearest.size == 1:
        return int(nearest[0])
    return int(nearest[np.argmin(np.abs(angles[nearest]))])


def review_current(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    angle: float,
) -> np.ndarray:
    """Display the authored mask nearest to a continuous review angle."""

    masks, angle_values = _validated_stack_and_angles(mask_stack, angles)
    selected = _nearest_index(angle_values, _validated_review_angle(angle))
    return _mask_rgba(masks[selected])


def review_onion_difference(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    angle: float,
) -> np.ndarray:
    """Display current mask with adjacent same-side changes as onion colors.

    The nearest authored mask is the base.  Pixels changed since the neighbour
    toward the front are cyan; pixels that differ in the neighbour toward the
    side are magenta.  This ordering remains intuitive on the negative side,
    where numeric angle order is reversed.  A pixel present in both difference
    sets is shown white.
    """

    masks, angle_values = _validated_stack_and_angles(mask_stack, angles)
    requested = _validated_review_angle(angle)
    selected = _nearest_index(angle_values, requested)
    selected_angle = float(angle_values[selected])

    # At exactly zero, use the direction in which the user was scrubbing.  The
    # public value has no negative-zero UX meaning, so zero defaults positive.
    sign = -1.0 if requested < 0.0 else 1.0
    if abs(selected_angle) > _ANGLE_EPSILON:
        sign = -1.0 if selected_angle < 0.0 else 1.0
    same_side = (angle_values * sign > _ANGLE_EPSILON) | np.isclose(
        angle_values, 0.0, atol=_ANGLE_EPSILON, rtol=0.0
    )
    side_indices = np.flatnonzero(same_side)
    side_indices = side_indices[np.argsort(np.abs(angle_values[side_indices]))]
    position_matches = np.flatnonzero(side_indices == selected)
    if position_matches.size:
        position = int(position_matches[0])
    else:
        # A project without a zero mask can select the opposite-side angle when
        # scrubbing close to front.  Fall back to the closest mask on this side.
        selected = int(side_indices[np.argmin(np.abs(angle_values[side_indices] - requested))])
        position = int(np.flatnonzero(side_indices == selected)[0])

    current = masks[selected]
    output = _mask_rgba(current)
    inward = masks[side_indices[position - 1]] if position > 0 else current
    outward = masks[side_indices[position + 1]] if position + 1 < side_indices.size else current
    inward_difference = current != inward
    outward_difference = current != outward
    output[inward_difference, :3] = _CYAN
    output[outward_difference, :3] = _MAGENTA
    output[inward_difference & outward_difference, :3] = 1.0
    return output


def review_threshold_rgba16(
    threshold_rgba16: np.ndarray,
    signed_angle: float,
) -> np.ndarray:
    """Evaluate a generated threshold texture at a continuous signed angle.

    Positive angles (including zero) use R and negative angles use G.  Codes 0
    and 65535 are reserved for always-Light and always-Shadow respectively;
    transition codes 1..65534 are compared in the same quantized domain used by
    threshold generation.
    """

    thresholds = np.asarray(threshold_rgba16)
    if thresholds.dtype != np.uint16:
        raise TypeError("threshold texture must have dtype uint16")
    if thresholds.ndim != 3 or thresholds.shape[-1] != 4:
        raise ValueError(
            f"expected an HxWx4 threshold texture, got shape {thresholds.shape}"
        )
    if thresholds.shape[0] <= 0 or thresholds.shape[1] <= 0:
        raise ValueError("threshold texture dimensions must be non-zero")

    angle = _validated_review_angle(signed_angle)
    channel = 0 if angle >= 0.0 else 1
    values = thresholds[..., channel]
    normalized = abs(angle) / 90.0
    current_code = 1 + int(np.floor(normalized * 65533.0 + 0.5))
    light = (values == 0) | ((values != 65535) & (values <= current_code))
    return _mask_rgba(light)


def review_violation_heatmap(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> np.ndarray:
    """Display forbidden Light-to-Shadow transitions by signed-angle side.

    Positive-side violations are red, negative-side violations are blue, and a
    pixel violating both contracts is magenta.  Valid pixels remain opaque
    black, making the result usable directly as an Image Editor display.
    """

    masks, angle_values = _validated_stack_and_angles(mask_stack, angles)
    zero_indices = np.flatnonzero(
        np.isclose(angle_values, 0.0, atol=_ANGLE_EPSILON, rtol=0.0)
    )
    if zero_indices.size != 1:
        raise ValueError("violation review requires exactly one 0 degree mask")
    zero = int(zero_indices[0])

    side_maps: list[np.ndarray] = []
    for sign in (1.0, -1.0):
        indices = np.flatnonzero(angle_values * sign > _ANGLE_EPSILON)
        indices = indices[np.argsort(np.abs(angle_values[indices]))]
        indices = np.concatenate((np.asarray([zero], dtype=np.intp), indices))
        violation = np.zeros(masks.shape[1:], dtype=np.bool_)
        for first, second in zip(indices[:-1], indices[1:]):
            violation |= masks[first] & ~masks[second]
        side_maps.append(violation)

    positive, negative = side_maps
    output = np.zeros((*positive.shape, 4), dtype=np.float32)
    output[..., 0] = positive
    output[..., 2] = negative
    output[..., 3] = 1.0
    return output


__all__ = [
    "review_current",
    "review_onion_difference",
    "review_threshold_rgba16",
    "review_violation_heatmap",
]

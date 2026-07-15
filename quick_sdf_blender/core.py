# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent reference algorithms for Quick SDF.

The native module may accelerate these operations, but this module deliberately
contains a complete NumPy implementation so generation and validation can be
tested without Blender or a GPU.

Mask convention
---------------
``True``/white is Light and ``False``/black is Shadow.  Signed distance is
positive in Shadow and negative in Light.  On each side of the face, masks are
ordered from 0 degrees towards 90 degrees and Light is required to expand.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np


# lilToon consumes the complete normalized channel range.  A larger value
# means that a pixel becomes Light earlier in the authored 0..90 sweep.
# These names describe the two endpoint outcomes; neither value is a reserved
# sentinel and both may also be produced by a transition exactly at an
# endpoint.
ALWAYS_LIGHT = np.uint16(65535)
ALWAYS_SHADOW = np.uint16(0)
_ANGLE_EPSILON = 1.0e-7


class RangeScope(str, Enum):
    """Angle ranges available to a propagated paint stroke."""

    CURRENT = "CURRENT"
    TOWARD_FRONT = "TOWARD_FRONT"
    TOWARD_SIDE = "TOWARD_SIDE"
    WHOLE_SIDE = "WHOLE_SIDE"
    BOTH_SIDES = "BOTH_SIDES"


@dataclass(frozen=True)
class MonotonicValidation:
    """Result of validating Light expansion on both signed-angle sides."""

    is_valid: bool
    violation_count: int
    violation_pixel_count: int
    violation_map: np.ndarray
    positive_violation_map: np.ndarray
    negative_violation_map: np.ndarray
    offending_transitions: tuple[tuple[float, float, int], ...]


@dataclass(frozen=True)
class GuardClipResult:
    """A monotonic proposal plus a mask of entries reverted by the guard."""

    masks: np.ndarray
    clipped: np.ndarray
    validation: MonotonicValidation

    @property
    def clipped_entry_count(self) -> int:
        return int(np.count_nonzero(self.clipped))

    @property
    def clipped_pixel_count(self) -> int:
        return int(np.count_nonzero(np.any(self.clipped, axis=0)))


@dataclass(frozen=True)
class MonotonicRepairResult:
    """A non-destructive projection of one side lane onto valid transitions."""

    masks: np.ndarray
    changed_mask: np.ndarray
    transition_indices: np.ndarray
    changed_sample_count: int
    changed_pixel_count: int
    protected_changed_sample_count: int
    protected_changed_pixel_count: int


@dataclass(frozen=True)
class PackedLane:
    """A 0..90 lane stored as one bit per key in each image pixel.

    Bit ``i`` corresponds to ``angles[i]``.  Studio lanes are limited to
    sixteen keys, so the representation stays a compact ``uint16`` plane and
    can be passed to the ABI-7 native core without expanding an ``N x H x W``
    boolean stack.
    """

    angles: np.ndarray
    display_bits: np.ndarray
    base_bits: np.ndarray
    coverage_bits: np.ndarray

    @property
    def count(self) -> int:
        return int(self.angles.size)

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(int(value) for value in self.display_bits.shape)


@dataclass(frozen=True)
class PackedLaneRepairResult:
    """Compact monotonic-repair result produced from a :class:`PackedLane`."""

    transition_indices: np.ndarray
    changed_count: np.ndarray
    changed_sample_count: int
    changed_pixel_count: int
    protected_changed_sample_count: int
    protected_changed_pixel_count: int


def _as_binary(values: np.ndarray | Sequence[object], *, ndim: int) -> np.ndarray:
    """Convert common normalized/8-bit/16-bit mask arrays to bool."""

    array = np.asarray(values)
    if array.ndim != ndim:
        raise ValueError(f"expected a {ndim}D mask array, got shape {array.shape}")
    if any(size <= 0 for size in array.shape):
        raise ValueError("mask dimensions must be non-zero")
    if array.dtype == np.bool_:
        return np.ascontiguousarray(array)
    if not np.issubdtype(array.dtype, np.number):
        raise TypeError("masks must contain boolean or numeric values")
    if not np.all(np.isfinite(array)):
        raise ValueError("masks must not contain NaN or infinity")

    if np.issubdtype(array.dtype, np.floating):
        threshold = 0.5
    else:
        minimum = int(np.min(array))
        maximum = int(np.max(array))
        if minimum >= 0 and maximum <= 1:
            threshold = 0.5
        elif minimum >= 0 and maximum <= 255:
            threshold = 127.0
        elif minimum >= 0 and maximum <= 65535:
            threshold = 32767.0
        else:
            raise ValueError("integer masks must use 0/1, 8-bit, or 16-bit values")
    return np.ascontiguousarray(array >= threshold)


def _validated_angles(angles: Sequence[float] | np.ndarray, count: int) -> np.ndarray:
    array = np.asarray(angles, dtype=np.float64)
    if array.ndim != 1 or array.size != count:
        raise ValueError(f"expected {count} angles, got shape {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError("angles must be finite")
    if np.any(array < -90.0 - 1e-7) or np.any(array > 90.0 + 1e-7):
        raise ValueError("angles must be in the inclusive range -90..90")
    order = np.argsort(array)
    if array.size > 1 and np.any(np.diff(array[order]) <= 1e-7):
        raise ValueError("angles must be unique")
    if np.count_nonzero(np.isclose(array, 0.0, atol=1e-7, rtol=0.0)) != 1:
        raise ValueError("angles must contain exactly one 0 degree mask")
    return array


def _side_indices(angles: np.ndarray, sign: int) -> np.ndarray:
    zero = int(np.flatnonzero(np.isclose(angles, 0.0, atol=1e-7, rtol=0.0))[0])
    if sign > 0:
        side = np.flatnonzero(angles > 1e-7)
    else:
        side = np.flatnonzero(angles < -1e-7)
    side = side[np.argsort(np.abs(angles[side]))]
    return np.concatenate((np.asarray([zero], dtype=np.intp), side.astype(np.intp)))


def _edt_rows_squared(cost: np.ndarray, *, block_rows: int = 1024) -> np.ndarray:
    """Felzenszwalb-Huttenlocher squared transform along the last axis.

    This vectorized adaptation is based on Pedro Felzenszwalb's 2006
    GPL-2.0-or-later implementation accompanying *Distance Transforms of
    Sampled Functions*: https://cs.brown.edu/people/pfelzens/dt/ . Quick SDF
    modifications are GPL-3.0-or-later; see ``THIRD_PARTY_NOTICES.md``.

    Rows are processed together with NumPy while the lower-envelope dimension
    remains sequential.  Blocking prevents the envelope workspaces from
    becoming excessive for 4K textures.
    """

    row_count, length = cost.shape
    output = np.empty_like(cost, dtype=np.float64)
    for block_start in range(0, row_count, block_rows):
        f = cost[block_start : block_start + block_rows]
        batch = f.shape[0]
        rows = np.arange(batch, dtype=np.intp)
        vertices = np.empty((batch, length), dtype=np.int32)
        bounds = np.empty((batch, length + 1), dtype=np.float64)
        envelope = np.zeros(batch, dtype=np.int32)
        vertices[:, 0] = 0
        bounds[:, 0] = -np.inf
        bounds[:, 1] = np.inf

        for q in range(1, length):
            vertex = vertices[rows, envelope]
            separation = (
                (f[:, q] + float(q * q))
                - (f[rows, vertex] + vertex.astype(np.float64) ** 2)
            ) / (2.0 * (q - vertex))
            pop = separation <= bounds[rows, envelope]
            while np.any(pop):
                envelope[pop] -= 1
                selected = rows[pop]
                vertex = vertices[selected, envelope[pop]]
                separation[pop] = (
                    (f[pop, q] + float(q * q))
                    - (f[selected, vertex] + vertex.astype(np.float64) ** 2)
                ) / (2.0 * (q - vertex))
                pop = separation <= bounds[rows, envelope]
            envelope += 1
            vertices[rows, envelope] = q
            bounds[rows, envelope] = separation
            bounds[rows, envelope + 1] = np.inf

        envelope.fill(0)
        block_output = output[block_start : block_start + batch]
        for q in range(length):
            advance = bounds[rows, envelope + 1] < float(q)
            while np.any(advance):
                envelope[advance] += 1
                advance = bounds[rows, envelope + 1] < float(q)
            vertex = vertices[rows, envelope]
            block_output[:, q] = (q - vertex) ** 2 + f[rows, vertex]
    return output


def exact_edt(features: np.ndarray | Sequence[object]) -> np.ndarray:
    """Return exact Euclidean distance to the nearest ``True`` pixel.

    The result is float64.  If no feature exists, every distance is infinity.
    Pixel centres use unit spacing.
    """

    feature_mask = _as_binary(features, ndim=2)
    if not np.any(feature_mask):
        return np.full(feature_mask.shape, np.inf, dtype=np.float64)

    # The parabolic lower envelope is the expensive pass.  Put the shorter
    # dimension on its axis to reduce Python loop overhead for non-square data.
    transposed = feature_mask.shape[1] > feature_mask.shape[0]
    work = feature_mask.T if transposed else feature_mask
    height, width = work.shape
    unreachable = float((height - 1) ** 2 + (width - 1) ** 2 + 1)

    vertical = np.full((height, width), unreachable, dtype=np.float64)
    last = np.full(width, -height, dtype=np.int64)
    for y in range(height):
        last = np.where(work[y], y, last)
        valid = last >= 0
        vertical[y, valid] = (y - last[valid]) ** 2
    last.fill(height * 2)
    for y in range(height - 1, -1, -1):
        last = np.where(work[y], y, last)
        valid = last < height
        vertical[y, valid] = np.minimum(vertical[y, valid], (last[valid] - y) ** 2)

    squared = _edt_rows_squared(vertical)
    distance = np.sqrt(squared, out=squared)
    return np.ascontiguousarray(distance.T if transposed else distance)


def exact_signed_edt(light_mask: np.ndarray | Sequence[object]) -> np.ndarray:
    """Return exact signed distance, positive in Shadow and negative in Light."""

    light = _as_binary(light_mask, ndim=2)
    if np.all(light):
        return np.full(light.shape, -np.inf, dtype=np.float64)
    if not np.any(light):
        return np.full(light.shape, np.inf, dtype=np.float64)
    to_light = exact_edt(light)
    to_shadow = exact_edt(~light)
    return to_light - to_shadow


def _cancel_requested(cancel_flag: object | None) -> bool:
    if cancel_flag is None:
        return False
    return bool(getattr(cancel_flag, "value", cancel_flag))


def interpolate_binary_masks(
    first: np.ndarray | Sequence[object],
    second: np.ndarray | Sequence[object],
    factor: float,
    cancel_flag: object | None = None,
) -> np.ndarray:
    """Blend two binary masks with the exact signed-distance preview rule.

    ``factor`` is zero at ``first`` and one at ``second``. Constant masks have
    infinite signed distance, which is replaced by the image diagonal plus one
    before blending. This is the same finite convention used by Studio seek
    preview and makes all-Light/all-Shadow interpolation deterministic.
    """

    first_mask = _as_binary(first, ndim=2)
    second_mask = _as_binary(second, ndim=2)
    if first_mask.shape != second_mask.shape:
        raise ValueError("first and second masks must have the same shape")
    if not first_mask.size:
        raise ValueError("mask dimensions must be positive")
    value = float(factor)
    if not np.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("factor must be a finite value in [0, 1]")
    if _cancel_requested(cancel_flag):
        raise RuntimeError("Quick SDF interpolation was cancelled")
    if value == 0.0:
        return np.ascontiguousarray(first_mask)
    if value == 1.0:
        return np.ascontiguousarray(second_mask)

    diagonal = float(
        (first_mask.shape[0] ** 2 + first_mask.shape[1] ** 2) ** 0.5 + 1.0
    )
    first_sdf = np.nan_to_num(
        exact_signed_edt(first_mask),
        nan=0.0,
        posinf=diagonal,
        neginf=-diagonal,
    )
    if _cancel_requested(cancel_flag):
        raise RuntimeError("Quick SDF interpolation was cancelled")
    second_sdf = np.nan_to_num(
        exact_signed_edt(second_mask),
        nan=0.0,
        posinf=diagonal,
        neginf=-diagonal,
    )
    if _cancel_requested(cancel_flag):
        raise RuntimeError("Quick SDF interpolation was cancelled")
    return np.ascontiguousarray(
        ((1.0 - value) * first_sdf + value * second_sdf) <= 0.0
    )


def validate_monotonic(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> MonotonicValidation:
    """Validate that Light only expands from 0 degrees toward each side."""

    masks = _as_binary(mask_stack, ndim=3)
    angle_values = _validated_angles(angles, masks.shape[0])
    height, width = masks.shape[1:]
    side_maps: list[np.ndarray] = []
    transitions: list[tuple[float, float, int]] = []
    violation_count = 0

    for sign in (1, -1):
        indices = _side_indices(angle_values, sign)
        side_map = np.zeros((height, width), dtype=np.bool_)
        for first, second in zip(indices[:-1], indices[1:]):
            # True -> False is the only forbidden transition for white-expands.
            invalid = masks[first] & ~masks[second]
            count = int(np.count_nonzero(invalid))
            if count:
                side_map |= invalid
                violation_count += count
                transitions.append(
                    (float(angle_values[first]), float(angle_values[second]), count)
                )
        side_maps.append(side_map)

    positive_map, negative_map = side_maps
    combined = positive_map | negative_map
    pixel_count = int(np.count_nonzero(combined))
    return MonotonicValidation(
        is_valid=violation_count == 0,
        violation_count=violation_count,
        violation_pixel_count=pixel_count,
        violation_map=combined,
        positive_violation_map=positive_map,
        negative_violation_map=negative_map,
        offending_transitions=tuple(transitions),
    )


def guard_clip_proposal(
    before: np.ndarray | Sequence[object],
    proposed: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> GuardClipResult:
    """Revert only proposed entries needed to retain monotonic masks.

    ``before`` must already be valid.  When both ends of a forbidden pair were
    changed, the farther-angle end is restored; subsequent passes handle any
    neighbouring pair exposed by that choice.
    """

    original = _as_binary(before, ndim=3)
    candidate = _as_binary(proposed, ndim=3).copy()
    if original.shape != candidate.shape:
        raise ValueError("before and proposed stacks must have the same shape")
    angle_values = _validated_angles(angles, original.shape[0])
    original_report = validate_monotonic(original, angle_values)
    if not original_report.is_valid:
        raise ValueError("before stack must be monotonic before guard clipping")

    clipped = np.zeros_like(candidate, dtype=np.bool_)
    for _ in range(max(1, candidate.shape[0] * 2)):
        changed_any = False
        for sign in (1, -1):
            indices = _side_indices(angle_values, sign)
            for first, second in zip(indices[:-1], indices[1:]):
                invalid = candidate[first] & ~candidate[second]
                if not np.any(invalid):
                    continue
                first_changed = invalid & (candidate[first] != original[first])
                second_changed = invalid & (candidate[second] != original[second])

                # Restore the sole changed endpoint.  For a two-endpoint swap,
                # prefer the farther endpoint so one proposed edit survives.
                restore_first = first_changed & ~second_changed
                restore_second = second_changed
                if np.any(restore_first):
                    candidate[first][restore_first] = original[first][restore_first]
                    clipped[first] |= restore_first
                    changed_any = True
                if np.any(restore_second):
                    candidate[second][restore_second] = original[second][restore_second]
                    clipped[second] |= restore_second
                    changed_any = True
                if np.any(invalid & ~first_changed & ~second_changed):
                    raise RuntimeError("guard encountered a violation not caused by the proposal")
        report = validate_monotonic(candidate, angle_values)
        if report.is_valid:
            return GuardClipResult(candidate, clipped, report)
        if not changed_any:
            break
    raise RuntimeError("monotonic guard could not resolve the proposal")


def range_target_indices(
    angles: Sequence[float] | np.ndarray,
    active_index: int,
    scope: RangeScope | str,
) -> np.ndarray:
    """Return source-order angle indices affected by a range paint operation."""

    angle_values = np.asarray(angles, dtype=np.float64)
    if angle_values.ndim != 1 or angle_values.size == 0:
        raise ValueError("angles must be a non-empty 1D sequence")
    if not np.all(np.isfinite(angle_values)):
        raise ValueError("angles must be finite")
    if active_index < 0 or active_index >= angle_values.size:
        raise IndexError("active_index is outside the angle sequence")
    try:
        selected_scope = scope if isinstance(scope, RangeScope) else RangeScope(str(scope).upper())
    except ValueError as error:
        raise ValueError(f"unknown range scope: {scope!r}") from error

    active = float(angle_values[active_index])
    absolute = np.abs(angle_values)
    epsilon = 1e-7
    if selected_scope is RangeScope.CURRENT:
        selection = np.zeros(angle_values.size, dtype=np.bool_)
        selection[active_index] = True
    elif selected_scope is RangeScope.BOTH_SIDES:
        selection = np.ones(angle_values.size, dtype=np.bool_)
    elif abs(active) <= epsilon:
        selection = (
            np.isclose(angle_values, 0.0, atol=epsilon, rtol=0.0)
            if selected_scope is RangeScope.TOWARD_FRONT
            else np.ones(angle_values.size, dtype=np.bool_)
        )
    else:
        same_side = (angle_values * active > 0.0) | np.isclose(
            angle_values, 0.0, atol=epsilon, rtol=0.0
        )
        if selected_scope is RangeScope.TOWARD_FRONT:
            selection = same_side & (absolute <= abs(active) + epsilon)
        elif selected_scope is RangeScope.TOWARD_SIDE:
            selection = same_side & (absolute + epsilon >= abs(active))
            selection &= ~np.isclose(angle_values, 0.0, atol=epsilon, rtol=0.0)
        else:  # WHOLE_SIDE
            selection = same_side
    return np.flatnonzero(selection).astype(np.intp, copy=False)


def _require_full_threshold_angles(angles: np.ndarray) -> None:
    for required in (-90.0, 90.0):
        if not np.any(np.isclose(angles, required, atol=1e-7, rtol=0.0)):
            raise ValueError("threshold generation requires -90, 0, and 90 degree masks")


def _quantize_transition(normalized_angle: np.ndarray) -> np.ndarray:
    """Encode authored transition progress as lilToon's inverted SDF value."""

    transition = np.clip(normalized_angle, 0.0, 1.0)
    values = np.floor((1.0 - transition) * 65535.0 + 0.5)
    return values.astype(np.uint16)


def _threshold_for_side(
    masks: np.ndarray,
    angles: np.ndarray,
    indices: np.ndarray,
    sdf_cache: dict[int, np.ndarray],
) -> np.ndarray:
    height, width = masks.shape[1:]
    threshold = np.full((height, width), ALWAYS_SHADOW, dtype=np.uint16)
    threshold[masks[indices[0]]] = ALWAYS_LIGHT

    for first, second in zip(indices[:-1], indices[1:]):
        transition = ~masks[first] & masks[second]
        if not np.any(transition):
            continue
        if int(first) not in sdf_cache:
            sdf_cache[int(first)] = exact_signed_edt(masks[first])
        if int(second) not in sdf_cache:
            sdf_cache[int(second)] = exact_signed_edt(masks[second])
        distance0 = np.abs(sdf_cache[int(first)][transition])
        distance1 = np.abs(sdf_cache[int(second)][transition])
        denominator = distance0 + distance1
        ratio = np.divide(
            distance0,
            denominator,
            out=np.full_like(distance0, 0.5),
            where=np.isfinite(denominator) & (denominator > 0.0),
        )
        angle0 = abs(float(angles[first])) / 90.0
        angle1 = abs(float(angles[second])) / 90.0
        interpolated = angle0 + (angle1 - angle0) * ratio
        threshold[transition] = _quantize_transition(interpolated)
    return threshold


def generate_threshold_channels(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    *,
    validate: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate positive-side R and negative-side G lilToon SDF values."""

    masks = _as_binary(mask_stack, ndim=3)
    angle_values = _validated_angles(angles, masks.shape[0])
    _require_full_threshold_angles(angle_values)
    if validate:
        report = validate_monotonic(masks, angle_values)
        if not report.is_valid:
            raise ValueError(
                "mask stack is not monotonic: "
                f"{report.violation_pixel_count} pixels, "
                f"{report.violation_count} invalid transitions"
            )
    sdf_cache: dict[int, np.ndarray] = {}
    positive = _threshold_for_side(
        masks, angle_values, _side_indices(angle_values, 1), sdf_cache
    )
    negative = _threshold_for_side(
        masks, angle_values, _side_indices(angle_values, -1), sdf_cache
    )
    return positive, negative


def _validated_side_stack(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    *,
    name: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Validate one artist-facing 0..90 degree mask lane.

    Quick SDF Paint authors the right and left lanes independently instead of
    encoding the side in an angle sign. Keeping this validation separate from
    the signed-stack convenience API makes it impossible to accidentally swap
    the R and G output channels.
    """

    masks = _as_binary(mask_stack, ndim=3)
    values = _validated_angles(angles, masks.shape[0])
    if np.any(values < -_ANGLE_EPSILON) or np.any(values > 90.0 + _ANGLE_EPSILON):
        raise ValueError(f"{name} angles must be in the range 0..90 degrees")
    order = np.argsort(values, kind="stable")
    masks = masks[order]
    values = values[order]
    if not np.isclose(values[0], 0.0, atol=_ANGLE_EPSILON, rtol=0.0):
        raise ValueError(f"{name} requires a 0 degree mask")
    if not np.isclose(values[-1], 90.0, atol=_ANGLE_EPSILON, rtol=0.0):
        raise ValueError(f"{name} requires a 90 degree mask")
    return masks, values


def validate_side_monotonic(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> MonotonicValidation:
    """Validate that light pixels only expand from front to profile."""

    masks, values = _validated_side_stack(mask_stack, angles, name="side")
    offending: list[tuple[float, float, int]] = []
    violation_map = np.zeros(masks.shape[1:], dtype=np.bool_)
    violation_count = 0
    for first, second, angle0, angle1 in zip(
        masks[:-1], masks[1:], values[:-1], values[1:]
    ):
        invalid = first & ~second
        count = int(np.count_nonzero(invalid))
        if count:
            violation_count += 1
            violation_map |= invalid
            offending.append((float(angle0), float(angle1), count))
    return MonotonicValidation(
        is_valid=not offending,
        violation_count=violation_count,
        violation_pixel_count=int(np.count_nonzero(violation_map)),
        violation_map=violation_map,
        positive_violation_map=violation_map.copy(),
        negative_violation_map=np.zeros_like(violation_map),
        offending_transitions=tuple(offending),
    )


def pack_lane_bits(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    base_stack: np.ndarray | Sequence[object],
    coverage_stack: np.ndarray | Sequence[object],
) -> PackedLane:
    """Pack three lane stacks into ABI-7 per-pixel ``uint16`` bit fields."""

    masks, values = _validated_side_stack(mask_stack, angles, name="lane")
    base = _as_binary(base_stack, ndim=3)
    coverage = _as_binary(coverage_stack, ndim=3)
    if base.shape != masks.shape or coverage.shape != masks.shape:
        raise ValueError("mask, base, and coverage stacks must have the same shape")
    if masks.shape[0] > 16:
        raise ValueError("packed lanes support at most 16 angle keys")

    # _validated_side_stack sorts the display stack. Apply the same stable
    # order to the other two planes before assigning their bits.
    original_angles = _validated_angles(angles, masks.shape[0])
    order = np.argsort(original_angles, kind="stable")
    base = np.ascontiguousarray(base[order])
    coverage = np.ascontiguousarray(coverage[order])
    shape = masks.shape[1:]
    display_bits = np.zeros(shape, dtype=np.uint16)
    base_bits = np.zeros(shape, dtype=np.uint16)
    coverage_bits = np.zeros(shape, dtype=np.uint16)
    for index in range(masks.shape[0]):
        bit = np.uint16(1 << index)
        display_bits[masks[index]] |= bit
        base_bits[base[index]] |= bit
        coverage_bits[coverage[index]] |= bit
    return PackedLane(
        angles=np.ascontiguousarray(values, dtype=np.float64),
        display_bits=display_bits,
        base_bits=base_bits,
        coverage_bits=coverage_bits,
    )


def unpack_lane_bits(bits: np.ndarray | Sequence[object], count: int) -> np.ndarray:
    """Expand an ABI-7 bit field to a boolean stack (reference/debug path)."""

    values = np.asarray(bits)
    if values.ndim != 2 or any(size <= 0 for size in values.shape):
        raise ValueError("packed lane bits must be a non-empty 2D plane")
    if values.dtype != np.uint16:
        if not np.issubdtype(values.dtype, np.integer):
            raise TypeError("packed lane bits must contain integer values")
        if np.any(values < 0) or np.any(values > np.iinfo(np.uint16).max):
            raise ValueError("packed lane bits must fit in uint16")
        values = np.asarray(values, dtype=np.uint16)
    key_count = int(count)
    if key_count < 1 or key_count > 16:
        raise ValueError("packed lane key count must be in [1, 16]")
    shifts = np.arange(key_count, dtype=np.uint16)[:, None, None]
    return np.ascontiguousarray(((values[None, ...] >> shifts) & 1) != 0)


def repair_packed_lane(lane: PackedLane) -> PackedLaneRepairResult:
    """Pure NumPy reference for ABI-7 compact monotonic repair."""

    if not isinstance(lane, PackedLane):
        raise TypeError("lane must be a PackedLane")
    values = _validated_angles(lane.angles, lane.count)
    if lane.count < 1 or lane.count > 16:
        raise ValueError("packed lanes support between 1 and 16 angle keys")
    if np.any(np.diff(values) <= _ANGLE_EPSILON):
        raise ValueError("packed lane angles must be strictly increasing")
    planes = []
    for name, plane in (
        ("display", lane.display_bits),
        ("base", lane.base_bits),
        ("coverage", lane.coverage_bits),
    ):
        array = np.asarray(plane)
        if array.ndim != 2 or any(size <= 0 for size in array.shape):
            raise ValueError(f"{name}_bits must be a non-empty 2D plane")
        if array.dtype != np.uint16:
            raise TypeError(f"{name}_bits must use uint16")
        planes.append(np.ascontiguousarray(array))
    display_bits, base_bits, coverage_bits = planes
    if base_bits.shape != display_bits.shape or coverage_bits.shape != display_bits.shape:
        raise ValueError("packed display, base, and coverage planes must share a shape")

    protected_cost = np.zeros(display_bits.shape, dtype=np.int16)
    display_cost = np.zeros(display_bits.shape, dtype=np.int16)
    base_cost = np.zeros(display_bits.shape, dtype=np.int16)
    for index in range(lane.count):
        bit = np.uint16(1 << index)
        display = (display_bits & bit) != 0
        base = (base_bits & bit) != 0
        protected = ((coverage_bits & bit) != 0) | (display != base)
        display_cost += (~display).astype(np.int16)
        base_cost += (~base).astype(np.int16)
        protected_cost += (protected & ~display).astype(np.int16)

    best_protected = protected_cost.copy()
    best_display = display_cost.copy()
    best_base = base_cost.copy()
    transitions = np.zeros(display_bits.shape, dtype=np.uint8)
    for transition in range(1, lane.count + 1):
        bit = np.uint16(1 << (transition - 1))
        display = (display_bits & bit) != 0
        base = (base_bits & bit) != 0
        protected = ((coverage_bits & bit) != 0) | (display != base)
        display_delta = np.where(display, 1, -1).astype(np.int16)
        protected_cost += display_delta * protected.astype(np.int16)
        display_cost += display_delta
        base_cost += np.where(base, 1, -1).astype(np.int16)
        better = (protected_cost < best_protected) | (
            (protected_cost == best_protected)
            & (
                (display_cost < best_display)
                | ((display_cost == best_display) & (base_cost < best_base))
            )
        )
        best_protected[better] = protected_cost[better]
        best_display[better] = display_cost[better]
        best_base[better] = base_cost[better]
        transitions[better] = np.uint8(transition)

    changed_count = np.zeros(display_bits.shape, dtype=np.uint8)
    protected_changed_count = np.zeros(display_bits.shape, dtype=np.uint8)
    for index in range(lane.count):
        bit = np.uint16(1 << index)
        display = (display_bits & bit) != 0
        base = (base_bits & bit) != 0
        repaired = index >= transitions
        changed = repaired != display
        protected = ((coverage_bits & bit) != 0) | (display != base)
        changed_count += changed.astype(np.uint8)
        protected_changed_count += (changed & protected).astype(np.uint8)
    return PackedLaneRepairResult(
        transition_indices=np.ascontiguousarray(transitions),
        changed_count=np.ascontiguousarray(changed_count),
        changed_sample_count=int(np.sum(changed_count, dtype=np.uint64)),
        changed_pixel_count=int(np.count_nonzero(changed_count)),
        protected_changed_sample_count=int(
            np.sum(protected_changed_count, dtype=np.uint64)
        ),
        protected_changed_pixel_count=int(np.count_nonzero(protected_changed_count)),
    )


def repair_side_monotonic(
    mask_stack: np.ndarray | Sequence[object],
    base_stack: np.ndarray | Sequence[object],
    coverage_stack: np.ndarray | Sequence[object],
) -> MonotonicRepairResult:
    """Project one 0..90 lane to the nearest valid Shadow→Light sequence.

    For every pixel, all ``N + 1`` valid transition positions are considered.
    Candidate cost is minimized lexicographically by protected edits, all
    display edits, then distance from the base guide. Inputs are never mutated.
    """

    masks = _as_binary(mask_stack, ndim=3)
    base = _as_binary(base_stack, ndim=3)
    coverage = _as_binary(coverage_stack, ndim=3)
    if base.shape != masks.shape or coverage.shape != masks.shape:
        raise ValueError("mask, base, and coverage stacks must have the same shape")

    protected = coverage | (masks != base)
    # t=0 is always Light. Moving from t to t+1 changes sample t to Shadow.
    protected_cost = np.count_nonzero(protected & ~masks, axis=0).astype(np.int32)
    display_cost = np.count_nonzero(~masks, axis=0).astype(np.int32)
    base_cost = np.count_nonzero(~base, axis=0).astype(np.int32)
    best_protected = protected_cost.copy()
    best_display = display_cost.copy()
    best_base = base_cost.copy()
    best_transition = np.zeros(masks.shape[1:], dtype=np.int32)

    for transition in range(1, masks.shape[0] + 1):
        index = transition - 1
        display_delta = np.where(masks[index], 1, -1).astype(np.int32)
        base_delta = np.where(base[index], 1, -1).astype(np.int32)
        protected_cost += display_delta * protected[index].astype(np.int32)
        display_cost += display_delta
        base_cost += base_delta
        better = (protected_cost < best_protected) | (
            (protected_cost == best_protected)
            & (
                (display_cost < best_display)
                | (
                    (display_cost == best_display)
                    & (base_cost < best_base)
                )
            )
        )
        if np.any(better):
            best_protected[better] = protected_cost[better]
            best_display[better] = display_cost[better]
            best_base[better] = base_cost[better]
            best_transition[better] = transition

    repaired = np.arange(masks.shape[0], dtype=np.int32)[:, None, None] >= best_transition[None, ...]
    repaired = np.ascontiguousarray(repaired, dtype=np.bool_)
    changed = np.ascontiguousarray(repaired != masks)
    protected_changed = changed & protected
    return MonotonicRepairResult(
        masks=repaired,
        changed_mask=changed,
        transition_indices=np.ascontiguousarray(best_transition),
        changed_sample_count=int(np.count_nonzero(changed)),
        changed_pixel_count=int(np.count_nonzero(np.any(changed, axis=0))),
        protected_changed_sample_count=int(np.count_nonzero(protected_changed)),
        protected_changed_pixel_count=int(
            np.count_nonzero(np.any(protected_changed, axis=0))
        ),
    )


def _threshold_for_lane(masks: np.ndarray, angles: np.ndarray) -> np.ndarray:
    indices = np.arange(masks.shape[0], dtype=np.intp)
    return _threshold_for_side(masks, angles, indices, {})


def generate_threshold_transitions(
    transition_indices: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    *,
    out: np.ndarray | None = None,
    channel: int = 0,
) -> np.ndarray:
    """Generate one exact threshold channel from compact transition indices.

    ``transition_indices`` stores the number of leading Shadow keys per pixel:
    zero is always Light and ``len(angles)`` is always Shadow.  ``out`` may be
    either a two-dimensional uint16 plane or an HxWxC uint16 image; the latter
    lets the native/export path write directly into an RGBA destination.
    """

    values = np.asarray(angles, dtype=np.float64)
    if values.ndim != 1 or values.size < 2 or values.size > 16:
        raise ValueError("angles must contain between 2 and 16 keys")
    if not np.all(np.isfinite(values)) or np.any(np.diff(values) <= _ANGLE_EPSILON):
        raise ValueError("angles must be finite and strictly increasing")
    if not np.isclose(values[0], 0.0, atol=_ANGLE_EPSILON, rtol=0.0) or not np.isclose(
        values[-1], 90.0, atol=_ANGLE_EPSILON, rtol=0.0
    ):
        raise ValueError("angles must include 0 and 90 degree endpoints")
    transitions = np.asarray(transition_indices)
    if transitions.ndim != 2 or any(size <= 0 for size in transitions.shape):
        raise ValueError("transition indices must be a non-empty 2D plane")
    if not np.issubdtype(transitions.dtype, np.integer):
        raise TypeError("transition indices must contain integers")
    if np.any(transitions < 0) or np.any(transitions > values.size):
        raise ValueError("transition indices are outside the angle sequence")
    masks = np.arange(values.size, dtype=np.int16)[:, None, None] >= transitions[None, ...]
    threshold = _threshold_for_lane(np.ascontiguousarray(masks), values)
    if out is None:
        return threshold
    destination = np.asarray(out)
    if destination.dtype != np.uint16:
        raise TypeError("threshold output must use uint16")
    if not destination.flags.writeable:
        raise ValueError("threshold output must be writeable")
    if destination.ndim == 2:
        if destination.shape != threshold.shape:
            raise ValueError("threshold output shape does not match transition plane")
        destination[...] = threshold
    elif destination.ndim == 3:
        selected = int(channel)
        if destination.shape[:2] != threshold.shape or not 0 <= selected < destination.shape[2]:
            raise ValueError("threshold output channel or shape is invalid")
        destination[..., selected] = threshold
    else:
        raise ValueError("threshold output must be a 2D plane or HxWxC image")
    return destination


def generate_threshold_pair_channels(
    right_masks: np.ndarray | Sequence[object],
    right_angles: Sequence[float] | np.ndarray,
    left_masks: np.ndarray | Sequence[object],
    left_angles: Sequence[float] | np.ndarray,
    *,
    validate: bool = True,
) -> np.ndarray:
    """Generate canonical right/left threshold channels from two 0..90 lanes.

    The returned contiguous ``(height, width, 2) uint16`` array stores the
    right threshold in plane 0 and the left threshold in plane 1.  It has no
    output-channel packing semantics: callers choose how these canonical
    signals map to an exported texture.  Each lane owns its own 0 degree mask,
    which is important after an artist chooses *Break Mirror*. A pixel's
    Light-transition progress ``u`` is encoded as
    ``round((1 - u) * 65535)`` over the complete uint16 range.
    """

    right, right_values = _validated_side_stack(
        right_masks, right_angles, name="right lane"
    )
    left, left_values = _validated_side_stack(left_masks, left_angles, name="left lane")
    if right.shape[1:] != left.shape[1:]:
        raise ValueError("right and left mask lanes must use the same dimensions")
    if validate:
        for label, masks, values in (
            ("right", right, right_values),
            ("left", left, left_values),
        ):
            report = validate_side_monotonic(masks, values)
            if not report.is_valid:
                raise ValueError(
                    f"{label} mask lane is not monotonic: "
                    f"{report.violation_pixel_count} pixels, "
                    f"{report.violation_count} invalid transitions"
                )
    red = _threshold_for_lane(right, right_values)
    green = _threshold_for_lane(left, left_values)
    output = np.empty((*red.shape, 2), dtype=np.uint16)
    output[..., 0] = red
    output[..., 1] = green
    return output


__all__ = [
    "ALWAYS_LIGHT",
    "ALWAYS_SHADOW",
    "GuardClipResult",
    "MonotonicValidation",
    "MonotonicRepairResult",
    "RangeScope",
    "PackedLane",
    "PackedLaneRepairResult",
    "exact_edt",
    "exact_signed_edt",
    "generate_threshold_channels",
    "generate_threshold_pair_channels",
    "generate_threshold_transitions",
    "guard_clip_proposal",
    "interpolate_binary_masks",
    "pack_lane_bits",
    "range_target_indices",
    "repair_packed_lane",
    "repair_side_monotonic",
    "unpack_lane_bits",
    "validate_monotonic",
    "validate_side_monotonic",
]

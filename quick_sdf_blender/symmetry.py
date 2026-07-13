# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent UV symmetry helpers for Quick SDF.

The authoring direction is deliberately one-way: positive-angle masks are the
source and matching negative-angle masks are generated from them.  This keeps
symmetry predictable even when the angle stack is stored in an arbitrary order.

Island pairs are supplied as ``(source_mask, target_mask)`` tuples (or as
:class:`IslandPair` instances).  Both masks use image-array coordinates and
must have the same height and width as one mask in the stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

import numpy as np


_ANGLE_EPSILON = 1.0e-7


class SymmetryMode(str, Enum):
    """Supported UV symmetry layouts."""

    INDEPENDENT = "INDEPENDENT"
    OVERLAPPED = "OVERLAPPED"
    TEXTURE_MIRROR = "TEXTURE_MIRROR"
    ISLAND_PAIR = "ISLAND_PAIR"
    AUTO = "AUTO"


@dataclass(frozen=True)
class IslandPair:
    """Occupancy masks for a positive-side source and negative-side target."""

    source_mask: np.ndarray
    target_mask: np.ndarray


@dataclass(frozen=True)
class SymmetryAnalysis:
    """AUTO layout suggestion derived from UV occupancy.

    ``overlap_score`` is the intersection-over-union score without moving the
    source occupancy. ``mirror_score`` compares the target against a global U
    flip of the source. Confidence includes both match quality and separation
    from the competing layout, so a perfectly U-symmetric occupancy correctly
    reports an ambiguous result even when both scores are one.
    """

    suggested_mode: SymmetryMode
    confidence: float
    mirror_score: float
    overlap_score: float
    requires_confirmation: bool


IslandPairLike = (
    IslandPair
    | tuple[np.ndarray, np.ndarray]
    | Mapping[str, np.ndarray]
)


def _as_mode(mode: SymmetryMode | str) -> SymmetryMode:
    if isinstance(mode, SymmetryMode):
        return mode
    text = str(mode).strip().upper()
    if text == "OVERLAPPED_UV":
        text = "OVERLAPPED"
    try:
        return SymmetryMode(text)
    except ValueError as error:
        raise ValueError(f"unknown symmetry mode: {mode!r}") from error


def _as_occupancy(values: np.ndarray | Sequence[object], *, name: str) -> np.ndarray:
    array = np.asarray(values)
    if array.ndim != 2 or any(size <= 0 for size in array.shape):
        raise ValueError(f"{name} must be a non-empty 2D array")
    if array.dtype == np.bool_:
        return np.ascontiguousarray(array)
    if not np.issubdtype(array.dtype, np.number):
        raise TypeError(f"{name} must contain boolean or numeric values")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must not contain NaN or infinity")
    return np.ascontiguousarray(array != 0)


def _iou(first: np.ndarray, second: np.ndarray) -> float:
    union = first | second
    union_count = int(np.count_nonzero(union))
    if union_count == 0:
        return 0.0
    return float(np.count_nonzero(first & second)) / float(union_count)


def analyze_symmetry(
    positive_occupancy: np.ndarray | Sequence[object],
    negative_occupancy: np.ndarray | Sequence[object],
    *,
    match_threshold: float = 0.75,
    confirmation_threshold: float = 0.65,
) -> SymmetryAnalysis:
    """Suggest an AUTO symmetry layout from positive/negative UV occupancy.

    The two inputs normally come from rasterizing faces on opposite sides of a
    character into its UV map.  Exact overlap suggests shared/overlapped UVs;
    overlap after reversing the U axis suggests a texture mirror.  A weak match
    recommends independent authoring.  Island-pair layouts are intentionally a
    manual/fallback choice because occupancy alone cannot establish topology.
    """

    positive = _as_occupancy(positive_occupancy, name="positive_occupancy")
    negative = _as_occupancy(negative_occupancy, name="negative_occupancy")
    if positive.shape != negative.shape:
        raise ValueError("positive and negative occupancy must have the same shape")
    if not 0.0 <= match_threshold <= 1.0:
        raise ValueError("match_threshold must be in the range 0..1")
    if not 0.0 <= confirmation_threshold <= 1.0:
        raise ValueError("confirmation_threshold must be in the range 0..1")

    overlap_score = _iou(positive, negative)
    mirror_score = _iou(positive[:, ::-1], negative)
    best_score = max(overlap_score, mirror_score)

    if not np.any(positive) and not np.any(negative):
        suggested = SymmetryMode.INDEPENDENT
        confidence = 0.0
    elif best_score < match_threshold:
        suggested = SymmetryMode.INDEPENDENT
        # A low best score is evidence that neither global layout applies.
        confidence = 1.0 - best_score
    else:
        # Prefer overlap for a tie: it is lossless and avoids an unnecessary
        # resample. A tie still produces zero confidence below.
        suggested = (
            SymmetryMode.OVERLAPPED
            if overlap_score >= mirror_score
            else SymmetryMode.TEXTURE_MIRROR
        )
        competing_score = min(overlap_score, mirror_score)
        confidence = best_score * (1.0 - competing_score)

    confidence = float(np.clip(confidence, 0.0, 1.0))
    return SymmetryAnalysis(
        suggested_mode=suggested,
        confidence=confidence,
        mirror_score=mirror_score,
        overlap_score=overlap_score,
        requires_confirmation=confidence < confirmation_threshold,
    )


def _validated_stack_and_angles(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    masks = np.asarray(mask_stack)
    if masks.ndim != 3 or any(size <= 0 for size in masks.shape):
        raise ValueError("mask_stack must be a non-empty (angle, height, width) array")
    angle_values = np.asarray(angles, dtype=np.float64)
    if angle_values.ndim != 1 or angle_values.size != masks.shape[0]:
        raise ValueError(f"expected {masks.shape[0]} angles, got shape {angle_values.shape}")
    if not np.all(np.isfinite(angle_values)):
        raise ValueError("angles must be finite")
    sorted_angles = np.sort(angle_values)
    if sorted_angles.size > 1 and np.any(np.diff(sorted_angles) <= _ANGLE_EPSILON):
        raise ValueError("angles must be unique")
    return masks, angle_values


def _coerce_pair(pair: IslandPairLike, shape: tuple[int, int]) -> IslandPair:
    if isinstance(pair, IslandPair):
        source_values, target_values = pair.source_mask, pair.target_mask
    elif isinstance(pair, Mapping):
        try:
            source_values = pair["source_mask"]
            target_values = pair["target_mask"]
        except KeyError as error:
            raise ValueError(
                "island pair mappings need source_mask and target_mask"
            ) from error
    else:
        try:
            source_values, target_values = pair
        except (TypeError, ValueError) as error:
            raise ValueError("each island pair must contain source and target masks") from error

    source = _as_occupancy(source_values, name="island source mask")
    target = _as_occupancy(target_values, name="island target mask")
    if source.shape != shape or target.shape != shape:
        raise ValueError(f"island pair masks must have shape {shape}")
    if not np.any(source) or not np.any(target):
        raise ValueError("island pair masks must each contain at least one occupied pixel")
    return IslandPair(source, target)


def _validated_pairs(
    island_pairs: Sequence[IslandPairLike] | None,
    shape: tuple[int, int],
) -> tuple[IslandPair, ...]:
    if island_pairs is None:
        return ()
    pairs = tuple(_coerce_pair(pair, shape) for pair in island_pairs)
    target_union = np.zeros(shape, dtype=np.bool_)
    for pair in pairs:
        if np.any(target_union & pair.target_mask):
            raise ValueError("target masks in island_pairs must not overlap")
        target_union |= pair.target_mask
    return pairs


def _bounding_box(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())


def _bbox_local_mirror_into(
    source_image: np.ndarray,
    target_image: np.ndarray,
    pair: IslandPair,
) -> None:
    """Nearest-resample a mirrored source bbox into target occupied pixels."""

    source_y0, source_y1, source_x0, source_x1 = _bounding_box(pair.source_mask)
    target_y0, target_y1, target_x0, target_x1 = _bounding_box(pair.target_mask)
    target_y, target_x = np.nonzero(pair.target_mask)

    target_height = target_y1 - target_y0
    target_width = target_x1 - target_x0
    if target_height:
        normalized_y = (target_y - target_y0) / float(target_height)
    else:
        normalized_y = np.full(target_y.shape, 0.5, dtype=np.float64)
    if target_width:
        normalized_x = (target_x - target_x0) / float(target_width)
    else:
        normalized_x = np.full(target_x.shape, 0.5, dtype=np.float64)

    source_y = np.rint(
        source_y0 + normalized_y * (source_y1 - source_y0)
    ).astype(np.intp)
    source_x = np.rint(
        source_x1 - normalized_x * (source_x1 - source_x0)
    ).astype(np.intp)
    target_image[target_y, target_x] = source_image[source_y, source_x]


def _validated_layer(values: np.ndarray | Sequence[object], *, name: str) -> np.ndarray:
    layer = np.asarray(values)
    if layer.ndim < 2 or layer.shape[0] <= 0 or layer.shape[1] <= 0:
        raise ValueError(f"{name} must have non-empty height and width dimensions")
    if layer.dtype.hasobject or layer.dtype.fields is not None:
        raise TypeError(f"{name} must use a plain, non-object dtype")
    if layer.dtype.kind not in "buifc":
        raise TypeError(f"{name} must contain boolean or numeric values")
    if not np.all(np.isfinite(layer)):
        raise ValueError(f"{name} must not contain NaN or infinity")
    return layer


def _resolve_live_mode(
    mode: SymmetryMode | str,
    shape: tuple[int, int],
    pairs: tuple[IslandPair, ...],
    source_occupancy: np.ndarray | Sequence[object] | None,
    target_occupancy: np.ndarray | Sequence[object] | None,
) -> SymmetryMode:
    selected = _as_mode(mode)
    if selected is not SymmetryMode.AUTO:
        return selected
    if (source_occupancy is None) != (target_occupancy is None):
        raise ValueError("AUTO requires both source_occupancy and target_occupancy")
    if source_occupancy is not None:
        source = _as_occupancy(source_occupancy, name="source_occupancy")
        target = _as_occupancy(target_occupancy, name="target_occupancy")
        if source.shape != shape or target.shape != shape:
            raise ValueError(f"AUTO occupancy masks must have shape {shape}")
    elif pairs:
        source = np.logical_or.reduce([pair.source_mask for pair in pairs])
        target = np.logical_or.reduce([pair.target_mask for pair in pairs])
    else:
        raise ValueError(
            "AUTO live mirroring needs occupancy masks or explicit island_pairs"
        )
    selected = analyze_symmetry(
        source, target, confirmation_threshold=0.90
    ).suggested_mode
    if selected is SymmetryMode.INDEPENDENT and pairs:
        return SymmetryMode.ISLAND_PAIR
    return selected


def mirror_side_layer(
    source_layer: np.ndarray | Sequence[object],
    mode: SymmetryMode | str,
    island_pairs: Sequence[IslandPairLike] | None = None,
    target_template: np.ndarray | Sequence[object] | None = None,
    *,
    source_occupancy: np.ndarray | Sequence[object] | None = None,
    target_occupancy: np.ndarray | Sequence[object] | None = None,
) -> np.ndarray:
    """Generate one opposite-side image layer from an authored-side layer.

    The function is agnostic to layer semantics and therefore supports masks,
    RGB display layers, and boolean override coverage.  ``target_template`` is
    preserved outside paired target islands.  When omitted it defaults to
    zeros of the source dtype.  ``AUTO`` must be given occupancy masks (or
    explicit island pairs); paint values are never misinterpreted as UV
    occupancy.
    """

    source = _validated_layer(source_layer, name="source_layer")
    shape = (int(source.shape[0]), int(source.shape[1]))
    pairs = _validated_pairs(island_pairs, shape)
    selected = _resolve_live_mode(
        mode, shape, pairs, source_occupancy, target_occupancy
    )
    if target_template is None:
        target = np.zeros_like(source)
    else:
        template = _validated_layer(target_template, name="target_template")
        if template.shape != source.shape or template.dtype != source.dtype:
            raise ValueError("target_template must match source_layer shape and dtype")
        target = np.array(template, copy=True, order="C")

    if selected is SymmetryMode.INDEPENDENT:
        return target if target_template is not None else np.array(source, copy=True, order="C")
    if selected is SymmetryMode.OVERLAPPED:
        return np.array(source, copy=True, order="C")
    if selected is SymmetryMode.TEXTURE_MIRROR:
        return np.ascontiguousarray(source[:, ::-1, ...])
    if selected is not SymmetryMode.ISLAND_PAIR:
        raise RuntimeError(f"unhandled symmetry mode: {selected.value}")
    if not pairs:
        raise ValueError("ISLAND_PAIR symmetry requires island_pairs")
    for pair in pairs:
        _bbox_local_mirror_into(source, target, pair)
    return np.ascontiguousarray(target)


def mirror_side_stack(
    source_stack: np.ndarray | Sequence[object],
    mode: SymmetryMode | str,
    island_pairs: Sequence[IslandPairLike] | None = None,
    target_template: np.ndarray | Sequence[object] | None = None,
    *,
    source_occupancy: np.ndarray | Sequence[object] | None = None,
    target_occupancy: np.ndarray | Sequence[object] | None = None,
) -> np.ndarray:
    """Generate an opposite-side stack while preserving source angle order."""

    source = np.asarray(source_stack)
    if source.ndim < 3 or any(size <= 0 for size in source.shape[:3]):
        raise ValueError("source_stack must have shape (angle, height, width, ...)")
    template = None if target_template is None else np.asarray(target_template)
    if template is not None and (template.shape != source.shape or template.dtype != source.dtype):
        raise ValueError("target_template must match source_stack shape and dtype")
    layers = [
        mirror_side_layer(
            source[index],
            mode,
            island_pairs,
            None if template is None else template[index],
            source_occupancy=source_occupancy,
            target_occupancy=target_occupancy,
        )
        for index in range(source.shape[0])
    ]
    return np.ascontiguousarray(np.stack(layers, axis=0))


def _angle_pairs(angles: np.ndarray) -> tuple[tuple[int, int], ...]:
    """Return ``(positive_source, negative_target)`` indices."""

    result: list[tuple[int, int]] = []
    for negative_index in np.flatnonzero(angles < -_ANGLE_EPSILON):
        matches = np.flatnonzero(
            np.isclose(
                angles,
                -float(angles[negative_index]),
                atol=_ANGLE_EPSILON,
                rtol=0.0,
            )
        )
        if matches.size != 1:
            raise ValueError(
                f"negative angle {angles[negative_index]:g} has no matching positive source"
            )
        result.append((int(matches[0]), int(negative_index)))
    return tuple(result)


def apply_symmetry_to_stack(
    mask_stack: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    mode: SymmetryMode | str,
    island_pairs: Sequence[IslandPairLike] | None = None,
) -> np.ndarray:
    """Generate negative-angle masks from matching positive-angle masks.

    The returned stack is a copy with the same dtype and source order.  Positive
    and zero-degree masks are never modified.  ``INDEPENDENT`` is therefore a
    simple copy.  ``AUTO`` analyzes the union occupancy of ``island_pairs`` when
    supplied, otherwise it treats the farthest matched angle masks as occupancy
    hints; its recommendation is then applied deterministically.
    """

    masks, angle_values = _validated_stack_and_angles(mask_stack, angles)
    selected_mode = _as_mode(mode)
    output = np.array(masks, copy=True, order="C")
    if selected_mode is SymmetryMode.INDEPENDENT:
        return output

    pairs = _validated_pairs(island_pairs, masks.shape[1:])
    angle_pairs = _angle_pairs(angle_values)
    if not angle_pairs:
        return output

    if selected_mode is SymmetryMode.AUTO:
        if pairs:
            positive_occupancy = np.logical_or.reduce(
                [pair.source_mask for pair in pairs]
            )
            negative_occupancy = np.logical_or.reduce(
                [pair.target_mask for pair in pairs]
            )
        else:
            positive_index, negative_index = max(
                angle_pairs, key=lambda indices: abs(float(angle_values[indices[0]]))
            )
            positive_occupancy = masks[positive_index] != 0
            negative_occupancy = masks[negative_index] != 0
        selected_mode = analyze_symmetry(
            positive_occupancy, negative_occupancy
        ).suggested_mode
        if selected_mode is SymmetryMode.INDEPENDENT and pairs:
            # Explicitly paired islands are stronger evidence than a weak
            # whole-texture match, and provide a safe local fallback.
            selected_mode = SymmetryMode.ISLAND_PAIR
        if selected_mode is SymmetryMode.INDEPENDENT:
            return output

    if selected_mode is SymmetryMode.ISLAND_PAIR and not pairs:
        raise ValueError("ISLAND_PAIR symmetry requires island_pairs")

    for positive_index, negative_index in angle_pairs:
        source = masks[positive_index]
        if selected_mode is SymmetryMode.OVERLAPPED:
            output[negative_index] = source
        elif selected_mode is SymmetryMode.TEXTURE_MIRROR:
            output[negative_index] = source[:, ::-1]
        elif selected_mode is SymmetryMode.ISLAND_PAIR:
            target = output[negative_index]
            for pair in pairs:
                _bbox_local_mirror_into(source, target, pair)
        else:  # Defensive: all public modes are handled above.
            raise RuntimeError(f"unhandled symmetry mode: {selected_mode.value}")
    return output


__all__ = [
    "IslandPair",
    "SymmetryAnalysis",
    "SymmetryMode",
    "analyze_symmetry",
    "apply_symmetry_to_stack",
    "mirror_side_layer",
    "mirror_side_stack",
]

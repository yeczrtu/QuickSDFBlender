# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent UV normal rasterization and light-sweep baking.

The Blender runtime is responsible for extracting evaluated triangle UVs and
corner normals on the main thread.  This module deliberately accepts only
plain array-like values, so its reference implementation can run in a worker
thread, without ``bpy``, and on systems where the optional native DLL is not
available.

Array rows are top-down.  UV ``(0, 1)`` therefore maps to the top-left of the
returned image.  Pixels outside the supplied triangles are always Light.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


_EPSILON = 1.0e-12
_ANGLE_EPSILON = 1.0e-7


def _positive_dimension(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer")
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _validated_triangles(
    triangle_uvs: np.ndarray | Sequence[object],
    corner_normals: np.ndarray | Sequence[object],
) -> tuple[np.ndarray, np.ndarray]:
    uvs = np.asarray(triangle_uvs, dtype=np.float64)
    normals = np.asarray(corner_normals, dtype=np.float64)
    if uvs.ndim != 3 or uvs.shape[1:] != (3, 2):
        raise ValueError(f"triangle_uvs must have shape (N, 3, 2), got {uvs.shape}")
    if normals.ndim != 3 or normals.shape[1:] != (3, 3):
        raise ValueError(
            f"corner_normals must have shape (N, 3, 3), got {normals.shape}"
        )
    if uvs.shape[0] != normals.shape[0]:
        raise ValueError("triangle_uvs and corner_normals must contain the same triangles")
    if not np.all(np.isfinite(uvs)) or not np.all(np.isfinite(normals)):
        raise ValueError("triangle UVs and corner normals must be finite")
    lengths = np.linalg.norm(normals, axis=2)
    if np.any(lengths <= _EPSILON):
        raise ValueError("corner normals must have non-zero length")
    normalized = normals / lengths[..., None]
    return np.ascontiguousarray(uvs), np.ascontiguousarray(normalized)


def _validated_angles(angles: Sequence[float] | np.ndarray) -> np.ndarray:
    values = np.asarray(angles, dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("angles must be a non-empty 1D sequence")
    if not np.all(np.isfinite(values)):
        raise ValueError("angles must be finite")
    if np.any(values < -90.0 - _ANGLE_EPSILON) or np.any(
        values > 90.0 + _ANGLE_EPSILON
    ):
        raise ValueError("angles must be in the inclusive range -90..90")
    ordered = np.sort(values)
    if values.size > 1 and np.any(np.diff(ordered) <= _ANGLE_EPSILON):
        raise ValueError("angles must be unique")
    if np.count_nonzero(
        np.isclose(values, 0.0, atol=_ANGLE_EPSILON, rtol=0.0)
    ) != 1:
        raise ValueError("angles must contain exactly one 0 degree value")
    return np.ascontiguousarray(values)


def _validated_guide_angles(angles: Sequence[float] | np.ndarray) -> np.ndarray:
    values = np.asarray(angles, dtype=np.float64)
    if values.ndim != 1 or values.size < 2:
        raise ValueError("guide angles must be a one-dimensional sequence with at least two keys")
    if not np.all(np.isfinite(values)) or np.any(values < -_ANGLE_EPSILON) or np.any(
        values > 90.0 + _ANGLE_EPSILON
    ):
        raise ValueError("guide angles must be finite values in the inclusive range 0..90")
    if np.any(np.diff(values) <= _ANGLE_EPSILON):
        raise ValueError("guide angles must be strictly increasing")
    if not np.isclose(values[0], 0.0, atol=_ANGLE_EPSILON, rtol=0.0) or not np.isclose(
        values[-1], 90.0, atol=_ANGLE_EPSILON, rtol=0.0
    ):
        raise ValueError("guide angles must include 0 and 90 degree endpoints")
    return np.ascontiguousarray(values)


def _unit_vector(value: Sequence[float] | np.ndarray, *, name: str) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (3,):
        raise ValueError(f"{name} must contain exactly three values")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be finite")
    length = float(np.linalg.norm(vector))
    if length <= _EPSILON:
        raise ValueError(f"{name} must have non-zero length")
    return vector / length


def light_directions(
    angles: Sequence[float] | np.ndarray,
    forward: Sequence[float] | np.ndarray,
    up: Sequence[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return validated angles and their object-local unit light directions.

    A view-derived forward vector can contain a vertical component.  It is
    projected onto the plane perpendicular to ``up`` before rotation, matching
    a horizontal face-light sweep while still accepting artist-set view axes.
    """

    angle_values = _validated_angles(angles)
    up_vector = _unit_vector(up, name="up")
    forward_vector = _unit_vector(forward, name="forward")
    forward_vector = forward_vector - up_vector * float(
        np.dot(forward_vector, up_vector)
    )
    forward_length = float(np.linalg.norm(forward_vector))
    if forward_length <= _EPSILON:
        raise ValueError("forward and up must not be parallel")
    forward_vector /= forward_length

    radians = np.deg2rad(angle_values)
    cosine = np.cos(radians)[:, None]
    sine = np.sin(radians)[:, None]
    axis_cross_forward = np.cross(up_vector, forward_vector)
    directions = forward_vector[None, :] * cosine + axis_cross_forward[None, :] * sine
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    return angle_values, np.ascontiguousarray(directions, dtype=np.float32)


def guide_light_directions(
    angles: Sequence[float] | np.ndarray,
    forward: Sequence[float] | np.ndarray,
    up: Sequence[float] | np.ndarray,
    side: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return guide directions where 0° is side light and 90° is front light."""

    angle_values = _validated_guide_angles(angles)
    side_name = str(side).upper()
    if side_name not in {"RIGHT", "LEFT"}:
        raise ValueError("side must be RIGHT or LEFT")
    up_vector = _unit_vector(up, name="up")
    front = _unit_vector(forward, name="forward")
    front = front - up_vector * float(np.dot(front, up_vector))
    front_length = float(np.linalg.norm(front))
    if front_length <= _EPSILON:
        raise ValueError("forward and up must not be parallel")
    front /= front_length
    side_vector = np.cross(up_vector, front)
    side_vector /= float(np.linalg.norm(side_vector))
    if side_name == "LEFT":
        side_vector = -side_vector

    radians = np.deg2rad(angle_values)
    directions = (
        side_vector[None, :] * np.cos(radians)[:, None]
        + front[None, :] * np.sin(radians)[:, None]
    )
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    return angle_values, np.ascontiguousarray(directions, dtype=np.float32)


def shadow_amount_cutoff(shadow_amount: float) -> float:
    """Map the artist-facing 0..100 Shadow Amount to the N·L cutoff."""

    if isinstance(shadow_amount, bool):
        raise TypeError("shadow_amount must be a number")
    amount = float(shadow_amount)
    if not np.isfinite(amount) or not 0.0 <= amount <= 100.0:
        raise ValueError("shadow_amount must be in the range 0..100")
    return -0.15 + amount * 0.005


def enforce_monotonic_expansion(
    masks: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
) -> np.ndarray:
    """Return a copy where Light can only expand from front toward each side."""

    values = np.asarray(masks, dtype=np.bool_)
    if values.ndim != 3 or any(size <= 0 for size in values.shape):
        raise ValueError("masks must be a non-empty (angle, height, width) array")
    angle_values = _validated_angles(angles)
    if angle_values.size != values.shape[0]:
        raise ValueError(f"expected {values.shape[0]} angles, got {angle_values.size}")
    result = np.array(values, copy=True, order="C")
    zero_index = int(
        np.flatnonzero(
            np.isclose(angle_values, 0.0, atol=_ANGLE_EPSILON, rtol=0.0)
        )[0]
    )
    for sign in (1, -1):
        side = np.flatnonzero(angle_values * sign > _ANGLE_EPSILON)
        side = side[np.argsort(np.abs(angle_values[side]), kind="stable")]
        sequence = np.concatenate((np.asarray([zero_index], dtype=np.intp), side))
        for closer, farther in zip(sequence[:-1], sequence[1:]):
            result[farther] |= result[closer]
    return result


def rasterize_uv_normals(
    triangle_uvs: np.ndarray | Sequence[object],
    corner_normals: np.ndarray | Sequence[object],
    width: int,
    height: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Rasterize interpolated corner normals into a top-down UV image.

    Pixel centres are sampled.  Degenerate and fully out-of-tile triangles are
    ignored.  If triangles overlap, the later triangle deterministically wins;
    callers authoring shared UVs should filter evaluated geometry to the chosen
    character side before calling this function.

    Returns ``(normal_image, occupancy)`` where the first array is contiguous
    float32 ``(H, W, 3)`` and the second is contiguous bool ``(H, W)``.
    """

    uvs, normals = _validated_triangles(triangle_uvs, corner_normals)
    image_width = _positive_dimension(width, name="width")
    image_height = _positive_dimension(
        image_width if height is None else height, name="height"
    )
    normal_image = np.zeros((image_height, image_width, 3), dtype=np.float32)
    occupancy = np.zeros((image_height, image_width), dtype=np.bool_)

    for triangle_uv, triangle_normal in zip(uvs, normals):
        # Integer image coordinates identify pixel centres after the -0.5
        # offset.  V is inverted because NumPy row zero is the image top.
        x = triangle_uv[:, 0] * image_width - 0.5
        y = (1.0 - triangle_uv[:, 1]) * image_height - 0.5
        x0 = max(0, int(np.ceil(float(np.min(x)))))
        x1 = min(image_width - 1, int(np.floor(float(np.max(x)))))
        y0 = max(0, int(np.ceil(float(np.min(y)))))
        y1 = min(image_height - 1, int(np.floor(float(np.max(y)))))
        if x1 < x0 or y1 < y0:
            continue

        denominator = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (
            y[0] - y[2]
        )
        if abs(float(denominator)) <= _EPSILON:
            continue
        grid_y, grid_x = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
        weight0 = (
            (y[1] - y[2]) * (grid_x - x[2])
            + (x[2] - x[1]) * (grid_y - y[2])
        ) / denominator
        weight1 = (
            (y[2] - y[0]) * (grid_x - x[2])
            + (x[0] - x[2]) * (grid_y - y[2])
        ) / denominator
        weight2 = 1.0 - weight0 - weight1
        inside = (
            (weight0 >= -1.0e-9)
            & (weight1 >= -1.0e-9)
            & (weight2 >= -1.0e-9)
        )
        if not np.any(inside):
            continue

        interpolated = (
            weight0[..., None] * triangle_normal[0]
            + weight1[..., None] * triangle_normal[1]
            + weight2[..., None] * triangle_normal[2]
        )
        lengths = np.linalg.norm(interpolated, axis=2)
        safe = inside & (lengths > _EPSILON)
        if np.any(safe):
            interpolated[safe] /= lengths[safe, None]
        cancelled = inside & ~safe
        if np.any(cancelled):
            # Opposing custom normals can cancel exactly.  The dominant corner
            # is a deterministic, unit-length fallback for those rare samples.
            weights = np.stack((weight0, weight1, weight2), axis=2)
            dominant = np.argmax(weights[cancelled], axis=1)
            interpolated[cancelled] = triangle_normal[dominant]

        region = normal_image[y0 : y1 + 1, x0 : x1 + 1]
        region[inside] = interpolated[inside].astype(np.float32, copy=False)
        occupancy[y0 : y1 + 1, x0 : x1 + 1][inside] = True

    return np.ascontiguousarray(normal_image), np.ascontiguousarray(occupancy)


def bake_normal_sweep(
    triangle_uvs: np.ndarray | Sequence[object],
    corner_normals: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    forward: Sequence[float] | np.ndarray,
    up: Sequence[float] | np.ndarray,
    width: int,
    height: int | None = None,
    *,
    enforce_monotonic: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Bake binary Light masks from evaluated UVs and corner normals.

    This is the exact, dependency-free fallback for the optional native bake.
    It is intended for correctness and headless testing; dense production
    meshes should use :func:`quick_sdf_blender.native.bake_normal_sweep` when
    the version-2 DLL is available.
    """

    angle_values, directions = light_directions(angles, forward, up)
    normals, occupancy = rasterize_uv_normals(
        triangle_uvs, corner_normals, width, height
    )
    dots = np.einsum("hwc,ac->ahw", normals, directions, optimize=True)
    masks = (~occupancy)[None, :] | (dots >= 0.0)
    if enforce_monotonic:
        masks = enforce_monotonic_expansion(masks, angle_values)
    return np.ascontiguousarray(masks), occupancy


def bake_face_shadow_guide(
    triangle_uvs: np.ndarray | Sequence[object],
    corner_normals: np.ndarray | Sequence[object],
    angles: Sequence[float] | np.ndarray,
    forward: Sequence[float] | np.ndarray,
    up: Sequence[float] | np.ndarray,
    side: str,
    shadow_amount: float,
    width: int,
    height: int | None = None,
    *,
    enforce_monotonic: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Bake the artist-facing normal guide while keeping the legacy sweep intact."""

    angle_values, directions = guide_light_directions(angles, forward, up, side)
    cutoff = shadow_amount_cutoff(shadow_amount)
    normals, occupancy = rasterize_uv_normals(
        triangle_uvs, corner_normals, width, height
    )
    dots = np.einsum("hwc,ac->ahw", normals, directions, optimize=True)
    masks = (~occupancy)[None, :] | (dots >= cutoff)
    if enforce_monotonic:
        masks = np.ascontiguousarray(masks, dtype=np.bool_)
        for closer in range(1, masks.shape[0]):
            masks[closer] |= masks[closer - 1]
    return np.ascontiguousarray(masks), occupancy


__all__ = [
    "bake_face_shadow_guide",
    "bake_normal_sweep",
    "enforce_monotonic_expansion",
    "guide_light_directions",
    "light_directions",
    "rasterize_uv_normals",
    "shadow_amount_cutoff",
]

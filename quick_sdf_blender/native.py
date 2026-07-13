# SPDX-License-Identifier: GPL-3.0-or-later
"""ctypes bridge to the optional Windows exact-EDT core."""

from __future__ import annotations

import ctypes
from pathlib import Path

import numpy as np

_DLL = None


class NativeCoreError(RuntimeError):
    pass


def _load():
    global _DLL
    if _DLL is False:
        return None
    if _DLL is not None:
        return _DLL
    path = Path(__file__).with_name("bin") / "quicksdf_core.dll"
    if not path.exists():
        _DLL = False
        return None
    try:
        dll = ctypes.CDLL(str(path))
        u8p = ctypes.POINTER(ctypes.c_uint8)
        f32p = ctypes.POINTER(ctypes.c_float)
        u16p = ctypes.POINTER(ctypes.c_uint16)
        i32p = ctypes.POINTER(ctypes.c_int)
        dll.qsdf_version.argtypes = []
        dll.qsdf_version.restype = ctypes.c_int
        dll.qsdf_generate_threshold.argtypes = [
            u8p, f32p, ctypes.c_int, ctypes.c_int, ctypes.c_int, u16p, i32p
        ]
        dll.qsdf_generate_threshold.restype = ctypes.c_int
        dll.qsdf_validate_monotonic.argtypes = [
            u8p, f32p, ctypes.c_int, ctypes.c_int, ctypes.c_int, i32p
        ]
        dll.qsdf_validate_monotonic.restype = ctypes.c_int
        if hasattr(dll, "qsdf_generate_threshold_pair"):
            dll.qsdf_generate_threshold_pair.argtypes = [
                u8p, f32p, ctypes.c_int,
                u8p, f32p, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, u16p, i32p, i32p,
            ]
            dll.qsdf_generate_threshold_pair.restype = ctypes.c_int
        if hasattr(dll, "qsdf_generate_threshold_pair_cancelable"):
            dll.qsdf_generate_threshold_pair_cancelable.argtypes = [
                u8p, f32p, ctypes.c_int,
                u8p, f32p, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, u16p, i32p, i32p, i32p,
            ]
            dll.qsdf_generate_threshold_pair_cancelable.restype = ctypes.c_int
        if hasattr(dll, "qsdf_bake_normal_sweep"):
            dll.qsdf_bake_normal_sweep.argtypes = [
                f32p, f32p, ctypes.c_int,
                f32p, ctypes.c_int, f32p, f32p,
                ctypes.c_int, ctypes.c_int, u8p, u8p, i32p,
            ]
            dll.qsdf_bake_normal_sweep.restype = ctypes.c_int
        if hasattr(dll, "qsdf_bake_face_shadow_guide"):
            dll.qsdf_bake_face_shadow_guide.argtypes = [
                f32p, f32p, ctypes.c_int,
                f32p, ctypes.c_int, f32p, f32p,
                ctypes.c_int, ctypes.c_float,
                ctypes.c_int, ctypes.c_int, u8p, u8p, i32p,
            ]
            dll.qsdf_bake_face_shadow_guide.restype = ctypes.c_int
        if hasattr(dll, "qsdf_repair_side_monotonic"):
            dll.qsdf_repair_side_monotonic.argtypes = [
                u8p, u8p, u8p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                u8p, u8p, i32p,
                i32p, i32p, i32p, i32p, i32p,
            ]
            dll.qsdf_repair_side_monotonic.restype = ctypes.c_int
        _DLL = dll
    except OSError:
        _DLL = False
    return None if _DLL is False else _DLL


def available() -> bool:
    return _load() is not None


def version() -> int:
    dll = _load()
    return int(dll.qsdf_version()) if dll is not None else 0


def _prepare(masks, angles):
    binary = np.ascontiguousarray(np.asarray(masks) >= 0.5, dtype=np.uint8)
    angle_values = np.asarray(angles, dtype=np.float32)
    if binary.ndim != 3 or angle_values.shape != (binary.shape[0],):
        raise ValueError("masks must be (N,H,W) and angles must be (N,)")
    if not np.all(np.isfinite(angle_values)) or np.any(np.abs(angle_values) > 90.0):
        raise ValueError("angles must be finite values in [-90, 90]")
    if len(np.unique(angle_values)) != len(angle_values):
        raise ValueError("angles must be unique")
    if not np.any(np.isclose(angle_values, 0.0, atol=1.0e-5)):
        raise ValueError("angles must include zero degrees")
    if not np.any(np.isclose(angle_values, -90.0, atol=1.0e-5)) or not np.any(
        np.isclose(angle_values, 90.0, atol=1.0e-5)
    ):
        raise ValueError("angles must include both -90 and +90 degree endpoints")
    order = np.argsort(angle_values, kind="stable")
    return np.ascontiguousarray(binary[order]), np.ascontiguousarray(angle_values[order])


def _prepare_side(masks, angles, *, name: str):
    binary = np.ascontiguousarray(np.asarray(masks) >= 0.5, dtype=np.uint8)
    angle_values = np.asarray(angles, dtype=np.float32)
    if binary.ndim != 3 or angle_values.shape != (binary.shape[0],):
        raise ValueError(f"{name}_masks must be (N,H,W) and {name}_angles must be (N,)")
    if not np.all(np.isfinite(angle_values)) or np.any(angle_values < -1.0e-5) or np.any(
        angle_values > 90.0 + 1.0e-5
    ):
        raise ValueError(f"{name}_angles must be finite values in [0, 90]")
    order = np.argsort(angle_values, kind="stable")
    sorted_angles = np.ascontiguousarray(angle_values[order])
    if sorted_angles.size < 2 or np.any(np.diff(sorted_angles) <= 1.0e-5):
        raise ValueError(f"{name}_angles must be unique")
    if not np.isclose(sorted_angles[0], 0.0, atol=1.0e-5) or not np.isclose(
        sorted_angles[-1], 90.0, atol=1.0e-5
    ):
        raise ValueError(f"{name}_angles must include 0 and 90 degree endpoints")
    return np.ascontiguousarray(binary[order]), sorted_angles


def generate_threshold(masks, angles):
    """Return ``(H, W, 2) uint16`` or raise for invalid/non-monotonic input."""
    dll = _load()
    if dll is None:
        raise NativeCoreError("Native Quick SDF core is not available")
    binary, angle_values = _prepare(masks, angles)
    count, height, width = binary.shape
    output = np.empty((height, width, 2), dtype=np.uint16)
    violations = ctypes.c_int(0)
    code = dll.qsdf_generate_threshold(
        binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        count,
        width,
        height,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        ctypes.byref(violations),
    )
    if code == 2:
        raise NativeCoreError(f"Non-monotonic masks: {violations.value} violation pixels")
    if code:
        raise NativeCoreError(f"Native Quick SDF core failed with status {code}")
    return output


def generate_threshold_pair(
    right_masks, right_angles, left_masks, left_angles, *, cancel_flag=None
):
    """Return independent right/left ``(H, W, 2) uint16`` thresholds.

    Unlike the legacy signed-stack ABI, each side owns its own zero-degree
    mask.  Version-1 DLLs are rejected rather than silently merging those two
    masks.
    """

    dll = _load()
    if dll is None or not hasattr(dll, "qsdf_generate_threshold_pair"):
        raise NativeCoreError("Native Quick SDF threshold-pair core is not available")
    right, right_values = _prepare_side(right_masks, right_angles, name="right")
    left, left_values = _prepare_side(left_masks, left_angles, name="left")
    if right.shape[1:] != left.shape[1:]:
        raise ValueError("right and left mask stacks must have the same image dimensions")
    right_count, height, width = right.shape
    left_count = left.shape[0]
    output = np.empty((height, width, 2), dtype=np.uint16)
    right_violations = ctypes.c_int(0)
    left_violations = ctypes.c_int(0)
    if cancel_flag is None:
        cancel_value = ctypes.c_int(0)
    elif isinstance(cancel_flag, ctypes.c_int):
        cancel_value = cancel_flag
    else:
        cancel_value = ctypes.c_int(int(bool(cancel_flag)))
    use_cancelable = hasattr(dll, "qsdf_generate_threshold_pair_cancelable")
    function = (
        dll.qsdf_generate_threshold_pair_cancelable
        if use_cancelable
        else dll.qsdf_generate_threshold_pair
    )
    arguments = (
        right.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        right_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        right_count,
        left.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        left_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        left_count,
        width,
        height,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        ctypes.byref(right_violations),
        ctypes.byref(left_violations),
    )
    if use_cancelable:
        arguments += (ctypes.byref(cancel_value),)
    code = function(*arguments)
    if code == 2:
        raise NativeCoreError(
            "Non-monotonic masks: "
            f"right={right_violations.value}, left={left_violations.value} violation pixels"
        )
    if code == 4:
        raise NativeCoreError("Native Quick SDF threshold generation was cancelled")
    if code:
        raise NativeCoreError(f"Native Quick SDF threshold-pair core failed with status {code}")
    return output


def native_bake_available() -> bool:
    """Return whether the cancellable UV normal rasterizer is available."""

    dll = _load()
    return bool(
        dll is not None
        and version() >= 3
        and hasattr(dll, "qsdf_bake_normal_sweep")
    )


def native_guide_bake_available() -> bool:
    """Return whether the ABI-4 artist guide rasterizer is available."""

    dll = _load()
    return bool(
        dll is not None
        and version() >= 4
        and hasattr(dll, "qsdf_bake_face_shadow_guide")
    )


def native_repair_available() -> bool:
    dll = _load()
    return bool(
        dll is not None
        and version() >= 4
        and hasattr(dll, "qsdf_repair_side_monotonic")
    )


def bake_normal_sweep(
    triangle_uvs,
    corner_normals,
    angles,
    forward,
    up,
    width,
    height=None,
    *,
    enforce_monotonic=True,
    cancel_flag=None,
):
    """Rasterize an evaluated normal sweep in C++, or use the exact fallback."""

    from .bake import bake_normal_sweep as fallback

    if not native_bake_available() or not enforce_monotonic:
        return fallback(
            triangle_uvs,
            corner_normals,
            angles,
            forward,
            up,
            width,
            height,
            enforce_monotonic=enforce_monotonic,
        )
    uvs = np.ascontiguousarray(np.asarray(triangle_uvs), dtype=np.float32)
    normals = np.ascontiguousarray(np.asarray(corner_normals), dtype=np.float32)
    angle_values = np.ascontiguousarray(np.asarray(angles), dtype=np.float32)
    forward_values = np.ascontiguousarray(np.asarray(forward), dtype=np.float32)
    up_values = np.ascontiguousarray(np.asarray(up), dtype=np.float32)
    if uvs.ndim != 3 or uvs.shape[1:] != (3, 2):
        raise ValueError("triangle_uvs must have shape (N, 3, 2)")
    if normals.shape != (uvs.shape[0], 3, 3):
        raise ValueError("corner_normals must have shape (N, 3, 3)")
    if not np.all(np.isfinite(uvs)) or not np.all(np.isfinite(normals)):
        raise ValueError("triangle data must be finite")
    lengths = np.linalg.norm(normals, axis=2)
    if np.any(lengths <= 1.0e-12):
        raise ValueError("corner normals must have non-zero length")
    normals = np.ascontiguousarray(normals / lengths[..., None], dtype=np.float32)
    if angle_values.ndim != 1 or angle_values.size == 0:
        raise ValueError("angles must be a non-empty one-dimensional array")
    if forward_values.shape != (3,) or up_values.shape != (3,):
        raise ValueError("forward and up must contain three values")
    image_width = int(width)
    image_height = image_width if height is None else int(height)
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")
    masks = np.empty(
        (angle_values.size, image_height, image_width), dtype=np.uint8
    )
    occupancy = np.empty((image_height, image_width), dtype=np.uint8)
    if cancel_flag is None:
        cancel_value = ctypes.c_int(0)
    elif isinstance(cancel_flag, ctypes.c_int):
        cancel_value = cancel_flag
    else:
        cancel_value = ctypes.c_int(int(bool(cancel_flag)))
    dll = _load()
    code = dll.qsdf_bake_normal_sweep(
        uvs.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        normals.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        int(uvs.shape[0]),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        int(angle_values.size),
        forward_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        up_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        image_width,
        image_height,
        masks.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        occupancy.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF bake was cancelled")
    if code:
        raise NativeCoreError(f"Native Quick SDF bake failed with status {code}")
    return np.ascontiguousarray(masks.astype(np.bool_)), np.ascontiguousarray(
        occupancy.astype(np.bool_)
    )


def bake_face_shadow_guide(
    triangle_uvs,
    corner_normals,
    angles,
    forward,
    up,
    side,
    shadow_amount,
    width,
    height=None,
    *,
    enforce_monotonic=True,
    cancel_flag=None,
):
    """Bake the side-explicit face-shadow guide in ABI 4 or exact Python."""

    from .bake import (
        bake_face_shadow_guide as fallback,
        guide_light_directions,
        shadow_amount_cutoff,
    )

    if not native_guide_bake_available() or not enforce_monotonic:
        return fallback(
            triangle_uvs,
            corner_normals,
            angles,
            forward,
            up,
            side,
            shadow_amount,
            width,
            height,
            enforce_monotonic=enforce_monotonic,
        )
    uvs = np.ascontiguousarray(np.asarray(triangle_uvs), dtype=np.float32)
    normals = np.ascontiguousarray(np.asarray(corner_normals), dtype=np.float32)
    angle_values, _directions = guide_light_directions(angles, forward, up, side)
    angle_values = np.ascontiguousarray(angle_values, dtype=np.float32)
    forward_values = np.ascontiguousarray(np.asarray(forward), dtype=np.float32)
    up_values = np.ascontiguousarray(np.asarray(up), dtype=np.float32)
    cutoff = shadow_amount_cutoff(shadow_amount)
    if uvs.ndim != 3 or uvs.shape[1:] != (3, 2):
        raise ValueError("triangle_uvs must have shape (N, 3, 2)")
    if normals.shape != (uvs.shape[0], 3, 3):
        raise ValueError("corner_normals must have shape (N, 3, 3)")
    if not np.all(np.isfinite(uvs)) or not np.all(np.isfinite(normals)):
        raise ValueError("triangle data must be finite")
    lengths = np.linalg.norm(normals, axis=2)
    if np.any(lengths <= 1.0e-12):
        raise ValueError("corner normals must have non-zero length")
    normals = np.ascontiguousarray(normals / lengths[..., None], dtype=np.float32)
    image_width = int(width)
    image_height = image_width if height is None else int(height)
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")
    masks = np.empty((angle_values.size, image_height, image_width), dtype=np.uint8)
    occupancy = np.empty((image_height, image_width), dtype=np.uint8)
    if cancel_flag is None:
        cancel_value = ctypes.c_int(0)
    elif isinstance(cancel_flag, ctypes.c_int):
        cancel_value = cancel_flag
    else:
        cancel_value = ctypes.c_int(int(bool(cancel_flag)))
    side_sign = 1 if str(side).upper() == "RIGHT" else -1
    dll = _load()
    code = dll.qsdf_bake_face_shadow_guide(
        uvs.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        normals.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        int(uvs.shape[0]),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        int(angle_values.size),
        forward_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        up_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        side_sign,
        ctypes.c_float(cutoff),
        image_width,
        image_height,
        masks.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        occupancy.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF guide bake was cancelled")
    if code:
        raise NativeCoreError(f"Native Quick SDF guide bake failed with status {code}")
    return np.ascontiguousarray(masks.astype(np.bool_)), np.ascontiguousarray(
        occupancy.astype(np.bool_)
    )


def repair_side_monotonic(
    mask_stack, base_stack, coverage_stack, *, cancel_flag=None
):
    """Use ABI 4 to repair an export lane, falling back to the pure core."""

    from .core import (
        MonotonicRepairResult,
        _as_binary,
        repair_side_monotonic as fallback,
    )

    arrays = tuple(
        _as_binary(value, ndim=3)
        for value in (mask_stack, base_stack, coverage_stack)
    )
    if cancel_flag is not None and int(getattr(cancel_flag, "value", bool(cancel_flag))):
        raise NativeCoreError("Native Quick SDF repair was cancelled")
    if not native_repair_available():
        result = fallback(*arrays)
        if cancel_flag is not None and int(getattr(cancel_flag, "value", bool(cancel_flag))):
            raise NativeCoreError("Quick SDF repair was cancelled")
        return result
    masks, base, coverage = tuple(np.ascontiguousarray(array, dtype=np.uint8) for array in arrays)
    if masks.ndim != 3 or base.shape != masks.shape or coverage.shape != masks.shape:
        return fallback(*arrays)
    count, height, width = masks.shape
    repaired = np.empty_like(masks)
    changed = np.empty_like(masks)
    transitions = np.empty((height, width), dtype=np.int32)
    changed_samples = ctypes.c_int(0)
    changed_pixels = ctypes.c_int(0)
    protected_samples = ctypes.c_int(0)
    protected_pixels = ctypes.c_int(0)
    if cancel_flag is None:
        cancel_value = ctypes.c_int(0)
    elif isinstance(cancel_flag, ctypes.c_int):
        cancel_value = cancel_flag
    else:
        cancel_value = ctypes.c_int(int(bool(cancel_flag)))
    dll = _load()
    code = dll.qsdf_repair_side_monotonic(
        masks.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        base.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        coverage.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        count,
        width,
        height,
        repaired.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        changed.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        transitions.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        ctypes.byref(changed_samples),
        ctypes.byref(changed_pixels),
        ctypes.byref(protected_samples),
        ctypes.byref(protected_pixels),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF repair was cancelled")
    if code:
        raise NativeCoreError(f"Native Quick SDF repair failed with status {code}")
    return MonotonicRepairResult(
        masks=np.ascontiguousarray(repaired.astype(np.bool_)),
        changed_mask=np.ascontiguousarray(changed.astype(np.bool_)),
        transition_indices=np.ascontiguousarray(transitions),
        changed_sample_count=int(changed_samples.value),
        changed_pixel_count=int(changed_pixels.value),
        protected_changed_sample_count=int(protected_samples.value),
        protected_changed_pixel_count=int(protected_pixels.value),
    )


def validate_monotonic(masks, angles) -> int:
    dll = _load()
    if dll is None:
        raise NativeCoreError("Native Quick SDF core is not available")
    binary, angle_values = _prepare(masks, angles)
    count, height, width = binary.shape
    violations = ctypes.c_int(0)
    code = dll.qsdf_validate_monotonic(
        binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        count,
        width,
        height,
        ctypes.byref(violations),
    )
    if code not in (0, 2):
        raise NativeCoreError(f"Native Quick SDF validation failed with status {code}")
    return violations.value


__all__ = [
    "NativeCoreError",
    "available",
    "bake_face_shadow_guide",
    "bake_normal_sweep",
    "generate_threshold",
    "generate_threshold_pair",
    "native_bake_available",
    "native_guide_bake_available",
    "native_repair_available",
    "repair_side_monotonic",
    "validate_monotonic",
    "version",
]

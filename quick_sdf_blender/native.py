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
        f64p = ctypes.POINTER(ctypes.c_double)
        u16p = ctypes.POINTER(ctypes.c_uint16)
        i32p = ctypes.POINTER(ctypes.c_int)
        dll.qsdf_version.argtypes = []
        dll.qsdf_version.restype = ctypes.c_int
        dll.qsdf_generate_threshold.argtypes = [
            u8p, f64p, ctypes.c_int, ctypes.c_int, ctypes.c_int, u16p, i32p
        ]
        dll.qsdf_generate_threshold.restype = ctypes.c_int
        dll.qsdf_validate_monotonic.argtypes = [
            u8p, f64p, ctypes.c_int, ctypes.c_int, ctypes.c_int, i32p
        ]
        dll.qsdf_validate_monotonic.restype = ctypes.c_int
        if hasattr(dll, "qsdf_generate_threshold_pair"):
            dll.qsdf_generate_threshold_pair.argtypes = [
                u8p, f64p, ctypes.c_int,
                u8p, f64p, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, u16p, i32p, i32p,
            ]
            dll.qsdf_generate_threshold_pair.restype = ctypes.c_int
        if hasattr(dll, "qsdf_generate_threshold_pair_cancelable"):
            dll.qsdf_generate_threshold_pair_cancelable.argtypes = [
                u8p, f64p, ctypes.c_int,
                u8p, f64p, ctypes.c_int,
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
                f64p, ctypes.c_int, f64p, f64p,
                ctypes.c_int, ctypes.c_double,
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
        if hasattr(dll, "qsdf_repair_lane_bits"):
            dll.qsdf_repair_lane_bits.argtypes = [
                u16p, u16p, u16p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                u8p, u8p,
                i32p, i32p, i32p, i32p, i32p,
            ]
            dll.qsdf_repair_lane_bits.restype = ctypes.c_int
        if hasattr(dll, "qsdf_generate_threshold_transitions"):
            dll.qsdf_generate_threshold_transitions.argtypes = [
                u8p, f64p, ctypes.c_int,
                ctypes.c_int, ctypes.c_int,
                u16p, ctypes.c_int, ctypes.c_int, i32p, i32p,
            ]
            dll.qsdf_generate_threshold_transitions.restype = ctypes.c_int
        if hasattr(dll, "qsdf_interpolate_binary_masks"):
            dll.qsdf_interpolate_binary_masks.argtypes = [
                u8p, u8p, ctypes.c_int, ctypes.c_int, ctypes.c_double, u8p, i32p,
            ]
            dll.qsdf_interpolate_binary_masks.restype = ctypes.c_int
        _DLL = dll
    except OSError:
        _DLL = False
    return None if _DLL is False else _DLL


def available() -> bool:
    return _load() is not None


def version() -> int:
    dll = _load()
    return int(dll.qsdf_version()) if dll is not None else 0


def native_threshold_available() -> bool:
    """Return whether the loaded core implements the ABI-5 lilToon encoder."""

    dll = _load()
    return bool(
        dll is not None
        and version() >= 5
        and hasattr(dll, "qsdf_generate_threshold")
        and hasattr(dll, "qsdf_generate_threshold_pair")
    )


def _prepare(masks, angles):
    binary = np.ascontiguousarray(np.asarray(masks) >= 0.5, dtype=np.uint8)
    angle_values = np.asarray(angles, dtype=np.float64)
    if binary.ndim != 3 or angle_values.shape != (binary.shape[0],):
        raise ValueError("masks must be (N,H,W) and angles must be (N,)")
    if not np.all(np.isfinite(angle_values)) or np.any(np.abs(angle_values) > 90.0):
        raise ValueError("angles must be finite values in [-90, 90]")
    if len(np.unique(angle_values)) != len(angle_values):
        raise ValueError("angles must be unique")
    if not np.any(np.isclose(angle_values, 0.0, atol=1.0e-7, rtol=0.0)):
        raise ValueError("angles must include zero degrees")
    if not np.any(np.isclose(angle_values, -90.0, atol=1.0e-7, rtol=0.0)) or not np.any(
        np.isclose(angle_values, 90.0, atol=1.0e-7, rtol=0.0)
    ):
        raise ValueError("angles must include both -90 and +90 degree endpoints")
    order = np.argsort(angle_values, kind="stable")
    return np.ascontiguousarray(binary[order]), np.ascontiguousarray(angle_values[order])


def _prepare_side(masks, angles, *, name: str):
    binary = np.ascontiguousarray(np.asarray(masks) >= 0.5, dtype=np.uint8)
    angle_values = np.asarray(angles, dtype=np.float64)
    if binary.ndim != 3 or angle_values.shape != (binary.shape[0],):
        raise ValueError(f"{name}_masks must be (N,H,W) and {name}_angles must be (N,)")
    if not np.all(np.isfinite(angle_values)) or np.any(angle_values < -1.0e-7) or np.any(
        angle_values > 90.0 + 1.0e-7
    ):
        raise ValueError(f"{name}_angles must be finite values in [0, 90]")
    order = np.argsort(angle_values, kind="stable")
    sorted_angles = np.ascontiguousarray(angle_values[order])
    if sorted_angles.size < 2 or np.any(np.diff(sorted_angles) <= 1.0e-7):
        raise ValueError(f"{name}_angles must be unique")
    if not np.isclose(sorted_angles[0], 0.0, atol=1.0e-7, rtol=0.0) or not np.isclose(
        sorted_angles[-1], 90.0, atol=1.0e-7, rtol=0.0
    ):
        raise ValueError(f"{name}_angles must include 0 and 90 degree endpoints")
    return np.ascontiguousarray(binary[order]), sorted_angles


def generate_threshold(masks, angles):
    """Return ABI-5 lilToon ``(H, W, 2) uint16`` channel values."""
    dll = _load()
    if not native_threshold_available():
        raise NativeCoreError("Native Quick SDF ABI 5 threshold core is not available")
    binary, angle_values = _prepare(masks, angles)
    count, height, width = binary.shape
    output = np.empty((height, width, 2), dtype=np.uint16)
    violations = ctypes.c_int(0)
    code = dll.qsdf_generate_threshold(
        binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
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
    """Return independent right/left lilToon ``(H, W, 2) uint16`` values."""

    dll = _load()
    if not native_threshold_available():
        raise NativeCoreError("Native Quick SDF ABI 5 threshold-pair core is not available")
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
        right_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        right_count,
        left.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        left_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
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
    """Return whether the ABI-5 lilToon guide rasterizer is available."""

    dll = _load()
    return bool(
        dll is not None
        and version() >= 5
        and hasattr(dll, "qsdf_bake_face_shadow_guide")
    )


def native_repair_available() -> bool:
    dll = _load()
    return bool(
        dll is not None
        and version() >= 4
        and hasattr(dll, "qsdf_repair_side_monotonic")
    )


def native_packed_lane_available() -> bool:
    """Return whether the compact ABI-7 repair/generation path is available."""

    dll = _load()
    return bool(
        dll is not None
        and version() >= 7
        and hasattr(dll, "qsdf_repair_lane_bits")
        and hasattr(dll, "qsdf_generate_threshold_transitions")
    )


def native_interpolation_available() -> bool:
    """Return whether the ABI-6 exact binary-mask interpolator is available."""

    dll = _load()
    return bool(
        dll is not None
        and version() >= 6
        and hasattr(dll, "qsdf_interpolate_binary_masks")
    )


def interpolate_binary_masks(first, second, factor, cancel_flag=None):
    """Return an exact-SDF blend, using ABI 6 when it is available."""

    from .core import _as_binary, interpolate_binary_masks as fallback

    first_mask = _as_binary(first, ndim=2)
    second_mask = _as_binary(second, ndim=2)
    if first_mask.shape != second_mask.shape:
        raise ValueError("first and second masks must have the same shape")
    if not first_mask.size:
        raise ValueError("mask dimensions must be positive")
    value = float(factor)
    if not np.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("factor must be a finite value in [0, 1]")
    if cancel_flag is None:
        cancel_value = ctypes.c_int(0)
    elif isinstance(cancel_flag, ctypes.c_int):
        cancel_value = cancel_flag
    else:
        cancel_value = ctypes.c_int(int(bool(cancel_flag)))
    if cancel_value.value:
        raise NativeCoreError("Native Quick SDF interpolation was cancelled")
    if not native_interpolation_available():
        try:
            return fallback(first_mask, second_mask, value, cancel_flag=cancel_value)
        except RuntimeError as error:
            if "cancelled" in str(error):
                raise NativeCoreError("Quick SDF interpolation was cancelled") from error
            raise

    first_binary = np.ascontiguousarray(first_mask, dtype=np.uint8)
    second_binary = np.ascontiguousarray(second_mask, dtype=np.uint8)
    height, width = first_binary.shape
    output = np.empty((height, width), dtype=np.uint8)
    dll = _load()
    code = dll.qsdf_interpolate_binary_masks(
        first_binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        second_binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        width,
        height,
        ctypes.c_double(value),
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF interpolation was cancelled")
    if code:
        raise NativeCoreError(
            f"Native Quick SDF interpolation failed with status {code}"
        )
    return np.ascontiguousarray(output.astype(np.bool_))


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
    """Bake the side-explicit lilToon guide in ABI 5 or exact Python."""

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
    angle_values = np.ascontiguousarray(angle_values, dtype=np.float64)
    forward_values = np.ascontiguousarray(np.asarray(forward), dtype=np.float64)
    up_values = np.ascontiguousarray(np.asarray(up), dtype=np.float64)
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
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        int(angle_values.size),
        forward_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        up_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        side_sign,
        ctypes.c_double(cutoff),
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


def _cancel_value(cancel_flag):
    if cancel_flag is None:
        return ctypes.c_int(0)
    if isinstance(cancel_flag, ctypes.c_int):
        return cancel_flag
    return ctypes.c_int(int(bool(cancel_flag)))


def repair_packed_lane(lane, *, cancel_flag=None):
    """Repair a compact :class:`core.PackedLane` through ABI 7."""

    from .core import PackedLane, PackedLaneRepairResult, repair_packed_lane as fallback

    if not isinstance(lane, PackedLane):
        raise TypeError("lane must be a PackedLane")
    # The reference implementation performs the complete public validation.
    # Keep that behavior on machines without the bundled Windows core.
    if not native_packed_lane_available():
        if _cancel_value(cancel_flag).value:
            raise NativeCoreError("Quick SDF packed repair was cancelled")
        return fallback(lane)
    count = int(np.asarray(lane.angles).size)
    if count < 1 or count > 16:
        raise ValueError("packed lanes support between 1 and 16 angle keys")
    planes = []
    for name, value in (
        ("display_bits", lane.display_bits),
        ("base_bits", lane.base_bits),
        ("coverage_bits", lane.coverage_bits),
    ):
        array = np.asarray(value)
        if array.ndim != 2 or any(size <= 0 for size in array.shape):
            raise ValueError(f"{name} must be a non-empty 2D plane")
        if array.dtype != np.uint16:
            raise TypeError(f"{name} must use uint16")
        planes.append(np.ascontiguousarray(array))
    display_bits, base_bits, coverage_bits = planes
    if base_bits.shape != display_bits.shape or coverage_bits.shape != display_bits.shape:
        raise ValueError("packed display, base, and coverage planes must share a shape")
    height, width = display_bits.shape
    transitions = np.empty((height, width), dtype=np.uint8)
    changed_count = np.empty((height, width), dtype=np.uint8)
    changed_samples = ctypes.c_int(0)
    changed_pixels = ctypes.c_int(0)
    protected_samples = ctypes.c_int(0)
    protected_pixels = ctypes.c_int(0)
    cancel_value = _cancel_value(cancel_flag)
    if cancel_value.value:
        raise NativeCoreError("Native Quick SDF packed repair was cancelled")
    dll = _load()
    code = dll.qsdf_repair_lane_bits(
        display_bits.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        base_bits.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        coverage_bits.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        count,
        width,
        height,
        transitions.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        changed_count.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(changed_samples),
        ctypes.byref(changed_pixels),
        ctypes.byref(protected_samples),
        ctypes.byref(protected_pixels),
        ctypes.byref(cancel_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF packed repair was cancelled")
    if code:
        raise NativeCoreError(f"Native Quick SDF packed repair failed with status {code}")
    return PackedLaneRepairResult(
        transition_indices=transitions,
        changed_count=changed_count,
        changed_sample_count=int(changed_samples.value),
        changed_pixel_count=int(changed_pixels.value),
        protected_changed_sample_count=int(protected_samples.value),
        protected_changed_pixel_count=int(protected_pixels.value),
    )


def generate_threshold_transitions(
    transition_indices,
    angles,
    *,
    out=None,
    channel=0,
    cancel_flag=None,
    progress=None,
):
    """Generate one exact threshold channel from an ABI-7 transition map."""

    from .core import generate_threshold_transitions as fallback

    transitions = np.asarray(transition_indices)
    angle_values = np.asarray(angles, dtype=np.float64)
    if transitions.ndim != 2 or any(size <= 0 for size in transitions.shape):
        raise ValueError("transition indices must be a non-empty 2D plane")
    if not np.issubdtype(transitions.dtype, np.integer):
        raise TypeError("transition indices must contain integers")
    if angle_values.ndim != 1 or angle_values.size < 2 or angle_values.size > 16:
        raise ValueError("angles must contain between 2 and 16 keys")
    if not np.all(np.isfinite(angle_values)) or np.any(np.diff(angle_values) <= 1.0e-7):
        raise ValueError("angles must be finite and strictly increasing")
    if not np.isclose(angle_values[0], 0.0, atol=1.0e-7, rtol=0.0) or not np.isclose(
        angle_values[-1], 90.0, atol=1.0e-7, rtol=0.0
    ):
        raise ValueError("angles must include 0 and 90 degree endpoints")
    if np.any(transitions < 0) or np.any(transitions > angle_values.size):
        raise ValueError("transition indices are outside the angle sequence")
    transitions = np.ascontiguousarray(transitions, dtype=np.uint8)
    angle_values = np.ascontiguousarray(angle_values, dtype=np.float64)
    height, width = transitions.shape
    if out is None:
        destination = np.empty((height, width), dtype=np.uint16)
        pixel_stride = 1
        channel_offset = 0
    else:
        destination = np.asarray(out)
        if destination.dtype != np.uint16:
            raise TypeError("threshold output must use uint16")
        if not destination.flags.writeable or not destination.flags.c_contiguous:
            raise ValueError("threshold output must be writeable and C-contiguous")
        if destination.ndim == 2:
            if destination.shape != transitions.shape:
                raise ValueError("threshold output shape does not match transition plane")
            pixel_stride = 1
            channel_offset = 0
        elif destination.ndim == 3:
            channel_offset = int(channel)
            if destination.shape[:2] != transitions.shape or not 0 <= channel_offset < destination.shape[2]:
                raise ValueError("threshold output channel or shape is invalid")
            pixel_stride = int(destination.shape[2])
        else:
            raise ValueError("threshold output must be a 2D plane or HxWxC image")
    cancel_value = _cancel_value(cancel_flag)
    if progress is None:
        progress_value = ctypes.c_int(0)
    elif isinstance(progress, ctypes.c_int):
        progress_value = progress
    else:
        raise TypeError("progress must be a ctypes.c_int counter")
    if cancel_value.value:
        raise NativeCoreError("Native Quick SDF threshold generation was cancelled")
    if not native_packed_lane_available():
        result = fallback(
            transitions, angle_values, out=destination, channel=channel_offset
        )
        progress_value.value = int(angle_values.size)
        return result
    dll = _load()
    code = dll.qsdf_generate_threshold_transitions(
        transitions.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        int(angle_values.size),
        width,
        height,
        destination.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
        pixel_stride,
        channel_offset,
        ctypes.byref(cancel_value),
        ctypes.byref(progress_value),
    )
    if code == 4:
        raise NativeCoreError("Native Quick SDF threshold generation was cancelled")
    if code:
        raise NativeCoreError(
            f"Native Quick SDF transition threshold failed with status {code}"
        )
    return destination


def repair_side_monotonic(
    mask_stack, base_stack, coverage_stack, *, cancel_flag=None
):
    """Use ABI 4+ to repair an export lane, falling back to the pure core."""

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
    if native_packed_lane_available() and 2 <= arrays[0].shape[0] <= 16:
        from .core import pack_lane_bits

        count = arrays[0].shape[0]
        lane = pack_lane_bits(
            arrays[0], np.linspace(0.0, 90.0, count), arrays[1], arrays[2]
        )
        compact = repair_packed_lane(lane, cancel_flag=cancel_flag)
        repaired = (
            np.arange(count, dtype=np.uint8)[:, None, None]
            >= compact.transition_indices[None, ...]
        )
        changed = np.ascontiguousarray(repaired != arrays[0])
        return MonotonicRepairResult(
            masks=np.ascontiguousarray(repaired),
            changed_mask=changed,
            transition_indices=np.ascontiguousarray(
                compact.transition_indices, dtype=np.int32
            ),
            changed_sample_count=compact.changed_sample_count,
            changed_pixel_count=compact.changed_pixel_count,
            protected_changed_sample_count=compact.protected_changed_sample_count,
            protected_changed_pixel_count=compact.protected_changed_pixel_count,
        )
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
    if not native_threshold_available():
        raise NativeCoreError("Native Quick SDF ABI 5 validation core is not available")
    binary, angle_values = _prepare(masks, angles)
    count, height, width = binary.shape
    violations = ctypes.c_int(0)
    code = dll.qsdf_validate_monotonic(
        binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        angle_values.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
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
    "generate_threshold_transitions",
    "interpolate_binary_masks",
    "native_bake_available",
    "native_guide_bake_available",
    "native_interpolation_available",
    "native_packed_lane_available",
    "native_repair_available",
    "native_threshold_available",
    "repair_side_monotonic",
    "repair_packed_lane",
    "validate_monotonic",
    "version",
]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent RGBA16 channel packing for Quick SDF exports.

Threshold generation deliberately stops at named, canonical signal planes.
This module is the only place where those signals acquire R/G/B/A meaning.
Normalized mask inputs are quantized with round-half-up semantics; existing
``uint16`` threshold values pass through byte-for-byte.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np


UNORM16_MAX = 65535


class PackingSource(str, Enum):
    """Signals that can be assigned to one exported texture channel."""

    RIGHT_THRESHOLD = "RIGHT_THRESHOLD"
    LEFT_THRESHOLD = "LEFT_THRESHOLD"
    SDF_AREA = "SDF_AREA"
    SHADOW_STRENGTH = "SHADOW_STRENGTH"
    CUSTOM_MASK = "CUSTOM_MASK"
    CONSTANT = "CONSTANT"


@dataclass(frozen=True)
class PackingChannelSpec:
    """One output-channel assignment in a project-local packing recipe.

    ``auxiliary_mask_uuid`` identifies the selected image for mask sources.
    Standard mask sources may instead be supplied by their stable source name,
    which keeps the pure core convenient for tests and scripted use.
    """

    source: PackingSource | str
    invert: bool = False
    constant_value: float = 0.0
    auxiliary_mask_uuid: str = ""


def quantize_unorm16(values: np.ndarray | Sequence[object]) -> np.ndarray:
    """Return a contiguous 2D uint16 plane using exact UNORM16 quantization.

    Existing uint16 values are retained without conversion. Boolean values map
    to 0/65535. Other real numeric values are interpreted as normalized values,
    clamped to 0..1, then encoded as ``floor(value * 65535 + 0.5)``. NaN and
    infinity are rejected rather than silently producing platform-dependent
    integer results.
    """

    array = np.asarray(values)
    if array.ndim != 2:
        raise ValueError("packing signal planes must be two-dimensional")
    if array.dtype == np.uint16:
        return np.ascontiguousarray(array)
    if np.issubdtype(array.dtype, np.bool_):
        return np.ascontiguousarray(array.astype(np.uint16) * UNORM16_MAX)
    if not (
        np.issubdtype(array.dtype, np.integer)
        or np.issubdtype(array.dtype, np.floating)
    ):
        raise TypeError("packing signal planes must contain real numeric values")
    normalized = np.asarray(array, dtype=np.float64)
    if not np.all(np.isfinite(normalized)):
        raise ValueError("packing signal planes must contain only finite values")
    normalized = np.clip(normalized, 0.0, 1.0)
    quantized = np.floor(normalized * float(UNORM16_MAX) + 0.5)
    return np.ascontiguousarray(quantized, dtype=np.uint16)


def _coerce_source(value: PackingSource | str) -> PackingSource:
    if isinstance(value, PackingSource):
        return value
    try:
        return PackingSource(str(value).upper())
    except ValueError as error:
        raise ValueError(f"unknown packing source: {value!r}") from error


def _coerce_spec(value: PackingChannelSpec | Mapping[str, Any]) -> PackingChannelSpec:
    if isinstance(value, PackingChannelSpec):
        return value
    if isinstance(value, Mapping):
        try:
            return PackingChannelSpec(**value)
        except TypeError as error:
            raise ValueError(f"invalid packing channel specification: {value!r}") from error
    raise TypeError("packing channel specifications must be specs or mappings")


def _normalized_signal_map(
    signals: Mapping[str | PackingSource, np.ndarray | Sequence[object]],
) -> dict[str, np.ndarray | Sequence[object]]:
    if not isinstance(signals, Mapping):
        raise TypeError("signals must be a mapping of names to 2D planes")
    normalized: dict[str, np.ndarray | Sequence[object]] = {}
    for key, plane in signals.items():
        name = key.value if isinstance(key, PackingSource) else str(key)
        if name in normalized:
            raise ValueError(f"duplicate packing signal name: {name!r}")
        normalized[name] = plane
    return normalized


def _signal_name(
    source: PackingSource,
    spec: PackingChannelSpec,
) -> str:
    if source in {PackingSource.RIGHT_THRESHOLD, PackingSource.LEFT_THRESHOLD}:
        return source.value
    if source is PackingSource.CUSTOM_MASK:
        if not spec.auxiliary_mask_uuid:
            raise ValueError("CUSTOM_MASK requires an auxiliary_mask_uuid")
        return spec.auxiliary_mask_uuid
    if spec.auxiliary_mask_uuid:
        return spec.auxiliary_mask_uuid
    return source.value


def _validated_shape(shape: Sequence[int] | None) -> tuple[int, int] | None:
    if shape is None:
        return None
    if len(shape) != 2:
        raise ValueError("packing output shape must contain height and width")
    height, width = int(shape[0]), int(shape[1])
    if height <= 0 or width <= 0:
        raise ValueError("packing output dimensions must be positive")
    return height, width


def _quantize_constant(value: object) -> np.uint16:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"packing constant must be a real number, got {value!r}") from error
    if not np.isfinite(number):
        raise ValueError("packing constant must be finite")
    number = min(1.0, max(0.0, number))
    return np.uint16(np.floor(number * float(UNORM16_MAX) + 0.5))


def _validated_signal_plane(values: np.ndarray | Sequence[object]) -> np.ndarray:
    """Validate a signal without creating a full-size quantized copy."""

    array = np.asarray(values)
    if array.ndim != 2:
        raise ValueError("packing signal planes must be two-dimensional")
    if not (
        np.issubdtype(array.dtype, np.bool_)
        or np.issubdtype(array.dtype, np.integer)
        or np.issubdtype(array.dtype, np.floating)
    ):
        raise TypeError("packing signal planes must contain real numeric values")
    if array.dtype != np.uint16 and not np.issubdtype(array.dtype, np.bool_):
        # Validate a row at a time so a 4K float mask does not create another
        # full-size temporary solely for np.isfinite.
        for row in array:
            if not np.all(np.isfinite(row)):
                raise ValueError("packing signal planes must contain only finite values")
    return array


def _write_quantized_plane(destination: np.ndarray, source: np.ndarray) -> None:
    if source.dtype == np.uint16:
        destination[...] = source
        return
    if np.issubdtype(source.dtype, np.bool_):
        destination[...] = source
        np.multiply(destination, UNORM16_MAX, out=destination)
        return
    # Quantize scanline-by-scanline.  This bounds temporary memory to O(width)
    # while retaining the public round-half-up behavior exactly.
    for row_index, row in enumerate(source):
        normalized = np.asarray(row, dtype=np.float64)
        normalized = np.clip(normalized, 0.0, 1.0)
        destination[row_index] = np.floor(
            normalized * float(UNORM16_MAX) + 0.5
        ).astype(np.uint16)


def pack_rgba16(
    signals: Mapping[str | PackingSource, np.ndarray | Sequence[object]],
    channel_specs: Sequence[PackingChannelSpec | Mapping[str, Any]],
    *,
    shape: Sequence[int] | None = None,
    out: np.ndarray | None = None,
) -> np.ndarray:
    """Pack four channel specifications into a contiguous RGBA16 image.

    Signal planes must share one ``(height, width)`` shape. A ``uint16`` plane
    is lossless; normalized boolean, integer, or floating planes are quantized
    through :func:`quantize_unorm16`. Constants are also normalized and
    clamped. Inversion is evaluated after quantization as ``65535 - value``.

    ``shape`` is only needed when every output is a constant and no signal is
    supplied from which the image dimensions can be inferred.  ``out`` may be
    a writable C-contiguous ``HxWx4 uint16`` image; it is filled in-place so a
    native threshold generator can write R/G into the final export allocation
    without allocating another RGBA image.
    """

    if len(channel_specs) != 4:
        raise ValueError("an RGBA packing recipe must contain exactly four channels")
    specs = tuple(_coerce_spec(value) for value in channel_specs)
    for spec in specs:
        if not isinstance(spec.invert, (bool, np.bool_)):
            raise ValueError("packing channel invert must be a boolean")
        if not isinstance(spec.auxiliary_mask_uuid, str):
            raise ValueError("packing auxiliary_mask_uuid must be a string")
    source_values = tuple(_coerce_source(spec.source) for spec in specs)
    normalized_signals = _normalized_signal_map(signals)
    output_shape = _validated_shape(shape)
    output: np.ndarray | None = None
    if out is not None:
        output = np.asarray(out)
        if output.ndim != 3 or output.shape[2] != 4:
            raise ValueError("packing output must have shape HxWx4")
        if output.dtype != np.uint16:
            raise TypeError("packing output must use uint16")
        if not output.flags.writeable or not output.flags.c_contiguous:
            raise ValueError("packing output must be writeable and C-contiguous")
        out_shape = (int(output.shape[0]), int(output.shape[1]))
        if output_shape is not None and output_shape != out_shape:
            raise ValueError(
                f"packing output has shape {out_shape}, expected {output_shape}"
            )
        output_shape = out_shape
    resolved: list[np.ndarray | np.uint16] = []

    for source, spec in zip(source_values, specs):
        if source is PackingSource.CONSTANT:
            resolved.append(_quantize_constant(spec.constant_value))
            continue
        name = _signal_name(source, spec)
        try:
            raw_plane = normalized_signals[name]
        except KeyError as error:
            raise ValueError(
                f"packing source {source.value!r} references missing signal {name!r}"
            ) from error
        plane = _validated_signal_plane(raw_plane)
        if output_shape is None:
            output_shape = plane.shape
        elif plane.shape != output_shape:
            raise ValueError(
                f"packing signal {name!r} has shape {plane.shape}, expected {output_shape}"
            )
        resolved.append(plane)

    if output_shape is None and normalized_signals:
        # All output channels are constants, but callers may still provide a
        # canonical threshold plane solely to define the export dimensions.
        first_name, first_plane = next(iter(normalized_signals.items()))
        output_shape = _validated_signal_plane(first_plane).shape
    if output_shape is None:
        raise ValueError("packing output shape cannot be inferred from constants alone")

    if output is None:
        output = np.empty((*output_shape, 4), dtype=np.uint16)
    else:
        # Preserve arbitrary channel permutations when canonical native signals
        # are views into ``out`` itself.  A source already in its destination
        # channel is safe and remains zero-copy; cross-channel dependencies are
        # snapshotted before the first write.
        for index, value in enumerate(resolved):
            if not isinstance(value, np.ndarray) or not np.shares_memory(value, output):
                continue
            target = output[..., index]
            exact_target = (
                value.shape == target.shape
                and value.dtype == target.dtype
                and value.ctypes.data == target.ctypes.data
                and value.strides == target.strides
            )
            if not exact_target:
                resolved[index] = np.ascontiguousarray(value)
    for index, (value, spec) in enumerate(zip(resolved, specs)):
        if isinstance(value, np.ndarray):
            if value.shape != output_shape:
                raise ValueError(
                    f"packing channel {index} has shape {value.shape}, expected {output_shape}"
                )
            _write_quantized_plane(output[..., index], value)
        else:
            output[..., index] = value
        if bool(spec.invert):
            np.subtract(UNORM16_MAX, output[..., index], out=output[..., index])
    return output


__all__ = [
    "PackingChannelSpec",
    "PackingSource",
    "UNORM16_MAX",
    "pack_rgba16",
    "quantize_unorm16",
]

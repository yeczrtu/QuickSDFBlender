# SPDX-License-Identifier: GPL-3.0-or-later
"""Compressed, Blender-independent undo history for texture changes.

Blender's native undo does not reliably group edits to several ``Image``
datablocks into one operation. :class:`History` is the fallback used by Quick
SDF: it stores only the changed pixel indices and values and compresses them
with zlib. It accepts the RGBA arrays used by Blender as well as the 2D
``uint8`` and boolean planes used by Quick SDF's compact storage.

The class never mutates arrays supplied by the caller. ``undo`` and ``redo``
retain their original dictionary-returning API. Callers that also need to undo
or redo structure (for example, an automatically created angle key) can attach
portable metadata to ``push`` and use ``undo_action`` / ``redo_action``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import pickle
from typing import TypeAlias
import zlib

import numpy as np


DEFAULT_BYTE_BUDGET = 256 * 1024 * 1024

MetadataScalar: TypeAlias = None | bool | int | float | str | bytes
MetadataValue: TypeAlias = (
    MetadataScalar
    | list["MetadataValue"]
    | tuple["MetadataValue", ...]
    | dict[str, "MetadataValue"]
)
Metadata: TypeAlias = dict[str, MetadataValue]


@dataclass(frozen=True, slots=True)
class _ArrayDelta:
    shape: tuple[int, ...]
    dtype: str
    index_dtype: str
    count: int
    indices: bytes
    before: bytes
    after: bytes

    @property
    def storage_bytes(self) -> int:
        return len(self.indices) + len(self.before) + len(self.after)


@dataclass(frozen=True, slots=True)
class _Entry:
    label: str
    images: dict[str, _ArrayDelta]
    metadata: bytes | None

    @property
    def storage_bytes(self) -> int:
        metadata_bytes = len(self.metadata) if self.metadata is not None else 0
        return metadata_bytes + sum(delta.storage_bytes for delta in self.images.values())


@dataclass(frozen=True, slots=True)
class HistoryActionResult:
    """Result of an undo/redo operation including caller-managed structure.

    ``images`` contains replacement arrays for touched keys. ``metadata`` is a
    fresh copy of the value supplied to :meth:`History.push`, so mutating it
    cannot affect the retained undo/redo entry.
    """

    label: str
    images: dict[str, np.ndarray]
    metadata: Metadata | None


def _validate_array(value: object, *, name: str) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise TypeError(f"{name} must be a NumPy array")
    if value.ndim == 2:
        if value.shape[0] <= 0 or value.shape[1] <= 0:
            raise ValueError(f"{name} dimensions must be non-zero")
        if value.dtype not in (np.dtype(np.bool_), np.dtype(np.uint8)):
            raise TypeError(f"{name} 2D plane must use bool or uint8 dtype")
        return value

    if value.ndim != 3 or value.shape[2] != 4:
        raise ValueError(
            f"{name} must have shape (height, width) or (height, width, 4), got {value.shape}"
        )
    if value.shape[0] <= 0 or value.shape[1] <= 0:
        raise ValueError(f"{name} dimensions must be non-zero")
    if value.dtype.hasobject or value.dtype.fields is not None:
        raise TypeError(f"{name} must use a plain, non-object dtype")
    if value.dtype.kind not in "buifc":
        raise TypeError(f"{name} must use a boolean or numeric dtype")
    return value


def _validate_images(
    before: Mapping[str, np.ndarray], after: Mapping[str, np.ndarray]
) -> tuple[tuple[str, np.ndarray, np.ndarray], ...]:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise TypeError("before and after must be mappings of image keys to arrays")
    if before.keys() != after.keys():
        raise ValueError("before and after must contain exactly the same image keys")

    validated: list[tuple[str, np.ndarray, np.ndarray]] = []
    for key in before:
        if not isinstance(key, str) or not key:
            raise TypeError("image keys must be non-empty strings")
        first = _validate_array(before[key], name=f"before[{key!r}]")
        second = _validate_array(after[key], name=f"after[{key!r}]")
        if first.shape != second.shape:
            raise ValueError(f"image {key!r} changed shape from {first.shape} to {second.shape}")
        if first.dtype != second.dtype:
            raise TypeError(f"image {key!r} changed dtype from {first.dtype} to {second.dtype}")
        validated.append((key, first, second))
    return tuple(validated)


def _components_per_pixel(array: np.ndarray) -> int:
    return 1 if array.ndim == 2 else 4


def _changed_pixel_mask(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Compare exact pixel representations, including NaN payloads and signed zero."""

    first = np.ascontiguousarray(before)
    second = np.ascontiguousarray(after)
    bytes_per_pixel = first.dtype.itemsize * _components_per_pixel(first)
    first_bytes = first.view(np.uint8).reshape(first.shape[:2] + (bytes_per_pixel,))
    second_bytes = second.view(np.uint8).reshape(second.shape[:2] + (bytes_per_pixel,))
    return np.any(first_bytes != second_bytes, axis=2)


def _make_delta(
    before: np.ndarray, after: np.ndarray, *, compression_level: int
) -> _ArrayDelta | None:
    changed = _changed_pixel_mask(before, after)
    if not np.any(changed):
        return None

    flat_indices = np.flatnonzero(changed.reshape(-1))
    index_dtype = np.dtype("<u4" if changed.size <= np.iinfo(np.uint32).max else "<u8")
    packed_indices = np.ascontiguousarray(flat_indices, dtype=index_dtype)
    components = _components_per_pixel(before)
    first = np.ascontiguousarray(before).reshape(-1, components)
    second = np.ascontiguousarray(after).reshape(-1, components)
    first_values = np.ascontiguousarray(first[flat_indices])
    second_values = np.ascontiguousarray(second[flat_indices])
    return _ArrayDelta(
        shape=tuple(int(dimension) for dimension in before.shape),
        dtype=before.dtype.str,
        index_dtype=index_dtype.str,
        count=int(flat_indices.size),
        indices=zlib.compress(packed_indices.tobytes(order="C"), compression_level),
        before=zlib.compress(first_values.tobytes(order="C"), compression_level),
        after=zlib.compress(second_values.tobytes(order="C"), compression_level),
    )


def _restore_patch(current: np.ndarray, delta: _ArrayDelta, payload: bytes) -> np.ndarray:
    array = _validate_array(current, name="current image")
    if tuple(array.shape) != delta.shape:
        raise ValueError(f"current image shape {array.shape} does not match history {delta.shape}")
    if array.dtype.str != delta.dtype:
        raise TypeError(f"current image dtype {array.dtype} does not match history {delta.dtype}")

    try:
        raw_indices = zlib.decompress(delta.indices)
        raw = zlib.decompress(payload)
    except zlib.error as error:
        raise RuntimeError("corrupt compressed image history") from error
    index_dtype = np.dtype(delta.index_dtype)
    components = 1 if len(delta.shape) == 2 else 4
    expected_index_bytes = delta.count * index_dtype.itemsize
    expected_bytes = delta.count * components * array.dtype.itemsize
    if len(raw_indices) != expected_index_bytes or len(raw) != expected_bytes:
        raise RuntimeError("corrupt image history payload size")

    indices = np.frombuffer(raw_indices, dtype=index_dtype)
    if np.any(indices >= array.shape[0] * array.shape[1]):
        raise RuntimeError("corrupt image history pixel index")
    values = np.frombuffer(raw, dtype=np.dtype(delta.dtype)).reshape(delta.count, components)
    restored = np.array(array, copy=True, order="C")
    restored.reshape(-1, components)[indices] = values
    return restored


def _normalize_metadata(value: object, *, path: str, ancestors: set[int]) -> MetadataValue:
    if value is None or isinstance(value, (bool, int, str, bytes)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise ValueError(f"{path} must not contain a reference cycle")
        ancestors.add(identity)
        try:
            normalized: Metadata = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path} mapping keys must be strings")
                normalized[key] = _normalize_metadata(
                    child, path=f"{path}[{key!r}]", ancestors=ancestors
                )
            return normalized
        finally:
            ancestors.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in ancestors:
            raise ValueError(f"{path} must not contain a reference cycle")
        ancestors.add(identity)
        try:
            items = tuple(
                _normalize_metadata(child, path=f"{path}[{index}]", ancestors=ancestors)
                for index, child in enumerate(value)
            )
            return list(items) if isinstance(value, list) else items
        finally:
            ancestors.remove(identity)
    raise TypeError(
        f"{path} contains unsupported {type(value).__name__}; "
        "use None, bool, int, finite float, str, bytes, list, tuple, or mapping"
    )


def _pack_metadata(metadata: Mapping[str, object] | None) -> bytes | None:
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping or None")
    normalized = _normalize_metadata(metadata, path="metadata", ancestors=set())
    assert isinstance(normalized, dict)
    return pickle.dumps(normalized, protocol=5)


def _unpack_metadata(payload: bytes | None) -> Metadata | None:
    if payload is None:
        return None
    value = pickle.loads(payload)
    if not isinstance(value, dict):  # Defensive: entries are produced internally.
        raise RuntimeError("corrupt history metadata")
    return value


class History:
    """A bounded stack of compressed, multi-plane pixel actions.

    ``push`` returns ``True`` when an action was recorded. It returns ``False``
    for a no-op or when the action alone is larger than ``byte_budget``.
    ``undo`` and ``redo`` preserve the legacy image-dictionary API; use their
    ``*_action`` counterparts to also receive structural metadata.
    """

    def __init__(
        self,
        byte_budget: int = DEFAULT_BYTE_BUDGET,
        *,
        compression_level: int = 6,
    ) -> None:
        if isinstance(byte_budget, bool) or not isinstance(byte_budget, int):
            raise TypeError("byte_budget must be an integer")
        if byte_budget < 0:
            raise ValueError("byte_budget must not be negative")
        if isinstance(compression_level, bool) or not isinstance(compression_level, int):
            raise TypeError("compression_level must be an integer")
        if not 0 <= compression_level <= 9:
            raise ValueError("compression_level must be in the range 0..9")
        self.byte_budget = byte_budget
        self.compression_level = compression_level
        self._undo: list[_Entry] = []
        self._redo: list[_Entry] = []
        self._bytes_used = 0

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)

    @property
    def bytes_used(self) -> int:
        """Compressed payload bytes retained across both stacks."""

        return self._bytes_used

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    @property
    def undo_keys(self) -> tuple[str, ...]:
        return tuple(self._undo[-1].images) if self._undo else ()

    @property
    def redo_keys(self) -> tuple[str, ...]:
        return tuple(self._redo[-1].images) if self._redo else ()

    @property
    def undo_metadata(self) -> Metadata | None:
        return _unpack_metadata(self._undo[-1].metadata) if self._undo else None

    @property
    def redo_metadata(self) -> Metadata | None:
        return _unpack_metadata(self._redo[-1].metadata) if self._redo else None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._bytes_used = 0

    def push(
        self,
        label: str,
        before: Mapping[str, np.ndarray],
        after: Mapping[str, np.ndarray],
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        if not isinstance(label, str):
            raise TypeError("label must be a string")

        packed_metadata = _pack_metadata(metadata)
        images: dict[str, _ArrayDelta] = {}
        for key, first, second in _validate_images(before, after):
            delta = _make_delta(first, second, compression_level=self.compression_level)
            if delta is not None:
                images[key] = delta
        if not images and metadata is None:
            return False

        # A newly performed operation always invalidates the redo branch, even
        # if its compressed representation cannot fit into the configured cap.
        self._bytes_used -= sum(entry.storage_bytes for entry in self._redo)
        self._redo.clear()
        entry = _Entry(label=label, images=images, metadata=packed_metadata)
        if entry.storage_bytes > self.byte_budget:
            return False

        self._undo.append(entry)
        self._bytes_used += entry.storage_bytes
        while self._bytes_used > self.byte_budget and self._undo:
            removed = self._undo.pop(0)
            self._bytes_used -= removed.storage_bytes
        return True

    def _apply_action(
        self,
        source: list[_Entry],
        destination: list[_Entry],
        current: Mapping[str, np.ndarray],
        *,
        use_after: bool,
    ) -> HistoryActionResult | None:
        if not source:
            return None
        if not isinstance(current, Mapping):
            raise TypeError("current must be a mapping of image keys to arrays")

        entry = source[-1]
        missing = entry.images.keys() - current.keys()
        if missing:
            formatted = ", ".join(repr(key) for key in sorted(missing))
            raise KeyError(f"current images are missing history keys: {formatted}")

        # Build every result before moving the entry. A bad array therefore
        # leaves both stacks untouched and the action can be retried.
        restored: dict[str, np.ndarray] = {}
        for key, delta in entry.images.items():
            payload = delta.after if use_after else delta.before
            restored[key] = _restore_patch(current[key], delta, payload)
        metadata = _unpack_metadata(entry.metadata)
        source.pop()
        destination.append(entry)
        return HistoryActionResult(label=entry.label, images=restored, metadata=metadata)

    def undo_action(self, current: Mapping[str, np.ndarray]) -> HistoryActionResult | None:
        """Undo the newest action and return pixels plus structural metadata."""

        return self._apply_action(self._undo, self._redo, current, use_after=False)

    def redo_action(self, current: Mapping[str, np.ndarray]) -> HistoryActionResult | None:
        """Redo the next action and return pixels plus structural metadata."""

        return self._apply_action(self._redo, self._undo, current, use_after=True)

    def undo(self, current: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Return affected images with the newest action's pixels restored."""

        result = self.undo_action(current)
        return result.images if result is not None else {}

    def redo(self, current: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Return affected images with the next action's pixels reapplied."""

        result = self.redo_action(current)
        return result.images if result is not None else {}


__all__ = [
    "DEFAULT_BYTE_BUDGET",
    "History",
    "HistoryActionResult",
    "Metadata",
    "MetadataValue",
]

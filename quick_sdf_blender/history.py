# SPDX-License-Identifier: GPL-3.0-or-later
"""Compressed, Blender-independent undo history for image pixel changes.

Blender's native undo does not reliably group edits to several ``Image``
datablocks into one operation.  :class:`History` is the fallback used by Quick
SDF: it stores only the smallest changed rectangle for each RGBA NumPy array
and compresses both sides of that rectangle with zlib.

The class never mutates arrays supplied by the caller.  ``undo`` and ``redo``
return full replacement arrays for the images affected by the action; image
keys not touched by the action are omitted from the returned dictionary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import zlib

import numpy as np


DEFAULT_BYTE_BUDGET = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ImageDelta:
    shape: tuple[int, int, int]
    dtype: str
    bbox: tuple[int, int, int, int]
    before: bytes
    after: bytes

    @property
    def storage_bytes(self) -> int:
        return len(self.before) + len(self.after)


@dataclass(frozen=True, slots=True)
class _Entry:
    label: str
    images: dict[str, _ImageDelta]

    @property
    def storage_bytes(self) -> int:
        return sum(delta.storage_bytes for delta in self.images.values())


def _validate_rgba_array(value: object, *, name: str) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise TypeError(f"{name} must be a NumPy array")
    if value.ndim != 3 or value.shape[2] != 4:
        raise ValueError(f"{name} must have shape (height, width, 4), got {value.shape}")
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
        first = _validate_rgba_array(before[key], name=f"before[{key!r}]")
        second = _validate_rgba_array(after[key], name=f"after[{key!r}]")
        if first.shape != second.shape:
            raise ValueError(f"image {key!r} changed shape from {first.shape} to {second.shape}")
        if first.dtype != second.dtype:
            raise TypeError(f"image {key!r} changed dtype from {first.dtype} to {second.dtype}")
        validated.append((key, first, second))
    return tuple(validated)


def _changed_pixel_mask(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Compare exact pixel representations, including NaN payloads and signed zero."""

    first = np.ascontiguousarray(before)
    second = np.ascontiguousarray(after)
    bytes_per_pixel = first.dtype.itemsize * 4
    first_bytes = first.view(np.uint8).reshape(first.shape[:2] + (bytes_per_pixel,))
    second_bytes = second.view(np.uint8).reshape(second.shape[:2] + (bytes_per_pixel,))
    return np.any(first_bytes != second_bytes, axis=2)


def _make_delta(
    before: np.ndarray, after: np.ndarray, *, compression_level: int
) -> _ImageDelta | None:
    changed = _changed_pixel_mask(before, after)
    if not np.any(changed):
        return None

    ys, xs = np.nonzero(changed)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    first_patch = np.ascontiguousarray(before[y0:y1, x0:x1, :])
    second_patch = np.ascontiguousarray(after[y0:y1, x0:x1, :])
    return _ImageDelta(
        shape=(int(before.shape[0]), int(before.shape[1]), 4),
        dtype=before.dtype.str,
        bbox=(y0, y1, x0, x1),
        before=zlib.compress(first_patch.tobytes(order="C"), compression_level),
        after=zlib.compress(second_patch.tobytes(order="C"), compression_level),
    )


def _restore_patch(current: np.ndarray, delta: _ImageDelta, payload: bytes) -> np.ndarray:
    array = _validate_rgba_array(current, name="current image")
    if tuple(array.shape) != delta.shape:
        raise ValueError(f"current image shape {array.shape} does not match history {delta.shape}")
    if array.dtype.str != delta.dtype:
        raise TypeError(f"current image dtype {array.dtype} does not match history {delta.dtype}")

    y0, y1, x0, x1 = delta.bbox
    patch_shape = (y1 - y0, x1 - x0, 4)
    expected_bytes = int(np.prod(patch_shape, dtype=np.int64)) * array.dtype.itemsize
    try:
        raw = zlib.decompress(payload)
    except zlib.error as error:
        raise RuntimeError("corrupt compressed image history") from error
    if len(raw) != expected_bytes:
        raise RuntimeError("corrupt image history payload size")

    patch = np.frombuffer(raw, dtype=np.dtype(delta.dtype)).reshape(patch_shape)
    restored = np.array(array, copy=True, order="K")
    restored[y0:y1, x0:x1, :] = patch
    return restored


class History:
    """A bounded stack of compressed, multi-image pixel actions.

    ``push`` returns ``True`` when an action was recorded.  It returns ``False``
    for a no-op or when the action alone is larger than ``byte_budget``.
    ``undo`` and ``redo`` return an empty dictionary when their stack is empty.
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

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._bytes_used = 0

    def push(
        self,
        label: str,
        before: dict[str, np.ndarray],
        after: dict[str, np.ndarray],
    ) -> bool:
        if not isinstance(label, str):
            raise TypeError("label must be a string")

        images: dict[str, _ImageDelta] = {}
        for key, first, second in _validate_images(before, after):
            delta = _make_delta(first, second, compression_level=self.compression_level)
            if delta is not None:
                images[key] = delta
        if not images:
            return False

        # A newly performed operation always invalidates the redo branch, even
        # if its compressed representation cannot fit into the configured cap.
        self._bytes_used -= sum(entry.storage_bytes for entry in self._redo)
        self._redo.clear()
        entry = _Entry(label=label, images=images)
        if entry.storage_bytes > self.byte_budget:
            return False

        self._undo.append(entry)
        self._bytes_used += entry.storage_bytes
        while self._bytes_used > self.byte_budget and self._undo:
            removed = self._undo.pop(0)
            self._bytes_used -= removed.storage_bytes
        return True

    def _apply(
        self,
        source: list[_Entry],
        destination: list[_Entry],
        current: dict[str, np.ndarray],
        *,
        use_after: bool,
    ) -> dict[str, np.ndarray]:
        if not source:
            return {}
        if not isinstance(current, Mapping):
            raise TypeError("current must be a mapping of image keys to arrays")

        entry = source[-1]
        missing = entry.images.keys() - current.keys()
        if missing:
            formatted = ", ".join(repr(key) for key in sorted(missing))
            raise KeyError(f"current images are missing history keys: {formatted}")

        # Build every result before moving the entry.  A bad array therefore
        # leaves both stacks untouched and the action can be retried.
        restored: dict[str, np.ndarray] = {}
        for key, delta in entry.images.items():
            payload = delta.after if use_after else delta.before
            restored[key] = _restore_patch(current[key], delta, payload)
        source.pop()
        destination.append(entry)
        return restored

    def undo(self, current: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Return affected images with the newest action's rectangles restored."""

        return self._apply(self._undo, self._redo, current, use_after=False)

    def redo(self, current: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """Return affected images with the next action's rectangles reapplied."""

        return self._apply(self._redo, self._undo, current, use_after=True)


__all__ = ["DEFAULT_BYTE_BUDGET", "History"]

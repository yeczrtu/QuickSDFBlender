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


# ``byte_budget`` remains the compatibility name for the per-action hard cap.
# The retained undo stack has a lower soft target: when it is exceeded older
# actions are evicted, but the newest valid action remains available.
DEFAULT_BYTE_BUDGET = 256 * 1024 * 1024
DEFAULT_SOFT_BYTE_BUDGET = 128 * 1024 * 1024
HISTORY_TILE_SIZE = 64
_TILE_BITMAP_BYTES = HISTORY_TILE_SIZE * HISTORY_TILE_SIZE // 8

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
    locator_kind: str
    locator_dtype: str
    locator_count: int
    count: int
    locator: bytes
    value_kind: str
    before: bytes
    after: bytes

    @property
    def storage_bytes(self) -> int:
        return len(self.locator) + len(self.before) + len(self.after)


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


def _tile_bitmap_locator(
    changed: np.ndarray,
    *,
    compression_level: int,
) -> tuple[str, int, bytes]:
    """Encode touched 64x64 tiles without retaining a full-image bitmap."""

    height, width = (int(changed.shape[0]), int(changed.shape[1]))
    tiles_x = (width + HISTORY_TILE_SIZE - 1) // HISTORY_TILE_SIZE
    tiles_y = (height + HISTORY_TILE_SIZE - 1) // HISTORY_TILE_SIZE
    tile_count = tiles_x * tiles_y
    tile_dtype = np.dtype("<u4" if tile_count <= np.iinfo(np.uint32).max else "<u8")
    identifiers: list[int] = []
    bitmaps = bytearray()
    for tile_y in range(tiles_y):
        y0 = tile_y * HISTORY_TILE_SIZE
        y1 = min(height, y0 + HISTORY_TILE_SIZE)
        for tile_x in range(tiles_x):
            x0 = tile_x * HISTORY_TILE_SIZE
            x1 = min(width, x0 + HISTORY_TILE_SIZE)
            tile = changed[y0:y1, x0:x1]
            local_flat = np.flatnonzero(tile.reshape(-1))
            if local_flat.size == 0:
                continue
            local_y, local_x = np.divmod(local_flat, tile.shape[1])
            local_indices = local_y * HISTORY_TILE_SIZE + local_x
            bitmap = np.zeros(_TILE_BITMAP_BYTES, dtype=np.uint8)
            np.bitwise_or.at(
                bitmap,
                local_indices // 8,
                np.left_shift(
                    np.uint8(1),
                    np.asarray(local_indices % 8, dtype=np.uint8),
                ),
            )
            identifiers.append(tile_y * tiles_x + tile_x)
            bitmaps.extend(bitmap.tobytes(order="C"))

    ids = np.asarray(identifiers, dtype=tile_dtype)
    raw = ids.tobytes(order="C") + bytes(bitmaps)
    return tile_dtype.str, len(identifiers), zlib.compress(raw, compression_level)


def _pack_changed_values(values: np.ndarray, *, compression_level: int) -> tuple[str, bytes]:
    contiguous = np.ascontiguousarray(values)
    if contiguous.dtype == np.bool_:
        raw = np.packbits(contiguous.reshape(-1), bitorder="little").tobytes(order="C")
        return "bits", zlib.compress(raw, compression_level)
    return "raw", zlib.compress(contiguous.tobytes(order="C"), compression_level)


def _make_delta(
    before: np.ndarray, after: np.ndarray, *, compression_level: int
) -> _ArrayDelta | None:
    changed = _changed_pixel_mask(before, after)
    if not np.any(changed):
        return None

    flat_indices = np.flatnonzero(changed.reshape(-1))
    index_dtype = np.dtype("<u4" if changed.size <= np.iinfo(np.uint32).max else "<u8")
    packed_indices = np.ascontiguousarray(flat_indices, dtype=index_dtype)
    sparse_locator = zlib.compress(packed_indices.tobytes(order="C"), compression_level)
    tile_dtype, tile_count, tile_locator = _tile_bitmap_locator(
        changed,
        compression_level=compression_level,
    )
    if len(tile_locator) < len(sparse_locator):
        locator_kind = "tiles64"
        locator_dtype = tile_dtype
        locator_count = tile_count
        locator = tile_locator
    else:
        locator_kind = "sparse"
        locator_dtype = index_dtype.str
        locator_count = int(flat_indices.size)
        locator = sparse_locator
    components = _components_per_pixel(before)
    first = np.ascontiguousarray(before).reshape(-1, components)
    second = np.ascontiguousarray(after).reshape(-1, components)
    first_values = np.ascontiguousarray(first[flat_indices])
    second_values = np.ascontiguousarray(second[flat_indices])
    value_kind, packed_before = _pack_changed_values(
        first_values,
        compression_level=compression_level,
    )
    second_kind, packed_after = _pack_changed_values(
        second_values,
        compression_level=compression_level,
    )
    assert second_kind == value_kind
    return _ArrayDelta(
        shape=tuple(int(dimension) for dimension in before.shape),
        dtype=before.dtype.str,
        locator_kind=locator_kind,
        locator_dtype=locator_dtype,
        locator_count=locator_count,
        count=int(flat_indices.size),
        locator=locator,
        value_kind=value_kind,
        before=packed_before,
        after=packed_after,
    )


def _restore_indices(delta: _ArrayDelta) -> np.ndarray:
    try:
        raw_locator = zlib.decompress(delta.locator)
    except zlib.error as error:
        raise RuntimeError("corrupt compressed image history") from error
    index_dtype = np.dtype(delta.locator_dtype)
    pixel_count = delta.shape[0] * delta.shape[1]
    if delta.locator_kind == "sparse":
        expected = delta.count * index_dtype.itemsize
        if delta.locator_count != delta.count or len(raw_locator) != expected:
            raise RuntimeError("corrupt image history locator size")
        indices = np.frombuffer(raw_locator, dtype=index_dtype).astype(np.uint64, copy=False)
    elif delta.locator_kind == "tiles64":
        expected = delta.locator_count * (index_dtype.itemsize + _TILE_BITMAP_BYTES)
        if len(raw_locator) != expected:
            raise RuntimeError("corrupt image history locator size")
        ids_bytes = delta.locator_count * index_dtype.itemsize
        tile_ids = np.frombuffer(raw_locator[:ids_bytes], dtype=index_dtype)
        bitmap_rows = np.frombuffer(raw_locator[ids_bytes:], dtype=np.uint8).reshape(
            delta.locator_count, _TILE_BITMAP_BYTES
        )
        height, width = delta.shape[:2]
        tiles_x = (width + HISTORY_TILE_SIZE - 1) // HISTORY_TILE_SIZE
        tiles_y = (height + HISTORY_TILE_SIZE - 1) // HISTORY_TILE_SIZE
        if np.any(tile_ids >= tiles_x * tiles_y) or (
            tile_ids.size > 1 and np.any(tile_ids[1:] <= tile_ids[:-1])
        ):
            raise RuntimeError("corrupt image history tile identifier")
        indices = np.empty(delta.count, dtype=np.uint64)
        cursor = 0
        for tile_id, bitmap in zip(tile_ids, bitmap_rows, strict=True):
            local = np.flatnonzero(
                np.unpackbits(bitmap, count=HISTORY_TILE_SIZE**2, bitorder="little")
            )
            local_y, local_x = np.divmod(local, HISTORY_TILE_SIZE)
            tile_y, tile_x = divmod(int(tile_id), tiles_x)
            global_y = tile_y * HISTORY_TILE_SIZE + local_y
            global_x = tile_x * HISTORY_TILE_SIZE + local_x
            if np.any(global_y >= height) or np.any(global_x >= width):
                raise RuntimeError("corrupt image history tile padding")
            end = cursor + local.size
            if end > delta.count:
                raise RuntimeError("corrupt image history changed-pixel count")
            indices[cursor:end] = global_y.astype(np.uint64) * width + global_x
            cursor = end
        if cursor != delta.count:
            raise RuntimeError("corrupt image history changed-pixel count")
        # Pixel values are stored in global row-major order for both locator
        # encodings, so normalize tile traversal to that order in-place.
        indices.sort()
    else:
        raise RuntimeError("corrupt image history locator encoding")

    if np.any(indices >= pixel_count) or (
        indices.size > 1 and np.any(indices[1:] <= indices[:-1])
    ):
        raise RuntimeError("corrupt image history pixel index")
    return indices


def _restore_values(delta: _ArrayDelta, payload: bytes, components: int) -> np.ndarray:
    try:
        raw = zlib.decompress(payload)
    except zlib.error as error:
        raise RuntimeError("corrupt compressed image history") from error
    value_count = delta.count * components
    dtype = np.dtype(delta.dtype)
    if delta.value_kind == "bits":
        expected = (value_count + 7) // 8
        if dtype != np.bool_ or len(raw) != expected:
            raise RuntimeError("corrupt image history payload size")
        return np.unpackbits(
            np.frombuffer(raw, dtype=np.uint8),
            count=value_count,
            bitorder="little",
        ).astype(np.bool_, copy=False).reshape(delta.count, components)
    if delta.value_kind != "raw":
        raise RuntimeError("corrupt image history value encoding")
    expected = value_count * dtype.itemsize
    if len(raw) != expected:
        raise RuntimeError("corrupt image history payload size")
    return np.frombuffer(raw, dtype=dtype).reshape(delta.count, components)


def _restore_patch(current: np.ndarray, delta: _ArrayDelta, payload: bytes) -> np.ndarray:
    array = _validate_array(current, name="current image")
    if tuple(array.shape) != delta.shape:
        raise ValueError(f"current image shape {array.shape} does not match history {delta.shape}")
    if array.dtype.str != delta.dtype:
        raise TypeError(f"current image dtype {array.dtype} does not match history {delta.dtype}")

    components = 1 if len(delta.shape) == 2 else 4
    indices = _restore_indices(delta)
    values = _restore_values(delta, payload, components)
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


class HistoryTransaction:
    """Incrementally compressed multi-image history action.

    Callers may release each ``before``/``after`` array immediately after
    :meth:`add_delta`.  If :attr:`needs_rollback` becomes true, call
    :meth:`rollback` with the current arrays (or restore individual keys with
    :meth:`restore_before`) before discarding the transaction.
    """

    def __init__(
        self,
        owner: "History",
        label: str,
        metadata: Mapping[str, object] | None,
    ) -> None:
        if not isinstance(label, str):
            raise TypeError("label must be a string")
        self._owner = owner
        self.label = label
        self._metadata = _pack_metadata(metadata)
        self._metadata_supplied = metadata is not None
        self._images: dict[str, _ArrayDelta] = {}
        self._closed = False

    def _require_active(self) -> None:
        if self._closed or self._owner.active_transaction is not self:
            raise RuntimeError("history transaction is no longer active")

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._images)

    @property
    def storage_bytes(self) -> int:
        metadata_bytes = len(self._metadata) if self._metadata is not None else 0
        return metadata_bytes + sum(delta.storage_bytes for delta in self._images.values())

    @property
    def needs_rollback(self) -> bool:
        """Whether the compressed action exceeds the per-action hard cap."""

        return self.storage_bytes > self._owner.byte_budget

    def add_delta(self, key: str, before: np.ndarray, after: np.ndarray) -> bool:
        """Compress one key immediately; return false for an unchanged plane."""

        self._require_active()
        if not isinstance(key, str) or not key:
            raise TypeError("image key must be a non-empty string")
        if key in self._images:
            raise ValueError(f"history transaction already contains key {key!r}")
        validated = _validate_images({key: before}, {key: after})
        _name, first, second = validated[0]
        delta = _make_delta(
            first,
            second,
            compression_level=self._owner.compression_level,
        )
        if delta is None:
            return False
        self._images[key] = delta
        return True

    def restore_before(self, key: str, current: np.ndarray) -> np.ndarray:
        """Restore one key without materializing every transaction plane."""

        self._require_active()
        try:
            delta = self._images[key]
        except KeyError as error:
            raise KeyError(f"history transaction has no key {key!r}") from error
        return _restore_patch(current, delta, delta.before)

    def commit(self) -> bool:
        """Commit the action, or return false while leaving oversize data rollbackable."""

        self._require_active()
        return self._owner._commit_transaction(self)

    def rollback(
        self,
        current: Mapping[str, np.ndarray] | None = None,
    ) -> dict[str, np.ndarray]:
        """Abort the transaction and optionally return every restored before plane."""

        self._require_active()
        restored: dict[str, np.ndarray] = {}
        if current is not None:
            if not isinstance(current, Mapping):
                raise TypeError("current must be a mapping of image keys to arrays")
            missing = self._images.keys() - current.keys()
            if missing:
                formatted = ", ".join(repr(key) for key in sorted(missing))
                raise KeyError(f"current images are missing history keys: {formatted}")
            # Validate and restore everything before closing, so a bad input
            # leaves the transaction available for a corrected retry.
            for key, delta in self._images.items():
                restored[key] = _restore_patch(current[key], delta, delta.before)
        self._owner._close_transaction(self)
        return restored


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
        soft_byte_budget: int | None = None,
        compression_level: int = 6,
    ) -> None:
        if isinstance(byte_budget, bool) or not isinstance(byte_budget, int):
            raise TypeError("byte_budget must be an integer")
        if byte_budget < 0:
            raise ValueError("byte_budget must not be negative")
        if soft_byte_budget is None:
            soft_byte_budget = min(DEFAULT_SOFT_BYTE_BUDGET, byte_budget)
        if isinstance(soft_byte_budget, bool) or not isinstance(soft_byte_budget, int):
            raise TypeError("soft_byte_budget must be an integer or None")
        if soft_byte_budget < 0:
            raise ValueError("soft_byte_budget must not be negative")
        if soft_byte_budget > byte_budget:
            raise ValueError("soft_byte_budget must not exceed byte_budget")
        if isinstance(compression_level, bool) or not isinstance(compression_level, int):
            raise TypeError("compression_level must be an integer")
        if not 0 <= compression_level <= 9:
            raise ValueError("compression_level must be in the range 0..9")
        self.byte_budget = byte_budget
        self.soft_byte_budget = soft_byte_budget
        self.compression_level = compression_level
        self._undo: list[_Entry] = []
        self._redo: list[_Entry] = []
        self._bytes_used = 0
        self._transaction: HistoryTransaction | None = None

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
    def active_transaction(self) -> HistoryTransaction | None:
        return self._transaction

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
        if self._transaction is not None:
            self._transaction._closed = True
            self._transaction = None
        self._undo.clear()
        self._redo.clear()
        self._bytes_used = 0

    def begin_transaction(
        self,
        label: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> HistoryTransaction:
        """Begin an incremental action; only one may be active per history."""

        if self._transaction is not None:
            raise RuntimeError("a history transaction is already active")
        transaction = HistoryTransaction(self, label, metadata)
        self._transaction = transaction
        return transaction

    def add_delta(self, key: str, before: np.ndarray, after: np.ndarray) -> bool:
        """Compatibility convenience for the currently active transaction."""

        if self._transaction is None:
            raise RuntimeError("no history transaction is active")
        return self._transaction.add_delta(key, before, after)

    def commit(self) -> bool:
        """Commit the active transaction."""

        if self._transaction is None:
            raise RuntimeError("no history transaction is active")
        return self._transaction.commit()

    def rollback(
        self,
        current: Mapping[str, np.ndarray] | None = None,
    ) -> dict[str, np.ndarray]:
        """Abort the active transaction, optionally restoring its before values."""

        if self._transaction is None:
            raise RuntimeError("no history transaction is active")
        return self._transaction.rollback(current)

    def _close_transaction(self, transaction: HistoryTransaction) -> None:
        if self._transaction is not transaction:
            raise RuntimeError("history transaction is no longer active")
        transaction._closed = True
        self._transaction = None

    def _discard_redo(self) -> None:
        self._bytes_used -= sum(entry.storage_bytes for entry in self._redo)
        self._redo.clear()

    def _append_entry(self, entry: _Entry, *, discard_redo_on_failure: bool) -> bool:
        if entry.storage_bytes > self.byte_budget:
            if discard_redo_on_failure:
                self._discard_redo()
            return False
        self._discard_redo()
        self._undo.append(entry)
        self._bytes_used += entry.storage_bytes
        # The newest action wins even when it is larger than the soft target.
        while self._bytes_used > self.soft_byte_budget and len(self._undo) > 1:
            removed = self._undo.pop(0)
            self._bytes_used -= removed.storage_bytes
        return True

    def _commit_transaction(self, transaction: HistoryTransaction) -> bool:
        if self._transaction is not transaction:
            raise RuntimeError("history transaction is no longer active")
        entry = _Entry(
            label=transaction.label,
            images=dict(transaction._images),
            metadata=transaction._metadata,
        )
        if not entry.images and not transaction._metadata_supplied:
            self._close_transaction(transaction)
            return False
        if entry.storage_bytes > self.byte_budget:
            # Keep compressed before values alive so the caller can rollback
            # already-published keys without retaining uncompressed snapshots.
            return False
        recorded = self._append_entry(entry, discard_redo_on_failure=False)
        assert recorded
        self._close_transaction(transaction)
        return True

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

        entry = _Entry(label=label, images=images, metadata=packed_metadata)
        # Preserve legacy push semantics: attempting a new operation clears a
        # redo branch even when its representation exceeds the hard cap.
        return self._append_entry(entry, discard_redo_on_failure=True)

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
    "DEFAULT_SOFT_BYTE_BUDGET",
    "HISTORY_TILE_SIZE",
    "History",
    "HistoryActionResult",
    "HistoryTransaction",
    "Metadata",
    "MetadataValue",
]

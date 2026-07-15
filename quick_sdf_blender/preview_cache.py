# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded derived-image caches used by the Studio timeline and previews.

The authoring Display images are the source of truth and may be several
thousand pixels wide.  UI chrome must never hand those images to Blender's GPU
texture cache: doing so makes every angle resident at full resolution.  This
module owns compact, revision-keyed binary proxies and 96 x 64 thumbnails
instead.

Only :func:`thumbnail_texture` depends on Blender's GPU API.  The resampling and
LRU implementation stay Blender-independent so they can be unit tested without
starting Blender.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any, Callable, Final, Hashable

import numpy as np


PROXY_MAXIMUM: Final = 512
THUMBNAIL_WIDTH: Final = 96
THUMBNAIL_HEIGHT: Final = 64
CPU_CACHE_BYTE_BUDGET: Final = 32 * 1024 * 1024
GPU_CACHE_BYTE_BUDGET: Final = 1 * 1024 * 1024


class ByteLRU:
    """Small thread-safe LRU whose entries have an explicit byte cost."""

    def __init__(self, byte_budget: int) -> None:
        if isinstance(byte_budget, bool) or not isinstance(byte_budget, int):
            raise TypeError("byte_budget must be an integer")
        if byte_budget < 0:
            raise ValueError("byte_budget must not be negative")
        self.byte_budget = byte_budget
        self._entries: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self._bytes_used = 0
        self._lock = RLock()

    @property
    def bytes_used(self) -> int:
        with self._lock:
            return self._bytes_used

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def put(self, key: Hashable, value: Any, byte_cost: int) -> Any:
        cost = max(0, int(byte_cost))
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes_used -= previous[1]
            if cost > self.byte_budget:
                return value
            while self._entries and self._bytes_used + cost > self.byte_budget:
                _old_key, (_old_value, old_cost) = self._entries.popitem(last=False)
                self._bytes_used -= old_cost
            self._entries[key] = (value, cost)
            self._bytes_used += cost
        return value

    def remove_where(self, predicate: Callable[[Hashable], bool]) -> None:
        with self._lock:
            stale = [key for key in self._entries if predicate(key)]
            for key in stale:
                _value, cost = self._entries.pop(key)
                self._bytes_used -= cost

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes_used = 0


_CPU_CACHE = ByteLRU(CPU_CACHE_BYTE_BUDGET)
_GPU_CACHE = ByteLRU(GPU_CACHE_BYTE_BUDGET)


def _target_shape(height: int, width: int, maximum: int) -> tuple[int, int]:
    maximum = max(1, int(maximum))
    scale = min(1.0, float(maximum) / max(height, width))
    return (
        max(1, int(round(height * scale))),
        max(1, int(round(width * scale))),
    )


def resize_nearest(values: Any, maximum: int = PROXY_MAXIMUM) -> np.ndarray:
    """Return a nearest-neighbour 2D proxy whose longest side is bounded."""

    plane = np.asarray(values)
    if plane.ndim != 2:
        raise ValueError(f"Preview planes must be two-dimensional, got {plane.shape}")
    height, width = plane.shape
    if height <= 0 or width <= 0:
        raise ValueError("Preview planes must not be empty")
    target_height, target_width = _target_shape(height, width, maximum)
    if (target_height, target_width) == (height, width):
        return np.ascontiguousarray(plane)
    ys = np.minimum(
        np.arange(target_height, dtype=np.int64) * height // target_height,
        height - 1,
    )
    xs = np.minimum(
        np.arange(target_width, dtype=np.int64) * width // target_width,
        width - 1,
    )
    return np.ascontiguousarray(plane[ys[:, None], xs[None, :]])


def max_pool(values: Any, maximum: int = PROXY_MAXIMUM) -> np.ndarray:
    """Downsample a 2D plane while retaining every non-zero/problem pixel.

    Output pixels cover integer source bins and contain the maximum of each
    bin.  This is intentionally different from thumbnail sampling: a one-pixel
    export adjustment must remain visible in the 512px review heatmap.
    """

    plane = np.asarray(values)
    if plane.ndim != 2:
        raise ValueError(f"Preview planes must be two-dimensional, got {plane.shape}")
    height, width = plane.shape
    if height <= 0 or width <= 0:
        raise ValueError("Preview planes must not be empty")
    target_height, target_width = _target_shape(height, width, maximum)
    if (target_height, target_width) == (height, width):
        return np.ascontiguousarray(plane)
    y_edges = np.arange(target_height + 1, dtype=np.int64) * height // target_height
    x_edges = np.arange(target_width + 1, dtype=np.int64) * width // target_width
    output = np.empty((target_height, target_width), dtype=plane.dtype)
    for target_y in range(target_height):
        rows = plane[y_edges[target_y] : y_edges[target_y + 1]]
        # ``maximum.reduceat`` avoids allocating one source-sized label map.
        output[target_y] = np.maximum.reduceat(rows.max(axis=0), x_edges[:-1])
    return np.ascontiguousarray(output)


def _normalized_bbox(values: Any) -> tuple[float, float, float, float]:
    try:
        u0, v0, u1, v1 = (float(value) for value in values)
    except (TypeError, ValueError):
        return (0.0, 0.0, 1.0, 1.0)
    if not (0.0 <= u0 < u1 <= 1.0 and 0.0 <= v0 < v1 <= 1.0):
        return (0.0, 0.0, 1.0, 1.0)
    return (u0, v0, u1, v1)


def thumbnail_plane(
    values: Any,
    uv_bbox: Any = (0.0, 0.0, 1.0, 1.0),
    *,
    width: int = THUMBNAIL_WIDTH,
    height: int = THUMBNAIL_HEIGHT,
) -> np.ndarray:
    """Crop a top-down plane to the UV box and make a fixed-size thumbnail."""

    plane = np.asarray(values)
    if plane.ndim != 2 or not plane.size:
        raise ValueError("Thumbnail source must be a non-empty 2D plane")
    source_height, source_width = plane.shape
    u0, v0, u1, v1 = _normalized_bbox(uv_bbox)
    # Image arrays are top-down while UV V is bottom-up.
    x0 = min(source_width - 1, int(np.floor(u0 * source_width)))
    x1 = max(x0 + 1, min(source_width, int(np.ceil(u1 * source_width))))
    y0 = min(source_height - 1, int(np.floor((1.0 - v1) * source_height)))
    y1 = max(y0 + 1, min(source_height, int(np.ceil((1.0 - v0) * source_height))))
    cropped = plane[y0:y1, x0:x1]
    target_width = max(1, int(width))
    target_height = max(1, int(height))
    ys = np.minimum(
        np.arange(target_height, dtype=np.int64) * cropped.shape[0] // target_height,
        cropped.shape[0] - 1,
    )
    xs = np.minimum(
        np.arange(target_width, dtype=np.int64) * cropped.shape[1] // target_width,
        cropped.shape[1] - 1,
    )
    return np.ascontiguousarray(cropped[ys[:, None], xs[None, :]])


def _image_identity(image: Any) -> tuple[str, str, int, int, int]:
    from . import runtime

    width, height = map(int, image.size[:])
    return (
        str(image.get(runtime.PROJECT_UUID_KEY, "")),
        str(image.name),
        int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
        width,
        height,
    )


def image_proxy_mask(image: Any, maximum: int = PROXY_MAXIMUM) -> np.ndarray:
    """Resolve one immutable revision-keyed binary Display proxy."""

    from . import runtime

    identity = _image_identity(image)
    key = ("image-proxy", *identity, int(maximum))
    cached = _CPU_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from .residency import ensure_loaded

        ensure_loaded(image)
    except ImportError:
        pass
    proxy = np.ascontiguousarray(
        resize_nearest(runtime.image_mask(image), maximum), dtype=np.bool_
    )
    proxy.setflags(write=False)
    return _CPU_CACHE.put(key, proxy, proxy.nbytes)


def plane_proxy(
    cache_key: Hashable,
    values: Any,
    maximum: int = PROXY_MAXIMUM,
) -> np.ndarray:
    """Cache a compact immutable proxy for a Base/Coverage or derived plane."""

    key = ("plane-proxy", cache_key, int(maximum))
    cached = _CPU_CACHE.get(key)
    if cached is not None:
        return cached
    proxy = np.ascontiguousarray(resize_nearest(values, maximum))
    proxy.setflags(write=False)
    return _CPU_CACHE.put(key, proxy, proxy.nbytes)


def plane_proxy_factory(
    cache_key: Hashable,
    factory: Callable[[], Any],
    maximum: int = PROXY_MAXIMUM,
) -> np.ndarray:
    """Resolve a plane proxy without decoding its source on a cache hit."""

    key = ("plane-proxy", cache_key, int(maximum))
    cached = _CPU_CACHE.get(key)
    if cached is not None:
        return cached
    proxy = np.ascontiguousarray(resize_nearest(factory(), maximum))
    proxy.setflags(write=False)
    return _CPU_CACHE.put(key, proxy, proxy.nbytes)


def array_cache_get(key: Hashable) -> Any | None:
    return _CPU_CACHE.get(("derived", key))


def array_cache_put(key: Hashable, values: Any) -> Any:
    array = np.ascontiguousarray(values)
    array.setflags(write=False)
    return _CPU_CACHE.put(("derived", key), array, int(array.nbytes))


def thumbnail_rgba8(image: Any, uv_bbox: Any) -> np.ndarray:
    """Return the compact opaque RGBA8 thumbnail for an image revision."""

    identity = _image_identity(image)
    bbox = _normalized_bbox(uv_bbox)
    key = ("thumbnail-rgba", *identity, bbox)
    cached = _CPU_CACHE.get(key)
    if cached is not None:
        return cached
    gray = thumbnail_plane(image_proxy_mask(image), bbox)
    rgba = np.empty((THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH, 4), dtype=np.uint8)
    value = np.where(gray, np.uint8(255), np.uint8(0))
    rgba[..., :3] = value[..., None]
    rgba[..., 3] = 255
    rgba.setflags(write=False)
    return _CPU_CACHE.put(key, rgba, rgba.nbytes)


def thumbnail_texture(image: Any, uv_bbox: Any) -> Any:
    """Return a 96 x 64 GPU texture without uploading the source Display."""

    identity = _image_identity(image)
    bbox = _normalized_bbox(uv_bbox)
    key = ("thumbnail-texture", *identity, bbox)
    cached = _GPU_CACHE.get(key)
    if cached is not None:
        return cached

    import gpu

    # GPUTexture consumes rows bottom-up.  Keep the cached CPU image top-down
    # for tests and other UI clients and only flip the tiny upload buffer.
    rgba = np.flip(thumbnail_rgba8(image, bbox), axis=0).copy()
    buffer = gpu.types.Buffer("UBYTE", rgba.shape, rgba.ravel())
    texture = gpu.types.GPUTexture(
        (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), format="RGBA8", data=buffer
    )
    return _GPU_CACHE.put(key, texture, rgba.nbytes)


def invalidate(project_uuid: str | None = None) -> None:
    """Drop derived revisions globally or for one project."""

    if not project_uuid:
        _CPU_CACHE.clear()
        _GPU_CACHE.clear()
        return
    uuid = str(project_uuid)

    def belongs(key: Hashable) -> bool:
        if key == uuid:
            return True
        return isinstance(key, tuple) and any(belongs(part) for part in key)

    _CPU_CACHE.remove_where(belongs)
    _GPU_CACHE.remove_where(belongs)


def cache_statistics() -> dict[str, int]:
    """Expose deterministic cache accounting for smoke/performance tests."""

    return {
        "cpu_bytes": _CPU_CACHE.bytes_used,
        "cpu_entries": _CPU_CACHE.entry_count,
        "gpu_bytes": _GPU_CACHE.bytes_used,
        "gpu_entries": _GPU_CACHE.entry_count,
    }


__all__ = [
    "CPU_CACHE_BYTE_BUDGET",
    "GPU_CACHE_BYTE_BUDGET",
    "PROXY_MAXIMUM",
    "THUMBNAIL_HEIGHT",
    "THUMBNAIL_WIDTH",
    "ByteLRU",
    "array_cache_get",
    "array_cache_put",
    "cache_statistics",
    "image_proxy_mask",
    "invalidate",
    "max_pool",
    "plane_proxy",
    "plane_proxy_factory",
    "resize_nearest",
    "thumbnail_plane",
    "thumbnail_rgba8",
    "thumbnail_texture",
]

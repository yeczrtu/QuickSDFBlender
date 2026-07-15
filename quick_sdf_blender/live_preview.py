# SPDX-License-Identifier: GPL-3.0-or-later
"""Continuous, non-destructive Studio preview for the timeline seek rail."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import bpy

from . import runtime


SEEK_PREVIEW_ROLE = "seek_preview"
ONION_PREVIEW_ROLE = "onion_preview"
_SDF_EXECUTOR: ThreadPoolExecutor | None = None
_SDF_JOBS: dict[tuple[Any, ...], Future[Any]] = {}
_PENDING_REFRESH: dict[str, tuple[float, tuple[tuple[Any, ...], ...]]] = {}
_CACHE_GENERATION = 0


def _revision(image: Any) -> int:
    return int(image.get(runtime.IMAGE_REVISION_KEY, 0))


def _sample_plane(values: Any, maximum: int = 512):
    from .preview_cache import resize_nearest

    return resize_nearest(values, maximum).astype("bool", copy=False)


def _sample_mask(image: Any, maximum: int = 512):
    from .preview_cache import image_proxy_mask

    return image_proxy_mask(image, maximum)


def _image_sdf_key(image: Any) -> tuple[Any, ...]:
    return ("image-sdf", str(image.name), _revision(image), 512)


def _sdf(image: Any):
    from .core import exact_signed_edt
    from .preview_cache import array_cache_get, array_cache_put

    key = _image_sdf_key(image)
    result = array_cache_get(key)
    if result is None:
        result = exact_signed_edt(_sample_mask(image))
        result = array_cache_put(key, result)
    return result


def _repaired_preview_stack(project: Any, items: list[Any]):
    """Return a cached 512px export projection without touching author images."""

    import numpy as np

    records = []
    for item in items:
        display = runtime.resolve_display_image(project, item)
        if display is None:
            return None, None
        records.append((item, display))
    key = (
        str(project.uuid),
        str(getattr(items[0], "side", "RIGHT")),
        512,
        tuple(
            (
                str(getattr(item, "uuid", "")),
                display.name,
                _revision(display),
                int(getattr(item, "base_revision", 0)),
                int(getattr(item, "coverage_revision", 0)),
            )
            for item, display in records
        ),
    )
    from .preview_cache import (
        array_cache_get,
        array_cache_put,
        plane_proxy_factory,
    )

    cache_key = ("repair-stack", key)
    cached = array_cache_get(cache_key)
    if cached is None:
        display_stack = np.stack([_sample_mask(display) for _item, display in records], axis=0)
        base_stack = np.stack(
            [
                plane_proxy_factory(
                    (
                        str(project.uuid), str(getattr(item, "uuid", "")),
                        "base", int(getattr(item, "base_revision", 0)),
                    ),
                    lambda item=item: runtime.base_mask(item),
                )
                for item, _display in records
            ],
            axis=0,
        )
        coverage_stack = np.stack(
            [
                plane_proxy_factory(
                    (
                        str(project.uuid), str(getattr(item, "uuid", "")),
                        "coverage", int(getattr(item, "coverage_revision", 0)),
                    ),
                    lambda item=item: runtime.coverage_mask(item),
                )
                for item, _display in records
            ],
            axis=0,
        )
        try:
            from . import native

            cached = native.repair_side_monotonic(
                display_stack, base_stack, coverage_stack
            ).masks
        except (ImportError, OSError, AttributeError):
            from .core import repair_side_monotonic

            cached = repair_side_monotonic(
                display_stack, base_stack, coverage_stack
            ).masks
        cached = array_cache_put(cache_key, cached)
    return cached, key


def _repaired_sdf(mask: Any, repair_key: tuple[Any, ...], index: int):
    from .core import exact_signed_edt
    from .preview_cache import array_cache_get, array_cache_put

    key = ("repaired", repair_key, int(index))
    result = array_cache_get(key)
    if result is None:
        result = exact_signed_edt(mask)
        result = array_cache_put(key, result)
    return result


def _repaired_sdf_key(repair_key: tuple[Any, ...], index: int) -> tuple[Any, ...]:
    return ("repaired", repair_key, int(index))


def _executor() -> ThreadPoolExecutor:
    global _SDF_EXECUTOR
    if _SDF_EXECUTOR is None:
        _SDF_EXECUTOR = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="QuickSDF-Preview"
        )
    return _SDF_EXECUTOR


def _compute_sdf_job(
    entries: tuple[tuple[tuple[Any, ...], Any], ...],
    generation: int,
) -> tuple[tuple[Any, ...], ...]:
    from .core import exact_signed_edt
    from .preview_cache import array_cache_put

    completed: list[tuple[Any, ...]] = []
    for key, mask in entries:
        result = exact_signed_edt(mask)
        if generation != _CACHE_GENERATION:
            return ()
        array_cache_put(key, result)
        completed.append(key)
    return tuple(completed)


def _queue_sdf_preview(
    project: Any,
    angle: float,
    entries: tuple[tuple[tuple[Any, ...], Any], ...],
) -> None:
    from .preview_cache import array_cache_get

    missing = tuple((key, mask) for key, mask in entries if array_cache_get(key) is None)
    keys = tuple(key for key, _mask in entries)
    uuid = str(getattr(project, "uuid", ""))
    _PENDING_REFRESH[uuid] = (float(angle), keys)
    if missing:
        job_key = tuple(key for key, _mask in missing)
        if job_key not in _SDF_JOBS:
            immutable = tuple((key, mask) for key, mask in missing)
            _SDF_JOBS[job_key] = _executor().submit(
                _compute_sdf_job, immutable, _CACHE_GENERATION
            )
    if not bpy.app.timers.is_registered(_poll_sdf_jobs):
        bpy.app.timers.register(_poll_sdf_jobs, first_interval=0.02)


def _project_for_uuid(uuid: str) -> Any | None:
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(getattr(project, "uuid", "")) == uuid:
                return project
    return None


def _poll_sdf_jobs() -> float | None:
    from .preview_cache import array_cache_get

    failed_keys: set[tuple[Any, ...]] = set()
    for job_key, future in tuple(_SDF_JOBS.items()):
        if not future.done():
            continue
        _SDF_JOBS.pop(job_key, None)
        try:
            future.result()
        except Exception:
            failed_keys.update(job_key)

    for uuid, (angle, keys) in tuple(_PENDING_REFRESH.items()):
        if any(key in failed_keys for key in keys):
            _PENDING_REFRESH.pop(uuid, None)
            continue
        if not all(array_cache_get(key) is not None for key in keys):
            continue
        _PENDING_REFRESH.pop(uuid, None)
        project = _project_for_uuid(uuid)
        if project is None:
            continue
        if abs(float(getattr(project, "seek_angle", angle)) - angle) > 1.0e-4:
            continue
        try:
            update_seek_preview(project, angle)
        except (AttributeError, ReferenceError, RuntimeError, ValueError):
            pass
    if _SDF_JOBS or _PENDING_REFRESH:
        return 0.02
    return None


def _finite_sdf(values: Any):
    """Replace constant-mask infinities with a stable image-sized distance."""

    import numpy as np

    diagonal = float((values.shape[0] ** 2 + values.shape[1] ** 2) ** 0.5 + 1.0)
    return np.nan_to_num(values, nan=0.0, posinf=diagonal, neginf=-diagonal)


def _preview_image(project: Any, width: int, height: int):
    name = f"QSDF Seek {str(project.uuid)[:8]}"
    image = bpy.data.images.get(name)
    if image is not None and tuple(image.size[:]) != (width, height):
        bpy.data.images.remove(image)
        image = None
    if image is None:
        image = bpy.data.images.new(name, width=width, height=height, alpha=False, float_buffer=False)
    image[runtime.PROJECT_UUID_KEY] = str(project.uuid)
    image[runtime.ROLE_KEY] = SEEK_PREVIEW_ROLE
    runtime.make_image_opaque(image)
    try:
        image.colorspace_settings.name = "Non-Color"
    except (AttributeError, TypeError):
        pass
    return image


def _onion_image(project: Any, width: int, height: int):
    name = f"QSDF Onion {str(project.uuid)[:8]}"
    image = bpy.data.images.get(name)
    if image is not None and tuple(image.size[:]) != (width, height):
        bpy.data.images.remove(image)
        image = None
    if image is None:
        image = bpy.data.images.new(name, width=width, height=height, alpha=False, float_buffer=False)
    image[runtime.PROJECT_UUID_KEY] = str(project.uuid)
    image[runtime.ROLE_KEY] = ONION_PREVIEW_ROLE
    runtime.make_image_opaque(image)
    try:
        image.colorspace_settings.name = "Non-Color"
    except (AttributeError, TypeError):
        pass
    return image


def update_onion_preview(project: Any) -> Any | None:
    """Show adjacent-key differences in 2D without replacing Paint Canvas."""

    import numpy as np

    side = str(getattr(project, "active_side", getattr(project, "authoring_side", "RIGHT")))
    items = sorted(
        (item for item in project.angles if str(getattr(item, "side", "RIGHT")) == side),
        key=lambda item: float(item.angle),
    )
    active = runtime.active_angle(project)
    if not items or active is None or str(getattr(active, "side", "RIGHT")) != side:
        return None
    active_position = next(
        (index for index, item in enumerate(items) if str(item.uuid) == str(active.uuid)),
        -1,
    )
    if active_position < 0:
        return None
    # Onion needs only the edit key and its immediate neighbours.  Loading the
    # other authored keys served no visual purpose and defeated image residency.
    first = max(0, active_position - 1)
    last = min(len(items), active_position + 2)
    neighbours = items[first:last]
    records = [
        (item, runtime.resolve_display_image(project, item)) for item in neighbours
    ]
    if any(image is None for _item, image in records):
        return None
    masks = np.stack([_sample_mask(image) for _item, image in records], axis=0)
    angles = np.asarray([float(item.angle) for item, _image in records], dtype=np.float64)
    from .review import review_onion_difference

    rgba = review_onion_difference(masks, angles, float(active.angle), maximum=512)
    height, width = rgba.shape[:2]
    image = _onion_image(project, width, height)
    runtime.write_image_rgba8(
        image, np.rint(np.clip(rgba, 0.0, 1.0) * 255.0).astype(np.uint8)
    )
    try:
        from .studio import active_session, find_window

        session = active_session()
        window = find_window(session.window_pointer) if session is not None else None
        if window is not None:
            for area in window.screen.areas:
                if area.type == "IMAGE_EDITOR":
                    area.spaces.active.image = image
                    area.tag_redraw()
    except (ImportError, ReferenceError, RuntimeError):
        pass
    return image


def update_seek_preview(project: Any, angle: float) -> Any | None:
    """Show an exact-SDF interpolation without changing the paint canvas."""

    import numpy as np

    side = str(getattr(project, "authoring_side", "RIGHT"))
    items = sorted(
        (item for item in project.angles if str(getattr(item, "side", "RIGHT")) == side),
        key=lambda item: float(item.angle),
    )
    if not items:
        return None
    repaired_stack, repair_key = _repaired_preview_stack(project, items)
    value = max(0.0, min(90.0, float(angle)))
    lower = max((item for item in items if float(item.angle) <= value), key=lambda item: float(item.angle), default=items[0])
    upper = min((item for item in items if float(item.angle) >= value), key=lambda item: float(item.angle), default=items[-1])
    lower_image = runtime.resolve_display_image(project, lower)
    upper_image = runtime.resolve_display_image(project, upper)
    if lower_image is None or upper_image is None:
        return None
    lower_index = next(index for index, item in enumerate(items) if item.uuid == lower.uuid)
    upper_index = next(index for index, item in enumerate(items) if item.uuid == upper.uuid)
    lower_angle = float(lower.angle)
    upper_angle = float(upper.angle)
    if lower.uuid == upper.uuid or abs(upper_angle - lower_angle) <= 1.0e-7:
        mask = (
            repaired_stack[lower_index]
            if repaired_stack is not None
            else _sample_mask(lower_image)
        )
    else:
        factor = (value - lower_angle) / (upper_angle - lower_angle)
        if repaired_stack is not None:
            from .preview_cache import array_cache_get

            first_key = _repaired_sdf_key(repair_key, lower_index)
            second_key = _repaired_sdf_key(repair_key, upper_index)
            first = array_cache_get(first_key)
            second = array_cache_get(second_key)
            if first is None or second is None:
                # Keep scrubbing responsive: show the nearest repaired key now,
                # compute exact SDFs on one bounded worker, then refresh this
                # angle from Blender's main-thread timer.
                mask = repaired_stack[
                    lower_index if factor < 0.5 else upper_index
                ]
                _queue_sdf_preview(
                    project,
                    value,
                    (
                        (first_key, repaired_stack[lower_index]),
                        (second_key, repaired_stack[upper_index]),
                    ),
                )
                first = second = None
            if first is not None and second is not None:
                first = _finite_sdf(first)
                second = _finite_sdf(second)
        else:
            first = _finite_sdf(_sdf(lower_image))
            second = _finite_sdf(_sdf(upper_image))
        if first is not None and second is not None:
            mask = ((1.0 - factor) * first + factor * second) <= 0.0
    height, width = mask.shape
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    luminance = mask.astype(np.uint8, copy=False) * np.uint8(255)
    rgba[..., :3] = luminance[..., None]
    rgba[..., 3] = 255
    image = _preview_image(project, width, height)
    runtime.write_image_rgba8(image, rgba)
    from .preview import set_preview_image

    set_preview_image(project, image)
    return image


def invalidate(project_uuid: str | None = None) -> None:
    global _CACHE_GENERATION
    _CACHE_GENERATION += 1
    for future in _SDF_JOBS.values():
        future.cancel()
    _SDF_JOBS.clear()
    if project_uuid:
        _PENDING_REFRESH.pop(str(project_uuid), None)
    else:
        _PENDING_REFRESH.clear()
    from .preview_cache import invalidate as invalidate_preview_cache

    invalidate_preview_cache(project_uuid)


def _detach_temporary_images(project_uuid: str = "", replacement: Any | None = None) -> None:
    roles = {SEEK_PREVIEW_ROLE, ONION_PREVIEW_ROLE}

    def matches(image: Any | None) -> bool:
        return bool(
            image is not None
            and image.get(runtime.ROLE_KEY) in roles
            and (
                not project_uuid
                or str(image.get(runtime.PROJECT_UUID_KEY, "")) == project_uuid
            )
        )

    for material in bpy.data.materials:
        if not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if hasattr(node, "image") and matches(node.image):
                node.image = None
    for screen in bpy.data.screens:
        for area in screen.areas:
            changed = False
            for space in area.spaces:
                if hasattr(space, "image") and matches(space.image):
                    space.image = replacement
                    changed = True
            if changed:
                area.tag_redraw()


def detach_project_temporaries(project: Any | None) -> None:
    if project is None:
        return
    uuid = str(getattr(project, "uuid", ""))
    active = runtime.active_angle(project)
    replacement = runtime.resolve_display_image(project, active) if active is not None else None
    _detach_temporary_images(uuid, replacement)
    # Do not call Image.gl_free() while an interactive GPU viewport may still
    # hold the texture.  Blender occasionally releases that cache one event
    # later, and forcing it here can trip its allocator diagnostics on quit.
    # With all data/editor references detached, Blender owns the remaining
    # zero-user image lifetime safely.  save_pre uses the explicit purge path
    # below so these transient datablocks are never serialized.


def purge_project_temporaries(project: Any | None = None) -> None:
    uuid = str(getattr(project, "uuid", "")) if project is not None else ""
    if project is not None:
        detach_project_temporaries(project)
    else:
        _detach_temporary_images()
    roles = {SEEK_PREVIEW_ROLE, ONION_PREVIEW_ROLE}
    for image in tuple(bpy.data.images):
        if image.get(runtime.ROLE_KEY) not in roles:
            continue
        if uuid and str(image.get(runtime.PROJECT_UUID_KEY, "")) != uuid:
            continue
        try:
            image.gl_free()
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        bpy.data.images.remove(image)


def release_project(project: Any | None) -> None:
    if project is None:
        return
    uuid = str(getattr(project, "uuid", ""))
    detach_project_temporaries(project)
    if bool(getattr(project, "onion_enabled", False)):
        project.onion_enabled = False
    invalidate(uuid)


def cleanup() -> None:
    global _SDF_EXECUTOR
    _detach_temporary_images()
    invalidate()
    if bpy.app.timers.is_registered(_poll_sdf_jobs):
        try:
            bpy.app.timers.unregister(_poll_sdf_jobs)
        except ValueError:
            pass
    if _SDF_EXECUTOR is not None:
        _SDF_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _SDF_EXECUTOR = None


__all__ = [
    "ONION_PREVIEW_ROLE", "SEEK_PREVIEW_ROLE", "cleanup",
    "detach_project_temporaries", "invalidate", "release_project",
    "purge_project_temporaries", "update_onion_preview", "update_seek_preview",
]

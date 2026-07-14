# SPDX-License-Identifier: GPL-3.0-or-later
"""Continuous, non-destructive Studio preview for the timeline seek rail."""

from __future__ import annotations

from typing import Any

import bpy

from . import runtime


SEEK_PREVIEW_ROLE = "seek_preview"
ONION_PREVIEW_ROLE = "onion_preview"
_SDF_CACHE: dict[tuple[Any, ...], Any] = {}
_REPAIR_CACHE: dict[tuple[Any, ...], Any] = {}


def _revision(image: Any) -> int:
    return int(image.get(runtime.IMAGE_REVISION_KEY, 0))


def _sample_plane(values: Any, maximum: int = 512):
    import numpy as np

    mask = np.asarray(values, dtype=np.bool_)
    if mask.ndim != 2:
        raise ValueError(f"Preview masks must be two-dimensional, got {mask.shape}")
    height, width = mask.shape
    scale = min(1.0, float(maximum) / max(height, width))
    target_height = max(1, int(round(height * scale)))
    target_width = max(1, int(round(width * scale)))
    if (target_height, target_width) == (height, width):
        return mask
    ys = np.minimum(np.arange(target_height) * height // target_height, height - 1)
    xs = np.minimum(np.arange(target_width) * width // target_width, width - 1)
    return mask[ys[:, None], xs[None, :]]


def _sample_mask(image: Any, maximum: int = 512):
    return _sample_plane(runtime.image_mask(image), maximum)


def _sdf(image: Any):
    from .core import exact_signed_edt

    key = (str(image.name), _revision(image), 512)
    result = _SDF_CACHE.get(key)
    if result is None:
        result = exact_signed_edt(_sample_mask(image))
        _SDF_CACHE[key] = result
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
    cached = _REPAIR_CACHE.get(key)
    if cached is None:
        display_stack = np.stack([_sample_mask(display) for _item, display in records], axis=0)
        base_stack = np.stack(
            [_sample_plane(runtime.base_mask(item)) for item, _display in records],
            axis=0,
        )
        coverage_stack = np.stack(
            [_sample_plane(runtime.coverage_mask(item)) for item, _display in records],
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
        _REPAIR_CACHE[key] = cached
    return cached, key


def _repaired_sdf(mask: Any, repair_key: tuple[Any, ...], index: int):
    from .core import exact_signed_edt

    key = ("repaired", repair_key, int(index))
    result = _SDF_CACHE.get(key)
    if result is None:
        result = exact_signed_edt(mask)
        _SDF_CACHE[key] = result
    return result


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
    masks = np.stack(
        [runtime.image_mask(runtime.resolve_display_image(project, item)) for item in items],
        axis=0,
    )
    angles = np.asarray([float(item.angle) for item in items], dtype=np.float64)
    from .review import review_onion_difference

    rgba = review_onion_difference(masks, angles, float(active.angle))
    height, width = rgba.shape[:2]
    image = _onion_image(project, width, height)
    runtime.write_image_rgba(image, rgba)
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
            first = _finite_sdf(
                _repaired_sdf(repaired_stack[lower_index], repair_key, lower_index)
            )
            second = _finite_sdf(
                _repaired_sdf(repaired_stack[upper_index], repair_key, upper_index)
            )
        else:
            first = _finite_sdf(_sdf(lower_image))
            second = _finite_sdf(_sdf(upper_image))
        mask = ((1.0 - factor) * first + factor * second) <= 0.0
    height, width = mask.shape
    rgba = np.ones((height, width, 4), dtype=np.float32)
    rgba[..., :3] = mask[..., None]
    image = _preview_image(project, width, height)
    runtime.write_image_rgba(image, rgba)
    from .preview import set_preview_image

    set_preview_image(project, image)
    return image


def invalidate(project_uuid: str | None = None) -> None:
    # Revision-bearing cache keys make selective invalidation unnecessary; a
    # full clear bounds memory after long paint sessions.
    _SDF_CACHE.clear()
    _REPAIR_CACHE.clear()


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
    _detach_temporary_images()
    invalidate()


__all__ = [
    "ONION_PREVIEW_ROLE", "SEEK_PREVIEW_ROLE", "cleanup",
    "detach_project_temporaries", "invalidate", "release_project",
    "purge_project_temporaries", "update_onion_preview", "update_seek_preview",
]

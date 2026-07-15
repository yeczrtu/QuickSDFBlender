# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded residency for persistent Quick SDF paint images.

Blender owns the actual image buffers.  This module only decides when a
generated image has a current packed source and when it is safe to release its
CPU/GPU cache.  No long-lived ``bpy`` object references are retained: queues
store image names so Undo/Load can replace datablocks safely.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import bpy


PACKED_REVISION_KEY = "quick_sdf_packed_revision"
RESIDENCY_MIN_RESOLUTION = 2048
DISPLAY_RESIDENT_BUDGET = 256 * 1024 * 1024
PERSISTENT_ROLES = frozenset(("angle_display", "aux_mask"))

_DIRTY: "OrderedDict[str, None]" = OrderedDict()
_PENDING_RELEASE: "OrderedDict[str, None]" = OrderedDict()
_TIMER_RUNNING = False


def _revision(image: Any) -> int:
    return int(image.get("quick_sdf_revision", 0))


def _packed_revision(image: Any) -> int:
    return int(image.get(PACKED_REVISION_KEY, -1))


def _persistent(image: Any) -> bool:
    return str(image.get("quick_sdf_role", "")) in PERSISTENT_ROLES and bool(
        image.get("quick_sdf_project_uuid", "")
    )


def _register_timer() -> None:
    global _TIMER_RUNNING
    if _TIMER_RUNNING or bpy.app.background:
        return
    if not bpy.app.timers.is_registered(_idle_step):
        bpy.app.timers.register(_idle_step, first_interval=0.02)
    _TIMER_RUNNING = True


def ensure_loaded(image: Any | None) -> Any | None:
    """Materialize an Image buffer from its packed source when necessary."""

    if image is None or bool(getattr(image, "has_data", True)):
        return image
    # Accessing one pixel asks Blender to decode a packed generated image.  It
    # preserves the datablock and is substantially cheaper than reload(),
    # which is not defined for generated images on every supported build.
    pixels = getattr(image, "pixels", None)
    if pixels is None or len(pixels) == 0:
        raise RuntimeError(f"Packed image {getattr(image, 'name', '<unknown>')!r} has no pixels")
    _sample = pixels[0]
    del _sample
    return image


def pack_now(image: Any | None) -> bool:
    """Synchronously publish one persistent image's current pixels."""

    if image is None or not _persistent(image):
        return False
    ensure_loaded(image)
    image.pack()
    image[PACKED_REVISION_KEY] = _revision(image)
    _DIRTY.pop(str(image.name), None)
    return True


def mark_changed(image: Any | None, *, synchronous: bool = False) -> None:
    """Mark an image's packed representation stale.

    The active/provisional Canvas is packed immediately to prevent Blender's
    projection-paint cache from replacing Python writes.  Other keys are
    serialized one at a time while Studio is idle.
    """

    if image is None or not _persistent(image):
        return
    if synchronous:
        pack_now(image)
        return
    name = str(image.name)
    _DIRTY.pop(name, None)
    _DIRTY[name] = None
    _register_timer()


def _image_in_use(image: Any) -> bool:
    for scene in bpy.data.scenes:
        paint = getattr(getattr(scene, "tool_settings", None), "image_paint", None)
        if paint is not None and getattr(paint, "canvas", None) == image:
            return True
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "IMAGE_EDITOR":
                continue
            for space in area.spaces:
                if getattr(space, "image", None) == image:
                    return True
    for material in bpy.data.materials:
        tree = getattr(material, "node_tree", None)
        if not getattr(material, "use_nodes", False) or tree is None:
            continue
        if any(getattr(node, "image", None) == image for node in tree.nodes):
            return True
    return False


def _release_cpu(image: Any) -> bool:
    if _image_in_use(image) or _packed_revision(image) != _revision(image):
        return False
    free = getattr(image, "buffers_free", None)
    if not callable(free):
        return False
    free()
    if bpy.app.background:
        return True
    name = str(image.name)
    _PENDING_RELEASE.pop(name, None)
    _PENDING_RELEASE[name] = None
    _register_timer()
    return True


def _release_gpu(image: Any) -> bool:
    if _image_in_use(image):
        # The image became active between CPU release and this deferred event.
        # Keep its GPU texture and drop this request; a later eviction will
        # enqueue a fresh release after it leaves the editors again.
        return True
    free = getattr(image, "gl_free", None)
    if callable(free):
        free()
    return True


def _project_for_uuid(project_uuid: str) -> Any | None:
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(getattr(project, "uuid", "")) == project_uuid:
                return project
    return None


def _keep_names(project: Any, primary_image: Any | None) -> set[str]:
    keep: list[Any] = []
    if primary_image is not None:
        keep.append(primary_image)
    active_index = int(getattr(project, "active_angle_index", -1))
    angles = getattr(project, "angles", ())
    if 0 <= active_index < len(angles):
        active = angles[active_index]
        side = str(getattr(active, "side", "RIGHT"))
        same_side = [
            item for item in angles if str(getattr(item, "side", "RIGHT")) == side
        ]
        same_side.sort(key=lambda item: float(getattr(item, "angle", 0.0)))
        try:
            position = next(
                index
                for index, item in enumerate(same_side)
                if str(getattr(item, "uuid", "")) == str(getattr(active, "uuid", ""))
            )
        except StopIteration:
            position = -1
        from .runtime import resolve_display_image

        for index in (position, position - 1, position + 1):
            if 0 <= index < len(same_side):
                image = resolve_display_image(project, same_side[index])
                if image is not None and image not in keep:
                    keep.append(image)
    result: set[str] = set()
    used = 0
    for image in keep:
        width, height = map(int, image.size[:])
        # Blender exposes every byte Image to Python through a float RGBA
        # buffer (16 bytes/pixel), which is the actual CPU residency cost.
        cost = width * height * 16
        if result and used + cost > DISPLAY_RESIDENT_BUDGET:
            continue
        result.add(str(image.name))
        used += cost
    return result


def reconcile_project(project: Any, primary_image: Any | None = None) -> None:
    """Keep the active key and its neighbours, evict other 2K+ buffers."""

    if project is None or int(getattr(project, "resolution", 0)) < RESIDENCY_MIN_RESOLUTION:
        return
    keep = _keep_names(project, primary_image)
    project_uuid = str(getattr(project, "uuid", ""))
    for image in tuple(bpy.data.images):
        if (
            str(image.get("quick_sdf_project_uuid", "")) != project_uuid
            or not _persistent(image)
            or str(image.name) in keep
        ):
            continue
        if _packed_revision(image) != _revision(image):
            mark_changed(image)
            continue
        if bool(getattr(image, "has_data", True)):
            _release_cpu(image)


def activate(project: Any, image: Any | None) -> Any | None:
    """Load the selected canvas before Blender receives the pointer."""

    ensure_loaded(image)
    return image


def flush_dirty(project_uuid: str = "") -> tuple[str, ...]:
    """Synchronously pack queued authoring images, normally before Save."""

    failed: list[str] = []
    for name in tuple(_DIRTY):
        image = bpy.data.images.get(name)
        if image is None:
            _DIRTY.pop(name, None)
            continue
        if project_uuid and str(image.get("quick_sdf_project_uuid", "")) != project_uuid:
            continue
        try:
            pack_now(image)
        except (OSError, ReferenceError, RuntimeError):
            # Saving must remain Blender's decision. A stale packed revision is
            # retained so the next validation can report the missing layer.
            failed.append(name)
    return tuple(failed)


def forget_image(image_or_name: Any) -> None:
    name = str(getattr(image_or_name, "name", image_or_name))
    _DIRTY.pop(name, None)
    _PENDING_RELEASE.pop(name, None)
    try:
        from .runtime import invalidate_gray_cache

        invalidate_gray_cache(name)
    except ImportError:
        pass


def _idle_step() -> float | None:
    global _TIMER_RUNNING
    if _DIRTY:
        name, _value = _DIRTY.popitem(last=False)
        image = bpy.data.images.get(name)
        if image is not None:
            try:
                pack_now(image)
                project = _project_for_uuid(str(image.get("quick_sdf_project_uuid", "")))
                if project is not None and int(getattr(project, "resolution", 0)) >= RESIDENCY_MIN_RESOLUTION:
                    reconcile_project(project)
            except (OSError, ReferenceError, RuntimeError):
                # A transient pack failure must not silently make the stale
                # recovery source eligible for Save or buffer eviction.
                _DIRTY[name] = None
                return 0.25
        return 0.02
    if _PENDING_RELEASE:
        name, _value = _PENDING_RELEASE.popitem(last=False)
        image = bpy.data.images.get(name)
        if image is not None:
            try:
                if not _release_gpu(image):
                    _PENDING_RELEASE[name] = None
            except (ReferenceError, RuntimeError):
                pass
        return 0.02 if _PENDING_RELEASE else None
    _TIMER_RUNNING = False
    return None


def shutdown() -> None:
    global _TIMER_RUNNING
    _DIRTY.clear()
    _PENDING_RELEASE.clear()
    if bpy.app.timers.is_registered(_idle_step):
        bpy.app.timers.unregister(_idle_step)
    _TIMER_RUNNING = False


def diagnostics() -> dict[str, int]:
    return {
        "dirty_images": len(_DIRTY),
        "pending_gpu_release": len(_PENDING_RELEASE),
        "display_budget_bytes": DISPLAY_RESIDENT_BUDGET,
    }


__all__ = [
    "DISPLAY_RESIDENT_BUDGET",
    "PACKED_REVISION_KEY",
    "RESIDENCY_MIN_RESOLUTION",
    "activate",
    "diagnostics",
    "ensure_loaded",
    "flush_dirty",
    "forget_image",
    "mark_changed",
    "pack_now",
    "reconcile_project",
    "shutdown",
]

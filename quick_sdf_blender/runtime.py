# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender runtime helpers shared by operators, UI, and preview code."""

from __future__ import annotations

from array import array
import hashlib
import math
import struct
from typing import Any, Iterable
import uuid

import bpy
from bpy.app.handlers import persistent

from .model import DEFAULT_ANGLES, SCHEMA_VERSION


PROJECT_UUID_KEY = "quick_sdf_project_uuid"
ANGLE_UUID_KEY = "quick_sdf_angle_uuid"
ROLE_KEY = "quick_sdf_role"
IMAGE_REVISION_KEY = "quick_sdf_revision"
AUX_MASK_UUID_KEY = "quick_sdf_aux_mask_uuid"
AUX_MASK_INITIALIZED_KEY = "quick_sdf_aux_mask_initialized"
DISPLAY_ROLE = "angle_display"
BASE_ROLE = "angle_base"
COVERAGE_ROLE = "angle_coverage"
AUX_MASK_ROLE = "aux_mask"
THRESHOLD_ROLE = "threshold_preview"
EXPORT_ADJUSTMENT_ROLE = "export_adjustment_preview"
PALETTE_NAME = "Quick SDF Light Shadow"
_PAINT_SNAPSHOTS: dict[str, Any] = {}
_INTERACTIVE_PAINT_SNAPSHOTS: dict[str, tuple[str, str, Any, str, Any | None]] = {}
_AUX_PAINT_SNAPSHOTS: dict[str, tuple[str, str, Any]] = {}
_BASE_BAKE_UUIDS: set[str] = set()
_PENDING_BASE_SIGNATURES: set[str] = set()


def new_uuid() -> str:
    return str(uuid.uuid4())


def begin_base_bake(project_uuid: str) -> None:
    _BASE_BAKE_UUIDS.add(str(project_uuid))


def end_base_bake(project_uuid: str) -> None:
    _BASE_BAKE_UUIDS.discard(str(project_uuid))


def compute_base_signature(project: Any, scene: bpy.types.Scene | None = None) -> str:
    """Hash artist-relevant source state without evaluating Blender off-thread."""

    obj = getattr(project, "target_object", None)
    if obj is None or getattr(obj, "type", "") != "MESH":
        return ""
    scene = scene or bpy.context.scene
    digest = hashlib.sha1()
    digest.update(str(obj.name_full).encode("utf-8", "surrogatepass"))
    digest.update(struct.pack("<q", int(getattr(scene, "frame_current", 0))))
    mesh = obj.data
    digest.update(struct.pack("<qqq", len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
    for vertex in mesh.vertices:
        digest.update(struct.pack("<3f", *map(float, vertex.co)))
    shape_keys = getattr(mesh, "shape_keys", None)
    if shape_keys is not None:
        for block in shape_keys.key_blocks:
            digest.update(str(block.name).encode("utf-8", "surrogatepass"))
            digest.update(struct.pack("<f", float(block.value)))
    for modifier in obj.modifiers:
        digest.update(str(modifier.name).encode("utf-8", "surrogatepass"))
        digest.update(str(modifier.type).encode("ascii", "ignore"))
        digest.update(bytes((bool(modifier.show_viewport), bool(modifier.show_render))))
        armature = getattr(modifier, "object", None)
        pose = getattr(armature, "pose", None)
        if pose is not None:
            for bone in pose.bones:
                digest.update(str(bone.name).encode("utf-8", "surrogatepass"))
                for row in bone.matrix_basis:
                    digest.update(struct.pack("<4f", *map(float, row)))
    return digest.hexdigest()


def refresh_base_staleness(project: Any, scene: bpy.types.Scene | None = None) -> bool:
    previous = str(getattr(project, "base_signature", ""))
    current = compute_base_signature(project, scene)
    stale = bool(previous and current and previous != current)
    if stale:
        project.base_needs_update = True
    return stale


def active_project(scene: bpy.types.Scene | None = None) -> Any | None:
    scene = scene or bpy.context.scene
    projects = getattr(scene, "quick_sdf_projects", None)
    if not projects:
        return None
    index = int(getattr(scene, "quick_sdf_active_project_index", -1))
    if index < 0 or index >= len(projects):
        return None
    return projects[index]


def active_angle(project: Any) -> Any | None:
    angles = getattr(project, "angles", None)
    if not angles:
        return None
    index = max(0, min(int(project.active_angle_index), len(angles) - 1))
    return angles[index]


_LAYER_FIELDS = {
    DISPLAY_ROLE: ("display_image", "display_image_name"),
    BASE_ROLE: ("base_image", "base_image_name"),
    COVERAGE_ROLE: ("coverage_image", "coverage_image_name"),
}


def _image_matches_role(image: bpy.types.Image, role: str) -> bool:
    return str(image.get(ROLE_KEY, "")) == role


def resolve_angle_data_image(
    project: Any,
    angle_item: Any,
    role: str,
) -> bpy.types.Image | None:
    """Resolve one angle layer without confusing same-UUID sibling images."""

    if role not in _LAYER_FIELDS:
        raise ValueError(f"Unknown angle image role: {role!r}")
    pointer_name, string_name = _LAYER_FIELDS[role]
    candidates: list[bpy.types.Image] = []
    pointer = getattr(angle_item, pointer_name, None)
    if pointer is not None:
        candidates.append(pointer)
    stored_name = str(getattr(angle_item, string_name, ""))
    named = bpy.data.images.get(stored_name) if stored_name else None
    if named is not None and named not in candidates:
        candidates.append(named)
    candidate = next(
        (
            image
            for image in candidates
            if _image_matches_role(image, role)
        ),
        None,
    )
    if candidate is None:
        for image in bpy.data.images:
            if (
                image.get(PROJECT_UUID_KEY) == project.uuid
                and image.get(ANGLE_UUID_KEY) == angle_item.uuid
                and _image_matches_role(image, role)
            ):
                candidate = image
                break
    if candidate is not None:
        setattr(angle_item, pointer_name, candidate)
        setattr(angle_item, string_name, candidate.name)
    return candidate


def resolve_display_image(project: Any, angle_item: Any) -> bpy.types.Image | None:
    return resolve_angle_data_image(project, angle_item, DISPLAY_ROLE)


def resolve_base_image(project: Any, angle_item: Any) -> bpy.types.Image | None:
    return resolve_angle_data_image(project, angle_item, BASE_ROLE)


def resolve_coverage_image(project: Any, angle_item: Any) -> bpy.types.Image | None:
    return resolve_angle_data_image(project, angle_item, COVERAGE_ROLE)


def resolve_aux_mask_image(project: Any, aux_item: Any) -> bpy.types.Image | None:
    """Resolve one project-owned angle-independent mask after Undo/Load."""

    if aux_item is None:
        return None
    candidates: list[bpy.types.Image] = []
    pointer = getattr(aux_item, "image", None)
    if pointer is not None:
        candidates.append(pointer)
    stored_name = str(getattr(aux_item, "image_name", ""))
    named = bpy.data.images.get(stored_name) if stored_name else None
    if named is not None and named not in candidates:
        candidates.append(named)
    mask_uuid = str(getattr(aux_item, "uuid", ""))
    candidate = next(
        (
            image
            for image in candidates
            if _image_matches_role(image, AUX_MASK_ROLE)
            and str(image.get(AUX_MASK_UUID_KEY, "")) == mask_uuid
        ),
        None,
    )
    if candidate is None:
        candidate = next(
            (
                image
                for image in bpy.data.images
                if str(image.get(PROJECT_UUID_KEY, "")) == str(getattr(project, "uuid", ""))
                and _image_matches_role(image, AUX_MASK_ROLE)
                and str(image.get(AUX_MASK_UUID_KEY, "")) == mask_uuid
            ),
            None,
        )
    if candidate is not None:
        aux_item.image = candidate
        aux_item.image_name = candidate.name
    return candidate


def aux_mask_for_uuid(project: Any, mask_uuid: str) -> Any | None:
    return next(
        (
            item
            for item in getattr(project, "aux_masks", ())
            if str(getattr(item, "uuid", "")) == str(mask_uuid)
        ),
        None,
    )


def active_aux_mask(project: Any) -> Any | None:
    items = getattr(project, "aux_masks", ())
    if items:
        index = int(getattr(project, "active_aux_mask_index", -1))
        if 0 <= index < len(items):
            item = items[index]
            try:
                project.active_aux_mask_uuid = str(item.uuid)
            except (AttributeError, ReferenceError, TypeError):
                pass
            return item
    uuid_value = str(getattr(project, "active_aux_mask_uuid", ""))
    if uuid_value:
        found = aux_mask_for_uuid(project, uuid_value)
        if found is not None:
            return found
    return None


def tag_image(
    image: bpy.types.Image,
    project_uuid: str,
    angle_uuid: str = "",
    role: str = DISPLAY_ROLE,
) -> None:
    image[PROJECT_UUID_KEY] = project_uuid
    image[ANGLE_UUID_KEY] = angle_uuid
    image[ROLE_KEY] = role
    try:
        image.colorspace_settings.name = "Non-Color"
    except (AttributeError, TypeError):
        pass


_tag_image = tag_image


def make_image_opaque(image: bpy.types.Image) -> None:
    try:
        # Reassigning alpha_mode on a packed image reloads its old packed
        # buffer and can discard pixel edits made immediately beforehand.
        if str(image.alpha_mode) != "NONE":
            image.alpha_mode = "NONE"
    except (AttributeError, TypeError, ValueError):
        pass


def create_angle_layer_image(
    project_uuid: str,
    angle_uuid: str,
    angle: float,
    resolution: int,
    role: str,
    *,
    side: str = "RIGHT",
) -> bpy.types.Image:
    if role not in _LAYER_FIELDS:
        raise ValueError(f"Unknown angle image role: {role!r}")
    stem = project_uuid.split("-", 1)[0]
    label = {
        DISPLAY_ROLE: "Mask",
        BASE_ROLE: "Base",
        COVERAGE_ROLE: "Coverage",
    }[role]
    name = f"QSDF {label} {stem} {side.title()} {angle:04.1f}"
    image = bpy.data.images.new(name, width=resolution, height=resolution, alpha=True, float_buffer=False)
    image.generated_type = "BLANK"
    value = 0.0 if role == COVERAGE_ROLE else 1.0
    image.generated_color = (value, value, value, 1.0)
    tag_image(image, project_uuid, angle_uuid, role)
    make_image_opaque(image)
    # generated_color is applied by Blender to the generated buffer.  Setting one
    # sample forces buffer allocation before the image becomes a paint canvas.
    image.update()
    return image


def create_aux_mask_image(
    project: Any,
    aux_item: Any,
    *,
    fill_value: float = 0.0,
) -> bpy.types.Image:
    """Create one opaque project-owned grayscale image for a static signal."""

    value = max(0.0, min(1.0, float(fill_value)))
    stem = str(getattr(project, "uuid", "project")).split("-", 1)[0]
    label = str(getattr(aux_item, "name", "Mask")) or "Mask"
    image = bpy.data.images.new(
        f"QSDF Aux {stem} {label}",
        width=int(project.resolution),
        height=int(project.resolution),
        alpha=True,
        float_buffer=False,
    )
    image.generated_type = "BLANK"
    image.generated_color = (value, value, value, 1.0)
    tag_image(image, str(project.uuid), role=AUX_MASK_ROLE)
    image[AUX_MASK_UUID_KEY] = str(aux_item.uuid)
    image[AUX_MASK_INITIALIZED_KEY] = False
    make_image_opaque(image)
    image.update()
    aux_item.image = image
    aux_item.image_name = image.name
    aux_item.dirty = False
    return image


def create_aux_mask(
    project: Any,
    *,
    role: str = "CUSTOM",
    name: str = "Custom Mask",
    fill_value: float = 0.0,
) -> Any:
    item = project.aux_masks.add()
    item.uuid = new_uuid()
    item.name = str(name).strip() or "Custom Mask"
    item.role = str(role)
    create_aux_mask_image(project, item, fill_value=fill_value)
    project.active_aux_mask_index = len(project.aux_masks) - 1
    project.active_aux_mask_uuid = item.uuid
    return item


def remove_aux_mask_image(project: Any, aux_item: Any) -> None:
    image = resolve_aux_mask_image(project, aux_item)
    if image is not None:
        bpy.data.images.remove(image)


def fill_aux_mask_image(image: bpy.types.Image, value: float) -> None:
    import numpy as np

    width, height = image.size[:]
    rgba = np.ones((height, width, 4), dtype=np.float32)
    rgba[..., :3] = max(0.0, min(1.0, float(value)))
    write_image_rgba(image, rgba)
    image[AUX_MASK_INITIALIZED_KEY] = True


def copy_image_channel_to_aux(
    source: bpy.types.Image,
    destination: bpy.types.Image,
    component: str,
) -> None:
    """Copy one source component with bilinear resize; never mutate the source."""

    import numpy as np

    source_width, source_height = map(int, source.size[:])
    target_width, target_height = map(int, destination.size[:])
    if source_width <= 0 or source_height <= 0:
        raise ValueError("The source image has no readable pixels")
    flat = np.empty(source_width * source_height * 4, dtype=np.float32)
    source.pixels.foreach_get(flat)
    rgba = flat.reshape(source_height, source_width, 4)
    component = str(component).upper()
    if component == "LUMINANCE":
        plane = (
            rgba[..., 0] * np.float32(0.2126)
            + rgba[..., 1] * np.float32(0.7152)
            + rgba[..., 2] * np.float32(0.0722)
        )
    else:
        indices = {"R": 0, "G": 1, "B": 2, "A": 3}
        if component not in indices:
            raise ValueError(f"Unknown image component: {component}")
        plane = rgba[..., indices[component]]
    if (source_width, source_height) != (target_width, target_height):
        xs = np.clip(
            (np.arange(target_width, dtype=np.float64) + 0.5) * source_width / target_width - 0.5,
            0.0,
            source_width - 1.0,
        )
        ys = np.clip(
            (np.arange(target_height, dtype=np.float64) + 0.5) * source_height / target_height - 0.5,
            0.0,
            source_height - 1.0,
        )
        x0 = np.floor(xs).astype(np.intp)
        y0 = np.floor(ys).astype(np.intp)
        x1 = np.minimum(x0 + 1, source_width - 1)
        y1 = np.minimum(y0 + 1, source_height - 1)
        wx = (xs - x0).astype(np.float32)[None, :]
        wy = (ys - y0).astype(np.float32)[:, None]
        top = plane[y0[:, None], x0[None, :]] * (1.0 - wx) + plane[y0[:, None], x1[None, :]] * wx
        bottom = plane[y1[:, None], x0[None, :]] * (1.0 - wx) + plane[y1[:, None], x1[None, :]] * wx
        plane = top * (1.0 - wy) + bottom * wy
    output = np.ones((target_height, target_width, 4), dtype=np.float32)
    output[..., :3] = np.clip(plane, 0.0, 1.0)[..., None]
    write_image_rgba(destination, output)
    destination[AUX_MASK_INITIALIZED_KEY] = True


def create_project_images(project: Any, source: bpy.types.Image | None = None) -> None:
    for value in DEFAULT_ANGLES:
        angle_item = project.angles.add()
        angle_item.uuid = new_uuid()
        angle_item.angle = value
        angle_item.side = str(getattr(project, "authoring_side", "RIGHT"))
        display = create_angle_layer_image(
            project.uuid, angle_item.uuid, value, int(project.resolution), DISPLAY_ROLE,
            side=angle_item.side,
        )
        base = create_angle_layer_image(
            project.uuid, angle_item.uuid, value, int(project.resolution), BASE_ROLE,
            side=angle_item.side,
        )
        coverage = create_angle_layer_image(
            project.uuid, angle_item.uuid, value, int(project.resolution), COVERAGE_ROLE,
            side=angle_item.side,
        )
        angle_item.display_image = display
        angle_item.display_image_name = display.name
        angle_item.base_image = base
        angle_item.base_image_name = base.name
        angle_item.coverage_image = coverage
        angle_item.coverage_image_name = coverage.name
        if source is not None:
            copy_image_pixels(source, display)
            copy_image_pixels(source, base)
    project.active_angle_index = min(
        range(len(project.angles)),
        key=lambda index: abs(float(project.angles[index].angle) - 45.0),
        default=0,
    )
    project.active_angle_uuid = (
        project.angles[project.active_angle_index].uuid if project.angles else ""
    )
    project.active_side = str(getattr(project, "authoring_side", "RIGHT"))
    if project.angles:
        project.seek_angle = float(project.angles[project.active_angle_index].angle)
        project.review_angle = project.seek_angle
    project.schema_version = SCHEMA_VERSION


def copy_image_pixels(
    source: bpy.types.Image,
    destination: bpy.types.Image,
    *,
    grayscale: bool = True,
) -> None:
    """Copy an RGBA image, using nearest-neighbour resampling when required."""
    import numpy as np

    source_width, source_height = source.size[:]
    target_width, target_height = destination.size[:]
    pixels = np.empty(source_width * source_height * 4, dtype=np.float32)
    source.pixels.foreach_get(pixels)
    rgba = pixels.reshape(source_height, source_width, 4)
    if (source_width, source_height) != (target_width, target_height):
        xs = np.minimum((np.arange(target_width) * source_width // target_width), source_width - 1)
        ys = np.minimum((np.arange(target_height) * source_height // target_height), source_height - 1)
        rgba = rgba[ys[:, None], xs[None, :]]
    result = np.empty((target_height, target_width, 4), dtype=np.float32)
    if grayscale:
        luminance = rgba[..., :3].mean(axis=2)
        result[..., :3] = luminance[..., None]
    else:
        result[..., :3] = rgba[..., :3]
    result[..., 3] = 1.0
    destination.pixels.foreach_set(result.ravel())
    destination[IMAGE_REVISION_KEY] = int(destination.get(IMAGE_REVISION_KEY, 0)) + 1
    make_image_opaque(destination)
    destination.update()


def _write_blender_rows(image: bpy.types.Image, blender_rows: Any) -> None:
    """Commit a contiguous bottom-up float32 buffer to one Blender image."""

    import numpy as np

    pixels = np.ascontiguousarray(blender_rows, dtype=np.float32)
    width, height = image.size[:]
    if pixels.shape != (height, width, 4):
        raise ValueError(
            f"RGBA shape {pixels.shape} does not match image {(height, width, 4)}"
        )
    # Blender keeps a separate projection-paint buffer for the active canvas.
    # Python pixel writes to a packed canvas can otherwise be overwritten by
    # that stale buffer. Detach only for the duration of this atomic write.
    detached = []
    for scene in bpy.data.scenes:
        image_paint = getattr(getattr(scene, "tool_settings", None), "image_paint", None)
        if image_paint is not None and getattr(image_paint, "canvas", None) == image:
            detached.append(image_paint)
            image_paint.canvas = None
    try:
        image.pixels.foreach_set(pixels.ravel())
        image[IMAGE_REVISION_KEY] = int(image.get(IMAGE_REVISION_KEY, 0)) + 1
        make_image_opaque(image)
        image.update()
        # Material/Image Editor uploads can reload a generated Image from
        # generated_color. Keep the current display pixels in its packed source
        # before reattaching an active projection-paint canvas.
        if str(image.get(ROLE_KEY, "")) == DISPLAY_ROLE:
            image.pack()
    finally:
        for image_paint in detached:
            image_paint.canvas = image


def write_image_rgba(image: bpy.types.Image, rgba: Any) -> None:
    """Write a top-down float RGBA array and keep the Blender image opaque."""

    import numpy as np

    values = np.asarray(rgba, dtype=np.float32)
    width, height = image.size[:]
    if values.shape != (height, width, 4):
        raise ValueError(
            f"RGBA shape {values.shape} does not match image {(height, width, 4)}"
        )
    blender_rows = np.flip(values, axis=0).copy()
    blender_rows[..., 3] = 1.0
    _write_blender_rows(image, blender_rows)


def write_image_rgba8(image: bpy.types.Image, rgba: Any) -> None:
    """Write a top-down RGBA8 array without retaining a float32 copy."""

    import numpy as np

    values = np.asarray(rgba)
    width, height = image.size[:]
    if values.shape != (height, width, 4):
        raise ValueError(
            f"RGBA shape {values.shape} does not match image {(height, width, 4)}"
        )
    if values.dtype != np.uint8:
        raise TypeError("RGBA8 image data must use uint8")
    blender_rows = np.flip(values, axis=0).astype(np.float32)
    blender_rows *= 1.0 / 255.0
    blender_rows[..., 3] = 1.0
    _write_blender_rows(image, blender_rows)


def initialize_normal_sweep(project: Any) -> None:
    """Rasterize a lightweight face-normal preview into all angle masks.

    Initialization is intentionally capped at 512 samples per axis and then
    nearest-neighbour upscaled.  It is an authoring head start, not a shading
    bake, and remains responsive on production meshes and 4K projects.
    """
    import numpy as np
    from mathutils import Matrix, Vector

    obj = project.target_object
    if obj is None or obj.type != "MESH":
        raise ValueError("Normal sweep requires a mesh target")
    mesh = obj.data
    uv_layer = mesh.uv_layers.get(project.uv_map_name)
    if uv_layer is None:
        raise ValueError("Normal sweep requires the project UV map")
    size = min(512, int(project.resolution))
    normal_buffer = np.zeros((size, size, 3), dtype=np.float32)
    occupied = np.zeros((size, size), dtype=np.bool_)
    mesh.calc_loop_triangles()
    for triangle in mesh.loop_triangles:
        polygon = mesh.polygons[triangle.polygon_index]
        if polygon.material_index != int(project.material_slot_index):
            continue
        uv = [uv_layer.data[index].uv for index in triangle.loops]
        x = np.asarray([point.x * (size - 1) for point in uv], dtype=np.float64)
        y = np.asarray([(1.0 - point.y) * (size - 1) for point in uv], dtype=np.float64)
        x0, x1 = max(0, int(np.floor(x.min()))), min(size - 1, int(np.ceil(x.max())))
        y0, y1 = max(0, int(np.floor(y.min()))), min(size - 1, int(np.ceil(y.max())))
        if x1 < x0 or y1 < y0:
            continue
        denominator = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(denominator) <= 1e-12:
            continue
        grid_y, grid_x = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
        weight0 = ((y[1] - y[2]) * (grid_x - x[2]) + (x[2] - x[1]) * (grid_y - y[2])) / denominator
        weight1 = ((y[2] - y[0]) * (grid_x - x[2]) + (x[0] - x[2]) * (grid_y - y[2])) / denominator
        weight2 = 1.0 - weight0 - weight1
        inside = (weight0 >= -1e-6) & (weight1 >= -1e-6) & (weight2 >= -1e-6)
        if not np.any(inside):
            continue
        region = normal_buffer[y0 : y1 + 1, x0 : x1 + 1]
        region[inside] = tuple(polygon.normal)
        occupied[y0 : y1 + 1, x0 : x1 + 1][inside] = True

    axis_vectors = {
        "NEG_X": Vector((-1.0, 0.0, 0.0)),
        "POS_X": Vector((1.0, 0.0, 0.0)),
        "NEG_Y": Vector((0.0, -1.0, 0.0)),
        "POS_Y": Vector((0.0, 1.0, 0.0)),
        "NEG_Z": Vector((0.0, 0.0, -1.0)),
        "POS_Z": Vector((0.0, 0.0, 1.0)),
    }
    forward = axis_vectors.get(str(project.forward_axis), Vector(project.forward_vector)).normalized()
    up = axis_vectors.get(str(project.up_axis), Vector(project.up_vector)).normalized()
    if abs(forward.dot(up)) > 0.999:
        raise ValueError("Forward and Up axes must be different and perpendicular")
    project.forward_vector = forward
    project.up_vector = up
    masks = []
    for angle_item in project.angles:
        signed_angle = float(angle_item.angle)
        if str(getattr(angle_item, "side", "RIGHT")) == "LEFT":
            signed_angle = -signed_angle
        light = Matrix.Rotation(np.deg2rad(signed_angle), 3, up) @ forward
        dots = normal_buffer @ np.asarray(light, dtype=np.float32)
        masks.append(~occupied | (dots >= 0.0))
    masks = np.stack(masks, axis=0)
    angle_values = np.asarray([float(item.angle) for item in project.angles])
    sides = np.asarray([str(getattr(item, "side", "RIGHT")) for item in project.angles])
    for side in ("RIGHT", "LEFT"):
        indices = np.flatnonzero(sides == side)
        indices = indices[np.argsort(angle_values[indices], kind="stable")]
        for closer, farther in zip(indices[:-1], indices[1:]):
            masks[farther] |= masks[closer]

    resolution = int(project.resolution)
    if size != resolution:
        sample = np.minimum(np.arange(resolution) * size // resolution, size - 1)
        masks = masks[:, sample[:, None], sample[None, :]]
    for angle_item, mask in zip(project.angles, masks):
        display = resolve_display_image(project, angle_item)
        base = resolve_base_image(project, angle_item)
        if display is None:
            continue
        rgba = np.ones((resolution, resolution, 4), dtype=np.float32)
        rgba[..., :3] = mask[..., None]
        write_image_rgba(display, rgba)
        if base is not None:
            write_image_rgba(base, rgba)


def ensure_palette(scene: bpy.types.Scene) -> bpy.types.Palette:
    palette = bpy.data.palettes.get(PALETTE_NAME) or bpy.data.palettes.new(PALETTE_NAME)
    if len(palette.colors) < 2:
        while len(palette.colors):
            palette.colors.remove(palette.colors[-1])
        black = palette.colors.new()
        black.color = (0.0, 0.0, 0.0)
        white = palette.colors.new()
        white.color = (1.0, 1.0, 1.0)
    image_paint = scene.tool_settings.image_paint
    try:
        image_paint.palette = palette
    except (AttributeError, TypeError):
        pass
    return palette


def sync_canvas(context: bpy.types.Context, project: Any | None = None) -> bpy.types.Image | None:
    project = project or active_project(context.scene)
    if project is None:
        return None
    image = None
    try:
        studio_active = False
        studio_window_pointer = 0
        editing_aux_mask_uuid = ""
        try:
            from . import studio

            studio_active = bool(studio.is_studio_active(context, str(project.uuid)))
            session = studio.active_session(context)
            if session is not None:
                studio_window_pointer = int(session.window_pointer)
                editing_aux_mask_uuid = str(
                    getattr(session, "editing_aux_mask_uuid", "")
                )
        except (ImportError, AttributeError, ReferenceError, RuntimeError):
            # ``studio`` is optional while the add-on is registering or
            # unregistering, so canvas synchronization remains defensive.
            studio_active = False
        if editing_aux_mask_uuid:
            image = resolve_aux_mask_image(
                project, aux_mask_for_uuid(project, editing_aux_mask_uuid)
            )
        if image is None:
            angle_item = active_angle(project)
            image = resolve_display_image(project, angle_item) if angle_item is not None else None
        if image is None:
            return None
        if studio_active:
            context.scene.tool_settings.image_paint.mode = "IMAGE"
        context.scene.tool_settings.image_paint.canvas = image
    except (AttributeError, TypeError, RuntimeError):
        pass
    ensure_palette(context.scene)
    # Keep every visible Image Editor in lockstep.  Screens not currently shown
    # are intentionally untouched so user workspaces do not acquire hidden state.
    windows = getattr(context.window_manager, "windows", ())
    for window in windows:
        if studio_active and studio_window_pointer and window.as_pointer() != studio_window_pointer:
            continue
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = image
            area.tag_redraw()
    if bool(getattr(project, "preview_enabled", False)):
        try:
            from .preview import set_preview_image

            set_preview_image(project, image)
        except (ImportError, RuntimeError, ValueError):
            pass
    if bool(getattr(project, "onion_enabled", False)):
        try:
            from .live_preview import update_onion_preview

            update_onion_preview(project)
        except (ImportError, ReferenceError, RuntimeError, ValueError):
            pass
    return image


def image_mask(image: bpy.types.Image) -> Any:
    """Return a top-down boolean mask (white/light is true)."""
    import numpy as np

    width, height = image.size[:]
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    blender_rows = flat.reshape(height, width, 4)
    return np.flip(blender_rows[..., 0] >= 0.5, axis=0).copy()


def image_rgba(image: bpy.types.Image) -> Any:
    """Return a writable top-down float32 RGBA copy of a Blender image."""
    import numpy as np

    width, height = image.size[:]
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return np.flip(flat.reshape(height, width, 4), axis=0).copy()


def image_channel_u16(
    image: bpy.types.Image,
    channel_index: int = 0,
    *,
    rows_per_chunk: int = 64,
) -> Any:
    """Read one image channel as a compact top-down UNORM16 plane.

    Blender exposes image pixels as an interleaved RGBA buffer, so one full
    temporary float buffer is unavoidable during ``foreach_get``. Converting
    into the final 2-byte plane in small row chunks prevents that RGBA buffer
    (and a float64 quantization copy) from being retained for the whole export.
    """

    import numpy as np

    index = int(channel_index)
    if index < 0 or index > 3:
        raise ValueError("image channel index must be between zero and three")
    chunk_rows = max(1, int(rows_per_chunk))
    width, height = map(int, image.size[:])
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    source = flat.reshape(height, width, 4)[..., index]
    output = np.empty((height, width), dtype=np.uint16)
    for top_start in range(0, height, chunk_rows):
        top_end = min(height, top_start + chunk_rows)
        # Blender stores rows bottom-up. Reverse only this block while copying
        # it into the top-down export plane.
        values = np.asarray(
            source[height - top_end : height - top_start][::-1],
            dtype=np.float64,
        )
        if not np.all(np.isfinite(values)):
            raise ValueError(f"Image {image.name!r} contains non-finite pixels")
        np.clip(values, 0.0, 1.0, out=values)
        values *= 65535.0
        values += 0.5
        np.floor(values, out=values)
        output[top_start:top_end] = values
    return output


def rgba_to_u8(rgba: Any) -> Any:
    """Quantize normalized RGBA values to the schema's RGBA8 storage."""

    import numpy as np

    values = np.asarray(rgba)
    if values.ndim != 3 or values.shape[2] != 4:
        raise ValueError(f"RGBA data must have shape (height, width, 4), got {values.shape}")
    if values.dtype == np.uint8:
        return np.array(values, copy=True, order="C")
    return np.rint(np.clip(values, 0.0, 1.0) * 255.0).astype(np.uint8)


def image_rgba8(image: bpy.types.Image) -> Any:
    """Return a compact top-down RGBA8 copy with bounded conversion memory."""

    import numpy as np

    from .pixel_buffer import blender_float_rgba_to_top_down_u8

    width, height = image.size[:]
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    return blender_float_rgba_to_top_down_u8(flat, width, height)


def capture_paint_snapshot(project: Any) -> None:
    angle_item = active_angle(project)
    image = resolve_display_image(project, angle_item) if angle_item is not None else None
    if image is None:
        raise ValueError("The active angle image is missing")
    _PAINT_SNAPSHOTS[str(project.uuid)] = image_rgba(image)


def capture_interactive_paint_snapshot(
    project: Any,
    *,
    include_coverage: bool = False,
) -> None:
    """Capture one active key for sparse Studio undo and optional coverage.

    Only the selected display image is read on the normal path. Boundary
    projects additionally capture coverage so their generated layer remains
    separate from hand paint. No other angle is touched.
    """

    angle_item = active_angle(project)
    display = resolve_display_image(project, angle_item) if angle_item is not None else None
    coverage = (
        resolve_coverage_image(project, angle_item)
        if include_coverage and angle_item is not None
        else None
    )
    if angle_item is None or display is None or (include_coverage and coverage is None):
        raise ValueError("The active angle paint layers are incomplete")
    _INTERACTIVE_PAINT_SNAPSHOTS[str(project.uuid)] = (
        str(angle_item.uuid),
        str(display.name),
        image_rgba8(display),
        str(coverage.name) if coverage is not None else "",
        image_rgba8(coverage) if coverage is not None else None,
    )


def capture_aux_paint_snapshot(project: Any, mask_uuid: str) -> None:
    item = aux_mask_for_uuid(project, mask_uuid)
    image = resolve_aux_mask_image(project, item)
    if item is None or image is None:
        raise ValueError("The selected additional mask is missing")
    _AUX_PAINT_SNAPSHOTS[str(project.uuid)] = (
        str(item.uuid),
        str(image.name),
        image_rgba8(image),
    )


def consume_aux_paint_snapshot(project: Any) -> tuple[str, str, Any] | None:
    return _AUX_PAINT_SNAPSHOTS.pop(str(project.uuid), None)


def discard_aux_paint_snapshot(project: Any | None = None) -> None:
    if project is None:
        _AUX_PAINT_SNAPSHOTS.clear()
    else:
        _AUX_PAINT_SNAPSHOTS.pop(str(project.uuid), None)


def consume_interactive_paint_snapshot(project: Any) -> tuple[str, str, Any, str, Any | None] | None:
    return _INTERACTIVE_PAINT_SNAPSHOTS.pop(str(getattr(project, "uuid", "")), None)


def discard_interactive_paint_snapshot(project: Any) -> None:
    _INTERACTIVE_PAINT_SNAPSHOTS.pop(str(getattr(project, "uuid", "")), None)


def has_paint_snapshot(project: Any) -> bool:
    """Return whether an explicit propagation snapshot is pending."""

    return str(getattr(project, "uuid", "")) in _PAINT_SNAPSHOTS


def consume_paint_snapshot(project: Any) -> Any | None:
    snapshot = _PAINT_SNAPSHOTS.pop(str(project.uuid), None)
    if snapshot is not None:
        return snapshot
    # The script-facing explicit "Propagate" operator cannot read display
    # alpha as coverage because the display is intentionally opaque. Encode
    # coverage as a harmless G-channel difference while leaving R (the
    # baseline mask consumed by that operator) unchanged.
    angle_item = active_angle(project)
    display = resolve_display_image(project, angle_item) if angle_item is not None else None
    coverage = resolve_coverage_image(project, angle_item) if angle_item is not None else None
    if display is None or coverage is None:
        return None
    current = image_rgba(display)
    covered = image_mask(coverage)
    if not covered.any():
        return None
    synthetic = current.copy()
    synthetic[..., 1][covered] = 1.0 - synthetic[..., 1][covered]
    return synthetic


def discard_paint_snapshot(project: Any | None = None) -> None:
    if project is None:
        _PAINT_SNAPSHOTS.clear()
        _INTERACTIVE_PAINT_SNAPSHOTS.clear()
        _AUX_PAINT_SNAPSHOTS.clear()
    else:
        _PAINT_SNAPSHOTS.pop(str(project.uuid), None)
        _INTERACTIVE_PAINT_SNAPSHOTS.pop(str(project.uuid), None)
        _AUX_PAINT_SNAPSHOTS.pop(str(project.uuid), None)


def _project_angle_for_image(image: bpy.types.Image) -> tuple[Any | None, Any | None]:
    project_uuid = str(image.get(PROJECT_UUID_KEY, ""))
    angle_uuid = str(image.get(ANGLE_UUID_KEY, ""))
    if not project_uuid or not angle_uuid:
        return None, None
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(getattr(project, "uuid", "")) != project_uuid:
                continue
            for angle_item in getattr(project, "angles", ()):
                if str(getattr(angle_item, "uuid", "")) == angle_uuid:
                    return project, angle_item
    return None, None


def _coverage_for_display(image: bpy.types.Image) -> bpy.types.Image | None:
    project, angle_item = _project_angle_for_image(image)
    if project is None or angle_item is None:
        return None
    return resolve_coverage_image(project, angle_item)


def _base_for_display(image: bpy.types.Image) -> bpy.types.Image | None:
    project, angle_item = _project_angle_for_image(image)
    if project is None or angle_item is None:
        return None
    return resolve_base_image(project, angle_item)


def coverage_mask(image_or_display: bpy.types.Image) -> Any:
    """Return top-down override coverage for a coverage or display image."""

    role = str(image_or_display.get(ROLE_KEY, ""))
    coverage = (
        image_or_display
        if role == COVERAGE_ROLE
        else _coverage_for_display(image_or_display)
    )
    if coverage is None:
        import numpy as np

        width, height = image_or_display.size[:]
        return np.zeros((height, width), dtype=np.bool_)
    return image_mask(coverage)


def materialize_effective_coverage(
    project: Any,
    angle_items: Iterable[Any] | None = None,
) -> int:
    """Persist visible paint/base differences before an explicit rebuild.

    Everyday Studio strokes stay entirely on Blender's native canvas so they
    do not read and rewrite every angle image on pen release. Before Rebake or
    enabling the optional Boundary layer, this function converts those visible
    corrections into the persistent coverage layer in one deliberate batch.
    """

    import numpy as np

    added = 0
    for angle_item in tuple(angle_items if angle_items is not None else project.angles):
        display = resolve_display_image(project, angle_item)
        base = resolve_base_image(project, angle_item)
        coverage = resolve_coverage_image(project, angle_item)
        if display is None or base is None or coverage is None:
            raise ValueError("An angle paint layer is missing")
        display_rgba = image_rgba(display)
        base_rgba = image_rgba(base)
        coverage_rgba = image_rgba(coverage)
        stored = coverage_rgba[..., 0] >= 0.5
        visible_delta = np.any(
            np.abs(display_rgba[..., :3] - base_rgba[..., :3]) > (0.5 / 255.0),
            axis=2,
        )
        effective = stored | visible_delta
        newly_added = effective & ~stored
        if not np.any(newly_added):
            continue
        coverage_rgba[..., :3][newly_added] = 1.0
        coverage_rgba[..., 3] = 1.0
        write_image_rgba(coverage, coverage_rgba)
        added += int(np.count_nonzero(newly_added))
    return added


def _mark_coverage(coverage: bpy.types.Image, region: Any, value: bool) -> None:
    import numpy as np

    rgba = image_rgba(coverage)
    changed = np.asarray(region, dtype=np.bool_)
    if changed.shape != rgba.shape[:2]:
        raise ValueError("Override region must match the image dimensions")
    rgba[..., :3][changed] = 1.0 if value else 0.0
    rgba[..., 3] = 1.0
    write_image_rgba(coverage, rgba)


def write_mask_overrides(
    image: bpy.types.Image,
    mask: Any,
    override: Any,
    coverage_image: bpy.types.Image | None = None,
) -> None:
    """Write display RGB and mark its separate persistent coverage layer."""
    import numpy as np

    rgba = image_rgba(image)
    binary = np.asarray(mask, dtype=np.bool_)
    footprint = np.asarray(override, dtype=np.bool_)
    if binary.shape != rgba.shape[:2] or footprint.shape != binary.shape:
        raise ValueError("Mask and override arrays must match the image dimensions")
    rgba[..., :3][footprint] = binary[footprint, None].astype(np.float32)
    rgba[..., 3] = 1.0
    write_image_rgba(image, rgba)
    coverage_image = coverage_image or _coverage_for_display(image)
    if coverage_image is not None:
        _mark_coverage(coverage_image, footprint, True)


def restore_image_region(image: bpy.types.Image, snapshot: Any, region: Any) -> None:
    import numpy as np

    rgba = image_rgba(image)
    previous = np.asarray(snapshot, dtype=np.float32)
    changed = np.asarray(region, dtype=np.bool_)
    if previous.shape != rgba.shape or changed.shape != rgba.shape[:2]:
        raise ValueError("Snapshot and region must match the image dimensions")
    rgba[changed] = previous[changed]
    rgba[..., 3] = 1.0
    write_image_rgba(image, rgba)


def mark_override_region(
    image: bpy.types.Image,
    region: Any,
    coverage_image: bpy.types.Image | None = None,
) -> None:
    coverage_image = coverage_image or _coverage_for_display(image)
    if coverage_image is None:
        return
    _mark_coverage(coverage_image, region, True)


def project_mask_stack(project: Any) -> tuple[Any, Any]:
    """Return a signed-stack adapter over the side-local authoring keys.

    A separate LEFT zero-degree key cannot be represented by a signed stack, so the
    RIGHT zero is used as its shared zero.  New generation code should consume
    :func:`project_side_stack` instead.
    """
    import numpy as np

    entries = []
    has_right_zero = any(
        str(getattr(item, "side", "RIGHT")) == "RIGHT"
        and math.isclose(float(item.angle), 0.0, abs_tol=1.0e-7)
        for item in project.angles
    )
    for angle_item in project.angles:
        side = str(getattr(angle_item, "side", "RIGHT"))
        local_angle = abs(float(angle_item.angle))
        if side == "LEFT" and math.isclose(local_angle, 0.0, abs_tol=1.0e-7) and has_right_zero:
            continue
        image = resolve_display_image(project, angle_item)
        if image is None:
            raise ValueError(f"Mask image is missing for {angle_item.angle:+g} degrees")
        signed = -local_angle if side == "LEFT" else local_angle
        entries.append((signed, image_mask(image)))
    if not entries:
        raise ValueError("Project has no angle masks")
    entries.sort(key=lambda entry: entry[0])
    values = [entry[0] for entry in entries]
    masks = [entry[1] for entry in entries]
    shape = masks[0].shape
    if any(mask.shape != shape for mask in masks[1:]):
        raise ValueError("All angle masks must use the same resolution")
    return np.stack(masks, axis=0), np.asarray(values, dtype=np.float64)


def project_side_stack(project: Any, side: str) -> tuple[Any, Any]:
    """Return one 0..90 side without collapsing its independent zero key."""

    import numpy as np

    selected = sorted(
        (item for item in project.angles if str(getattr(item, "side", "RIGHT")) == side),
        key=lambda item: float(item.angle),
    )
    if not selected:
        raise ValueError(f"Project has no {side.title()} angle keys")
    masks = []
    angles = []
    for item in selected:
        image = resolve_display_image(project, item)
        if image is None:
            raise ValueError(f"Missing {side.title()} mask at {float(item.angle):g} degrees")
        masks.append(image_mask(image))
        angles.append(float(item.angle))
    return np.stack(masks, axis=0), np.asarray(angles, dtype=np.float64)


def project_side_export_layers(project: Any, side: str) -> tuple[Any, Any, Any, Any]:
    """Copy display, angle, base, and coverage arrays for one export lane.

    This function is deliberately main-thread-only: all ``bpy`` image access is
    completed here, leaving repair, symmetry, EDT and PNG encoding free to run
    in a worker without retaining Blender data-block references.
    """

    import numpy as np

    selected = sorted(
        (item for item in project.angles if str(getattr(item, "side", "RIGHT")) == side),
        key=lambda item: float(item.angle),
    )
    if not selected:
        raise ValueError(f"Project has no {side.title()} angle keys")
    display_layers = []
    base_layers = []
    coverage_layers = []
    angles = []
    expected_shape = None
    for item in selected:
        angle = float(item.angle)
        display = resolve_display_image(project, item)
        base = resolve_base_image(project, item)
        coverage = resolve_coverage_image(project, item)
        if display is None:
            raise ValueError(f"Missing {side.title()} mask at {angle:g} degrees")
        if base is None:
            raise ValueError(f"Missing {side.title()} base mask at {angle:g} degrees")
        if coverage is None:
            raise ValueError(f"Missing {side.title()} override coverage at {angle:g} degrees")
        display_mask = image_mask(display)
        base_mask = image_mask(base)
        coverage_values = coverage_mask(coverage)
        if expected_shape is None:
            expected_shape = display_mask.shape
        if (
            display_mask.shape != expected_shape
            or base_mask.shape != expected_shape
            or coverage_values.shape != expected_shape
        ):
            raise ValueError("All export layers must use the same resolution")
        display_layers.append(display_mask)
        base_layers.append(base_mask)
        coverage_layers.append(coverage_values)
        angles.append(angle)
    return (
        np.ascontiguousarray(np.stack(display_layers, axis=0), dtype=np.bool_),
        np.asarray(angles, dtype=np.float64),
        np.ascontiguousarray(np.stack(base_layers, axis=0), dtype=np.bool_),
        np.ascontiguousarray(np.stack(coverage_layers, axis=0), dtype=np.bool_),
    )


def project_side_stacks(project: Any, *, island_pairs: Any | None = None) -> tuple[Any, Any, Any, Any]:
    """Return canonical Right and Left 0..90 stacks for preview/export.

    A mirrored project stores only the authoring lane.  The opposite lane is
    derived every time this function is called, so paint, rebake, preview and
    export can never observe a stale "generated side".
    """

    import numpy as np

    available = {
        str(getattr(item, "side", "RIGHT"))
        for item in getattr(project, "angles", ())
    }
    linked = bool(getattr(project, "mirror_enabled", True)) and str(
        getattr(project, "symmetry_mode", "AUTO")
    ) != "INDEPENDENT"
    if not linked:
        right, right_angles = project_side_stack(project, "RIGHT")
        left, left_angles = project_side_stack(project, "LEFT")
        return right, right_angles, left, left_angles

    author_side = str(getattr(project, "authoring_side", "RIGHT"))
    if author_side not in available:
        author_side = "RIGHT" if "RIGHT" in available else "LEFT"
    source, source_angles = project_side_stack(project, author_side)
    mode_name = str(getattr(project, "symmetry_mode", "AUTO"))
    if mode_name == "AUTO":
        mode_name = str(getattr(project, "symmetry_candidate", "TEXTURE_MIRROR"))
    mode_map = {
        "OVERLAPPED_UV": "OVERLAPPED",
        "TEXTURE_MIRROR": "TEXTURE_MIRROR",
        "ISLAND_PAIR": "ISLAND_PAIR",
    }
    selected_mode = mode_map.get(mode_name, "TEXTURE_MIRROR")
    from .symmetry import mirror_side_stack

    mirrored = mirror_side_stack(source, selected_mode, island_pairs=island_pairs)
    mirrored_angles = np.array(source_angles, copy=True)
    if author_side == "RIGHT":
        return source, source_angles, mirrored, mirrored_angles
    return mirrored, mirrored_angles, source, source_angles


def update_threshold_preview(project: Any, rgba16: Any) -> bpy.types.Image:
    import numpy as np

    rgba = np.asarray(rgba16, dtype=np.uint16)
    height, width = rgba.shape[:2]
    image = getattr(project, "generated_image", None)
    if image is None or tuple(image.size[:]) != (width, height):
        if image is not None and image.get(PROJECT_UUID_KEY) == project.uuid:
            bpy.data.images.remove(image)
        image = bpy.data.images.new(
            f"QSDF Threshold {project.uuid[:8]}", width=width, height=height, alpha=True, float_buffer=True
        )
        _tag_image(image, project.uuid, role=THRESHOLD_ROLE)
        project.generated_image = image
    # Core/PNG rows are top-down; Blender's image buffer starts at the bottom.
    normalized = np.flip(rgba, axis=0).astype(np.float32) / 65535.0
    image.pixels.foreach_set(normalized.ravel())
    image.update()
    return image


def clear_export_adjustment_preview(project: Any) -> None:
    image = getattr(project, "export_adjustment_image", None)
    if image is not None:
        active = active_angle(project)
        replacement = resolve_display_image(project, active) if active is not None else None
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != "IMAGE_EDITOR":
                    continue
                for space in area.spaces:
                    if hasattr(space, "image") and space.image == image:
                        space.image = replacement
                        area.tag_redraw()
        for material in bpy.data.materials:
            if not material.use_nodes or material.node_tree is None:
                continue
            for node in material.node_tree.nodes:
                if hasattr(node, "image") and node.image == image:
                    node.image = replacement
    if image is not None and image.get(PROJECT_UUID_KEY) == project.uuid:
        bpy.data.images.remove(image)
    project.export_adjustment_image = None


def update_export_adjustment_preview(project: Any, heatmap: Any) -> bpy.types.Image | None:
    """Store a transient red heatmap for Advanced export review."""

    import numpy as np

    values = np.asarray(heatmap, dtype=np.float32)
    if values.ndim != 2 or any(size <= 0 for size in values.shape):
        raise ValueError("Export adjustment heatmap must be a non-empty 2D array")
    values = np.clip(values, 0.0, 1.0)
    if not np.any(values > 0.0):
        clear_export_adjustment_preview(project)
        return None
    height, width = values.shape
    image = getattr(project, "export_adjustment_image", None)
    if image is None or tuple(image.size[:]) != (width, height):
        if image is not None and image.get(PROJECT_UUID_KEY) == project.uuid:
            bpy.data.images.remove(image)
        image = bpy.data.images.new(
            f"QSDF Export Adjustments {project.uuid[:8]}",
            width=width,
            height=height,
            alpha=True,
            float_buffer=False,
        )
        _tag_image(image, project.uuid, role=EXPORT_ADJUSTMENT_ROLE)
        project.export_adjustment_image = image
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[..., 0] = values
    rgba[..., 1] = values * 0.08
    rgba[..., 3] = 1.0
    image.pixels.foreach_set(np.flip(rgba, axis=0).ravel())
    image.update()
    return image


def clear_image_alpha(image: bpy.types.Image) -> None:
    """Clear separate overrides and restore the current base RGB."""
    import numpy as np

    coverage = _coverage_for_display(image)
    base = _base_for_display(image)
    if coverage is None or base is None:
        raise ValueError("The paint override layers are incomplete")
    overridden = coverage_mask(coverage)
    rgba = image_rgba(image)
    base_rgba = image_rgba(base)
    rgba[..., :3][overridden] = base_rgba[..., :3][overridden]
    rgba[..., 3] = 1.0
    write_image_rgba(image, rgba)
    _mark_coverage(coverage, np.ones(overridden.shape, dtype=np.bool_), False)


def remove_project_images(project: Any) -> None:
    for image in tuple(bpy.data.images):
        if image.get(PROJECT_UUID_KEY) == project.uuid:
            bpy.data.images.remove(image)


_TOPOLOGY_MODIFIERS = {
    "ARRAY", "BEVEL", "BOOLEAN", "BUILD", "DECIMATE", "EDGE_SPLIT", "MASK",
    "MIRROR", "MULTIRES", "NODES", "REMESH", "SCREW", "SKIN", "SOLIDIFY",
    "SUBSURF", "TRIANGULATE", "WELD", "WIREFRAME",
}


def validate_project(project: Any, *, include_monotonic: bool = True) -> tuple[list[str], list[str], Any | None]:
    errors: list[str] = []
    warnings: list[str] = []
    report = None
    if int(getattr(project, "schema_version", -1)) != SCHEMA_VERSION:
        errors.append(f"Project schema {SCHEMA_VERSION} is required.")
    obj = project.target_object
    if obj is None or obj.type != "MESH":
        errors.append("Target must be a mesh object.")
    else:
        if obj.library is not None or obj.data.library is not None:
            errors.append("Linked objects and mesh data are read-only; make a local copy.")
        if not (0 <= int(project.material_slot_index) < len(obj.material_slots)):
            errors.append("Material slot is unavailable.")
        uv_layer = obj.data.uv_layers.get(project.uv_map_name)
        if uv_layer is None:
            errors.append("UV map is unavailable.")
        else:
            target_loops = (
                loop_index
                for polygon in obj.data.polygons
                if polygon.material_index == int(project.material_slot_index)
                for loop_index in polygon.loop_indices
            )
            outside = any(
                uv_layer.data[index].uv.x < -1e-5
                or uv_layer.data[index].uv.x > 1.00001
                or uv_layer.data[index].uv.y < -1e-5
                or uv_layer.data[index].uv.y > 1.00001
                for index in target_loops
            )
            if outside:
                errors.append("UV coordinates must stay in the 0-1 tile.")
        if obj.data.polygons and not any(
            polygon.material_index == int(project.material_slot_index) for polygon in obj.data.polygons
        ):
            errors.append("No faces use the selected material slot.")
        modifiers = [modifier.name for modifier in obj.modifiers if modifier.show_viewport and modifier.type in _TOPOLOGY_MODIFIERS]
        if modifiers:
            warnings.append("Topology-changing modifiers are active: " + ", ".join(modifiers))
    side_values: dict[str, list[float]] = {"RIGHT": [], "LEFT": []}
    for item in project.angles:
        side = str(getattr(item, "side", "RIGHT"))
        value = float(item.angle)
        if side not in side_values or value < -1.0e-4 or value > 90.0001:
            errors.append("Angle keys must belong to Right/Left and stay in 0..90 degrees.")
            continue
        side_values[side].append(value)
    for side, values in side_values.items():
        if not values:
            continue
        if len(values) != len({round(value, 5) for value in values}):
            errors.append(f"{side.title()} angle keys must be unique.")
        if not any(math.isclose(value, 0.0, abs_tol=1.0e-4) for value in values):
            errors.append(f"{side.title()} keys require a 0 degree endpoint.")
        if not any(math.isclose(value, 90.0, abs_tol=1.0e-4) for value in values):
            errors.append(f"{side.title()} keys require a 90 degree endpoint.")
    if not side_values["RIGHT"] and not side_values["LEFT"]:
        errors.append("Project has no angle keys.")
    for angle_item in project.angles:
        image = resolve_display_image(project, angle_item)
        if image is None:
            errors.append(f"Missing mask at {angle_item.angle:+g} degrees.")
        elif tuple(image.size[:]) != (int(project.resolution), int(project.resolution)):
            errors.append(f"Mask size differs at {angle_item.angle:+g} degrees.")
        if resolve_base_image(project, angle_item) is None:
            errors.append(f"Missing base mask at {angle_item.angle:+g} degrees.")
        if resolve_coverage_image(project, angle_item) is None:
            errors.append(f"Missing override coverage at {angle_item.angle:+g} degrees.")
    aux_uuids: set[str] = set()
    aux_roles: dict[str, int] = {}
    for item in getattr(project, "aux_masks", ()):
        mask_uuid = str(getattr(item, "uuid", ""))
        role = str(getattr(item, "role", ""))
        if not mask_uuid or mask_uuid in aux_uuids:
            errors.append("Additional masks require unique UUIDs.")
        aux_uuids.add(mask_uuid)
        aux_roles[role] = aux_roles.get(role, 0) + 1
        image = resolve_aux_mask_image(project, item)
        if image is None:
            errors.append(f"Missing additional mask: {getattr(item, 'name', role)}.")
        elif tuple(image.size[:]) != (int(project.resolution), int(project.resolution)):
            errors.append(f"Additional mask size differs: {getattr(item, 'name', role)}.")
    for role, label in (("SDF_AREA", "SDF Area"), ("SHADOW_STRENGTH", "Shadow Strength")):
        if aux_roles.get(role, 0) != 1:
            errors.append(f"Project requires exactly one {label} mask.")
    if include_monotonic and not errors:
        from .core import validate_monotonic

        masks, angles = project_mask_stack(project)
        report = validate_monotonic(masks, angles)
        project.has_violations = not report.is_valid
        for angle_item in project.angles:
            angle_item.has_violation = False
        for first, second, _count in report.offending_transitions:
            for angle_item in project.angles:
                signed = abs(float(angle_item.angle))
                if str(getattr(angle_item, "side", "RIGHT")) == "LEFT":
                    signed = -signed
                if math.isclose(signed, first) or math.isclose(signed, second):
                    angle_item.has_violation = True
        if not report.is_valid:
            errors.append(
                f"Monotonic Guard: {report.violation_pixel_count} pixels violate "
                f"{report.violation_count} adjacent transitions."
            )
    project.validation_message = "OK" if not errors else "\n".join(errors)
    project.warning_message = "\n".join(warnings)
    project.diagnostic_message = "\n".join(errors + warnings)
    return errors, warnings, report


def repair_project_references(scene: bpy.types.Scene) -> None:
    for project in getattr(scene, "quick_sdf_projects", ()):
        for angle_item in project.angles:
            resolve_display_image(project, angle_item)
            resolve_base_image(project, angle_item)
            resolve_coverage_image(project, angle_item)
        for aux_item in getattr(project, "aux_masks", ()):
            resolve_aux_mask_image(project, aux_item)


def cleanup_export_adjustment_previews() -> None:
    """Remove derived review images after lifecycle changes or unregister."""

    # A Studio Image Editor showing the heatmap is deliberately in VIEW mode.
    # Restore its real paint canvas before the derived image is unlinked.
    try:
        from .studio import leave_export_adjustment_review

        leave_export_adjustment_review()
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        pass

    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            try:
                clear_export_adjustment_preview(project)
                project.export_adjustment_pixel_count = 0
                project.export_adjustment_sample_count = 0
                project.export_adjustment_protected_pixel_count = 0
            except (AttributeError, ReferenceError, TypeError):
                pass
    for image in tuple(bpy.data.images):
        if image.get(ROLE_KEY) == EXPORT_ADJUSTMENT_ROLE:
            try:
                for screen in bpy.data.screens:
                    for area in screen.areas:
                        if area.type != "IMAGE_EDITOR":
                            continue
                        for space in area.spaces:
                            if hasattr(space, "image") and space.image == image:
                                space.image = None
                bpy.data.images.remove(image)
            except (ReferenceError, RuntimeError):
                pass


@persistent
def _load_or_undo_post(_unused: Any) -> None:
    _PAINT_SNAPSHOTS.clear()
    _INTERACTIVE_PAINT_SNAPSHOTS.clear()
    _AUX_PAINT_SNAPSHOTS.clear()
    try:
        from .operators import clear_histories

        clear_histories()
    except ImportError:
        pass
    cleanup_export_adjustment_previews()
    try:
        from .live_preview import invalidate

        invalidate()
    except ImportError:
        pass
    for scene in bpy.data.scenes:
        repair_project_references(scene)
    context = bpy.context
    if getattr(context, "scene", None) is not None:
        try:
            sync_canvas(context)
        except (AttributeError, ReferenceError, RuntimeError):
            pass


@persistent
def _save_project_images(_unused: Any) -> None:
    """Refresh packed authoring pixels immediately before Blender saves."""

    # Export review is derived data.  Never serialize a potentially 4K
    # heatmap into the artist's source-of-truth blend file.
    cleanup_export_adjustment_previews()
    persistent_roles = {DISPLAY_ROLE, BASE_ROLE, COVERAGE_ROLE, AUX_MASK_ROLE}
    for image in tuple(bpy.data.images):
        if image.get(ROLE_KEY) not in persistent_roles or not image.get(PROJECT_UUID_KEY):
            continue
        try:
            image.pack()
        except (OSError, ReferenceError, RuntimeError):
            # Let Blender's save continue; diagnostics on next load will expose
            # a genuinely missing layer without risking a corrupt half-save.
            pass


def _watched_source_ids(project: Any) -> set[Any]:
    obj = getattr(project, "target_object", None)
    if obj is None:
        return set()
    result = {obj, getattr(obj, "data", None)}
    shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    if shape_keys is not None:
        result.add(shape_keys)
    for modifier in getattr(obj, "modifiers", ()):
        target = getattr(modifier, "object", None)
        if target is not None:
            result.add(target)
            data = getattr(target, "data", None)
            if data is not None:
                result.add(data)
    result.discard(None)
    return result


@persistent
def _depsgraph_base_update(scene: bpy.types.Scene, depsgraph: Any) -> None:
    updates = tuple(getattr(depsgraph, "updates", ()))
    if not updates:
        return
    for project in getattr(scene, "quick_sdf_projects", ()):
        if str(getattr(project, "uuid", "")) in _BASE_BAKE_UUIDS:
            continue
        if not str(getattr(project, "base_signature", "")):
            continue
        obj = getattr(project, "target_object", None)
        data = getattr(obj, "data", None)
        shape_keys = getattr(data, "shape_keys", None)
        modifier_ids = _watched_source_ids(project) - {obj, data, shape_keys}
        for update in updates:
            identifier = getattr(update, "id", None)
            identifier = getattr(identifier, "original", identifier)
            geometry = bool(getattr(update, "is_updated_geometry", False))
            transform = bool(getattr(update, "is_updated_transform", False))
            if identifier is data or identifier is shape_keys:
                _PENDING_BASE_SIGNATURES.add(str(project.uuid))
                break
            if identifier is obj and geometry:
                _PENDING_BASE_SIGNATURES.add(str(project.uuid))
                break
            if identifier in modifier_ids and (geometry or transform):
                _PENDING_BASE_SIGNATURES.add(str(project.uuid))
                break
    if _PENDING_BASE_SIGNATURES and not bpy.app.timers.is_registered(_deferred_base_check):
        bpy.app.timers.register(_deferred_base_check, first_interval=0.25)


@persistent
def _frame_base_update(scene: bpy.types.Scene, _depsgraph: Any) -> None:
    for project in getattr(scene, "quick_sdf_projects", ()):
        if str(getattr(project, "uuid", "")) not in _BASE_BAKE_UUIDS and str(
            getattr(project, "base_signature", "")
        ):
            project.base_needs_update = True


def _deferred_base_check() -> None:
    pending = set(_PENDING_BASE_SIGNATURES)
    _PENDING_BASE_SIGNATURES.difference_update(pending)
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(getattr(project, "uuid", "")) in pending:
                try:
                    refresh_base_staleness(project, scene)
                except (AttributeError, ReferenceError, RuntimeError):
                    pass
    return None


def register_runtime() -> None:
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _load_or_undo_post not in handlers:
            handlers.append(_load_or_undo_post)
    if _save_project_images not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_save_project_images)
    if _depsgraph_base_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_base_update)
    if _frame_base_update not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_frame_base_update)


def unregister_runtime() -> None:
    cleanup_export_adjustment_previews()
    _PAINT_SNAPSHOTS.clear()
    _INTERACTIVE_PAINT_SNAPSHOTS.clear()
    try:
        from .operators import clear_histories

        clear_histories()
    except ImportError:
        pass
    _BASE_BAKE_UUIDS.clear()
    _PENDING_BASE_SIGNATURES.clear()
    if bpy.app.timers.is_registered(_deferred_base_check):
        bpy.app.timers.unregister(_deferred_base_check)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        while _load_or_undo_post in handlers:
            handlers.remove(_load_or_undo_post)
    while _save_project_images in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_save_project_images)
    while _depsgraph_base_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_base_update)
    while _frame_base_update in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_base_update)


CLASSES: tuple[type, ...] = ()

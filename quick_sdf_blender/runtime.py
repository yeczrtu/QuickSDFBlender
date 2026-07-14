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

from .bitplane import (
    BitplaneError,
    BitplaneRole,
    DecodedBitplaneCache,
    encode_bitplane,
    inspect_bitplane_header,
)
from .model import DEFAULT_ANGLES, MAX_KEYS_PER_SIDE, SCHEMA_VERSION


PROJECT_UUID_KEY = "quick_sdf_project_uuid"
ANGLE_UUID_KEY = "quick_sdf_angle_uuid"
ROLE_KEY = "quick_sdf_role"
IMAGE_REVISION_KEY = "quick_sdf_revision"
AUX_MASK_UUID_KEY = "quick_sdf_aux_mask_uuid"
AUX_MASK_INITIALIZED_KEY = "quick_sdf_aux_mask_initialized"
DISPLAY_ROLE = "angle_display"
PROVISIONAL_DISPLAY_ROLE = "provisional_display"
AUX_MASK_ROLE = "aux_mask"
THRESHOLD_ROLE = "threshold_preview"
EXPORT_ADJUSTMENT_ROLE = "export_adjustment_preview"
PALETTE_NAME = "Quick SDF Light Shadow"
_PAINT_SNAPSHOTS: dict[str, Any] = {}
_INTERACTIVE_PAINT_SNAPSHOTS: dict[str, tuple[str, str, Any, str, Any | None]] = {}
_AUX_PAINT_SNAPSHOTS: dict[str, tuple[str, str, Any]] = {}
_BASE_BAKE_UUIDS: set[str] = set()
_PENDING_BASE_SIGNATURES: set[str] = set()
BASE_BITPLANE_KEY = "_qsdf_base_bitplane"
COVERAGE_BITPLANE_KEY = "_qsdf_coverage_bitplane"
_BITPLANE_CACHE = DecodedBitplaneCache()
_GRAY_CACHE_NAME = ""
_GRAY_CACHE_IDENTITY = 0
_GRAY_CACHE_REVISION = -1
_GRAY_CACHE_VALUE: Any | None = None
_GRAY_UPLOAD_BUFFER: Any | None = None
_GRAY_UPLOAD_CACHE_LIMIT = 2048 * 2048 * 4


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
    if mesh.vertices:
        import numpy as np

        coordinates = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coordinates)
        digest.update(coordinates.tobytes(order="C"))
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


def _bitplane_spec(role: BitplaneRole) -> tuple[str, str]:
    return (
        (BASE_BITPLANE_KEY, "base_revision")
        if role is BitplaneRole.BASE
        else (COVERAGE_BITPLANE_KEY, "coverage_revision")
    )


def bitplane_blob(angle_item: Any, role: BitplaneRole | str) -> bytes:
    semantic_role = BitplaneRole[role.upper()] if isinstance(role, str) else role
    property_name, _revision_name = _bitplane_spec(semantic_role)
    value = angle_item.get(property_name)
    if not isinstance(value, bytes):
        raise BitplaneError(f"Missing {semantic_role.name.title()} bitplane")
    return value


def _read_bitplane(angle_item: Any, role: BitplaneRole) -> Any:
    property_name, revision_name = _bitplane_spec(role)
    blob = angle_item.get(property_name)
    if not isinstance(blob, bytes):
        raise BitplaneError(f"Missing {role.name.title()} bitplane")
    identifier = f"{getattr(angle_item, 'uuid', '')}:{role.name}"
    return _BITPLANE_CACHE.decode(
        identifier,
        int(getattr(angle_item, revision_name, 0)),
        blob,
        expected_role=role,
    )


def base_mask(angle_item: Any) -> Any:
    """Return the immutable top-down Base bitplane for an angle key."""

    return _read_bitplane(angle_item, BitplaneRole.BASE)


def coverage_mask(image_or_angle: Any) -> Any:
    """Return the immutable top-down override bitplane for a key or Display."""

    angle_item = image_or_angle
    if isinstance(image_or_angle, bpy.types.Image):
        _project, angle_item = _project_angle_for_image(image_or_angle)
    if angle_item is None:
        raise BitplaneError("The Display image is not attached to an angle key")
    return _read_bitplane(angle_item, BitplaneRole.COVERAGE)


def _set_bitplane(angle_item: Any, role: BitplaneRole, plane: Any) -> None:
    import numpy as np

    values = np.ascontiguousarray(plane, dtype=np.bool_)
    if values.ndim != 2:
        raise ValueError("Bitplane values must be a two-dimensional mask")
    property_name, revision_name = _bitplane_spec(role)
    # Blender 5.1's in-place assignment path for an existing byte-string
    # IDProperty treats embedded NUL bytes as a C string terminator. Recreate
    # the property so arbitrary binary payloads remain byte-exact.
    if property_name in angle_item:
        del angle_item[property_name]
    angle_item[property_name] = encode_bitplane(values, role)
    setattr(angle_item, revision_name, int(getattr(angle_item, revision_name, 0)) + 1)
    _BITPLANE_CACHE.invalidate(f"{getattr(angle_item, 'uuid', '')}:{role.name}")


def set_base_mask(angle_item: Any, plane: Any) -> None:
    _set_bitplane(angle_item, BitplaneRole.BASE, plane)


def set_coverage_mask(angle_item: Any, plane: Any) -> None:
    _set_bitplane(angle_item, BitplaneRole.COVERAGE, plane)


def copy_angle_bitplanes(source: Any, destination: Any) -> None:
    """Copy compact Base/Coverage payloads without decoding them."""

    for role in (BitplaneRole.BASE, BitplaneRole.COVERAGE):
        property_name, revision_name = _bitplane_spec(role)
        if property_name in destination:
            del destination[property_name]
        destination[property_name] = bytes(bitplane_blob(source, role))
        setattr(destination, revision_name, int(getattr(source, revision_name, 0)))


def bitplane_revision_token(angle_item: Any, role: BitplaneRole | str) -> tuple[Any, ...]:
    semantic_role = BitplaneRole[role.upper()] if isinstance(role, str) else role
    _property_name, revision_name = _bitplane_spec(semantic_role)
    blob = bitplane_blob(angle_item, semantic_role)
    header = inspect_bitplane_header(blob)
    return (
        int(getattr(angle_item, revision_name, 0)),
        header.width,
        header.height,
        header.crc32,
        len(blob),
    )


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
    if role != DISPLAY_ROLE:
        raise ValueError(f"Unknown angle image role: {role!r}")
    stem = project_uuid.split("-", 1)[0]
    label = "Mask"
    name = f"QSDF {label} {stem} {side.title()} {angle:04.1f}"
    image = bpy.data.images.new(name, width=resolution, height=resolution, alpha=True, float_buffer=False)
    image.generated_type = "BLANK"
    image.generated_color = (1.0, 1.0, 1.0, 1.0)
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
        try:
            from .residency import forget_image

            forget_image(image)
        except ImportError:
            pass
        bpy.data.images.remove(image)


def fill_aux_mask_image(image: bpy.types.Image, value: float) -> None:
    import numpy as np

    width, height = image.size[:]
    quantized = np.uint8(round(max(0.0, min(1.0, float(value))) * 255.0))
    write_image_gray8(image, np.full((height, width), quantized, dtype=np.uint8))
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
    output = np.rint(np.clip(plane, 0.0, 1.0) * 255.0).astype(np.uint8)
    write_image_gray8(destination, output)
    destination[AUX_MASK_INITIALIZED_KEY] = True


def create_project_images(project: Any, source: bpy.types.Image | None = None) -> None:
    import numpy as np

    resolution = int(project.resolution)
    for value in DEFAULT_ANGLES:
        angle_item = project.angles.add()
        angle_item.uuid = new_uuid()
        angle_item.angle = value
        angle_item.side = str(getattr(project, "authoring_side", "RIGHT"))
        display = create_angle_layer_image(
            project.uuid, angle_item.uuid, value, resolution, DISPLAY_ROLE,
            side=angle_item.side,
        )
        angle_item.display_image = display
        angle_item.display_image_name = display.name
        if source is not None:
            copy_image_pixels(source, display)
            base = image_mask(display)
        else:
            base = np.ones((resolution, resolution), dtype=np.bool_)
        set_base_mask(angle_item, base)
        set_coverage_mask(
            angle_item, np.zeros((resolution, resolution), dtype=np.bool_)
        )
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
    try:
        from .residency import mark_changed

        mark_changed(destination)
    except (ImportError, ReferenceError, RuntimeError):
        pass


def _write_blender_flat(image: bpy.types.Image, pixels: Any) -> None:
    """Commit one contiguous bottom-up flat float32 RGBA buffer."""

    import numpy as np

    flat = np.asarray(pixels)
    width, height = image.size[:]
    if flat.dtype != np.float32 or flat.ndim != 1 or not flat.flags.c_contiguous:
        raise ValueError("Blender RGBA upload must be contiguous flat float32")
    if flat.size != height * width * 4:
        raise ValueError(
            f"RGBA upload has {flat.size} samples; expected {height * width * 4}"
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
        image.pixels.foreach_set(flat)
        image[IMAGE_REVISION_KEY] = int(image.get(IMAGE_REVISION_KEY, 0)) + 1
        make_image_opaque(image)
        image.update()
        # Material/Image Editor uploads can reload a generated Image from
        # generated_color. Keep the current display pixels in its packed source
        # before reattaching an active projection-paint canvas.
        role = str(image.get(ROLE_KEY, ""))
        if role in {DISPLAY_ROLE, AUX_MASK_ROLE}:
            from .residency import mark_changed

            mark_changed(image, synchronous=bool(detached))
        elif role == PROVISIONAL_DISPLAY_ROLE:
            image.pack()
    finally:
        for image_paint in detached:
            image_paint.canvas = image


def _write_blender_rows(image: bpy.types.Image, blender_rows: Any) -> None:
    """Commit a contiguous bottom-up float32 buffer to one Blender image."""

    import numpy as np

    pixels = np.ascontiguousarray(blender_rows, dtype=np.float32)
    width, height = image.size[:]
    if pixels.shape != (height, width, 4):
        raise ValueError(
            f"RGBA shape {pixels.shape} does not match image {(height, width, 4)}"
        )
    _write_blender_flat(image, pixels.reshape(-1))


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
        if display is None:
            continue
        write_image_gray8(display, mask.astype(np.uint8) * np.uint8(255))
        set_base_mask(angle_item, mask)


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
        from .residency import activate

        activate(project, image)
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
    try:
        from .residency import reconcile_project

        reconcile_project(project, image)
    except (ImportError, ReferenceError, RuntimeError):
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


def _clear_gray_cache(image_name: str = "") -> None:
    global _GRAY_CACHE_NAME, _GRAY_CACHE_IDENTITY, _GRAY_CACHE_REVISION, _GRAY_CACHE_VALUE

    if image_name and image_name != _GRAY_CACHE_NAME:
        return
    _GRAY_CACHE_NAME = ""
    _GRAY_CACHE_IDENTITY = 0
    _GRAY_CACHE_REVISION = -1
    _GRAY_CACHE_VALUE = None


def invalidate_gray_cache(image_or_name: Any | None = None) -> None:
    """Release the session gray cache after lifecycle or datablock changes."""

    if image_or_name is None:
        _clear_gray_cache()
        return
    _clear_gray_cache(str(getattr(image_or_name, "name", image_or_name)))


def _image_identity(image: bpy.types.Image) -> int:
    try:
        # Blender may immediately recycle an RNA memory address after an Image
        # is removed. ID.session_uid remains unique for the whole file session
        # and therefore distinguishes same-name replacement datablocks.
        session_uid = int(getattr(image, "session_uid", 0))
        return session_uid if session_uid else int(image.as_pointer())
    except (AttributeError, ReferenceError, RuntimeError):
        return 0


def _is_paint_canvas(image: bpy.types.Image) -> bool:
    for scene in bpy.data.scenes:
        paint = getattr(getattr(scene, "tool_settings", None), "image_paint", None)
        if paint is not None and getattr(paint, "canvas", None) == image:
            return True
    return False


def _store_gray_cache(image: bpy.types.Image, values: Any) -> None:
    global _GRAY_CACHE_NAME, _GRAY_CACHE_IDENTITY, _GRAY_CACHE_REVISION, _GRAY_CACHE_VALUE

    import numpy as np

    _GRAY_CACHE_NAME = str(image.name)
    _GRAY_CACHE_IDENTITY = _image_identity(image)
    _GRAY_CACHE_REVISION = int(image.get(IMAGE_REVISION_KEY, 0))
    _GRAY_CACHE_VALUE = np.array(values, copy=True, order="C")


def cache_image_gray8(image: bpy.types.Image, values: Any) -> None:
    """Retain a finalized native-paint result for the next Active snapshot."""

    import numpy as np

    gray = np.asarray(values)
    width, height = map(int, image.size[:])
    if gray.shape != (height, width) or gray.dtype != np.uint8:
        raise ValueError("Display gray cache must be a matching uint8 image")
    if _is_paint_canvas(image):
        _store_gray_cache(image, gray)


def image_gray8(image: bpy.types.Image, *, use_cache: bool = False) -> Any:
    """Return Display gray8 without constructing a full RGBA8 intermediate."""

    import numpy as np

    revision = int(image.get(IMAGE_REVISION_KEY, 0))
    if (
        use_cache
        and str(image.name) == _GRAY_CACHE_NAME
        and _image_identity(image) == _GRAY_CACHE_IDENTITY
        and revision == _GRAY_CACHE_REVISION
        and _GRAY_CACHE_VALUE is not None
        and _GRAY_CACHE_VALUE.shape == (int(image.size[1]), int(image.size[0]))
    ):
        return np.array(_GRAY_CACHE_VALUE, copy=True, order="C")
    from .pixel_buffer import blender_float_rgba_to_top_down_gray8
    from .residency import ensure_loaded

    ensure_loaded(image)
    width, height = map(int, image.size[:])
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    result = blender_float_rgba_to_top_down_gray8(flat, width, height)
    if use_cache:
        _store_gray_cache(image, result)
    return result


def write_image_gray8(image: bpy.types.Image, values: Any) -> None:
    """Expand a top-down uint8 plane into Blender's opaque RGBA canvas."""

    import numpy as np

    gray = np.asarray(values)
    width, height = map(int, image.size[:])
    if gray.shape != (height, width) or gray.dtype != np.uint8:
        raise ValueError("Display history plane must be a matching uint8 image")
    from .pixel_buffer import top_down_gray8_to_blender_float_rgba

    global _GRAY_UPLOAD_BUFFER
    required = height * width * 4
    if required <= _GRAY_UPLOAD_CACHE_LIMIT:
        if (
            _GRAY_UPLOAD_BUFFER is None
            or getattr(_GRAY_UPLOAD_BUFFER, "size", 0) != required
        ):
            _GRAY_UPLOAD_BUFFER = np.empty(required, dtype=np.float32)
        upload = _GRAY_UPLOAD_BUFFER
    else:
        upload = np.empty(required, dtype=np.float32)
    top_down_gray8_to_blender_float_rgba(gray, out=upload)
    _write_blender_flat(image, upload)
    # Propagation writes several non-active keys.  Retaining the final one
    # would evict the useful pre-stroke Active snapshot and force a full float
    # read on the next stroke.
    cache_image_gray8(image, gray)


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
    coverage = coverage_mask(angle_item).copy() if include_coverage and angle_item is not None else None
    if angle_item is None or display is None:
        raise ValueError("The active angle paint layers are incomplete")
    capture_interactive_paint_snapshot_values(
        project,
        angle_uuid=str(angle_item.uuid),
        display=display,
        coverage=coverage,
    )


def capture_interactive_paint_snapshot_values(
    project: Any,
    *,
    angle_uuid: str,
    display: bpy.types.Image,
    coverage: Any | None,
) -> None:
    """Capture a paint canvas which is not yet a persistent angle key.

    Adaptive angles stay session-only until a native stroke changes an actual
    8-bit Display value.  Keeping this snapshot API independent of
    ``project.angles`` lets Blender paint the prepared Image without briefly
    inserting a key into the timeline first.
    """

    import numpy as np

    before = image_gray8(display, use_cache=True)
    coverage_copy = None
    coverage_uuid = ""
    if coverage is not None:
        coverage_copy = np.ascontiguousarray(coverage, dtype=np.bool_).copy()
        if coverage_copy.shape != before.shape:
            raise ValueError("The prepared angle Coverage does not match its Display")
        coverage_uuid = str(angle_uuid)
    _INTERACTIVE_PAINT_SNAPSHOTS[str(project.uuid)] = (
        str(angle_uuid),
        str(display.name),
        before,
        coverage_uuid,
        coverage_copy,
    )


def capture_aux_paint_snapshot(project: Any, mask_uuid: str) -> None:
    item = aux_mask_for_uuid(project, mask_uuid)
    image = resolve_aux_mask_image(project, item)
    if item is None or image is None:
        raise ValueError("The selected additional mask is missing")
    _AUX_PAINT_SNAPSHOTS[str(project.uuid)] = (
        str(item.uuid),
        str(image.name),
        image_gray8(image, use_cache=True),
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
    if display is None or angle_item is None:
        return None
    current = image_rgba(display)
    covered = coverage_mask(angle_item)
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


def _coverage_for_display(image: bpy.types.Image) -> Any | None:
    project, angle_item = _project_angle_for_image(image)
    if project is None or angle_item is None:
        return None
    return angle_item


def _base_for_display(image: bpy.types.Image) -> Any | None:
    project, angle_item = _project_angle_for_image(image)
    if project is None or angle_item is None:
        return None
    return angle_item


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
        if display is None:
            raise ValueError("An angle paint layer is missing")
        display_values = image_rgba8(display)[..., 0]
        base_values = base_mask(angle_item)
        stored = coverage_mask(angle_item)
        visible_delta = display_values != np.where(base_values, 255, 0)
        effective = stored | visible_delta
        newly_added = effective & ~stored
        if not np.any(newly_added):
            continue
        set_coverage_mask(angle_item, effective)
        added += int(np.count_nonzero(newly_added))
    return added


def _mark_coverage(angle_item: Any, region: Any, value: bool) -> None:
    import numpy as np

    coverage = np.array(coverage_mask(angle_item), copy=True)
    changed = np.asarray(region, dtype=np.bool_)
    if changed.shape != coverage.shape:
        raise ValueError("Override region must match the image dimensions")
    coverage[changed] = bool(value)
    set_coverage_mask(angle_item, coverage)


def write_mask_overrides(
    image: bpy.types.Image,
    mask: Any,
    override: Any,
    coverage_item: Any | None = None,
) -> None:
    """Write display RGB and mark its separate persistent coverage layer."""
    import numpy as np

    gray = image_gray8(image)
    binary = np.asarray(mask, dtype=np.bool_)
    footprint = np.asarray(override, dtype=np.bool_)
    if binary.shape != gray.shape or footprint.shape != binary.shape:
        raise ValueError("Mask and override arrays must match the image dimensions")
    gray[footprint] = binary[footprint].astype(np.uint8) * np.uint8(255)
    write_image_gray8(image, gray)
    coverage_item = coverage_item or _coverage_for_display(image)
    if coverage_item is not None:
        _mark_coverage(coverage_item, footprint, True)


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
    coverage_item: Any | None = None,
) -> None:
    coverage_item = coverage_item or _coverage_for_display(image)
    if coverage_item is None:
        return
    _mark_coverage(coverage_item, region, True)


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
        if display is None:
            raise ValueError(f"Missing {side.title()} mask at {angle:g} degrees")
        display_mask = image_mask(display)
        base_values = base_mask(item)
        coverage_values = coverage_mask(item)
        if expected_shape is None:
            expected_shape = display_mask.shape
        if (
            display_mask.shape != expected_shape
            or base_values.shape != expected_shape
            or coverage_values.shape != expected_shape
        ):
            raise ValueError("All export layers must use the same resolution")
        display_layers.append(display_mask)
        base_layers.append(base_values)
        coverage_layers.append(coverage_values)
        angles.append(angle)
    return (
        np.ascontiguousarray(np.stack(display_layers, axis=0), dtype=np.bool_),
        np.asarray(angles, dtype=np.float64),
        np.ascontiguousarray(np.stack(base_layers, axis=0), dtype=np.bool_),
        np.ascontiguousarray(np.stack(coverage_layers, axis=0), dtype=np.bool_),
    )


def insert_display_bit(
    image: bpy.types.Image,
    bit_index: int,
    out: Any,
    *,
    rows_per_chunk: int = 64,
) -> None:
    """Insert one Display threshold directly into an ABI-7 uint16 bit field."""

    import numpy as np

    from .residency import ensure_loaded

    destination = np.asarray(out)
    width, height = map(int, image.size[:])
    if destination.shape != (height, width) or destination.dtype != np.uint16:
        raise ValueError("Packed Display destination must match the image as uint16")
    index = int(bit_index)
    if not 0 <= index < 16:
        raise ValueError("Packed Display bit index must be in 0..15")
    ensure_loaded(image)
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    red = flat.reshape(height, width, 4)[..., 0]
    bit = np.uint16(1 << index)
    chunk = max(1, int(rows_per_chunk))
    for top_start in range(0, height, chunk):
        top_end = min(height, top_start + chunk)
        source = red[height - top_end : height - top_start][::-1]
        light = source >= np.float32(0.5)
        destination[top_start:top_end] |= light.astype(np.uint16) * bit


class PackedLaneSnapshot:
    """Incrementally snapshot one bpy-backed side without retaining ID refs."""

    def __init__(self, project: Any, side: str) -> None:
        import numpy as np

        self.project_uuid = str(getattr(project, "uuid", ""))
        self.side = str(side).upper()
        selected = sorted(
            (
                item
                for item in getattr(project, "angles", ())
                if str(getattr(item, "side", "RIGHT")) == self.side
            ),
            key=lambda item: float(getattr(item, "angle", 0.0)),
        )
        if not 2 <= len(selected) <= 16:
            raise ValueError(f"{self.side.title()} export requires 2..16 keys")
        angles = np.asarray([float(item.angle) for item in selected], dtype=np.float64)
        if (
            not np.isclose(angles[0], 0.0, atol=1.0e-7, rtol=0.0)
            or not np.isclose(angles[-1], 90.0, atol=1.0e-7, rtol=0.0)
            or np.any(np.diff(angles) <= 1.0e-7)
        ):
            raise ValueError(f"{self.side.title()} export keys must span 0..90 uniquely")
        resolution = int(getattr(project, "resolution", 0))
        if resolution <= 0:
            raise ValueError("Project resolution is invalid")
        self.entries = tuple(
            (
                str(item.uuid),
                str(getattr(item, "display_image_name", "")),
                float(item.angle),
            )
            for item in selected
        )
        self.angles = angles
        shape = (resolution, resolution)
        self.display_bits = np.zeros(shape, dtype=np.uint16)
        self.base_bits = np.zeros(shape, dtype=np.uint16)
        self.coverage_bits = np.zeros(shape, dtype=np.uint16)
        self.index = 0

    @property
    def done(self) -> bool:
        return self.index >= len(self.entries)

    @property
    def count(self) -> int:
        return len(self.entries)

    def step(self, project: Any) -> bool:
        """Snapshot one key and return whether the lane is complete."""

        if self.done:
            return True
        if str(getattr(project, "uuid", "")) != self.project_uuid:
            raise RuntimeError("Export project changed during snapshot")
        uuid_value, _image_name, _angle = self.entries[self.index]
        item = next(
            (
                candidate
                for candidate in getattr(project, "angles", ())
                if str(getattr(candidate, "uuid", "")) == uuid_value
            ),
            None,
        )
        if item is None:
            raise RuntimeError("An angle key was removed during export snapshot")
        image = resolve_display_image(project, item)
        if image is None:
            raise ValueError(f"Missing {self.side.title()} Display at {float(item.angle):g} degrees")
        insert_display_bit(image, self.index, self.display_bits)
        from .bitplane import BitplaneRole, insert_bitplane_into_uint16

        insert_bitplane_into_uint16(
            bitplane_blob(item, BitplaneRole.BASE),
            self.index,
            self.base_bits,
            expected_role=BitplaneRole.BASE,
        )
        insert_bitplane_into_uint16(
            bitplane_blob(item, BitplaneRole.COVERAGE),
            self.index,
            self.coverage_bits,
            expected_role=BitplaneRole.COVERAGE,
        )
        self.index += 1
        try:
            from .residency import reconcile_project

            active = active_angle(project)
            active_image = resolve_display_image(project, active) if active is not None else None
            reconcile_project(project, active_image)
        except (ImportError, ReferenceError, RuntimeError):
            pass
        return self.done

    def finish(self) -> Any:
        if not self.done:
            raise RuntimeError("Packed lane snapshot is incomplete")
        from .core import PackedLane

        return PackedLane(
            angles=self.angles,
            display_bits=self.display_bits,
            base_bits=self.base_bits,
            coverage_bits=self.coverage_bits,
        )


def project_side_packed_lane(project: Any, side: str) -> Any:
    """Synchronously build a compact lane for script/background callers."""

    snapshot = PackedLaneSnapshot(project, side)
    while not snapshot.done:
        snapshot.step(project)
    return snapshot.finish()


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
    source_height, source_width = rgba.shape[:2]
    if max(source_height, source_width) > 512:
        scale = 512.0 / float(max(source_height, source_width))
        height = max(1, int(round(source_height * scale)))
        width = max(1, int(round(source_width * scale)))
        rows = np.rint(np.linspace(0, source_height - 1, height)).astype(np.intp)
        columns = np.rint(np.linspace(0, source_width - 1, width)).astype(np.intp)
        rgba = rgba[rows[:, None], columns[None, :]]
    height, width = rgba.shape[:2]
    image = getattr(project, "generated_image", None)
    if (
        image is None
        or tuple(image.size[:]) != (width, height)
        or bool(getattr(image, "is_float", False))
    ):
        if image is not None and image.get(PROJECT_UUID_KEY) == project.uuid:
            bpy.data.images.remove(image)
        image = bpy.data.images.new(
            f"QSDF Threshold {project.uuid[:8]}",
            width=width,
            height=height,
            alpha=True,
            float_buffer=False,
        )
        _tag_image(image, project.uuid, role=THRESHOLD_ROLE)
        project.generated_image = image
    preview = ((rgba.astype(np.uint32) + 128) // 257).astype(np.uint8)
    write_image_rgba8(image, preview)
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
    if max(values.shape) > 512:
        block_y = max(1, int(math.ceil(values.shape[0] / 512.0)))
        block_x = max(1, int(math.ceil(values.shape[1] / 512.0)))
        padded_height = int(math.ceil(values.shape[0] / block_y) * block_y)
        padded_width = int(math.ceil(values.shape[1] / block_x) * block_x)
        padded = np.zeros((padded_height, padded_width), dtype=np.float32)
        padded[: values.shape[0], : values.shape[1]] = values
        values = padded.reshape(
            padded_height // block_y,
            block_y,
            padded_width // block_x,
            block_x,
        ).max(axis=(1, 3))
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
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = np.rint(values * 255.0).astype(np.uint8)
    rgba[..., 1] = np.rint(values * (255.0 * 0.08)).astype(np.uint8)
    rgba[..., 3] = 255
    write_image_rgba8(image, rgba)
    return image


def clear_image_alpha(image: bpy.types.Image) -> None:
    """Clear separate overrides and restore the current base RGB."""
    import numpy as np

    angle_item = _coverage_for_display(image)
    if angle_item is None:
        raise ValueError("The paint override layers are incomplete")
    overridden = coverage_mask(angle_item)
    gray = image_gray8(image)
    base_values = base_mask(angle_item)
    gray[overridden] = base_values[overridden].astype(np.uint8) * np.uint8(255)
    write_image_gray8(image, gray)
    set_coverage_mask(angle_item, np.zeros(overridden.shape, dtype=np.bool_))


def remove_project_images(project: Any) -> None:
    for image in tuple(bpy.data.images):
        if image.get(PROJECT_UUID_KEY) == project.uuid:
            try:
                from .residency import forget_image

                forget_image(image)
            except ImportError:
                pass
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
        if len(values) > MAX_KEYS_PER_SIDE:
            errors.append(
                f"{side.title()} has {len(values)} angle keys; the maximum is "
                f"{MAX_KEYS_PER_SIDE}."
            )
    if not side_values["RIGHT"] and not side_values["LEFT"]:
        errors.append("Project has no angle keys.")
    for angle_item in project.angles:
        image = resolve_display_image(project, angle_item)
        if image is None:
            errors.append(f"Missing mask at {angle_item.angle:+g} degrees.")
        elif tuple(image.size[:]) != (int(project.resolution), int(project.resolution)):
            errors.append(f"Mask size differs at {angle_item.angle:+g} degrees.")
        for label, getter in (("base mask", base_mask), ("override coverage", coverage_mask)):
            try:
                plane = getter(angle_item)
                if plane.shape != (int(project.resolution), int(project.resolution)):
                    errors.append(f"{label.title()} size differs at {angle_item.angle:+g} degrees.")
            except (BitplaneError, TypeError, ValueError):
                errors.append(f"Missing or corrupt {label} at {angle_item.angle:+g} degrees.")
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
def _load_pre_cleanup(_unused: Any) -> None:
    """Stop workers before Blender invalidates every referenced datablock."""

    try:
        from .operators import shutdown_bake_job, shutdown_export_job

        shutdown_bake_job(message="Base update cancelled because a file was loaded")
        shutdown_export_job(message="Export cancelled because a file was loaded")
    except (ImportError, ReferenceError, RuntimeError):
        pass


@persistent
def _load_or_undo_post(_unused: Any) -> None:
    global _GRAY_UPLOAD_BUFFER

    _PAINT_SNAPSHOTS.clear()
    _INTERACTIVE_PAINT_SNAPSHOTS.clear()
    _AUX_PAINT_SNAPSHOTS.clear()
    _BITPLANE_CACHE.clear()
    _clear_gray_cache()
    _GRAY_UPLOAD_BUFFER = None
    try:
        from .residency import shutdown

        shutdown()
    except ImportError:
        pass
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
    try:
        from .residency import flush_dirty

        flush_dirty()
    except ImportError:
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


def _queue_base_signature_check(project: Any, scene: bpy.types.Scene) -> None:
    """Debounce ambiguous mode/paint geometry tags into one exact check."""

    uuid_value = str(getattr(project, "uuid", ""))
    if not uuid_value:
        return
    _PENDING_BASE_SIGNATURES.add(uuid_value)
    if bpy.app.background:
        refresh_base_staleness(project, scene)
        _PENDING_BASE_SIGNATURES.discard(uuid_value)
    elif not bpy.app.timers.is_registered(_deferred_base_check):
        bpy.app.timers.register(_deferred_base_check, first_interval=0.05)


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
            # Material/paint/canvas changes also report the Mesh ID, but do
            # not change the evaluated normals used by the guide.
            if (identifier is data or identifier is shape_keys) and geometry:
                _queue_base_signature_check(project, scene)
                break
            if identifier is obj and geometry:
                _queue_base_signature_check(project, scene)
                break
            if identifier in modifier_ids and (geometry or transform):
                project.base_needs_update = True
                break


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
    if _load_pre_cleanup not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre_cleanup)
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
    global _GRAY_UPLOAD_BUFFER

    cleanup_export_adjustment_previews()
    _PAINT_SNAPSHOTS.clear()
    _INTERACTIVE_PAINT_SNAPSHOTS.clear()
    _AUX_PAINT_SNAPSHOTS.clear()
    try:
        from .operators import clear_histories

        clear_histories()
    except ImportError:
        pass
    _BASE_BAKE_UUIDS.clear()
    _PENDING_BASE_SIGNATURES.clear()
    _BITPLANE_CACHE.clear()
    _clear_gray_cache()
    _GRAY_UPLOAD_BUFFER = None
    try:
        from .residency import shutdown

        shutdown()
    except ImportError:
        pass
    if bpy.app.timers.is_registered(_deferred_base_check):
        bpy.app.timers.unregister(_deferred_base_check)
    while _load_pre_cleanup in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre_cleanup)
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

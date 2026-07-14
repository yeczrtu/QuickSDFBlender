# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent RNA data model for Quick SDF Blender.

Only Blender ID pointers and plain RNA values are stored here.  Runtime caches must
key themselves by the UUID fields instead of retaining PropertyGroup instances,
because undo/load can invalidate those Python wrappers.
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import Callable

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


SCHEMA_VERSION = 5
# Eight evenly spaced authoring stages.  The values are intentionally not
# rounded: interpolation and export use the exact normalized position i / 7.
DEFAULT_KEY_ANGLES = tuple(index * 90.0 / 7.0 for index in range(8))
# Public name used by project creation. A linked/mirrored project authors one
# 0..90 degree side; the opposite side is generated live.
DEFAULT_ANGLES = DEFAULT_KEY_ANGLES


SIDE_ITEMS = (
    ("RIGHT", "Right", "Right-light authoring side"),
    ("LEFT", "Left", "Left-light authoring side"),
)


BASE_SOURCE_ITEMS = (
    ("NORMAL_GUIDE", "Normal Guide", "Base masks generated from evaluated mesh normals"),
    ("IMPORTED", "Imported Mask", "Base masks copied from artist-supplied images"),
    ("WHITE", "All Light", "Base masks initialized as all Light"),
)


PACKING_OUTPUT_CHANNELS = ("R", "G", "B", "A")

PACKING_OUTPUT_CHANNEL_ITEMS = (
    ("R", "R", "Red output channel"),
    ("G", "G", "Green output channel"),
    ("B", "B", "Blue output channel"),
    ("A", "A", "Alpha output channel"),
)

PACKING_SOURCE_ITEMS = (
    (
        "RIGHT_THRESHOLD",
        "Right Threshold",
        "Face-shadow threshold authored for light from the character's right",
    ),
    (
        "LEFT_THRESHOLD",
        "Left Threshold",
        "Face-shadow threshold authored for light from the character's left",
    ),
    ("SDF_AREA", "SDF Area", "Angle-independent mask selecting face-SDF shading"),
    (
        "SHADOW_STRENGTH",
        "Shadow Strength",
        "Angle-independent mask controlling face-shadow strength",
    ),
    ("CUSTOM_MASK", "Custom Mask", "A project-local angle-independent custom mask"),
    ("CONSTANT", "Constant", "A uniform value between zero and one"),
)

AUX_MASK_ROLE_ITEMS = (
    ("SDF_AREA", "SDF Area", "White where face-SDF shading is used"),
    ("SHADOW_STRENGTH", "Shadow Strength", "White where face shadow is enabled"),
    ("CUSTOM", "Custom", "A user-defined angle-independent mask"),
)

STANDARD_AUX_MASK_ROLES = ("SDF_AREA", "SHADOW_STRENGTH")


_PACKING_RESET_DEPTH = 0


def _same_rna_value(left, right) -> bool:
    try:
        return int(left.as_pointer()) == int(right.as_pointer())
    except (AttributeError, ReferenceError, TypeError):
        return left is right


def _project_owning_packing_channel(channel):
    """Resolve a child PropertyGroup owner without retaining an RNA wrapper."""

    scene = getattr(channel, "id_data", None)
    projects = getattr(scene, "quick_sdf_projects", ())
    try:
        for project in projects:
            if any(_same_rna_value(item, channel) for item in project.packing_channels):
                return project
    except (AttributeError, ReferenceError, TypeError):
        pass
    return None


def _packing_channel_changed(channel, _context) -> None:
    if _PACKING_RESET_DEPTH:
        return
    project = _project_owning_packing_channel(channel)
    if project is None:
        return
    project.packing_customized = True
    project.packing_revision = int(project.packing_revision) + 1
    project.dirty = True


APPLY_TARGET_ITEMS = (
    ("CURRENT", "Current", "Apply only to the displayed angle"),
    ("TOWARD_FRONT", "Toward Front", "Apply from this angle toward zero degrees"),
    ("TOWARD_SIDE", "Toward Side", "Apply from this angle toward the same-side profile"),
    ("WHOLE_SIDE", "Whole Side", "Apply to every angle on this side, including front"),
    ("BOTH_SIDES", "Both Sides", "Apply symmetrically to both angle ranges"),
)

SYMMETRY_ITEMS = (
    ("INDEPENDENT", "Independent", "Author both sides independently"),
    ("TEXTURE_MIRROR", "Texture Mirror", "Mirror across texture U"),
    ("ISLAND_PAIR", "Island Pair", "Mirror between paired UV islands"),
    ("OVERLAPPED_UV", "Overlapped UV", "Both sides use the same UV coordinates"),
    ("AUTO", "Auto", "Analyze the UVs and suggest a symmetry mode"),
)


def _preview_mode_changed(project, _context) -> None:
    if not bool(getattr(project, "preview_enabled", False)):
        return
    try:
        if str(project.preview_mode) == "TOON":
            from .live_preview import update_seek_preview

            update_seek_preview(project, float(project.seek_angle))
        else:
            from . import runtime
            from .preview import ensure_preview_material

            item = runtime.active_angle(project)
            image = runtime.resolve_display_image(project, item) if item is not None else None
            ensure_preview_material(project, image)
    except (AttributeError, ImportError, ReferenceError, RuntimeError, ValueError):
        pass


def _onion_changed(project, context) -> None:
    try:
        if bool(project.onion_enabled):
            from .live_preview import update_onion_preview

            update_onion_preview(project)
        else:
            from . import runtime

            runtime.sync_canvas(context, project)
    except (AttributeError, ImportError, ReferenceError, RuntimeError, ValueError):
        pass


class QSDFBoundaryPoint(PropertyGroup):
    co: FloatVectorProperty(
        name="UV Coordinate",
        size=2,
        default=(0.5, 0.5),
        min=-16.0,
        max=16.0,
        precision=5,
    )


class QSDFBoundaryKey(PropertyGroup):
    uuid: StringProperty(name="UUID", default="")
    angle: FloatProperty(name="Angle", default=0.0, min=-90.0, max=90.0)
    angle_uuid: StringProperty(name="Angle UUID", default="")
    side: EnumProperty(name="Side", items=SIDE_ITEMS, default="RIGHT")
    is_manual: BoolProperty(name="Manual Key", default=True)
    points: CollectionProperty(type=QSDFBoundaryPoint)


class QSDFBoundaryTrack(PropertyGroup):
    uuid: StringProperty(name="UUID", default="")
    name: StringProperty(name="Name", default="Boundary")
    side: EnumProperty(name="Side", items=SIDE_ITEMS, default="RIGHT")
    closed: BoolProperty(name="Closed", default=False)
    fill_mode: EnumProperty(
        name="Fill",
        items=(
            ("INSIDE", "Inside", "Paint the inside of a closed boundary"),
            ("OUTSIDE", "Outside", "Paint the outside of a closed boundary"),
        ),
        default="INSIDE",
    )
    paint_value: IntProperty(name="Value", default=0, min=0, max=1)
    island_index: IntProperty(name="UV Island", default=-1, min=-1)
    keys: CollectionProperty(type=QSDFBoundaryKey)
    active_key_index: IntProperty(name="Active Key", default=-1, min=-1)
    enabled: BoolProperty(name="Enabled", default=True)


class QSDFAngle(PropertyGroup):
    uuid: StringProperty(name="UUID", default="")
    angle: FloatProperty(name="Angle", default=0.0, min=-90.0, max=90.0)
    side: EnumProperty(name="Side", items=SIDE_ITEMS, default="RIGHT")
    display_image: PointerProperty(name="Display Image", type=bpy.types.Image)
    display_image_name: StringProperty(name="Display Image Name", default="")
    base_image: PointerProperty(name="Base Mask", type=bpy.types.Image)
    base_image_name: StringProperty(name="Base Image Name", default="")
    coverage_image: PointerProperty(name="Override Coverage", type=bpy.types.Image)
    coverage_image_name: StringProperty(name="Coverage Image Name", default="")
    retimed: BoolProperty(name="Retimed", default=False)
    is_manual: BoolProperty(name="Manual", default=False)
    is_generated: BoolProperty(name="Generated", default=True)
    has_violation: BoolProperty(name="Monotonic Violation", default=False)
    dirty: BoolProperty(name="Dirty", default=False)


class QSDFPackingChannel(PropertyGroup):
    """One project-local mapping from a named signal to an RGBA channel."""

    output_channel: EnumProperty(
        name="Output Channel",
        items=PACKING_OUTPUT_CHANNEL_ITEMS,
        default="R",
        update=_packing_channel_changed,
    )
    source_type: EnumProperty(
        name="Source",
        items=PACKING_SOURCE_ITEMS,
        default="CONSTANT",
        update=_packing_channel_changed,
    )
    auxiliary_mask_uuid: StringProperty(
        name="Auxiliary Mask UUID",
        default="",
        update=_packing_channel_changed,
    )
    invert: BoolProperty(name="Invert", default=False, update=_packing_channel_changed)
    constant_value: FloatProperty(
        name="Value",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_packing_channel_changed,
    )


class QSDFAuxMask(PropertyGroup):
    """An angle-independent grayscale image owned by one Quick SDF project."""

    uuid: StringProperty(name="UUID", default="")
    name: StringProperty(name="Name", default="Mask")
    role: EnumProperty(name="Role", items=AUX_MASK_ROLE_ITEMS, default="CUSTOM")
    image: PointerProperty(name="Image", type=bpy.types.Image)
    image_name: StringProperty(name="Image Name", default="")
    revision: IntProperty(
        name="Revision",
        default=0,
        min=0,
        options={"HIDDEN"},
    )
    dirty: BoolProperty(name="Dirty", default=False, options={"HIDDEN"})


class QSDFProject(PropertyGroup):
    uuid: StringProperty(name="UUID", default="")
    schema_version: IntProperty(
        name="Schema Version",
        default=SCHEMA_VERSION,
        min=SCHEMA_VERSION,
        max=SCHEMA_VERSION,
    )
    name: StringProperty(name="Name", default="Quick SDF")

    target_object: PointerProperty(name="Object", type=bpy.types.Object)
    material_slot_index: IntProperty(name="Material Slot", default=0, min=0)
    uv_map_name: StringProperty(name="UV Map", default="")
    resolution: IntProperty(name="Resolution", default=1024, min=512, max=4096)
    forward_vector: FloatVectorProperty(
        name="Forward",
        size=3,
        subtype="DIRECTION",
        default=(0.0, -1.0, 0.0),
    )
    up_vector: FloatVectorProperty(
        name="Up",
        size=3,
        subtype="DIRECTION",
        default=(0.0, 0.0, 1.0),
    )
    forward_axis: EnumProperty(
        name="Forward Axis",
        items=(("NEG_Y", "-Y", "Negative Y"), ("POS_Y", "+Y", "Positive Y"), ("NEG_X", "-X", "Negative X"), ("POS_X", "+X", "Positive X")),
        default="NEG_Y",
    )
    up_axis: EnumProperty(
        name="Up Axis",
        items=(("POS_Z", "+Z", "Positive Z"), ("NEG_Z", "-Z", "Negative Z"), ("POS_Y", "+Y", "Positive Y"), ("POS_X", "+X", "Positive X")),
        default="POS_Z",
    )

    angles: CollectionProperty(type=QSDFAngle)
    active_angle_index: IntProperty(name="Active Angle", default=0, min=0)
    active_angle_uuid: StringProperty(name="Active Angle UUID", default="")
    active_side: EnumProperty(name="Active Side", items=SIDE_ITEMS, default="RIGHT")
    boundary_tracks: CollectionProperty(type=QSDFBoundaryTrack)
    active_boundary_track_index: IntProperty(name="Active Boundary", default=-1, min=-1)

    packing_channels: CollectionProperty(type=QSDFPackingChannel)
    packing_customized: BoolProperty(name="Custom Packing", default=False)
    packing_revision: IntProperty(
        name="Packing Revision",
        default=0,
        min=0,
        options={"HIDDEN"},
    )
    aux_masks: CollectionProperty(type=QSDFAuxMask)
    active_aux_mask_index: IntProperty(name="Active Additional Mask", default=-1, min=-1)
    active_aux_mask_uuid: StringProperty(name="Active Additional Mask UUID", default="")
    packing_preview_channel: EnumProperty(
        name="Packing Preview",
        items=(
            ("RGB", "RGB", "Preview the packed RGB channels"),
            ("R", "R", "Preview the red channel as grayscale"),
            ("G", "G", "Preview the green channel as grayscale"),
            ("B", "B", "Preview the blue channel as grayscale"),
            ("A", "A", "Preview the alpha channel as grayscale"),
        ),
        default="RGB",
    )

    author_tool: EnumProperty(
        name="Author Tool",
        items=(("BOUNDARY", "Boundary", "Edit boundary curves"), ("PAINT", "Paint", "Paint local corrections")),
        default="PAINT",
    )
    review_mode: EnumProperty(
        name="Review",
        items=(
            ("CURRENT_MASK", "Current Mask", "Display the active mask"),
            ("ONION_DIFFERENCE", "Onion / Difference", "Compare neighboring masks"),
            ("THRESHOLD_RESULT", "Threshold Result", "Display the generated threshold result"),
            ("VIOLATION_HEATMAP", "Violation Heatmap", "Display monotonic violations"),
        ),
        default="CURRENT_MASK",
    )
    apply_target: EnumProperty(name="Apply To", items=APPLY_TARGET_ITEMS, default="CURRENT")
    spread_mode: EnumProperty(
        name="Spread",
        items=(("SOLID", "Solid", "Keep the footprint across angles"), ("GRADIENT", "Gradient", "Shrink the footprint over angular distance")),
        default="SOLID",
    )
    spread_falloff: FloatProperty(name="Falloff", default=1.0, min=0.01, max=8.0)
    symmetry_mode: EnumProperty(name="Symmetry", items=SYMMETRY_ITEMS, default="AUTO")
    mirror_enabled: BoolProperty(name="Mirror", default=True)
    authoring_side: EnumProperty(name="Authoring Side", items=SIDE_ITEMS, default="RIGHT")
    symmetry_candidate: EnumProperty(
        name="Suggested Mirror",
        items=SYMMETRY_ITEMS,
        default="TEXTURE_MIRROR",
        options={"HIDDEN"},
    )
    symmetry_requires_confirmation: BoolProperty(
        name="Choose Mirror Layout", default=False, options={"HIDDEN"}
    )
    paint_value: IntProperty(name="Paint Value", default=0, min=0, max=1)
    review_angle: FloatProperty(name="Review Angle", default=0.0, min=-90.0, max=90.0)
    seek_angle: FloatProperty(name="Light Angle", default=0.0, min=0.0, max=90.0)
    symmetry_confidence: FloatProperty(name="Symmetry Confidence", default=0.0, min=0.0, max=1.0, subtype="FACTOR")
    preview_enabled: BoolProperty(name="Preview Enabled", default=False)
    preview_mode: EnumProperty(
        name="Display",
        items=(
            ("OVERLAY", "Paint Overlay", "Original material with warm Light and cool Shadow overlay"),
            ("MASK", "Mask", "Opaque black and white authoring mask"),
            ("TOON", "Toon Result", "Continuous light-angle threshold result"),
        ),
        default="OVERLAY",
        update=_preview_mode_changed,
    )
    onion_enabled: BoolProperty(name="Onion", default=False, update=_onion_changed)
    boundary_enabled: BoolProperty(name="Enable Boundary Tool", default=False)

    output_path: StringProperty(name="Output", subtype="FILE_PATH", default="")
    overwrite: BoolProperty(name="Overwrite", default=False)
    generated_image: PointerProperty(name="Generated Threshold", type=bpy.types.Image)
    export_adjustment_image: PointerProperty(
        name="Export Adjustment Heatmap",
        type=bpy.types.Image,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    export_adjustment_pixel_count: IntProperty(
        name="Adjusted Authored Pixels",
        default=0,
        min=0,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    export_adjustment_sample_count: IntProperty(
        name="Adjusted Angle Samples",
        default=0,
        min=0,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    export_adjustment_protected_pixel_count: IntProperty(
        name="Adjusted Edited Pixels",
        default=0,
        min=0,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    export_failed: BoolProperty(
        name="Export Failed", default=False, options={"HIDDEN"}
    )
    has_violations: BoolProperty(name="Has Violations", default=False)
    validation_message: StringProperty(name="Validation", default="Not validated")
    warning_message: StringProperty(name="Warnings", default="")
    diagnostic_message: StringProperty(name="Diagnostics", default="")
    dirty: BoolProperty(name="Dirty", default=True)
    base_needs_update: BoolProperty(name="Base Needs Update", default=False)
    base_signature: StringProperty(name="Base Signature", default="", options={"HIDDEN"})
    base_source: EnumProperty(
        name="Base Source", items=BASE_SOURCE_ITEMS, default="NORMAL_GUIDE"
    )
    guide_version: IntProperty(name="Guide Version", default=2, min=0, options={"HIDDEN"})
    guide_shadow_amount: FloatProperty(
        name="Shadow Amount", default=50.0, min=0.0, max=100.0, subtype="PERCENTAGE"
    )
    thumbnail_uv_bbox: FloatVectorProperty(
        name="Thumbnail UV Bounds",
        size=4,
        default=(0.0, 0.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        options={"HIDDEN"},
    )
    guide_direction_warning: BoolProperty(
        name="Check Guide Direction", default=False, options={"HIDDEN"}
    )
    guide_direction_message: StringProperty(
        name="Guide Direction Message", default="", options={"HIDDEN"}
    )
    first_stroke_complete: BoolProperty(name="First Stroke Complete", default=False)
    job_running: BoolProperty(
        name="Quick SDF Job Running", default=False, options={"HIDDEN", "SKIP_SAVE"}
    )
    job_progress: FloatProperty(
        name="Progress", default=0.0, min=0.0, max=1.0, subtype="FACTOR",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    job_message: StringProperty(
        name="Job Status", default="", options={"HIDDEN", "SKIP_SAVE"}
    )

    # Preview material restoration state.  Pointers survive save/load, unlike a
    # module-level dictionary, and are repaired conservatively when missing.
    original_material: PointerProperty(name="Original Material", type=bpy.types.Material)
    preview_material: PointerProperty(name="Preview Material", type=bpy.types.Material)
    preview_material_name: StringProperty(name="Preview Material Name", default="")
    original_slot_link: EnumProperty(
        name="Original Slot Link",
        items=(("DATA", "Data", "Data-linked material"), ("OBJECT", "Object", "Object-linked material")),
        default="DATA",
    )
    original_material_was_none: BoolProperty(name="Original Material Was Empty", default=False)
    material_override_active: BoolProperty(name="Material Override Active", default=False)


class QSDFSettings(PropertyGroup):
    resolution: IntProperty(name="Resolution", default=1024, min=512, max=4096)
    initialization: EnumProperty(
        name="Initialize",
        items=(
            ("WHITE", "All Light", "Initialize every angle to white/light"),
            ("EXISTING", "Existing Mask", "Copy the selected image to every angle"),
            ("NORMAL_SWEEP", "Light Sweep", "Initialize from mesh normals in UV space"),
        ),
        default="NORMAL_SWEEP",
    )
    source_image: PointerProperty(name="Source Mask", type=bpy.types.Image)
    create_author_workspace: BoolProperty(name="Create Author Workspace", default=False)
    mask_sequence_directory: StringProperty(name="Mask Directory", subtype="DIR_PATH", default="//QuickSDF_masks")


def is_current_schema(project) -> bool:
    """Return whether ``project`` is exactly the only supported schema."""

    try:
        return int(project.schema_version) == SCHEMA_VERSION
    except (AttributeError, ReferenceError, TypeError, ValueError):
        return False


def validate_schema(project):
    """Require schema 5 and return the validated project for call chaining."""

    actual = getattr(project, "schema_version", None)
    if not is_current_schema(project):
        raise ValueError(
            f"Unsupported Quick SDF project schema {actual!r}; "
            f"schema {SCHEMA_VERSION} is required"
        )
    return project


def aux_mask_for_uuid(project, mask_uuid: str):
    wanted = str(mask_uuid)
    if not wanted:
        return None
    try:
        return next(
            (item for item in project.aux_masks if str(item.uuid) == wanted),
            None,
        )
    except (AttributeError, ReferenceError, TypeError):
        return None


def aux_mask_for_role(project, role: str):
    wanted = str(role)
    try:
        return next(
            (item for item in project.aux_masks if str(item.role) == wanted),
            None,
        )
    except (AttributeError, ReferenceError, TypeError):
        return None


def packing_channel_for(project, output_channel: str):
    wanted = str(output_channel).upper()
    try:
        return next(
            (
                item
                for item in project.packing_channels
                if str(item.output_channel) == wanted
            ),
            None,
        )
    except (AttributeError, ReferenceError, TypeError):
        return None


def _default_uuid() -> str:
    return str(uuid_module.uuid4())


def ensure_standard_aux_masks(
    project,
    *,
    uuid_factory: Callable[[], str] | None = None,
) -> tuple:
    """Ensure the two built-in mask records exist and have stable UUIDs.

    Image creation deliberately remains in the runtime layer.  This helper only
    creates persistent records so project creation can attach Blender Images to
    the returned entries.
    """

    uuid_factory = uuid_factory or _default_uuid
    labels = {
        "SDF_AREA": "SDF Area",
        "SHADOW_STRENGTH": "Shadow Strength",
    }
    result = []
    for role in STANDARD_AUX_MASK_ROLES:
        item = aux_mask_for_role(project, role)
        if item is None:
            item = project.aux_masks.add()
            item.role = role
            item.name = labels[role]
        if not str(item.uuid):
            item.uuid = str(uuid_factory())
        result.append(item)
    if int(getattr(project, "active_aux_mask_index", -1)) < 0 and result:
        active = result[0]
        project.active_aux_mask_uuid = str(active.uuid)
        project.active_aux_mask_index = next(
            (
                index
                for index, item in enumerate(project.aux_masks)
                if str(item.uuid) == str(active.uuid)
            ),
            0,
        )
    return tuple(result)


def mark_packing_changed(project, *, customized: bool = True) -> int:
    """Invalidate an in-flight packed export after a recipe or mask change."""

    project.packing_customized = bool(customized)
    project.packing_revision = int(getattr(project, "packing_revision", 0)) + 1
    project.dirty = True
    return int(project.packing_revision)


def mark_aux_mask_changed(project, aux_mask=None) -> int:
    if aux_mask is not None:
        aux_mask.revision = int(getattr(aux_mask, "revision", 0)) + 1
        aux_mask.dirty = True
    return mark_packing_changed(project, customized=bool(project.packing_customized))


def reset_liltoon_packing(project) -> None:
    """Replace the project-local recipe with the built-in lilToon mapping."""

    global _PACKING_RESET_DEPTH

    sdf_area, shadow_strength = ensure_standard_aux_masks(project)
    rows = (
        ("R", "RIGHT_THRESHOLD", "", False, 0.0),
        ("G", "LEFT_THRESHOLD", "", False, 0.0),
        ("B", "SDF_AREA", str(sdf_area.uuid), True, 0.0),
        ("A", "SHADOW_STRENGTH", str(shadow_strength.uuid), False, 1.0),
    )
    _PACKING_RESET_DEPTH += 1
    try:
        project.packing_channels.clear()
        for output, source, mask_uuid, invert, constant in rows:
            item = project.packing_channels.add()
            item.output_channel = output
            item.source_type = source
            item.auxiliary_mask_uuid = mask_uuid
            item.invert = invert
            item.constant_value = constant
        project.packing_customized = False
        project.packing_revision = int(getattr(project, "packing_revision", 0)) + 1
        project.dirty = True
    finally:
        _PACKING_RESET_DEPTH -= 1


def ensure_liltoon_packing(project) -> bool:
    """Initialize a missing/invalid four-row recipe without replacing custom rows."""

    outputs = tuple(
        str(getattr(item, "output_channel", ""))
        for item in getattr(project, "packing_channels", ())
    )
    if outputs == PACKING_OUTPUT_CHANNELS:
        return False
    reset_liltoon_packing(project)
    return True


CLASSES = (
    QSDFBoundaryPoint,
    QSDFBoundaryKey,
    QSDFBoundaryTrack,
    QSDFAngle,
    QSDFPackingChannel,
    QSDFAuxMask,
    QSDFProject,
    QSDFSettings,
)


def register_properties() -> None:
    """Attach root properties after ``CLASSES`` have been registered."""
    bpy.types.Scene.quick_sdf_projects = CollectionProperty(type=QSDFProject)
    bpy.types.Scene.quick_sdf_active_project_index = IntProperty(
        name="Active Quick SDF Project", default=-1, min=-1
    )
    bpy.types.Scene.quick_sdf_settings = PointerProperty(type=QSDFSettings)


def unregister_properties() -> None:
    for name in (
        "quick_sdf_settings",
        "quick_sdf_active_project_index",
        "quick_sdf_projects",
    ):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent RNA data model for Quick SDF Blender.

Only Blender ID pointers and plain RNA values are stored here.  Runtime caches must
key themselves by the UUID fields instead of retaining PropertyGroup instances,
because undo/load can invalidate those Python wrappers.
"""

from __future__ import annotations

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


SCHEMA_VERSION = 3
DEFAULT_KEY_ANGLES = tuple(float(value) for value in range(0, 91, 15))
LEGACY_SIGNED_ANGLES = tuple(float(value) for value in range(-90, 91, 15))
# Public compatibility name.  From schema v2 onward a linked/mirrored project
# authors one 0..90 degree side; the opposite side is generated live.
DEFAULT_ANGLES = DEFAULT_KEY_ANGLES


SIDE_ITEMS = (
    ("RIGHT", "Right", "Right-light authoring side"),
    ("LEFT", "Left", "Left-light authoring side"),
)


BASE_SOURCE_ITEMS = (
    ("NORMAL_GUIDE", "Normal Guide", "Base masks generated from evaluated mesh normals"),
    ("IMPORTED", "Imported Mask", "Base masks copied from artist-supplied images"),
    ("WHITE", "All Light", "Base masks initialized as all Light"),
    ("LEGACY", "Legacy", "Base masks preserved from an earlier Quick SDF version"),
)


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
    # ``image``/``image_name`` are retained through schema v2 so Blender can
    # deserialize v1 PointerProperties.  Runtime treats them as deprecated
    # aliases of ``display_image``.
    image: PointerProperty(name="Mask Image", type=bpy.types.Image)
    image_name: StringProperty(name="Image Name", default="")
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


class QSDFProject(PropertyGroup):
    uuid: StringProperty(name="UUID", default="")
    schema_version: IntProperty(name="Schema Version", default=SCHEMA_VERSION, min=1)
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

    # Deprecated compatibility fields.  Studio execution state is transient
    # and must never be inferred from these values.  SKIP_SAVE prevents new
    # files from recreating the stale "Stop Authoring" state.
    author_active: BoolProperty(
        name="Authoring (Legacy)", default=False, options={"HIDDEN", "SKIP_SAVE"}
    )
    previous_image_paint_mode: StringProperty(
        name="Previous Paint Mode (Legacy)",
        default="MATERIAL",
        options={"HIDDEN", "SKIP_SAVE"},
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
    guide_version: IntProperty(name="Guide Version", default=1, min=0, options={"HIDDEN"})
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


CLASSES = (
    QSDFBoundaryPoint,
    QSDFBoundaryKey,
    QSDFBoundaryTrack,
    QSDFAngle,
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


# Backward-compatible names for early development builds.
register_runtime = register_properties
unregister_runtime = unregister_properties

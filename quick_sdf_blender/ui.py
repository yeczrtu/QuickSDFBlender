"""Artist-first launcher and Advanced controls for Quick SDF Studio.

Daily angle selection lives in the full-width Studio timeline and daily paint
controls live in the WorkSpaceTool header.  This module intentionally keeps the
ordinary N-panel to one obvious entry action.
"""

from __future__ import annotations

from typing import Any

import bpy

from . import runtime
from .i18n import tr


def tag_editors_for_redraw() -> None:
    manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(manager, "windows", ()):
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "IMAGE_EDITOR", "DOPESHEET_EDITOR"}:
                area.tag_redraw()


def _project_for_context(context: Any) -> Any | None:
    active = getattr(context, "active_object", None)
    projects = getattr(context.scene, "quick_sdf_projects", ())
    for project in projects:
        if project.target_object == active:
            return project
    # Selecting another mesh is the artist's intent to create/open that face,
    # not a reason to keep showing controls for an unrelated prior project.
    return runtime.active_project(context.scene) if active is None else None


def _studio_active(context: Any, project: Any | None) -> bool:
    if project is None:
        return False
    try:
        from .studio import is_studio_active

        return is_studio_active(context, str(project.uuid))
    except (ImportError, ReferenceError, RuntimeError):
        return False


def _paint_buttons(layout: Any, project: Any) -> None:
    row = layout.row(align=True)
    light = row.operator("quicksdf.paint_value_set", text="Light", icon="LIGHT_SUN", depress=int(project.paint_value) == 1)
    light.value = 1
    shadow = row.operator("quicksdf.paint_value_set", text="Shadow", icon="SHADING_SOLID", depress=int(project.paint_value) == 0)
    shadow.value = 0


def _mirror_confirmation(layout: Any, project: Any) -> None:
    if not bool(getattr(project, "symmetry_requires_confirmation", False)):
        return
    box = layout.box()
    box.alert = True
    box.label(text="Choose the preview that matches the face UV", icon="MOD_MIRROR")
    row = box.row(align=True)
    for mode, label in (
        ("TEXTURE_MIRROR", "Whole Texture"),
        ("ISLAND_PAIR", "Paired Islands"),
        ("OVERLAPPED_UV", "Shared UV"),
    ):
        operator = row.operator("quicksdf.symmetry_choose", text=label)
        operator.mode = mode


def _export_controls(layout: Any, project: Any, *, compact: bool = False) -> None:
    if bool(getattr(project, "job_running", False)):
        box = layout.box()
        message = str(getattr(project, "job_message", "")) or "Generating…"
        if hasattr(box, "progress"):
            box.progress(
                factor=float(getattr(project, "job_progress", 0.0)),
                type="BAR",
                text=message,
            )
        else:
            row = box.row()
            row.enabled = False
            row.prop(project, "job_progress", text=message, slider=True)
        box.operator("quicksdf.cancel_job", text="Cancel", icon="CANCEL")
        return
    export = layout.column()
    export.scale_y = 1.0 if compact else 1.45
    export.operator(
        "quicksdf.export_texture",
        text="Retry Export" if bool(getattr(project, "export_failed", False)) else "Export Face Shadow Texture",
        icon="FILE_REFRESH" if bool(getattr(project, "export_failed", False)) else "EXPORT",
    )
    message = str(getattr(project, "job_message", ""))
    if message:
        if bool(getattr(project, "export_failed", False)):
            icon = "ERROR"
        elif message.startswith("Exported"):
            icon = "CHECKMARK"
        else:
            icon = "INFO"
        layout.label(text=message, icon=icon)


class QSDF_PT_launcher(bpy.types.Panel):
    bl_idname = "QSDF_PT_launcher"
    bl_label = "Quick SDF Studio"
    bl_category = "Quick SDF"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        project = _project_for_context(context)
        studio = _studio_active(context, project)

        if project is None:
            obj = context.active_object
            if obj is None or obj.type != "MESH":
                box = layout.box()
                box.label(text="Select the face mesh", icon="OUTLINER_OB_MESH")
                box.label(text="Then create a paint-ready face shadow.")
                return
            info = layout.box()
            info.label(text=obj.name, icon="OUTLINER_OB_MESH")
            material = obj.active_material.name if obj.active_material else "A material will be created"
            info.label(text=material, icon="MATERIAL")
            button = layout.column()
            button.scale_y = 1.7
            button.operator("quicksdf.create_and_edit", text="Create & Edit", icon="BRUSH_DATA")
            layout.label(text="Auto-bakes the current pose, then opens Studio.", icon="INFO")
            return

        if studio:
            status = layout.box()
            status.label(text=tr("Editing %s") % project.target_object.name, icon="BRUSH_DATA")
            _paint_buttons(status, project)
            row = status.row(align=True)
            row.operator(
                "quicksdf.mirror_toggle", text="Mirror On", icon="MOD_MIRROR",
                depress=bool(project.mirror_enabled),
            )
            row.prop(project, "preview_mode", text="")
            if bool(project.base_needs_update):
                warning = layout.box()
                warning.label(text="Base needs update", icon="ERROR")
                warning.operator("quicksdf.bake_base", text="Rebake Base", icon="FILE_REFRESH")
            if str(getattr(project, "base_source", "LEGACY")) == "LEGACY":
                guide = layout.box()
                guide.label(text="Start with a shadow guide from the model", icon="LIGHT_SUN")
                guide.operator(
                    "quicksdf.bake_base", text="Create Normal Shadow Guide", icon="SHADING_RENDERED"
                )
            if bool(getattr(project, "guide_direction_warning", False)):
                direction = layout.box()
                direction.alert = True
                direction.label(
                    text=tr(str(project.guide_direction_message)), icon="ORIENTATION_VIEW"
                )
                direction.operator(
                    "quicksdf.set_forward_from_view", text="Use This View as Front", icon="VIEW_CAMERA"
                )
            _mirror_confirmation(layout, project)
            _export_controls(layout, project)
            layout.operator("quicksdf.studio_exit", text="Exit Quick SDF", icon="X")
        else:
            layout.label(text=project.name, icon="IMAGE_DATA")
            button = layout.column()
            button.scale_y = 1.7
            button.operator("quicksdf.studio_enter", text="Open Quick SDF Studio", icon="WORKSPACE")
            if str(getattr(project, "base_source", "LEGACY")) == "LEGACY":
                guide = layout.box()
                guide.label(text="Start with a shadow guide from the model", icon="LIGHT_SUN")
                guide.operator(
                    "quicksdf.bake_base", text="Create Normal Shadow Guide", icon="SHADING_RENDERED"
                )
            _mirror_confirmation(layout, project)


class QSDF_PT_advanced(bpy.types.Panel):
    bl_idname = "QSDF_PT_advanced"
    bl_label = "Advanced"
    bl_parent_id = "QSDF_PT_launcher"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        project = _project_for_context(context)
        if project is None:
            settings = context.scene.quick_sdf_settings
            layout.prop(settings, "resolution")
            layout.prop(settings, "initialization")
            if settings.initialization == "EXISTING":
                layout.prop(settings, "source_image")
            if int(settings.resolution) >= 4096:
                layout.label(text="4096 uses substantial memory.", icon="ERROR")
            return

        column = layout.column(align=True)
        column.prop(project, "target_object", text="Object")
        column.prop(project, "material_slot_index", text="Material Slot")
        column.prop(project, "uv_map_name", text="UV Map")
        column.prop(project, "resolution")
        axes = layout.box()
        axes.label(text="Character Axes")
        axes.operator("quicksdf.set_forward_from_view", text="Use This View as Front")

        guide = layout.box()
        guide.label(text="Adjust Shadow Guide")
        guide.prop(project, "guide_shadow_amount", text="Shadow Amount", slider=True)
        guide.operator("quicksdf.bake_base", text="Update Shadow Guide", icon="FILE_REFRESH")
        if bool(getattr(project, "guide_direction_warning", False)):
            guide.alert = True
            guide.label(text=tr(str(project.guide_direction_message)), icon="ERROR")

        mirror = layout.box()
        mirror.label(text="Mirror")
        mirror.prop(project, "symmetry_mode", text="Layout")
        mirror.prop(project, "authoring_side", text="Paint Side")
        if bool(project.mirror_enabled):
            mirror.operator("quicksdf.break_mirror", text="Break Mirror", icon="UNLINKED")

        keys = layout.box()
        keys.label(text="Angle Keys")
        row = keys.row(align=True)
        add = row.operator("quicksdf.key_add", text="Add at Seek", icon="ADD")
        add.duplicate = False
        duplicate = row.operator("quicksdf.key_add", text="Duplicate to Seek", icon="DUPLICATE")
        duplicate.duplicate = True
        active = runtime.active_angle(project)
        if active is not None:
            row = keys.row(align=True)
            move = row.operator("quicksdf.key_move", text="Move / Retime", icon="ARROW_LEFTRIGHT")
            move.uuid = str(active.uuid)
            remove = row.operator("quicksdf.key_delete", text="Delete", icon="REMOVE")
            remove.uuid = str(active.uuid)

        layout.operator("quicksdf.bake_base", text="Rebake Base", icon="FILE_REFRESH")
        layout.prop(project, "boundary_enabled")
        if bool(project.boundary_enabled):
            row = layout.row(align=True)
            row.operator("quicksdf.boundary_track_add", text="Add Boundary", icon="ADD")
            row.operator("quicksdf.boundary_track_remove", text="Remove", icon="REMOVE")
            row = layout.row(align=True)
            row.operator("quicksdf.boundary_draw", text="Draw Key", icon="GREASEPENCIL")
            row.operator("quicksdf.boundary_commit", text="Commit", icon="CHECKMARK")
        review = layout.box()
        review.label(text="Review / Recovery")
        review.operator("quicksdf.export_mask_sequence", text="Export Review Masks")
        review.operator("quicksdf.restore_materials", text="Restore Materials", icon="LOOP_BACK")
        adjusted = int(getattr(project, "export_adjustment_pixel_count", 0))
        adjustment_image = getattr(project, "export_adjustment_image", None)
        if adjusted and adjustment_image is not None:
            adjustments = layout.box()
            adjustments.label(
                text=tr("%s authored pixels needed angle adjustment.") % f"{adjusted:,}"
            )
            adjustments.label(
                text=tr("%s angle samples changed")
                % f"{int(getattr(project, 'export_adjustment_sample_count', 0)):,}"
            )
            protected = int(
                getattr(project, "export_adjustment_protected_pixel_count", 0)
            )
            if protected:
                adjustments.label(
                    text=tr("%s edited pixels required adjustment.") % f"{protected:,}",
                    icon="INFO",
                )
            adjustments.operator(
                "quicksdf.review_export_adjustments",
                text="Review Export Adjustments",
                icon="IMAGE_DATA",
            )
        if project.diagnostic_message:
            warning = layout.box()
            warning.alert = True
            for line in str(project.diagnostic_message).splitlines():
                warning.label(text=line, icon="ERROR")
        layout.separator()
        layout.operator("quicksdf.project_remove", text="Remove Quick SDF Project", icon="TRASH")


class QSDF_PT_image_studio(bpy.types.Panel):
    bl_idname = "QSDF_PT_image_studio"
    bl_label = "Quick SDF Studio"
    bl_category = "Quick SDF"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"

    @classmethod
    def poll(cls, context):
        return _studio_active(context, _project_for_context(context))

    def draw(self, context):
        project = _project_for_context(context)
        _paint_buttons(self.layout, project)
        self.layout.prop(project, "onion_enabled", text="Onion", toggle=True)
        _export_controls(self.layout, project, compact=True)


def register_draw_handlers() -> None:
    # Timeline and tool overlays own their dedicated handlers in schema v2.
    return None


def unregister_draw_handlers() -> None:
    return None


def register_keymaps() -> None:
    # LMB and navigation are scoped to the two Quick SDF WorkSpaceTools.
    return None


def unregister_keymaps() -> None:
    return None


CLASSES = (QSDF_PT_launcher, QSDF_PT_advanced, QSDF_PT_image_studio)


__all__ = [
    "CLASSES", "register_draw_handlers", "register_keymaps", "tag_editors_for_redraw",
    "unregister_draw_handlers", "unregister_keymaps",
]

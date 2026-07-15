# SPDX-License-Identifier: GPL-3.0-or-later
"""Artist-first launcher and Advanced controls for Quick SDF Paint.

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
            if area.type in {"VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR"}:
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


def _mask_paint_buttons(layout: Any, project: Any) -> None:
    """Use value language while an angle-independent mask is being painted."""

    row = layout.row(align=True)
    white = row.operator(
        "quicksdf.paint_value_set",
        text="White",
        icon="RADIOBUT_ON",
        depress=int(getattr(project, "paint_value", 1)) == 1,
    )
    white.value = 1
    black = row.operator(
        "quicksdf.paint_value_set",
        text="Black",
        icon="RADIOBUT_OFF",
        depress=int(getattr(project, "paint_value", 1)) == 0,
    )
    black.value = 0


def _operator_property(operator: Any, name: str, value: Any) -> None:
    """Set an optional operator property without making an in-flight schema fatal."""

    try:
        if hasattr(operator, name):
            setattr(operator, name, value)
    except (AttributeError, ReferenceError, TypeError):
        pass


def _editing_aux_mask_uuid(context: Any, project: Any | None) -> str:
    if project is None:
        return ""
    try:
        from .studio import active_session

        session = active_session(context)
    except (ImportError, ReferenceError, RuntimeError):
        return ""
    if session is None or str(getattr(session, "project_uuid", "")) != str(
        getattr(project, "uuid", "")
    ):
        return ""
    return str(getattr(session, "editing_aux_mask_uuid", ""))


def _aux_mask_by_uuid(project: Any, mask_uuid: str) -> Any | None:
    for mask in getattr(project, "aux_masks", ()):
        if str(getattr(mask, "uuid", "")) == str(mask_uuid):
            return mask
    return None


def _active_aux_mask(project: Any) -> Any | None:
    masks = getattr(project, "aux_masks", ())
    index = int(getattr(project, "active_aux_mask_index", -1))
    if index < 0 or index >= len(masks):
        return None
    return masks[index]


def _packing_channels(project: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for channel in getattr(project, "packing_channels", ()):
        output = str(getattr(channel, "output_channel", "")).upper()
        if output in {"R", "G", "B", "A"} and output not in result:
            result[output] = channel
    return result


def _packing_meaning(channel: Any) -> str:
    """Describe the packed black/white result, including inversion."""

    source = str(getattr(channel, "source_type", "")).upper()
    invert = bool(getattr(channel, "invert", False))
    meanings = {
        "RIGHT_THRESHOLD": ("lights late", "lights early"),
        "LEFT_THRESHOLD": ("lights late", "lights early"),
        "SDF_AREA": ("normal shading", "threshold shading"),
        "SHADOW_STRENGTH": ("shadow off", "full shadow"),
        "CUSTOM_MASK": ("mask off", "mask on"),
    }
    if source == "CONSTANT":
        value = max(0.0, min(1.0, float(getattr(channel, "constant_value", 0.0))))
        if invert:
            value = 1.0 - value
        return tr("Constant output: %s") % f"{value:.2f}"
    black, white = meanings.get(source, ("0", "1"))
    if invert:
        black, white = white, black
    return tr("Black: %s · White: %s") % (tr(black), tr(white))


def _packing_channel_error(project: Any, output: str, channel: Any) -> str:
    """Return an artist-facing error for one visible packing row."""

    source = str(getattr(channel, "source_type", "")).upper()
    valid_sources = {
        "RIGHT_THRESHOLD",
        "LEFT_THRESHOLD",
        "SDF_AREA",
        "SHADOW_STRENGTH",
        "CUSTOM_MASK",
        "CONSTANT",
    }
    if source not in valid_sources:
        return tr("%s: Unknown source") % output
    if source in {"SDF_AREA", "SHADOW_STRENGTH"}:
        item = next(
            (
                mask
                for mask in getattr(project, "aux_masks", ())
                if str(getattr(mask, "role", "")) == source
            ),
            None,
        )
        if item is None:
            return tr("%s: Required mask is missing") % output
    elif source == "CUSTOM_MASK":
        item = _aux_mask_by_uuid(
            project, str(getattr(channel, "auxiliary_mask_uuid", ""))
        )
        if item is None or str(getattr(item, "role", "")) != "CUSTOM":
            return tr("%s: Select a Custom Mask") % output
    else:
        item = None
    if item is None:
        return ""
    image = runtime.resolve_aux_mask_image(project, item)
    if image is None:
        return tr("%s: Mask image is missing") % output
    expected = int(getattr(project, "resolution", 0))
    if tuple(map(int, image.size[:])) != (expected, expected):
        return tr("%s: Mask resolution does not match the project") % output
    return ""


def _draw_aux_edit_status(layout: Any, context: Any, project: Any) -> bool:
    mask_uuid = _editing_aux_mask_uuid(context, project)
    if not mask_uuid:
        return False
    mask = _aux_mask_by_uuid(project, mask_uuid)
    name = str(getattr(mask, "name", "")) or tr("Additional Mask")
    box = layout.box()
    box.label(text=tr("Editing Mask: %s") % name, icon="IMAGE_DATA")
    box.label(text="These masks are shared by every angle.", icon="INFO")
    _mask_paint_buttons(box, project)
    back = box.operator(
        "quicksdf.aux_mask_back",
        text="Back to Face Shadow",
        icon="LOOP_BACK",
    )
    _operator_property(back, "mask_uuid", mask_uuid)
    return True


class QSDF_UL_aux_masks(bpy.types.UIList):
    """Compact project-local list used by mask editing and packing assignment."""

    bl_idname = "QSDF_UL_aux_masks"

    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index=0,
        _filter_flag=0,
    ):
        if self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="IMAGE_DATA")
            return
        role = str(getattr(item, "role", "CUSTOM"))
        if role == "CUSTOM" and hasattr(item, "name"):
            layout.prop(item, "name", text="", emboss=False, icon="IMAGE_DATA")
        else:
            layout.label(
                text=str(getattr(item, "name", "")) or tr(role.replace("_", " ").title()),
                icon="IMAGE_DATA",
            )


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
        text="Retry Export" if bool(getattr(project, "export_failed", False)) else "Export Threshold Map",
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
    bl_label = "Quick SDF Paint"
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
            layout.label(
                text="Auto-bakes the current pose, then opens Quick SDF Paint.",
                icon="INFO",
            )
            return

        if studio:
            status = layout.box()
            status.label(text=tr("Editing %s") % project.target_object.name, icon="BRUSH_DATA")
            editing_aux = _draw_aux_edit_status(status, context, project)
            if not editing_aux:
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
            button.operator("quicksdf.studio_enter", text="Open Quick SDF Paint", icon="WORKSPACE")
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
        add = row.operator("quicksdf.key_add", text="Add Angle…", icon="ADD")
        add.duplicate = False
        duplicate = row.operator("quicksdf.key_add", text="Duplicate Angle…", icon="DUPLICATE")
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


class QSDF_PT_output_packing(bpy.types.Panel):
    bl_idname = "QSDF_PT_output_packing"
    bl_label = "Output Packing"
    bl_parent_id = "QSDF_PT_advanced"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    @classmethod
    def poll(cls, context):
        return _project_for_context(context) is not None

    def draw(self, context):
        layout = self.layout
        project = _project_for_context(context)
        if project is None:
            return

        customized = bool(getattr(project, "packing_customized", False))
        header = layout.row(align=True)
        header.label(
            text="Packing: Custom — This Project" if customized else "Packing: lilToon",
            icon="NODE_COMPOSITING",
        )
        if customized:
            header.operator("quicksdf.packing_reset_liltoon", text="Reset to lilToon")
        else:
            header.operator("quicksdf.packing_customize", text="Customize")

        channels = _packing_channels(project)
        output_counts = {
            output: sum(
                str(getattr(item, "output_channel", "")).upper() == output
                for item in getattr(project, "packing_channels", ())
            )
            for output in "RGBA"
        }
        if not hasattr(project, "packing_channels") or not channels:
            warning = layout.box()
            warning.alert = True
            warning.label(
                text="Packing data is not available for this project.",
                icon="ERROR",
            )
            return

        for output in "RGBA":
            channel = channels.get(output)
            box = layout.box()
            if channel is None:
                box.alert = True
                box.label(text=tr("%s: Missing channel mapping") % output, icon="ERROR")
                continue

            row_error = (
                tr("%s: Duplicate channel mapping") % output
                if output_counts.get(output, 0) > 1
                else _packing_channel_error(project, output, channel)
            )
            if row_error:
                box.alert = True

            row = box.row(align=True)
            output_label = row.row(align=True)
            output_label.alignment = "CENTER"
            output_label.label(text=output)

            controls = row.row(align=True)
            controls.enabled = customized
            if hasattr(channel, "source_type"):
                controls.prop(channel, "source_type", text="")
            else:
                controls.label(text=str(getattr(channel, "source_type", "")))
            if hasattr(channel, "invert"):
                controls.prop(
                    channel,
                    "invert",
                    text="Invert" if bool(getattr(channel, "invert", False)) else "Direct",
                    toggle=True,
                )

            preview_row = row.row(align=True)
            preview_row.enabled = _studio_active(context, project)
            preview = preview_row.operator(
                "quicksdf.packing_preview_channel",
                text="Preview",
                icon="HIDE_OFF",
            )
            _operator_property(preview, "output_channel", output)

            source = str(getattr(channel, "source_type", "")).upper()
            if source == "CUSTOM_MASK":
                mask_row = box.row(align=True)
                mask_row.enabled = customized
                if hasattr(channel, "auxiliary_mask_uuid"):
                    selected = _aux_mask_by_uuid(
                        project, str(getattr(channel, "auxiliary_mask_uuid", ""))
                    )
                    mask_row.label(
                        text=str(getattr(selected, "name", ""))
                        or tr("No Custom Mask Selected"),
                        icon="IMAGE_DATA",
                    )
                    active_mask = _active_aux_mask(project)
                    assign_row = mask_row.row(align=True)
                    assign_row.enabled = bool(
                        customized
                        and active_mask is not None
                        and str(getattr(active_mask, "role", "")) == "CUSTOM"
                    )
                    assign = assign_row.operator(
                        "quicksdf.packing_assign_active_mask",
                        text="Use Selected Mask",
                    )
                    _operator_property(assign, "output_channel", output)
                else:
                    mask_row.label(text="Add a Custom Mask first.", icon="INFO")
            elif source == "CONSTANT" and hasattr(channel, "constant_value"):
                value_row = box.row(align=True)
                value_row.enabled = customized
                value_row.prop(channel, "constant_value", text="Constant", slider=True)

            meaning = box.row()
            meaning.enabled = False
            meaning.label(text=_packing_meaning(channel), icon="INFO")
            if row_error:
                box.label(text=row_error, icon="ERROR")

        combined = layout.row()
        combined.enabled = _studio_active(context, project)
        preview = combined.operator(
            "quicksdf.packing_preview_channel",
            text="Preview Packed RGB",
            icon="IMAGE_RGB",
        )
        _operator_property(preview, "output_channel", "RGB")


class QSDF_PT_additional_masks(bpy.types.Panel):
    bl_idname = "QSDF_PT_additional_masks"
    bl_label = "Additional Masks"
    bl_parent_id = "QSDF_PT_advanced"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    @classmethod
    def poll(cls, context):
        return _project_for_context(context) is not None

    def draw(self, context):
        layout = self.layout
        project = _project_for_context(context)
        if project is None:
            return

        editing_uuid = _editing_aux_mask_uuid(context, project)
        if editing_uuid:
            _draw_aux_edit_status(layout, context, project)
        else:
            layout.label(text="These masks are shared by every angle.", icon="INFO")

        if not hasattr(project, "aux_masks"):
            warning = layout.box()
            warning.alert = True
            warning.label(
                text="Additional mask data is not available for this project.",
                icon="ERROR",
            )
            return

        masks = list(getattr(project, "aux_masks", ()))
        if not masks:
            layout.label(text="No additional masks.", icon="INFO")
        if masks and hasattr(project, "active_aux_mask_index"):
            list_row = layout.row()
            list_row.template_list(
                "QSDF_UL_aux_masks",
                "project_masks",
                project,
                "aux_masks",
                project,
                "active_aux_mask_index",
                rows=min(5, max(3, len(masks))),
            )
            list_buttons = list_row.column(align=True)
            list_buttons.operator("quicksdf.aux_mask_add", text="", icon="ADD")
            active_for_delete = _active_aux_mask(project)
            delete_row = list_buttons.row(align=True)
            delete_row.enabled = bool(
                active_for_delete is not None
                and str(getattr(active_for_delete, "role", "")) == "CUSTOM"
            )
            remove = delete_row.operator("quicksdf.aux_mask_delete", text="", icon="REMOVE")
            if active_for_delete is not None:
                _operator_property(
                    remove, "mask_uuid", str(getattr(active_for_delete, "uuid", ""))
                )
        else:
            layout.operator(
                "quicksdf.aux_mask_add",
                text="Add Custom Mask",
                icon="ADD",
            )

        mask = _active_aux_mask(project)
        if mask is None:
            return
        mask_uuid = str(getattr(mask, "uuid", ""))
        role = str(getattr(mask, "role", "CUSTOM")).upper()
        box = layout.box()
        name = str(getattr(mask, "name", "")) or tr("Additional Mask")
        box.label(
            text=name,
            icon="BRUSH_DATA" if editing_uuid == mask_uuid else "IMAGE_DATA",
        )
        image = getattr(mask, "image", None)
        image_name = str(getattr(image, "name", "")) or str(
            getattr(mask, "image_name", "")
        )
        if image_name:
            image_row = box.row()
            image_row.enabled = False
            image_row.label(text=tr("Image: %s") % image_name, icon="IMAGE")

        edit = box.operator("quicksdf.aux_mask_edit", text="Edit Mask", icon="BRUSH_DATA")
        _operator_property(edit, "mask_uuid", mask_uuid)
        actions = box.row(align=True)
        import_op = actions.operator(
            "quicksdf.aux_mask_import",
            text="Import from Image",
            icon="IMPORT",
        )
        _operator_property(import_op, "mask_uuid", mask_uuid)
        white = actions.operator("quicksdf.aux_mask_fill", text="Fill White")
        _operator_property(white, "mask_uuid", mask_uuid)
        _operator_property(white, "value", 1.0)
        black = actions.operator("quicksdf.aux_mask_fill", text="Fill Black")
        _operator_property(black, "mask_uuid", mask_uuid)
        _operator_property(black, "value", 0.0)

        if role == "SDF_AREA":
            reset = box.operator(
                "quicksdf.aux_mask_reset_sdf_area",
                text="Reset SDF Area from UV",
                icon="UV",
            )
            _operator_property(reset, "mask_uuid", mask_uuid)


class QSDF_PT_image_studio(bpy.types.Panel):
    bl_idname = "QSDF_PT_image_studio"
    bl_label = "Quick SDF Paint"
    bl_category = "Quick SDF"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"

    @classmethod
    def poll(cls, context):
        return _studio_active(context, _project_for_context(context))

    def draw(self, context):
        project = _project_for_context(context)
        editing_aux = _draw_aux_edit_status(self.layout, context, project)
        if not editing_aux:
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


CLASSES = (
    QSDF_UL_aux_masks,
    QSDF_PT_launcher,
    QSDF_PT_advanced,
    QSDF_PT_output_packing,
    QSDF_PT_additional_masks,
    QSDF_PT_image_studio,
)


__all__ = [
    "CLASSES", "register_draw_handlers", "register_keymaps", "tag_editors_for_redraw",
    "unregister_draw_handlers", "unregister_keymaps",
]

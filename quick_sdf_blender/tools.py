"""Quick SDF paint tools for Blender's 3D and Image editors.

The keymaps live on WorkSpaceTool definitions, so LMB is wrapped only while the
artist has explicitly selected Quick SDF Paint.  No global paint keymap is
installed by this module.
"""

from __future__ import annotations

from typing import Any

import bpy
from bpy.types import WorkSpaceTool


VIEW_TOOL_ID = "quicksdf.paint_view"
IMAGE_TOOL_ID = "quicksdf.paint_image"
_REGISTERED: list[type[WorkSpaceTool]] = []
_ADDON_KEYMAPS: list[tuple[Any, Any]] = []


def _operator_exists(idname: str) -> bool:
    namespace, name = idname.split(".", 1)
    return hasattr(getattr(bpy.ops, namespace, object()), name)


def _project(context: Any) -> Any | None:
    try:
        from .studio import active_session, resolve_session_project

        return resolve_session_project(active_session(context))
    except (ImportError, AttributeError, ReferenceError):
        return None


def _draw_tool_settings(context: Any, layout: Any, _tool: Any) -> None:
    project = _project(context)
    if project is None:
        layout.label(text="Open Quick SDF Studio to paint", icon="INFO")
        return

    try:
        from .studio import active_session

        session = active_session(context)
    except (ImportError, AttributeError, ReferenceError):
        session = None
    if session is not None and session.view_mode == "PREVIEW":
        preview_row = layout.row(align=True)
        preview_row.label(
            text=f"Preview {session.seek_angle:g}° / Paint {session.paint_key_angle:g}°",
            icon="HIDE_OFF",
        )
        if _operator_exists("quicksdf.back_to_paint"):
            preview_row.operator("quicksdf.back_to_paint", text=f"Back to Paint {session.paint_key_angle:g}°")
        preview_row.separator()

    row = layout.row(align=True)
    if _operator_exists("quicksdf.paint_value_set"):
        light = row.operator(
            "quicksdf.paint_value_set",
            text="Light",
            icon="LIGHT_SUN",
            depress=int(getattr(project, "paint_value", 0)) == 1,
        )
        light.value = 1
        shadow = row.operator(
            "quicksdf.paint_value_set",
            text="Shadow",
            icon="SHADING_SOLID",
            depress=int(getattr(project, "paint_value", 0)) == 0,
        )
        shadow.value = 0
    elif hasattr(project, "paint_value"):
        row.prop(project, "paint_value", text="Light / Shadow")

    row.separator()
    if _operator_exists("quicksdf.mirror_toggle"):
        row.operator(
            "quicksdf.mirror_toggle", text="Mirror On", icon="MOD_MIRROR",
            depress=bool(getattr(project, "mirror_enabled", False)),
        )
    elif hasattr(project, "mirror_enabled"):
        row.prop(project, "mirror_enabled", text="Mirror On", toggle=True, icon="MOD_MIRROR")

    display_prop = "preview_mode" if hasattr(project, "preview_mode") else (
        "review_mode" if hasattr(project, "review_mode") else ""
    )
    if display_prop:
        row.prop(project, display_prop, text="")
    if getattr(getattr(context, "area", None), "type", "") == "IMAGE_EDITOR" and hasattr(
        project, "onion_enabled"
    ):
        row.prop(project, "onion_enabled", text="Onion", toggle=True, icon="IMAGE_ALPHA")

    row.separator()
    if bool(getattr(project, "job_running", False)) and _operator_exists("quicksdf.cancel_job"):
        row.operator("quicksdf.cancel_job", text="Cancel", icon="CANCEL")
    elif _operator_exists("quicksdf.export_texture"):
        row.operator("quicksdf.export_texture", text="Export Face Shadow Texture", icon="EXPORT")
    if _operator_exists("quicksdf.studio_exit"):
        row.operator("quicksdf.studio_exit", text="Exit Quick SDF", icon="X")


_PAINT_KEYMAP = (
    ("quicksdf.range_paint", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
    ("quicksdf.range_paint_invert", {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True}, None),
    ("quicksdf.paint_value_toggle", {"type": "X", "value": "PRESS"}, None),
    ("quicksdf.angle_step", {"type": "LEFT_ARROW", "value": "PRESS"}, {"properties": [("step", -1)]}),
    ("quicksdf.angle_step", {"type": "RIGHT_ARROW", "value": "PRESS"}, {"properties": [("step", 1)]}),
    ("quicksdf.angle_set", {"type": "HOME", "value": "PRESS"}, {"properties": [("index", -1)]}),
    ("quicksdf.history_undo", {"type": "Z", "value": "PRESS", "ctrl": True}, None),
    ("quicksdf.history_redo", {"type": "Z", "value": "PRESS", "ctrl": True, "shift": True}, None),
)


class QSDF_WST_view_paint(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "PAINT_TEXTURE"
    bl_idname = VIEW_TOOL_ID
    bl_label = "Quick SDF Paint"
    bl_description = "Paint Light or Shadow on the selected angle; export keeps angles consistent"
    bl_icon = "brush.generic"
    bl_options = {"USE_BRUSHES"}
    bl_widget = None
    bl_keymap = _PAINT_KEYMAP
    draw_settings = staticmethod(_draw_tool_settings)


class QSDF_WST_image_paint(WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "PAINT"
    bl_idname = IMAGE_TOOL_ID
    bl_label = "Quick SDF Paint"
    bl_description = "Paint Light or Shadow on the selected angle; export keeps angles consistent"
    bl_icon = "brush.generic"
    bl_options = {"USE_BRUSHES"}
    bl_widget = None
    bl_keymap = _PAINT_KEYMAP
    draw_settings = staticmethod(_draw_tool_settings)


TOOLS = (QSDF_WST_view_paint, QSDF_WST_image_paint)


def register_tools() -> None:
    if _REGISTERED:
        return
    try:
        for tool in TOOLS:
            bpy.utils.register_tool(tool, after={"builtin.brush"}, separator=True)
            _REGISTERED.append(tool)
        keyconfig = getattr(
            getattr(bpy.context, "window_manager", None), "keyconfigs", None
        )
        addon = getattr(keyconfig, "addon", None)
        if addon is not None:
            keymap = addon.keymaps.new(
                name="Dopesheet",
                space_type="DOPESHEET_EDITOR",
                region_type="WINDOW",
            )
            for operator, shift in (
                ("quicksdf.history_undo", False),
                ("quicksdf.history_redo", True),
            ):
                item = keymap.keymap_items.new(
                    operator,
                    type="Z",
                    value="PRESS",
                    ctrl=True,
                    shift=shift,
                )
                _ADDON_KEYMAPS.append((keymap, item))
    except Exception:
        unregister_tools()
        raise


def unregister_tools() -> None:
    while _ADDON_KEYMAPS:
        keymap, item = _ADDON_KEYMAPS.pop()
        try:
            keymap.keymap_items.remove(item)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    while _REGISTERED:
        tool = _REGISTERED.pop()
        try:
            bpy.utils.unregister_tool(tool)
        except (ReferenceError, RuntimeError, ValueError):
            pass


def _activate_tool(context: Any, window: Any, area: Any, tool_id: str) -> None:
    region = next((region for region in area.regions if region.type == "WINDOW"), None)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        result = bpy.ops.wm.tool_set_by_id(name=tool_id, space_type=area.type)
    if not result or "FINISHED" not in result:
        raise RuntimeError(f"Could not activate workspace tool: {tool_id}")


def activate_tools(context: Any, *, window: Any | None = None) -> None:
    """Select the Quick SDF tool in both editors of the Studio screen."""

    window = window or getattr(context, "window", None)
    if window is None:
        raise RuntimeError("Quick SDF tools require an interactive Blender window")
    areas = tuple(window.screen.areas)
    view = next((area for area in areas if area.type == "VIEW_3D"), None)
    image = next((area for area in areas if area.type == "IMAGE_EDITOR"), None)
    if view is None or image is None:
        raise RuntimeError("Quick SDF Studio needs both a 3D View and an Image Editor")
    image_space = image.spaces.active
    if hasattr(image_space, "ui_mode"):
        image_space.ui_mode = "PAINT"
    elif hasattr(image_space, "mode"):
        image_space.mode = "PAINT"
    _activate_tool(context, window, view, VIEW_TOOL_ID)
    _activate_tool(context, window, image, IMAGE_TOOL_ID)


CLASSES: tuple[type, ...] = ()


__all__ = [
    "CLASSES", "IMAGE_TOOL_ID", "QSDF_WST_image_paint", "QSDF_WST_view_paint", "TOOLS",
    "VIEW_TOOL_ID", "activate_tools", "register_tools", "unregister_tools",
]

# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient Quick SDF Studio session and workspace management.

The persistent project model deliberately does not contain an ``author_active``
flag.  A Studio session belongs to one Blender window and is valid only for the
current process.  This keeps save/load and undo from resurrecting half-active
paint state.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

import bpy
from bpy.app.handlers import persistent


WORKSPACE_PROJECT_TAG = "quick_sdf_studio_project_uuid"
WORKSPACE_VERSION_TAG = "quick_sdf_studio_layout_version"
WORKSPACE_LAYOUT_VERSION = 2
WORKSPACE_BASENAME = "Quick SDF Studio"
TIMELINE_HEIGHT = 104
TIMELINE_SPACE_TYPE = "NODE_EDITOR"


class StudioError(RuntimeError):
    """Base error raised by Studio lifecycle operations."""


class StudioUnavailableError(StudioError):
    """Raised when an interactive Blender window is unavailable."""


class StudioBusyError(StudioError):
    """Raised when another window/project already owns the singleton session."""


@dataclass(slots=True)
class StudioSession:
    project_uuid: str
    window_pointer: int
    scene_name: str
    workspace_name: str
    previous_workspace_name: str
    previous_active_object_name: str
    previous_selected_object_names: tuple[str, ...]
    previous_mode: str
    previous_image_paint_mode: str
    previous_canvas_name: str
    workspace_created: bool = False
    preview_suspended: bool = False
    onion_suspended: bool = False
    show_first_stroke_hint: bool = True
    first_hint_text: str = ""
    view_mode: str = "EDIT"
    paint_key_uuid: str = ""
    paint_key_angle: float = 0.0
    seek_angle: float = 0.0
    previous_normal_falloff: bool | None = None
    previous_clip_starts: tuple[tuple[int, float], ...] = ()
    projection_settings_suspended: bool = False
    stroke_brush_name: str = ""
    stroke_brush_color: tuple[float, float, float] | None = None
    stroke_brush_secondary_color: tuple[float, float, float] | None = None
    stroke_unified_use_color: bool | None = None
    stroke_unified_color: tuple[float, float, float] | None = None
    stroke_unified_secondary_color: tuple[float, float, float] | None = None
    stroke_watchdog_deadline: float = 0.0
    stroke_from_view3d: bool = False
    projection_hint: str = ""
    export_review_active: bool = False
    editing_aux_mask_uuid: str = ""
    aux_previous_preview_mode: str = ""
    aux_previous_onion_enabled: bool = False


@dataclass(slots=True)
class _PendingEnter:
    session: StudioSession
    manage_preview: bool
    state: str = "JOIN"


@dataclass(slots=True)
class _PendingSwitch:
    project_uuid: str
    attempts: int = 0


_SESSION: StudioSession | None = None
_PENDING: _PendingEnter | None = None
_SWITCH_PENDING: _PendingSwitch | None = None
_HANDLERS_REGISTERED = False


def current_session() -> StudioSession | None:
    return _SESSION


def active_session(context: Any | None = None) -> StudioSession | None:
    session = _SESSION
    if session is None:
        return None
    context = context or bpy.context
    window = getattr(context, "window", None)
    if window is None or window.as_pointer() != session.window_pointer:
        return None
    return session


def is_studio_active(context: Any | None = None, project_uuid: str = "") -> bool:
    session = active_session(context)
    if session is None:
        return False
    if project_uuid and session.project_uuid != project_uuid:
        return False
    workspace = getattr(context or bpy.context, "workspace", None)
    return bool(workspace and workspace.get(WORKSPACE_PROJECT_TAG) == session.project_uuid)


def find_window(pointer: int) -> Any | None:
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        if window.as_pointer() == pointer:
            return window
    return None


def resolve_session_project(session: StudioSession | None = None) -> Any | None:
    session = session or _SESSION
    if session is None:
        return None
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(getattr(project, "uuid", "")) == session.project_uuid:
                return project
    return None


def _resolve_project_uuid(project_uuid: str) -> Any | None:
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(getattr(project, "uuid", "")) == str(project_uuid):
                return project
    return None


def _project_location(project: Any) -> tuple[Any, int] | tuple[None, None]:
    project_uuid = str(getattr(project, "uuid", ""))
    if not project_uuid:
        return None, None
    for scene in bpy.data.scenes:
        for index, candidate in enumerate(getattr(scene, "quick_sdf_projects", ())):
            if str(getattr(candidate, "uuid", "")) == project_uuid:
                return scene, index
    return None, None


def _activate_project(project: Any) -> tuple[Any, int]:
    scene, index = _project_location(project)
    if scene is None or index is None:
        raise StudioError("The Quick SDF project is no longer part of a scene")
    scene.quick_sdf_active_project_index = int(index)
    return scene, int(index)


def _preflight_studio_target(context: Any, project: Any) -> tuple[Any, int, Any, Any]:
    """Resolve every structural dependency before changing the live session."""

    scene, index = _project_location(project)
    if scene is None or index is None:
        raise StudioError("The Quick SDF project is no longer part of a scene")
    context_scene = getattr(context, "scene", None)
    if context_scene is None or scene.as_pointer() != context_scene.as_pointer():
        raise StudioError("Open the target scene before editing this Quick SDF project")
    obj = getattr(project, "target_object", None)
    if obj is None or getattr(obj, "type", "") != "MESH":
        raise StudioError("The Quick SDF target must be a local mesh")
    if obj.name not in context.view_layer.objects:
        raise StudioError("The Quick SDF target is not visible in the current View Layer")
    slot_index = int(getattr(project, "material_slot_index", 0))
    if not 0 <= slot_index < len(obj.material_slots):
        raise StudioError("The Quick SDF material slot is unavailable")
    uv_name = str(getattr(project, "uv_map_name", ""))
    if not uv_name or getattr(obj.data, "uv_layers", None) is None or obj.data.uv_layers.get(uv_name) is None:
        raise StudioError("The Quick SDF UV map is unavailable")
    _index, item = _resolve_paint_key(project)
    if item is None:
        raise StudioError("The Quick SDF project has no paint angle")
    from .runtime import resolve_display_image

    image = resolve_display_image(project, item)
    if image is None:
        raise StudioError("The active angle image could not be used as a paint canvas")
    return scene, int(index), obj, image


def _side_signed_angle(project: Any, item: Any) -> float:
    angle = float(getattr(item, "angle", 0.0))
    return -angle if str(getattr(item, "side", getattr(project, "authoring_side", "RIGHT"))) == "LEFT" else angle


def _resolve_paint_key(
    project: Any,
    *,
    key_uuid: str = "",
    index: int = -1,
) -> tuple[int, Any] | tuple[None, None]:
    angles = getattr(project, "angles", ())
    if key_uuid:
        for candidate_index, item in enumerate(angles):
            if str(getattr(item, "uuid", "")) == str(key_uuid):
                return candidate_index, item
    if 0 <= int(index) < len(angles):
        return int(index), angles[int(index)]
    active_uuid = str(getattr(project, "active_angle_uuid", ""))
    if active_uuid:
        for candidate_index, item in enumerate(angles):
            if str(getattr(item, "uuid", "")) == active_uuid:
                return candidate_index, item
    if angles:
        active_index = max(0, min(int(getattr(project, "active_angle_index", 0)), len(angles) - 1))
        return active_index, angles[active_index]
    return None, None


def select_paint_key(
    context: Any,
    project: Any,
    *,
    key_uuid: str = "",
    index: int = -1,
) -> bool:
    """Select one edit key and atomically synchronize every paint surface."""

    resolved_index, item = _resolve_paint_key(project, key_uuid=key_uuid, index=index)
    if item is None or resolved_index is None:
        return False
    angle = float(getattr(item, "angle", 0.0))
    project.active_angle_index = resolved_index
    project.active_angle_uuid = str(getattr(item, "uuid", ""))
    if hasattr(project, "active_side"):
        project.active_side = str(getattr(item, "side", getattr(project, "authoring_side", "RIGHT")))
    project.seek_angle = angle
    project.review_angle = _side_signed_angle(project, item)

    session = active_session(context)
    if (
        session is not None
        and session.project_uuid == str(getattr(project, "uuid", ""))
        and session.editing_aux_mask_uuid
    ):
        return False
    if session is not None and session.project_uuid == str(getattr(project, "uuid", "")):
        leave_export_adjustment_review(project, session=session)
        ensure_studio_material_preview(context, session=session)
        session.view_mode = "EDIT"
        session.paint_key_uuid = str(getattr(item, "uuid", ""))
        session.paint_key_angle = angle
        session.seek_angle = angle

    from .runtime import sync_canvas

    image = sync_canvas(context, project)
    if session is not None and image is not None:
        try:
            _assign_preview(project, image)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    tag_studio_redraw()
    return True


def enter_aux_mask_edit(context: Any, project: Any, mask_uuid: str) -> bool:
    """Switch both paint surfaces to one angle-independent project mask."""

    session = active_session(context)
    if session is None or session.project_uuid != str(getattr(project, "uuid", "")):
        raise StudioError("Open Quick SDF Studio before editing an additional mask")
    from . import runtime

    item = runtime.aux_mask_for_uuid(project, mask_uuid)
    image = runtime.resolve_aux_mask_image(project, item)
    if item is None or image is None:
        raise StudioError("The selected additional mask is missing")
    leave_export_adjustment_review(project, session=session)
    if not session.editing_aux_mask_uuid:
        session.aux_previous_preview_mode = str(
            getattr(project, "preview_mode", "OVERLAY")
        )
        session.aux_previous_onion_enabled = bool(
            getattr(project, "onion_enabled", False)
        )
    if bool(getattr(project, "onion_enabled", False)):
        project.onion_enabled = False
    session.editing_aux_mask_uuid = str(item.uuid)
    session.view_mode = "AUX_MASK"
    project.active_aux_mask_uuid = str(item.uuid)
    project.active_aux_mask_index = next(
        (
            index
            for index, candidate in enumerate(project.aux_masks)
            if str(candidate.uuid) == str(item.uuid)
        ),
        0,
    )
    if hasattr(project, "preview_mode"):
        project.preview_mode = "MASK"
    image = runtime.sync_canvas(context, project)
    if image is None:
        raise StudioError("The additional mask could not be used as a paint canvas")
    try:
        _assign_preview(project, image)
    except (ReferenceError, RuntimeError, ValueError):
        pass
    tag_studio_redraw()
    return True


def leave_aux_mask_edit(context: Any, project: Any) -> bool:
    """Return from an angle-independent mask to the stored face-shadow key."""

    session = active_session(context)
    if session is None or session.project_uuid != str(getattr(project, "uuid", "")):
        return False
    if not session.editing_aux_mask_uuid:
        return select_paint_key(
            context,
            project,
            key_uuid=session.paint_key_uuid,
            index=int(getattr(project, "active_angle_index", 0)),
        )
    session.editing_aux_mask_uuid = ""
    session.view_mode = "EDIT"
    if session.aux_previous_preview_mode and hasattr(project, "preview_mode"):
        project.preview_mode = session.aux_previous_preview_mode
    if session.aux_previous_onion_enabled and hasattr(project, "onion_enabled"):
        project.onion_enabled = True
    session.aux_previous_preview_mode = ""
    session.aux_previous_onion_enabled = False
    return select_paint_key(
        context,
        project,
        key_uuid=session.paint_key_uuid,
        index=int(getattr(project, "active_angle_index", 0)),
    )


def show_export_adjustment_review(context: Any, project: Any, image: Any) -> bool:
    """Show an export-only heatmap without leaving a paint surface armed."""

    session = active_session(context)
    if session is None or session.project_uuid != str(getattr(project, "uuid", "")):
        return False
    window = find_window(session.window_pointer)
    if window is None:
        return False
    shown = False
    for area in window.screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        space = area.spaces.active
        if hasattr(space, "ui_mode"):
            space.ui_mode = "VIEW"
        elif hasattr(space, "mode"):
            space.mode = "VIEW"
        space.image = image
        area.tag_redraw()
        shown = True
    session.export_review_active = shown
    tag_studio_redraw()
    return shown


def leave_export_adjustment_review(
    project: Any | None = None,
    *,
    session: StudioSession | None = None,
) -> bool:
    """Restore the authoring canvas after the read-only export review."""

    session = session or _SESSION
    if session is None or not session.export_review_active:
        return False
    project = project or resolve_session_project(session)
    image = None
    if project is not None:
        try:
            from .runtime import active_angle, resolve_display_image

            item = active_angle(project)
            image = resolve_display_image(project, item) if item is not None else None
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            image = None
    window = find_window(session.window_pointer)
    if window is not None:
        for area in window.screen.areas:
            if area.type != "IMAGE_EDITOR":
                continue
            space = area.spaces.active
            if hasattr(space, "ui_mode"):
                space.ui_mode = "PAINT"
            elif hasattr(space, "mode"):
                space.mode = "PAINT"
            if image is not None:
                space.image = image
            area.tag_redraw()
    session.export_review_active = False
    tag_studio_redraw()
    return True


def seek_preview(
    context: Any,
    project: Any,
    angle: float,
    *,
    show_in_image_editor: bool = False,
) -> float:
    """Scrub a transient result without changing the edit key or paint canvas."""

    value = max(0.0, min(90.0, float(angle)))
    session = active_session(context)
    if (
        session is not None
        and session.project_uuid == str(getattr(project, "uuid", ""))
        and session.editing_aux_mask_uuid
    ):
        return float(getattr(project, "seek_angle", value))
    project.seek_angle = value
    sign = -1.0 if str(getattr(project, "authoring_side", "RIGHT")) == "LEFT" else 1.0
    project.review_angle = sign * value
    if session is not None and session.project_uuid == str(getattr(project, "uuid", "")):
        ensure_studio_material_preview(context, session=session)
        session.view_mode = "PREVIEW"
        session.seek_angle = value
    preview_image = None
    try:
        from .live_preview import update_seek_preview

        preview_image = update_seek_preview(project, value)
    except (ImportError, ReferenceError, RuntimeError, ValueError):
        pass
    if show_in_image_editor and session is not None and preview_image is not None:
        window = find_window(session.window_pointer)
        if window is not None:
            for area in window.screen.areas:
                if area.type != "IMAGE_EDITOR":
                    continue
                area.spaces.active.image = preview_image
                area.tag_redraw()
    tag_studio_redraw()
    return value


def back_to_paint(context: Any, project: Any) -> bool:
    """Return Preview to the stored edit key before any mutating action."""

    session = active_session(context)
    if (
        session is not None
        and session.project_uuid == str(getattr(project, "uuid", ""))
        and session.editing_aux_mask_uuid
    ):
        from .runtime import sync_canvas

        return sync_canvas(context, project) is not None
    if session is not None and session.project_uuid == str(getattr(project, "uuid", "")):
        active_uuid = str(getattr(project, "active_angle_uuid", ""))
        canvas = getattr(context.scene.tool_settings.image_paint, "canvas", None)
        canvas_uuid = str(canvas.get("quick_sdf_angle_uuid", "")) if canvas is not None else ""
        # The common paint path is already synchronized. Re-selecting the key
        # here used to rebuild the preview material on every mouse press, which
        # caused a black shader-compilation frame and severe input latency.
        if (
            session.view_mode == "EDIT"
            and session.paint_key_uuid == active_uuid
            and canvas_uuid == active_uuid
            and not session.export_review_active
        ):
            return True
    key_uuid = session.paint_key_uuid if session is not None else str(getattr(project, "active_angle_uuid", ""))
    if select_paint_key(context, project, key_uuid=key_uuid):
        return True
    return select_paint_key(context, project, index=int(getattr(project, "active_angle_index", 0)))


def reconcile_view_state(context: Any | None = None) -> bool:
    """Re-resolve transient key IDs after Undo/Redo or key list edits."""

    context = context or bpy.context
    session = active_session(context)
    project = resolve_session_project(session)
    if session is None or project is None:
        return False
    if session.editing_aux_mask_uuid:
        from .runtime import aux_mask_for_uuid, resolve_aux_mask_image, sync_canvas

        item = aux_mask_for_uuid(project, session.editing_aux_mask_uuid)
        if resolve_aux_mask_image(project, item) is None:
            session.editing_aux_mask_uuid = ""
            session.view_mode = "EDIT"
        else:
            return sync_canvas(context, project) is not None
    resolved_index, item = _resolve_paint_key(project, key_uuid=session.paint_key_uuid)
    if item is None:
        resolved_index, item = _resolve_paint_key(project)
    if item is None or resolved_index is None:
        return False
    session.paint_key_uuid = str(getattr(item, "uuid", ""))
    session.paint_key_angle = float(getattr(item, "angle", 0.0))
    project.active_angle_index = resolved_index
    project.active_angle_uuid = session.paint_key_uuid
    if session.view_mode == "PREVIEW":
        from .runtime import sync_canvas

        sync_canvas(context, project)
        seek_preview(context, project, session.seek_angle)
        return True
    return select_paint_key(context, project, key_uuid=session.paint_key_uuid)


def prepare_stroke_brush(context: Any, project: Any) -> None:
    """Temporarily apply the selected binary colour without changing assets."""

    session = active_session(context)
    if session is None:
        return
    ensure_studio_material_preview(context, session=session)
    restore_stroke_brush(context)
    image_paint = context.scene.tool_settings.image_paint
    brush = getattr(image_paint, "brush", None)
    session.stroke_from_view3d = getattr(getattr(context, "area", None), "type", "") == "VIEW_3D"
    session.projection_hint = ""
    value = 1.0 if int(getattr(project, "paint_value", 0)) else 0.0
    inverse = 1.0 - value
    unified = getattr(image_paint, "unified_paint_settings", None)
    if unified is not None:
        session.stroke_unified_use_color = bool(unified.use_unified_color)
        session.stroke_unified_color = tuple(
            float(channel) for channel in unified.color[:3]
        )
        session.stroke_unified_secondary_color = tuple(
            float(channel) for channel in unified.secondary_color[:3]
        )
        try:
            # Force the transient unified colour path so the active Brush Asset
            # is never modified. Blender 5.1 otherwise chooses between Brush
            # and Unified colours based on the user's setting.
            unified.use_unified_color = True
            unified.color = (value, value, value)
            unified.secondary_color = (inverse, inverse, inverse)
            session.stroke_watchdog_deadline = time.monotonic() + 0.25
            if not bpy.app.timers.is_registered(_stroke_restore_watchdog):
                bpy.app.timers.register(_stroke_restore_watchdog, first_interval=0.05)
            return
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            try:
                if session.stroke_unified_color is not None:
                    unified.color = session.stroke_unified_color
                if session.stroke_unified_secondary_color is not None:
                    unified.secondary_color = session.stroke_unified_secondary_color
                if session.stroke_unified_use_color is not None:
                    unified.use_unified_color = session.stroke_unified_use_color
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                pass
            session.stroke_unified_use_color = None
            session.stroke_unified_color = None
            session.stroke_unified_secondary_color = None
    # Compatibility fallback for Blender builds without unified colour.
    if brush is None:
        return
    session.stroke_brush_name = str(getattr(brush, "name", ""))
    session.stroke_brush_color = tuple(float(channel) for channel in brush.color[:3])
    session.stroke_brush_secondary_color = tuple(
        float(channel) for channel in brush.secondary_color[:3]
    )
    try:
        brush.color = (value, value, value)
        brush.secondary_color = (inverse, inverse, inverse)
        brush.update_tag()
        session.stroke_watchdog_deadline = time.monotonic() + 0.25
        if not bpy.app.timers.is_registered(_stroke_restore_watchdog):
            bpy.app.timers.register(_stroke_restore_watchdog, first_interval=0.05)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        session.stroke_brush_name = ""
        session.stroke_brush_color = None
        session.stroke_brush_secondary_color = None


def restore_stroke_brush(
    context: Any | None = None,
    *,
    session: StudioSession | None = None,
) -> None:
    """Restore a Brush Asset after the native stroke, cancel, save, or exit."""

    session = session or _SESSION
    if session is None:
        return
    brush = bpy.data.brushes.get(session.stroke_brush_name) if session.stroke_brush_name else None
    if brush is not None and session.stroke_brush_color is not None:
        try:
            brush.color = session.stroke_brush_color
            if session.stroke_brush_secondary_color is not None:
                brush.secondary_color = session.stroke_brush_secondary_color
            brush.update_tag()
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is None or str(getattr(scene, "name", "")) != session.scene_name:
        scene = bpy.data.scenes.get(session.scene_name)
    image_paint = getattr(getattr(scene, "tool_settings", None), "image_paint", None)
    unified = getattr(image_paint, "unified_paint_settings", None)
    if unified is not None and session.stroke_unified_color is not None:
        try:
            unified.color = session.stroke_unified_color
            if session.stroke_unified_secondary_color is not None:
                unified.secondary_color = session.stroke_unified_secondary_color
            if session.stroke_unified_use_color is not None:
                unified.use_unified_color = session.stroke_unified_use_color
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    session.stroke_brush_name = ""
    session.stroke_brush_color = None
    session.stroke_brush_secondary_color = None
    session.stroke_unified_use_color = None
    session.stroke_unified_color = None
    session.stroke_unified_secondary_color = None
    session.stroke_watchdog_deadline = 0.0


def _stroke_restore_watchdog() -> float | None:
    """Restore temporary colours when Blender cancels a native modal stroke."""

    session = _SESSION
    if session is None:
        return None
    override_active = bool(
        session.stroke_brush_color is not None
        or session.stroke_unified_color is not None
    )
    if not override_active:
        return None
    window = find_window(session.window_pointer)
    modal_ids = {
        str(
            getattr(operator, "bl_idname", "")
            or getattr(getattr(operator, "bl_rna", None), "identifier", "")
        )
        for operator in getattr(window, "modal_operators", ())
    }
    if modal_ids & {"QUICKSDF_OT_range_paint", "QUICKSDF_OT_range_paint_invert"}:
        return 0.05
    if time.monotonic() < float(session.stroke_watchdog_deadline):
        return 0.05
    project = resolve_session_project(session)
    restore_stroke_brush(session=session)
    if project is not None:
        try:
            from .runtime import discard_interactive_paint_snapshot

            discard_interactive_paint_snapshot(project)
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            pass
    return None


def _cancel_stroke_watchdog() -> None:
    if bpy.app.timers.is_registered(_stroke_restore_watchdog):
        try:
            bpy.app.timers.unregister(_stroke_restore_watchdog)
        except ValueError:
            pass


def _release_project_history(project: Any | None = None) -> None:
    try:
        from .operators import clear_histories

        if project is None:
            clear_histories()
        else:
            clear_histories(
                str(getattr(project, "uuid", "")),
                release_fence=True,
            )
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        pass


def _discard_project_paint_snapshot(project: Any | None) -> None:
    if project is None:
        return
    try:
        from .runtime import discard_paint_snapshot

        discard_paint_snapshot(project)
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        pass


def set_projection_hint(context: Any | None, *, no_change: bool) -> None:
    """Clear the retired no-op warning without interrupting native painting.

    A same-colour stroke and a projection miss are indistinguishable after the
    native operator returns. Treating either as a red error was misleading and
    occupied the standard Brush Asset controls in the Tool Header.
    """

    session = active_session(context or bpy.context)
    if session is None:
        return
    session.projection_hint = ""
    tag_studio_redraw()


def studio_areas(window: Any) -> tuple[Any | None, Any | None, Any | None]:
    screen = getattr(window, "screen", None)
    areas = getattr(screen, "areas", ()) if screen else ()
    view = next((area for area in areas if area.type == "VIEW_3D"), None)
    image = next((area for area in areas if area.type == "IMAGE_EDITOR"), None)
    timeline = next((area for area in areas if area.type == TIMELINE_SPACE_TYPE), None)
    return view, image, timeline


def _window_region(area: Any) -> Any | None:
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _operator_succeeded(result: Any) -> bool:
    return bool(result and "FINISHED" in result)


def _valid_studio_layout(workspace: Any) -> bool:
    return any(
        sum(area.type == kind for area in screen.areas) >= 1
        for screen in workspace.screens
        for kind in ("VIEW_3D",)
    ) and any(
        any(area.type == "IMAGE_EDITOR" for area in screen.areas)
        and any(area.type == TIMELINE_SPACE_TYPE for area in screen.areas)
        for screen in workspace.screens
    )


def find_studio_workspace(project_uuid: str) -> Any | None:
    for workspace in bpy.data.workspaces:
        if (
            workspace.get(WORKSPACE_PROJECT_TAG) == project_uuid
            and int(workspace.get(WORKSPACE_VERSION_TAG, 0)) == WORKSPACE_LAYOUT_VERSION
            and _valid_studio_layout(workspace)
        ):
            return workspace
    return None


def _duplicate_workspace(context: Any, window: Any, project: Any) -> Any:
    previous = window.workspace
    before = {candidate.as_pointer() for candidate in bpy.data.workspaces}
    with context.temp_override(window=window):
        result = bpy.ops.workspace.duplicate()
    workspace = next(
        (candidate for candidate in bpy.data.workspaces if candidate.as_pointer() not in before),
        None,
    )
    if not _operator_succeeded(result) or workspace is None:
        raise StudioError("Could not create the Quick SDF Studio workspace")
    window.workspace = workspace
    workspace.name = WORKSPACE_BASENAME
    workspace[WORKSPACE_PROJECT_TAG] = str(project.uuid)
    workspace[WORKSPACE_VERSION_TAG] = WORKSPACE_LAYOUT_VERSION
    return workspace


def _collapse_screen(context: Any, window: Any) -> Any:
    screen = window.screen
    # ``screen.area_close`` is a modal UI command and cannot be used inside an
    # atomic Python transaction. Explicit-coordinate area_join is synchronous.
    attempts = 0
    while len(screen.areas) > 1:
        attempts += 1
        if attempts > 32:
            raise StudioError("Studio layout changes did not settle; try opening Studio again")
        areas = tuple(screen.areas)
        pair = next(
            (
                (first, second)
                for first in areas
                for second in areas
                if first.as_pointer() < second.as_pointer()
                and (
                    (abs(first.x - second.x) <= 4 and abs(first.width - second.width) <= 4 and (abs(first.y + first.height - second.y) <= 4 or abs(second.y + second.height - first.y) <= 4))
                    or
                    (abs(first.y - second.y) <= 4 and abs(first.height - second.height) <= 4 and (abs(first.x + first.width - second.x) <= 4 or abs(second.x + second.width - first.x) <= 4))
                )
            ),
            None,
        )
        if pair is None:
            raise StudioError("Could not find rectangular Studio areas to join")
        area, target = pair
        region = _window_region(area)
        override = {"window": window, "screen": screen, "area": area}
        if region is not None:
            override["region"] = region
        source_xy = (area.x + area.width // 2, area.y + area.height // 2)
        target_xy = (target.x + target.width // 2, target.y + target.height // 2)
        with context.temp_override(**override):
            result = bpy.ops.screen.area_join(source_xy=source_xy, target_xy=target_xy)
        if not _operator_succeeded(result):
            raise StudioError("Could not normalize the Studio workspace layout")
        screen = window.screen
    return screen.areas[0]


def _split_area(context: Any, window: Any, area: Any, direction: str, factor: float) -> None:
    region = _window_region(area)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        result = bpy.ops.screen.area_split(direction=direction, factor=factor)
    if not _operator_succeeded(result):
        raise StudioError("Could not split the Studio workspace")


def configure_workspace(context: Any, window: Any) -> tuple[Any, Any, Any]:
    """Replace the cloned screen with Image/3D over a compact timeline."""

    base = _collapse_screen(context, window)
    factor = max(0.08, min(0.24, TIMELINE_HEIGHT / max(1.0, float(base.height))))
    _split_area(context, window, base, "HORIZONTAL", factor)
    screen = window.screen
    bottom = min(screen.areas, key=lambda area: area.y + area.height * 0.5)
    top = max(screen.areas, key=lambda area: area.y + area.height * 0.5)
    _split_area(context, window, top, "VERTICAL", 0.5)

    areas = list(screen.areas)
    bottom = min(areas, key=lambda area: area.y + area.height * 0.5)
    top_areas = sorted((area for area in areas if area != bottom), key=lambda area: area.x)
    if len(top_areas) != 2:
        raise StudioError("Studio workspace did not produce the expected three areas")
    image_area, view_area = top_areas
    image_area.type = "IMAGE_EDITOR"
    view_area.type = "VIEW_3D"
    # A Dope Sheet exposes Blender's native frame playhead even with all of
    # its regions hidden.  That cursor looks like the Quick SDF angle seek,
    # changes the animated pose, and can mark the normal guide stale.  A Node
    # Editor gives the custom timeline a non-temporal host with no playhead.
    bottom.type = TIMELINE_SPACE_TYPE

    image_space = image_area.spaces.active
    if hasattr(image_space, "ui_mode"):
        image_space.ui_mode = "PAINT"
    elif hasattr(image_space, "mode"):
        image_space.mode = "PAINT"
    for space in (image_space, view_area.spaces.active):
        if hasattr(space, "show_region_tool_header"):
            space.show_region_tool_header = True
        if hasattr(space, "show_region_header"):
            space.show_region_header = True
    timeline_space = bottom.spaces.active
    if hasattr(timeline_space, "tree_type"):
        timeline_space.tree_type = "ShaderNodeTree"
    if hasattr(timeline_space, "show_gizmo"):
        timeline_space.show_gizmo = True
    if hasattr(timeline_space, "show_gizmo_active_node"):
        timeline_space.show_gizmo_active_node = False
    for name in (
        "show_region_header", "show_region_footer", "show_region_toolbar",
        "show_region_tool_header", "show_region_ui",
    ):
        if hasattr(timeline_space, name):
            setattr(timeline_space, name, False)
    return view_area, image_area, bottom


def _set_active_object(context: Any, obj: Any) -> None:
    for selected in tuple(getattr(context, "selected_objects", ())):
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _mode_set(context: Any, window: Any, area: Any, mode: str) -> None:
    region = _window_region(area)
    override = {"window": window, "screen": window.screen, "area": area}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        if getattr(context.object, "mode", "OBJECT") != mode:
            result = bpy.ops.object.mode_set(mode=mode)
            if not _operator_succeeded(result):
                raise StudioError(f"Could not enter Blender mode: {mode}")


def _restore_selection_and_mode(context: Any, session: StudioSession, window: Any) -> None:
    for obj in context.view_layer.objects:
        if obj.select_get():
            obj.select_set(False)
    for name in session.previous_selected_object_names:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.name in context.view_layer.objects:
            obj.select_set(True)
    active = bpy.data.objects.get(session.previous_active_object_name)
    if active is not None and active.name in context.view_layer.objects:
        context.view_layer.objects.active = active
        if session.previous_mode and session.previous_mode != "OBJECT":
            view = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
            if view is not None:
                try:
                    _mode_set(context, window, view, session.previous_mode)
                except (RuntimeError, StudioError):
                    pass


def _restore_paint_settings(scene: Any, session: StudioSession) -> None:
    image_paint = scene.tool_settings.image_paint
    if session.previous_image_paint_mode in {"MATERIAL", "IMAGE"}:
        image_paint.mode = session.previous_image_paint_mode
    image_paint.canvas = bpy.data.images.get(session.previous_canvas_name) if session.previous_canvas_name else None


def _studio_view_spaces(window: Any | None) -> tuple[Any, ...]:
    if window is None or getattr(window, "screen", None) is None:
        return ()
    return tuple(
        area.spaces.active
        for area in window.screen.areas
        if area.type == "VIEW_3D" and hasattr(area.spaces.active, "clip_start")
    )


def ensure_studio_material_preview(
    context: Any | None = None,
    *,
    session: StudioSession | None = None,
) -> bool:
    """Keep the 3D paint surface visible after users change viewport shading.

    Texture Paint can remain active while a View3D is switched back to Solid.
    In that state Blender still edits the Image canvas, but the temporary Quick
    SDF material is not displayed.  Studio owns its cloned workspace, so it is
    safe to restore Material Preview whenever editing resumes.
    """

    session = session or active_session(context or bpy.context)
    if session is None:
        return False
    window = find_window(session.window_pointer)
    changed = False
    for space in _studio_view_spaces(window):
        shading = getattr(space, "shading", None)
        if shading is not None and str(getattr(shading, "type", "")) != "MATERIAL":
            shading.type = "MATERIAL"
            changed = True
    if changed:
        tag_studio_redraw()
    return True


def _adaptive_clip_start(obj: Any | None) -> float:
    dimensions = tuple(abs(float(value)) for value in getattr(obj, "dimensions", (1.0, 1.0, 1.0)))
    extent = max(dimensions or (1.0,))
    return max(1.0e-6, extent * 1.0e-4)


def _apply_projection_settings(scene: Any, session: StudioSession, window: Any, obj: Any | None) -> None:
    image_paint = scene.tool_settings.image_paint
    if session.previous_normal_falloff is None and hasattr(image_paint, "use_normal_falloff"):
        session.previous_normal_falloff = bool(image_paint.use_normal_falloff)
    if hasattr(image_paint, "use_normal_falloff"):
        image_paint.use_normal_falloff = False

    spaces = _studio_view_spaces(window)
    if not session.previous_clip_starts:
        session.previous_clip_starts = tuple(
            (int(space.as_pointer()), float(space.clip_start)) for space in spaces
        )
    clip_start = _adaptive_clip_start(obj)
    for space in spaces:
        space.clip_start = min(float(space.clip_start), clip_start)
    session.projection_settings_suspended = False


def _restore_projection_settings(scene: Any | None, session: StudioSession, window: Any | None) -> None:
    if scene is not None and session.previous_normal_falloff is not None:
        image_paint = scene.tool_settings.image_paint
        if hasattr(image_paint, "use_normal_falloff"):
            image_paint.use_normal_falloff = session.previous_normal_falloff
    previous = dict(session.previous_clip_starts)
    for space in _studio_view_spaces(window):
        value = previous.get(int(space.as_pointer()))
        if value is not None:
            space.clip_start = value
    session.projection_settings_suspended = True


def _restore_preview(project: Any | None) -> None:
    try:
        from .preview import restore_preview_materials

        restore_preview_materials(project)
    except (ImportError, ReferenceError, RuntimeError):
        pass


def _assign_preview(project: Any, image: Any | None) -> None:
    from .preview import assign_preview_material, set_preview_image

    obj = getattr(project, "target_object", None)
    slot_index = int(getattr(project, "material_slot_index", -1))
    material_name = str(getattr(project, "preview_material_name", ""))
    material = bpy.data.materials.get(material_name) if material_name else None
    if (
        obj is not None
        and 0 <= slot_index < len(getattr(obj, "material_slots", ()))
        and material is not None
        and obj.material_slots[slot_index].material == material
    ):
        # Key selection only changes the Image node. Rebuilding and rewiring
        # the whole material here used to compile a shader on every stroke and
        # also exposed the preview graph to accidental self-links.
        if image is not None:
            set_preview_image(project, image)
        project.preview_enabled = True
        project.material_override_active = True
        return
    assign_preview_material(project, image)


def _remove_workspace(workspace: Any | None) -> None:
    if workspace is None:
        return
    try:
        bpy.data.workspaces.remove(workspace)
    except (ReferenceError, RuntimeError):
        pass


def _join_one_area(context: Any, window: Any) -> bool:
    """Join one rectangular pair and return whether more areas remain."""

    screen = window.screen
    areas = tuple(screen.areas)
    if len(areas) <= 1:
        return False
    pair = next(
        (
            (first, second)
            for first in areas
            for second in areas
            if first.as_pointer() < second.as_pointer()
            and (
                (
                    abs(first.x - second.x) <= 4
                    and abs(first.width - second.width) <= 4
                    and (
                        abs(first.y + first.height - second.y) <= 4
                        or abs(second.y + second.height - first.y) <= 4
                    )
                )
                or (
                    abs(first.y - second.y) <= 4
                    and abs(first.height - second.height) <= 4
                    and (
                        abs(first.x + first.width - second.x) <= 4
                        or abs(second.x + second.width - first.x) <= 4
                    )
                )
            )
        ),
        None,
    )
    if pair is None:
        raise StudioError("Could not find adjacent workspace areas to join")
    source, target = pair
    region = _window_region(source)
    override = {"window": window, "screen": screen, "area": source}
    if region is not None:
        override["region"] = region
    with context.temp_override(**override):
        result = bpy.ops.screen.area_join(
            source_xy=(source.x + source.width // 2, source.y + source.height // 2),
            target_xy=(target.x + target.width // 2, target.y + target.height // 2),
        )
    if not _operator_succeeded(result):
        raise StudioError("Could not normalize the Studio workspace layout")
    return len(areas) > 2


def _configure_area_types(window: Any) -> tuple[Any, Any, Any]:
    areas = list(window.screen.areas)
    if len(areas) != 3:
        raise StudioError("Studio workspace did not produce the expected three areas")
    bottom = min(areas, key=lambda area: area.y + area.height * 0.5)
    top_areas = sorted((area for area in areas if area != bottom), key=lambda area: area.x)
    if len(top_areas) != 2:
        raise StudioError("Studio workspace top row is incomplete")
    image_area, view_area = top_areas
    image_area.type = "IMAGE_EDITOR"
    view_area.type = "VIEW_3D"
    bottom.type = TIMELINE_SPACE_TYPE
    image_space = image_area.spaces.active
    if hasattr(image_space, "ui_mode"):
        image_space.ui_mode = "PAINT"
    elif hasattr(image_space, "mode"):
        image_space.mode = "PAINT"
    for space in (image_space, view_area.spaces.active):
        if hasattr(space, "show_region_tool_header"):
            space.show_region_tool_header = True
        if hasattr(space, "show_region_header"):
            space.show_region_header = True
    view_space = view_area.spaces.active
    if hasattr(view_space, "shading"):
        view_space.shading.type = "MATERIAL"
    timeline_space = bottom.spaces.active
    if hasattr(timeline_space, "tree_type"):
        timeline_space.tree_type = "ShaderNodeTree"
    if hasattr(timeline_space, "show_gizmo"):
        timeline_space.show_gizmo = True
    if hasattr(timeline_space, "show_gizmo_active_node"):
        timeline_space.show_gizmo_active_node = False
    for name in (
        "show_region_header", "show_region_footer", "show_region_toolbar",
        "show_region_tool_header", "show_region_ui",
    ):
        if hasattr(timeline_space, name):
            setattr(timeline_space, name, False)
    return view_area, image_area, bottom


def _frame_studio_content(context: Any, window: Any, view_area: Any, image_area: Any) -> None:
    """Make both editors useful immediately without changing the source workspace."""

    view_region = _window_region(view_area)
    if view_region is not None:
        try:
            with context.temp_override(
                window=window,
                screen=window.screen,
                area=view_area,
                region=view_region,
            ):
                bpy.ops.view3d.view_selected(use_all_regions=False)
        except RuntimeError:
            pass
        obj = getattr(context.view_layer.objects, "active", None)
        region_3d = getattr(view_area.spaces.active, "region_3d", None)
        if obj is not None and region_3d is not None and getattr(obj, "bound_box", None):
            from mathutils import Vector

            corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            center = sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)
            radius = max((corner - center).length for corner in corners)
            region_3d.view_location = center
            region_3d.view_distance = max(0.05, radius * 2.4)
    image_region = _window_region(image_area)
    if image_region is not None:
        try:
            with context.temp_override(
                window=window,
                screen=window.screen,
                area=image_area,
                region=image_region,
            ):
                bpy.ops.image.view_all(fit_view=True)
        except RuntimeError:
            pass


def _rollback_pending(error: BaseException | None = None) -> None:
    global _PENDING
    pending = _PENDING
    _PENDING = None
    if pending is None:
        return
    session = pending.session
    window = find_window(session.window_pointer)
    project = resolve_session_project(session)
    _restore_preview(project)
    if project is not None and error is not None:
        try:
            project.diagnostic_message = f"Could not open Studio: {error}"
        except (AttributeError, ReferenceError):
            pass
    restore_stroke_brush(session=session)
    _discard_project_paint_snapshot(project)
    _cancel_stroke_watchdog()
    _release_project_history(project)
    if window is None:
        return
    failed_workspace = bpy.data.workspaces.get(session.workspace_name)
    scene = bpy.data.scenes.get(session.scene_name)
    _restore_projection_settings(scene, session, window)
    previous = bpy.data.workspaces.get(session.previous_workspace_name)
    if previous is not None:
        window.workspace = previous
    if scene is not None:
        _restore_paint_settings(scene, session)
    try:
        _restore_selection_and_mode(bpy.context, session, window)
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    if session.workspace_created and failed_workspace is not None and window.workspace is not failed_workspace:
        _remove_workspace(failed_workspace)


def _continue_enter() -> float | None:
    """Advance workspace topology by one event-loop-safe operation."""

    global _PENDING, _SESSION
    pending = _PENDING
    if pending is None:
        return None
    session = pending.session
    window = find_window(session.window_pointer)
    project = resolve_session_project(session)
    if window is None or project is None:
        _rollback_pending(StudioError("The Studio target disappeared while opening"))
        return None
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if workspace is None:
        _rollback_pending(StudioError("The Studio workspace disappeared while opening"))
        return None
    window.workspace = workspace
    context = bpy.context
    try:
        if pending.state == "JOIN":
            if len(window.screen.areas) > 1:
                _join_one_area(context, window)
                return 0.03
            pending.state = "SPLIT_BOTTOM"
        if pending.state == "SPLIT_BOTTOM":
            base = window.screen.areas[0]
            factor = max(0.08, min(0.24, TIMELINE_HEIGHT / max(1.0, float(base.height))))
            _split_area(context, window, base, "HORIZONTAL", factor)
            pending.state = "SPLIT_TOP"
            return 0.03
        if pending.state == "SPLIT_TOP":
            if len(window.screen.areas) < 2:
                return 0.03
            top = max(window.screen.areas, key=lambda area: area.y + area.height * 0.5)
            _split_area(context, window, top, "VERTICAL", 0.5)
            pending.state = "FINISH"
            return 0.03
        if pending.state == "FINISH":
            if len(window.screen.areas) < 3:
                return 0.03
            view_area, image_area, _timeline_area = _configure_area_types(window)
            obj = project.target_object
            _set_active_object(context, obj)
            scene = bpy.data.scenes.get(session.scene_name) or context.scene
            scene.tool_settings.image_paint.mode = "IMAGE"
            from .runtime import sync_canvas

            image = sync_canvas(context, project)
            if image is None:
                raise StudioError("The active angle image could not be used as a paint canvas")
            _index, active_item = _resolve_paint_key(project)
            if active_item is not None:
                session.view_mode = "EDIT"
                session.paint_key_uuid = str(getattr(active_item, "uuid", ""))
                session.paint_key_angle = float(getattr(active_item, "angle", 0.0))
                session.seek_angle = session.paint_key_angle
                project.seek_angle = session.paint_key_angle
                project.review_angle = _side_signed_angle(project, active_item)
            session.show_first_stroke_hint = not bool(getattr(project, "first_stroke_complete", False))
            if str(getattr(project, "base_source", "NORMAL_GUIDE")) == "NORMAL_GUIDE":
                session.first_hint_text = (
                    "A light-sweep guide from rear oblique to full light is ready. "
                    "Paint only the areas you want to adjust."
                )
            else:
                session.first_hint_text = "Choose an angle · choose Light or Shadow · paint"
            _mode_set(context, window, view_area, "TEXTURE_PAINT")
            from .tools import activate_tools

            activate_tools(context, window=window)
            _frame_studio_content(context, window, view_area, image_area)
            _apply_projection_settings(scene, session, window, obj)
            if pending.manage_preview:
                _assign_preview(project, image)
            project.warning_message = ""
            _SESSION = session
            _PENDING = None
            from .operators import arm_undo_fence

            arm_undo_fence(str(getattr(project, "uuid", "")))
            tag_studio_redraw()
            return None
    except Exception as error:
        _rollback_pending(error)
        return None
    return 0.03


def _leave_object_mode(context: Any, window: Any, obj: Any | None) -> None:
    if obj is None or str(getattr(obj, "mode", "OBJECT")) == "OBJECT":
        return
    view_area = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
    if view_area is None:
        raise StudioError("Quick SDF Studio has no 3D View")
    _set_active_object(context, obj)
    _mode_set(context, window, view_area, "OBJECT")


def _configure_session_target(
    context: Any,
    session: StudioSession,
    project: Any,
    *,
    frame_content: bool,
) -> Any:
    """Synchronize an already-built Studio workspace to one project."""

    scene, project_index, obj, _preflight_image = _preflight_studio_target(context, project)
    window = find_window(session.window_pointer)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if window is None or workspace is None:
        raise StudioError("The Quick SDF Studio workspace is no longer available")
    window.workspace = workspace
    workspace[WORKSPACE_PROJECT_TAG] = str(getattr(project, "uuid", ""))
    workspace[WORKSPACE_VERSION_TAG] = WORKSPACE_LAYOUT_VERSION
    scene.quick_sdf_active_project_index = project_index
    session.project_uuid = str(getattr(project, "uuid", ""))
    session.scene_name = scene.name

    view_area, image_area, _timeline_area = studio_areas(window)
    if view_area is None or image_area is None:
        raise StudioError("Quick SDF Studio needs both a 3D View and an Image Editor")
    _set_active_object(context, obj)
    scene.tool_settings.image_paint.mode = "IMAGE"
    if not select_paint_key(
        context,
        project,
        key_uuid=str(getattr(project, "active_angle_uuid", "")),
        index=int(getattr(project, "active_angle_index", 0)),
    ):
        raise StudioError("The active angle image could not be used as a paint canvas")
    _mode_set(context, window, view_area, "TEXTURE_PAINT")
    from .tools import activate_tools

    activate_tools(context, window=window)
    _apply_projection_settings(scene, session, window, obj)
    ensure_studio_material_preview(context, session=session)
    if frame_content:
        _frame_studio_content(context, window, view_area, image_area)
    try:
        from .runtime import refresh_base_staleness

        refresh_base_staleness(project, scene)
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    project.warning_message = ""
    return project


def _focus_or_switch_studio(context: Any, project: Any) -> StudioSession:
    """Focus the current target or atomically retarget the live Studio session."""

    session = _SESSION
    if session is None:
        raise StudioError("Quick SDF Studio is not open")
    window = find_window(session.window_pointer)
    if window is None:
        raise StudioError("The Quick SDF Studio window is no longer available")
    context_window = getattr(context, "window", None)
    if context_window is None or context_window.as_pointer() != session.window_pointer:
        raise StudioError("Quick SDF Studio is open in another Blender window")

    target_uuid = str(getattr(project, "uuid", ""))
    if not target_uuid:
        raise StudioError("The Quick SDF project has no UUID")
    _preflight_studio_target(context, project)
    caller_workspace = window.workspace
    if session.project_uuid == target_uuid:
        _activate_project(project)
        if session.editing_aux_mask_uuid:
            workspace = bpy.data.workspaces.get(session.workspace_name)
            if workspace is not None:
                window.workspace = workspace
            from .runtime import sync_canvas

            sync_canvas(context, project)
            tag_studio_redraw()
            return session
        _configure_session_target(context, session, project, frame_content=False)
        tag_studio_redraw()
        return session

    old_project = resolve_session_project(session)
    if old_project is None:
        raise StudioError("The current Quick SDF Studio target is unavailable")
    if session.editing_aux_mask_uuid:
        leave_aux_mask_edit(context, old_project)
    old_scene, old_index = _project_location(old_project)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if old_scene is None or old_index is None or workspace is None:
        raise StudioError("The current Quick SDF Studio state could not be restored")

    old_session_state = {
        "project_uuid": session.project_uuid,
        "scene_name": session.scene_name,
        "view_mode": session.view_mode,
        "paint_key_uuid": session.paint_key_uuid,
        "paint_key_angle": session.paint_key_angle,
        "seek_angle": session.seek_angle,
        "show_first_stroke_hint": session.show_first_stroke_hint,
        "first_hint_text": session.first_hint_text,
        "projection_hint": session.projection_hint,
        "export_review_active": session.export_review_active,
    }
    old_workspace_tag = str(workspace.get(WORKSPACE_PROJECT_TAG, ""))
    target_obj = getattr(project, "target_object", None)
    old_obj = getattr(old_project, "target_object", None)
    switched = False
    try:
        window.workspace = workspace
        _leave_object_mode(context, window, old_obj)
        restore_stroke_brush(session=session)
        _cancel_stroke_watchdog()
        _restore_preview(old_project)
        try:
            from .runtime import discard_paint_snapshot

            discard_paint_snapshot(old_project)
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            pass
        try:
            from .live_preview import release_project

            release_project(old_project)
        except (ImportError, ReferenceError, RuntimeError):
            pass

        session.view_mode = "EDIT"
        session.paint_key_uuid = ""
        session.paint_key_angle = 0.0
        session.seek_angle = 0.0
        session.projection_hint = ""
        session.export_review_active = False
        session.show_first_stroke_hint = not bool(getattr(project, "first_stroke_complete", False))
        session.first_hint_text = (
            "A light-sweep guide from rear oblique to full light is ready. "
            "Paint only the areas you want to adjust."
            if str(getattr(project, "base_source", "NORMAL_GUIDE")) == "NORMAL_GUIDE"
            else "Choose an angle · choose Light or Shadow · paint"
        )
        _configure_session_target(context, session, project, frame_content=True)
        switched = True
        _release_project_history(old_project)
        from .operators import arm_undo_fence

        arm_undo_fence(str(getattr(project, "uuid", "")))
        tag_studio_redraw()
        return session
    except Exception as error:
        # The target is disposable until the final synchronization succeeds.
        _restore_preview(project)
        try:
            _leave_object_mode(context, window, target_obj)
        except (ReferenceError, RuntimeError, StudioError):
            pass
        for name, value in old_session_state.items():
            setattr(session, name, value)
        workspace[WORKSPACE_PROJECT_TAG] = old_workspace_tag or session.project_uuid
        old_scene.quick_sdf_active_project_index = int(old_index)
        try:
            window.workspace = workspace
            _configure_session_target(context, session, old_project, frame_content=False)
        except Exception as rollback_error:
            # Never leave a half-switched paint session armed.
            try:
                exit_studio(context, reason="switch-rollback")
            except Exception:
                pass
            raise StudioError(
                f"Could not open this model, and Studio recovery failed: {rollback_error}"
            ) from error
        finally:
            if caller_workspace.as_pointer() != workspace.as_pointer():
                window.workspace = caller_workspace
        raise StudioError(f"Could not open this model in Quick SDF Studio: {error}") from error
    finally:
        if not switched:
            tag_studio_redraw()


def _continue_switch() -> float | None:
    """Complete a workspace focus/switch after Blender changes screens."""

    global _SWITCH_PENDING
    pending = _SWITCH_PENDING
    session = _SESSION
    if pending is None or session is None:
        _SWITCH_PENDING = None
        return None
    project = _resolve_project_uuid(pending.project_uuid)
    window = find_window(session.window_pointer)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if project is None or window is None or workspace is None:
        _SWITCH_PENDING = None
        return None
    window.workspace = workspace
    view, image, _timeline = studio_areas(window)
    if view is None or image is None:
        pending.attempts += 1
        if pending.attempts < 60:
            return 0.03
        try:
            project.diagnostic_message = "Could not focus the Quick SDF Studio workspace"
        except (AttributeError, ReferenceError):
            pass
        _SWITCH_PENDING = None
        return None
    _SWITCH_PENDING = None
    try:
        _focus_or_switch_studio(bpy.context, project)
    except Exception as error:
        try:
            project.diagnostic_message = str(error)
            project.warning_message = str(error)
        except (AttributeError, ReferenceError):
            pass
    return None


def _queue_studio_switch(context: Any, project: Any) -> StudioSession:
    global _SWITCH_PENDING
    session = _SESSION
    if session is None:
        raise StudioError("Quick SDF Studio is not open")
    context_window = getattr(context, "window", None)
    if context_window is None or context_window.as_pointer() != session.window_pointer:
        raise StudioError("Quick SDF Studio is open in another Blender window")
    _preflight_studio_target(context, project)
    window = find_window(session.window_pointer)
    workspace = bpy.data.workspaces.get(session.workspace_name)
    if window is None or workspace is None:
        raise StudioError("The Quick SDF Studio workspace is no longer available")
    _SWITCH_PENDING = _PendingSwitch(str(getattr(project, "uuid", "")))
    window.workspace = workspace
    if not bpy.app.timers.is_registered(_continue_switch):
        bpy.app.timers.register(_continue_switch, first_interval=0.01)
    return session


def open_or_switch_studio(
    context: Any,
    project: Any,
    *,
    manage_preview: bool = True,
) -> StudioSession:
    """Artist-facing entry point: open, refocus, or switch in one action."""

    global _PENDING
    target_uuid = str(getattr(project, "uuid", ""))
    if _PENDING is not None:
        if _PENDING.session.project_uuid == target_uuid:
            window = find_window(_PENDING.session.window_pointer)
            workspace = bpy.data.workspaces.get(_PENDING.session.workspace_name)
            if window is not None and workspace is not None:
                window.workspace = workspace
            return _PENDING.session
        _rollback_pending()
    if _SESSION is not None:
        session = _SESSION
        window = find_window(session.window_pointer)
        workspace = bpy.data.workspaces.get(session.workspace_name)
        if window is None or workspace is None:
            raise StudioError("The Quick SDF Studio workspace is no longer available")
        if window.workspace.as_pointer() != workspace.as_pointer():
            return _queue_studio_switch(context, project)
        return _focus_or_switch_studio(context, project)
    _activate_project(project)
    return enter_studio(context, project, manage_preview=manage_preview)


def enter_studio(
    context: Any,
    project: Any,
    *,
    sync_canvas_fn: Callable[[Any, Any], Any] | None = None,
    manage_preview: bool = True,
) -> StudioSession:
    """Atomically enter Studio and publish the singleton only after success."""

    global _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    if bpy.app.background or getattr(context, "window", None) is None:
        raise StudioUnavailableError("Quick SDF Studio requires an interactive Blender window")
    project_uuid = str(getattr(project, "uuid", ""))
    if not project_uuid:
        raise StudioError("The Quick SDF project has no UUID")
    if _SESSION is not None:
        return open_or_switch_studio(context, project, manage_preview=manage_preview)
    if _PENDING is not None:
        if _PENDING.session.project_uuid == project_uuid:
            return _PENDING.session
        raise StudioBusyError("Quick SDF Studio is already opening")
    obj = getattr(project, "target_object", None)
    if obj is None or getattr(obj, "type", "") != "MESH":
        raise StudioError("The Quick SDF target must be a local mesh")
    try:
        from .runtime import refresh_base_staleness

        refresh_base_staleness(project, context.scene)
    except (AttributeError, ReferenceError, RuntimeError):
        pass

    window = context.window
    scene = context.scene
    previous_workspace = window.workspace
    previous_active = context.view_layer.objects.active
    image_paint = scene.tool_settings.image_paint
    previous_canvas = getattr(image_paint, "canvas", None)
    session = StudioSession(
        project_uuid=project_uuid,
        window_pointer=window.as_pointer(),
        scene_name=scene.name,
        workspace_name="",
        previous_workspace_name=previous_workspace.name,
        previous_active_object_name=previous_active.name if previous_active else "",
        previous_selected_object_names=tuple(obj.name for obj in context.selected_objects),
        previous_mode=str(getattr(previous_active, "mode", "OBJECT")),
        previous_image_paint_mode=str(getattr(image_paint, "mode", "MATERIAL")),
        previous_canvas_name=previous_canvas.name if previous_canvas else "",
    )
    try:
        workspace = find_studio_workspace(project_uuid)
        if workspace is None:
            workspace = _duplicate_workspace(context, window, project)
            session.workspace_created = True
            state = "JOIN"
        else:
            window.workspace = workspace
            state = "FINISH"
        session.workspace_name = workspace.name
        _PENDING = _PendingEnter(session=session, manage_preview=manage_preview, state=state)
        project.warning_message = "Opening Quick SDF Studio…"
        if not bpy.app.timers.is_registered(_continue_enter):
            bpy.app.timers.register(_continue_enter, first_interval=0.01)
        return session
    except Exception:
        _restore_preview(project)
        window.workspace = previous_workspace
        _restore_paint_settings(scene, session)
        _restore_selection_and_mode(context, session, window)
        raise


def exit_studio(
    context: Any | None = None,
    *,
    remove_workspace: bool = False,
    reason: str = "user",
) -> bool:
    """Leave Studio and restore the captured user workspace and paint state."""

    global _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    if _PENDING is not None:
        _rollback_pending()
        if _SESSION is None:
            return True
    session = _SESSION
    if session is None:
        return False
    context = context or bpy.context
    window = find_window(session.window_pointer)
    project = resolve_session_project(session)
    if project is not None and session.editing_aux_mask_uuid:
        session.editing_aux_mask_uuid = ""
        if session.aux_previous_preview_mode and hasattr(project, "preview_mode"):
            project.preview_mode = session.aux_previous_preview_mode
        if session.aux_previous_onion_enabled and hasattr(project, "onion_enabled"):
            project.onion_enabled = True
        session.aux_previous_preview_mode = ""
        session.aux_previous_onion_enabled = False
    _restore_preview(project)
    restore_stroke_brush(session=session)
    _discard_project_paint_snapshot(project)
    _cancel_stroke_watchdog()
    _release_project_history(project)
    scene = bpy.data.scenes.get(session.scene_name) or getattr(window, "scene", None)
    _restore_projection_settings(scene, session, window)
    try:
        from .live_preview import release_project

        release_project(project)
    except (ImportError, ReferenceError, RuntimeError):
        pass
    if window is None:
        _SESSION = None
        return True
    studio_workspace = bpy.data.workspaces.get(session.workspace_name)
    try:
        target = getattr(project, "target_object", None) if project else None
        view_area = next((area for area in window.screen.areas if area.type == "VIEW_3D"), None)
        if target is not None and view_area is not None and target.mode == "TEXTURE_PAINT":
            context.view_layer.objects.active = target
            _mode_set(context, window, view_area, "OBJECT")
    except (ReferenceError, RuntimeError, StudioError):
        pass
    previous_workspace = bpy.data.workspaces.get(session.previous_workspace_name)
    if previous_workspace is not None:
        window.workspace = previous_workspace
    if scene is not None:
        try:
            _restore_paint_settings(scene, session)
        except (AttributeError, ReferenceError):
            pass
    try:
        _restore_selection_and_mode(context, session, window)
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    _SESSION = None
    if remove_workspace and studio_workspace is not None and window.workspace is not studio_workspace:
        _remove_workspace(studio_workspace)
    tag_studio_redraw()
    return True


def dismiss_first_stroke_hint() -> None:
    if _SESSION is not None:
        _SESSION.show_first_stroke_hint = False
        tag_studio_redraw()


def tag_studio_redraw() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "IMAGE_EDITOR", TIMELINE_SPACE_TYPE}:
                area.tag_redraw()


@persistent
def _save_pre(_unused: Any) -> None:
    if _SESSION is None:
        return
    project = resolve_session_project()
    if (
        project is not None
        and _SESSION.editing_aux_mask_uuid
        and _SESSION.aux_previous_preview_mode
        and hasattr(project, "preview_mode")
    ):
        # Aux edit mode is transient; save the artist's normal preview choice.
        project.preview_mode = _SESSION.aux_previous_preview_mode
    _restore_preview(project)
    window = find_window(_SESSION.window_pointer)
    scene = bpy.data.scenes.get(_SESSION.scene_name) or getattr(window, "scene", None)
    restore_stroke_brush(session=_SESSION)
    _discard_project_paint_snapshot(project)
    _cancel_stroke_watchdog()
    _restore_projection_settings(scene, _SESSION, window)
    if project is not None:
        _SESSION.onion_suspended = bool(getattr(project, "onion_enabled", False))
        if _SESSION.onion_suspended:
            project.onion_enabled = False
        try:
            from .live_preview import purge_project_temporaries

            purge_project_temporaries(project)
        except (ImportError, ReferenceError, RuntimeError):
            pass
    _SESSION.preview_suspended = True


@persistent
def _save_post(_unused: Any) -> None:
    if _SESSION is None or not _SESSION.preview_suspended:
        return
    project = resolve_session_project()
    if project is not None:
        try:
            window = find_window(_SESSION.window_pointer)
            scene = bpy.data.scenes.get(_SESSION.scene_name) or getattr(window, "scene", None)
            if window is not None and scene is not None:
                _apply_projection_settings(scene, _SESSION, window, getattr(project, "target_object", None))
            from .runtime import (
                active_angle,
                aux_mask_for_uuid,
                resolve_aux_mask_image,
                resolve_display_image,
            )

            if _SESSION.editing_aux_mask_uuid:
                if hasattr(project, "preview_mode"):
                    project.preview_mode = "MASK"
                aux_item = aux_mask_for_uuid(project, _SESSION.editing_aux_mask_uuid)
                image = resolve_aux_mask_image(project, aux_item)
            else:
                item = active_angle(project)
                image = resolve_display_image(project, item) if item else None
            _assign_preview(project, image)
            if _SESSION.view_mode == "PREVIEW":
                from .live_preview import update_seek_preview

                update_seek_preview(project, float(_SESSION.seek_angle))
            if _SESSION.onion_suspended:
                project.onion_enabled = True
        except (ImportError, ReferenceError, RuntimeError, ValueError):
            pass
    _SESSION.preview_suspended = False
    _SESSION.onion_suspended = False


@persistent
def _load_pre(_unused: Any) -> None:
    global _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    try:
        from .operators import shutdown_export_job

        shutdown_export_job(message="Export cancelled while loading a file")
    except (ImportError, RuntimeError):
        pass
    if _PENDING is not None:
        _rollback_pending()
    if _SESSION is not None:
        window = find_window(_SESSION.window_pointer)
        scene = bpy.data.scenes.get(_SESSION.scene_name) or getattr(window, "scene", None)
        project = resolve_session_project(_SESSION)
        if project is not None and _SESSION.editing_aux_mask_uuid:
            if _SESSION.aux_previous_preview_mode and hasattr(project, "preview_mode"):
                project.preview_mode = _SESSION.aux_previous_preview_mode
            if _SESSION.aux_previous_onion_enabled and hasattr(project, "onion_enabled"):
                project.onion_enabled = True
        restore_stroke_brush(session=_SESSION)
        _discard_project_paint_snapshot(project)
        _cancel_stroke_watchdog()
        _restore_projection_settings(scene, _SESSION, window)
    try:
        from .preview import restore_all_preview_materials

        restore_all_preview_materials()
    except (ImportError, ReferenceError, RuntimeError):
        pass
    _release_project_history()
    _SESSION = None


@persistent
def _undo_post(_unused: Any) -> None:
    global _SESSION
    if _SESSION is not None and (find_window(_SESSION.window_pointer) is None or resolve_session_project() is None):
        _SESSION = None
    elif _SESSION is not None:
        try:
            reconcile_view_state(bpy.context)
            project = resolve_session_project(_SESSION)
            if project is not None:
                from .operators import arm_undo_fence

                arm_undo_fence(str(getattr(project, "uuid", "")))
        except (AttributeError, ImportError, ReferenceError, RuntimeError, ValueError):
            pass


def register_studio() -> None:
    global _HANDLERS_REGISTERED
    if _HANDLERS_REGISTERED:
        return
    for collection, handler in (
        (bpy.app.handlers.save_pre, _save_pre),
        (bpy.app.handlers.save_post, _save_post),
        (bpy.app.handlers.load_pre, _load_pre),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        if handler not in collection:
            collection.append(handler)
    _HANDLERS_REGISTERED = True


def unregister_studio() -> None:
    global _HANDLERS_REGISTERED, _SESSION, _PENDING, _SWITCH_PENDING
    _SWITCH_PENDING = None
    if bpy.app.timers.is_registered(_continue_switch):
        bpy.app.timers.unregister(_continue_switch)
    if bpy.app.timers.is_registered(_continue_enter):
        bpy.app.timers.unregister(_continue_enter)
    _cancel_stroke_watchdog()
    if _PENDING is not None:
        _rollback_pending()
    try:
        exit_studio(bpy.context)
    except (ReferenceError, RuntimeError, StudioError):
        _SESSION = None
    for collection, handler in (
        (bpy.app.handlers.save_pre, _save_pre),
        (bpy.app.handlers.save_post, _save_post),
        (bpy.app.handlers.load_pre, _load_pre),
        (bpy.app.handlers.undo_post, _undo_post),
        (bpy.app.handlers.redo_post, _undo_post),
    ):
        while handler in collection:
            collection.remove(handler)
    _HANDLERS_REGISTERED = False


CLASSES: tuple[type, ...] = ()


__all__ = [
    "CLASSES", "StudioBusyError", "StudioError", "StudioSession", "StudioUnavailableError",
    "WORKSPACE_PROJECT_TAG", "active_session", "configure_workspace", "current_session",
    "back_to_paint", "dismiss_first_stroke_hint", "ensure_studio_material_preview",
    "enter_aux_mask_edit", "leave_aux_mask_edit",
    "enter_studio", "exit_studio", "open_or_switch_studio",
    "find_studio_workspace", "is_studio_active", "leave_export_adjustment_review",
    "TIMELINE_SPACE_TYPE",
    "reconcile_view_state", "register_studio", "show_export_adjustment_review",
    "prepare_stroke_brush", "resolve_session_project", "restore_stroke_brush", "seek_preview",
    "select_paint_key", "set_projection_hint", "studio_areas", "tag_studio_redraw",
    "unregister_studio",
]

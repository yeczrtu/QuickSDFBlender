"""Transient Quick SDF Studio session and workspace management.

The persistent project model deliberately does not contain an ``author_active``
flag.  A Studio session belongs to one Blender window and is valid only for the
current process.  This keeps save/load and undo from resurrecting half-active
paint state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import bpy
from bpy.app.handlers import persistent


WORKSPACE_PROJECT_TAG = "quick_sdf_studio_project_uuid"
WORKSPACE_VERSION_TAG = "quick_sdf_studio_layout_version"
WORKSPACE_LAYOUT_VERSION = 1
WORKSPACE_BASENAME = "Quick SDF Studio"
TIMELINE_HEIGHT = 104


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
    view_mode: str = "EDIT"
    paint_key_uuid: str = ""
    paint_key_angle: float = 0.0
    seek_angle: float = 0.0


@dataclass(slots=True)
class _PendingEnter:
    session: StudioSession
    manage_preview: bool
    state: str = "JOIN"


_SESSION: StudioSession | None = None
_PENDING: _PendingEnter | None = None
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
    if session is not None and session.project_uuid == str(getattr(project, "uuid", "")):
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


def seek_preview(context: Any, project: Any, angle: float) -> float:
    """Scrub the 3D result without changing the edit key or paint canvas."""

    value = max(0.0, min(90.0, float(angle)))
    project.seek_angle = value
    sign = -1.0 if str(getattr(project, "authoring_side", "RIGHT")) == "LEFT" else 1.0
    project.review_angle = sign * value
    session = active_session(context)
    if session is not None and session.project_uuid == str(getattr(project, "uuid", "")):
        session.view_mode = "PREVIEW"
        session.seek_angle = value
    try:
        from .live_preview import update_seek_preview

        update_seek_preview(project, value)
    except (ImportError, ReferenceError, RuntimeError, ValueError):
        pass
    tag_studio_redraw()
    return value


def back_to_paint(context: Any, project: Any) -> bool:
    """Return Preview to the stored edit key before any mutating action."""

    session = active_session(context)
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


def studio_areas(window: Any) -> tuple[Any | None, Any | None, Any | None]:
    screen = getattr(window, "screen", None)
    areas = getattr(screen, "areas", ()) if screen else ()
    view = next((area for area in areas if area.type == "VIEW_3D"), None)
    image = next((area for area in areas if area.type == "IMAGE_EDITOR"), None)
    timeline = next((area for area in areas if area.type == "DOPESHEET_EDITOR"), None)
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
        and any(area.type == "DOPESHEET_EDITOR" for area in screen.areas)
        for screen in workspace.screens
    )


def find_studio_workspace(project_uuid: str) -> Any | None:
    for workspace in bpy.data.workspaces:
        if workspace.get(WORKSPACE_PROJECT_TAG) == project_uuid and _valid_studio_layout(workspace):
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
    bottom.type = "DOPESHEET_EDITOR"

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
    for name in ("show_region_header", "show_region_footer", "show_region_channels", "show_region_ui"):
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


def _restore_preview(project: Any | None) -> None:
    try:
        from .preview import restore_preview_materials

        restore_preview_materials(project)
    except (ImportError, ReferenceError, RuntimeError):
        pass


def _assign_preview(project: Any, image: Any | None) -> None:
    from .preview import assign_preview_material

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
    bottom.type = "DOPESHEET_EDITOR"
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
    for name in ("show_region_header", "show_region_footer", "show_region_channels", "show_region_ui"):
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
    if window is None:
        return
    failed_workspace = bpy.data.workspaces.get(session.workspace_name)
    previous = bpy.data.workspaces.get(session.previous_workspace_name)
    if previous is not None:
        window.workspace = previous
    scene = bpy.data.scenes.get(session.scene_name)
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
            _mode_set(context, window, view_area, "TEXTURE_PAINT")
            from .tools import activate_tools

            activate_tools(context, window=window)
            _frame_studio_content(context, window, view_area, image_area)
            brush = getattr(scene.tool_settings.image_paint, "brush", None)
            if brush is not None:
                value = 1.0 if int(getattr(project, "paint_value", 0)) else 0.0
                try:
                    brush.color = (value, value, value)
                except (AttributeError, TypeError):
                    pass
            if pending.manage_preview:
                _assign_preview(project, image)
            project.warning_message = ""
            _SESSION = session
            _PENDING = None
            tag_studio_redraw()
            return None
    except Exception as error:
        _rollback_pending(error)
        return None
    return 0.03


def enter_studio(
    context: Any,
    project: Any,
    *,
    sync_canvas_fn: Callable[[Any, Any], Any] | None = None,
    manage_preview: bool = True,
) -> StudioSession:
    """Atomically enter Studio and publish the singleton only after success."""

    global _SESSION, _PENDING
    if bpy.app.background or getattr(context, "window", None) is None:
        raise StudioUnavailableError("Quick SDF Studio requires an interactive Blender window")
    project_uuid = str(getattr(project, "uuid", ""))
    if not project_uuid:
        raise StudioError("The Quick SDF project has no UUID")
    if _SESSION is not None:
        if is_studio_active(context, project_uuid):
            return _SESSION
        raise StudioBusyError("Exit the current Quick SDF Studio before opening another project")
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

    global _SESSION, _PENDING
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
    _restore_preview(project)
    try:
        from .live_preview import release_project

        release_project(project)
    except (ImportError, ReferenceError, RuntimeError):
        pass
    if window is None:
        _SESSION = None
        return True
    scene = bpy.data.scenes.get(session.scene_name) or getattr(window, "scene", None)
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
            if area.type in {"VIEW_3D", "IMAGE_EDITOR", "DOPESHEET_EDITOR"}:
                area.tag_redraw()


@persistent
def _save_pre(_unused: Any) -> None:
    if _SESSION is None:
        return
    project = resolve_session_project()
    _restore_preview(project)
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
            from .runtime import active_angle, resolve_angle_image

            item = active_angle(project)
            image = resolve_angle_image(project, item) if item else None
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
    global _SESSION, _PENDING
    try:
        from .operators import shutdown_export_job

        shutdown_export_job(message="Export cancelled while loading a file")
    except (ImportError, RuntimeError):
        pass
    if _PENDING is not None:
        _rollback_pending()
    try:
        from .preview import restore_all_preview_materials

        restore_all_preview_materials()
    except (ImportError, ReferenceError, RuntimeError):
        pass
    _SESSION = None


@persistent
def _undo_post(_unused: Any) -> None:
    global _SESSION
    if _SESSION is not None and (find_window(_SESSION.window_pointer) is None or resolve_session_project() is None):
        _SESSION = None
    elif _SESSION is not None:
        try:
            reconcile_view_state(bpy.context)
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
    global _HANDLERS_REGISTERED, _SESSION, _PENDING
    if bpy.app.timers.is_registered(_continue_enter):
        bpy.app.timers.unregister(_continue_enter)
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
    "back_to_paint", "dismiss_first_stroke_hint", "enter_studio", "exit_studio",
    "find_studio_workspace", "is_studio_active", "reconcile_view_state", "register_studio",
    "resolve_session_project", "seek_preview", "select_paint_key", "studio_areas",
    "tag_studio_redraw", "unregister_studio",
]

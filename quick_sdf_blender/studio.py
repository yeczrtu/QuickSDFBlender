# SPDX-License-Identifier: GPL-3.0-or-later
"""Transient Quick SDF Studio session and workspace management.

The persistent project model deliberately does not contain an ``author_active``
flag.  A Studio session belongs to one Blender window and is valid only for the
current process.  This keeps save/load and undo from resurrecting half-active
paint state.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
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
PROVISIONAL_DISPLAY_ROLE = "provisional_display"


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
    provisional_uuid: str = ""
    provisional_angle: float = -1.0
    provisional_side: str = ""
    provisional_image_name: str = ""
    provisional_base_blob: bytes = b""
    provisional_coverage_blob: bytes = b""
    provisional_base_revision: int = 0
    provisional_coverage_revision: int = 0
    provisional_state: str = "NONE"
    provisional_source_token: tuple[Any, ...] = ()
    provisional_promoting: bool = False
    provisional_previous_key_uuid: str = ""
    provisional_rebuild_after_save: bool = False
    provisional_rebuild_angle: float = -1.0


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
_PROVISIONAL_JOB: dict[str, Any] | None = None


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

    session = active_session(context)
    if session is not None and session.provisional_uuid and not session.provisional_promoting:
        discard_provisional(context, project)

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
        session.projection_hint = ""

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
    discard_provisional(context, project)
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


def _provisional_lane(project: Any, side: str) -> list[Any]:
    return sorted(
        (item for item in project.angles if str(getattr(item, "side", "RIGHT")) == side),
        key=lambda item: float(item.angle),
    )


def _provisional_token(project: Any, side: str) -> tuple[Any, ...]:
    from . import runtime

    records = []
    for item in _provisional_lane(project, side):
        image = runtime.resolve_display_image(project, item)
        records.append(
            (
                str(item.uuid),
                float(item.angle),
                "" if image is None else str(image.name),
                -1
                if image is None
                else int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                runtime.bitplane_revision_token(item, "BASE"),
                runtime.bitplane_revision_token(item, "COVERAGE"),
            )
        )
    return tuple(records)


def _cancel_provisional_job() -> None:
    global _PROVISIONAL_JOB

    job = _PROVISIONAL_JOB
    _PROVISIONAL_JOB = None
    if job is None:
        return
    flag = job.get("cancel_flag")
    if flag is not None:
        flag.value = 1
    manager = job.get("manager")
    if manager is not None:
        try:
            manager.cancel()
            manager.shutdown(wait=False)
        except RuntimeError:
            pass
    if bpy.app.timers.is_registered(_poll_provisional_job):
        bpy.app.timers.unregister(_poll_provisional_job)


def _remove_provisional_image(session: StudioSession) -> None:
    image = bpy.data.images.get(str(session.provisional_image_name))
    if image is None:
        return
    try:
        from . import runtime

        if str(image.get(runtime.ROLE_KEY, "")) == PROVISIONAL_DISPLAY_ROLE:
            bpy.data.images.remove(image)
    except (ReferenceError, RuntimeError):
        pass


def discard_provisional(
    context: Any | None = None,
    project: Any | None = None,
    *,
    keep_preview_angle: bool = False,
) -> None:
    """Cancel and remove the session-only in-between paint key."""

    session = _SESSION
    if session is None:
        _cancel_provisional_job()
        return
    project = project or resolve_session_project(session)
    _cancel_provisional_job()
    if session.provisional_promoting and project is not None and session.provisional_uuid:
        index = next(
            (
                index
                for index, item in enumerate(project.angles)
                if str(item.uuid) == session.provisional_uuid
                and bool(item.get("_qsdf_provisional", False))
            ),
            -1,
        )
        if index >= 0:
            project.angles.remove(index)
    _remove_provisional_image(session)
    angle = session.provisional_angle
    session.provisional_uuid = ""
    session.provisional_angle = -1.0
    session.provisional_side = ""
    session.provisional_image_name = ""
    session.provisional_base_blob = b""
    session.provisional_coverage_blob = b""
    session.provisional_base_revision = 0
    session.provisional_coverage_revision = 0
    session.provisional_state = "NONE"
    session.provisional_source_token = ()
    session.provisional_promoting = False
    session.provisional_previous_key_uuid = ""
    if keep_preview_angle and angle >= 0.0:
        session.seek_angle = angle


def _compute_provisional_masks(
    lower_display: Any,
    upper_display: Any,
    lower_base: Any,
    upper_base: Any,
    lower_coverage: Any,
    upper_coverage: Any,
    factor: float,
    cancel_flag: Any,
) -> tuple[Any, Any, Any]:
    import numpy as np

    from .native import interpolate_binary_masks

    display = interpolate_binary_masks(
        lower_display, upper_display, factor, cancel_flag=cancel_flag
    )
    base = interpolate_binary_masks(
        lower_base, upper_base, factor, cancel_flag=cancel_flag
    )
    inherited_coverage = np.ascontiguousarray(
        lower_coverage | upper_coverage, dtype=np.bool_
    )
    return display, base, inherited_coverage


def _assign_provisional_canvas(
    context: Any,
    project: Any,
    session: StudioSession,
    image: Any,
) -> None:
    window = find_window(session.window_pointer)
    scene = bpy.data.scenes.get(session.scene_name) or getattr(window, "scene", None)
    if scene is None:
        raise StudioError("The Studio scene disappeared while preparing this angle")
    scene.tool_settings.image_paint.mode = "IMAGE"
    scene.tool_settings.image_paint.canvas = image
    if window is not None:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = image
            area.tag_redraw()
    try:
        _assign_preview(project, image)
    except (ReferenceError, RuntimeError, ValueError):
        pass
    tag_studio_redraw()


def _publish_provisional_result(job: dict[str, Any], result: tuple[Any, Any, Any]) -> None:
    import numpy as np

    from . import runtime
    from .bitplane import BitplaneRole, encode_bitplane

    session = _SESSION
    project = resolve_session_project(session)
    if (
        session is None
        or project is None
        or session.project_uuid != job["project_uuid"]
        or session.provisional_uuid != job["provisional_uuid"]
    ):
        return
    if (
        session.provisional_source_token != job["source_token"]
        or _provisional_token(project, job["side"]) != job["source_token"]
    ):
        discard_provisional(bpy.context, project, keep_preview_angle=True)
        return
    interpolated_display, base, inherited_coverage = result
    resolution = int(project.resolution)
    if interpolated_display.shape != (resolution, resolution):
        raise StudioError("The prepared angle has an unexpected resolution")
    # Boundary is a generated layer, never a hand-painted override. Evaluate it
    # at the provisional angle before deriving Coverage so Boundary-only pixels
    # cannot accidentally become permanent paint when the key is promoted.
    generated = base
    if getattr(project, "boundary_tracks", None):
        from .boundary import evaluate_boundary_mask

        generated = evaluate_boundary_mask(
            project,
            float(job["angle"]),
            str(job["side"]),
            base,
        )
    coverage = np.ascontiguousarray(inherited_coverage, dtype=np.bool_)
    display = np.ascontiguousarray(
        np.where(coverage, interpolated_display, generated), dtype=np.bool_
    )
    _remove_provisional_image(session)
    image = bpy.data.images.new(
        f"QSDF Pending {str(project.uuid)[:8]} {float(job['angle']):04.1f}",
        width=resolution,
        height=resolution,
        alpha=True,
        float_buffer=False,
    )
    runtime.tag_image(
        image,
        str(project.uuid),
        str(job["provisional_uuid"]),
        PROVISIONAL_DISPLAY_ROLE,
    )
    runtime.make_image_opaque(image)
    rgba = np.ones((resolution, resolution, 4), dtype=np.float32)
    rgba[..., :3] = np.asarray(display, dtype=np.float32)[..., None]
    runtime.write_image_rgba(image, rgba)
    session.provisional_image_name = image.name
    session.provisional_base_blob = encode_bitplane(
        np.ascontiguousarray(base, dtype=np.bool_), BitplaneRole.BASE
    )
    session.provisional_coverage_blob = encode_bitplane(
        np.ascontiguousarray(coverage, dtype=np.bool_), BitplaneRole.COVERAGE
    )
    session.provisional_base_revision = 1
    session.provisional_coverage_revision = 1
    session.provisional_state = "READY"
    session.view_mode = "PROVISIONAL"
    _assign_provisional_canvas(bpy.context, project, session, image)


def _poll_provisional_job() -> float | None:
    global _PROVISIONAL_JOB

    job = _PROVISIONAL_JOB
    if job is None:
        return None
    from .jobs import JobState

    manager = job["manager"]
    state = manager.poll()
    if state in {JobState.PENDING, JobState.RUNNING}:
        return 0.05
    _PROVISIONAL_JOB = None
    try:
        result = manager.take_result()
        _publish_provisional_result(job, result)
    except Exception as exc:
        session = _SESSION
        if session is not None and session.provisional_uuid == job["provisional_uuid"]:
            session.provisional_state = "FAILED"
            session.projection_hint = f"Could not prepare this angle: {exc}"
    finally:
        manager.shutdown(wait=False)
    return None


def settle_seek(context: Any, project: Any, angle: float) -> float:
    """Snap to an existing key or prepare a session-only key for painting."""

    from . import runtime
    from .jobs import GenerationJobManager
    from .model import AUTO_KEY_ANGLE_STEP, AUTO_KEY_SNAP_DEGREES, MAX_KEYS_PER_SIDE

    session = active_session(context)
    if session is None or session.project_uuid != str(project.uuid):
        return seek_preview(context, project, angle)
    clamped = max(0.0, min(90.0, float(angle)))
    value = float(int(clamped / AUTO_KEY_ANGLE_STEP + 0.5)) * AUTO_KEY_ANGLE_STEP
    if (
        session.provisional_uuid
        and not session.provisional_promoting
        and abs(float(session.provisional_angle) - value) <= 1.0e-6
        and session.provisional_state in {"PREPARING", "READY", "LIMIT"}
    ):
        project.seek_angle = value
        session.seek_angle = value
        tag_studio_redraw()
        return value
    mode = str(getattr(project, "symmetry_mode", "AUTO"))
    side = str(
        getattr(project, "active_side", "RIGHT")
        if mode == "INDEPENDENT"
        else getattr(project, "authoring_side", "RIGHT")
    )
    items = _provisional_lane(project, side)
    if not items:
        return seek_preview(context, project, value)
    nearest = min(items, key=lambda item: abs(float(item.angle) - value))
    if abs(float(nearest.angle) - value) <= AUTO_KEY_SNAP_DEGREES:
        discard_provisional(context, project)
        select_paint_key(context, project, key_uuid=str(nearest.uuid))
        return float(nearest.angle)

    seek_preview(context, project, value, show_in_image_editor=True)
    discard_provisional(context, project, keep_preview_angle=True)
    session.provisional_uuid = runtime.new_uuid()
    session.provisional_angle = value
    session.provisional_side = side
    session.provisional_previous_key_uuid = session.paint_key_uuid
    session.seek_angle = value
    session.view_mode = "PREVIEW"
    if len(items) >= MAX_KEYS_PER_SIDE:
        session.provisional_state = "LIMIT"
        session.projection_hint = (
            f"Maximum {MAX_KEYS_PER_SIDE} keys. Select or delete an existing key."
        )
        tag_studio_redraw()
        return value

    lower = max((item for item in items if float(item.angle) < value), key=lambda item: float(item.angle))
    upper = min((item for item in items if float(item.angle) > value), key=lambda item: float(item.angle))
    lower_angle = float(lower.angle)
    upper_angle = float(upper.angle)
    factor = (value - lower_angle) / (upper_angle - lower_angle)
    token = _provisional_token(project, side)
    session.provisional_source_token = token
    session.provisional_state = "PREPARING"
    session.projection_hint = f"Preparing {value:.0f} degrees..."
    manager = GenerationJobManager(thread_name_prefix="QuickSDFAutoKey")
    cancel_flag = ctypes.c_int(0)
    manager.submit(
        _compute_provisional_masks,
        runtime.image_mask(runtime.resolve_display_image(project, lower)),
        runtime.image_mask(runtime.resolve_display_image(project, upper)),
        runtime.base_mask(lower).copy(),
        runtime.base_mask(upper).copy(),
        runtime.coverage_mask(lower).copy(),
        runtime.coverage_mask(upper).copy(),
        factor,
        cancel_flag,
    )
    global _PROVISIONAL_JOB
    _PROVISIONAL_JOB = {
        "manager": manager,
        "cancel_flag": cancel_flag,
        "project_uuid": str(project.uuid),
        "provisional_uuid": session.provisional_uuid,
        "source_token": token,
        "side": side,
        "angle": value,
    }
    if not bpy.app.timers.is_registered(_poll_provisional_job):
        bpy.app.timers.register(_poll_provisional_job, first_interval=0.05)
    tag_studio_redraw()
    return value


def activate_provisional_for_stroke(context: Any, project: Any) -> Any | None:
    """Temporarily insert the ready key so the native Paint macro can use it."""

    from . import runtime

    session = active_session(context)
    if session is None or not session.provisional_uuid:
        return None
    if session.provisional_state == "PREPARING":
        raise StudioError("This angle is still being prepared")
    if session.provisional_state == "LIMIT":
        raise StudioError("Maximum 16 keys. Select or delete an existing key")
    if session.provisional_state != "READY":
        raise StudioError("This angle is not ready for painting")
    image = bpy.data.images.get(session.provisional_image_name)
    if image is None:
        raise StudioError("The prepared paint canvas is missing")
    provisional_uuid = str(session.provisional_uuid)
    provisional_angle = float(session.provisional_angle)
    provisional_side = str(session.provisional_side)
    item = project.angles.add()
    item.uuid = provisional_uuid
    item.angle = provisional_angle
    item.side = provisional_side
    item.display_image = image
    item.display_image_name = image.name
    for property_name, payload in (
        (runtime.BASE_BITPLANE_KEY, session.provisional_base_blob),
        (runtime.COVERAGE_BITPLANE_KEY, session.provisional_coverage_blob),
    ):
        if property_name in item:
            del item[property_name]
        item[property_name] = bytes(payload)
    item.base_revision = session.provisional_base_revision
    item.coverage_revision = session.provisional_coverage_revision
    item["_qsdf_provisional"] = True
    runtime.tag_image(image, str(project.uuid), provisional_uuid, runtime.DISPLAY_ROLE)
    desired = [
        str(candidate.uuid)
        for candidate in sorted(
            project.angles,
            key=lambda candidate: (
                0 if str(candidate.side) == "RIGHT" else 1,
                float(candidate.angle),
            ),
        )
    ]
    for target, candidate_uuid in enumerate(desired):
        current = next(
            index
            for index, candidate in enumerate(project.angles)
            if str(candidate.uuid) == candidate_uuid
        )
        if current != target:
            project.angles.move(current, target)
    # Collection.move() can rebind an existing PropertyGroup wrapper to a
    # different element. Resolve the promoted key again by its captured UUID.
    index = next(
        index
        for index, candidate in enumerate(project.angles)
        if str(candidate.uuid) == provisional_uuid
    )
    item = project.angles[index]
    project.active_angle_index = index
    project.active_angle_uuid = provisional_uuid
    project.active_side = provisional_side
    project.seek_angle = provisional_angle
    project.review_angle = _side_signed_angle(project, item)
    session.provisional_promoting = True
    session.view_mode = "PROVISIONAL_STROKE"
    runtime.sync_canvas(context, project)
    return project.angles[index]


def provisional_created_metadata(project: Any, item: Any) -> dict[str, Any] | None:
    session = _SESSION
    if (
        session is None
        or not session.provisional_promoting
        or str(getattr(item, "uuid", "")) != session.provisional_uuid
    ):
        return None
    from . import runtime

    return {
        "kind": "CREATE_KEY",
        "uuid": str(item.uuid),
        "angle": float(item.angle),
        "side": str(item.side),
        "display_image_name": str(item.display_image_name),
        "base_blob": runtime.bitplane_blob(item, "BASE"),
        "coverage_blob": runtime.bitplane_blob(item, "COVERAGE"),
        "base_revision": int(item.base_revision),
        "coverage_revision": int(item.coverage_revision),
    }


def finish_provisional_stroke(context: Any, project: Any, *, changed: bool) -> None:
    session = active_session(context)
    if session is None or not session.provisional_promoting:
        return
    uuid = session.provisional_uuid
    item = next((candidate for candidate in project.angles if str(candidate.uuid) == uuid), None)
    image = bpy.data.images.get(session.provisional_image_name)
    if changed and item is not None:
        if "_qsdf_provisional" in item:
            del item["_qsdf_provisional"]
        if image is not None:
            from . import runtime

            runtime.tag_image(image, str(project.uuid), uuid, runtime.DISPLAY_ROLE)
        session.provisional_uuid = ""
        session.provisional_image_name = ""
        session.provisional_base_blob = b""
        session.provisional_coverage_blob = b""
        session.provisional_state = "NONE"
        session.provisional_promoting = False
        session.view_mode = "EDIT"
        session.paint_key_uuid = uuid
        session.paint_key_angle = float(item.angle)
        return
    if item is not None:
        index = next(index for index, candidate in enumerate(project.angles) if str(candidate.uuid) == uuid)
        project.angles.remove(index)
    if image is not None:
        from . import runtime

        runtime.tag_image(image, str(project.uuid), uuid, PROVISIONAL_DISPLAY_ROLE)
    session.provisional_promoting = False
    session.provisional_state = "READY"
    session.view_mode = "PROVISIONAL"
    previous = next(
        (
            candidate
            for candidate in project.angles
            if str(candidate.uuid) == session.provisional_previous_key_uuid
        ),
        None,
    )
    if previous is not None:
        previous_index = next(
            index
            for index, candidate in enumerate(project.angles)
            if str(candidate.uuid) == str(previous.uuid)
        )
        project.active_angle_index = previous_index
        project.active_angle_uuid = str(previous.uuid)
        project.active_side = str(previous.side)
    if image is not None:
        _assign_provisional_canvas(context, project, session, image)


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
        and session.provisional_uuid
        and not session.provisional_promoting
        and abs(float(session.provisional_angle) - value) > 1.0e-6
    ):
        discard_provisional(context, project)
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
        and session.provisional_uuid
    ):
        if session.provisional_state == "READY" and not session.provisional_promoting:
            image = bpy.data.images.get(session.provisional_image_name)
            if image is not None:
                _assign_provisional_canvas(context, project, session, image)
                return True
        if session.provisional_state in {"PREPARING", "LIMIT", "FAILED"}:
            return False
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
        # If Blender cancelled the native modal stroke, the propagation macro
        # never gets a chance to decide whether an auto key changed. Roll the
        # temporary collection item back and keep its prepared canvas available.
        if session.provisional_promoting:
            try:
                finish_provisional_stroke(bpy.context, project, changed=False)
            except (AttributeError, ReferenceError, RuntimeError):
                discard_provisional(bpy.context, project)
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
    """Show a quiet 3D-only projection hint after a no-op native stroke."""

    session = active_session(context or bpy.context)
    if session is None:
        return
    session.projection_hint = (
        "No paint landed · move back or press Numpad 5"
        if no_change and session.stroke_from_view3d
        else ""
    )
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
                    "Paint only the areas you want to adjust. Paint between keys to add that angle."
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
    discard_provisional(context, old_project)
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
            "Paint only the areas you want to adjust. Paint between keys to add that angle."
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
    discard_provisional(context, project)
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
    # Provisional canvases and structural Redo images are process-only state.
    # Never serialize them into the .blend. A ready angle is reconstructed
    # after saving so the artist can continue without changing the playhead.
    if _SESSION.provisional_uuid:
        _SESSION.provisional_rebuild_after_save = bool(
            _SESSION.provisional_state in {"PREPARING", "READY"}
            and not _SESSION.provisional_promoting
        )
        _SESSION.provisional_rebuild_angle = float(_SESSION.provisional_angle)
        discard_provisional(bpy.context, project, keep_preview_angle=True)
    else:
        _SESSION.provisional_rebuild_after_save = False
        _SESSION.provisional_rebuild_angle = -1.0
    if project is not None:
        _release_project_history(project)
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
            if (
                _SESSION.provisional_rebuild_after_save
                and _SESSION.provisional_rebuild_angle >= 0.0
                and not _SESSION.editing_aux_mask_uuid
            ):
                angle = float(_SESSION.provisional_rebuild_angle)
                _SESSION.provisional_rebuild_after_save = False
                _SESSION.provisional_rebuild_angle = -1.0
                settle_seek(bpy.context, project, angle)
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
        discard_provisional(bpy.context, project)
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
    _cancel_provisional_job()
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
    "activate_provisional_for_stroke", "back_to_paint", "discard_provisional",
    "dismiss_first_stroke_hint", "ensure_studio_material_preview",
    "enter_aux_mask_edit", "leave_aux_mask_edit",
    "enter_studio", "exit_studio", "open_or_switch_studio",
    "find_studio_workspace", "is_studio_active", "leave_export_adjustment_review",
    "TIMELINE_SPACE_TYPE",
    "reconcile_view_state", "register_studio", "show_export_adjustment_review",
    "finish_provisional_stroke", "prepare_stroke_brush", "provisional_created_metadata",
    "resolve_session_project", "restore_stroke_brush", "seek_preview", "settle_seek",
    "select_paint_key", "set_projection_hint", "studio_areas", "tag_studio_redraw",
    "unregister_studio",
]

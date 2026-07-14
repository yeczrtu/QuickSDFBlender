# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive Quick SDF timeline hosted by a compact non-temporal area."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import bpy

from .studio import (
    TIMELINE_SPACE_TYPE,
    WORKSPACE_PROJECT_TAG,
    active_session,
    resolve_session_project,
)


@dataclass(frozen=True, slots=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclass(frozen=True, slots=True)
class TimelineGeometry:
    rail: Rect
    key_rects: tuple[tuple[int, Rect], ...]
    angle_min: float
    angle_max: float

    def angle_from_x(self, x: float) -> float:
        span = max(1.0, self.rail.x1 - self.rail.x0)
        factor = max(0.0, min(1.0, (x - self.rail.x0) / span))
        return self.angle_min + factor * (self.angle_max - self.angle_min)


_DRAW_HANDLE: Any = None
_COLOR_SHADER: Any = None
_IMAGE_SHADER: Any = None


def _visible_keys(project: Any) -> list[tuple[int, Any]]:
    keys = list(enumerate(getattr(project, "angles", ())))
    edit_side = str(getattr(project, "authoring_side", "RIGHT"))
    if keys and hasattr(keys[0][1], "side"):
        keys = [(index, item) for index, item in keys if str(item.side) == edit_side]
    else:
        mirror = bool(getattr(project, "mirror_enabled", False))
        if not hasattr(project, "mirror_enabled"):
            mirror = str(getattr(project, "symmetry_mode", "INDEPENDENT")) != "INDEPENDENT"
        if mirror:
            sign = 1.0 if edit_side == "RIGHT" else -1.0
            keys = [(index, item) for index, item in keys if float(item.angle) * sign >= -1.0e-4]
    return sorted(keys, key=lambda pair: float(getattr(pair[1], "angle", 0.0)))


def build_geometry(width: float, height: float, keys: Sequence[tuple[int, Any]]) -> TimelineGeometry:
    margin = 16.0
    rail_y = max(20.0, height - 24.0)
    content_x0 = margin
    content_x1 = max(margin + 1.0, width - margin)
    content_width = max(1.0, content_x1 - content_x0)
    angles = [float(getattr(item, "angle", 0.0)) for _index, item in keys] or [0.0, 90.0]
    angle_min, angle_max = min(angles), max(angles)
    if abs(angle_max - angle_min) < 1.0e-6:
        angle_max = angle_min + 90.0
    thumb_width = max(28.0, min(68.0, content_width / max(1, len(keys)) - 4.0))
    thumb_width = min(content_width, thumb_width)
    endpoint_inset = thumb_width * 0.5 if keys else 0.0
    rail_x0 = content_x0 + endpoint_inset
    rail_x1 = content_x1 - endpoint_inset
    if rail_x1 <= rail_x0:
        rail_x0 = (content_x0 + content_x1) * 0.5
        rail_x1 = rail_x0 + 1.0
    rail = Rect(rail_x0, rail_y - 5.0, rail_x1, rail_y + 5.0)
    available = max(1.0, rail.x1 - rail.x0)
    thumb_height = max(24.0, min(64.0, rail.y0 - 18.0))
    rects: list[tuple[int, Rect]] = []
    for collection_index, item in keys:
        factor = (float(item.angle) - angle_min) / (angle_max - angle_min)
        center = rail.x0 + factor * available
        rects.append((collection_index, Rect(center - thumb_width * 0.5, 8.0, center + thumb_width * 0.5, 8.0 + thumb_height)))
    return TimelineGeometry(rail, tuple(rects), angle_min, angle_max)


def _project_for_context(context: Any) -> Any | None:
    session = active_session(context)
    workspace = getattr(context, "workspace", None)
    if session is None or workspace is None or workspace.get(WORKSPACE_PROJECT_TAG) != session.project_uuid:
        return None
    return resolve_session_project(session)


def _image_for_key(project: Any, item: Any) -> Any | None:
    image = getattr(item, "display_image", None)
    if image is not None:
        return image
    image_name = str(getattr(item, "display_image_name", ""))
    if image_name:
        image = bpy.data.images.get(image_name)
        if image is not None:
            return image
    return None


def _shader() -> Any:
    global _COLOR_SHADER
    if _COLOR_SHADER is None:
        import gpu

        _COLOR_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _COLOR_SHADER


def _rect(rect: Rect, color: tuple[float, float, float, float]) -> None:
    from gpu_extras.batch import batch_for_shader

    shader = _shader()
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": ((rect.x0, rect.y0), (rect.x1, rect.y0), (rect.x1, rect.y1), (rect.x0, rect.y1))},
        indices=((0, 1, 2), (2, 3, 0)),
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _outline(rect: Rect, color: tuple[float, float, float, float], thickness: float = 2.0) -> None:
    _rect(Rect(rect.x0, rect.y0, rect.x1, rect.y0 + thickness), color)
    _rect(Rect(rect.x0, rect.y1 - thickness, rect.x1, rect.y1), color)
    _rect(Rect(rect.x0, rect.y0, rect.x0 + thickness, rect.y1), color)
    _rect(Rect(rect.x1 - thickness, rect.y0, rect.x1, rect.y1), color)


def _text(text: str, x: float, y: float, color=(0.9, 0.92, 0.96, 1.0), size: int = 11) -> None:
    import blf

    try:
        blf.size(0, size)
    except TypeError:
        blf.size(0, size, 72)
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _draw_thumbnail(
    image: Any,
    rect: Rect,
    uv_bbox: tuple[float, float, float, float],
) -> bool:
    try:
        global _IMAGE_SHADER
        import gpu
        from gpu_extras.batch import batch_for_shader

        texture = gpu.texture.from_image(image)
        if _IMAGE_SHADER is None:
            _IMAGE_SHADER = gpu.shader.from_builtin("IMAGE_SCENE_LINEAR_TO_REC709_SRGB")
        u0, v0, u1, v1 = uv_bbox
        positions = (
            (rect.x0 + 2.0, rect.y0 + 2.0),
            (rect.x1 - 2.0, rect.y0 + 2.0),
            (rect.x1 - 2.0, rect.y1 - 16.0),
            (rect.x0 + 2.0, rect.y1 - 16.0),
        )
        tex_coords = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
        batch = batch_for_shader(
            _IMAGE_SHADER,
            "TRIS",
            {"pos": positions, "texCoord": tex_coords},
            indices=((0, 1, 2), (2, 3, 0)),
        )
        _IMAGE_SHADER.uniform_sampler("image", texture)
        batch.draw(_IMAGE_SHADER)
        return True
    except (ReferenceError, RuntimeError, SystemError, ValueError):
        return False


def _affected_indices(project: Any, keys: Sequence[tuple[int, Any]]) -> set[int]:
    if not keys:
        return set()
    active = int(getattr(project, "active_angle_index", keys[0][0]))
    # Interactive strokes edit only the selected key so Blender remains as
    # responsive as its native Texture Paint. Export performs the non-
    # destructive cross-angle consistency repair.
    return {active}


def _seek_value(project: Any) -> float:
    return float(getattr(project, "seek_angle", getattr(project, "review_angle", 0.0)))


def _playhead_layout(
    geometry: TimelineGeometry,
    width: float,
    height: float,
    angle: float,
) -> tuple[float, Rect, str]:
    span = max(1.0e-6, geometry.angle_max - geometry.angle_min)
    factor = max(0.0, min(1.0, (angle - geometry.angle_min) / span))
    x = geometry.rail.x0 + factor * (geometry.rail.x1 - geometry.rail.x0)
    label = f"{angle:.1f}°"
    label_width = max(30.0, 8.0 + 7.0 * len(label))
    label_x0 = max(2.0, min(width - label_width - 2.0, x - label_width * 0.5))
    label_y0 = geometry.rail.y1 + 2.0
    label_rect = Rect(
        label_x0,
        label_y0,
        label_x0 + label_width,
        max(label_y0 + 1.0, min(height - 1.0, label_y0 + 16.0)),
    )
    return x, label_rect, label


def _nearest_key_index(
    keys: Sequence[tuple[int, Any]],
    angle: float,
) -> int | None:
    """Return the visible key nearest to ``angle``, preferring lower on ties."""

    if not keys:
        return None
    index, _item = min(
        keys,
        key=lambda pair: (
            abs(float(getattr(pair[1], "angle", 0.0)) - angle),
            float(getattr(pair[1], "angle", 0.0)),
        ),
    )
    return index


def _draw_timeline() -> None:
    context = bpy.context
    if getattr(context, "area", None) is None or context.area.type != TIMELINE_SPACE_TYPE:
        return
    project = _project_for_context(context)
    if project is None or context.region is None:
        return
    import gpu

    keys = _visible_keys(project)
    geometry = build_geometry(context.region.width, context.region.height, keys)
    active = int(getattr(project, "active_angle_index", -1))
    affected = _affected_indices(project, keys)
    session = active_session(context)
    previewing = bool(session is not None and session.view_mode == "PREVIEW")
    show_hint = bool(session is not None and session.show_first_stroke_hint)
    highlighted = (
        _nearest_key_index(keys, _seek_value(project)) if previewing else active
    )
    if previewing and highlighted is not None:
        affected = {highlighted}
    bbox_values = tuple(float(value) for value in getattr(project, "thumbnail_uv_bbox", (0.0, 0.0, 1.0, 1.0)))
    uv_bbox = bbox_values if (
        len(bbox_values) == 4
        and 0.0 <= bbox_values[0] < bbox_values[2] <= 1.0
        and 0.0 <= bbox_values[1] < bbox_values[3] <= 1.0
    ) else (0.0, 0.0, 1.0, 1.0)
    try:
        gpu.state.blend_set("ALPHA")
        _rect(Rect(0.0, 0.0, context.region.width, context.region.height), (0.025, 0.03, 0.04, 1.0))
        _rect(geometry.rail, (0.18, 0.20, 0.24, 1.0))
        seek_x, label_rect, label_text = _playhead_layout(
            geometry,
            context.region.width,
            context.region.height,
            _seek_value(project),
        )
        key_map = {index: item for index, item in keys}
        for collection_index, rect in geometry.key_rects:
            item = key_map[collection_index]
            _rect(rect, (0.10, 0.22, 0.34, 0.82) if collection_index in affected else (0.08, 0.09, 0.11, 1.0))
            image = _image_for_key(project, item)
            if image is None or not _draw_thumbnail(image, rect, uv_bbox):
                _rect(Rect(rect.x0 + 2, rect.y0 + 2, rect.x1 - 2, rect.y1 - 16), (0.18, 0.19, 0.21, 1.0))
            angle = float(getattr(item, "angle", 0.0))
            _text(f"{angle:.1f}°", rect.x0 + 4.0, rect.y0 + 3.0, size=10)
            if collection_index == highlighted:
                _outline(rect, (0.18, 0.62, 1.0, 1.0), 2.0)
            if bool(getattr(item, "has_violation", False)):
                _rect(Rect(rect.x1 - 9, rect.y1 - 9, rect.x1 - 2, rect.y1 - 2), (1.0, 0.08, 0.03, 1.0))
            elif bool(getattr(item, "is_manual", False)):
                _rect(Rect(rect.x1 - 7, rect.y1 - 7, rect.x1 - 3, rect.y1 - 3), (0.95, 0.95, 1.0, 1.0))
            else:
                _rect(Rect(rect.x1 - 6, rect.y1 - 6, rect.x1 - 4, rect.y1 - 4), (0.70, 0.74, 0.80, 1.0))
        _rect(
            Rect(seek_x - 1.0, 4.0, seek_x + 1.0, geometry.rail.y1 + 5.0),
            (0.25, 0.72, 1.0, 1.0),
        )
        from .i18n import tr

        semantic_y = max(2.0, geometry.rail.y0 - 12.0)
        rail_mid = (geometry.rail.x0 + geometry.rail.x1) * 0.5
        semantic_color = (0.58, 0.62, 0.68, 1.0)
        _text(
            tr("Light Starts"), geometry.rail.x0, semantic_y,
            color=semantic_color, size=9,
        )
        _text(
            tr("Side"), rail_mid - 12.0, semantic_y,
            color=semantic_color, size=9,
        )
        _text(
            tr("Full Light"), max(2.0, geometry.rail.x1 - 48.0), semantic_y,
            color=semantic_color, size=9,
        )
        if not show_hint:
            _rect(label_rect, (0.16, 0.48, 0.78, 1.0))
            _text(label_text, label_rect.x0 + 4.0, label_rect.y0 + 2.0, size=10)
        if show_hint and session is not None:
            message = session.first_hint_text or "Choose an angle · choose Light or Shadow · paint"
            _text(tr(message), geometry.rail.x0, geometry.rail.y1 + 5.0, size=11)
    except (AttributeError, ReferenceError, RuntimeError, SystemError, ValueError):
        return
    finally:
        try:
            gpu.state.blend_set("NONE")
        except (AttributeError, RuntimeError):
            pass


def _operator_exists(idname: str) -> bool:
    namespace, name = idname.split(".", 1)
    return hasattr(getattr(bpy.ops, namespace, object()), name)


def _select_key(context: Any, project: Any, index: int) -> None:
    item = project.angles[index]
    if _operator_exists("quicksdf.key_select"):
        operator = bpy.ops.quicksdf.key_select
        props = operator.get_rna_type().properties.keys()
        kwargs: dict[str, Any] = {}
        if "index" in props:
            kwargs["index"] = index
        if "uuid" in props:
            kwargs["uuid"] = str(getattr(item, "uuid", ""))
        elif "key_uuid" in props:
            kwargs["key_uuid"] = str(getattr(item, "uuid", ""))
        try:
            if "FINISHED" in operator("EXEC_DEFAULT", **kwargs):
                return
        except (RuntimeError, TypeError):
            pass
    project.active_angle_index = index
    if hasattr(project, "review_angle"):
        project.review_angle = float(item.angle)
    if hasattr(project, "seek_angle"):
        project.seek_angle = float(item.angle)
    try:
        from .runtime import sync_canvas

        sync_canvas(context, project)
    except (ImportError, ReferenceError, RuntimeError):
        pass
    tag_timeline_redraw()


def _set_seek(context: Any, project: Any, value: float) -> None:
    try:
        from .studio import seek_preview

        seek_preview(context, project, value, show_in_image_editor=True)
        return
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        pass
    if _operator_exists("quicksdf.seek_set"):
        operator = bpy.ops.quicksdf.seek_set
        props = operator.get_rna_type().properties.keys()
        kwargs = {name: value for name in ("angle", "value") if name in props}
        try:
            if "FINISHED" in operator("EXEC_DEFAULT", **kwargs):
                return
        except (RuntimeError, TypeError):
            pass
    if hasattr(project, "seek_angle"):
        project.seek_angle = value
    elif hasattr(project, "review_angle"):
        project.review_angle = value
    tag_timeline_redraw()


class QSDF_GT_timeline_capture(bpy.types.Gizmo):
    bl_idname = "QSDF_GT_timeline_capture"
    __slots__ = ("_shape", "_initial_seek", "_initial_key_uuid", "_dragging")

    def setup(self):
        self._shape = self.new_custom_shape("TRIS", ((0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)))
        self._initial_seek = 0.0
        self._initial_key_uuid = ""
        self._dragging = False

    def draw(self, _context):
        # The visible timeline is rendered by the shared draw handler.
        pass

    def test_select(self, context, location):
        x, y = location
        return 0 if 0.0 <= x <= context.region.width and 0.0 <= y <= context.region.height else -1

    def invoke(self, context, event):
        project = _project_for_context(context)
        if project is None:
            return {"CANCELLED"}
        keys = _visible_keys(project)
        geometry = build_geometry(context.region.width, context.region.height, keys)
        x, y = float(event.mouse_region_x), float(event.mouse_region_y)
        for index, rect in geometry.key_rects:
            if rect.contains(x, y):
                _select_key(context, project, index)
                return {"FINISHED"}
        _playhead_x, label_rect, _label = _playhead_layout(
            geometry,
            context.region.width,
            context.region.height,
            _seek_value(project),
        )
        scrub_lane = Rect(
            geometry.rail.x0,
            0.0,
            geometry.rail.x1,
            float(context.region.height),
        )
        if scrub_lane.contains(x, y) or label_rect.contains(x, y):
            self._initial_seek = _seek_value(project)
            active = int(getattr(project, "active_angle_index", -1))
            self._initial_key_uuid = str(
                getattr(project.angles[active], "uuid", "")
                if 0 <= active < len(project.angles)
                else ""
            )
            self._dragging = True
            value = geometry.angle_from_x(x)
            _set_seek(context, project, value)
            return {"RUNNING_MODAL"}
        return {"FINISHED"}

    def _restore_initial_key(self, context: Any, project: Any) -> None:
        if self._initial_key_uuid:
            for index, item in enumerate(project.angles):
                if str(getattr(item, "uuid", "")) == self._initial_key_uuid:
                    _select_key(context, project, index)
                    return
        index = _nearest_key_index(_visible_keys(project), self._initial_seek)
        if index is not None:
            _select_key(context, project, index)
        else:
            _set_seek(context, project, self._initial_seek)

    def modal(self, context, event, _tweak):
        project = _project_for_context(context)
        if project is None:
            return {"CANCELLED"}
        if event.type in {"ESC", "WINDOW_DEACTIVATE"} or (
            event.type == "RIGHTMOUSE" and event.value == "PRESS"
        ):
            self._restore_initial_key(context, project)
            self._dragging = False
            return {"CANCELLED"}
        if event.type == "MOUSEMOVE":
            geometry = build_geometry(context.region.width, context.region.height, _visible_keys(project))
            _set_seek(context, project, geometry.angle_from_x(float(event.mouse_region_x)))
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            keys = _visible_keys(project)
            geometry = build_geometry(context.region.width, context.region.height, keys)
            angle = geometry.angle_from_x(float(event.mouse_region_x))
            index = _nearest_key_index(keys, angle)
            if index is not None:
                _select_key(context, project, index)
            self._dragging = False
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def exit(self, context, cancel):
        if not self._dragging:
            return
        project = _project_for_context(context)
        if project is None:
            self._dragging = False
            return
        if cancel:
            self._restore_initial_key(context, project)
        else:
            index = _nearest_key_index(_visible_keys(project), _seek_value(project))
            if index is not None:
                _select_key(context, project, index)
        self._dragging = False


class QSDF_GGT_timeline(bpy.types.GizmoGroup):
    bl_idname = "QSDF_GGT_timeline"
    bl_label = "Quick SDF Timeline"
    bl_space_type = TIMELINE_SPACE_TYPE
    bl_region_type = "WINDOW"
    bl_options = {"PERSISTENT"}

    @classmethod
    def poll(cls, context):
        return _project_for_context(context) is not None

    def setup(self, _context):
        gizmo = self.gizmos.new(QSDF_GT_timeline_capture.bl_idname)
        gizmo.use_draw_modal = True
        gizmo.use_tooltip = False
        self.capture = gizmo


CLASSES = (QSDF_GT_timeline_capture, QSDF_GGT_timeline)


def tag_timeline_redraw() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in window.screen.areas:
            if area.type == TIMELINE_SPACE_TYPE:
                area.tag_redraw()


def register_timeline() -> None:
    global _DRAW_HANDLE
    if _DRAW_HANDLE is None:
        _DRAW_HANDLE = bpy.types.SpaceNodeEditor.draw_handler_add(
            _draw_timeline, (), "WINDOW", "POST_PIXEL"
        )


def unregister_timeline() -> None:
    global _DRAW_HANDLE, _COLOR_SHADER, _IMAGE_SHADER
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceNodeEditor.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        except (ReferenceError, ValueError):
            pass
        _DRAW_HANDLE = None
    _COLOR_SHADER = None
    _IMAGE_SHADER = None


__all__ = [
    "CLASSES", "QSDF_GGT_timeline", "QSDF_GT_timeline_capture", "Rect", "TimelineGeometry",
    "build_geometry", "register_timeline", "tag_timeline_redraw", "unregister_timeline",
]

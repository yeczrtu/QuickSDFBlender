"""Interactive-window smoke for the Quick SDF Studio workspace transaction."""

from __future__ import annotations

from pathlib import Path
import hashlib
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402


def _source_fingerprints(project, runtime):
    records = []
    for item in project.angles:
        for image in (
            runtime.resolve_display_image(project, item),
            runtime.resolve_base_image(project, item),
            runtime.resolve_coverage_image(project, item),
        ):
            assert image is not None
            records.append(
                (
                    image.name,
                    int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                    hashlib.sha256(runtime.image_rgba(image).tobytes()).hexdigest(),
                )
            )
    return tuple(records)


def _mesh() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    material = bpy.data.materials.new("Studio Smoke Material")
    obj.data.materials.append(material)
    uv = obj.data.uv_layers.new(name="StudioSmokeUV")
    square = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for polygon in obj.data.polygons:
        for corner, loop in enumerate(polygon.loop_indices):
            uv.data[loop].uv = square[corner % 4]
    obj.data.uv_layers.active = uv
    return obj


STATE = {}


def _timeline_draw_probe(timeline, timeline_area, timeline_region, project):
    """Capture the semantic timeline primitives without depending on pixels."""

    rectangles = []
    labels = []
    originals = {
        "_rect": timeline._rect,
        "_outline": timeline._outline,
        "_draw_thumbnail": timeline._draw_thumbnail,
        "_text": timeline._text,
    }
    timeline._rect = lambda rect, color: rectangles.append((rect, color))
    timeline._outline = lambda *_args, **_kwargs: None
    timeline._draw_thumbnail = lambda *_args, **_kwargs: True
    timeline._text = lambda text, *_args, **_kwargs: labels.append(str(text))
    try:
        with bpy.context.temp_override(
            window=bpy.context.window,
            screen=bpy.context.window.screen,
            area=timeline_area,
            region=timeline_region,
        ):
            timeline._draw_timeline()
    finally:
        for name, value in originals.items():
            setattr(timeline, name, value)

    geometry = timeline.build_geometry(
        timeline_region.width,
        timeline_region.height,
        timeline._visible_keys(project),
    )
    playheads = [
        rect
        for rect, _color in rectangles
        if rect.x1 - rect.x0 <= 6.0
        and rect.y0 <= geometry.rail.y0
        and rect.y1 >= geometry.rail.y1
    ]
    return playheads, labels


def finish(error: BaseException | None = None) -> None:
    result_path = ROOT / "build" / "studio_smoke_result.txt"
    if error is not None:
        result_path.write_text("".join(traceback.format_exception(error)), encoding="utf-8")
    try:
        bpy.ops.preferences.addon_disable(module="quick_sdf_blender")
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def check() -> float | None:
    try:
        from quick_sdf_blender import runtime, studio

        if STATE.get("phase") == "STALE_EXPORTING":
            project = STATE["project"]
            STATE["stale_attempts"] = STATE.get("stale_attempts", 0) + 1
            if bool(project.job_running):
                if STATE["stale_attempts"] > 300:
                    raise AssertionError(project.job_message or project.diagnostic_message)
                return 0.05
            assert project.export_failed
            assert project.dirty
            assert "project changed" in project.job_message
            assert not STATE["stale_path"].exists()
            export_path = ROOT / "build" / "studio_async_export.png"
            export_path.unlink(missing_ok=True)
            assert bpy.ops.quicksdf.export_texture(
                filepath=str(export_path), overwrite=True
            ) == {"FINISHED"}
            assert project.job_running
            STATE["export_path"] = export_path
            STATE["phase"] = "EXPORTING"
            return 0.1
        if STATE.get("phase") == "EXPORTING":
            project = STATE["project"]
            export_path = STATE["export_path"]
            STATE["export_attempts"] = STATE.get("export_attempts", 0) + 1
            if bool(project.job_running) or str(project.output_path) != str(export_path):
                if STATE["export_attempts"] > 300:
                    raise AssertionError(project.job_message or project.diagnostic_message)
                return 0.1
            assert export_path.is_file()
            assert project.job_message == "Adjusted angle continuity and exported"
            assert project.export_adjustment_pixel_count > 0
            assert project.export_adjustment_image is not None
            assert _source_fingerprints(project, runtime) == STATE["source_fingerprints"]
            assert studio.current_session().view_mode == "PREVIEW"
            assert str(runtime.active_angle(project).uuid) == STATE["edit_uuid"]
            assert bpy.context.scene.tool_settings.image_paint.canvas == STATE["canvas"]
            image_area = next(
                area for area in bpy.context.window.screen.areas if area.type == "IMAGE_EDITOR"
            )
            assert bpy.ops.quicksdf.review_export_adjustments() == {"FINISHED"}
            assert image_area.spaces.active.image == project.export_adjustment_image
            assert image_area.spaces.active.ui_mode == "VIEW"
            assert studio.current_session().export_review_active
            image_region = next(
                region for region in image_area.regions if region.type == "WINDOW"
            )
            with bpy.context.temp_override(
                window=bpy.context.window,
                screen=bpy.context.window.screen,
                area=image_area,
                region=image_region,
            ):
                assert not bpy.ops.quicksdf.range_paint.poll()
            # A key selection is the explicit exit from read-only review.
            assert bpy.ops.quicksdf.key_select(index=int(project.active_angle_index)) == {"FINISHED"}
            assert image_area.spaces.active.ui_mode == "PAINT"
            assert image_area.spaces.active.image == STATE["canvas"]
            assert not studio.current_session().export_review_active
            assert bpy.ops.quicksdf.seek_set(angle=22.5) == {"FINISHED"}
            assert studio.current_session().view_mode == "PREVIEW"
            assert bpy.ops.quicksdf.review_export_adjustments() == {"FINISHED"}
            assert image_area.spaces.active.ui_mode == "VIEW"
            adjusted_save = ROOT / "build" / "studio_adjusted_save.blend"
            adjusted_save.unlink(missing_ok=True)
            assert bpy.ops.wm.save_as_mainfile(filepath=str(adjusted_save)) == {"FINISHED"}
            assert project.export_adjustment_image is None
            assert project.export_adjustment_pixel_count == 0
            assert not any(
                image.get(runtime.ROLE_KEY) == runtime.EXPORT_ADJUSTMENT_ROLE
                for image in bpy.data.images
            )
            assert image_area.spaces.active.image != project.export_adjustment_image
            assert image_area.spaces.active.ui_mode == "PAINT"
            assert not studio.current_session().export_review_active
            assert studio.current_session().view_mode == "PREVIEW"
            assert bpy.context.scene.tool_settings.image_paint.canvas == STATE["canvas"]
            assert bpy.ops.quicksdf.studio_exit() == {"FINISHED"}
            STATE["phase"] = "EXITED"
            return 0.1
        if STATE.get("phase") == "EXITED":
            obj = STATE["obj"]
            from quick_sdf_blender.live_preview import ONION_PREVIEW_ROLE, SEEK_PREVIEW_ROLE
            from quick_sdf_blender import operators

            assert studio.current_session() is None
            assert bpy.context.window.workspace == STATE["original_workspace"], (
                bpy.context.window.workspace.name,
                STATE["original_workspace"].name,
                [workspace.name for workspace in bpy.data.workspaces],
            )
            assert obj.material_slots[0].material == STATE["original_material"]
            assert obj.mode == "OBJECT"
            assert bpy.context.scene.tool_settings.image_paint.use_normal_falloff is True
            for screen in bpy.data.screens:
                for area in screen.areas:
                    if area.type != "VIEW_3D":
                        continue
                    space = area.spaces.active
                    expected = STATE["studio_clip_starts"].get(int(space.as_pointer()))
                    if expected is not None:
                        assert float(space.clip_start) == expected
            assert not STATE["project"].job_running
            assert not bpy.app.timers.is_registered(operators._poll_export_job)
            temporary_images = [
                image for image in bpy.data.images
                if image.get(runtime.ROLE_KEY) in {SEEK_PREVIEW_ROLE, ONION_PREVIEW_ROLE}
            ]
            assert temporary_images
            assert all(
                not bpy.data.user_map(subset={image}).get(image, set())
                for image in temporary_images
            )
            (ROOT / "build" / "studio_smoke_result.txt").write_text("PASS", encoding="utf-8")
            finish()
            return None
        if studio.current_session() is None:
            STATE["attempts"] = STATE.get("attempts", 0) + 1
            project = STATE["project"]
            if STATE["attempts"] < 80 and str(project.warning_message).startswith("Opening"):
                return 0.05
            raise AssertionError(project.diagnostic_message or "Studio did not finish opening")
        if not STATE.get("settled"):
            STATE["settled"] = True
            # Material Preview may compile its first EEVEE shader after the
            # session becomes active. Verify and capture only after it settles.
            return 2.0
        obj = STATE["obj"]
        project = STATE["project"]
        assert studio.is_studio_active(bpy.context, str(project.uuid))
        assert len(bpy.context.window.screen.areas) == 3
        assert {area.type for area in bpy.context.window.screen.areas} == {
            "VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR"
        }
        timeline_area = next(
            area for area in bpy.context.window.screen.areas
            if area.type == "NODE_EDITOR"
        )
        assert timeline_area.spaces.active.show_gizmo
        if not STATE.get("timeline_seek_started"):
            from quick_sdf_blender import timeline

            timeline_region = next(
                region for region in timeline_area.regions if region.type == "WINDOW"
            )
            keys = timeline._visible_keys(project)
            geometry = timeline.build_geometry(
                timeline_region.width, timeline_region.height, keys
            )
            # Pick a non-key angle with an unambiguous nearest key. Pressing
            # starts a continuous preview; releasing must snap to one of the
            # eight evenly spaced authoring stages.
            target_angle = 21.0
            factor = (
                (target_angle - geometry.angle_min)
                / (geometry.angle_max - geometry.angle_min)
            )
            local_x = geometry.rail.x0 + factor * (
                geometry.rail.x1 - geometry.rail.x0
            )
            local_y = (geometry.rail.y0 + geometry.rail.y1) * 0.5
            event_x = int(timeline_region.x + local_x)
            event_y = int(timeline_region.y + local_y)
            start_factor = (
                (float(runtime.active_angle(project).angle) - geometry.angle_min)
                / (geometry.angle_max - geometry.angle_min)
            )
            start_local_x = geometry.rail.x0 + start_factor * (
                geometry.rail.x1 - geometry.rail.x0
            )
            start_event_x = int(timeline_region.x + start_local_x)
            STATE.update(
                timeline_seek_started=True,
                timeline_seek_target=target_angle,
                timeline_paint_uuid=str(runtime.active_angle(project).uuid),
                timeline_canvas=bpy.context.scene.tool_settings.image_paint.canvas,
                timeline_key_uuids=tuple(str(item.uuid) for item in project.angles),
                timeline_event_xy=(event_x, event_y),
            )

            # The interpolated position has one Blender-like playhead. The
            # selected key and Canvas remain untouched until mouse release.
            assert bpy.ops.quicksdf.seek_set(angle=target_angle) == {"FINISHED"}
            assert studio.current_session().view_mode == "PREVIEW"
            assert str(runtime.active_angle(project).uuid) == STATE["timeline_paint_uuid"]
            assert bpy.context.scene.tool_settings.image_paint.canvas == STATE["timeline_canvas"]
            playheads, labels = _timeline_draw_probe(
                timeline, timeline_area, timeline_region, project
            )
            assert len(playheads) == 1, playheads
            assert not any("Back to Paint" in label for label in labels), labels
            assert bpy.context.scene.frame_current == 96
            assert tuple(str(item.uuid) for item in project.angles) == STATE["timeline_key_uuids"]
            assert bpy.ops.quicksdf.back_to_paint() == {"FINISHED"}

            seek_trace = []
            seek_editor_roles = []
            select_trace = []
            original_set_seek = timeline._set_seek
            original_select_key = timeline._select_key

            def tracked_set_seek(context, selected_project, value):
                seek_trace.append(float(value))
                result = original_set_seek(context, selected_project, value)
                image_area = next(
                    area for area in context.window.screen.areas
                    if area.type == "IMAGE_EDITOR"
                )
                preview_image = image_area.spaces.active.image
                seek_editor_roles.append(
                    str(preview_image.get(runtime.ROLE_KEY, ""))
                    if preview_image is not None
                    else ""
                )
                return result

            def tracked_select_key(context, selected_project, index):
                select_trace.append(int(index))
                return original_select_key(context, selected_project, index)

            timeline._set_seek = tracked_set_seek
            timeline._select_key = tracked_select_key
            STATE.update(
                timeline_seek_trace=seek_trace,
                timeline_seek_editor_roles=seek_editor_roles,
                timeline_select_trace=select_trace,
                timeline_original_set_seek=original_set_seek,
                timeline_original_select_key=original_select_key,
            )
            window = bpy.context.window
            window.event_simulate(
                type="MOUSEMOVE", value="NOTHING", x=start_event_x, y=event_y
            )
            window.event_simulate(
                type="LEFTMOUSE", value="PRESS", x=start_event_x, y=event_y
            )
            window.event_simulate(
                type="MOUSEMOVE", value="NOTHING", x=event_x, y=event_y
            )
            window.event_simulate(
                type="LEFTMOUSE", value="RELEASE", x=event_x, y=event_y
            )
            return 0.1
        if not STATE.get("timeline_release_verified"):
            from quick_sdf_blender import timeline

            timeline._set_seek = STATE.pop("timeline_original_set_seek")
            timeline._select_key = STATE.pop("timeline_original_select_key")
            snapped = min(
                timeline._visible_keys(project),
                key=lambda pair: abs(float(pair[1].angle) - STATE["timeline_seek_target"]),
            )
            snapped_index, snapped_item = snapped
            STATE["timeline_snapped_angle"] = float(snapped_item.angle)
            assert any(
                abs(value - STATE["timeline_seek_target"]) < 0.2
                for value in STATE["timeline_seek_trace"]
            ), STATE["timeline_seek_trace"]
            assert "seek_preview" in STATE["timeline_seek_editor_roles"], (
                STATE["timeline_seek_editor_roles"]
            )
            assert STATE["timeline_select_trace"][-1:] == [snapped_index], (
                STATE["timeline_select_trace"],
                STATE["timeline_seek_trace"],
                float(project.seek_angle),
                float(runtime.active_angle(project).angle),
            )
            assert studio.current_session().view_mode == "EDIT", (
                studio.current_session().view_mode,
                float(project.seek_angle),
                float(runtime.active_angle(project).angle),
            )
            assert int(project.active_angle_index) == snapped_index
            assert str(runtime.active_angle(project).uuid) == str(snapped_item.uuid)
            assert float(project.seek_angle) == float(snapped_item.angle)
            snapped_canvas = runtime.resolve_display_image(project, snapped_item)
            assert bpy.context.scene.tool_settings.image_paint.canvas == snapped_canvas
            image_area = next(
                area for area in bpy.context.window.screen.areas
                if area.type == "IMAGE_EDITOR"
            )
            assert image_area.spaces.active.image == snapped_canvas
            assert tuple(str(item.uuid) for item in project.angles) == STATE["timeline_key_uuids"]
            assert bpy.context.scene.frame_current == 96

            # A stroke snapshot after release is already synchronized. It must
            # not jump back to the old paint key or swap the visible material.
            preview_material = obj.material_slots[0].material
            material_image = preview_material.node_tree.nodes["QSDF Mask"].image
            assert material_image == snapped_canvas
            assert bpy.ops.quicksdf.paint_snapshot() == {"FINISHED"}
            assert studio.current_session().view_mode == "EDIT"
            assert float(project.seek_angle) == float(snapped_item.angle)
            assert str(runtime.active_angle(project).uuid) == str(snapped_item.uuid)
            assert bpy.context.scene.tool_settings.image_paint.canvas == snapped_canvas
            assert preview_material.node_tree.nodes["QSDF Mask"].image == material_image
            assert tuple(str(item.uuid) for item in project.angles) == STATE["timeline_key_uuids"]
            runtime.discard_paint_snapshot(project)
            runtime.discard_interactive_paint_snapshot(project)
            studio.restore_stroke_brush(bpy.context)
            STATE["timeline_release_verified"] = True
        assert obj.mode == "TEXTURE_PAINT"
        assert abs(
            float(runtime.active_angle(project).angle)
            - float(STATE["timeline_snapped_angle"])
        ) < 1.0e-5
        assert str(project.base_source) == "NORMAL_GUIDE"
        assert studio.current_session().first_hint_text.startswith(
            "A light-sweep guide from rear oblique"
        )
        assert bpy.context.scene.tool_settings.image_paint.use_normal_falloff is False
        session = studio.current_session()
        assert session is not None
        STATE["studio_clip_starts"] = dict(session.previous_clip_starts)
        view_area = next(area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D")
        view_space = view_area.spaces.active
        assert view_space.shading.type == "MATERIAL"
        assert float(view_space.clip_start) <= max(float(value) for value in obj.dimensions) * 1.0e-4 + 1.0e-8
        from quick_sdf_blender.tools import QSDF_WST_image_paint, QSDF_WST_view_paint

        assert "USE_BRUSHES" in QSDF_WST_view_paint.bl_options
        assert "USE_BRUSHES" in QSDF_WST_image_paint.bl_options
        brush = bpy.context.scene.tool_settings.image_paint.brush
        if brush is not None:
            original_brush = (
                tuple(float(value) for value in brush.color[:3]),
                tuple(float(value) for value in brush.secondary_color[:3]),
            )
            unified = bpy.context.scene.tool_settings.image_paint.unified_paint_settings
            original_unified = (
                bool(unified.use_unified_color),
                tuple(float(value) for value in unified.color[:3]),
                tuple(float(value) for value in unified.secondary_color[:3]),
            )
            project.paint_value = 1
            studio.prepare_stroke_brush(bpy.context, project)
            assert (
                tuple(float(value) for value in brush.color[:3]),
                tuple(float(value) for value in brush.secondary_color[:3]),
            ) == original_brush
            assert bool(unified.use_unified_color)
            assert tuple(float(value) for value in unified.color[:3]) == (1.0, 1.0, 1.0)
            assert tuple(float(value) for value in unified.secondary_color[:3]) == (0.0, 0.0, 0.0)
            studio.restore_stroke_brush(bpy.context)
            assert (
                bool(unified.use_unified_color),
                tuple(float(value) for value in unified.color[:3]),
                tuple(float(value) for value in unified.secondary_color[:3]),
            ) == original_unified
            assert (
                tuple(float(value) for value in brush.color[:3]),
                tuple(float(value) for value in brush.secondary_color[:3]),
            ) == original_brush
        project.paint_value = 0
        session.stroke_from_view3d = True
        studio.set_projection_hint(bpy.context, no_change=True)
        assert not session.projection_hint
        project.paint_value = 1
        studio.set_projection_hint(bpy.context, no_change=True)
        assert not session.projection_hint
        studio.set_projection_hint(bpy.context, no_change=False)
        assert not session.projection_hint
        project.paint_value = 0
        assert not project.base_needs_update
        canvas = runtime.resolve_display_image(
            project, runtime.active_angle(project)
        )
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        # Selecting an edit key is the public synchronization point before a
        # stroke. Recover Material Preview if Blender or the user left Solid,
        # without changing the Canvas or Texture Paint mode.
        view_space.shading.type = "SOLID"
        assert bpy.ops.quicksdf.key_select(index=int(project.active_angle_index)) == {"FINISHED"}
        assert view_space.shading.type == "MATERIAL"
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        assert obj.mode == "TEXTURE_PAINT"
        project.onion_enabled = True
        from quick_sdf_blender.live_preview import ONION_PREVIEW_ROLE

        image_area = next(
            area for area in bpy.context.window.screen.areas if area.type == "IMAGE_EDITOR"
        )
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        assert image_area.spaces.active.image.get(runtime.ROLE_KEY) == ONION_PREVIEW_ROLE
        assert bpy.ops.quicksdf.paint_snapshot() == {"FINISHED"}
        assert not project.onion_enabled
        assert image_area.spaces.active.image == canvas
        runtime.discard_paint_snapshot(project)
        assert obj.material_slots[0].material != STATE["original_material"]
        bpy.ops.screen.screenshot(
            filepath=str(ROOT / "build" / "quick_sdf_studio.png"),
            check_existing=False,
        )
        project.preview_mode = "TOON"
        project.onion_enabled = True
        save_path = ROOT / "build" / "studio_active_save.blend"
        assert bpy.ops.wm.save_as_mainfile(filepath=str(save_path)) == {"FINISHED"}
        assert studio.is_studio_active(bpy.context, str(project.uuid))
        assert obj.material_slots[0].material != STATE["original_material"]
        assert bpy.context.scene.tool_settings.image_paint.use_normal_falloff is False
        assert float(view_space.clip_start) <= max(float(value) for value in obj.dimensions) * 1.0e-4 + 1.0e-8
        assert project.onion_enabled
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        assert image_area.spaces.active.image.get(runtime.ROLE_KEY) == ONION_PREVIEW_ROLE
        edit_uuid = str(runtime.active_angle(project).uuid)
        edit_angle = float(runtime.active_angle(project).angle)
        assert bpy.ops.quicksdf.seek_set(angle=22.5) == {"FINISHED"}
        assert studio.current_session().view_mode == "PREVIEW"
        assert str(runtime.active_angle(project).uuid) == edit_uuid
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        from quick_sdf_blender.live_preview import SEEK_PREVIEW_ROLE

        preview_material = obj.material_slots[0].material
        preview_image = preview_material.node_tree.nodes["QSDF Mask"].image
        assert preview_image.get(runtime.ROLE_KEY) == SEEK_PREVIEW_ROLE
        assert bpy.ops.quicksdf.paint_snapshot() == {"FINISHED"}
        assert studio.current_session().view_mode == "EDIT"
        assert float(project.seek_angle) == edit_angle
        assert str(runtime.active_angle(project).uuid) == edit_uuid
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        assert preview_material.node_tree.nodes["QSDF Mask"].image == canvas
        runtime.discard_paint_snapshot(project)
        assert bpy.ops.quicksdf.seek_set(angle=22.5) == {"FINISHED"}
        invalid_values = (True, False, True, True, True, True, True)
        for index, (item, value) in enumerate(zip(project.angles, invalid_values)):
            display = runtime.resolve_display_image(project, item)
            coverage = runtime.resolve_coverage_image(project, item)
            display_rgba = runtime.image_rgba(display)
            coverage_rgba = runtime.image_rgba(coverage)
            display_rgba[90, 90, :3] = float(value)
            display_rgba[90, 90, 3] = 1.0
            coverage_rgba[90, 90, :3] = float(index == 0)
            coverage_rgba[90, 90, 3] = 1.0
            runtime.write_image_rgba(display, display_rgba)
            runtime.write_image_rgba(coverage, coverage_rgba)
        STATE["source_fingerprints"] = _source_fingerprints(project, runtime)
        STATE["edit_uuid"] = edit_uuid
        STATE["canvas"] = canvas
        from quick_sdf_blender import operators

        cancelled_path = ROOT / "build" / "studio_cancelled_export.png"
        cancelled_path.unlink(missing_ok=True)
        assert bpy.ops.quicksdf.export_texture(
            filepath=str(cancelled_path), overwrite=True
        ) == {"FINISHED"}
        assert project.job_running
        cancel_started = time.perf_counter()
        assert bpy.ops.quicksdf.cancel_job() == {"FINISHED"}
        assert time.perf_counter() - cancel_started < 1.0
        assert not project.job_running
        assert operators._EXPORT_JOB is None
        assert not bpy.app.timers.is_registered(operators._poll_export_job)
        assert not cancelled_path.exists()
        stale_path = ROOT / "build" / "studio_stale_export.png"
        stale_path.unlink(missing_ok=True)
        assert bpy.ops.quicksdf.export_texture(
            filepath=str(stale_path), overwrite=True
        ) == {"FINISHED"}
        assert project.job_running
        # A no-op pixel rewrite changes the image revision after the worker's
        # snapshot. The stale result must never be written or mark this edit clean.
        stale_image = runtime.resolve_display_image(project, project.angles[0])
        stale_rgba = runtime.image_rgba(stale_image)
        runtime.write_image_rgba(stale_image, stale_rgba)
        project.dirty = True
        STATE["source_fingerprints"] = _source_fingerprints(project, runtime)
        STATE["stale_path"] = stale_path
        STATE["phase"] = "STALE_EXPORTING"
        return 0.1
    except Exception as error:
        finish(error)
    return None


def run() -> None:
    result_path = ROOT / "build" / "studio_smoke_result.txt"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        assert not bpy.app.background
        assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {"FINISHED"}
        from quick_sdf_blender import runtime, studio

        obj = _mesh()
        original_workspace = bpy.context.window.workspace
        original_material = obj.material_slots[0].material
        bpy.context.scene.quick_sdf_settings.resolution = 512
        bpy.context.scene.quick_sdf_settings.initialization = "NORMAL_SWEEP"
        bpy.context.scene.frame_set(96)
        bpy.context.scene.tool_settings.image_paint.use_normal_falloff = True
        for area in bpy.context.window.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.shading.type = "SOLID"
        assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
        project = runtime.active_project()
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        STATE.update(
            obj=obj,
            project=project,
            original_workspace=original_workspace,
            original_material=original_material,
        )
        bpy.app.timers.register(check, first_interval=0.1)
    except Exception as error:
        finish(error)


bpy.app.timers.register(run, first_interval=0.25)

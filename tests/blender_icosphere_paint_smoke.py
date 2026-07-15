"""Artist-path regression for Quick SDF painting on a UV Icosphere.

This interactive Blender 5.1 smoke test reproduces the model and defaults from
the reported black-viewport regression. It keeps the default normal guide,
middle light-sweep key, and Overlay preview, then drives the public 3D paint macro.

The test deliberately checks rendered UI pixels in addition to node topology:
an acyclic graph can still show a stale or wrong image, while a purely
structural test would miss the artist-visible failure.
"""

from __future__ import annotations

import json
from pathlib import Path
import statistics
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras import view3d_utils  # noqa: E402


RESULT_PATH = ROOT / "build" / "icosphere_paint_smoke_result.txt"
DETAIL_PATH = ROOT / "build" / "icosphere_paint_smoke_details.json"
SCREENSHOT_PATH = ROOT / "build" / "icosphere_paint_smoke.png"
STATE: dict[str, object] = {"phase": "WAIT_STUDIO", "waits": 0, "details": {}}


def _make_icosphere() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.0)
    obj = bpy.context.object
    obj.name = "Quick SDF Artist Paint Icosphere"
    for polygon in obj.data.polygons:
        polygon.use_smooth = True

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=np.deg2rad(66.0), island_margin=0.015)
    bpy.ops.object.mode_set(mode="OBJECT")
    assert obj.data.uv_layers.active is not None

    material = bpy.data.materials.new("Quick SDF Icosphere Artist Material")
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    assert principled is not None
    principled.inputs["Base Color"].default_value = (0.36, 0.36, 0.36, 1.0)
    principled.inputs["Roughness"].default_value = 0.55
    obj.data.materials.append(material)
    return obj


def _active_tool_ids() -> dict[str, str | None]:
    view = bpy.context.workspace.tools.from_space_view3d_mode(
        "PAINT_TEXTURE", create=False
    )
    image = bpy.context.workspace.tools.from_space_image_mode("PAINT", create=False)
    return {
        "view": view.idname if view is not None else None,
        "image": image.idname if image is not None else None,
    }


def _assert_active_tools() -> dict[str, str | None]:
    from quick_sdf_blender.tools import IMAGE_TOOL_ID, VIEW_TOOL_ID

    tools = _active_tool_ids()
    assert tools == {"view": VIEW_TOOL_ID, "image": IMAGE_TOOL_ID}, tools
    return tools


def _graph_state(obj: bpy.types.Object, project, canvas) -> dict[str, object]:
    material = obj.material_slots[int(project.material_slot_index)].material
    expected = bpy.data.materials.get(str(project.preview_material_name))
    assert material is not None and material == expected
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    texture = nodes.get("QSDF Mask")
    mix = nodes.get("QSDF Original Overlay")
    group = nodes.get("QSDF Preview")
    assert texture is not None and texture.image == canvas
    assert mix is not None and group is not None
    assert len(mix.inputs[1].links) == 1
    assert len(mix.inputs[2].links) == 1

    self_links = [
        (link.from_node.name, link.to_node.name)
        for link in links
        if int(link.from_node.as_pointer()) == int(link.to_node.as_pointer())
    ]
    assert not self_links, self_links
    assert int(mix.inputs[1].links[0].from_node.as_pointer()) != int(mix.as_pointer())
    assert int(mix.inputs[2].links[0].from_node.as_pointer()) == int(group.as_pointer())

    outputs = [
        node
        for node in nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
        and bool(getattr(node, "is_active_output", False))
    ]
    assert len(outputs) == 1
    surface_links = tuple(outputs[0].inputs["Surface"].links)
    assert len(surface_links) == 1
    assert int(surface_links[0].from_node.as_pointer()) == int(mix.as_pointer())

    topology = sorted(
        (
            link.from_node.name,
            link.from_socket.name,
            link.to_node.name,
            link.to_socket.name,
        )
        for link in links
    )
    return {
        "material_pointer": int(material.as_pointer()),
        "material_name": material.name,
        "node_count": len(nodes),
        "topology": topology,
        "mask_image": texture.image.name if texture.image is not None else "",
        "original_input": mix.inputs[1].links[0].from_node.name,
        "preview_input": mix.inputs[2].links[0].from_node.name,
        "output_input": surface_links[0].from_node.name,
    }


def _assert_graph_unchanged(reference: dict[str, object], current: dict[str, object]) -> None:
    assert current["material_pointer"] == reference["material_pointer"], current
    assert current["node_count"] == reference["node_count"], current
    assert current["topology"] == reference["topology"], current
    assert current["mask_image"] == reference["mask_image"], current


def _stroke_points(x: float, y: float, *, size: float = 92.0) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    offsets = np.linspace(-16.0, 16.0, 9)
    for index, offset in enumerate(offsets):
        mouse = (x + float(offset), y)
        points.append(
            {
                "name": "Quick SDF Icosphere artist stroke",
                "location": (0.0, 0.0, 0.0),
                "mouse": mouse,
                "mouse_event": mouse,
                "pressure": 1.0,
                "size": size,
                "x_tilt": 0.0,
                "y_tilt": 0.0,
                "time": float(index) * 0.02,
                "is_start": index == 0,
            }
        )
    return points


def _public_stroke(
    view_area, view_region, stroke, *, invert: bool = False
) -> tuple[set[str], float]:
    with bpy.context.temp_override(
        window=bpy.context.window,
        screen=bpy.context.window.screen,
        area=view_area,
        region=view_region,
    ):
        operator = (
            bpy.ops.quicksdf.range_paint_invert
            if invert
            else bpy.ops.quicksdf.range_paint
        )
        assert operator.poll()
        started = time.perf_counter()
        result = operator(
            "EXEC_DEFAULT",
            PAINT_OT_image_paint={"stroke": stroke, "mode": "NORMAL"},
        )
        elapsed = time.perf_counter() - started
    return result, elapsed


def _sample_screenshot(path: Path, x: float, y: float) -> list[float]:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size[:]
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rgba = pixels.reshape(height, width, 4)
        assert 0.0 <= x < width and 0.0 <= y < height, (x, y, width, height)
        cx = int(round(x))
        cy = int(round(y))
        patch = rgba[
            max(0, cy - 4) : min(height, cy + 5),
            max(0, cx - 4) : min(width, cx + 5),
            :3,
        ]
        rgb = np.median(patch, axis=(0, 1))
        assert np.all(np.isfinite(rgb)), rgb
        return rgb.astype(float).tolist()
    finally:
        bpy.data.images.remove(image)


def _finish(error: BaseException | None = None) -> None:
    details = dict(STATE.get("details", {}))
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if error is None:
        RESULT_PATH.write_text("PASS", encoding="utf-8")
        details["status"] = "PASS"
    else:
        formatted = "".join(traceback.format_exception(error))
        RESULT_PATH.write_text(formatted, encoding="utf-8")
        details["status"] = "FAIL"
        details["error"] = formatted
    DETAIL_PATH.write_text(json.dumps(details, indent=2), encoding="utf-8")
    try:
        bpy.ops.preferences.addon_disable(module="quick_sdf_blender")
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def _tick() -> float | None:
    try:
        from quick_sdf_blender import preview, runtime, studio

        project = STATE["project"]
        obj = STATE["object"]
        phase = str(STATE["phase"])

        if phase == "WAIT_STUDIO":
            session = studio.current_session()
            if session is None:
                STATE["waits"] = int(STATE["waits"]) + 1
                if int(STATE["waits"]) > 250:
                    raise AssertionError(
                        project.diagnostic_message or "Quick SDF Paint did not open"
                    )
                return 0.04
            STATE["details"]["tools_after_enter"] = _assert_active_tools()
            STATE["phase"] = "FRAME"
            return 0.1

        if phase == "WAIT_TIMELINE_UNDO":
            from quick_sdf_blender import operators as operator_module

            project_uuid = str(project.uuid)
            session = studio.current_session()
            assert session is not None, "Timeline Ctrl+Z escaped to Blender's global Undo"
            assert session.project_uuid == project_uuid
            assert runtime.active_project(bpy.context.scene) == project
            assert any(
                str(candidate.uuid) == project_uuid
                for candidate in bpy.context.scene.quick_sdf_projects
            )
            canvas = bpy.data.images.get(str(STATE["timeline_canvas_name"]))
            assert canvas is not None
            assert int(canvas.as_pointer()) == int(STATE["timeline_canvas_pointer"])
            assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
            assert obj.mode == "TEXTURE_PAINT"

            history = operator_module._HISTORIES.get(project_uuid)
            expected_undo = int(STATE["timeline_undo_count_before"]) - 1
            expected_redo = int(STATE["timeline_redo_count_before"]) + 1
            event_finished = bool(
                history is not None
                and history.undo_count == expected_undo
                and history.redo_count == expected_redo
            )
            if not event_finished:
                STATE["timeline_undo_waits"] = int(
                    STATE.get("timeline_undo_waits", 0)
                ) + 1
                if int(STATE["timeline_undo_waits"]) <= 80:
                    return 0.05
                raise AssertionError(
                    "Timeline Ctrl+Z did not reach Quick SDF history: "
                    f"undo={getattr(history, 'undo_count', None)}, "
                    f"redo={getattr(history, 'redo_count', None)}, "
                    f"expected=({expected_undo}, {expected_redo})"
                )

            np.testing.assert_array_equal(
                runtime.image_rgba8(canvas), STATE["timeline_before"]
            )
            assert project_uuid in operator_module._UNDO_FENCES
            assert bpy.ops.quicksdf.history_redo.poll()
            assert bpy.ops.quicksdf.history_redo() == {"FINISHED"}
            np.testing.assert_array_equal(
                runtime.image_rgba8(canvas), STATE["timeline_after"]
            )
            assert history.undo_count == int(STATE["timeline_undo_count_before"])
            assert history.redo_count == int(STATE["timeline_redo_count_before"])
            STATE["details"]["timeline_ctrl_z"] = {
                "undo_count_before": int(STATE["timeline_undo_count_before"]),
                "redo_count_before": int(STATE["timeline_redo_count_before"]),
                "canvas_exact": True,
                "project_and_session_preserved": True,
                "event_xy": STATE["timeline_event_xy"],
            }
            STATE["phase"] = "SCREENSHOT"
            return 0.6

        if phase == "WAIT_EXIT":
            STATE["exit_waits"] = int(STATE.get("exit_waits", 0)) + 1
            workspace_restored = (
                studio.current_session() is None
                and bpy.context.window.workspace.name
                == STATE["previous_workspace_name"]
            )
            if not workspace_restored:
                if int(STATE["exit_waits"]) <= 200:
                    return 0.05
                raise AssertionError(
                    "Quick SDF Paint did not restore the previous workspace: "
                    f"session={studio.current_session()!r}, "
                    f"workspace={bpy.context.window.workspace.name!r}"
                )

            from quick_sdf_blender import operators as operator_module

            project_uuid = str(STATE["exit_project_uuid"])
            assert obj.mode == STATE["previous_object_mode"]
            assert obj.material_slots[0].material == STATE["original_material"]
            assert (
                bpy.context.scene.tool_settings.image_paint.canvas
                == STATE["previous_canvas"]
            )
            restored_brush = bpy.context.scene.tool_settings.image_paint.brush
            assert restored_brush is not None
            assert int(restored_brush.as_pointer()) == STATE["brush_pointer"]
            assert (
                tuple(restored_brush.color),
                tuple(restored_brush.secondary_color),
            ) == STATE["brush_colours"]
            restored_unified = (
                bpy.context.scene.tool_settings.image_paint.unified_paint_settings
            )
            assert (
                bool(restored_unified.use_unified_color),
                tuple(restored_unified.color),
                tuple(restored_unified.secondary_color),
            ) == STATE["unified_colours"]
            assert not bpy.app.timers.is_registered(studio._stroke_restore_watchdog)
            assert project_uuid not in runtime._PAINT_SNAPSHOTS
            assert project_uuid not in runtime._INTERACTIVE_PAINT_SNAPSHOTS
            assert project_uuid not in operator_module._HISTORIES
            assert project_uuid not in operator_module._UNDO_FENCES
            STATE["details"]["studio_exit_cleanup"] = {
                "wait_iterations": int(STATE["exit_waits"]),
                "workspace": bpy.context.window.workspace.name,
                "mode": obj.mode,
                "material_restored": True,
                "canvas_restored": True,
                "paint_settings_restored": True,
                "watchdog_released": True,
                "snapshots_released": True,
                "history_released": True,
            }
            print("ICOSPHERE_STROKE_SECONDS", STATE["details"]["stroke_seconds"])
            print(
                "ICOSPHERE_VIEWPORT_CENTER_RGB",
                STATE["details"]["viewport_center_rgb"],
            )
            print("[Quick SDF Icosphere paint smoke] PASS")
            _finish()
            return None

        view_area = next(
            area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D"
        )
        view_region = next(
            region for region in view_area.regions if region.type == "WINDOW"
        )
        view_space = view_area.spaces.active

        if phase == "FRAME":
            assert obj.mode == "TEXTURE_PAINT"
            assert view_space.shading.type == "MATERIAL"
            with bpy.context.temp_override(
                window=bpy.context.window,
                screen=bpy.context.window.screen,
                area=view_area,
                region=view_region,
            ):
                assert bpy.ops.view3d.view_axis(type="FRONT", align_active=False) == {
                    "FINISHED"
                }
                assert bpy.ops.view3d.view_selected(use_all_regions=False) == {
                    "FINISHED"
                }
            STATE["phase"] = "PAINT"
            # Let the view matrices and initial Material Preview shader settle.
            return 0.8

        if phase == "PAINT":
            active = runtime.active_angle(project)
            assert active is not None
            expected = min(
                (float(item.angle) for item in project.angles),
                key=lambda value: abs(value - 45.0),
            )
            assert abs(float(active.angle) - expected) < 0.001, float(active.angle)
            assert str(active.side) == "RIGHT"
            assert str(project.base_source) == "NORMAL_GUIDE"
            assert int(project.resolution) == 1024
            project.preview_mode = "OVERLAY"

            canvas = runtime.resolve_display_image(project, active)
            assert canvas is not None
            runtime.sync_canvas(bpy.context, project)
            assert bpy.context.scene.tool_settings.image_paint.canvas == canvas

            # Reusing the preview graph was the trigger for the previous black
            # self-cycle, so exercise idempotence before the first stroke.
            preview.ensure_preview_material(project, canvas)
            preview.ensure_preview_material(project, canvas)
            graph_reference = _graph_state(obj, project, canvas)

            brush = bpy.context.scene.tool_settings.image_paint.brush
            assert brush is not None
            brush.size = 92
            brush.strength = 1.0
            brush.blend = "MIX"
            image_paint = bpy.context.scene.tool_settings.image_paint
            unified = image_paint.unified_paint_settings
            brush_colours = (tuple(brush.color), tuple(brush.secondary_color))
            unified_colours = (
                bool(unified.use_unified_color),
                tuple(unified.color),
                tuple(unified.secondary_color),
            )
            STATE["brush_pointer"] = int(brush.as_pointer())
            STATE["brush_colours"] = brush_colours
            STATE["unified_colours"] = unified_colours

            def assert_paint_settings_restored() -> None:
                assert (tuple(brush.color), tuple(brush.secondary_color)) == brush_colours
                assert (
                    bool(unified.use_unified_color),
                    tuple(unified.color),
                    tuple(unified.secondary_color),
                ) == unified_colours

            center = view3d_utils.location_3d_to_region_2d(
                view_region,
                view_space.region_3d,
                obj.matrix_world.translation,
            )
            assert center is not None
            x, y = float(center.x), float(center.y)
            assert 40.0 < x < view_region.width - 40.0, (x, view_region.width)
            assert 40.0 < y < view_region.height - 40.0, (y, view_region.height)
            stroke = _stroke_points(x, y)

            before_first = runtime.image_rgba(canvas)
            timings: list[float] = []
            changed_counts: list[int] = []
            latest = before_first
            for stroke_index in range(5):
                # Restore the same guide input between measurements so every
                # timed Shadow stroke has deterministic work to do. This keeps
                # the benchmark independent of brush falloff accumulation.
                if stroke_index:
                    runtime.write_image_rgba(canvas, before_first)
                project.paint_value = 0
                before = runtime.image_rgba(canvas)
                result, elapsed = _public_stroke(view_area, view_region, stroke)
                assert result == {"FINISHED"}, result
                assert_paint_settings_restored()
                after = runtime.image_rgba(canvas)
                changed = np.any(
                    np.abs(after[..., :3] - before[..., :3]) > (0.5 / 255.0),
                    axis=2,
                )
                changed_count = int(np.count_nonzero(changed))
                assert changed_count > 0, stroke_index
                assert np.all(after[..., 0][changed] < before[..., 0][changed])
                timings.append(elapsed)
                changed_counts.append(changed_count)
                latest = after
                _assert_graph_unchanged(
                    graph_reference, _graph_state(obj, project, canvas)
                )
                assert _assert_active_tools() == STATE["details"]["tools_after_enter"]

                if stroke_index == 0:
                    endpoint = next(
                        item
                        for item in project.angles
                        if str(item.side) == "RIGHT" and abs(float(item.angle)) < 0.001
                    )
                    # A rejected structural action must not discard the last
                    # valid paint Undo entry.
                    try:
                        bpy.ops.quicksdf.key_delete(uuid=str(endpoint.uuid))
                    except RuntimeError as exc:
                        assert "endpoints cannot be deleted" in str(exc)
                    else:
                        raise AssertionError("The locked endpoint delete was not rejected")
                    assert bpy.ops.quicksdf.history_undo.poll()
                    assert bpy.ops.quicksdf.history_undo() == {"FINISHED"}
                    np.testing.assert_array_equal(
                        runtime.image_rgba(canvas), before_first
                    )
                    assert bpy.ops.quicksdf.history_redo.poll()
                    assert bpy.ops.quicksdf.history_redo() == {"FINISHED"}
                    np.testing.assert_array_equal(runtime.image_rgba(canvas), after)
                    _assert_graph_unchanged(
                        graph_reference, _graph_state(obj, project, canvas)
                    )

            median_seconds = float(statistics.median(timings))
            max_seconds = float(max(timings))
            assert median_seconds < 0.30, timings
            # Allows a one-off driver/shader scheduling spike while still
            # rejecting the former near-second whole-stack pen-up stall.
            assert max_seconds < 0.75, timings

            # Light and Shadow are the only artist-facing paint actions.  A
            # Blender 5.1 Brush Asset can keep the active Palette swatch, so a
            # regression here previously made the Light button paint Shadow.
            project.paint_value = 1
            before_light = runtime.image_rgba(canvas)
            light_result, light_seconds = _public_stroke(
                view_area, view_region, stroke
            )
            assert light_result == {"FINISHED"}, light_result
            assert_paint_settings_restored()
            after_light = runtime.image_rgba(canvas)
            light_changed = np.any(
                np.abs(after_light[..., :3] - before_light[..., :3])
                > (0.5 / 255.0),
                axis=2,
            )
            assert np.any(light_changed)
            assert np.all(
                after_light[..., 0][light_changed]
                > before_light[..., 0][light_changed]
            )
            assert light_seconds < 0.75, light_seconds
            _assert_graph_unchanged(
                graph_reference, _graph_state(obj, project, canvas)
            )
            assert _assert_active_tools() == STATE["details"]["tools_after_enter"]

            # Ctrl+LMB uses the opposite temporary colour without changing the
            # selected Light/Shadow action or the user's Brush Asset settings.
            runtime.write_image_rgba(canvas, latest)

            # Once native painting has started, extra Undo/Redo shortcuts stay
            # inside Quick SDF instead of falling through to Blender's global
            # stack and removing the project/session.
            from quick_sdf_blender import operators as operator_module

            operator_module.clear_histories(str(project.uuid))
            fenced_pixels = runtime.image_rgba8(canvas)
            assert bpy.ops.quicksdf.history_undo.poll()
            assert bpy.ops.quicksdf.history_undo() == {"FINISHED"}
            assert bpy.ops.quicksdf.history_undo() == {"FINISHED"}
            assert bpy.ops.quicksdf.history_redo.poll()
            assert bpy.ops.quicksdf.history_redo() == {"FINISHED"}
            np.testing.assert_array_equal(runtime.image_rgba8(canvas), fenced_pixels)
            assert str(project.uuid) in operator_module._UNDO_FENCES
            project.paint_value = 0
            before_shadow_invert = runtime.image_rgba(canvas)
            shadow_invert_result, _ = _public_stroke(
                view_area, view_region, stroke, invert=True
            )
            assert shadow_invert_result == {"FINISHED"}
            after_shadow_invert = runtime.image_rgba(canvas)
            shadow_invert_changed = np.any(
                np.abs(after_shadow_invert[..., :3] - before_shadow_invert[..., :3])
                > (0.5 / 255.0),
                axis=2,
            )
            assert np.any(shadow_invert_changed)
            assert np.all(
                after_shadow_invert[..., 0][shadow_invert_changed]
                > before_shadow_invert[..., 0][shadow_invert_changed]
            )
            assert_paint_settings_restored()

            project.paint_value = 1
            before_light_invert = runtime.image_rgba(canvas)
            light_invert_result, _ = _public_stroke(
                view_area, view_region, stroke, invert=True
            )
            assert light_invert_result == {"FINISHED"}
            after_light_invert = runtime.image_rgba(canvas)
            light_invert_changed = np.any(
                np.abs(after_light_invert[..., :3] - before_light_invert[..., :3])
                > (0.5 / 255.0),
                axis=2,
            )
            assert np.any(light_invert_changed)
            assert np.all(
                after_light_invert[..., 0][light_invert_changed]
                < before_light_invert[..., 0][light_invert_changed]
            )
            assert_paint_settings_restored()

            # A same-colour 3D no-op remains successful and must use only the
            # quiet Studio hint; it must never become a warning/error banner.
            # Filling the source black makes the no-op deterministic.
            all_shadow = np.array(latest, copy=True)
            all_shadow[..., :3] = 0.0
            all_shadow[..., 3] = 1.0
            runtime.write_image_rgba(canvas, all_shadow)
            project.paint_value = 0
            session = studio.current_session()
            assert session is not None
            session.projection_hint = ""
            project.warning_message = ""
            project.diagnostic_message = ""
            no_op_result, no_op_seconds = _public_stroke(
                view_area, view_region, stroke
            )
            assert no_op_result == {"FINISHED"}, no_op_result
            np.testing.assert_array_equal(runtime.image_rgba(canvas), all_shadow)
            assert session.projection_hint == (
                "No paint landed · move back or press Numpad 5"
            )
            assert not project.warning_message
            assert not project.diagnostic_message
            assert no_op_seconds < 0.75, no_op_seconds
            runtime.write_image_rgba(canvas, latest)

            _assert_graph_unchanged(
                graph_reference, _graph_state(obj, project, canvas)
            )
            assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
            assert obj.mode == "TEXTURE_PAINT"
            assert view_space.shading.type == "MATERIAL"
            tools_after = _assert_active_tools()

            for area in bpy.context.window.screen.areas:
                area.tag_redraw()
            STATE["screen_center"] = (
                float(view_region.x + x),
                float(view_region.y + y),
            )
            STATE["details"].update(
                {
                    "blender": bpy.app.version_string,
                    "object": obj.name,
                    "resolution": int(project.resolution),
                    "base_source": str(project.base_source),
                    "active_angle": float(active.angle),
                    "active_side": str(active.side),
                    "preview_mode": str(project.preview_mode),
                    "stroke_seconds": timings,
                    "stroke_median_seconds": median_seconds,
                    "stroke_max_seconds": max_seconds,
                    "light_seconds": light_seconds,
                    "changed_pixels": changed_counts,
                    "no_op_seconds": no_op_seconds,
                    "undo_exact": True,
                    "redo_exact": True,
                    "tools_after_strokes": tools_after,
                    "graph": {
                        key: value
                        for key, value in graph_reference.items()
                        if key != "material_pointer"
                    },
                }
            )

            # Create one final deterministic history entry, then send the
            # actual Ctrl+Z key event with the pointer over the full-width
            # timeline. A direct operator call cannot prove that the timeline
            # keymap prevents Blender's global Undo from removing the session.
            runtime.write_image_rgba(canvas, before_first)
            timeline_before = runtime.image_rgba8(canvas)
            project.paint_value = 0
            timeline_result, timeline_seconds = _public_stroke(
                view_area, view_region, stroke
            )
            assert timeline_result == {"FINISHED"}, timeline_result
            assert_paint_settings_restored()
            timeline_after = runtime.image_rgba8(canvas)
            assert not np.array_equal(timeline_after, timeline_before)
            _assert_graph_unchanged(
                graph_reference, _graph_state(obj, project, canvas)
            )
            assert _assert_active_tools() == STATE["details"]["tools_after_enter"]

            history = operator_module._HISTORIES.get(str(project.uuid))
            assert history is not None and history.can_undo
            timeline_area = next(
                area
                for area in bpy.context.window.screen.areas
                if area.type == "NODE_EDITOR"
            )
            timeline_region = next(
                region for region in timeline_area.regions if region.type == "WINDOW"
            )
            event_x = int(timeline_region.x + timeline_region.width // 2)
            event_y = int(timeline_region.y + timeline_region.height // 2)
            STATE.update(
                timeline_canvas_name=canvas.name,
                timeline_canvas_pointer=int(canvas.as_pointer()),
                timeline_before=timeline_before,
                timeline_after=timeline_after,
                timeline_undo_count_before=history.undo_count,
                timeline_redo_count_before=history.redo_count,
                timeline_event_xy=[event_x, event_y],
                timeline_undo_waits=0,
                phase="WAIT_TIMELINE_UNDO",
            )
            STATE["details"]["timeline_stroke_seconds"] = timeline_seconds
            window = bpy.context.window
            window.event_simulate(
                type="MOUSEMOVE", value="NOTHING", x=event_x, y=event_y
            )
            window.event_simulate(
                type="Z", value="PRESS", x=event_x, y=event_y, ctrl=True
            )
            window.event_simulate(
                type="Z", value="RELEASE", x=event_x, y=event_y, ctrl=True
            )
            return 0.1

        if phase == "SCREENSHOT":
            SCREENSHOT_PATH.unlink(missing_ok=True)
            result = bpy.ops.screen.screenshot(
                "EXEC_DEFAULT",
                filepath=str(SCREENSHOT_PATH),
                check_existing=False,
            )
            assert result == {"FINISHED"}, result
            assert SCREENSHOT_PATH.is_file()
            x, y = STATE["screen_center"]
            rgb = _sample_screenshot(SCREENSHOT_PATH, float(x), float(y))
            STATE["details"]["viewport_center_rgb"] = rgb
            # The reported material cycle rendered the target at exact black.
            assert max(rgb) > 0.03, rgb
            STATE["exit_project_uuid"] = str(project.uuid)
            STATE["exit_waits"] = 0
            assert bpy.ops.quicksdf.studio_exit() == {"FINISHED"}
            STATE["phase"] = "WAIT_EXIT"
            return 0.05

        raise AssertionError(f"Unknown smoke-test phase: {phase}")
    except Exception as error:
        _finish(error)
        return None


def _start() -> None:
    try:
        RESULT_PATH.unlink(missing_ok=True)
        DETAIL_PATH.unlink(missing_ok=True)
        SCREENSHOT_PATH.unlink(missing_ok=True)
        assert not bpy.app.background
        assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {
            "FINISHED"
        }
        from quick_sdf_blender import runtime

        obj = _make_icosphere()
        scene = bpy.context.scene
        previous_workspace_name = bpy.context.window.workspace.name
        previous_object_mode = obj.mode
        original_material = obj.material_slots[0].material
        scene.quick_sdf_settings.resolution = 1024
        scene.quick_sdf_settings.initialization = "NORMAL_SWEEP"
        assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
        project = runtime.active_project(scene)
        assert project is not None
        expected = min(
            (float(item.angle) for item in project.angles),
            key=lambda value: abs(value - 45.0),
        )
        assert abs(float(runtime.active_angle(project).angle) - expected) < 0.001
        # Studio restores the paint settings present at its own entry point.
        # ``project_create`` intentionally synchronizes the new project canvas
        # before this separate ``studio_enter`` call.
        previous_canvas = scene.tool_settings.image_paint.canvas
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        STATE.update(
            object=obj,
            project=project,
            previous_workspace_name=previous_workspace_name,
            previous_canvas=previous_canvas,
            previous_object_mode=previous_object_mode,
            original_material=original_material,
        )
        bpy.app.timers.register(_tick, first_interval=0.1)
    except Exception as error:
        _finish(error)


bpy.app.timers.register(_start, first_interval=0.25)

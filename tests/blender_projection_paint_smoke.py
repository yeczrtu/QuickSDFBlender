"""Interactive Blender 5.1 regression for a real 3D Projection Paint stroke.

Unlike array-level Smart Paint tests, this drives Quick SDF's public paint
macro with ``PAINT_OT_image_paint`` stroke elements in a VIEW_3D region.  A
camera-facing plane with one full-tile UV removes Cube face and hidden-island
ambiguity from the result.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras import view3d_utils  # noqa: E402


RESULT_PATH = ROOT / "build" / "projection_paint_smoke_result.txt"
STATE: dict[str, object] = {"waits": 0, "phase": "OPENING"}


def _plane() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2.0)
    obj = bpy.context.object
    obj.name = "Quick SDF Projection Paint Plane"
    uv_layer = obj.data.uv_layers.active or obj.data.uv_layers.new(name="PaintUV")
    for loop in obj.data.loops:
        co = obj.data.vertices[loop.vertex_index].co
        uv_layer.data[loop.index].uv = (co.x * 0.5 + 0.5, co.y * 0.5 + 0.5)
    obj.data.uv_layers.active = uv_layer
    material = bpy.data.materials.new("Projection Paint Source")
    material.use_nodes = True
    material.node_tree.nodes.clear()
    obj.data.materials.append(material)
    return obj


def _assert_preview_graph(obj, project, canvas) -> None:
    material = obj.material_slots[int(project.material_slot_index)].material
    stored = bpy.data.materials.get(str(project.preview_material_name))
    assert material is not None and material == stored
    assert material != project.original_material
    nodes = material.node_tree.nodes
    mix = nodes.get("QSDF Original Overlay")
    group = nodes.get("QSDF Preview")
    texture = nodes.get("QSDF Mask")
    assert mix is not None and group is not None and texture is not None
    assert texture.image == canvas
    assert len(mix.inputs[1].links) == 1
    assert len(mix.inputs[2].links) == 1
    assert mix.inputs[1].links[0].from_node.as_pointer() != mix.as_pointer()
    assert mix.inputs[2].links[0].from_node.as_pointer() == group.as_pointer()
    assert not any(
        link.from_node.as_pointer() == link.to_node.as_pointer()
        for link in material.node_tree.links
    )
    outputs = [
        node
        for node in nodes
        if node.bl_idname == "ShaderNodeOutputMaterial"
        and bool(getattr(node, "is_active_output", False))
    ]
    assert len(outputs) == 1
    surface_links = tuple(outputs[0].inputs["Surface"].links)
    assert len(surface_links) == 1
    assert surface_links[0].from_node.as_pointer() == mix.as_pointer()


def _finish(error: BaseException | None = None) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if error is None:
        RESULT_PATH.write_text("PASS", encoding="utf-8")
    else:
        RESULT_PATH.write_text(
            "".join(traceback.format_exception(error)), encoding="utf-8"
        )
    try:
        bpy.ops.preferences.addon_disable(module="quick_sdf_blender")
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def _paint() -> float | None:
    try:
        from quick_sdf_blender import preview, runtime, studio
        from quick_sdf_blender.tools import VIEW_TOOL_ID

        project = STATE["project"]
        obj = STATE["object"]
        if studio.current_session() is None:
            STATE["waits"] = int(STATE["waits"]) + 1
            if int(STATE["waits"]) > 200:
                raise AssertionError(
                    project.diagnostic_message or "Studio did not finish opening"
                )
            return 0.05

        view_area = next(
            area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D"
        )
        view_region = next(region for region in view_area.regions if region.type == "WINDOW")
        view_space = view_area.spaces.active

        if STATE["phase"] == "OPENING":
            STATE["phase"] = "VIEW_READY"
            with bpy.context.temp_override(
                window=bpy.context.window,
                screen=bpy.context.window.screen,
                area=view_area,
                region=view_region,
            ):
                assert bpy.ops.view3d.view_axis(type="TOP", align_active=False) == {
                    "FINISHED"
                }
                assert bpy.ops.view3d.view_selected(use_all_regions=False) == {
                    "FINISHED"
                }
            # Let Blender update RegionView3D matrices before deriving the
            # screen-space stroke coordinates.
            return 0.5

        assert obj.mode == "TEXTURE_PAINT"
        assert view_space.shading.type == "MATERIAL"
        tool = bpy.context.workspace.tools.from_space_view3d_mode(
            "PAINT_TEXTURE", create=False
        )
        assert tool is not None and tool.idname == VIEW_TOOL_ID

        project.preview_mode = "MASK"
        project.paint_value = 0  # Shadow over the all-white initialization.
        canvas = runtime.resolve_angle_image(project, runtime.active_angle(project))
        coverage = runtime.resolve_coverage_image(project, runtime.active_angle(project))
        assert canvas is not None and coverage is not None
        runtime.sync_canvas(bpy.context, project)
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas

        # Reusing a preview graph must never connect the Mix Shader to itself.
        preview.ensure_preview_material(project, canvas)
        preview.ensure_preview_material(project, canvas)
        assert bpy.ops.quicksdf.key_select(index=int(project.active_angle_index)) == {
            "FINISHED"
        }
        _assert_preview_graph(obj, project, canvas)

        brush = bpy.context.scene.tool_settings.image_paint.brush
        assert brush is not None, "Factory-startup Texture Paint has no active Brush Asset"
        brush.size = 96
        brush.strength = 1.0
        brush.blend = "MIX"

        before = runtime.image_rgba(canvas)
        coverage_before = runtime.coverage_mask(coverage)
        assert np.all(before[..., :3] == 1.0)
        assert not np.any(coverage_before)

        center = view3d_utils.location_3d_to_region_2d(
            view_region,
            view_space.region_3d,
            obj.matrix_world.translation,
        )
        assert center is not None
        x, y = float(center.x), float(center.y)
        assert 24.0 < x < view_region.width - 24.0
        assert 24.0 < y < view_region.height - 24.0
        stroke = [
            {
                "name": "Quick SDF regression stroke",
                "location": (0.0, 0.0, 0.0),
                "mouse": (x - 8.0, y),
                "mouse_event": (x - 8.0, y),
                "pressure": 1.0,
                "size": 96.0,
                "x_tilt": 0.0,
                "y_tilt": 0.0,
                "time": 0.0,
                "is_start": True,
            },
            {
                "name": "Quick SDF regression stroke",
                "location": (0.0, 0.0, 0.0),
                "mouse": (x + 8.0, y),
                "mouse_event": (x + 8.0, y),
                "pressure": 1.0,
                "size": 96.0,
                "x_tilt": 0.0,
                "y_tilt": 0.0,
                "time": 0.1,
                "is_start": False,
            },
        ]
        with bpy.context.temp_override(
            window=bpy.context.window,
            screen=bpy.context.window.screen,
            area=view_area,
            region=view_region,
        ):
            assert bpy.ops.quicksdf.range_paint.poll()
            started = time.perf_counter()
            result = bpy.ops.quicksdf.range_paint(
                "EXEC_DEFAULT",
                PAINT_OT_image_paint={"stroke": stroke, "mode": "NORMAL"},
            )
            elapsed = time.perf_counter() - started
        assert result == {"FINISHED"}, result
        assert elapsed < 0.75, f"1024px paint macro took {elapsed:.3f}s"

        after = runtime.image_rgba(canvas)
        coverage_after = runtime.coverage_mask(coverage)
        changed = np.any(np.abs(after[..., :3] - before[..., :3]) > (0.5 / 255.0), axis=2)
        assert np.any(changed)
        np.testing.assert_array_equal(coverage_after, coverage_before)
        assert np.any(after[..., 0] < 0.5)
        assert project.first_stroke_complete
        assert project.dirty
        assert bpy.ops.quicksdf.history_undo.poll()

        # The macro's final Canvas synchronization must not drop the visible
        # 3D material or leave the editor in Solid mode.
        assert view_space.shading.type == "MATERIAL"
        assert obj.mode == "TEXTURE_PAINT"
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        _assert_preview_graph(obj, project, canvas)

        assert bpy.ops.quicksdf.history_undo() == {"FINISHED"}
        restored = runtime.image_rgba(canvas)
        np.testing.assert_array_equal(restored, before)
        assert bpy.ops.quicksdf.history_redo() == {"FINISHED"}
        redone = runtime.image_rgba(canvas)
        np.testing.assert_array_equal(redone, after)
        assert view_space.shading.type == "MATERIAL"
        _assert_preview_graph(obj, project, canvas)

        # Native-speed strokes defer coverage until an explicit rebuild. The
        # Rebake operation must materialize that visible delta and preserve it.
        project.guide_shadow_amount = 0.0
        assert bpy.ops.quicksdf.bake_base() == {"FINISHED"}
        rebaked = runtime.image_rgba(canvas)
        np.testing.assert_array_equal(rebaked[..., :3][changed], after[..., :3][changed])
        coverage_rebaked = runtime.coverage_mask(coverage)
        assert np.all(coverage_rebaked[changed])
        _assert_preview_graph(obj, project, canvas)

        print("PROJECTION_PAINT_CHANGED_PIXELS", int(np.count_nonzero(changed)))
        print("PROJECTION_PAINT_SECONDS", f"{elapsed:.4f}")
        print("[Quick SDF projection paint smoke] PASS")
        _finish()
    except Exception as error:
        _finish(error)
    return None


def _run() -> None:
    try:
        RESULT_PATH.unlink(missing_ok=True)
        assert not bpy.app.background
        assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {
            "FINISHED"
        }
        from quick_sdf_blender import runtime

        obj = _plane()
        scene = bpy.context.scene
        scene.quick_sdf_settings.resolution = 1024
        scene.quick_sdf_settings.initialization = "WHITE"
        assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
        project = runtime.active_project(scene)
        assert project is not None
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        STATE.update(object=obj, project=project)
        bpy.app.timers.register(_paint, first_interval=0.1)
    except Exception as error:
        _finish(error)


bpy.app.timers.register(_run, first_interval=0.25)

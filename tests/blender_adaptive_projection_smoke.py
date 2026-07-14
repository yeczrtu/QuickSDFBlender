"""Blender 5.1 GUI regression for Projection Paint on an adaptive key.

This test deliberately combines the two paths that older smoke tests kept
separate: a session-only interpolated angle and Blender's real
``PAINT_OT_image_paint`` operator.  It protects against a generated provisional
Image falling back to its black ``generated_color`` when it becomes the active
Projection Paint canvas.

Run with::

    blender.exe --factory-startup --python-exit-code 1 \
        --python tests/blender_adaptive_projection_smoke.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras import view3d_utils  # noqa: E402


RESULT_PATH = ROOT / "build" / "adaptive_projection_smoke_result.txt"
DETAIL_PATH = ROOT / "build" / "adaptive_projection_smoke_details.json"
STATE: dict[str, object] = {
    "phase": "WAIT_STUDIO",
    "waits": 0,
    "details": {},
}


def _make_multislot_uv_sphere() -> tuple[bpy.types.Object, tuple[bpy.types.Material, ...]]:
    """Create a moderately dense, multi-island, three-slot paint target."""

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0)
    obj = bpy.context.object
    obj.name = "Quick SDF Adaptive Projection Sphere"
    for polygon in obj.data.polygons:
        polygon.use_smooth = True

    materials = []
    colors = ((0.16, 0.20, 0.30, 1.0), (0.62, 0.48, 0.34, 1.0), (0.24, 0.40, 0.20, 1.0))
    for index, color in enumerate(colors):
        material = bpy.data.materials.new(f"Quick SDF Adaptive Slot {index}")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        assert principled is not None
        principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
        materials.append(material)

    # Keep the target slot on the central visible belt, while sprinkling it
    # across the remaining surface.  This resembles a character whose face
    # material shares one mesh with hair/eye/accessory slots and guarantees a
    # center-screen Projection Paint hit on either front-view direction.
    for index, polygon in enumerate(obj.data.polygons):
        if abs(float(polygon.center.z)) < 0.38 or index % 11 == 0:
            polygon.material_index = 1
        elif float(polygon.center.z) >= 0.0:
            polygon.material_index = 0
        else:
            polygon.material_index = 2

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(
        angle_limit=math.radians(58.0),
        island_margin=0.008,
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    assert obj.data.uv_layers.active is not None
    assert len(obj.data.polygons) > 1_000
    assert {polygon.material_index for polygon in obj.data.polygons} == {0, 1, 2}
    obj.active_material_index = 1
    return obj, tuple(materials)


def _lane(project, side: str = "RIGHT") -> list[object]:
    return sorted(
        (item for item in project.angles if str(item.side) == side),
        key=lambda item: float(item.angle),
    )


def _stroke_points(x: float, y: float, *, size: float = 72.0) -> list[dict[str, object]]:
    points = []
    for index, offset in enumerate(np.linspace(-12.0, 12.0, 7)):
        mouse = (x + float(offset), y)
        points.append(
            {
                "name": "Quick SDF adaptive Projection Paint stroke",
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


def _public_stroke(view_area, view_region, stroke) -> tuple[set[str], float]:
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
    return result, time.perf_counter() - started


def _finish(error: BaseException | None = None) -> None:
    details = dict(STATE.get("details", {}))
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if error is None:
        RESULT_PATH.write_text("PASS", encoding="utf-8")
        details["status"] = "PASS"
    else:
        formatted = "".join(traceback.format_exception(error))
        RESULT_PATH.write_text(formatted, encoding="utf-8")
        details.update(status="FAIL", error=formatted)
    DETAIL_PATH.write_text(json.dumps(details, indent=2), encoding="utf-8")
    try:
        from quick_sdf_blender import studio

        project = STATE.get("project")
        if project is not None:
            studio.discard_provisional(bpy.context, project)
        if studio.current_session() is not None:
            bpy.ops.quicksdf.studio_exit()
    except Exception:
        pass
    try:
        bpy.ops.preferences.addon_disable(module="quick_sdf_blender")
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def _check() -> float | None:
    try:
        from quick_sdf_blender import runtime, studio

        project = STATE["project"]
        obj = STATE["object"]
        session = studio.current_session()
        if session is None:
            STATE["waits"] = int(STATE.get("waits", 0)) + 1
            if int(STATE["waits"]) <= 200:
                return 0.05
            raise AssertionError("Quick SDF Studio did not finish opening")

        view_area = next(
            area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D"
        )
        view_region = next(
            region for region in view_area.regions if region.type == "WINDOW"
        )
        view_space = view_area.spaces.active
        phase = str(STATE["phase"])

        if phase == "WAIT_STUDIO":
            assert obj.mode == "TEXTURE_PAINT"
            assert len(_lane(project)) == 8
            assert int(project.material_slot_index) == 1
            originals = STATE["original_materials"]
            assert obj.material_slots[0].material == originals[0]
            assert obj.material_slots[2].material == originals[2]
            assert obj.material_slots[1].material != originals[1]
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
            STATE["phase"] = "PREPARE_KEY"
            return 0.5

        if phase == "PREPARE_KEY":
            project.preview_mode = "MASK"
            project.paint_value = 1
            settled = studio.settle_seek(bpy.context, project, 71.0)
            assert settled == 71.0
            assert len(_lane(project)) == 8
            assert session.provisional_state in {"PREPARING", "READY"}
            STATE.update(phase="WAIT_READY", ready_waits=0)
            return 0.05

        if phase == "WAIT_READY":
            if session.provisional_state == "PREPARING":
                STATE["ready_waits"] = int(STATE.get("ready_waits", 0)) + 1
                if int(STATE["ready_waits"]) <= 240:
                    return 0.05
                raise AssertionError("Timed out preparing the 71 degree adaptive key")
            assert session.provisional_state == "READY", (
                session.provisional_state,
                session.projection_hint,
            )
            image = bpy.data.images.get(str(session.provisional_image_name))
            assert image is not None
            before = runtime.image_gray8(image)
            assert np.any(before != 0), "The READY provisional canvas is already black"
            assert bpy.context.scene.tool_settings.image_paint.canvas == image
            center = view3d_utils.location_3d_to_region_2d(
                view_region,
                view_space.region_3d,
                obj.matrix_world.translation,
            )
            assert center is not None
            x, y = float(center.x), float(center.y)
            assert 32.0 < x < view_region.width - 32.0
            assert 32.0 < y < view_region.height - 32.0
            brush = bpy.context.scene.tool_settings.image_paint.brush
            assert brush is not None
            brush.size = 72
            brush.strength = 1.0
            brush.blend = "MIX"
            STATE.update(
                provisional_uuid=str(session.provisional_uuid),
                provisional_name=image.name,
                provisional_pointer=int(image.as_pointer()),
                provisional_before=before,
                provisional_base_blob=bytes(session.provisional_base_blob),
                provisional_coverage_blob=bytes(session.provisional_coverage_blob),
                stroke=_stroke_points(x, y),
            )

            # White over the all-white pending key is a real native no-op. It
            # must roll back the temporary collection insertion without
            # destroying or replacing the prepared Canvas.
            result, elapsed = _public_stroke(
                view_area, view_region, STATE["stroke"]
            )
            assert result == {"FINISHED"}, result
            assert len(_lane(project)) == 8
            assert session.provisional_state == "READY"
            assert str(session.provisional_uuid) == STATE["provisional_uuid"]
            image_after_noop = bpy.data.images.get(str(session.provisional_image_name))
            assert image_after_noop is not None
            assert image_after_noop.name == STATE["provisional_name"]
            assert int(image_after_noop.as_pointer()) == STATE["provisional_pointer"]
            np.testing.assert_array_equal(
                runtime.image_gray8(image_after_noop), STATE["provisional_before"]
            )
            assert bpy.context.scene.tool_settings.image_paint.canvas == image_after_noop

            # The second native stroke crosses white to Shadow and therefore
            # must atomically promote exactly this provisional key.
            project.paint_value = 0
            result, paint_elapsed = _public_stroke(
                view_area, view_region, STATE["stroke"]
            )
            assert result == {"FINISHED"}, result
            assert len(_lane(project)) == 9
            assert session.provisional_state == "NONE"
            created = next(
                item
                for item in _lane(project)
                if str(item.uuid) == STATE["provisional_uuid"]
            )
            assert float(created.angle) == 71.0
            created_image = runtime.resolve_display_image(project, created)
            assert created_image is not None
            assert created_image.name == STATE["provisional_name"]
            assert int(created_image.as_pointer()) == STATE["provisional_pointer"]
            assert runtime.bitplane_blob(created, "BASE") == STATE["provisional_base_blob"]
            after = runtime.image_gray8(created_image)
            before = STATE["provisional_before"]
            changed = after != before
            changed_count = int(np.count_nonzero(changed))
            print(
                "ADAPTIVE_PROJECTION_DIAGNOSTIC",
                "before", int(before.min()), int(before.max()),
                "after", int(after.min()), int(after.max()),
                "coverage", int(np.count_nonzero(runtime.coverage_mask(created))),
                "changed", changed_count,
            )
            assert changed_count > 0
            assert changed_count < after.size // 2, (
                "Projection Paint changed an implausibly large part of the canvas",
                changed_count,
                after.size,
            )
            assert np.any(after != 0), "The adaptive canvas became entirely black"
            assert np.all(after[changed] < before[changed])
            coverage = runtime.coverage_mask(created)
            assert np.all(coverage[changed])
            assert np.count_nonzero(coverage) < coverage.size // 2
            assert bpy.context.scene.tool_settings.image_paint.canvas == created_image
            assert obj.material_slots[0].material == STATE["original_materials"][0]
            assert obj.material_slots[2].material == STATE["original_materials"][2]

            created_uuid = str(created.uuid)
            created_base_blob = runtime.bitplane_blob(created, "BASE")
            created_coverage_blob = runtime.bitplane_blob(created, "COVERAGE")
            created_after = after.copy()
            created_pointer = int(created_image.as_pointer())
            assert bpy.ops.quicksdf.history_undo() == {"FINISHED"}
            assert len(_lane(project)) == 8
            assert not any(str(item.uuid) == created_uuid for item in project.angles)
            assert bpy.data.images.get(created_image.name) == created_image

            assert bpy.ops.quicksdf.history_redo() == {"FINISHED"}
            assert len(_lane(project)) == 9
            restored = next(
                item for item in project.angles if str(item.uuid) == created_uuid
            )
            restored_image = runtime.resolve_display_image(project, restored)
            assert restored_image is not None
            assert int(restored_image.as_pointer()) == created_pointer
            assert str(restored.uuid) == created_uuid
            assert float(restored.angle) == 71.0
            assert runtime.bitplane_blob(restored, "BASE") == created_base_blob
            assert runtime.bitplane_blob(restored, "COVERAGE") == created_coverage_blob
            np.testing.assert_array_equal(
                runtime.image_gray8(restored_image), created_after
            )

            STATE["details"].update(
                blender=bpy.app.version_string,
                object=obj.name,
                polygons=len(obj.data.polygons),
                material_slots=len(obj.material_slots),
                angle=float(restored.angle),
                changed_pixels=changed_count,
                no_op_seconds=elapsed,
                paint_seconds=paint_elapsed,
                image_name=restored_image.name,
                uuid=created_uuid,
            )
            print("ADAPTIVE_PROJECTION_CHANGED_PIXELS", changed_count)
            print("ADAPTIVE_PROJECTION_SECONDS", f"{paint_elapsed:.4f}")
            print("[Quick SDF adaptive projection smoke] PASS")
            _finish()
            return None

        raise AssertionError(f"Unknown adaptive projection smoke phase: {phase}")
    except Exception as error:
        _finish(error)
        return None


def _run() -> None:
    try:
        RESULT_PATH.unlink(missing_ok=True)
        DETAIL_PATH.unlink(missing_ok=True)
        assert not bpy.app.background
        assert bpy.app.version[:2] == (5, 1), bpy.app.version_string
        assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {
            "FINISHED"
        }
        from quick_sdf_blender import runtime

        obj, materials = _make_multislot_uv_sphere()
        scene = bpy.context.scene
        scene.quick_sdf_settings.resolution = 512
        scene.quick_sdf_settings.initialization = "WHITE"
        assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
        project = runtime.active_project(scene)
        assert project is not None
        project.symmetry_mode = "TEXTURE_MIRROR"
        project.mirror_enabled = True
        project.authoring_side = "RIGHT"
        project.active_side = "RIGHT"
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        STATE.update(
            object=obj,
            project=project,
            original_materials=materials,
        )
        bpy.app.timers.register(_check, first_interval=0.1)
    except Exception as error:
        _finish(error)


bpy.app.timers.register(_run, first_interval=0.25)

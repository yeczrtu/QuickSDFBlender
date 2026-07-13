"""Interactive-window smoke for the Quick SDF Studio workspace transaction."""

from __future__ import annotations

from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402


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

        if STATE.get("phase") == "EXPORTING":
            project = STATE["project"]
            export_path = STATE["export_path"]
            STATE["export_attempts"] = STATE.get("export_attempts", 0) + 1
            if bool(project.job_running) or str(project.output_path) != str(export_path):
                if STATE["export_attempts"] > 300:
                    raise AssertionError(project.job_message or project.diagnostic_message)
                return 0.1
            assert export_path.is_file()
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
            "VIEW_3D", "IMAGE_EDITOR", "DOPESHEET_EDITOR"
        }
        assert obj.mode == "TEXTURE_PAINT"
        assert not project.base_needs_update
        canvas = runtime.resolve_angle_image(
            project, runtime.active_angle(project)
        )
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
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
        assert project.onion_enabled
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        assert image_area.spaces.active.image.get(runtime.ROLE_KEY) == ONION_PREVIEW_ROLE
        assert bpy.ops.quicksdf.seek_set(angle=22.5) == {"FINISHED"}
        assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
        from quick_sdf_blender.live_preview import SEEK_PREVIEW_ROLE

        preview_material = obj.material_slots[0].material
        preview_image = preview_material.node_tree.nodes["QSDF Mask"].image
        assert preview_image.get(runtime.ROLE_KEY) == SEEK_PREVIEW_ROLE
        export_path = ROOT / "build" / "studio_async_export.png"
        export_path.unlink(missing_ok=True)
        assert bpy.ops.quicksdf.export_texture(
            filepath=str(export_path), overwrite=True
        ) == {"FINISHED"}
        assert project.job_running
        STATE["export_path"] = export_path
        STATE["phase"] = "EXPORTING"
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

"""Blender 5.1/5.2 regression smoke for adaptive angle-key authoring.

Run with a real Blender window because Studio sessions and Texture Paint
canvases do not exist in background mode::

    blender.exe --factory-startup --python-exit-code 1 \
        --python tests/blender_auto_key_smoke.py

The smoke exercises the native paint wrapper without synthesizing mouse input:
it snapshots the provisional Canvas, changes one 8-bit texel as a real stroke
would, then runs the normal post-stroke operator.
"""

from __future__ import annotations

from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "build" / "auto_key_smoke_result.txt"
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402


STATE: dict[str, object] = {}


def _mesh() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = "Quick SDF Auto Key Cube"
    material = bpy.data.materials.new("Quick SDF Auto Key Material")
    obj.data.materials.append(material)
    uv = obj.data.uv_layers.active or obj.data.uv_layers.new(name="QuickSDFAutoKeyUV")
    uv.name = "QuickSDFAutoKeyUV"
    obj.data.uv_layers.active = uv
    return obj


def _lane(project, side: str):
    return sorted(
        (item for item in project.angles if str(item.side) == side),
        key=lambda item: float(item.angle),
    )


def _persistent_display_names(project, runtime) -> set[str]:
    project_uuid = str(project.uuid)
    return {
        image.name
        for image in bpy.data.images
        if str(image.get(runtime.PROJECT_UUID_KEY, "")) == project_uuid
        and str(image.get(runtime.ROLE_KEY, "")) == runtime.DISPLAY_ROLE
    }


def _provisional_images(project, runtime, studio) -> list[bpy.types.Image]:
    project_uuid = str(project.uuid)
    return [
        image
        for image in bpy.data.images
        if str(image.get(runtime.PROJECT_UUID_KEY, "")) == project_uuid
        and str(image.get(runtime.ROLE_KEY, "")) == studio.PROVISIONAL_DISPLAY_ROLE
    ]


def _select_side(project, side: str, studio) -> None:
    candidates = _lane(project, side)
    assert candidates, side
    item = min(candidates, key=lambda value: abs(float(value.angle) - 45.0))
    assert studio.select_paint_key(
        bpy.context, project, key_uuid=str(item.uuid)
    )
    assert str(project.active_side) == side


def _start_prepare(project, studio, angle: float, action: str) -> None:
    settled = studio.settle_seek(bpy.context, project, angle)
    session = studio.current_session()
    assert session is not None
    assert settled == round(angle), (settled, angle)
    assert float(session.provisional_angle) == round(angle)
    assert session.provisional_state in {"PREPARING", "READY"}
    STATE["phase"] = "WAIT_PROVISIONAL"
    STATE["action"] = action
    STATE["attempts"] = 0


def _paint_one_texel(project, runtime, seed: int) -> None:
    canvas = bpy.context.scene.tool_settings.image_paint.canvas
    assert canvas is not None
    rgba = runtime.image_rgba8(canvas)
    y = (seed * 11 + 7) % rgba.shape[0]
    x = (seed * 17 + 5) % rgba.shape[1]
    value = 0 if int(rgba[y, x, 0]) >= 128 else 255
    rgba[y, x, :3] = value
    rgba[y, x, 3] = 255
    runtime.write_image_rgba8(canvas, rgba)
    assert bpy.ops.quicksdf.propagate_overrides() == {"FINISHED"}


def _promote_ready(project, runtime, studio, seed: int) -> None:
    session = studio.current_session()
    assert session is not None and session.provisional_state == "READY"
    expected_uuid = str(session.provisional_uuid)
    expected_angle = float(session.provisional_angle)
    expected_side = str(session.provisional_side)
    before_count = len(_lane(project, expected_side))
    canvas = bpy.context.scene.tool_settings.image_paint.canvas
    before_snapshot = runtime.image_gray8(canvas)
    assert bpy.ops.quicksdf.paint_snapshot() == {"FINISHED"}
    assert session.provisional_promoting
    assert len(_lane(project, expected_side)) == before_count
    assert canvas.packed_file is not None
    np.testing.assert_array_equal(runtime.image_gray8(canvas), before_snapshot)
    _paint_one_texel(project, runtime, seed)
    assert not session.provisional_promoting
    assert session.provisional_state == "NONE"
    created = [item for item in _lane(project, expected_side) if str(item.uuid) == expected_uuid]
    assert len(created) == 1
    assert float(created[0].angle) == expected_angle
    return created[0]


def finish(error: BaseException | None = None) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if error is not None:
        RESULT_PATH.write_text(
            "".join(traceback.format_exception(error)), encoding="utf-8"
        )
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


def check() -> float | None:
    try:
        from quick_sdf_blender import runtime, studio
        from quick_sdf_blender.model import MAX_KEYS_PER_SIDE

        project = STATE["project"]
        session = studio.current_session()
        if session is None:
            STATE["open_attempts"] = int(STATE.get("open_attempts", 0)) + 1
            if int(STATE["open_attempts"]) < 100:
                return 0.05
            raise AssertionError("Studio did not finish opening")
        if not bool(STATE.get("studio_settled", False)):
            # Texture Paint Brush Assets and the preview material finish their
            # first UI/GPU initialization after the session becomes visible.
            STATE["studio_settled"] = True
            return 2.0

        phase = str(STATE.get("phase", "INITIAL"))
        if phase == "INITIAL":
            assert len(_lane(project, "RIGHT")) == 8
            assert len(_lane(project, "LEFT")) == 0
            initial_uuids = tuple(str(item.uuid) for item in project.angles)
            initial_images = _persistent_display_names(project, runtime)

            # 14.49 quantizes to 14 degrees, which lies within the two-degree
            # snap radius of the 12.857-degree default key.
            nearest = min(
                _lane(project, "RIGHT"),
                key=lambda item: abs(float(item.angle) - 14.0),
            )
            settled = studio.settle_seek(bpy.context, project, 14.49)
            assert settled == float(nearest.angle)
            assert session.provisional_state == "NONE"
            assert tuple(str(item.uuid) for item in project.angles) == initial_uuids
            assert _persistent_display_names(project, runtime) == initial_images

            STATE["initial_uuids"] = initial_uuids
            STATE["initial_images"] = initial_images
            # 15.49 quantizes to 15; it is just outside the same snap radius.
            _start_prepare(project, studio, 15.49, "NO_OP")
            return 0.05

        if phase == "WAIT_PROVISIONAL":
            STATE["attempts"] = int(STATE.get("attempts", 0)) + 1
            if session.provisional_state == "PREPARING":
                if int(STATE["attempts"]) > 200:
                    raise AssertionError("Timed out preparing an adaptive angle key")
                return 0.05
            assert session.provisional_state == "READY", (
                session.provisional_state,
                session.projection_hint,
            )
            action = str(STATE["action"])

            if action == "NO_OP":
                # Seek/preparation itself may own one transient Canvas, but it
                # must not create a persistent angle or DISPLAY image.
                assert tuple(str(item.uuid) for item in project.angles) == STATE["initial_uuids"]
                assert _persistent_display_names(project, runtime) == STATE["initial_images"]
                assert len(_provisional_images(project, runtime, studio)) == 1

                provisional_canvas = bpy.context.scene.tool_settings.image_paint.canvas
                before_snapshot = runtime.image_gray8(provisional_canvas)
                assert bpy.ops.quicksdf.paint_snapshot() == {"FINISHED"}
                assert len(project.angles) == 8
                assert provisional_canvas.packed_file is not None
                np.testing.assert_array_equal(
                    runtime.image_gray8(provisional_canvas), before_snapshot
                )
                assert bpy.ops.quicksdf.propagate_overrides() == {"FINISHED"}
                assert len(project.angles) == 8
                assert session.provisional_state == "READY"
                assert not session.provisional_promoting
                assert _persistent_display_names(project, runtime) == STATE["initial_images"]

                # The same prepared Canvas becomes persistent only after an
                # actual 8-bit display change.
                created = _promote_ready(project, runtime, studio, 15)
                assert len(_lane(project, "RIGHT")) == 9
                assert len(_lane(project, "LEFT")) == 0
                assert any(float(item.angle) == 15.0 for item in _lane(project, "RIGHT"))
                assert len(_persistent_display_names(project, runtime)) == 9

                # The first stroke and the structural key promotion are one
                # Quick SDF action. Undo detaches the key while retaining its
                # orphan Canvas; Redo reattaches the same UUID and bitplanes.
                from quick_sdf_blender import operators

                created_uuid = str(created.uuid)
                created_image = runtime.resolve_display_image(project, created)
                assert created_image is not None
                display_after = runtime.image_gray8(created_image)
                base_after = runtime.base_mask(created).copy()
                coverage_after = runtime.coverage_mask(created).copy()
                history = operators._HISTORIES[str(project.uuid)]
                metadata = history.undo_metadata
                assert metadata is not None
                assert metadata["created_key"]["uuid"] == created_uuid
                assert bpy.ops.quicksdf.history_undo() == {"FINISHED"}
                assert not any(str(item.uuid) == created_uuid for item in project.angles)
                assert str(created_image.get(runtime.ROLE_KEY, "")) == "history_orphan_display"
                assert bpy.ops.quicksdf.history_redo() == {"FINISHED"}
                restored = next(item for item in project.angles if str(item.uuid) == created_uuid)
                assert runtime.resolve_display_image(project, restored) == created_image
                assert str(created_image.get(runtime.ROLE_KEY, "")) == runtime.DISPLAY_ROLE
                np.testing.assert_array_equal(runtime.image_gray8(created_image), display_after)
                np.testing.assert_array_equal(runtime.base_mask(restored), base_after)
                np.testing.assert_array_equal(runtime.coverage_mask(restored), coverage_after)
                for item in project.angles:
                    try:
                        runtime.base_mask(item)
                        runtime.coverage_mask(item)
                    except Exception as error:
                        raise AssertionError(
                            f"Missing bitplane at {item.side} {item.angle}: "
                            f"{tuple(item.keys())}"
                        ) from error

                # A linked mirror stores only its source lane. Breaking it
                # materializes both lanes, after which auto-keys target only
                # the active side.
                assert bpy.ops.quicksdf.break_mirror() == {"FINISHED"}
                assert str(project.symmetry_mode) == "INDEPENDENT"
                assert not project.mirror_enabled
                assert len(_lane(project, "RIGHT")) == 9
                assert len(_lane(project, "LEFT")) == 9
                _select_side(project, "LEFT", studio)
                _start_prepare(project, studio, 34.4, "INDEPENDENT_LEFT")
                return 0.05

            if action == "INDEPENDENT_LEFT":
                right_before = len(_lane(project, "RIGHT"))
                _promote_ready(project, runtime, studio, 34)
                assert len(_lane(project, "LEFT")) == 10
                assert len(_lane(project, "RIGHT")) == right_before == 9
                _select_side(project, "RIGHT", studio)
                STATE["cap_targets"] = [5.0, 8.0, 19.0, 22.0, 31.0, 34.0, 44.0]
                target = STATE["cap_targets"].pop(0)
                _start_prepare(project, studio, target, "FILL_TO_LIMIT")
                return 0.05

            if action == "FILL_TO_LIMIT":
                assert session.provisional_side == "RIGHT", (
                    session.provisional_side,
                    project.active_side,
                    session.provisional_angle,
                )
                before = len(_lane(project, "RIGHT"))
                _promote_ready(project, runtime, studio, int(session.provisional_angle))
                after = len(_lane(project, "RIGHT"))
                assert str(project.active_side) == "RIGHT"
                assert after == before + 1, (
                    before,
                    after,
                    tuple(float(item.angle) for item in _lane(project, "RIGHT")),
                )
                assert len(_lane(project, "LEFT")) == 10
                targets = STATE["cap_targets"]
                if targets:
                    target = targets.pop(0)
                    _start_prepare(project, studio, target, "FILL_TO_LIMIT")
                    return 0.05
                assert before == 15 and after == MAX_KEYS_PER_SIDE == 16
                persistent_before = _persistent_display_names(project, runtime)
                result = studio.settle_seek(bpy.context, project, 57.4)
                assert result == 57.0
                assert session.provisional_state == "LIMIT"
                assert len(_lane(project, "RIGHT")) == MAX_KEYS_PER_SIDE
                assert _persistent_display_names(project, runtime) == persistent_before
                try:
                    studio.activate_provisional_for_stroke(bpy.context, project)
                except studio.StudioError as error:
                    assert "Maximum 16 keys" in str(error)
                else:
                    raise AssertionError("A seventeenth side key was unexpectedly activated")
                assert len(_lane(project, "RIGHT")) == MAX_KEYS_PER_SIDE
                assert len(_lane(project, "LEFT")) == 10
                studio.discard_provisional(bpy.context, project)
                assert session.provisional_state == "NONE"
                RESULT_PATH.write_text("PASS", encoding="utf-8")
                finish()
                return None

            raise AssertionError(f"Unknown auto-key smoke action: {action}")

        raise AssertionError(f"Unknown auto-key smoke phase: {phase}")
    except Exception as error:
        finish(error)
    return None


def run() -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.unlink(missing_ok=True)
    try:
        assert not bpy.app.background
        assert (5, 1) <= bpy.app.version[:2] < (5, 3), bpy.app.version_string
        assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {"FINISHED"}
        from quick_sdf_blender import runtime

        _mesh()
        settings = bpy.context.scene.quick_sdf_settings
        settings.resolution = 512
        settings.initialization = "WHITE"
        assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
        project = runtime.active_project()
        project.symmetry_mode = "TEXTURE_MIRROR"
        project.mirror_enabled = True
        project.authoring_side = "RIGHT"
        project.active_side = "RIGHT"
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        STATE.update(project=project, phase="INITIAL", open_attempts=0)
        bpy.app.timers.register(check, first_interval=0.1)
    except Exception as error:
        finish(error)


bpy.app.timers.register(run, first_interval=0.25)

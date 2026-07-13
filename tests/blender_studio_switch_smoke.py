"""Interactive regression smoke for one-click Quick SDF Studio switching.

Run with a real Blender window because workspace duplication/splitting and
Texture Paint are unavailable in background mode::

    blender.exe --factory-startup --python-exit-code 1 \
        --python tests/blender_studio_switch_smoke.py

The process writes ``build/studio_switch_smoke_result.txt`` and exits by
itself.  This is intentionally an acceptance test for the public operators;
it must not call a private switch helper that an artist cannot reach.
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "build" / "studio_switch_smoke_result.txt"
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402


STATE: dict[str, object] = {}


def _add_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    material = bpy.data.materials.new(f"{name} Material")
    obj.data.materials.append(material)
    uv = obj.data.uv_layers.active or obj.data.uv_layers.new(name=f"{name}UV")
    uv.name = f"{name}UV"
    obj.data.uv_layers.active = uv
    return obj


def _window_region(area: bpy.types.Area):
    return next(region for region in area.regions if region.type == "WINDOW")


def _set_object_mode() -> None:
    obj = bpy.context.view_layer.objects.active
    if obj is None or obj.mode == "OBJECT":
        return
    area = next(area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D")
    with bpy.context.temp_override(
        window=bpy.context.window,
        screen=bpy.context.window.screen,
        area=area,
        region=_window_region(area),
    ):
        result = bpy.ops.object.mode_set(mode="OBJECT")
    assert result == {"FINISHED"}, result


def _select_only(obj: bpy.types.Object) -> None:
    _set_object_mode()
    for candidate in bpy.context.view_layer.objects:
        candidate.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _project_for_object(obj: bpy.types.Object):
    matches = [
        project
        for project in bpy.context.scene.quick_sdf_projects
        if project.target_object == obj
    ]
    assert len(matches) == 1, (obj.name, len(matches))
    return matches[0]


def _canvas_fingerprint(image, runtime) -> tuple[str, int, str]:
    assert image is not None
    return (
        image.name,
        int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
        hashlib.sha256(runtime.image_rgba(image).tobytes()).hexdigest(),
    )


def _studio_image():
    area = next(area for area in bpy.context.window.screen.areas if area.type == "IMAGE_EDITOR")
    return area.spaces.active.image


def _assert_project_is_fully_active(project, obj, runtime, studio) -> None:
    session = studio.current_session()
    assert session is not None
    assert session.project_uuid == str(project.uuid)
    assert studio.is_studio_active(bpy.context, str(project.uuid))
    assert bpy.context.window.workspace.get(studio.WORKSPACE_PROJECT_TAG) == str(project.uuid)
    assert runtime.active_project(bpy.context.scene) == project
    assert bpy.context.view_layer.objects.active == obj
    assert obj.mode == "TEXTURE_PAINT"
    expected = runtime.resolve_angle_image(project, runtime.active_angle(project))
    assert expected is not None
    assert bpy.context.scene.tool_settings.image_paint.canvas == expected
    assert _studio_image() == expected


def _stage_open_from_original_workspace(obj, operator_name: str, wait_phase: str) -> float:
    """Model workspace selection and button click as separate UI events."""

    bpy.context.window.workspace = STATE["original_workspace"]
    _select_only(obj)
    STATE["request_operator"] = operator_name
    STATE["request_wait_phase"] = wait_phase
    STATE["phase"] = "INVOKE_REQUEST"
    return 0.1


def _project_is_fully_active(project, obj, runtime, studio) -> bool:
    session = studio.current_session()
    if session is None or session.project_uuid != str(project.uuid):
        return False
    if not studio.is_studio_active(bpy.context, str(project.uuid)):
        return False
    if bpy.context.view_layer.objects.active != obj or obj.mode != "TEXTURE_PAINT":
        return False
    expected = runtime.resolve_angle_image(project, runtime.active_angle(project))
    if expected is None:
        return False
    if bpy.context.scene.tool_settings.image_paint.canvas != expected:
        return False
    try:
        return _studio_image() == expected
    except StopIteration:
        return False


def _wait_for_project(project, obj, next_phase: str, runtime, studio) -> float:
    if _project_is_fully_active(project, obj, runtime, studio):
        STATE["attempts"] = 0
        STATE["phase"] = next_phase
        return 0.15
    STATE["attempts"] = int(STATE.get("attempts", 0)) + 1
    if int(STATE["attempts"]) > 160:
        session = studio.current_session()
        active_uuid = str(getattr(session, "project_uuid", ""))
        raise AssertionError(
            f"Studio did not switch to {project.target_object.name}; active={active_uuid}, "
            f"warning={project.warning_message!r}, diagnostic={project.diagnostic_message!r}"
        )
    return 0.05


def finish(error: BaseException | None = None) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if error is None:
        RESULT_PATH.write_text("PASS", encoding="utf-8")
    else:
        RESULT_PATH.write_text("".join(traceback.format_exception(error)), encoding="utf-8")
    try:
        bpy.ops.preferences.addon_disable(module="quick_sdf_blender")
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def check() -> float | None:
    try:
        from quick_sdf_blender import runtime, studio

        phase = str(STATE["phase"])
        if phase == "INVOKE_REQUEST":
            assert bpy.context.window.workspace == STATE["original_workspace"]
            operator_name = str(STATE["request_operator"])
            if bool(STATE.pop("inject_switch_failure", False)):
                from quick_sdf_blender import tools

                original_activate_tools = tools.activate_tools
                calls = {"count": 0}

                def fail_once(*args, **kwargs):
                    calls["count"] += 1
                    if calls["count"] == 1:
                        raise RuntimeError("injected target activation failure")
                    return original_activate_tools(*args, **kwargs)

                STATE["original_activate_tools"] = original_activate_tools
                tools.activate_tools = fail_once
            if operator_name == "create_and_edit":
                result = bpy.ops.quicksdf.create_and_edit()
                assert result == {"FINISHED"}, result
                STATE["project_b"] = _project_for_object(STATE["obj_b"])
            else:
                assert operator_name == "studio_enter"
                result = bpy.ops.quicksdf.studio_enter()
                assert result == {"FINISHED"}, result
            STATE["phase"] = STATE["request_wait_phase"]
            STATE["attempts"] = 0
            return 0.1

        if phase == "WAIT_A":
            return _wait_for_project(
                STATE["project_a"], STATE["obj_a"], "REFOCUS_A", runtime, studio
            )

        if phase == "REFOCUS_A":
            project_a = STATE["project_a"]
            obj_a = STATE["obj_a"]
            _assert_project_is_fully_active(project_a, obj_a, runtime, studio)
            STATE["studio_workspace"] = bpy.context.window.workspace

            # Merely clicking another Workspace must not turn Open Studio into
            # a misleading "another project" error for the same project.
            return _stage_open_from_original_workspace(
                obj_a, "studio_enter", "WAIT_REFOCUS_A"
            )

        if phase == "WAIT_REFOCUS_A":
            project_a = STATE["project_a"]
            obj_a = STATE["obj_a"]
            if not _project_is_fully_active(project_a, obj_a, runtime, studio):
                return _wait_for_project(
                    project_a, obj_a, "WAIT_REFOCUS_A", runtime, studio
                )
            assert bpy.context.window.workspace == STATE["studio_workspace"]
            _assert_project_is_fully_active(project_a, obj_a, runtime, studio)

            canvas_a = bpy.context.scene.tool_settings.image_paint.canvas
            STATE["canvas_a"] = canvas_a
            STATE["canvas_a_fingerprint"] = _canvas_fingerprint(canvas_a, runtime)

            # B has no project yet. Create & Edit must create it and switch in
            # one click without mutating or deleting A's current paint canvas.
            return _stage_open_from_original_workspace(
                STATE["obj_b"], "create_and_edit", "WAIT_NEW_B"
            )

        if phase == "WAIT_NEW_B":
            project_b = STATE["project_b"]
            if not _project_is_fully_active(project_b, STATE["obj_b"], runtime, studio):
                return _wait_for_project(
                    project_b, STATE["obj_b"], "WAIT_NEW_B", runtime, studio
                )
            _assert_project_is_fully_active(project_b, STATE["obj_b"], runtime, studio)
            assert _canvas_fingerprint(STATE["canvas_a"], runtime) == STATE["canvas_a_fingerprint"]
            assert STATE["obj_a"].material_slots[0].material == STATE["material_a"]
            assert STATE["obj_b"].material_slots[0].material != STATE["material_b"]
            assert bpy.context.window.workspace == STATE["studio_workspace"]

            # B is now an existing project. Exercise both directions through
            # the same public Open operator, not a test-only switch helper.
            return _stage_open_from_original_workspace(
                STATE["obj_a"], "studio_enter", "WAIT_EXISTING_A"
            )

        if phase == "WAIT_EXISTING_A":
            project_a = STATE["project_a"]
            if not _project_is_fully_active(project_a, STATE["obj_a"], runtime, studio):
                return _wait_for_project(
                    project_a, STATE["obj_a"], "WAIT_EXISTING_A", runtime, studio
                )
            _assert_project_is_fully_active(project_a, STATE["obj_a"], runtime, studio)
            assert STATE["obj_b"].material_slots[0].material == STATE["material_b"]
            # A failure after B has entered Texture Paint must roll back to A,
            # not leave a half-switched session or demand a manual exit.
            STATE["inject_switch_failure"] = True
            return _stage_open_from_original_workspace(
                STATE["obj_b"], "studio_enter", "WAIT_ROLLBACK_A"
            )

        if phase == "WAIT_ROLLBACK_A":
            project_a = STATE["project_a"]
            project_b = STATE["project_b"]
            if not _project_is_fully_active(project_a, STATE["obj_a"], runtime, studio):
                return _wait_for_project(
                    project_a, STATE["obj_a"], "WAIT_ROLLBACK_A", runtime, studio
                )
            from quick_sdf_blender import tools

            tools.activate_tools = STATE.pop("original_activate_tools")
            assert "injected target activation failure" in str(project_b.diagnostic_message)
            assert STATE["obj_b"].material_slots[0].material == STATE["material_b"]
            assert _canvas_fingerprint(STATE["canvas_a"], runtime) == STATE["canvas_a_fingerprint"]
            project_b.diagnostic_message = ""
            project_b.warning_message = ""
            return _stage_open_from_original_workspace(
                STATE["obj_b"], "studio_enter", "WAIT_EXISTING_B"
            )

        if phase == "WAIT_EXISTING_B":
            project_b = STATE["project_b"]
            if not _project_is_fully_active(project_b, STATE["obj_b"], runtime, studio):
                return _wait_for_project(
                    project_b, STATE["obj_b"], "WAIT_EXISTING_B", runtime, studio
                )
            _assert_project_is_fully_active(project_b, STATE["obj_b"], runtime, studio)
            assert STATE["obj_a"].material_slots[0].material == STATE["material_a"]
            assert bpy.context.window.workspace == STATE["studio_workspace"]
            assert bpy.ops.quicksdf.studio_exit() == {"FINISHED"}
            STATE["phase"] = "EXITED"
            return 0.1

        if phase == "EXITED":
            assert studio.current_session() is None
            assert bpy.context.window.workspace == STATE["original_workspace"]
            assert bpy.context.scene.tool_settings.image_paint.canvas == STATE["original_canvas"]
            assert bpy.context.scene.tool_settings.image_paint.mode == STATE["original_paint_mode"]
            assert bpy.context.view_layer.objects.active == STATE["obj_a"]
            assert STATE["obj_a"].mode == "OBJECT"
            assert STATE["obj_a"].material_slots[0].material == STATE["material_a"]
            assert STATE["obj_b"].material_slots[0].material == STATE["material_b"]
            assert _canvas_fingerprint(STATE["canvas_a"], runtime) == STATE["canvas_a_fingerprint"]
            finish()
            return None

        raise AssertionError(f"Unknown smoke-test phase: {phase}")
    except Exception as error:
        finish(error)
        return None


def run() -> None:
    try:
        assert not bpy.app.background
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.unlink(missing_ok=True)
        assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {"FINISHED"}
        from quick_sdf_blender import runtime

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        obj_a = _add_cube("Switch Smoke A", (-1.5, 0.0, 0.0))
        obj_b = _add_cube("Switch Smoke B", (1.5, 0.0, 0.0))
        _select_only(obj_a)
        original_workspace = bpy.context.window.workspace
        material_a = obj_a.material_slots[0].material
        material_b = obj_b.material_slots[0].material
        bpy.context.scene.quick_sdf_settings.resolution = 512
        bpy.context.scene.quick_sdf_settings.initialization = "NORMAL_SWEEP"
        original_canvas = bpy.context.scene.tool_settings.image_paint.canvas
        original_paint_mode = bpy.context.scene.tool_settings.image_paint.mode
        assert bpy.ops.quicksdf.create_and_edit() == {"FINISHED"}
        project_a = _project_for_object(obj_a)
        assert project_a.target_object == obj_a
        STATE.update(
            phase="WAIT_A",
            obj_a=obj_a,
            obj_b=obj_b,
            project_a=project_a,
            original_workspace=original_workspace,
            original_canvas=original_canvas,
            original_paint_mode=original_paint_mode,
            material_a=material_a,
            material_b=material_b,
        )
        bpy.app.timers.register(check, first_interval=0.1)
    except Exception as error:
        finish(error)


bpy.app.timers.register(run, first_interval=0.25)

"""Interactive smoke test for the isolated Quick SDF timeline event surface."""

from __future__ import annotations

from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402


RESULT_PATH = ROOT / "build" / "timeline_isolation_smoke_result.txt"
SAVE_PATH = ROOT / "build" / "timeline_isolation_smoke.blend"
STATE: dict[str, object] = {}


def _mesh() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    material = bpy.data.materials.new("Timeline Isolation Material")
    material.use_nodes = True
    obj.data.materials.append(material)
    uv = obj.data.uv_layers.new(name="TimelineIsolationUV")
    square = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for polygon in obj.data.polygons:
        for corner, loop in enumerate(polygon.loop_indices):
            uv.data[loop].uv = square[corner % 4]
    obj.data.uv_layers.active = uv
    return obj


def _tagged_hosts(tag: str) -> list[bpy.types.NodeTree]:
    return [tree for tree in bpy.data.node_groups if bool(tree.get(tag, False))]


def _timeline_context():
    from quick_sdf_blender import studio

    area = next(
        area
        for area in bpy.context.window.screen.areas
        if area.type == studio.TIMELINE_SPACE_TYPE
    )
    region = next(region for region in area.regions if region.type == "WINDOW")
    return area, region


def _assert_isolated_host(project, original_tree):
    from quick_sdf_blender import studio

    area, _region = _timeline_context()
    space = area.spaces.active
    host = space.node_tree
    preview_material = project.target_object.material_slots[
        project.material_slot_index
    ].material
    assert host is not None
    assert bool(space.pin)
    assert host.get(studio.TIMELINE_HOST_TAG) == str(project.uuid)
    assert len(host.nodes) == 0
    assert host != original_tree
    assert preview_material is not None and preview_material.node_tree is not None
    assert host != preview_material.node_tree
    assert _tagged_hosts(studio.TIMELINE_HOST_TAG) == [host]
    return host


def _dispatch_seek(start_angle: float, target_angle: float) -> None:
    from quick_sdf_blender import runtime, timeline

    project = STATE["project"]
    area, region = _timeline_context()
    geometry = timeline.build_geometry(
        region.width,
        region.height,
        timeline._visible_keys(project),
    )

    def x_for(angle: float) -> int:
        factor = (angle - geometry.angle_min) / (
            geometry.angle_max - geometry.angle_min
        )
        local_x = geometry.rail.x0 + factor * (
            geometry.rail.x1 - geometry.rail.x0
        )
        return int(region.x + local_x)

    event_y = int(region.y + (geometry.rail.y0 + geometry.rail.y1) * 0.5)
    start_x = x_for(start_angle)
    target_x = x_for(target_angle)
    STATE["seek_set_before"] = len(STATE["seek_set_trace"])
    STATE["seek_settle_before"] = len(STATE["seek_settle_trace"])
    STATE["seek_target"] = float(target_angle)
    STATE["seek_waits"] = 0
    window = bpy.context.window
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=start_x, y=event_y)
    window.event_simulate(type="LEFTMOUSE", value="PRESS", x=start_x, y=event_y)
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=target_x, y=event_y)
    window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=target_x, y=event_y)

    # Keep references alive while Blender dispatches the queued events.
    STATE["seek_canvas"] = runtime.resolve_display_image(
        project, runtime.active_angle(project)
    )


def _restore_timeline_hooks() -> None:
    if "original_set_seek" not in STATE:
        return
    from quick_sdf_blender import timeline

    timeline._set_seek = STATE.pop("original_set_seek")
    timeline._settle_seek = STATE.pop("original_settle_seek")


def _restore_save_hooks() -> None:
    if "original_remove_timeline_hosts" not in STATE:
        return
    from quick_sdf_blender import studio

    studio._remove_timeline_hosts = STATE.pop("original_remove_timeline_hosts")
    studio._ensure_session_timeline_host = STATE.pop(
        "original_ensure_session_timeline_host"
    )


def _finish(error: BaseException | None = None) -> None:
    _restore_timeline_hooks()
    _restore_save_hooks()
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        "PASS" if error is None else "".join(traceback.format_exception(error)),
        encoding="utf-8",
    )
    try:
        if "quick_sdf_blender" in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_disable(module="quick_sdf_blender")
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def _check() -> float | None:
    try:
        from quick_sdf_blender import runtime, studio, timeline

        phase = str(STATE.get("phase", "OPENING"))
        if phase == "OPENING":
            if studio.current_session() is None:
                STATE["open_waits"] = int(STATE.get("open_waits", 0)) + 1
                if int(STATE["open_waits"]) <= 100:
                    return 0.05
                project = STATE["project"]
                raise AssertionError(
                    project.diagnostic_message or "Studio did not finish opening"
                )
            if not bool(STATE.get("studio_settled", False)):
                STATE["studio_settled"] = True
                return 1.0

            project = STATE["project"]
            _assert_isolated_host(project, STATE["original_tree"])
            keys = timeline._visible_keys(project)
            assert len(keys) == 8
            angles = [float(item.angle) for _index, item in keys]
            STATE["seek_cases"] = [
                (angles[0], angles[2]),
                (angles[2], angles[5]),
                (angles[5], angles[7]),
            ]
            STATE["seek_case_index"] = 0
            STATE["seek_set_trace"] = []
            STATE["seek_settle_trace"] = []
            STATE["original_set_seek"] = timeline._set_seek
            STATE["original_settle_seek"] = timeline._settle_seek

            def tracked_set_seek(context, selected_project, value):
                STATE["seek_set_trace"].append(float(value))
                return STATE["original_set_seek"](context, selected_project, value)

            def tracked_settle_seek(context, selected_project, value):
                STATE["seek_settle_trace"].append(float(value))
                return STATE["original_settle_seek"](
                    context, selected_project, value
                )

            timeline._set_seek = tracked_set_seek
            timeline._settle_seek = tracked_settle_seek
            start, target = STATE["seek_cases"][0]
            _dispatch_seek(start, target)
            STATE["phase"] = "SEEKING"
            return 0.1

        if phase == "SEEKING":
            target = float(STATE["seek_target"])
            set_values = STATE["seek_set_trace"][int(STATE["seek_set_before"]):]
            settle_values = STATE["seek_settle_trace"][
                int(STATE["seek_settle_before"]):
            ]
            # Blender may coalesce the synthetic MOUSEMOVE between press and
            # release. Invocation must still enter seek preview and release
            # must settle at the requested rail position.
            reached = bool(set_values)
            settled = any(abs(value - target) < 0.25 for value in settle_values)
            if not (reached and settled):
                STATE["seek_waits"] = int(STATE.get("seek_waits", 0)) + 1
                if int(STATE["seek_waits"]) <= 40:
                    return 0.05
                raise AssertionError(
                    f"Timeline LMB drag did not reach {target}: "
                    f"set={set_values}, settle={settle_values}"
                )

            project = STATE["project"]
            active = runtime.active_angle(project)
            assert abs(float(active.angle) - target) < 0.01
            case_index = int(STATE["seek_case_index"]) + 1
            STATE["seek_case_index"] = case_index
            cases = STATE["seek_cases"]
            if case_index < len(cases):
                start, target = cases[case_index]
                _dispatch_seek(start, target)
                return 0.1

            _restore_timeline_hooks()
            area, region = _timeline_context()
            with bpy.context.temp_override(
                window=bpy.context.window,
                screen=bpy.context.window.screen,
                area=area,
                region=region,
            ):
                assert bpy.ops.quicksdf.timeline_block_context_menu.poll()
                assert bpy.ops.quicksdf.timeline_block_context_menu() == {"FINISHED"}
            addon_keyconfig = bpy.context.window_manager.keyconfigs.addon
            assert addon_keyconfig is not None
            assert any(
                item.idname == "quicksdf.timeline_block_context_menu"
                and item.type == "RIGHTMOUSE"
                and item.value == "PRESS"
                for keymap in addon_keyconfig.keymaps
                if keymap.name == "Node Editor"
                for item in keymap.keymap_items
            )

            host = _assert_isolated_host(project, STATE["original_tree"])
            assert host is not None
            STATE["save_remove_observations"] = []
            STATE["save_ensure_observations"] = []
            STATE["original_remove_timeline_hosts"] = studio._remove_timeline_hosts
            STATE["original_ensure_session_timeline_host"] = (
                studio._ensure_session_timeline_host
            )

            def tracked_remove_timeline_hosts():
                before = len(_tagged_hosts(studio.TIMELINE_HOST_TAG))
                result = STATE["original_remove_timeline_hosts"]()
                after = len(_tagged_hosts(studio.TIMELINE_HOST_TAG))
                STATE["save_remove_observations"].append((before, after))
                return result

            def tracked_ensure_session_timeline_host(session=None):
                result = STATE["original_ensure_session_timeline_host"](session)
                STATE["save_ensure_observations"].append(
                    (bool(result), len(_tagged_hosts(studio.TIMELINE_HOST_TAG)))
                )
                return result

            studio._remove_timeline_hosts = tracked_remove_timeline_hosts
            studio._ensure_session_timeline_host = tracked_ensure_session_timeline_host
            SAVE_PATH.unlink(missing_ok=True)
            assert bpy.ops.wm.save_as_mainfile(
                filepath=str(SAVE_PATH), copy=True
            ) == {"FINISHED"}
            STATE["phase"] = "SAVED"
            return 0.2

        if phase == "SAVED":
            project = STATE["project"]
            host = _assert_isolated_host(project, STATE["original_tree"])
            assert host is not None
            assert any(
                before == 1 and after == 0
                for before, after in STATE["save_remove_observations"]
            ), STATE["save_remove_observations"]
            assert any(
                succeeded and count == 1
                for succeeded, count in STATE["save_ensure_observations"]
            ), STATE["save_ensure_observations"]
            _restore_save_hooks()
            assert SAVE_PATH.is_file()
            with bpy.data.libraries.load(str(SAVE_PATH), link=False) as (
                data_from,
                data_to,
            ):
                saved_node_groups = tuple(data_from.node_groups)
                data_to.node_groups = []
            assert studio.TIMELINE_HOST_NAME not in saved_node_groups
            assert bpy.ops.quicksdf.studio_exit() == {"FINISHED"}
            STATE["phase"] = "EXITED"
            return 0.1

        if phase == "EXITED":
            if studio.current_session() is not None:
                return 0.05
            assert not _tagged_hosts(studio.TIMELINE_HOST_TAG)

            # Exercise unregister cleanup independently from the normal Exit path.
            orphan = studio._timeline_host_tree(str(STATE["project_uuid"]))
            assert orphan.get(studio.TIMELINE_HOST_TAG) == str(STATE["project_uuid"])
            assert _tagged_hosts(studio.TIMELINE_HOST_TAG) == [orphan]
            assert bpy.ops.preferences.addon_disable(
                module="quick_sdf_blender"
            ) == {"FINISHED"}
            assert not _tagged_hosts(studio.TIMELINE_HOST_TAG)
            _finish()
            return None

        raise AssertionError(f"Unknown test phase: {phase}")
    except Exception as error:
        _finish(error)
        return None


def _run() -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.unlink(missing_ok=True)
    SAVE_PATH.unlink(missing_ok=True)
    try:
        assert not bpy.app.background
        assert bpy.ops.preferences.addon_enable(
            module="quick_sdf_blender"
        ) == {"FINISHED"}
        from quick_sdf_blender import runtime

        obj = _mesh()
        original_material = obj.material_slots[0].material
        original_tree = original_material.node_tree
        assert original_tree is not None
        assert any(
            node.bl_idname == "ShaderNodeBsdfPrincipled"
            for node in original_tree.nodes
        )
        bpy.context.scene.quick_sdf_settings.resolution = 512
        bpy.context.scene.quick_sdf_settings.initialization = "NORMAL_SWEEP"
        assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
        project = runtime.active_project()
        assert project is not None
        STATE.update(
            phase="OPENING",
            project=project,
            project_uuid=str(project.uuid),
            original_tree=original_tree,
        )
        assert bpy.ops.quicksdf.studio_enter() == {"FINISHED"}
        bpy.app.timers.register(_check, first_interval=0.1)
    except Exception as error:
        _finish(error)


bpy.app.timers.register(_run, first_interval=0.25)

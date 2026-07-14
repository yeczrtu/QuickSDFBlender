"""Headless Blender 5.1 smoke for schema-6 interactive Smart Paint.

Run from the repository root with::

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender_interactive_paint_smoke.py

The test changes one Display texel between the normal snapshot and propagation
operators. This is equivalent to the native Texture Paint step while remaining
deterministic in a background Blender process.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import quick_sdf_blender  # noqa: E402
from quick_sdf_blender import operators, runtime  # noqa: E402
from quick_sdf_blender.history import History  # noqa: E402


PIXEL = (23, 31)


def _expect_finished(result: set[str], label: str) -> None:
    assert result == {"FINISHED"}, f"{label} returned {result!r}"


def _make_uv_cube() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    assert obj is not None
    material = bpy.data.materials.new("Quick SDF Interactive Paint Material")
    obj.data.materials.append(material)
    uv = obj.data.uv_layers.new(name="QuickSDFInteractiveUV")
    quad = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for polygon in obj.data.polygons:
        for corner, loop_index in enumerate(polygon.loop_indices):
            uv.data[loop_index].uv = quad[corner % 4]
    obj.data.uv_layers.active = uv
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def _lane(project) -> list[object]:
    return sorted(
        (item for item in project.angles if str(item.side) == "RIGHT"),
        key=lambda item: float(item.angle),
    )


def _select(project, item) -> None:
    index = next(
        index for index, candidate in enumerate(project.angles)
        if str(candidate.uuid) == str(item.uuid)
    )
    project.active_angle_index = index
    project.active_angle_uuid = str(item.uuid)
    project.active_side = str(item.side)
    runtime.sync_canvas(bpy.context, project)


def _state(project) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    result = {}
    for item in project.angles:
        image = runtime.resolve_display_image(project, item)
        assert image is not None
        result[str(item.uuid)] = (
            runtime.image_gray8(image),
            runtime.coverage_mask(item).copy(),
        )
    return result


def _assert_state(project, expected) -> None:
    assert {str(item.uuid) for item in project.angles} == set(expected)
    for item in project.angles:
        image = runtime.resolve_display_image(project, item)
        assert image is not None
        display, coverage = expected[str(item.uuid)]
        np.testing.assert_array_equal(runtime.image_gray8(image), display)
        np.testing.assert_array_equal(runtime.coverage_mask(item), coverage)


def _fill_lane(project, value: int) -> None:
    resolution = int(project.resolution)
    gray = np.full((resolution, resolution), value, dtype=np.uint8)
    clear = np.zeros((resolution, resolution), dtype=np.bool_)
    for item in _lane(project):
        image = runtime.resolve_display_image(project, item)
        assert image is not None
        runtime.write_image_gray8(image, gray)
        runtime.set_coverage_mask(item, clear)


def _paint_texel(project, value: int) -> set[str]:
    _expect_finished(bpy.ops.quicksdf.paint_snapshot(), "paint_snapshot")
    item = runtime.active_angle(project)
    image = runtime.resolve_display_image(project, item)
    assert item is not None and image is not None
    gray = runtime.image_gray8(image)
    gray[PIXEL] = value
    runtime.write_image_gray8(image, gray)
    return bpy.ops.quicksdf.propagate_overrides()


def _assert_direction(project, active_angle: float, *, light: bool) -> None:
    for item in _lane(project):
        image = runtime.resolve_display_image(project, item)
        assert image is not None
        propagated = (
            float(item.angle) >= active_angle
            if light
            else float(item.angle) <= active_angle
        )
        expected = 255 if (light and propagated) or (not light and not propagated) else 0
        assert int(runtime.image_gray8(image)[PIXEL]) == expected, (
            item.angle,
            active_angle,
            light,
        )
        assert bool(runtime.coverage_mask(item)[PIXEL]) is propagated


def _test_direction_and_history(project) -> None:
    operators.clear_histories(str(project.uuid))
    _fill_lane(project, 255)
    active = _lane(project)[4]
    _select(project, active)
    before = _state(project)
    assert _paint_texel(project, 0) == {"FINISHED"}
    active_image = runtime.resolve_display_image(project, active)
    assert active_image is not None
    assert runtime._GRAY_CACHE_NAME == active_image.name
    assert runtime._GRAY_CACHE_REVISION == int(
        active_image.get(runtime.IMAGE_REVISION_KEY, 0)
    )
    _assert_direction(project, float(active.angle), light=False)
    after = _state(project)

    history = operators._HISTORIES[str(project.uuid)]
    assert history.undo_count == 1
    _expect_finished(bpy.ops.quicksdf.history_undo(), "history_undo")
    _assert_state(project, before)
    _expect_finished(bpy.ops.quicksdf.history_redo(), "history_redo")
    _assert_state(project, after)

    operators.clear_histories(str(project.uuid))
    _fill_lane(project, 0)
    active = _lane(project)[3]
    _select(project, active)
    assert _paint_texel(project, 255) == {"FINISHED"}
    _assert_direction(project, float(active.angle), light=True)


def _test_failed_propagation_rolls_back(project) -> None:
    operators.clear_histories(str(project.uuid))
    _fill_lane(project, 255)
    active = _lane(project)[6]
    _select(project, active)
    before = _state(project)
    flags_before = {
        str(item.uuid): (bool(item.is_manual), bool(item.dirty))
        for item in project.angles
    }
    original_set_coverage = runtime.set_coverage_mask
    calls = 0
    failed = False

    def fail_second_coverage_write(item, values):
        nonlocal calls, failed
        calls += 1
        if calls == 2 and not failed:
            failed = True
            raise RuntimeError("simulated interactive Coverage failure")
        return original_set_coverage(item, values)

    _expect_finished(bpy.ops.quicksdf.paint_snapshot(), "rollback snapshot")
    source = runtime.resolve_display_image(project, active)
    assert source is not None
    gray = runtime.image_gray8(source)
    gray[PIXEL] = 0
    runtime.write_image_gray8(source, gray)
    runtime.set_coverage_mask = fail_second_coverage_write
    try:
        try:
            result = bpy.ops.quicksdf.propagate_overrides()
        except RuntimeError as error:
            assert "simulated interactive Coverage failure" in str(error)
        else:
            assert result == {"CANCELLED"}, result
    finally:
        runtime.set_coverage_mask = original_set_coverage
    assert failed
    _assert_state(project, before)
    for item in project.angles:
        assert (bool(item.is_manual), bool(item.dirty)) == flags_before[str(item.uuid)]
    history = operators._HISTORIES.get(str(project.uuid))
    assert history is None or not history.can_undo
    assert runtime.consume_interactive_paint_snapshot(project) is None


def _test_hard_history_cap_rolls_back(project) -> None:
    operators.clear_histories(str(project.uuid))
    _fill_lane(project, 255)
    active = _lane(project)[5]
    _select(project, active)
    before = _state(project)
    history = History(
        byte_budget=128,
        soft_byte_budget=128,
        compression_level=0,
    )
    assert history.push("older action", {}, {}, metadata={"marker": "keep"})
    operators._HISTORIES[str(project.uuid)] = history

    _expect_finished(bpy.ops.quicksdf.paint_snapshot(), "hard-cap snapshot")
    source = runtime.resolve_display_image(project, active)
    assert source is not None
    gray = runtime.image_gray8(source)
    gray[PIXEL] = 0
    runtime.write_image_gray8(source, gray)
    try:
        result = bpy.ops.quicksdf.propagate_overrides()
    except RuntimeError as error:
        assert "too large" in str(error)
    else:
        assert result == {"CANCELLED"}, result
    _assert_state(project, before)
    retained = operators._HISTORIES.get(str(project.uuid))
    assert retained is history
    assert retained.active_transaction is None
    assert retained.can_undo
    assert retained.undo_label == "older action"


def _test_aux_history_hard_cap_rolls_back(project) -> None:
    operators.clear_histories(str(project.uuid))
    item = project.aux_masks[0]
    image = runtime.resolve_aux_mask_image(project, item)
    assert image is not None
    before = runtime.image_gray8(image)
    yy, xx = np.indices(before.shape)
    after = (((xx * 37 + yy * 61) & 255)).astype(np.uint8)
    runtime.write_image_gray8(image, after)

    history = History(byte_budget=64, soft_byte_budget=64, compression_level=0)
    assert history.push("older action", {}, {}, metadata={"marker": "keep"})
    operators._HISTORIES[str(project.uuid)] = history
    try:
        operators._record_aux_image_change(
            project,
            item,
            image,
            before,
            "oversize aux",
            after=after,
        )
    except RuntimeError as error:
        assert "too large" in str(error)
    else:
        raise AssertionError("oversize Aux history action unexpectedly succeeded")
    np.testing.assert_array_equal(runtime.image_gray8(image), before)
    assert history.active_transaction is None
    assert history.can_undo and history.undo_label == "older action"


def _test_structural_history(project) -> None:
    operators.clear_histories(str(project.uuid))
    item = project.angles.add()
    item.uuid = runtime.new_uuid()
    item.angle = 15.0
    item.side = "RIGHT"
    image = runtime.create_angle_layer_image(
        str(project.uuid),
        str(item.uuid),
        float(item.angle),
        int(project.resolution),
        runtime.DISPLAY_ROLE,
        side=str(item.side),
    )
    item.display_image = image
    item.display_image_name = image.name
    created_uuid = str(item.uuid)
    resolution = int(project.resolution)
    base = np.ones((resolution, resolution), dtype=np.bool_)
    coverage_before = np.zeros_like(base)
    runtime.set_base_mask(item, base)
    runtime.set_coverage_mask(item, coverage_before)
    base_blob = runtime.bitplane_blob(item, "BASE")
    coverage_blob = runtime.bitplane_blob(item, "COVERAGE")
    base_revision = int(item.base_revision)
    coverage_revision = int(item.coverage_revision)
    operators._sort_angle_items(project)
    item = next(value for value in project.angles if str(value.uuid) == created_uuid)
    _select(project, item)

    display_before = np.full((resolution, resolution), 255, dtype=np.uint8)
    display_after = display_before.copy()
    display_after[PIXEL] = 0
    coverage_after = coverage_before.copy()
    coverage_after[PIXEL] = True
    runtime.write_image_gray8(image, display_after)
    runtime.set_coverage_mask(item, coverage_after)
    metadata = {
        "created_key": {
            "kind": "CREATE_KEY",
            "uuid": created_uuid,
            "angle": float(item.angle),
            "side": str(item.side),
            "display_image_name": image.name,
            "base_blob": base_blob,
            "coverage_blob": coverage_blob,
            "base_revision": base_revision,
            "coverage_revision": coverage_revision,
        }
    }
    history = History(compression_level=1)
    assert history.push(
        "Paint + Auto Key",
        {
            f"display:{created_uuid}": display_before,
            f"coverage:{created_uuid}": coverage_before,
        },
        {
            f"display:{created_uuid}": display_after,
            f"coverage:{created_uuid}": coverage_after,
        },
        metadata=metadata,
    )
    operators._HISTORIES[str(project.uuid)] = history

    _expect_finished(bpy.ops.quicksdf.history_undo(), "structural history_undo")
    assert not any(str(value.uuid) == created_uuid for value in project.angles)
    assert str(image.get(runtime.ROLE_KEY, "")) == "history_orphan_display", str(
        image.get(runtime.ROLE_KEY, "")
    )
    assert history.can_redo

    _expect_finished(bpy.ops.quicksdf.history_redo(), "structural history_redo")
    restored = next(value for value in project.angles if str(value.uuid) == created_uuid)
    assert runtime.resolve_display_image(project, restored) == image
    assert str(image.get(runtime.ROLE_KEY, "")) == runtime.DISPLAY_ROLE
    np.testing.assert_array_equal(runtime.base_mask(restored), base)
    np.testing.assert_array_equal(runtime.coverage_mask(restored), coverage_after)
    np.testing.assert_array_equal(runtime.image_gray8(image), display_after)
    assert history.can_undo


def run() -> None:
    assert bpy.app.background
    assert bpy.app.version[:2] == (5, 1), bpy.app.version_string
    registered = False
    try:
        quick_sdf_blender.register()
        registered = True
        _make_uv_cube()
        settings = bpy.context.scene.quick_sdf_settings
        settings.resolution = 512
        settings.initialization = "WHITE"
        _expect_finished(bpy.ops.quicksdf.project_create(), "project_create")
        project = runtime.active_project()
        assert project is not None and len(_lane(project)) == 8
        _test_direction_and_history(project)
        _test_failed_propagation_rolls_back(project)
        _test_hard_history_cap_rolls_back(project)
        _test_aux_history_hard_cap_rolls_back(project)
        _test_structural_history(project)
        print("[Quick SDF interactive paint smoke] PASS")
    finally:
        if registered:
            quick_sdf_blender.unregister()


run()

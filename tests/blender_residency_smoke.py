"""Blender 5.1/5.2 smoke test for bounded 2K Display residency.

Run from the repository root with::

    blender --background --factory-startup --python tests/blender_residency_smoke.py

The test deliberately uses real 2048px generated Images.  It verifies that
non-neighbour angle buffers can be released only after a matching packed
revision exists, and that Blender reconstructs the exact gray8 canvas from the
packed source after both ``buffers_free`` and save/reload.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import quick_sdf_blender  # noqa: E402
from quick_sdf_blender import live_preview, residency, runtime  # noqa: E402
from quick_sdf_blender.bitplane import BitplaneRole  # noqa: E402
from quick_sdf_blender.model import DEFAULT_KEY_ANGLES, SCHEMA_VERSION  # noqa: E402
from quick_sdf_blender.preview_cache import (  # noqa: E402
    cache_statistics,
    image_proxy_mask,
)


RESOLUTION = 2048
PROJECT_UUID = "residency-2k-smoke"
TARGET_INDEX = 0
ACTIVE_INDEX = 4


def _digest(values: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest()


def _project() -> object:
    return next(
        project
        for scene in bpy.data.scenes
        for project in getattr(scene, "quick_sdf_projects", ())
        if str(project.uuid) == PROJECT_UUID
    )


def _display(project: object, index: int) -> bpy.types.Image:
    image = runtime.resolve_display_image(project, project.angles[index])
    assert image is not None
    return image


def _make_project() -> object:
    scene = bpy.context.scene
    project = scene.quick_sdf_projects.add()
    project.uuid = PROJECT_UUID
    project.name = "2K Residency Smoke"
    project.schema_version = SCHEMA_VERSION
    project.resolution = RESOLUTION
    project.authoring_side = "RIGHT"
    project.active_side = "RIGHT"
    scene.quick_sdf_active_project_index = len(scene.quick_sdf_projects) - 1

    coordinate = (np.arange(RESOLUTION, dtype=np.uint16) // 64) & 1
    checker = np.bitwise_xor(coordinate[:, None], coordinate[None, :]).astype(
        np.uint8
    )
    checker *= np.uint8(255)
    yy, xx = np.ogrid[:RESOLUTION, :RESOLUTION]
    base_template = ((xx + 2 * yy) % 11) < 5
    coverage_template = ((3 * xx + yy) % 97) == 0

    for index, angle in enumerate(DEFAULT_KEY_ANGLES):
        item = project.angles.add()
        item.uuid = f"residency-angle-{index}"
        item.angle = float(angle)
        item.side = "RIGHT"
        image = runtime.create_angle_layer_image(
            project.uuid,
            item.uuid,
            float(angle),
            RESOLUTION,
            runtime.DISPLAY_ROLE,
            side="RIGHT",
        )
        item.display_image = image
        item.display_image_name = image.name
        runtime.set_base_mask(item, np.logical_xor(base_template, bool(index & 1)))
        runtime.set_coverage_mask(
            item, np.logical_and(coverage_template, (xx + index) % 5 == 0)
        )
        if index == TARGET_INDEX:
            runtime.write_image_gray8(image, checker)

    project.active_angle_index = ACTIVE_INDEX
    project.active_angle_uuid = project.angles[ACTIVE_INDEX].uuid
    project.seek_angle = float(project.angles[ACTIVE_INDEX].angle)
    project.review_angle = project.seek_angle
    return project


def _assert_quiescent() -> None:
    diagnostics = residency.diagnostics()
    assert diagnostics["dirty_images"] == 0, diagnostics
    assert diagnostics["pending_gpu_release"] == 0, diagnostics
    assert not bpy.app.timers.is_registered(residency._idle_step)
    assert not bpy.app.timers.is_registered(live_preview._poll_sdf_jobs)
    assert live_preview._SDF_EXECUTOR is None
    assert not live_preview._SDF_JOBS
    assert not live_preview._PENDING_REFRESH
    assert cache_statistics() == {
        "cpu_bytes": 0,
        "cpu_entries": 0,
        "gpu_bytes": 0,
        "gpu_entries": 0,
    }


def _test_gray_cache_datablock_identity() -> None:
    """A recreated same-name/same-revision Image must not reuse stale gray."""

    paint = bpy.context.scene.tool_settings.image_paint
    previous_canvas = paint.canvas
    first = bpy.data.images.new(
        "QSDF Gray Cache Identity",
        width=4,
        height=3,
        alpha=True,
        float_buffer=False,
    )
    try:
        paint.canvas = first
        runtime.write_image_gray8(first, np.full((3, 4), 23, dtype=np.uint8))
        cached = runtime.image_gray8(first, use_cache=True)
        np.testing.assert_array_equal(cached, 23)
        revision = int(first.get(runtime.IMAGE_REVISION_KEY, 0))
        paint.canvas = None
        bpy.data.images.remove(first)

        second = bpy.data.images.new(
            "QSDF Gray Cache Identity",
            width=4,
            height=3,
            alpha=True,
            float_buffer=False,
        )
        second[runtime.IMAGE_REVISION_KEY] = revision
        rgba = np.ones((3, 4, 4), dtype=np.float32)
        rgba[..., :3] = 199.0 / 255.0
        second.pixels.foreach_set(rgba.reshape(-1))
        second.update()
        paint.canvas = second
        resolved = runtime.image_gray8(second, use_cache=True)
        np.testing.assert_array_equal(resolved, 199)
    finally:
        paint.canvas = previous_canvas
        runtime.invalidate_gray_cache()
        candidate = bpy.data.images.get("QSDF Gray Cache Identity")
        if candidate is not None:
            bpy.data.images.remove(candidate)


def _test_dirty_pack_retry(project: object) -> None:
    """An idle pack failure remains dirty and succeeds on the next event."""

    image = bpy.data.images.new(
        "QSDF Dirty Retry",
        width=4,
        height=4,
        alpha=True,
        float_buffer=False,
    )
    runtime.tag_image(
        image,
        str(project.uuid),
        "dirty-retry",
        runtime.DISPLAY_ROLE,
    )
    original_pack_now = residency.pack_now
    try:
        residency.mark_changed(image)

        def fail_once(_image):
            raise OSError("simulated transient pack failure")

        residency.pack_now = fail_once
        assert residency._idle_step() == 0.25
        assert residency.diagnostics()["dirty_images"] == 1
        residency.pack_now = original_pack_now
        assert residency._idle_step() == 0.02
        assert residency.diagnostics()["dirty_images"] == 0
    finally:
        residency.pack_now = original_pack_now
        residency.forget_image(image)
        if image.name in bpy.data.images:
            bpy.data.images.remove(image)


def main() -> None:
    assert (5, 1) <= bpy.app.version[:2] < (5, 3), bpy.app.version_string
    quick_sdf_blender.register()
    try:
        project = _make_project()
        target_item = project.angles[TARGET_INDEX]
        target = _display(project, TARGET_INDEX)
        expected_gray = runtime.image_gray8(target)
        expected_gray_digest = _digest(expected_gray)
        expected_base_blob = runtime.bitplane_blob(target_item, BitplaneRole.BASE)
        expected_coverage_blob = runtime.bitplane_blob(
            target_item, BitplaneRole.COVERAGE
        )
        expected_base_digest = _digest(runtime.base_mask(target_item))
        expected_coverage_digest = _digest(runtime.coverage_mask(target_item))

        # Every persistent image must have a current recovery source before any
        # buffer is eligible for eviction.
        for item in project.angles:
            residency.pack_now(runtime.resolve_display_image(project, item))

        active = _display(project, ACTIVE_INDEX)
        residency.activate(project, active)
        bpy.context.scene.tool_settings.image_paint.canvas = active
        residency.reconcile_project(project, active)
        assert residency.diagnostics()["pending_gpu_release"] == 0

        keep_indices = {ACTIVE_INDEX - 1, ACTIVE_INDEX, ACTIVE_INDEX + 1}
        for index, item in enumerate(project.angles):
            image = runtime.resolve_display_image(project, item)
            assert image is not None
            assert int(image.get(residency.PACKED_REVISION_KEY, -1)) == int(
                image.get(runtime.IMAGE_REVISION_KEY, 0)
            )
            if index not in keep_indices:
                assert not image.has_data, (
                    index,
                    image.name,
                    "non-neighbour Display remained CPU resident",
                )

        assert not target.has_data
        residency.ensure_loaded(target)
        assert target.has_data
        cold_gray = runtime.image_gray8(target)
        assert _digest(cold_gray) == expected_gray_digest
        np.testing.assert_array_equal(cold_gray, expected_gray)
        assert runtime.bitplane_blob(target_item, BitplaneRole.BASE) == expected_base_blob
        assert (
            runtime.bitplane_blob(target_item, BitplaneRole.COVERAGE)
            == expected_coverage_blob
        )
        assert _digest(runtime.base_mask(target_item)) == expected_base_digest
        assert (
            _digest(runtime.coverage_mask(target_item))
            == expected_coverage_digest
        )

        # Exercise the bounded derived cache before cleanup, without uploading
        # the full Display to a GPU texture in background mode.
        proxy = image_proxy_mask(target)
        assert max(proxy.shape) == 512
        assert cache_statistics()["cpu_bytes"] > 0

        residency.reconcile_project(project, active)
        assert not target.has_data
        with tempfile.TemporaryDirectory(prefix="quicksdf-residency-") as directory:
            blend_path = Path(directory) / "residency.blend"
            assert bpy.ops.wm.save_as_mainfile(filepath=str(blend_path)) == {
                "FINISHED"
            }
            assert bpy.ops.wm.open_mainfile(filepath=str(blend_path)) == {
                "FINISHED"
            }

        project = _project()
        runtime.repair_project_references(bpy.context.scene)
        target_item = project.angles[TARGET_INDEX]
        target = _display(project, TARGET_INDEX)
        residency.ensure_loaded(target)
        assert _digest(runtime.image_gray8(target)) == expected_gray_digest
        assert runtime.bitplane_blob(target_item, BitplaneRole.BASE) == expected_base_blob
        assert (
            runtime.bitplane_blob(target_item, BitplaneRole.COVERAGE)
            == expected_coverage_blob
        )
        assert _digest(runtime.base_mask(target_item)) == expected_base_digest
        assert (
            _digest(runtime.coverage_mask(target_item))
            == expected_coverage_digest
        )

        _test_gray_cache_datablock_identity()
        _test_dirty_pack_retry(project)

        live_preview.cleanup()
        residency.shutdown()
        _assert_quiescent()
    finally:
        quick_sdf_blender.unregister()

    _assert_quiescent()
    print("[Quick SDF 2K residency smoke] PASS")


if __name__ == "__main__":
    main()

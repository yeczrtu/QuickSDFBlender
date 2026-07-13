"""Blender 5.1 background smoke test for schema-v1 image migration.

Run from the repository root::

    blender --background --factory-startup --python tests/blender_migration_smoke.py

The test builds v1-style images directly.  In particular, no v2 base or
coverage pointers are populated before migration, so it exercises the same
RGBA-alpha split used when an old ``.blend`` is opened.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from quick_sdf_blender import runtime  # noqa: E402
from quick_sdf_blender.migration import migrate_project_v1_to_v2  # noqa: E402


def _expect_finished(result: set[str], operation: str) -> None:
    assert result == {"FINISHED"}, f"{operation} returned {result!r}"


def _legacy_pixels(seed: int, size: int) -> np.ndarray:
    """Return top-down RGB test data with several non-zero alpha strengths."""

    rgba = np.empty((size, size, 4), dtype=np.float32)
    rgba[..., 0] = (seed + 1) / 16.0
    rgba[..., 1] = (seed + 3) / 16.0
    rgba[..., 2] = (seed + 5) / 16.0
    rgba[..., 3] = 0.0
    rgba[0, 0] = (0.0, 0.25, 1.0, 1.0 / 255.0)
    rgba[1, 2] = (1.0, 0.5, 0.0, 0.49)
    rgba[3, 4] = (0.125, 0.875, 0.375, 1.0)
    rgba[5, 6] = (0.75, 0.125, 0.625, 0.0)
    return rgba


def _make_v1_project() -> tuple[object, dict[str, np.ndarray]]:
    scene = bpy.context.scene
    project = scene.quick_sdf_projects.add()
    project.uuid = runtime.new_uuid()
    project.name = "Schema v1 Migration Smoke"
    project.schema_version = 1
    project.resolution = 512
    project.author_active = True
    project.preview_enabled = True
    project.material_override_active = True
    project.symmetry_mode = "AUTO"
    project.mirror_enabled = True

    expected: dict[str, np.ndarray] = {}
    for seed, signed_angle in enumerate((-15.0, 0.0)):
        angle = project.angles.add()
        angle.uuid = runtime.new_uuid()
        angle.angle = signed_angle
        image = bpy.data.images.new(
            f"Legacy v1 {signed_angle:+g}",
            width=project.resolution,
            height=project.resolution,
            alpha=True,
            float_buffer=False,
        )
        runtime.tag_image(
            image,
            project.uuid,
            angle.uuid,
            runtime.LEGACY_MASK_ROLE,
        )
        # v1 only populated these deprecated fields.
        angle.image = image
        angle.image_name = image.name
        pixels = _legacy_pixels(seed, project.resolution)
        image.pixels.foreach_set(np.flip(pixels, axis=0).ravel())
        image.update()
        # Compare against Blender's stored 8-bit values rather than ideal input
        # floats so the assertion tests migration, not buffer quantization.
        expected[angle.uuid] = runtime.image_rgba(image)

    project.active_angle_index = 0
    scene.quick_sdf_active_project_index = len(scene.quick_sdf_projects) - 1
    return project, expected


def _project_images(project: object) -> tuple[bpy.types.Image, ...]:
    return tuple(
        image
        for image in bpy.data.images
        if image.get(runtime.PROJECT_UUID_KEY) == project.uuid
    )


def _assert_layer_tags(
    image: bpy.types.Image,
    project: object,
    angle: object,
    role: str,
) -> None:
    assert image.get(runtime.PROJECT_UUID_KEY) == project.uuid
    assert image.get(runtime.ANGLE_UUID_KEY) == angle.uuid
    assert image.get(runtime.ROLE_KEY) == role
    assert image.alpha_mode == "NONE"


def _assert_migrated(project: object, expected: dict[str, np.ndarray]) -> None:
    assert project.schema_version == 2
    assert not project.author_active
    assert not project.preview_enabled
    assert not project.material_override_active
    assert not project.mirror_enabled
    assert project.symmetry_mode == "INDEPENDENT"
    assert [(item.side, item.angle) for item in project.angles] == [
        ("RIGHT", 0.0),
        ("LEFT", 0.0),
        ("LEFT", 15.0),
    ]

    for angle in project.angles:
        display = runtime.resolve_display_image(project, angle)
        base = runtime.resolve_base_image(project, angle)
        coverage = runtime.resolve_coverage_image(project, angle)
        assert display is not None and base is not None and coverage is not None
        assert angle.image == display
        assert angle.image_name == display.name
        _assert_layer_tags(display, project, angle, runtime.DISPLAY_ROLE)
        _assert_layer_tags(base, project, angle, runtime.BASE_ROLE)
        _assert_layer_tags(coverage, project, angle, runtime.COVERAGE_ROLE)

        display_rgba = runtime.image_rgba(display)
        base_rgba = runtime.image_rgba(base)
        coverage_rgba = runtime.image_rgba(coverage)
        assert np.all(display_rgba[..., 3] == 1.0)
        assert np.all(base_rgba[..., 3] == 1.0)
        assert np.all(coverage_rgba[..., 3] == 1.0)

        if angle.uuid in expected:
            legacy = expected[angle.uuid]
            np.testing.assert_array_equal(display_rgba[..., :3], legacy[..., :3])
            np.testing.assert_array_equal(base_rgba[..., :3], legacy[..., :3])
            expected_coverage = legacy[..., 3] > 0.0
            np.testing.assert_array_equal(
                coverage_rgba[..., 0] >= 0.5,
                expected_coverage,
            )
            np.testing.assert_array_equal(
                coverage_rgba[..., :3],
                np.repeat(expected_coverage[..., None], 3, axis=2).astype(np.float32),
            )

    # The new independent LEFT zero is a deep copy of RIGHT zero, including
    # coverage, but has its own UUID and role tags.
    right_zero, left_zero = project.angles[0], project.angles[1]
    assert right_zero.uuid != left_zero.uuid
    for resolver in (
        runtime.resolve_display_image,
        runtime.resolve_base_image,
        runtime.resolve_coverage_image,
    ):
        np.testing.assert_array_equal(
            runtime.image_rgba(resolver(project, right_zero)),
            runtime.image_rgba(resolver(project, left_zero)),
        )

    project.active_angle_index = 0
    canvas = runtime.sync_canvas(bpy.context, project)
    assert canvas == runtime.resolve_display_image(project, right_zero)
    assert bpy.context.scene.tool_settings.image_paint.canvas == canvas
    assert canvas.get(runtime.ROLE_KEY) == runtime.DISPLAY_ROLE


def run() -> None:
    assert bpy.app.version[:2] == (5, 1), bpy.app.version_string
    _expect_finished(
        bpy.ops.preferences.addon_enable(module="quick_sdf_blender"),
        "addon_enable",
    )

    project, expected = _make_v1_project()
    project_uuid = project.uuid
    assert migrate_project_v1_to_v2(project)
    _assert_migrated(project, expected)

    image_names = tuple(sorted(image.name for image in _project_images(project)))
    assert len(image_names) == 9  # three angle keys, each with three layers
    assert not migrate_project_v1_to_v2(project)
    assert tuple(sorted(image.name for image in _project_images(project))) == image_names

    # Verify that save/reload and the registered load handler do not recreate
    # layers or reintroduce v1 transparency.
    with tempfile.TemporaryDirectory(prefix="quicksdf-migration-") as directory:
        blend_path = Path(directory) / "migration_v2.blend"
        _expect_finished(
            bpy.ops.wm.save_as_mainfile(filepath=str(blend_path)),
            "save migrated blend",
        )
        _expect_finished(
            bpy.ops.wm.open_mainfile(filepath=str(blend_path)),
            "reload migrated blend",
        )
        project = next(
            item
            for scene in bpy.data.scenes
            for item in scene.quick_sdf_projects
            if item.uuid == project_uuid
        )
        _assert_migrated(project, expected)
        assert tuple(sorted(image.name for image in _project_images(project))) == image_names
        assert not migrate_project_v1_to_v2(project)

    print("[Quick SDF migration smoke] PASS")


if __name__ == "__main__":
    run()

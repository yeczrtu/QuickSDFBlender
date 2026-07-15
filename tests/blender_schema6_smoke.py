"""Blender 5.1/5.2 smoke test for schema-6 bitplane persistence.

Run from the repository root with::

    blender --background --factory-startup --python tests/blender_schema6_smoke.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import quick_sdf_blender  # noqa: E402
from quick_sdf_blender import runtime  # noqa: E402
from quick_sdf_blender.bitplane import (  # noqa: E402
    BitplaneRole,
    decode_bitplane,
    inspect_bitplane_header,
)
from quick_sdf_blender.model import SCHEMA_VERSION, validate_schema  # noqa: E402


def _project_angle_images(project_uuid: str) -> tuple[bpy.types.Image, ...]:
    return tuple(
        image
        for image in bpy.data.images
        if image.get(runtime.PROJECT_UUID_KEY) == project_uuid
        and image.get(runtime.ANGLE_UUID_KEY)
    )


def main() -> None:
    assert (5, 1) <= bpy.app.version[:2] < (5, 3), bpy.app.version_string
    assert SCHEMA_VERSION == 6
    quick_sdf_blender.register()
    try:
        scene = bpy.context.scene
        project = scene.quick_sdf_projects.add()
        project.uuid = "schema-6-save-reload"
        project.name = "Schema 6 Bitplanes"
        project.schema_version = SCHEMA_VERSION
        project.resolution = 512
        scene.quick_sdf_active_project_index = 0

        rng = np.random.default_rng(20260714)
        records: dict[str, tuple[bytes, bytes, np.ndarray, np.ndarray, int, int]] = {}
        for index, angle in enumerate((0.0, 90.0)):
            item = project.angles.add()
            item.uuid = f"schema-6-angle-{index}"
            item.angle = angle
            item.side = "RIGHT"
            display = runtime.create_angle_layer_image(
                project.uuid,
                item.uuid,
                angle,
                13,
                runtime.DISPLAY_ROLE,
                side="RIGHT",
            )
            item.display_image = display
            item.display_image_name = display.name

            yy, xx = np.indices((13, 13))
            base = ((xx + yy + index) % 3) == 0
            coverage = rng.integers(0, 2, size=(13, 13), dtype=np.uint8).astype(
                np.bool_
            )
            runtime.set_base_mask(item, base)
            runtime.set_coverage_mask(item, coverage)
            base_blob = runtime.bitplane_blob(item, BitplaneRole.BASE)
            coverage_blob = runtime.bitplane_blob(item, BitplaneRole.COVERAGE)
            base_header = inspect_bitplane_header(base_blob)
            coverage_header = inspect_bitplane_header(coverage_blob)
            assert base_header.role is BitplaneRole.BASE
            assert coverage_header.role is BitplaneRole.COVERAGE
            assert base_header.shape == coverage_header.shape == (13, 13)
            assert base_header.raw_size == coverage_header.raw_size == 22
            records[item.uuid] = (
                base_blob,
                coverage_blob,
                base.copy(),
                coverage.copy(),
                base_header.crc32,
                coverage_header.crc32,
            )

        project.active_angle_index = 0
        project.active_angle_uuid = project.angles[0].uuid
        assert len(_project_angle_images(project.uuid)) == len(project.angles)
        assert all(
            image.get(runtime.ROLE_KEY) == runtime.DISPLAY_ROLE
            for image in _project_angle_images(project.uuid)
        )
        assert all(not hasattr(item, "base_image") for item in project.angles)
        assert all(not hasattr(item, "coverage_image") for item in project.angles)

        with tempfile.TemporaryDirectory(prefix="quicksdf-schema6-") as directory:
            blend_path = Path(directory) / "schema6.blend"
            assert bpy.ops.wm.save_as_mainfile(filepath=str(blend_path)) == {"FINISHED"}
            assert bpy.ops.wm.open_mainfile(filepath=str(blend_path)) == {"FINISHED"}

        scene = bpy.context.scene
        project = scene.quick_sdf_projects[0]
        assert project.schema_version == SCHEMA_VERSION == 6
        assert project.uuid == "schema-6-save-reload"
        runtime.repair_project_references(scene)
        assert len(project.angles) == 2
        assert len(_project_angle_images(project.uuid)) == len(project.angles)
        assert all(
            image.get(runtime.ROLE_KEY) == runtime.DISPLAY_ROLE
            for image in _project_angle_images(project.uuid)
        )

        for item in project.angles:
            (
                expected_base_blob,
                expected_coverage_blob,
                expected_base,
                expected_coverage,
                expected_base_crc,
                expected_coverage_crc,
            ) = records[item.uuid]
            display = runtime.resolve_display_image(project, item)
            assert display is not None and item.display_image == display
            assert display.get(runtime.PROJECT_UUID_KEY) == project.uuid
            assert display.get(runtime.ANGLE_UUID_KEY) == item.uuid
            assert display.get(runtime.ROLE_KEY) == runtime.DISPLAY_ROLE
            assert display.alpha_mode == "NONE"

            base_blob = runtime.bitplane_blob(item, BitplaneRole.BASE)
            coverage_blob = runtime.bitplane_blob(item, BitplaneRole.COVERAGE)
            assert base_blob == expected_base_blob
            assert coverage_blob == expected_coverage_blob
            assert inspect_bitplane_header(base_blob).crc32 == expected_base_crc
            assert (
                inspect_bitplane_header(coverage_blob).crc32
                == expected_coverage_crc
            )
            np.testing.assert_array_equal(
                decode_bitplane(base_blob, expected_role=BitplaneRole.BASE),
                expected_base,
            )
            np.testing.assert_array_equal(runtime.base_mask(item), expected_base)
            np.testing.assert_array_equal(
                decode_bitplane(
                    coverage_blob, expected_role=BitplaneRole.COVERAGE
                ),
                expected_coverage,
            )
            np.testing.assert_array_equal(
                runtime.coverage_mask(item), expected_coverage
            )
            assert not hasattr(item, "base_image")
            assert not hasattr(item, "coverage_image")

        legacy = scene.quick_sdf_projects.add()
        legacy.uuid = "unsupported-schema-5"
        legacy.schema_version = 5
        legacy_state = (legacy.uuid, int(legacy.schema_version), len(legacy.angles))
        try:
            validate_schema(legacy)
        except ValueError as error:
            assert "schema 6 is required" in str(error)
        else:
            raise AssertionError("Schema 5 was unexpectedly accepted or migrated")
        assert (legacy.uuid, int(legacy.schema_version), len(legacy.angles)) == legacy_state
    finally:
        quick_sdf_blender.unregister()

    print("[Quick SDF schema-6 bitplane persistence smoke] PASS")


if __name__ == "__main__":
    main()

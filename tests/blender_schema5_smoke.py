"""Blender 5.1 smoke test for schema-5 packing persistence.

Run from the repository root with::

    blender --background --factory-startup --python tests/blender_schema5_smoke.py
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
from quick_sdf_blender import runtime  # noqa: E402
from quick_sdf_blender.model import (  # noqa: E402
    SCHEMA_VERSION,
    ensure_standard_aux_masks,
    reset_liltoon_packing,
)


def _image_hash(image: bpy.types.Image) -> str:
    return hashlib.sha256(runtime.image_rgba(image).tobytes()).hexdigest()


def _recipe(project) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            str(item.output_channel),
            str(item.source_type),
            str(item.auxiliary_mask_uuid),
            bool(item.invert),
            float(item.constant_value),
        )
        for item in project.packing_channels
    )


def main() -> None:
    assert bpy.app.version[:2] == (5, 1), bpy.app.version_string
    assert SCHEMA_VERSION == 5
    quick_sdf_blender.register()
    try:
        scene = bpy.context.scene
        project = scene.quick_sdf_projects.add()
        project.uuid = "schema-5-save-reload"
        project.name = "Schema 5 Channel Packing"
        project.schema_version = SCHEMA_VERSION
        project.resolution = 512

        uuids = iter(("schema-5-area", "schema-5-strength"))
        sdf_area, shadow_strength = ensure_standard_aux_masks(
            project, uuid_factory=lambda: next(uuids)
        )
        area_image = runtime.create_aux_mask_image(
            project, sdf_area, fill_value=1.0
        )
        strength_image = runtime.create_aux_mask_image(
            project, shadow_strength, fill_value=1.0
        )
        area_rgba = runtime.image_rgba(area_image)
        area_rgba[:32, :48, :3] = 0.0
        runtime.write_image_rgba(area_image, area_rgba)
        strength_rgba = runtime.image_rgba(strength_image)
        strength_rgba[64:96, 80:128, :3] = 0.25
        runtime.write_image_rgba(strength_image, strength_rgba)
        area_image[runtime.AUX_MASK_INITIALIZED_KEY] = True
        strength_image[runtime.AUX_MASK_INITIALIZED_KEY] = True
        sdf_area.revision = 3
        shadow_strength.revision = 5

        reset_liltoon_packing(project)
        project.packing_revision = 7
        scene.quick_sdf_active_project_index = 0

        assert [(item.role, item.uuid) for item in project.aux_masks] == [
            ("SDF_AREA", "schema-5-area"),
            ("SHADOW_STRENGTH", "schema-5-strength"),
        ]
        expected_recipe = (
            ("R", "RIGHT_THRESHOLD", "", False, 0.0),
            ("G", "LEFT_THRESHOLD", "", False, 0.0),
            ("B", "SDF_AREA", "schema-5-area", True, 0.0),
            ("A", "SHADOW_STRENGTH", "schema-5-strength", False, 1.0),
        )
        assert _recipe(project) == expected_recipe
        image_records = {
            item.uuid: (
                item.role,
                item.image_name,
                int(item.revision),
                _image_hash(runtime.resolve_aux_mask_image(project, item)),
            )
            for item in project.aux_masks
        }

        with tempfile.TemporaryDirectory(prefix="quicksdf-schema5-") as directory:
            blend_path = Path(directory) / "schema5.blend"
            assert bpy.ops.wm.save_as_mainfile(filepath=str(blend_path)) == {"FINISHED"}
            assert bpy.ops.wm.open_mainfile(filepath=str(blend_path)) == {"FINISHED"}

        scene = bpy.context.scene
        project = scene.quick_sdf_projects[0]
        assert project.schema_version == 5
        assert project.uuid == "schema-5-save-reload"
        assert int(project.packing_revision) == 7
        assert _recipe(project) == expected_recipe
        runtime.repair_project_references(scene)
        assert len(project.aux_masks) == 2
        for item in project.aux_masks:
            role, image_name, revision, digest = image_records[item.uuid]
            image = runtime.resolve_aux_mask_image(project, item)
            assert image is not None and item.image == image
            assert item.role == role
            assert item.image_name == image_name
            assert int(item.revision) == revision
            assert image.get(runtime.PROJECT_UUID_KEY) == project.uuid
            assert image.get(runtime.ROLE_KEY) == runtime.AUX_MASK_ROLE
            assert image.get(runtime.AUX_MASK_UUID_KEY) == item.uuid
            assert image.alpha_mode == "NONE"
            assert _image_hash(image) == digest
            assert np.all(runtime.image_rgba(image)[..., 3] == 1.0)
    finally:
        quick_sdf_blender.unregister()

    print("[Quick SDF schema-5 packing persistence smoke] PASS")


if __name__ == "__main__":
    main()

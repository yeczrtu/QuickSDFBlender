"""Blender 5.1 smoke test for schema-4 save/reload persistence."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import quick_sdf_blender  # noqa: E402
from quick_sdf_blender import runtime  # noqa: E402
from quick_sdf_blender.model import SCHEMA_VERSION  # noqa: E402


def _assign_layer(item, role: str, image) -> None:
    fields = {
        runtime.DISPLAY_ROLE: ("display_image", "display_image_name"),
        runtime.BASE_ROLE: ("base_image", "base_image_name"),
        runtime.COVERAGE_ROLE: ("coverage_image", "coverage_image_name"),
    }
    pointer, name = fields[role]
    setattr(item, pointer, image)
    setattr(item, name, image.name)


def main() -> None:
    assert bpy.app.version[:2] == (5, 1), bpy.app.version_string
    assert SCHEMA_VERSION == 4
    quick_sdf_blender.register()
    try:
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        material = bpy.data.materials.new("Schema 4 Material")
        obj.data.materials.append(material)

        scene = bpy.context.scene
        project = scene.quick_sdf_projects.add()
        project.uuid = "schema-4-save-reload"
        project.name = "Schema 4 Face Shadow"
        project.schema_version = SCHEMA_VERSION
        project.guide_version = 2
        project.target_object = obj
        project.material_slot_index = 0
        project.uv_map_name = obj.data.uv_layers.active.name
        project.resolution = 512

        item = project.angles.add()
        item.uuid = "schema-4-angle"
        item.angle = 0.0
        item.side = "RIGHT"
        images = {}
        for role in (runtime.DISPLAY_ROLE, runtime.BASE_ROLE, runtime.COVERAGE_ROLE):
            image = runtime.create_angle_layer_image(
                project.uuid, item.uuid, 0.0, 4, role, side="RIGHT"
            )
            image["schema4_sentinel"] = role
            _assign_layer(item, role, image)
            images[role] = image

        scene.quick_sdf_active_project_index = 0
        with tempfile.TemporaryDirectory(prefix="quicksdf-schema4-") as directory:
            blend_path = Path(directory) / "schema4.blend"
            assert bpy.ops.wm.save_as_mainfile(filepath=str(blend_path)) == {"FINISHED"}
            assert bpy.ops.wm.open_mainfile(filepath=str(blend_path)) == {"FINISHED"}

        scene = bpy.context.scene
        project = scene.quick_sdf_projects[0]
        assert project.schema_version == 4
        assert project.guide_version == 2
        assert project.uuid == "schema-4-save-reload"
        item = project.angles[0]
        runtime.repair_project_references(scene)
        for role, resolver in (
            (runtime.DISPLAY_ROLE, runtime.resolve_display_image),
            (runtime.BASE_ROLE, runtime.resolve_base_image),
            (runtime.COVERAGE_ROLE, runtime.resolve_coverage_image),
        ):
            image = resolver(project, item)
            assert image is not None
            assert image.get("schema4_sentinel") == role
    finally:
        quick_sdf_blender.unregister()

    print("[Quick SDF schema-4 persistence smoke] PASS")


if __name__ == "__main__":
    main()

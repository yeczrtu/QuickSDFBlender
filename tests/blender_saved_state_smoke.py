"""Verify that an active Studio save opens as a clean, inactive project."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", type=Path, required=True)
    return parser.parse_args(values)


def run(blend_path: Path) -> None:
    path = blend_path.resolve()
    assert path.is_file(), path
    assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {"FINISHED"}
    assert bpy.ops.wm.open_mainfile(filepath=str(path)) == {"FINISHED"}

    from quick_sdf_blender import operators, preview, runtime, studio
    from quick_sdf_blender.live_preview import ONION_PREVIEW_ROLE, SEEK_PREVIEW_ROLE

    assert studio.current_session() is None
    assert operators._EXPORT_JOB is None
    assert not bpy.app.timers.is_registered(operators._poll_export_job)
    assert not any(obj.get(preview.RESTORE_KEY) for obj in bpy.data.objects)
    transient_roles = {
        runtime.EXPORT_ADJUSTMENT_ROLE,
        ONION_PREVIEW_ROLE,
        SEEK_PREVIEW_ROLE,
    }
    assert not any(image.get(runtime.ROLE_KEY) in transient_roles for image in bpy.data.images)

    projects = [
        project
        for scene in bpy.data.scenes
        for project in getattr(scene, "quick_sdf_projects", ())
    ]
    assert projects
    for project in projects:
        assert not project.author_active
        assert not project.job_running
        assert not project.preview_enabled
        assert not project.material_override_active
        assert project.export_adjustment_image is None
        assert project.export_adjustment_pixel_count == 0
        obj = project.target_object
        assert obj is not None
        slot = int(project.material_slot_index)
        assert 0 <= slot < len(obj.material_slots)
        assert obj.material_slots[slot].material == project.original_material

    assert bpy.context.scene.tool_settings.image_paint.use_normal_falloff is True
    assert not studio.is_studio_active(bpy.context)
    print(f"[Quick SDF saved-state smoke] PASS: {path}")


if __name__ == "__main__":
    run(_arguments().blend)

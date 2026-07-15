"""Verify that an active Studio save opens as a clean, inactive project."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--fingerprints", type=Path)
    return parser.parse_args(values)


def _source_fingerprints(project, runtime):
    records = []
    for item in project.angles:
        image = runtime.resolve_display_image(project, item)
        assert image is not None
        records.append(
            (
                "display",
                str(item.uuid),
                image.name,
                int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                hashlib.sha256(runtime.image_rgba(image).tobytes()).hexdigest(),
            )
        )
        for role in ("BASE", "COVERAGE"):
            records.append(
                (
                    role.lower(),
                    str(item.uuid),
                    runtime.bitplane_revision_token(item, role),
                    hashlib.sha256(runtime.bitplane_blob(item, role)).hexdigest(),
                )
            )
    return tuple(records)


def _freeze_json(value):
    """Restore tuple-shaped fingerprint tokens after their JSON round-trip."""

    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze_json(item)) for key, item in value.items()))
    return value


def run(blend_path: Path, fingerprints_path: Path | None = None) -> None:
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
        studio.PROVISIONAL_DISPLAY_ROLE,
    }
    assert not any(image.get(runtime.ROLE_KEY) in transient_roles for image in bpy.data.images)
    assert not any(tree.get(studio.TIMELINE_HOST_TAG) for tree in bpy.data.node_groups)
    assert not any(
        workspace.get(studio.PROJECTION_RECOVERY_KEY) for workspace in bpy.data.workspaces
    )

    projects = [
        project
        for scene in bpy.data.scenes
        for project in getattr(scene, "quick_sdf_projects", ())
    ]
    assert projects
    for project in projects:
        assert not project.job_running
        assert not project.preview_enabled
        assert not project.material_override_active
        assert not project.onion_enabled
        assert project.export_adjustment_image is None
        assert project.export_adjustment_pixel_count == 0
        obj = project.target_object
        assert obj is not None
        slot = int(project.material_slot_index)
        assert 0 <= slot < len(obj.material_slots)
        assert obj.material_slots[slot].material == project.original_material

    if fingerprints_path is not None:
        expected = _freeze_json(
            json.loads(fingerprints_path.resolve().read_text(encoding="utf-8"))
        )
        assert len(projects) == 1
        assert _source_fingerprints(projects[0], runtime) == expected

    assert bpy.context.scene.tool_settings.image_paint.use_normal_falloff is True
    clip_starts = [
        float(area.spaces.active.clip_start)
        for screen in bpy.data.screens
        for area in screen.areas
        if area.type == "VIEW_3D" and hasattr(area.spaces.active, "clip_start")
    ]
    assert clip_starts and min(clip_starts) >= 0.009
    assert not studio.is_studio_active(bpy.context)
    print(f"[Quick SDF saved-state smoke] PASS: {path}")


if __name__ == "__main__":
    arguments = _arguments()
    run(arguments.blend, arguments.fingerprints)

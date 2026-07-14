from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import bpy
import numpy as np


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--resolution", type=int, required=True)
    parser.add_argument("--keys", type=int, required=True)
    parser.add_argument("--lane-mode", choices=("MIRROR", "INDEPENDENT"), required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(values)


def _timed(results: dict[str, float], name: str, callback):
    started = time.perf_counter()
    value = callback()
    results[name] = time.perf_counter() - started
    return value


def _make_target() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("QSDF Performance Mesh")
    mesh.from_pydata(
        [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
        [],
        [(0, 1, 2), (0, 2, 3)],
    )
    uv = mesh.uv_layers.new(name="UVMap")
    coordinates = ((0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1))
    for loop, value in zip(uv.data, coordinates):
        loop.uv = value
    material = bpy.data.materials.new("QSDF Performance Material")
    mesh.materials.append(material)
    obj = bpy.data.objects.new("QSDF Performance Target", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def _add_keys(project, runtime, total_keys: int, lane_mode: str) -> None:
    per_side = total_keys if lane_mode == "MIRROR" else max(2, total_keys // 2)
    per_side = min(16, per_side)
    wanted_sides = ("RIGHT",) if lane_mode == "MIRROR" else ("RIGHT", "LEFT")
    existing = list(project.angles)
    for item in existing:
        image = runtime.resolve_display_image(project, item)
        if image is not None:
            bpy.data.images.remove(image)
    project.angles.clear()
    resolution = int(project.resolution)
    empty_coverage = np.zeros((resolution, resolution), dtype=np.bool_)
    for side in wanted_sides:
        for angle in np.linspace(0.0, 90.0, per_side):
            item = project.angles.add()
            item.uuid = runtime.new_uuid()
            item.angle = float(angle)
            item.side = side
            image = runtime.create_angle_layer_image(
                project.uuid,
                item.uuid,
                float(angle),
                resolution,
                runtime.DISPLAY_ROLE,
                side=side,
            )
            item.display_image = image
            item.display_image_name = image.name
            # Deterministic monotonic half-plane without allocating RGBA here.
            boundary = int(round((float(angle) / 90.0) * resolution))
            mask = np.zeros((resolution, resolution), dtype=np.bool_)
            mask[:, :boundary] = True
            runtime.write_image_gray8(image, mask.astype(np.uint8) * np.uint8(255))
            runtime.set_base_mask(item, mask)
            runtime.set_coverage_mask(item, empty_coverage)
    project.active_angle_index = min(1, len(project.angles) - 1)
    project.active_angle_uuid = project.angles[project.active_angle_index].uuid
    project.active_side = "RIGHT"
    project.mirror_enabled = lane_mode == "MIRROR"
    project.symmetry_mode = "TEXTURE_MIRROR" if project.mirror_enabled else "INDEPENDENT"


def main() -> None:
    args = _arguments()
    repository = str(Path(args.repository).resolve())
    if repository not in sys.path:
        sys.path.insert(0, repository)
    import quick_sdf_blender
    from quick_sdf_blender import operators, runtime

    quick_sdf_blender.register()
    results: dict[str, float] = {}
    try:
        _make_target()
        settings = bpy.context.scene.quick_sdf_settings
        settings.resolution = str(args.resolution)
        settings.initialization = "WHITE"
        project = _timed(
            results,
            "project_create_seconds",
            lambda: operators._create_project_data(bpy.context, sync_ui=False),
        )
        _timed(
            results,
            "key_materialize_seconds",
            lambda: _add_keys(project, runtime, args.keys, args.lane_mode),
        )

        def switch_all() -> None:
            for index, item in enumerate(project.angles):
                project.active_angle_index = index
                project.active_angle_uuid = item.uuid
                runtime.sync_canvas(bpy.context, project)

        _timed(results, "cold_key_cycle_seconds", switch_all)
        _timed(results, "warm_key_cycle_seconds", switch_all)

        def gray_roundtrip() -> None:
            item = runtime.active_angle(project)
            image = runtime.resolve_display_image(project, item)
            gray = runtime.image_gray8(image)
            runtime.write_image_gray8(image, gray)

        _timed(results, "display_gray_roundtrip_seconds", gray_roundtrip)

        inputs = _timed(
            results,
            "export_snapshot_seconds",
            lambda: operators._build_export_inputs(project),
        )
        _timed(
            results,
            "export_compute_seconds",
            lambda: operators._compute_export_result(inputs),
        )
        output = {
            "resolution": args.resolution,
            "keys": args.keys,
            "lane_mode": args.lane_mode,
            "timings": results,
            "native_abi": int(__import__("quick_sdf_blender.native", fromlist=["version"]).version()),
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
    finally:
        quick_sdf_blender.unregister()


if __name__ == "__main__":
    main()

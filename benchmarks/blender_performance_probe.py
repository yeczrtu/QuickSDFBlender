from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
import tempfile
import time

import bpy
import numpy as np


_STAGE_PATH: Path | None = None


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--resolution", type=int, required=True)
    parser.add_argument("--keys", type=int, required=True)
    parser.add_argument("--lane-mode", choices=("MIRROR", "INDEPENDENT"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--stage-file", default="")
    return parser.parse_args(values)


def _set_stage(value: str) -> None:
    if _STAGE_PATH is not None:
        _STAGE_PATH.write_text(value, encoding="utf-8")


def _timed(results: dict[str, float], name: str, callback):
    _set_stage(name)
    started = time.perf_counter()
    try:
        value = callback()
        results[name] = time.perf_counter() - started
        return value
    finally:
        _set_stage(f"{name}:complete")


def _memory_checkpoint(name: str, seconds: float = 0.25) -> None:
    _set_stage(name)
    time.sleep(max(0.05, float(seconds)))


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
    reusable = {
        side: sorted(
            (item for item in project.angles if str(item.side) == side),
            key=lambda item: float(item.angle),
        )
        for side in ("RIGHT", "LEFT")
    }
    used_uuids: set[str] = set()
    resolution = int(project.resolution)
    empty_coverage = np.zeros((resolution, resolution), dtype=np.bool_)
    for side in wanted_sides:
        for angle in np.linspace(0.0, 90.0, per_side):
            if reusable[side]:
                item = reusable[side].pop(0)
                image = runtime.resolve_display_image(project, item)
            else:
                item = project.angles.add()
                item.uuid = runtime.new_uuid()
                image = None
            item.angle = float(angle)
            item.side = side
            used_uuids.add(str(item.uuid))
            if image is None:
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
    for index in reversed(range(len(project.angles))):
        item = project.angles[index]
        if str(item.uuid) in used_uuids:
            continue
        image = runtime.resolve_display_image(project, item)
        project.angles.remove(index)
        if image is not None:
            bpy.data.images.remove(image)
    project.active_angle_index = min(1, len(project.angles) - 1)
    project.active_angle_uuid = project.angles[project.active_angle_index].uuid
    project.active_side = "RIGHT"
    project.mirror_enabled = lane_mode == "MIRROR"
    project.symmetry_mode = "TEXTURE_MIRROR" if project.mirror_enabled else "INDEPENDENT"


def main() -> None:
    global _STAGE_PATH

    args = _arguments()
    _STAGE_PATH = Path(args.stage_file).resolve() if args.stage_file else None
    _memory_checkpoint("startup_baseline")
    repository = str(Path(args.repository).resolve())
    if repository not in sys.path:
        sys.path.insert(0, repository)
    import quick_sdf_blender
    from quick_sdf_blender import live_preview, operators, preview_cache, residency, runtime

    quick_sdf_blender.register()
    results: dict[str, float] = {}
    try:
        _make_target()
        settings = bpy.context.scene.quick_sdf_settings
        settings.resolution = int(args.resolution)
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
        residency.flush_dirty(str(project.uuid))
        runtime.sync_canvas(bpy.context, project)
        active_image = runtime.resolve_display_image(project, runtime.active_angle(project))
        residency.reconcile_project(project, active_image)

        def select_index(index: int) -> None:
            item = project.angles[int(index)]
            project.active_angle_index = int(index)
            project.active_angle_uuid = item.uuid
            runtime.sync_canvas(bpy.context, project)

        _timed(
            results,
            "cold_key_switch_seconds",
            lambda: select_index(len(project.angles) - 1),
        )
        _timed(
            results,
            "warm_key_switch_seconds",
            lambda: select_index(max(0, len(project.angles) - 2)),
        )

        def switch_all() -> None:
            for index, item in enumerate(project.angles):
                project.active_angle_index = index
                project.active_angle_uuid = item.uuid
                runtime.sync_canvas(bpy.context, project)

        _timed(results, "cold_key_cycle_seconds", switch_all)
        _timed(results, "warm_key_cycle_seconds", switch_all)

        def thumbnail_cycle() -> None:
            bbox = tuple(float(value) for value in project.thumbnail_uv_bbox)
            for item in project.angles:
                image = runtime.resolve_display_image(project, item)
                preview_cache.thumbnail_rgba8(image, bbox)

        preview_cache.invalidate()
        _timed(results, "cold_thumbnail_cycle_seconds", thumbnail_cycle)
        _timed(results, "warm_thumbnail_cycle_seconds", thumbnail_cycle)

        seek_angle = 33.0
        _timed(
            results,
            "first_seek_seconds",
            lambda: live_preview.update_seek_preview(project, seek_angle),
        )
        _timed(
            results,
            "warm_seek_seconds",
            lambda: live_preview.update_seek_preview(project, seek_angle),
        )

        # Studio keeps the finalized Active Display in its gray8 session cache.
        # Warm that cache outside the timed region so this probe measures the
        # real stroke snapshot/upload path, not a cold diagnostic pixel read.
        roundtrip_item = runtime.active_angle(project)
        roundtrip_image = runtime.resolve_display_image(project, roundtrip_item)
        runtime.cache_image_gray8(
            roundtrip_image,
            runtime.image_gray8(roundtrip_image),
        )

        def gray_roundtrip() -> None:
            gray = runtime.image_gray8(roundtrip_image, use_cache=True)
            runtime.write_image_gray8(roundtrip_image, gray)

        _timed(results, "display_gray_roundtrip_seconds", gray_roundtrip)

        _timed(
            results,
            "rebake_seconds",
            lambda: operators._bake_project(bpy.context, project),
        )

        _timed(
            results,
            "residency_pack_seconds",
            lambda: residency.flush_dirty(str(project.uuid)),
        )
        active = runtime.resolve_display_image(project, runtime.active_angle(project))
        residency.reconcile_project(project, active)
        gc.collect()
        # Give Blender one idle interval to finish deferred GPU/cache release;
        # shorter probes mostly measure allocator high-water from Rebake.
        _memory_checkpoint("residency_steady", 2.0)

        project_images = [
            image
            for image in bpy.data.images
            if str(image.get(runtime.PROJECT_UUID_KEY, "")) == str(project.uuid)
        ]
        image_cpu_bytes = sum(
            int(image.size[0]) * int(image.size[1]) * 16
            for image in project_images
            if bool(getattr(image, "has_data", False))
        )
        packed_image_bytes = sum(
            int(getattr(getattr(item, "packed_file", None), "size", 0))
            for image in project_images
            for item in getattr(image, "packed_files", ())
        )
        bitplane_blob_bytes = sum(
            len(runtime.bitplane_blob(item, role))
            for item in project.angles
            for role in ("BASE", "COVERAGE")
        )
        preview_stats = preview_cache.cache_statistics()
        gray_cache_bytes = int(getattr(runtime._GRAY_CACHE_VALUE, "nbytes", 0))
        upload_buffer_bytes = int(getattr(runtime._GRAY_UPLOAD_BUFFER, "nbytes", 0))
        history_bytes = sum(
            int(history.bytes_used) for history in operators._HISTORIES.values()
        )
        accounted_memory = {
            "resident_image_cpu_bytes": image_cpu_bytes,
            "active_display_gpu_estimate_bytes": (
                int(active.size[0]) * int(active.size[1]) * 4 if active is not None else 0
            ),
            "packed_image_bytes": packed_image_bytes,
            "bitplane_blob_bytes": bitplane_blob_bytes,
            "decoded_bitplane_cache_bytes": int(runtime._BITPLANE_CACHE.bytes_used),
            "preview_cpu_bytes": int(preview_stats["cpu_bytes"]),
            "preview_gpu_bytes": int(preview_stats["gpu_bytes"]),
            "active_gray_cache_bytes": gray_cache_bytes,
            "gray_upload_buffer_bytes": upload_buffer_bytes,
            "history_bytes": history_bytes,
        }
        accounted_memory["total_bytes"] = sum(accounted_memory.values())

        inputs = _timed(
            results,
            "export_snapshot_seconds",
            lambda: operators._prepare_packed_threshold_inputs(project),
        )
        _timed(
            results,
            "export_compute_seconds",
            lambda: operators._compute_export_result(inputs),
        )
        resident_display_images = sum(
            1
            for item in project.angles
            if bool(getattr(runtime.resolve_display_image(project, item), "has_data", False))
        )
        total_display_images = len(project.angles)
        resident_images_by_role: dict[str, int] = {}
        packed_bytes_by_role: dict[str, int] = {}
        for image in bpy.data.images:
            if str(image.get(runtime.PROJECT_UUID_KEY, "")) != str(project.uuid):
                continue
            role = str(image.get(runtime.ROLE_KEY, "untagged"))
            if bool(getattr(image, "has_data", False)):
                resident_images_by_role[role] = resident_images_by_role.get(role, 0) + 1
            packed_size = sum(
                int(getattr(getattr(item, "packed_file", None), "size", 0))
                for item in getattr(image, "packed_files", ())
            )
            packed_bytes_by_role[role] = packed_bytes_by_role.get(role, 0) + packed_size
        with tempfile.TemporaryDirectory(prefix="quicksdf-benchmark-") as temporary:
            export_path = Path(temporary) / "performance.png"

            def export_file() -> None:
                result = operators._compute_export_file_result(inputs, export_path)
                Path(result["temporary_path"]).unlink()

            _timed(results, "export_file_seconds", export_file)
            blend_path = Path(temporary) / "performance.blend"

            def save_reload() -> None:
                bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
                bpy.ops.wm.open_mainfile(filepath=str(blend_path), load_ui=False)

            _timed(results, "save_reload_seconds", save_reload)
        _set_stage("complete")
        output = {
            "resolution": args.resolution,
            "keys": args.keys,
            "lane_mode": args.lane_mode,
            "timings": results,
            "native_abi": int(__import__("quick_sdf_blender.native", fromlist=["version"]).version()),
            "resident_display_images": resident_display_images,
            "total_display_images": total_display_images,
            "resident_images_by_role": resident_images_by_role,
            "packed_bytes_by_role": packed_bytes_by_role,
            "accounted_memory": accounted_memory,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
    finally:
        quick_sdf_blender.unregister()


if __name__ == "__main__":
    main()

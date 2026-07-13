"""End-to-end Blender 5.1 smoke test for the Quick SDF extension.

Run from the repository root with::

    blender --background --factory-startup --python tests/blender_smoke.py

The script deliberately drives the public Blender operators rather than calling
their implementations directly.  Artifacts are written to ``build`` by default.
"""

from __future__ import annotations

import argparse
import binascii
from pathlib import Path
import struct
import sys
import zlib


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import bpy  # noqa: E402  (Blender-only script)
import numpy as np  # noqa: E402

import quick_sdf_blender  # noqa: E402
from quick_sdf_blender import runtime, studio  # noqa: E402


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "build",
        help="Directory for the smoke-test blend file and RGBA16 PNG",
    )
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(arguments)


def _expect_finished(result: set[str], operation: str) -> None:
    assert result == {"FINISHED"}, f"{operation} returned {result!r}"


def _make_uv_cube() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    _expect_finished(bpy.ops.mesh.primitive_cube_add(), "create cube")
    cube = bpy.context.active_object
    assert cube is not None and cube.type == "MESH"
    cube.name = "Quick SDF Smoke Cube"

    material = bpy.data.materials.new("Quick SDF Smoke Material")
    cube.data.materials.append(material)
    for polygon in cube.data.polygons:
        polygon.material_index = 0

    uv_layer = cube.data.uv_layers.new(name="QuickSDFSmokeUV")
    quad_uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for polygon in cube.data.polygons:
        for corner, loop_index in enumerate(polygon.loop_indices):
            uv_layer.data[loop_index].uv = quad_uvs[corner % len(quad_uvs)]
    cube.data.uv_layers.active = uv_layer
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    return cube


def _decode_rgba16(path: Path) -> tuple[dict[str, int], np.ndarray]:
    encoded = path.read_bytes()
    assert encoded.startswith(PNG_SIGNATURE), "export is not a PNG file"
    position = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while position < len(encoded):
        length = struct.unpack_from(">I", encoded, position)[0]
        kind = encoded[position + 4 : position + 8]
        payload_start = position + 8
        payload = encoded[payload_start : payload_start + length]
        checksum = struct.unpack_from(">I", encoded, payload_start + length)[0]
        assert checksum == (binascii.crc32(kind + payload) & 0xFFFFFFFF), (
            f"invalid {kind!r} CRC"
        )
        chunks.append((kind, payload))
        position += length + 12
    assert position == len(encoded), "PNG contains a truncated chunk"

    ihdr = next(payload for kind, payload in chunks if kind == b"IHDR")
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    raw = zlib.decompress(b"".join(payload for kind, payload in chunks if kind == b"IDAT"))
    stride = width * 8 + 1
    assert len(raw) == height * stride
    rows: list[bytes] = []
    for row in range(height):
        start = row * stride
        assert raw[start] == 0, "smoke decoder expects PNG filter type 0"
        rows.append(raw[start + 1 : start + stride])
    pixels = np.frombuffer(b"".join(rows), dtype=">u2").reshape(height, width, 4)
    header = {
        "width": width,
        "height": height,
        "depth": depth,
        "color_type": color_type,
        "compression": compression,
        "filtering": filtering,
        "interlace": interlace,
    }
    return header, pixels.astype(np.uint16)


def _assert_canvas(project: object) -> None:
    angle_item = project.angles[int(project.active_angle_index)]
    image = runtime.resolve_angle_image(project, angle_item)
    assert image is not None
    assert bpy.context.scene.tool_settings.image_paint.canvas == image
    assert image.get(runtime.PROJECT_UUID_KEY) == project.uuid
    assert image.get(runtime.ANGLE_UUID_KEY) == angle_item.uuid


def run(output_directory: Path) -> None:
    assert bpy.app.version[:2] == (5, 1), (
        f"this smoke test targets Blender 5.1, got {bpy.app.version_string}"
    )
    output_directory = output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    blend_path = output_directory / "quick_sdf_smoke.blend"
    png_path = output_directory / "quick_sdf_smoke_rgba16.png"
    if blend_path.exists():
        blend_path.unlink()
    if png_path.exists():
        png_path.unlink()

    print("[Quick SDF smoke] enable through Blender preferences")
    # This is deliberately not a direct ``register()`` call.  Blender wraps
    # add-on activation in ``RestrictBlend`` and exposes ``bpy.data`` as
    # ``_RestrictData`` during registration; the production enable path must
    # remain free of datablock creation.
    _expect_finished(
        bpy.ops.preferences.addon_enable(module="quick_sdf_blender"),
        "addon_enable",
    )
    assert "quick_sdf_blender" in bpy.context.preferences.addons
    assert hasattr(bpy.types.Scene, "quick_sdf_projects")
    assert hasattr(bpy.types, "QUICKSDF_OT_project_create")
    assert bpy.app.handlers.load_post.count(runtime._load_or_undo_post) == 1
    assert bpy.app.handlers.save_pre.count(runtime._save_project_images) == 1
    assert bpy.app.handlers.depsgraph_update_post.count(runtime._depsgraph_base_update) == 1
    assert bpy.app.handlers.frame_change_post.count(runtime._frame_base_update) == 1

    cube = _make_uv_cube()
    scene = bpy.context.scene
    scene.quick_sdf_settings.resolution = 512
    scene.quick_sdf_settings.initialization = "NORMAL_SWEEP"

    print("[Quick SDF smoke] create 512px project and synchronize canvas")
    _expect_finished(bpy.ops.quicksdf.project_create(), "project_create")
    assert len(scene.quick_sdf_projects) == 1
    project = scene.quick_sdf_projects[0]
    assert project.target_object == cube
    assert project.resolution == 512
    assert project.schema_version == 2
    assert len(project.angles) == 7
    assert [round(item.angle) for item in project.angles] == list(range(0, 91, 15))
    assert {item.side for item in project.angles} == {"RIGHT"}
    assert not project.author_active
    assert not project.base_needs_update
    original_base_signature = project.base_signature
    assert original_base_signature
    cube.data.vertices[0].co.x += 0.01
    assert runtime.refresh_base_staleness(project, scene)
    assert project.base_needs_update
    cube.data.vertices[0].co.x -= 0.01
    project.base_needs_update = False
    project.base_signature = runtime.compute_base_signature(project, scene)
    assert studio.current_session() is None
    _assert_canvas(project)
    # Studio is deliberately interactive-only. A headless smoke run must not
    # publish a half-active session or persist a Stop state.
    try:
        result = bpy.ops.quicksdf.studio_enter()
    except RuntimeError as error:
        assert "interactive Blender window" in str(error)
    else:
        assert result == {"CANCELLED"}
    assert studio.current_session() is None

    for angle_item in project.angles:
        display = runtime.resolve_display_image(project, angle_item)
        base = runtime.resolve_base_image(project, angle_item)
        coverage = runtime.resolve_coverage_image(project, angle_item)
        assert display is not None and base is not None and coverage is not None
        assert display.alpha_mode == "NONE"
        assert base.alpha_mode == "NONE"
        assert coverage.alpha_mode == "NONE"
        assert np.all(runtime.image_rgba(display)[..., 3] == 1.0)
        assert np.all(runtime.image_rgba(base)[..., 3] == 1.0)
        assert not np.any(runtime.coverage_mask(coverage))

    original_canvas = scene.tool_settings.image_paint.canvas
    _expect_finished(bpy.ops.quicksdf.angle_step(step=1), "angle_step")
    assert scene.tool_settings.image_paint.canvas != original_canvas
    _expect_finished(bpy.ops.quicksdf.angle_set(index=-1), "angle_set front")
    assert project.angles[project.active_angle_index].angle == 0.0
    _assert_canvas(project)

    print("[Quick SDF smoke] add and remove a boundary track")
    _expect_finished(bpy.ops.quicksdf.boundary_track_add(), "boundary_track_add")
    assert len(project.boundary_tracks) == 1
    assert len(project.boundary_tracks[0].keys) == 1
    _expect_finished(bpy.ops.quicksdf.boundary_track_remove(), "boundary_track_remove")
    assert len(project.boundary_tracks) == 0

    print("[Quick SDF smoke] apply one Smart Paint stroke and one history action")
    # Pick a semantic/range guaranteed to change the sampled pixel while
    # preserving closure: Shadow at 90 reaches every key when the pixel is
    # currently Light; Light at 0 reaches every key otherwise.
    front = runtime.resolve_angle_image(project, project.angles[0])
    current_light = bool(runtime.image_mask(front)[256, 256])
    selected_index = 6 if current_light else 0
    project.paint_value = 0 if current_light else 1
    _expect_finished(bpy.ops.quicksdf.key_select(index=selected_index), "key_select")
    source_image = runtime.resolve_angle_image(project, project.angles[project.active_angle_index])
    assert source_image is not None
    source_mask = runtime.image_mask(source_image)
    footprint = np.zeros(source_mask.shape, dtype=np.bool_)
    footprint[255:257, 255:257] = True
    runtime.capture_paint_snapshot(project)
    painted = runtime.image_rgba(source_image)
    painted[..., :3][footprint] = float(project.paint_value)
    runtime.write_image_rgba(source_image, painted)
    _expect_finished(bpy.ops.quicksdf.propagate_overrides(), "propagate_overrides")
    for angle_item in project.angles:
        display = runtime.resolve_display_image(project, angle_item)
        coverage = runtime.resolve_coverage_image(project, angle_item)
        assert display is not None and coverage is not None
        assert np.all(runtime.coverage_mask(coverage)[255:257, 255:257])
        assert np.all(runtime.image_rgba(display)[..., 3] == 1.0)
    _expect_finished(bpy.ops.quicksdf.history_undo(), "history_undo")
    _expect_finished(bpy.ops.quicksdf.history_redo(), "history_redo")

    print("[Quick SDF smoke] preview material is reversible")
    original_material = cube.material_slots[0].material
    original_link = cube.material_slots[0].link
    _expect_finished(bpy.ops.quicksdf.preview_enable(), "preview_enable")
    preview_material = cube.material_slots[0].material
    assert preview_material is not None and preview_material != original_material
    assert preview_material.node_tree.nodes.get("QSDF Original Overlay") is not None
    _expect_finished(bpy.ops.quicksdf.preview_disable(), "preview_disable")
    assert cube.material_slots[0].material == original_material
    assert cube.material_slots[0].link == original_link

    print("[Quick SDF smoke] validate, generate, and export RGBA16 PNG")
    _expect_finished(bpy.ops.quicksdf.validate(), "validate")
    assert not project.has_violations
    assert project.validation_message == "OK"
    _expect_finished(bpy.ops.quicksdf.generate(), "generate")
    assert project.generated_image is not None
    assert project.generated_image.get(runtime.PROJECT_UUID_KEY) == project.uuid
    assert project.generated_image.get(runtime.ROLE_KEY) == runtime.THRESHOLD_ROLE
    _expect_finished(
        bpy.ops.quicksdf.export_texture(filepath=str(png_path), overwrite=True),
        "export_texture",
    )
    assert png_path.is_file()
    header, pixels = _decode_rgba16(png_path)
    assert header == {
        "width": 512,
        "height": 512,
        "depth": 16,
        "color_type": 6,
        "compression": 0,
        "filtering": 0,
        "interlace": 0,
    }
    assert np.all(pixels[..., 2] == 0)
    assert np.all(pixels[..., 3] == 65535)
    assert np.all((pixels[..., 0] >= 0) & (pixels[..., 0] <= 65535))
    assert np.all((pixels[..., 1] >= 0) & (pixels[..., 1] <= 65535))

    print("[Quick SDF smoke] save/reload and repair UUID-backed references")
    project_uuid = project.uuid
    angle_uuids = tuple(item.uuid for item in project.angles)
    angle_names = tuple(item.display_image_name for item in project.angles)
    base_names = tuple(item.base_image_name for item in project.angles)
    coverage_names = tuple(item.coverage_image_name for item in project.angles)
    expected_painted_pixels = tuple(
        runtime.image_rgba(runtime.resolve_display_image(project, item))[255, 255, :3].copy()
        for item in project.angles
    )
    expected_base_pixels = tuple(
        runtime.image_rgba(runtime.resolve_base_image(project, item))[255, 255, :3].copy()
        for item in project.angles
    )
    generated_name = project.generated_image.name
    _expect_finished(bpy.ops.wm.save_as_mainfile(filepath=str(blend_path)), "save blend")
    _expect_finished(bpy.ops.wm.open_mainfile(filepath=str(blend_path)), "reload blend")

    scene = bpy.context.scene
    assert len(scene.quick_sdf_projects) == 1
    project = runtime.active_project(scene)
    assert project is not None and project.uuid == project_uuid
    assert tuple(item.uuid for item in project.angles) == angle_uuids
    assert tuple(item.display_image_name for item in project.angles) == angle_names
    assert tuple(item.base_image_name for item in project.angles) == base_names
    assert tuple(item.coverage_image_name for item in project.angles) == coverage_names
    assert not project.author_active
    assert studio.current_session() is None
    for index, angle_item in enumerate(project.angles):
        image = runtime.resolve_angle_image(project, angle_item)
        assert image is not None and angle_item.image == image
        assert image.get(runtime.PROJECT_UUID_KEY) == project_uuid
        assert image.get(runtime.ANGLE_UUID_KEY) == angle_item.uuid
        base = runtime.resolve_base_image(project, angle_item)
        coverage = runtime.resolve_coverage_image(project, angle_item)
        assert base is not None
        assert coverage is not None
        np.testing.assert_array_equal(
            runtime.image_rgba(image)[255, 255, :3], expected_painted_pixels[index]
        )
        np.testing.assert_array_equal(
            runtime.image_rgba(base)[255, 255, :3], expected_base_pixels[index]
        )
        assert runtime.coverage_mask(coverage)[255, 255]
        assert np.all(runtime.image_rgba(image)[..., 3] == 1.0)
    assert project.generated_image is not None
    assert project.generated_image.name == generated_name
    assert project.generated_image.get(runtime.PROJECT_UUID_KEY) == project_uuid
    assert project.generated_image.get(runtime.ROLE_KEY) == runtime.THRESHOLD_ROLE
    _assert_canvas(project)
    _expect_finished(bpy.ops.quicksdf.validate(), "validate after reload")
    assert project.validation_message == "OK"

    print("[Quick SDF smoke] remove project, then disable/re-enable")
    _expect_finished(bpy.ops.quicksdf.project_remove(), "project_remove")
    assert len(scene.quick_sdf_projects) == 0
    assert not any(
        image.get(runtime.PROJECT_UUID_KEY) == project_uuid for image in bpy.data.images
    )

    _expect_finished(
        bpy.ops.preferences.addon_disable(module="quick_sdf_blender"),
        "addon_disable",
    )
    assert not hasattr(bpy.types.Scene, "quick_sdf_projects")
    assert not hasattr(bpy.types, "QUICKSDF_OT_project_create")
    assert runtime._load_or_undo_post not in bpy.app.handlers.load_post
    assert runtime._save_project_images not in bpy.app.handlers.save_pre
    assert runtime._depsgraph_base_update not in bpy.app.handlers.depsgraph_update_post
    assert runtime._frame_base_update not in bpy.app.handlers.frame_change_post
    _expect_finished(
        bpy.ops.preferences.addon_enable(module="quick_sdf_blender"),
        "addon re-enable",
    )
    assert hasattr(bpy.types.Scene, "quick_sdf_projects")
    assert hasattr(bpy.types, "QUICKSDF_OT_project_create")
    assert bpy.app.handlers.load_post.count(runtime._load_or_undo_post) == 1
    assert bpy.app.handlers.save_pre.count(runtime._save_project_images) == 1
    assert bpy.app.handlers.depsgraph_update_post.count(runtime._depsgraph_base_update) == 1
    assert bpy.app.handlers.frame_change_post.count(runtime._frame_base_update) == 1
    assert len(bpy.context.scene.quick_sdf_projects) == 0

    print(f"[Quick SDF smoke] PASS: {png_path}")
    print(f"[Quick SDF smoke] PASS: {blend_path}")


if __name__ == "__main__":
    run(_arguments().output_dir)

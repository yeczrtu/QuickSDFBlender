"""End-to-end Blender 5.1 smoke test for the Quick SDF extension.

Run from the repository root with::

    blender --background --factory-startup --python tests/blender_smoke.py

The script deliberately drives the public Blender operators rather than calling
their implementations directly.  Artifacts are written to ``build`` by default.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
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


def _assert_export_worker_side_contracts() -> None:
    from quick_sdf_blender import operators
    from quick_sdf_blender.core import generate_threshold_pair
    from quick_sdf_blender.symmetry import IslandPair, mirror_side_stack

    angles = np.asarray([0.0, 45.0, 90.0], dtype=np.float64)
    right_transition = np.asarray([[0, 1, 3, 2], [3, 2, 1, 0]])
    left_transition = np.asarray([[3, 1, 2, 0], [1, 3, 0, 2]])
    right = np.arange(3)[:, None, None] >= right_transition[None, ...]
    left = np.arange(3)[:, None, None] >= left_transition[None, ...]
    coverage = np.zeros_like(right)
    independent = operators._compute_export_result(
        {
            "linked": False,
            "right": (right, angles, ~right, coverage),
            "left": (left, angles, np.roll(left, 1, axis=0), coverage),
        }
    )
    np.testing.assert_array_equal(
        independent["rgba"], generate_threshold_pair(right, angles, left, angles)
    )
    assert independent["changed_pixel_count"] == 0

    full = np.ones(right.shape[1:], dtype=np.bool_)
    for mode, pairs in (
        ("OVERLAPPED", None),
        ("TEXTURE_MIRROR", None),
        ("ISLAND_PAIR", [IslandPair(full, full)]),
    ):
        mirrored = mirror_side_stack(left, mode, island_pairs=pairs)
        linked_left = operators._compute_export_result(
            {
                "linked": True,
                "author_side": "LEFT",
                "source": (left, angles, ~left, coverage),
                "mirror_mode": mode,
                "island_pairs": pairs,
            }
        )
        np.testing.assert_array_equal(
            linked_left["rgba"],
            generate_threshold_pair(mirrored, angles, left, angles),
        )
        assert linked_left["changed_pixel_count"] == 0


def run(output_directory: Path) -> None:
    assert bpy.app.version[:2] == (5, 1), (
        f"this smoke test targets Blender 5.1, got {bpy.app.version_string}"
    )
    output_directory = output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    blend_path = output_directory / "quick_sdf_smoke.blend"
    png_path = output_directory / "quick_sdf_smoke_rgba16.png"
    repaired_png_path = output_directory / "quick_sdf_smoke_repaired_rgba16.png"
    if blend_path.exists():
        blend_path.unlink()
    if png_path.exists():
        png_path.unlink()
    if repaired_png_path.exists():
        repaired_png_path.unlink()

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
    assert project.schema_version == 3
    assert project.base_source == "NORMAL_GUIDE"
    assert project.guide_version == 1
    assert len(project.angles) == 7
    assert [round(item.angle) for item in project.angles] == list(range(0, 91, 15))
    assert {item.side for item in project.angles} == {"RIGHT"}
    assert not project.author_active
    assert not project.base_needs_update
    assert project.angles[project.active_angle_index].angle == 45.0
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
    source_before = runtime.image_rgba(source_image)
    footprint = np.zeros(source_mask.shape, dtype=np.bool_)
    footprint[255:257, 255:257] = True
    runtime.capture_paint_snapshot(project)
    painted = runtime.image_rgba(source_image)
    painted[..., :3][footprint] = float(project.paint_value)
    assert np.all(painted[255:257, 255:257, :3] == float(project.paint_value))
    runtime.write_image_rgba(source_image, painted)
    source_after = runtime.image_rgba(source_image)
    assert np.any(source_after[..., :3] != source_before[..., :3]), (
        current_light,
        selected_index,
        project.paint_value,
        source_before[256, 256, :3],
        source_after[256, 256, :3],
        source_image.source,
        bool(source_image.packed_file),
    )
    _expect_finished(bpy.ops.quicksdf.propagate_overrides(), "propagate_overrides")
    for angle_item in project.angles:
        display = runtime.resolve_display_image(project, angle_item)
        coverage = runtime.resolve_coverage_image(project, angle_item)
        assert display is not None and coverage is not None
        assert np.all(runtime.coverage_mask(coverage)[255:257, 255:257])
        assert np.all(runtime.image_rgba(display)[..., 3] == 1.0)
    from quick_sdf_blender import operators as operator_module

    history = operator_module._HISTORIES[str(project.uuid)]
    transaction_before = {
        name: runtime.image_rgba8(bpy.data.images[name]) for name in history.undo_keys
    }
    undo_count_before = history.undo_count
    original_write_rgba8 = runtime.write_image_rgba8
    write_calls = 0

    def fail_second_history_write(image, rgba):
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            raise RuntimeError("simulated second-image write failure")
        return original_write_rgba8(image, rgba)

    runtime.write_image_rgba8 = fail_second_history_write
    try:
        try:
            bpy.ops.quicksdf.history_undo()
        except RuntimeError as exc:
            assert "simulated second-image write failure" in str(exc)
        else:
            raise AssertionError("The simulated history write failure was not reported")
    finally:
        runtime.write_image_rgba8 = original_write_rgba8
    assert history.undo_count == undo_count_before
    for name, expected in transaction_before.items():
        np.testing.assert_array_equal(runtime.image_rgba8(bpy.data.images[name]), expected)
    _expect_finished(bpy.ops.quicksdf.history_undo(), "history_undo")
    _expect_finished(bpy.ops.quicksdf.history_redo(), "history_redo")

    print("[Quick SDF smoke] roll back a partial legacy Smart Paint collection")
    # Reproduce a compatibility-path failure after an earlier key has already
    # populated ``before_history`` but before the active (90 degree) canvas is
    # captured. A cancelled propagation must still remove the native stroke.
    _expect_finished(bpy.ops.quicksdf.key_select(index=6), "key_select legacy rollback")
    legacy_source = runtime.resolve_display_image(project, project.angles[6])
    fail_display = runtime.resolve_display_image(project, project.angles[1])
    assert legacy_source is not None and fail_display is not None
    legacy_original = runtime.image_rgba(legacy_source)
    legacy_region = np.zeros(runtime.image_mask(legacy_source).shape, dtype=np.bool_)
    legacy_region[200:202, 200:202] = True
    legacy_light = runtime.image_rgba(legacy_source)
    legacy_light[..., :3][legacy_region] = 1.0
    legacy_light[..., 3] = 1.0
    runtime.write_image_rgba(legacy_source, legacy_light)
    runtime.capture_paint_snapshot(project)
    legacy_snapshot = runtime.image_rgba(legacy_source)
    legacy_shadow = legacy_snapshot.copy()
    legacy_shadow[..., :3][legacy_region] = 0.0
    runtime.write_image_rgba(legacy_source, legacy_shadow)
    assert np.any(runtime.image_rgba(legacy_source) != legacy_snapshot)

    original_image_rgba8 = runtime.image_rgba8
    failure_injected = False

    def fail_before_active_collection(image):
        nonlocal failure_injected
        if image == fail_display:
            failure_injected = True
            raise RuntimeError("simulated legacy pre-active collection failure")
        return original_image_rgba8(image)

    runtime.image_rgba8 = fail_before_active_collection
    try:
        try:
            failed_legacy_result = bpy.ops.quicksdf.propagate_overrides()
        except RuntimeError as exc:
            assert "simulated legacy pre-active collection failure" in str(exc)
        else:
            assert failed_legacy_result == {"CANCELLED"}, failed_legacy_result
    finally:
        runtime.image_rgba8 = original_image_rgba8
    assert failure_injected
    assert not runtime.has_paint_snapshot(project)
    np.testing.assert_array_equal(runtime.image_rgba(legacy_source), legacy_snapshot)
    runtime.write_image_rgba(legacy_source, legacy_original)

    print("[Quick SDF smoke] rebake guide preserves painted RGB and coverage")
    painted_before = {
        item.uuid: (
            runtime.image_rgba(runtime.resolve_display_image(project, item)).copy(),
            runtime.image_rgba(runtime.resolve_coverage_image(project, item)).copy(),
        )
        for item in project.angles
    }
    project.forward_vector = (0.2, -1.0, 0.0)
    project.guide_shadow_amount = 63.0
    _expect_finished(bpy.ops.quicksdf.bake_base(), "bake_base guide")
    from quick_sdf_blender.core import validate_monotonic, validate_side_monotonic

    guide_stack, guide_angles = runtime.project_side_stack(project, "RIGHT")
    guide_report = validate_side_monotonic(guide_stack, guide_angles)
    assert guide_report.is_valid, guide_report.offending_transitions
    signed_stack, signed_angles = runtime.project_mask_stack(project)
    signed_report = validate_monotonic(signed_stack, signed_angles)
    assert signed_report.is_valid, signed_report.offending_transitions
    for item in project.angles:
        before_display, before_coverage = painted_before[item.uuid]
        display = runtime.image_rgba(runtime.resolve_display_image(project, item))
        coverage = runtime.image_rgba(runtime.resolve_coverage_image(project, item))
        covered = before_coverage[..., 0] >= 0.5
        np.testing.assert_array_equal(coverage, before_coverage)
        np.testing.assert_array_equal(display[..., :3][covered], before_display[..., :3][covered])

    print("[Quick SDF smoke] preview material is reversible")
    original_material = cube.material_slots[0].material
    original_link = cube.material_slots[0].link
    masks_before_preview, angles_before_preview = runtime.project_mask_stack(project)
    _expect_finished(bpy.ops.quicksdf.preview_enable(), "preview_enable")
    masks_after_enable, _angles_after_enable = runtime.project_mask_stack(project)
    np.testing.assert_array_equal(masks_after_enable, masks_before_preview)
    preview_material = cube.material_slots[0].material
    assert preview_material is not None and preview_material != original_material
    assert preview_material.node_tree.nodes.get("QSDF Original Overlay") is not None
    _expect_finished(bpy.ops.quicksdf.preview_disable(), "preview_disable")
    masks_after_disable, _angles_after_disable = runtime.project_mask_stack(project)
    np.testing.assert_array_equal(masks_after_disable, masks_before_preview)
    assert cube.material_slots[0].material == original_material
    assert cube.material_slots[0].link == original_link
    signed_stack, signed_angles = runtime.project_mask_stack(project)
    signed_report = validate_monotonic(signed_stack, signed_angles)
    assert signed_report.is_valid, signed_report.offending_transitions

    print("[Quick SDF smoke] validate, generate, and export RGBA16 PNG")
    _assert_export_worker_side_contracts()
    _expect_finished(bpy.ops.quicksdf.validate(), "validate")
    assert not project.has_violations
    assert project.validation_message == "OK"
    _expect_finished(bpy.ops.quicksdf.generate(), "generate")
    assert project.generated_image is not None
    assert project.generated_image.get(runtime.PROJECT_UUID_KEY) == project.uuid
    assert project.generated_image.get(runtime.ROLE_KEY) == runtime.THRESHOLD_ROLE
    from quick_sdf_blender import operators

    strict_expected = operators._compute_threshold_rgba(
        operators._prepare_strict_threshold_inputs(project)
    )
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
    np.testing.assert_array_equal(pixels, strict_expected)
    assert project.export_adjustment_pixel_count == 0
    assert project.export_adjustment_image is None
    valid_png_bytes = png_path.read_bytes()

    print("[Quick SDF smoke] auto-repair an invalid paint stack without changing source images")
    from quick_sdf_blender.core import generate_threshold_pair, repair_side_monotonic
    from quick_sdf_blender.symmetry import mirror_side_stack

    test_y, test_x = 100, 100
    invalid_values = (True, False, True, True, True, True, True)
    original_pixels = {}
    for item, value in zip(project.angles, invalid_values):
        display = runtime.resolve_display_image(project, item)
        coverage = runtime.resolve_coverage_image(project, item)
        assert display is not None and coverage is not None
        display_rgba = runtime.image_rgba(display)
        coverage_rgba = runtime.image_rgba(coverage)
        original_pixels[(item.uuid, "display")] = display_rgba[test_y, test_x].copy()
        original_pixels[(item.uuid, "coverage")] = coverage_rgba[test_y, test_x].copy()
        display_rgba[test_y, test_x, :3] = float(value)
        display_rgba[test_y, test_x, 3] = 1.0
        coverage_rgba[test_y, test_x, :3] = 1.0
        coverage_rgba[test_y, test_x, 3] = 1.0
        runtime.write_image_rgba(display, display_rgba)
        runtime.write_image_rgba(coverage, coverage_rgba)

    def source_fingerprints():
        records = []
        for item in project.angles:
            for image in (
                runtime.resolve_display_image(project, item),
                runtime.resolve_base_image(project, item),
                runtime.resolve_coverage_image(project, item),
            ):
                assert image is not None
                records.append(
                    (
                        image.name,
                        int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                        hashlib.sha256(runtime.image_rgba(image).tobytes()).hexdigest(),
                    )
                )
        return tuple(records)

    before_repair_export = source_fingerprints()
    try:
        strict_result = bpy.ops.quicksdf.generate()
    except RuntimeError as error:
        assert "wrong direction" in str(error)
    else:
        assert strict_result == {"CANCELLED"}
    prepared = operators._prepare_threshold_inputs(project)
    assert prepared["linked"]
    source_display, source_angles, source_base, source_coverage = prepared["source"]
    repair = repair_side_monotonic(source_display, source_base, source_coverage)
    assert repair.changed_pixel_count > 0
    assert repair.protected_changed_pixel_count > 0
    from quick_sdf_blender import live_preview

    canvas_before_seek = scene.tool_settings.image_paint.canvas
    seek_image = live_preview.update_seek_preview(project, float(source_angles[1]))
    assert seek_image is not None
    np.testing.assert_array_equal(runtime.image_mask(seek_image), repair.masks[1])
    assert scene.tool_settings.image_paint.canvas == canvas_before_seek
    mirrored = mirror_side_stack(
        repair.masks,
        prepared["mirror_mode"],
        island_pairs=prepared["island_pairs"],
    )
    if prepared["author_side"] == "RIGHT":
        expected_repaired = generate_threshold_pair(
            repair.masks, source_angles, mirrored, source_angles
        )
    else:
        expected_repaired = generate_threshold_pair(
            mirrored, source_angles, repair.masks, source_angles
        )
    _expect_finished(
        bpy.ops.quicksdf.export_texture(filepath=str(repaired_png_path), overwrite=True),
        "repairing export_texture",
    )
    assert repaired_png_path.is_file()
    _repair_header, repaired_pixels = _decode_rgba16(repaired_png_path)
    np.testing.assert_array_equal(repaired_pixels, expected_repaired)
    assert source_fingerprints() == before_repair_export
    assert project.export_adjustment_pixel_count == repair.changed_pixel_count
    assert project.export_adjustment_sample_count == repair.changed_sample_count
    assert (
        project.export_adjustment_protected_pixel_count
        == repair.protected_changed_pixel_count
    )
    assert project.export_adjustment_image is not None
    assert project.export_adjustment_image.get(runtime.ROLE_KEY) == runtime.EXPORT_ADJUSTMENT_ROLE
    assert project.job_message == "Adjusted angle continuity and exported"

    print("[Quick SDF smoke] preserve successful derived state on I/O failure")
    second_y, second_x = 101, 101
    second_values = (False, True, False, True, True, True, True)
    for item, value in zip(project.angles, second_values):
        display = runtime.resolve_display_image(project, item)
        coverage = runtime.resolve_coverage_image(project, item)
        display_rgba = runtime.image_rgba(display)
        coverage_rgba = runtime.image_rgba(coverage)
        original_pixels[(item.uuid, "display2")] = display_rgba[second_y, second_x].copy()
        original_pixels[(item.uuid, "coverage2")] = coverage_rgba[second_y, second_x].copy()
        display_rgba[second_y, second_x, :3] = float(value)
        display_rgba[second_y, second_x, 3] = 1.0
        runtime.write_image_rgba(display, display_rgba)
    project.dirty = True
    adjustment_image = project.export_adjustment_image
    previous_derived = (
        project.generated_image.name,
        hashlib.sha256(runtime.image_rgba(project.generated_image).tobytes()).hexdigest(),
        adjustment_image.name,
        hashlib.sha256(runtime.image_rgba(adjustment_image).tobytes()).hexdigest(),
        int(project.export_adjustment_pixel_count),
        int(project.export_adjustment_sample_count),
        int(project.export_adjustment_protected_pixel_count),
        str(project.validation_message),
    )
    blocker = output_directory / "export_blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    retry_path = blocker / "face_shadow.png"
    retry_source = source_fingerprints()
    try:
        failed_result = bpy.ops.quicksdf.export_texture(
            filepath=str(retry_path), overwrite=True
        )
    except RuntimeError as error:
        assert "export_blocker" in str(error)
    else:
        assert failed_result == {"CANCELLED"}
    assert project.export_failed
    assert project.dirty
    assert str(project.output_path) == str(retry_path)
    assert project.job_message.startswith("Export failed:")
    assert source_fingerprints() == retry_source
    assert previous_derived == (
        project.generated_image.name,
        hashlib.sha256(runtime.image_rgba(project.generated_image).tobytes()).hexdigest(),
        project.export_adjustment_image.name,
        hashlib.sha256(runtime.image_rgba(project.export_adjustment_image).tobytes()).hexdigest(),
        int(project.export_adjustment_pixel_count),
        int(project.export_adjustment_sample_count),
        int(project.export_adjustment_protected_pixel_count),
        str(project.validation_message),
    )
    assert blocker.read_text(encoding="utf-8") == "not a directory"
    blocker.unlink()

    # Restore the test pixel, then verify a normal stack remains byte-identical
    # through the repair-enabled export path.
    for item in project.angles:
        display = runtime.resolve_display_image(project, item)
        coverage = runtime.resolve_coverage_image(project, item)
        display_rgba = runtime.image_rgba(display)
        coverage_rgba = runtime.image_rgba(coverage)
        display_rgba[test_y, test_x] = original_pixels[(item.uuid, "display")]
        coverage_rgba[test_y, test_x] = original_pixels[(item.uuid, "coverage")]
        display_rgba[second_y, second_x] = original_pixels[(item.uuid, "display2")]
        coverage_rgba[second_y, second_x] = original_pixels[(item.uuid, "coverage2")]
        runtime.write_image_rgba(display, display_rgba)
        runtime.write_image_rgba(coverage, coverage_rgba)
    _expect_finished(
        bpy.ops.quicksdf.export_texture(filepath=str(png_path), overwrite=True),
        "clean re-export",
    )
    assert png_path.read_bytes() == valid_png_bytes
    assert project.export_adjustment_pixel_count == 0
    assert project.export_adjustment_image is None

    project.output_path = str(png_path)
    project.export_failed = False
    project.job_message = ""
    project.diagnostic_message = ""

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

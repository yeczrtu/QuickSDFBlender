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
import math
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
from quick_sdf_blender.model import DEFAULT_ANGLES, SCHEMA_VERSION  # noqa: E402
from quick_sdf_blender.packing import (  # noqa: E402
    PackingChannelSpec,
    PackingSource,
    pack_rgba16,
    quantize_unorm16,
)


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
    image = runtime.resolve_display_image(project, angle_item)
    assert image is not None
    assert bpy.context.scene.tool_settings.image_paint.canvas == image
    assert image.get(runtime.PROJECT_UUID_KEY) == project.uuid
    assert image.get(runtime.ANGLE_UUID_KEY) == angle_item.uuid


def _liltoon_packing_snapshot(
    sdf_area: np.ndarray,
    shadow_strength: np.ndarray,
) -> dict[str, object]:
    shape = tuple(int(value) for value in np.asarray(sdf_area).shape)
    assert shape == tuple(int(value) for value in np.asarray(shadow_strength).shape)
    return {
        "specs": (
            PackingChannelSpec(PackingSource.RIGHT_THRESHOLD),
            PackingChannelSpec(PackingSource.LEFT_THRESHOLD),
            PackingChannelSpec(
                PackingSource.SDF_AREA,
                invert=True,
                auxiliary_mask_uuid="smoke-sdf-area",
            ),
            PackingChannelSpec(
                PackingSource.SHADOW_STRENGTH,
                auxiliary_mask_uuid="smoke-shadow-strength",
            ),
        ),
        "signals": {
            "smoke-sdf-area": np.asarray(sdf_area),
            "smoke-shadow-strength": np.asarray(shadow_strength),
        },
        "shape": shape,
    }


def _pack_expected_thresholds(
    channels: np.ndarray,
    packing: dict[str, object],
) -> np.ndarray:
    signals = dict(packing["signals"])
    signals[PackingSource.RIGHT_THRESHOLD] = channels[..., 0]
    signals[PackingSource.LEFT_THRESHOLD] = channels[..., 1]
    return pack_rgba16(
        signals,
        packing["specs"],
        shape=packing["shape"],
    )


def _assert_export_worker_side_contracts() -> None:
    from quick_sdf_blender import operators
    from quick_sdf_blender.core import generate_threshold_pair_channels
    from quick_sdf_blender.symmetry import IslandPair, mirror_side_stack

    angles = np.asarray([0.0, 45.0, 90.0], dtype=np.float64)
    right_transition = np.asarray([[0, 1, 3, 2], [3, 2, 1, 0]])
    left_transition = np.asarray([[3, 1, 2, 0], [1, 3, 0, 2]])
    right = np.arange(3)[:, None, None] >= right_transition[None, ...]
    left = np.arange(3)[:, None, None] >= left_transition[None, ...]
    coverage = np.zeros_like(right)
    sdf_area = np.asarray(
        [[True, True, False, False], [True, False, True, False]], dtype=np.bool_
    )
    shadow_strength = np.asarray(
        [[1.0, 0.75, 0.5, 0.25], [0.0, 0.25, 0.5, 1.0]], dtype=np.float64
    )
    packing = _liltoon_packing_snapshot(sdf_area, shadow_strength)
    independent = operators._compute_export_result(
        {
            "linked": False,
            "right": (right, angles, ~right, coverage),
            "left": (left, angles, np.roll(left, 1, axis=0), coverage),
            "packing": packing,
        }
    )
    independent_channels = generate_threshold_pair_channels(
        right, angles, left, angles
    )
    np.testing.assert_array_equal(
        independent["rgba"],
        _pack_expected_thresholds(independent_channels, packing),
    )
    np.testing.assert_array_equal(
        independent["rgba"][..., 2], 65535 - quantize_unorm16(sdf_area)
    )
    np.testing.assert_array_equal(
        independent["rgba"][..., 3], quantize_unorm16(shadow_strength)
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
                "packing": packing,
            }
        )
        linked_channels = generate_threshold_pair_channels(
            mirrored, angles, left, angles
        )
        np.testing.assert_array_equal(
            linked_left["rgba"],
            _pack_expected_thresholds(linked_channels, packing),
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
    custom_png_path = output_directory / "quick_sdf_smoke_custom_rgba16.png"
    repaired_png_path = output_directory / "quick_sdf_smoke_repaired_rgba16.png"
    if blend_path.exists():
        blend_path.unlink()
    if png_path.exists():
        png_path.unlink()
    if custom_png_path.exists():
        custom_png_path.unlink()
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
    assert project.schema_version == SCHEMA_VERSION == 6
    assert project.base_source == "NORMAL_GUIDE"
    assert project.guide_version == 2
    assert len(project.angles) == 8
    np.testing.assert_allclose(
        [float(item.angle) for item in project.angles],
        DEFAULT_ANGLES,
        rtol=0.0,
        atol=1.0e-5,
    )
    assert {item.side for item in project.angles} == {"RIGHT"}
    assert not project.base_needs_update
    assert len(project.aux_masks) == 2
    aux_by_role = {str(item.role): item for item in project.aux_masks}
    assert set(aux_by_role) == {"SDF_AREA", "SHADOW_STRENGTH"}
    for role, item in aux_by_role.items():
        image = runtime.resolve_aux_mask_image(project, item)
        assert image is not None
        assert image.alpha_mode == "NONE"
        assert image.get(runtime.PROJECT_UUID_KEY) == project.uuid
        assert image.get(runtime.ROLE_KEY) == runtime.AUX_MASK_ROLE
        assert image.get(runtime.AUX_MASK_UUID_KEY) == item.uuid
        assert bool(image.get(runtime.AUX_MASK_INITIALIZED_KEY, False))
        assert np.all(runtime.image_rgba(image)[..., 3] == 1.0), role
    sdf_area_image = runtime.resolve_aux_mask_image(
        project, aux_by_role["SDF_AREA"]
    )
    shadow_strength_image = runtime.resolve_aux_mask_image(
        project, aux_by_role["SHADOW_STRENGTH"]
    )
    assert sdf_area_image is not None and shadow_strength_image is not None
    sdf_area = runtime.image_rgba(sdf_area_image)[..., 0]
    shadow_strength = runtime.image_rgba(shadow_strength_image)[..., 0]
    assert np.any(sdf_area >= 0.5)
    assert np.all(shadow_strength == 1.0)
    recipe = {
        str(item.output_channel): (
            str(item.source_type),
            bool(item.invert),
            str(item.auxiliary_mask_uuid),
        )
        for item in project.packing_channels
    }
    assert recipe == {
        "R": ("RIGHT_THRESHOLD", False, ""),
        "G": ("LEFT_THRESHOLD", False, ""),
        "B": ("SDF_AREA", True, aux_by_role["SDF_AREA"].uuid),
        "A": (
            "SHADOW_STRENGTH",
            False,
            aux_by_role["SHADOW_STRENGTH"].uuid,
        ),
    }
    assert not project.packing_customized
    expected_active = min(DEFAULT_ANGLES, key=lambda value: abs(value - 45.0))
    assert math.isclose(
        float(project.angles[project.active_angle_index].angle),
        expected_active,
        abs_tol=1.0e-5,
    )
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
        base = runtime.base_mask(angle_item)
        coverage = runtime.coverage_mask(angle_item)
        assert display is not None
        assert display.alpha_mode == "NONE"
        assert base.shape == coverage.shape == (512, 512)
        assert base.dtype == coverage.dtype == np.bool_
        assert not base.flags.writeable
        assert not coverage.flags.writeable
        assert not hasattr(angle_item, "base_image")
        assert not hasattr(angle_item, "coverage_image")
        assert np.all(runtime.image_rgba(display)[..., 3] == 1.0)
        assert not np.any(coverage)

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
    front = runtime.resolve_display_image(project, project.angles[0])
    current_light = bool(runtime.image_mask(front)[256, 256])
    selected_index = 6 if current_light else 0
    project.paint_value = 0 if current_light else 1
    _expect_finished(bpy.ops.quicksdf.key_select(index=selected_index), "key_select")
    source_image = runtime.resolve_display_image(project, project.angles[project.active_angle_index])
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
        assert display is not None
        assert np.all(runtime.coverage_mask(angle_item)[255:257, 255:257])
        assert np.all(runtime.image_rgba(display)[..., 3] == 1.0)
    from quick_sdf_blender import operators as operator_module

    history = operator_module._HISTORIES[str(project.uuid)]
    transaction_after = operator_module._history_values(project, history.undo_keys)
    _expect_finished(bpy.ops.quicksdf.history_undo(), "history_undo")
    _expect_finished(bpy.ops.quicksdf.history_redo(), "history_redo")
    restored_after = operator_module._history_values(project, transaction_after)
    assert restored_after.keys() == transaction_after.keys()
    for name, expected in transaction_after.items():
        np.testing.assert_array_equal(restored_after[name], expected)

    print("[Quick SDF smoke] roll back a partial Smart Paint collection")
    # Reproduce a propagation failure after an earlier key has already
    # populated ``before_history`` but before the active (90 degree) canvas is
    # captured. A cancelled propagation must still remove the native stroke.
    _expect_finished(bpy.ops.quicksdf.key_select(index=6), "key_select propagation rollback")
    propagation_source = runtime.resolve_display_image(project, project.angles[6])
    fail_display = runtime.resolve_display_image(project, project.angles[1])
    assert propagation_source is not None and fail_display is not None
    propagation_original = runtime.image_rgba(propagation_source)
    propagation_region = np.zeros(runtime.image_mask(propagation_source).shape, dtype=np.bool_)
    propagation_region[200:202, 200:202] = True
    propagation_light = runtime.image_rgba(propagation_source)
    propagation_light[..., :3][propagation_region] = 1.0
    propagation_light[..., 3] = 1.0
    runtime.write_image_rgba(propagation_source, propagation_light)
    runtime.capture_paint_snapshot(project)
    propagation_snapshot = runtime.image_rgba(propagation_source)
    propagation_shadow = propagation_snapshot.copy()
    propagation_shadow[..., :3][propagation_region] = 0.0
    runtime.write_image_rgba(propagation_source, propagation_shadow)
    assert np.any(runtime.image_rgba(propagation_source) != propagation_snapshot)

    original_image_rgba8 = runtime.image_rgba8
    failure_injected = False

    def fail_before_active_collection(image):
        nonlocal failure_injected
        if image == fail_display:
            failure_injected = True
            raise RuntimeError("simulated pre-active collection failure")
        return original_image_rgba8(image)

    runtime.image_rgba8 = fail_before_active_collection
    try:
        try:
            failed_propagation_result = bpy.ops.quicksdf.propagate_overrides()
        except RuntimeError as exc:
            assert "simulated pre-active collection failure" in str(exc)
        else:
            assert failed_propagation_result == {"CANCELLED"}, failed_propagation_result
    finally:
        runtime.image_rgba8 = original_image_rgba8
    assert failure_injected
    assert not runtime.has_paint_snapshot(project)
    np.testing.assert_array_equal(
        runtime.image_rgba(propagation_source), propagation_snapshot
    )
    runtime.write_image_rgba(propagation_source, propagation_original)

    print("[Quick SDF smoke] rebake guide preserves painted RGB and coverage")
    painted_before = {
        item.uuid: (
            runtime.image_rgba(runtime.resolve_display_image(project, item)).copy(),
            runtime.coverage_mask(item).copy(),
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
        coverage = runtime.coverage_mask(item)
        covered = before_coverage
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

    strict_channels = operators._compute_threshold_channels(
        operators._prepare_strict_threshold_inputs(project)
    )
    strict_expected = operators._pack_threshold_channels(
        strict_channels, operators._snapshot_packing_inputs(project)
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
    np.testing.assert_array_equal(
        pixels[..., 2], 65535 - quantize_unorm16(sdf_area)
    )
    np.testing.assert_array_equal(
        pixels[..., 3], quantize_unorm16(shadow_strength)
    )
    assert np.all((pixels[..., 0] >= 0) & (pixels[..., 0] <= 65535))
    assert np.all((pixels[..., 1] >= 0) & (pixels[..., 1] <= 65535))
    np.testing.assert_array_equal(pixels, strict_expected)
    assert project.export_adjustment_pixel_count == 0
    assert project.export_adjustment_image is None
    valid_png_bytes = png_path.read_bytes()

    print("[Quick SDF smoke] customize, export, and reset project-local packing")
    _expect_finished(
        bpy.ops.quicksdf.aux_mask_add(name="Smoke Custom", fill_value=0.25),
        "aux_mask_add",
    )
    custom_item = runtime.active_aux_mask(project)
    assert custom_item is not None and custom_item.role == "CUSTOM"
    custom_uuid = str(custom_item.uuid)
    custom_image = runtime.resolve_aux_mask_image(project, custom_item)
    assert custom_image is not None
    custom_mask_values = quantize_unorm16(
        runtime.image_rgba(custom_image)[..., 0]
    )
    np.testing.assert_array_equal(
        runtime.image_channel_u16(custom_image), custom_mask_values
    )
    assert np.unique(custom_mask_values).size == 1
    assert 0 < int(custom_mask_values[0, 0]) < 65535
    _expect_finished(bpy.ops.quicksdf.packing_customize(), "packing_customize")
    _expect_finished(
        bpy.ops.quicksdf.packing_assign_active_mask(output_channel="B"),
        "packing_assign_active_mask",
    )
    packing_by_output = {
        str(item.output_channel): item for item in project.packing_channels
    }
    packing_by_output["B"].invert = False
    packing_by_output["A"].source_type = "CONSTANT"
    packing_by_output["A"].constant_value = 0.5
    assert project.packing_customized
    valid_custom_uuid = str(packing_by_output["B"].auxiliary_mask_uuid)
    packing_by_output["B"].auxiliary_mask_uuid = "missing-mask-for-row-error"
    try:
        operators._prepare_threshold_inputs(project)
    except ValueError as error:
        assert str(error).startswith("Packing B:"), str(error)
    else:
        raise AssertionError("A missing Custom Mask must identify its packing row")
    finally:
        packing_by_output["B"].auxiliary_mask_uuid = valid_custom_uuid
    _expect_finished(
        bpy.ops.quicksdf.export_texture(
            filepath=str(custom_png_path), overwrite=True
        ),
        "custom packing export_texture",
    )
    _custom_header, custom_pixels = _decode_rgba16(custom_png_path)
    np.testing.assert_array_equal(custom_pixels[..., :2], pixels[..., :2])
    np.testing.assert_array_equal(custom_pixels[..., 2], custom_mask_values)
    assert np.all(custom_pixels[..., 3] == 32768)

    _expect_finished(
        bpy.ops.quicksdf.packing_reset_liltoon(), "packing_reset_liltoon"
    )
    assert not project.packing_customized
    _expect_finished(
        bpy.ops.quicksdf.aux_mask_delete(mask_uuid=custom_uuid),
        "aux_mask_delete",
    )
    assert len(project.aux_masks) == 2
    _expect_finished(
        bpy.ops.quicksdf.export_texture(filepath=str(png_path), overwrite=True),
        "default packing re-export",
    )
    assert png_path.read_bytes() == valid_png_bytes

    print("[Quick SDF smoke] auto-repair an invalid paint stack without changing source images")
    from quick_sdf_blender.core import (
        generate_threshold_pair_channels,
        repair_side_monotonic,
    )
    from quick_sdf_blender.symmetry import mirror_side_stack

    test_y, test_x = 100, 100
    invalid_values = (True, False, True, True, True, True, True, True)
    original_pixels = {}
    for item, value in zip(project.angles, invalid_values):
        display = runtime.resolve_display_image(project, item)
        assert display is not None
        display_rgba = runtime.image_rgba(display)
        coverage = runtime.coverage_mask(item).copy()
        original_pixels[(item.uuid, "display")] = display_rgba[test_y, test_x].copy()
        original_pixels[(item.uuid, "coverage")] = bool(coverage[test_y, test_x])
        display_rgba[test_y, test_x, :3] = float(value)
        display_rgba[test_y, test_x, 3] = 1.0
        coverage[test_y, test_x] = True
        runtime.write_image_rgba(display, display_rgba)
        runtime.set_coverage_mask(item, coverage)

    def source_fingerprints():
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
                blob = runtime.bitplane_blob(item, role)
                records.append(
                    (
                        role.lower(),
                        str(item.uuid),
                        runtime.bitplane_revision_token(item, role),
                        hashlib.sha256(blob).hexdigest(),
                    )
                )
        for aux_item in project.aux_masks:
            image = runtime.resolve_aux_mask_image(project, aux_item)
            assert image is not None
            records.append(
                (
                    image.name,
                    int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                    hashlib.sha256(runtime.image_rgba(image).tobytes()).hexdigest(),
                )
            )
        recipe_fingerprint = tuple(
            (
                str(item.output_channel),
                str(item.source_type),
                bool(item.invert),
                float(item.constant_value),
                str(item.auxiliary_mask_uuid),
            )
            for item in project.packing_channels
        )
        return tuple(records), int(project.packing_revision), recipe_fingerprint

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
        expected_repaired_channels = generate_threshold_pair_channels(
            repair.masks, source_angles, mirrored, source_angles
        )
    else:
        expected_repaired_channels = generate_threshold_pair_channels(
            mirrored, source_angles, repair.masks, source_angles
        )
    expected_repaired = operators._pack_threshold_channels(
        expected_repaired_channels, prepared["packing"]
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
    second_values = (False, True, False, True, True, True, True, True)
    for item, value in zip(project.angles, second_values):
        display = runtime.resolve_display_image(project, item)
        display_rgba = runtime.image_rgba(display)
        coverage = runtime.coverage_mask(item)
        original_pixels[(item.uuid, "display2")] = display_rgba[second_y, second_x].copy()
        original_pixels[(item.uuid, "coverage2")] = bool(coverage[second_y, second_x])
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
        display_rgba = runtime.image_rgba(display)
        coverage = runtime.coverage_mask(item).copy()
        display_rgba[test_y, test_x] = original_pixels[(item.uuid, "display")]
        coverage[test_y, test_x] = original_pixels[(item.uuid, "coverage")]
        display_rgba[second_y, second_x] = original_pixels[(item.uuid, "display2")]
        coverage[second_y, second_x] = original_pixels[(item.uuid, "coverage2")]
        runtime.write_image_rgba(display, display_rgba)
        runtime.set_coverage_mask(item, coverage)
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
    bitplane_records = {
        str(item.uuid): (
            runtime.bitplane_blob(item, "BASE"),
            runtime.bitplane_blob(item, "COVERAGE"),
            runtime.bitplane_revision_token(item, "BASE"),
            runtime.bitplane_revision_token(item, "COVERAGE"),
        )
        for item in project.angles
    }
    aux_records = tuple(
        (
            str(item.uuid),
            str(item.name),
            str(item.role),
            str(item.image_name),
            int(item.revision),
            hashlib.sha256(
                runtime.image_rgba(runtime.resolve_aux_mask_image(project, item)).tobytes()
            ).hexdigest(),
        )
        for item in project.aux_masks
    )
    packing_records = tuple(
        (
            str(item.output_channel),
            str(item.source_type),
            str(item.auxiliary_mask_uuid),
            bool(item.invert),
            float(item.constant_value),
        )
        for item in project.packing_channels
    )
    packing_revision = int(project.packing_revision)
    expected_painted_pixels = tuple(
        runtime.image_rgba(runtime.resolve_display_image(project, item))[255, 255, :3].copy()
        for item in project.angles
    )
    expected_base_pixels = tuple(
        bool(runtime.base_mask(item)[255, 255])
        for item in project.angles
    )
    expected_coverage_pixels = tuple(
        bool(runtime.coverage_mask(item)[255, 255])
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
    assert tuple(
        (
            str(item.uuid),
            str(item.name),
            str(item.role),
            str(item.image_name),
            int(item.revision),
            hashlib.sha256(
                runtime.image_rgba(runtime.resolve_aux_mask_image(project, item)).tobytes()
            ).hexdigest(),
        )
        for item in project.aux_masks
    ) == aux_records
    assert tuple(
        (
            str(item.output_channel),
            str(item.source_type),
            str(item.auxiliary_mask_uuid),
            bool(item.invert),
            float(item.constant_value),
        )
        for item in project.packing_channels
    ) == packing_records
    assert int(project.packing_revision) == packing_revision
    assert studio.current_session() is None
    for index, angle_item in enumerate(project.angles):
        image = runtime.resolve_display_image(project, angle_item)
        assert image is not None and angle_item.display_image == image
        assert image.get(runtime.PROJECT_UUID_KEY) == project_uuid
        assert image.get(runtime.ANGLE_UUID_KEY) == angle_item.uuid
        assert not hasattr(angle_item, "base_image")
        assert not hasattr(angle_item, "coverage_image")
        base_blob, coverage_blob, base_token, coverage_token = bitplane_records[
            str(angle_item.uuid)
        ]
        assert runtime.bitplane_blob(angle_item, "BASE") == base_blob
        assert runtime.bitplane_blob(angle_item, "COVERAGE") == coverage_blob
        assert runtime.bitplane_revision_token(angle_item, "BASE") == base_token
        assert (
            runtime.bitplane_revision_token(angle_item, "COVERAGE")
            == coverage_token
        )
        np.testing.assert_array_equal(
            runtime.image_rgba(image)[255, 255, :3], expected_painted_pixels[index]
        )
        assert bool(runtime.base_mask(angle_item)[255, 255]) == expected_base_pixels[index]
        assert (
            bool(runtime.coverage_mask(angle_item)[255, 255])
            == expected_coverage_pixels[index]
        )
        assert np.all(runtime.image_rgba(image)[..., 3] == 1.0)
    angle_images = tuple(
        image
        for image in bpy.data.images
        if image.get(runtime.PROJECT_UUID_KEY) == project_uuid
        and image.get(runtime.ANGLE_UUID_KEY)
    )
    assert len(angle_images) == len(project.angles)
    assert all(image.get(runtime.ROLE_KEY) == runtime.DISPLAY_ROLE for image in angle_images)
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

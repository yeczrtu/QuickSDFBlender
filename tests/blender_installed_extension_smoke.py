"""Exercise the packaged extension from an isolated Blender user directory."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys
import tomllib

import bpy
import numpy as np


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", default="bl_ext.user_default.quick_sdf_blender")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--isolated-root", type=Path, required=True)
    return parser.parse_args(values)


def run(module_name: str, expected_version: str, isolated_root: Path) -> None:
    # A package test must never succeed by importing the adjacent source tree.
    source_root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        entry
        for entry in sys.path
        if not entry or Path(entry).resolve() != source_root
    ]

    assert module_name in bpy.context.preferences.addons
    addon = importlib.import_module(module_name)
    module_path = Path(addon.__file__).resolve()
    isolated = isolated_root.resolve()
    assert isolated == module_path or isolated in module_path.parents, module_path
    manifest_path = module_path.with_name("blender_manifest.toml")
    assert manifest_path.is_file(), manifest_path
    with manifest_path.open("rb") as handle:
        manifest = tomllib.load(handle)
    assert str(manifest["version"]) == expected_version
    # bl_info is legacy metadata and Blender's Extension loader may omit it
    # from the public package module. If exposed, it must still agree.
    if hasattr(addon, "bl_info"):
        expected_tuple = tuple(int(part) for part in expected_version.split("."))
        assert tuple(addon.bl_info["version"]) == expected_tuple

    native = importlib.import_module(f"{module_name}.native")
    assert native.available()
    assert native.version() == 6
    assert native.native_threshold_available()
    assert native.native_guide_bake_available()
    assert native.native_repair_available()
    dll = native._load()
    assert hasattr(dll, "qsdf_repair_side_monotonic")
    assert hasattr(dll, "qsdf_generate_threshold_pair_cancelable")

    # Exercise the DLL through the installed package, not merely its symbol
    # table. Fractional 8-stage angles catch stale pre-ABI-5 builds that would
    # otherwise truncate authoring angles or use the old reserved endpoints.
    core = importlib.import_module(f"{module_name}.core")
    model = importlib.import_module(f"{module_name}.model")
    packing = importlib.import_module(f"{module_name}.packing")
    bitplane = importlib.import_module(f"{module_name}.bitplane")
    assert model.SCHEMA_VERSION == 6
    angles = np.arange(8, dtype=np.float64) * (90.0 / 7.0)
    steps = np.arange(8, dtype=np.int64)[:, None]
    right = (steps >= np.array([0, 1, 4, 8])[None, :]).reshape(8, 1, 4)
    left = (steps >= np.array([7, 5, 2, 0])[None, :]).reshape(8, 1, 4)
    expected = core.generate_threshold_pair_channels(right, angles, left, angles)
    actual = native.generate_threshold_pair(right, angles, left, angles)
    assert expected.shape == actual.shape == (1, 4, 2)
    assert expected.dtype == actual.dtype == np.uint16
    assert expected.flags.c_contiguous and actual.flags.c_contiguous
    np.testing.assert_array_equal(actual, expected)
    assert expected[0, 0, 0] == 65535
    assert expected[0, 3, 0] == 0

    sdf_area = np.asarray([[True, True, False, False]], dtype=np.bool_)
    strength = np.asarray([[1.0, 0.5, 0.25, 0.0]], dtype=np.float64)
    packed = packing.pack_rgba16(
        {
            packing.PackingSource.RIGHT_THRESHOLD: actual[..., 0],
            packing.PackingSource.LEFT_THRESHOLD: actual[..., 1],
            "installed-sdf": sdf_area,
            "installed-strength": strength,
        },
        (
            packing.PackingChannelSpec(packing.PackingSource.RIGHT_THRESHOLD),
            packing.PackingChannelSpec(packing.PackingSource.LEFT_THRESHOLD),
            packing.PackingChannelSpec(
                packing.PackingSource.SDF_AREA,
                invert=True,
                auxiliary_mask_uuid="installed-sdf",
            ),
            packing.PackingChannelSpec(
                packing.PackingSource.SHADOW_STRENGTH,
                auxiliary_mask_uuid="installed-strength",
            ),
        ),
    )
    assert packed.shape == (1, 4, 4)
    np.testing.assert_array_equal(packed[..., :2], actual)
    np.testing.assert_array_equal(
        packed[..., 2], np.asarray([[0, 0, 65535, 65535]], dtype=np.uint16)
    )
    np.testing.assert_array_equal(
        packed[..., 3], np.asarray([[65535, 32768, 16384, 0]], dtype=np.uint16)
    )

    binary = np.asarray(
        [[True, False, True, False, True], [False, True, False, True, False]],
        dtype=np.bool_,
    )
    binary_blob = bitplane.encode_bitplane(binary, bitplane.BitplaneRole.BASE)
    binary_header = bitplane.inspect_bitplane_header(binary_blob)
    assert binary_header.role is bitplane.BitplaneRole.BASE
    assert binary_header.shape == binary.shape
    np.testing.assert_array_equal(
        bitplane.decode_bitplane(
            binary_blob, expected_role=bitplane.BitplaneRole.BASE
        ),
        binary,
    )

    triangle_uvs = np.array(
        [[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]], dtype=np.float32
    )
    corner_normals = np.array(
        [[[0.0, -1.0, 0.0]] * 3], dtype=np.float32
    )
    guide_arguments = (
        triangle_uvs,
        corner_normals,
        angles,
        (0.0, -1.0, 0.0),
        (0.0, 0.0, 1.0),
        "RIGHT",
        50.0,
        8,
    )
    guide, occupancy = native.bake_face_shadow_guide(*guide_arguments)
    bake = importlib.import_module(f"{module_name}.bake")
    expected_guide, expected_occupancy = bake.bake_face_shadow_guide(
        *guide_arguments
    )
    assert guide.shape == (8, 8, 8)
    assert occupancy.any()
    np.testing.assert_array_equal(occupancy, expected_occupancy)
    np.testing.assert_array_equal(guide, expected_guide)

    assert hasattr(bpy.types.Scene, "quick_sdf_projects")
    assert bpy.types.PropertyGroup.bl_rna_get_subclass_py("QSDFPackingChannel") is not None
    assert bpy.types.PropertyGroup.bl_rna_get_subclass_py("QSDFAuxMask") is not None
    project = bpy.context.scene.quick_sdf_projects.add()
    project.uuid = "installed-schema-six"
    uuids = iter(("installed-area", "installed-strength"))
    aux_items = model.ensure_standard_aux_masks(
        project, uuid_factory=lambda: next(uuids)
    )
    model.reset_liltoon_packing(project)
    assert [(item.role, item.uuid) for item in aux_items] == [
        ("SDF_AREA", "installed-area"),
        ("SHADOW_STRENGTH", "installed-strength"),
    ]
    assert [
        (
            item.output_channel,
            item.source_type,
            item.invert,
            item.auxiliary_mask_uuid,
        )
        for item in project.packing_channels
    ] == [
        ("R", "RIGHT_THRESHOLD", False, ""),
        ("G", "LEFT_THRESHOLD", False, ""),
        ("B", "SDF_AREA", True, "installed-area"),
        ("A", "SHADOW_STRENGTH", False, "installed-strength"),
    ]
    bpy.context.scene.quick_sdf_projects.remove(
        len(bpy.context.scene.quick_sdf_projects) - 1
    )
    addon.unregister()
    assert not hasattr(bpy.types.Scene, "quick_sdf_projects")
    assert bpy.types.PropertyGroup.bl_rna_get_subclass_py("QSDFPackingChannel") is None
    assert bpy.types.PropertyGroup.bl_rna_get_subclass_py("QSDFAuxMask") is None
    addon.register()
    assert hasattr(bpy.types.Scene, "quick_sdf_projects")
    assert bpy.types.PropertyGroup.bl_rna_get_subclass_py("QSDFPackingChannel") is not None
    assert bpy.types.PropertyGroup.bl_rna_get_subclass_py("QSDFAuxMask") is not None
    print(
        "[Quick SDF installed extension smoke] PASS: "
        f"{expected_version} ABI {native.version()} at {module_path}"
    )


if __name__ == "__main__":
    args = _arguments()
    run(args.module, args.expected_version, args.isolated_root)

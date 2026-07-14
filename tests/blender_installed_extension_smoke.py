"""Exercise the packaged extension from an isolated Blender user directory."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys
import tomllib

import bpy


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
    assert native.version() == 5
    assert native.native_threshold_available()
    assert native.native_guide_bake_available()
    assert native.native_repair_available()
    dll = native._load()
    assert hasattr(dll, "qsdf_repair_side_monotonic")
    assert hasattr(dll, "qsdf_generate_threshold_pair_cancelable")

    assert hasattr(bpy.types.Scene, "quick_sdf_projects")
    addon.unregister()
    assert not hasattr(bpy.types.Scene, "quick_sdf_projects")
    addon.register()
    assert hasattr(bpy.types.Scene, "quick_sdf_projects")
    print(
        "[Quick SDF installed extension smoke] PASS: "
        f"{expected_version} ABI {native.version()} at {module_path}"
    )


if __name__ == "__main__":
    args = _arguments()
    run(args.module, args.expected_version, args.isolated_root)

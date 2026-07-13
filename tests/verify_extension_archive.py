"""Ensure the release ZIP is a byte-exact package of the extension source."""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import zipfile


def _source_files(source: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".zip"}:
            continue
        if relative.parts[:2] == ("bin", "Release"):
            continue
        result[relative.as_posix()] = path.read_bytes()
    return result


def run(archive: Path, source: Path, expected_version: str) -> None:
    expected = _source_files(source.resolve())
    with zipfile.ZipFile(archive.resolve()) as package:
        actual = {
            info.filename: package.read(info)
            for info in package.infolist()
            if not info.is_dir()
        }
    assert set(actual) == set(expected), (
        f"missing={sorted(set(expected) - set(actual))}, "
        f"unexpected={sorted(set(actual) - set(expected))}"
    )
    mismatches = [name for name in expected if actual[name] != expected[name]]
    assert not mismatches, f"archive byte mismatches: {mismatches}"
    manifest = actual["blender_manifest.toml"].decode("utf-8")
    assert f'version = "{expected_version}"' in manifest
    print(
        f"[Quick SDF archive verification] PASS: {len(actual)} files, "
        f"sha256={sha256(archive.read_bytes()).hexdigest()}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    arguments = parser.parse_args()
    run(arguments.archive, arguments.source, arguments.expected_version)

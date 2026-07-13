"""Quick SDF persistent-data migrations.

Migration is deliberately idempotent.  Blender can invoke load/undo handlers
more than once and an interrupted high-resolution migration must be safe to
resume.  ``schema_version`` is therefore written only after every angle layer
and legacy boundary key has been converted.
"""

from __future__ import annotations

from typing import Any


def split_legacy_rgba(rgba: Any) -> tuple[Any, Any, Any]:
    """Split a v1 RGBA mask into opaque display/base and coverage layers.

    v1 used alpha as an override flag.  Any non-zero legacy alpha is preserved
    as coverage, while display RGB is bit-for-bit unchanged and made opaque.
    The original unpainted value beneath an override was never stored in v1;
    copying the visible RGB into ``base`` is the only lossless migration.
    """

    import numpy as np

    legacy = np.asarray(rgba, dtype=np.float32)
    if legacy.ndim != 3 or legacy.shape[2] != 4:
        raise ValueError("legacy image must have shape (height, width, 4)")
    display = legacy.copy()
    display[..., 3] = 1.0
    base = display.copy()
    covered = legacy[..., 3] > 0.0
    coverage = np.empty_like(display)
    coverage[..., :3] = covered[..., None].astype(np.float32)
    coverage[..., 3] = 1.0
    return display, base, coverage


def _copy_points(source: Any, destination: Any) -> None:
    for source_point in getattr(source, "points", ()):
        point = destination.points.add()
        point.co = tuple(source_point.co)


def _migrate_boundary_sides(project: Any, left_zero_uuid: str) -> None:
    """Retain signed v1 boundary keys after angles become side-local."""

    from . import runtime

    for track in getattr(project, "boundary_tracks", ()):
        original_keys = list(track.keys)
        zero_keys: list[Any] = []
        for key in original_keys:
            signed = float(key.angle)
            key.side = "LEFT" if signed < 0.0 else "RIGHT"
            key.angle = abs(signed)
            if abs(signed) <= 1.0e-7:
                zero_keys.append(key)
        if not left_zero_uuid:
            continue
        for source in zero_keys:
            duplicate = track.keys.add()
            duplicate.uuid = runtime.new_uuid()
            duplicate.angle = 0.0
            duplicate.angle_uuid = left_zero_uuid
            duplicate.side = "LEFT"
            duplicate.is_manual = bool(source.is_manual)
            _copy_points(source, duplicate)


def _legacy_layout(project: Any) -> bool:
    """Detect v1 even if an old default-valued schema field loads as v2."""

    from . import runtime

    if int(getattr(project, "schema_version", 1)) < 2:
        return True
    for item in getattr(project, "angles", ()):
        if float(getattr(item, "angle", 0.0)) < -1.0e-7:
            return True
        legacy = getattr(item, "image", None)
        display = getattr(item, "display_image", None)
        if legacy is not None and display is None:
            return True
        if legacy is not None and legacy.get(runtime.ROLE_KEY, runtime.LEGACY_MASK_ROLE) == runtime.LEGACY_MASK_ROLE:
            return True
    return False


def _set_angle_layer_refs(item: Any, display: Any, base: Any, coverage: Any) -> None:
    item.display_image = display
    item.display_image_name = display.name
    # Deprecated v1 aliases intentionally point at the opaque display image.
    item.image = display
    item.image_name = display.name
    item.base_image = base
    item.base_image_name = base.name
    item.coverage_image = coverage
    item.coverage_image_name = coverage.name


def _pack_migrated_image(image: Any) -> None:
    """Store generated pixel edits in the blend instead of generated_color."""

    image.update()
    image.pack()


def _ensure_layers_for_item(project: Any, item: Any, *, legacy: bool) -> None:
    from . import runtime

    legacy_display = runtime.resolve_display_image(project, item, allow_legacy=True)
    if legacy_display is None:
        raise ValueError(f"Missing legacy mask at {float(item.angle):+g} degrees")

    rgba = runtime.image_rgba(legacy_display)
    replace_display = (
        legacy
        or legacy_display.get(runtime.ROLE_KEY, runtime.LEGACY_MASK_ROLE)
        == runtime.LEGACY_MASK_ROLE
    )
    if replace_display:
        display_rgba, base_rgba, coverage_rgba = split_legacy_rgba(rgba)
    else:
        display_rgba = rgba.copy()
        display_rgba[..., 3] = 1.0
        base_rgba = display_rgba.copy()
        coverage_rgba = display_rgba.copy()
        coverage_rgba[..., :3] = 0.0

    side = str(getattr(item, "side", "RIGHT"))
    angle = abs(float(item.angle))
    resolution = int(project.resolution)
    base = runtime.resolve_base_image(project, item)
    if base is None:
        base = runtime.create_angle_layer_image(
            project.uuid, item.uuid, angle, resolution, runtime.BASE_ROLE, side=side
        )
        runtime.write_image_rgba(base, base_rgba)
    coverage = runtime.resolve_coverage_image(project, item)
    if coverage is None:
        coverage = runtime.create_angle_layer_image(
            project.uuid, item.uuid, angle, resolution, runtime.COVERAGE_ROLE, side=side
        )
        runtime.write_image_rgba(coverage, coverage_rgba)

    if replace_display:
        # A v1 datablock can retain its old straight-alpha association across a
        # .blend save/reload even after pixels and ``alpha_mode`` are changed.
        # Build a fresh channel-packed display datablock so zero-alpha legacy
        # pixels cannot be premultiplied away on the next load.
        display = runtime.create_angle_layer_image(
            project.uuid,
            item.uuid,
            angle,
            resolution,
            runtime.DISPLAY_ROLE,
            side=side,
        )
    else:
        display = legacy_display
        runtime.tag_image(display, project.uuid, item.uuid, runtime.DISPLAY_ROLE)
        runtime.make_image_opaque(display)
    runtime.write_image_rgba(display, display_rgba)
    runtime.make_image_opaque(base)
    runtime.make_image_opaque(coverage)
    for image in (display, base, coverage):
        _pack_migrated_image(image)
    _set_angle_layer_refs(item, display, base, coverage)
    if display is not legacy_display:
        # Release the obsolete owned image only after both compatibility and v2
        # pointers reference the replacement.  If another datablock still uses
        # it, keep it but remove Quick SDF identity tags to avoid ambiguity.
        try:
            import bpy

            if legacy_display.users == 0:
                bpy.data.images.remove(legacy_display)
            else:
                for key in (
                    runtime.PROJECT_UUID_KEY,
                    runtime.ANGLE_UUID_KEY,
                    runtime.ROLE_KEY,
                ):
                    if key in legacy_display:
                        del legacy_display[key]
        except (AttributeError, ReferenceError, RuntimeError):
            pass


def _clone_zero_for_left(project: Any, source: Any) -> Any:
    from . import runtime

    item = project.angles.add()
    item.uuid = runtime.new_uuid()
    item.angle = 0.0
    item.side = "LEFT"
    item.is_manual = bool(source.is_manual)
    item.is_generated = bool(source.is_generated)
    item.has_violation = bool(source.has_violation)
    item.dirty = bool(source.dirty)
    resolution = int(project.resolution)
    layers = []
    for role, resolver in (
        (runtime.DISPLAY_ROLE, runtime.resolve_display_image),
        (runtime.BASE_ROLE, runtime.resolve_base_image),
        (runtime.COVERAGE_ROLE, runtime.resolve_coverage_image),
    ):
        source_image = resolver(project, source)
        if source_image is None:
            raise ValueError(f"Cannot duplicate the v1 zero-degree {role} layer")
        destination = runtime.create_angle_layer_image(
            project.uuid, item.uuid, 0.0, resolution, role, side="LEFT"
        )
        runtime.copy_image_pixels(source_image, destination, grayscale=False)
        _pack_migrated_image(destination)
        layers.append(destination)
    _set_angle_layer_refs(item, *layers)
    return item


def _sort_angles(project: Any) -> None:
    desired = sorted(
        range(len(project.angles)),
        key=lambda index: (
            0 if str(project.angles[index].side) == "RIGHT" else 1,
            float(project.angles[index].angle),
        ),
    )
    # Collection.move mutates subsequent indices, so place each wanted UUID in
    # order instead of applying stale source indices from ``desired``.
    uuids = [str(project.angles[index].uuid) for index in desired]
    for destination, uuid in enumerate(uuids):
        source = next(i for i, item in enumerate(project.angles) if str(item.uuid) == uuid)
        if source != destination:
            project.angles.move(source, destination)


def migrate_project_v1_to_v2(project: Any) -> bool:
    """Compatibility entry point migrating any older project to schema v3."""

    from .model import SCHEMA_VERSION

    legacy = _legacy_layout(project)
    current_schema = int(getattr(project, "schema_version", 1))
    if not legacy and current_schema >= 2:
        # Repair partially-created v2/v3 layers without changing image pixels.
        changed = False
        for item in getattr(project, "angles", ()):
            if (
                getattr(item, "display_image", None) is None
                or getattr(item, "base_image", None) is None
                or getattr(item, "coverage_image", None) is None
            ):
                _ensure_layers_for_item(project, item, legacy=False)
                changed = True
        if current_schema < SCHEMA_VERSION:
            project.base_source = "LEGACY"
            project.guide_version = 0
            project.guide_shadow_amount = 50.0
            project.thumbnail_uv_bbox = (0.0, 0.0, 1.0, 1.0)
            project.guide_direction_warning = False
            project.guide_direction_message = ""
            project.author_active = False
            project.preview_enabled = False
            project.material_override_active = False
            project.schema_version = SCHEMA_VERSION
            project.dirty = True
            changed = True
        return changed

    active_uuid = ""
    if getattr(project, "angles", None):
        index = max(0, min(int(getattr(project, "active_angle_index", 0)), len(project.angles) - 1))
        active_uuid = str(project.angles[index].uuid)

    for item in list(getattr(project, "angles", ())):
        signed = float(item.angle)
        item.side = "LEFT" if signed < 0.0 else "RIGHT"
        _ensure_layers_for_item(project, item, legacy=True)
        item.angle = abs(signed)

    right_zero = next(
        (
            item
            for item in project.angles
            if str(item.side) == "RIGHT" and abs(float(item.angle)) <= 1.0e-7
        ),
        None,
    )
    left_zero = next(
        (
            item
            for item in project.angles
            if str(item.side) == "LEFT" and abs(float(item.angle)) <= 1.0e-7
        ),
        None,
    )
    if right_zero is not None and left_zero is None:
        left_zero = _clone_zero_for_left(project, right_zero)

    _migrate_boundary_sides(project, str(left_zero.uuid) if left_zero is not None else "")
    _sort_angles(project)

    project.symmetry_mode = "INDEPENDENT"
    project.mirror_enabled = False
    project.author_active = False
    project.preview_enabled = False
    project.material_override_active = False
    project.base_source = "LEGACY"
    project.guide_version = 0
    project.guide_shadow_amount = 50.0
    project.thumbnail_uv_bbox = (0.0, 0.0, 1.0, 1.0)
    project.guide_direction_warning = False
    project.guide_direction_message = ""
    project.active_angle_uuid = active_uuid
    for index, item in enumerate(project.angles):
        if str(item.uuid) == active_uuid:
            project.active_angle_index = index
            project.active_side = str(item.side)
            break
    project.schema_version = SCHEMA_VERSION
    project.dirty = True
    return True


def migrate_project_to_v3(project: Any) -> bool:
    """Named schema-v3 entry point retained alongside the v1 compatibility API."""

    return migrate_project_v1_to_v2(project)


def ensure_project_schema(project: Any) -> bool:
    """Bring ``project`` to the current schema, including partial repairs."""

    return migrate_project_v1_to_v2(project)


def migrate_all_scenes() -> tuple[str, ...]:
    """Migrate every loaded project and return non-fatal diagnostic strings."""

    try:
        import bpy
    except ImportError:
        return ("Blender is unavailable",)

    errors: list[str] = []
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            try:
                ensure_project_schema(project)
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
                message = f"Schema migration failed for {getattr(project, 'name', 'Quick SDF')}: {exc}"
                errors.append(message)
                try:
                    project.diagnostic_message = message
                except (AttributeError, ReferenceError):
                    pass
    return tuple(errors)


__all__ = [
    "ensure_project_schema",
    "migrate_all_scenes",
    "migrate_project_v1_to_v2",
    "migrate_project_to_v3",
    "split_legacy_rgba",
]

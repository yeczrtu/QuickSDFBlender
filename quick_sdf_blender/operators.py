# SPDX-License-Identifier: GPL-3.0-or-later
"""Primary Blender operators for Quick SDF projects and angle masks."""

from __future__ import annotations

import os
from pathlib import Path
import struct
import zlib

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from mathutils import Vector

from . import runtime
from .history import History


_HISTORIES: dict[str, History] = {}
_UNDO_FENCES: set[str] = set()
_EXPORT_JOB: dict[str, object] | None = None


def clear_histories(
    project_uuid: str | None = None,
    *,
    release_fence: bool = False,
) -> None:
    if project_uuid is None:
        _HISTORIES.clear()
        _UNDO_FENCES.clear()
    else:
        uuid = str(project_uuid)
        _HISTORIES.pop(uuid, None)
        if release_fence:
            _UNDO_FENCES.discard(uuid)


def arm_undo_fence(project_uuid: str) -> None:
    uuid = str(project_uuid)
    if uuid:
        _UNDO_FENCES.add(uuid)


def _project(context: bpy.types.Context):
    return runtime.active_project(context.scene)


def _require_project(operator: bpy.types.Operator, context: bpy.types.Context):
    project = _project(context)
    if project is None:
        operator.report({"ERROR"}, "Create or select a Quick SDF project first")
    return project


def _set_active_object(context: bpy.types.Context, obj: bpy.types.Object) -> None:
    if context.view_layer.objects.active is not obj:
        context.view_layer.objects.active = obj
    if not obj.select_get():
        obj.select_set(True)


def _project_entry_for_object(
    scene: bpy.types.Scene,
    obj: bpy.types.Object | None,
) -> tuple[int, object] | tuple[None, None]:
    if obj is None:
        return None, None
    for index, project in enumerate(getattr(scene, "quick_sdf_projects", ())):
        if project.target_object == obj:
            return index, project
    return None, None


def _project_for_object(scene: bpy.types.Scene, obj: bpy.types.Object | None):
    """Pure lookup; Studio commits the active index only after preflight."""

    _index, project = _project_entry_for_object(scene, obj)
    return project


def _axis_vector(name: str):
    values = {
        "NEG_X": (-1.0, 0.0, 0.0),
        "POS_X": (1.0, 0.0, 0.0),
        "NEG_Y": (0.0, -1.0, 0.0),
        "POS_Y": (0.0, 1.0, 0.0),
        "NEG_Z": (0.0, 0.0, -1.0),
        "POS_Z": (0.0, 0.0, 1.0),
    }
    return values[name]


def _extract_evaluated_bake_input(context, project):
    """Copy evaluated UV triangles and per-corner normals on the main thread."""

    import numpy as np

    obj = project.target_object
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    if mesh is None:
        raise ValueError("Could not evaluate the target mesh")
    try:
        uv_layer = mesh.uv_layers.get(project.uv_map_name)
        if uv_layer is None:
            raise ValueError("The evaluated mesh no longer contains the project UV map")
        mesh.calc_loop_triangles()
        corner_normals = mesh.corner_normals
        triangles_uv = []
        triangles_normal = []
        triangle_centers = []
        slot = int(project.material_slot_index)
        for triangle in mesh.loop_triangles:
            polygon = mesh.polygons[triangle.polygon_index]
            if polygon.material_index != slot:
                continue
            triangles_uv.append([tuple(uv_layer.data[index].uv) for index in triangle.loops])
            normals = []
            for index in triangle.loops:
                value = corner_normals[index]
                vector = getattr(value, "vector", value)
                normals.append(tuple(vector))
            triangles_normal.append(normals)
            center = sum(
                (mesh.vertices[index].co for index in triangle.vertices),
                mesh.vertices[triangle.vertices[0]].co * 0.0,
            ) / 3.0
            triangle_centers.append(tuple(center))
        if not triangles_uv:
            raise ValueError("No evaluated faces use the selected material slot")
        return (
            np.ascontiguousarray(triangles_uv, dtype=np.float32),
            np.ascontiguousarray(triangles_normal, dtype=np.float32),
            np.ascontiguousarray(triangle_centers, dtype=np.float32),
        )
    finally:
        evaluated.to_mesh_clear()


def _ensure_project_aux_images(project):
    """Create the two standard static-mask images before Studio can open."""

    from .model import ensure_standard_aux_masks, ensure_liltoon_packing

    sdf_area, shadow_strength = ensure_standard_aux_masks(
        project, uuid_factory=runtime.new_uuid
    )
    for item, fill in ((sdf_area, 0.0), (shadow_strength, 1.0)):
        image = runtime.resolve_aux_mask_image(project, item)
        if image is None:
            image = runtime.create_aux_mask_image(project, item, fill_value=fill)
            if str(item.role) == "SHADOW_STRENGTH":
                image[runtime.AUX_MASK_INITIALIZED_KEY] = True
    ensure_liltoon_packing(project)
    return sdf_area, shadow_strength


def _write_sdf_area_occupancy(project, occupancy, *, force: bool = False) -> bool:
    """Initialize or explicitly reset the canonical SDF-area mask."""

    import numpy as np

    from .model import aux_mask_for_role, mark_aux_mask_changed

    item = aux_mask_for_role(project, "SDF_AREA")
    image = runtime.resolve_aux_mask_image(project, item)
    if item is None or image is None:
        raise ValueError("The SDF Area mask is missing")
    if bool(image.get(runtime.AUX_MASK_INITIALIZED_KEY, False)) and not force:
        return False
    mask = np.asarray(occupancy, dtype=np.bool_)
    expected = (int(project.resolution), int(project.resolution))
    if mask.shape != expected:
        raise ValueError(f"SDF Area shape {mask.shape} does not match {expected}")
    rgba = np.ones((*mask.shape, 4), dtype=np.float32)
    rgba[..., :3] = mask[..., None]
    runtime.write_image_rgba(image, rgba)
    image[runtime.AUX_MASK_INITIALIZED_KEY] = True
    item.dirty = bool(force)
    mark_aux_mask_changed(project, item)
    if not force:
        item.dirty = False
    return True


def _reset_sdf_area_from_uv(context, project, *, force: bool = True) -> bool:
    from .bake import rasterize_uv_normals

    triangle_uvs, corner_normals, _centers = _extract_evaluated_bake_input(
        context, project
    )
    _normal, occupancy = rasterize_uv_normals(
        triangle_uvs,
        corner_normals,
        int(project.resolution),
        int(project.resolution),
    )
    return _write_sdf_area_occupancy(project, occupancy, force=force)


def _detect_project_symmetry(project, triangle_uvs, corner_normals, triangle_centers) -> None:
    """Suggest a live UV mirror without asking a technical setup question."""

    import numpy as np

    from .bake import rasterize_uv_normals
    from .symmetry import SymmetryMode, analyze_symmetry

    up = np.asarray(tuple(project.up_vector), dtype=np.float64)
    forward = np.asarray(tuple(project.forward_vector), dtype=np.float64)
    right = np.cross(up, forward)
    right /= max(float(np.linalg.norm(right)), 1.0e-12)
    signed = triangle_centers @ right
    positive = signed >= 0.0
    negative = ~positive
    if not np.any(positive) or not np.any(negative):
        project.symmetry_candidate = "TEXTURE_MIRROR"
        project.symmetry_confidence = 0.0
        project.symmetry_requires_confirmation = True
        project.symmetry_mode = "AUTO"
        return
    size = min(256, int(project.resolution))
    _normal, positive_occupancy = rasterize_uv_normals(
        triangle_uvs[positive], corner_normals[positive], size, size
    )
    _normal, negative_occupancy = rasterize_uv_normals(
        triangle_uvs[negative], corner_normals[negative], size, size
    )
    analysis = analyze_symmetry(
        positive_occupancy, negative_occupancy, confirmation_threshold=0.90
    )
    mapping = {
        SymmetryMode.OVERLAPPED: "OVERLAPPED_UV",
        SymmetryMode.TEXTURE_MIRROR: "TEXTURE_MIRROR",
        SymmetryMode.ISLAND_PAIR: "ISLAND_PAIR",
        SymmetryMode.INDEPENDENT: "ISLAND_PAIR",
    }
    candidate = mapping[analysis.suggested_mode]
    project.symmetry_candidate = candidate
    project.symmetry_confidence = float(analysis.confidence)
    project.symmetry_requires_confirmation = analysis.confidence < 0.90
    project.symmetry_mode = "AUTO" if project.symmetry_requires_confirmation else candidate


def _bake_project(context, project) -> None:
    """Bake evaluated corner normals and preserve every manual override."""

    import numpy as np

    from .native import bake_face_shadow_guide

    clear_histories(str(project.uuid))
    runtime.begin_base_bake(str(project.uuid))
    window_manager = context.window_manager
    window_manager.progress_begin(0, 3)
    try:
        triangle_uvs, corner_normals, triangle_centers = _extract_evaluated_bake_input(
            context, project
        )
        if not project.boundary_tracks:
            runtime.materialize_effective_coverage(project)
        window_manager.progress_update(1)
        guide_warning = False
        guide_message = ""
        for side in ("RIGHT", "LEFT"):
            items = sorted(
                (item for item in project.angles if str(item.side) == side),
                key=lambda item: float(item.angle),
            )
            if not items:
                continue
            local_angles = np.asarray([float(item.angle) for item in items], dtype=np.float64)
            masks, occupancy = bake_face_shadow_guide(
                triangle_uvs,
                corner_normals,
                local_angles,
                tuple(project.forward_vector),
                tuple(project.up_vector),
                side,
                float(project.guide_shadow_amount),
                int(project.resolution),
                int(project.resolution),
            )
            _write_sdf_area_occupancy(project, occupancy, force=False)
            if np.any(occupancy):
                rows, columns = np.nonzero(occupancy)
                height, width = occupancy.shape
                project.thumbnail_uv_bbox = (
                    float(columns.min()) / width,
                    1.0 - float(rows.max() + 1) / height,
                    float(columns.max() + 1) / width,
                    1.0 - float(rows.min()) / height,
                )
                middle = int(np.argmin(np.abs(local_angles - 45.0)))
                light_ratio = float(np.mean(masks[middle, occupancy]))
                changed = np.any(masks[1:] != masks[:-1], axis=0)
                variation = float(np.mean(changed[occupancy]))
                if light_ratio <= 0.02 or light_ratio >= 0.98 or variation < 0.01:
                    guide_warning = True
                    guide_message = "The guide is nearly uniform; confirm which way the face points"
            for item, mask in zip(items, masks):
                display = runtime.resolve_display_image(project, item)
                base = runtime.resolve_base_image(project, item)
                coverage = runtime.resolve_coverage_image(project, item)
                if display is None or base is None or coverage is None:
                    raise ValueError(f"Angle data is incomplete at {float(item.angle):g} degrees")
                base_rgba = np.ones((*mask.shape, 4), dtype=np.float32)
                base_rgba[..., :3] = mask[..., None]
                old_display = runtime.image_rgba(display)
                overridden = runtime.coverage_mask(coverage)
                composed = base_rgba.copy()
                composed[..., :3][overridden] = old_display[..., :3][overridden]
                runtime.write_image_rgba(base, base_rgba)
                runtime.write_image_rgba(display, composed)
                item.is_generated = True
                item.dirty = True
        if project.boundary_tracks:
            from .boundary import regenerate_boundary_images

            regenerate_boundary_images(project)
        window_manager.progress_update(2)
        if bool(getattr(project, "mirror_enabled", True)):
            _detect_project_symmetry(project, triangle_uvs, corner_normals, triangle_centers)
        project.base_needs_update = False
        project.base_signature = runtime.compute_base_signature(project, context.scene)
        project.base_source = "NORMAL_GUIDE"
        project.guide_version = 2
        project.guide_direction_warning = guide_warning
        project.guide_direction_message = guide_message
        project.dirty = True
        window_manager.progress_update(3)
    finally:
        window_manager.progress_end()
        runtime.end_base_bake(str(project.uuid))


def _create_project_data(context, *, sync_ui: bool = True, activate: bool = True):
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        raise ValueError("Select a mesh object first")
    if obj.library is not None or obj.data.library is not None:
        raise ValueError("Make the mesh and object local before creating a project")
    if not obj.data.uv_layers:
        raise ValueError("The active mesh needs a 0-1 UV map")
    settings = context.scene.quick_sdf_settings
    if settings.initialization == "EXISTING" and settings.source_image is None:
        raise ValueError("Select an existing mask image")
    created_material = None
    if not obj.material_slots:
        created_material = bpy.data.materials.new(f"{obj.name} Quick SDF")
        obj.data.materials.append(created_material)
    previous_index = int(getattr(context.scene, "quick_sdf_active_project_index", -1))
    project = context.scene.quick_sdf_projects.add()
    created_index = len(context.scene.quick_sdf_projects) - 1
    project.uuid = runtime.new_uuid()
    project.name = f"{obj.name} Face Shadow"
    project.target_object = obj
    project.material_slot_index = max(0, int(obj.active_material_index))
    project.uv_map_name = obj.data.uv_layers.active.name
    project.resolution = int(settings.resolution)
    project.authoring_side = "RIGHT"
    project.active_side = "RIGHT"
    project.mirror_enabled = True
    project.symmetry_mode = "AUTO"
    project.base_source = {
        "NORMAL_SWEEP": "NORMAL_GUIDE",
        "EXISTING": "IMPORTED",
        "WHITE": "WHITE",
    }.get(str(settings.initialization), "NORMAL_GUIDE")
    project.guide_version = 2 if project.base_source == "NORMAL_GUIDE" else 0
    project.guide_shadow_amount = 50.0
    try:
        source = settings.source_image if settings.initialization == "EXISTING" else None
        runtime.create_project_images(project, source)
        _ensure_project_aux_images(project)
        if settings.initialization == "NORMAL_SWEEP":
            _bake_project(context, project)
        else:
            _reset_sdf_area_from_uv(context, project, force=False)
        errors, warnings, _report = runtime.validate_project(project, include_monotonic=True)
        if errors:
            raise ValueError("\n".join(errors))
        project.warning_message = "\n".join(warnings)
        if activate:
            context.scene.quick_sdf_active_project_index = created_index
        if sync_ui:
            runtime.sync_canvas(context, project)
        return project
    except Exception:
        runtime.remove_project_images(project)
        remove_index = next(
            (
                index
                for index, candidate in enumerate(context.scene.quick_sdf_projects)
                if str(getattr(candidate, "uuid", "")) == str(getattr(project, "uuid", ""))
            ),
            None,
        )
        if remove_index is not None:
            context.scene.quick_sdf_projects.remove(remove_index)
        if context.scene.quick_sdf_projects:
            context.scene.quick_sdf_active_project_index = max(
                0, min(previous_index, len(context.scene.quick_sdf_projects) - 1)
            )
        else:
            context.scene.quick_sdf_active_project_index = -1
        if created_material is not None:
            if obj.data.materials and obj.data.materials[-1] == created_material:
                obj.data.materials.pop(index=len(obj.data.materials) - 1)
            if created_material.users == 0:
                bpy.data.materials.remove(created_material)
        raise


class QUICKSDF_OT_project_create(bpy.types.Operator):
    bl_idname = "quicksdf.project_create"
    bl_label = "Create Quick SDF Project"
    bl_description = "Create eight paint-ready face-shadow keys from the evaluated pose"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        try:
            _create_project_data(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Created and auto-baked eight face-shadow keys")
        return {"FINISHED"}


class QUICKSDF_OT_project_remove(bpy.types.Operator):
    bl_idname = "quicksdf.project_remove"
    bl_label = "Remove Quick SDF Project"
    bl_description = "Remove the active project and its generated images"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return runtime.active_project(context.scene) is not None

    def execute(self, context):
        project = _project(context)
        index = context.scene.quick_sdf_active_project_index
        shutdown_export_job(str(project.uuid), message="Export cancelled because the project was removed")
        try:
            from .studio import is_studio_active, exit_studio

            if is_studio_active(context, str(project.uuid)):
                exit_studio(context, reason="project-remove")
        except (ImportError, RuntimeError, ReferenceError):
            pass
        runtime.discard_paint_snapshot(project)
        _HISTORIES.pop(str(project.uuid), None)
        obj = project.target_object
        if obj is not None and not bpy.app.background and obj.mode == "TEXTURE_PAINT":
            try:
                _set_active_object(context, obj)
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                self.report({"ERROR"}, "Could not leave Texture Paint; project was not removed")
                return {"CANCELLED"}
        try:
            from .preview import restore_preview_materials

            restore_preview_materials(project)
        except (ImportError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, f"Could not restore preview material: {exc}")
            return {"CANCELLED"}
        runtime.remove_project_images(project)
        context.scene.quick_sdf_projects.remove(index)
        context.scene.quick_sdf_active_project_index = min(index, len(context.scene.quick_sdf_projects) - 1)
        if context.scene.quick_sdf_projects:
            runtime.sync_canvas(context)
        return {"FINISHED"}


class QUICKSDF_OT_set_forward_from_view(bpy.types.Operator):
    bl_idname = "quicksdf.set_forward_from_view"
    bl_label = "Set Forward from View"
    bl_description = "Use the current 3D view direction as character forward"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return runtime.active_project(context.scene) is not None and context.region_data is not None

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        obj = project.target_object
        if obj is None or context.region_data is None:
            return {"CANCELLED"}
        world_forward = context.region_data.view_rotation @ Vector((0.0, 0.0, 1.0))
        local_forward = (obj.matrix_world.to_quaternion().inverted() @ world_forward).normalized()
        previous_forward = tuple(project.forward_vector)
        project.forward_vector = local_forward
        try:
            _bake_project(context, project)
            runtime.sync_canvas(context, project)
        except Exception as exc:
            project.forward_vector = previous_forward
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        project.dirty = True
        return {"FINISHED"}


class QUICKSDF_OT_create_and_edit(bpy.types.Operator):
    bl_idname = "quicksdf.create_and_edit"
    bl_label = "Create & Edit"
    bl_description = "Create the face-shadow project, auto-bake the current pose, and open Studio"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        target_object = context.active_object
        _existing_index, project = _project_entry_for_object(context.scene, target_object)
        created = project is None
        previous_index = int(getattr(context.scene, "quick_sdf_active_project_index", -1))
        try:
            if project is None:
                # Do not replace the live Studio canvas while the target is
                # still being created and baked. The switch commits later.
                project = _create_project_data(context, sync_ui=False, activate=False)
            from .studio import open_or_switch_studio

            open_or_switch_studio(context, project)
        except Exception as exc:
            if created:
                remove_index, created_project = _project_entry_for_object(context.scene, target_object)
                if created_project is not None and remove_index is not None:
                    project = created_project
                    runtime.remove_project_images(project)
                    context.scene.quick_sdf_projects.remove(remove_index)
                    if context.scene.quick_sdf_projects:
                        context.scene.quick_sdf_active_project_index = max(
                            0, min(previous_index, len(context.scene.quick_sdf_projects) - 1)
                        )
                    else:
                        context.scene.quick_sdf_active_project_index = -1
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_studio_enter(bpy.types.Operator):
    bl_idname = "quicksdf.studio_enter"
    bl_label = "Open Quick SDF Studio"
    bl_description = "Open the paint canvas, 3D preview, and angle timeline in one workspace"

    def execute(self, context):
        project = _project_for_object(context.scene, context.active_object) or _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        try:
            from .studio import open_or_switch_studio

            open_or_switch_studio(context, project)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_studio_exit(bpy.types.Operator):
    bl_idname = "quicksdf.studio_exit"
    bl_label = "Exit Quick SDF"
    bl_description = "Restore the original workspace, mode, canvas, selection, and material"

    def execute(self, context):
        try:
            from .studio import exit_studio

            exit_studio(context, reason="user")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_bake_base(bpy.types.Operator):
    bl_idname = "quicksdf.bake_base"
    bl_label = "Rebake Base"
    bl_description = "Update the automatic shadow from the evaluated pose and keep painted corrections"

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        if bool(getattr(project, "onion_enabled", False)):
            project.onion_enabled = False
            runtime.sync_canvas(context, project)
        try:
            _bake_project(context, project)
            runtime.sync_canvas(context, project)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Base updated; painted corrections were preserved")
        return {"FINISHED"}


class QUICKSDF_OT_angle_set(bpy.types.Operator):
    bl_idname = "quicksdf.angle_set"
    bl_label = "Set Quick SDF Angle"
    bl_options = {"INTERNAL"}

    index: IntProperty(name="Index", default=-1)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None or not project.angles:
            return {"CANCELLED"}
        if self.index < 0:
            side = str(getattr(project, "active_side", getattr(project, "authoring_side", "RIGHT")))
            choices = [i for i, item in enumerate(project.angles) if str(item.side) == side]
            index = min(choices or range(len(project.angles)), key=lambda i: abs(project.angles[i].angle))
        else:
            index = max(0, min(self.index, len(project.angles) - 1))
        return {"FINISHED"} if _select_angle_uuid(context, project, project.angles[index].uuid) else {"CANCELLED"}


class QUICKSDF_OT_angle_step(bpy.types.Operator):
    bl_idname = "quicksdf.angle_step"
    bl_label = "Step Quick SDF Angle"
    bl_options = {"INTERNAL"}

    step: IntProperty(name="Step", default=1)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None or not project.angles:
            return {"CANCELLED"}
        current = runtime.active_angle(project)
        side = str(getattr(current, "side", getattr(project, "active_side", "RIGHT")))
        indices = [
            index for index, item in enumerate(project.angles) if str(item.side) == side
        ]
        indices.sort(key=lambda index: float(project.angles[index].angle))
        position = indices.index(int(project.active_angle_index)) if int(project.active_angle_index) in indices else 0
        position = max(0, min(position + int(self.step), len(indices) - 1))
        return bpy.ops.quicksdf.key_select(index=indices[position])


def _sort_angle_items(project) -> None:
    desired = [
        item.uuid
        for item in sorted(
            project.angles,
            key=lambda value: (
                0 if str(value.side) == "RIGHT" else 1,
                float(value.angle),
            ),
        )
    ]
    for target, uuid in enumerate(desired):
        current = next(index for index, item in enumerate(project.angles) if item.uuid == uuid)
        if current != target:
            project.angles.move(current, target)


def _select_angle_uuid(context, project, uuid: str) -> bool:
    try:
        from .studio import select_paint_key

        return select_paint_key(context, project, key_uuid=str(uuid))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, ValueError):
        return False


class QUICKSDF_OT_key_select(bpy.types.Operator):
    bl_idname = "quicksdf.key_select"
    bl_label = "Select Face Shadow Key"
    bl_options = {"INTERNAL"}

    uuid: StringProperty(name="Key UUID", default="")
    index: IntProperty(name="Index", default=-1)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None or not project.angles:
            return {"CANCELLED"}
        uuid = self.uuid
        if not uuid and self.index >= 0:
            uuid = project.angles[min(self.index, len(project.angles) - 1)].uuid
        if not uuid:
            uuid = min(project.angles, key=lambda item: abs(float(item.angle))).uuid
        return {"FINISHED"} if _select_angle_uuid(context, project, uuid) else {"CANCELLED"}


class QUICKSDF_OT_seek_set(bpy.types.Operator):
    bl_idname = "quicksdf.seek_set"
    bl_label = "Scrub Light Angle"
    bl_options = {"INTERNAL"}

    angle: FloatProperty(name="Angle", default=0.0, min=0.0, max=90.0)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        try:
            from .studio import seek_preview

            seek_preview(context, project, float(self.angle))
        except (ImportError, AttributeError, ReferenceError, RuntimeError, ValueError):
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_back_to_paint(bpy.types.Operator):
    bl_idname = "quicksdf.back_to_paint"
    bl_label = "Back to Paint"
    bl_description = "Return the 3D preview to the angle currently being painted"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        try:
            from .studio import back_to_paint

            return {"FINISHED"} if back_to_paint(context, project) else {"CANCELLED"}
        except (ImportError, AttributeError, ReferenceError, RuntimeError, ValueError):
            return {"CANCELLED"}


def _assign_angle_layers(project, item, display, base, coverage) -> None:
    item.display_image = display
    item.display_image_name = display.name
    item.base_image = base
    item.base_image_name = base.name
    item.coverage_image = coverage
    item.coverage_image_name = coverage.name


class QUICKSDF_OT_key_add(bpy.types.Operator):
    bl_idname = "quicksdf.key_add"
    bl_label = "Add Angle Key"
    bl_description = "Add a paint key at a chosen angle and initialize it from the evaluated pose"
    bl_options = {"REGISTER", "UNDO"}

    angle: FloatProperty(name="Angle", default=-1.0, min=-1.0, max=90.0)
    duplicate: BoolProperty(name="Duplicate Current", default=False)

    def invoke(self, context, _event):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        if self.angle < 0.0:
            candidate = max(0.0, min(90.0, float(project.seek_angle)))
            side = str(project.active_side or project.authoring_side)
            angles = sorted(
                float(item.angle)
                for item in project.angles
                if str(item.side) == side
            )
            if angles and any(abs(value - candidate) < 1.0e-4 for value in angles):
                position = min(
                    range(len(angles)),
                    key=lambda index: abs(angles[index] - candidate),
                )
                if position + 1 < len(angles):
                    candidate = 0.5 * (angles[position] + angles[position + 1])
                elif position > 0:
                    candidate = 0.5 * (angles[position - 1] + angles[position])
            self.angle = candidate
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, _context):
        self.layout.prop(self, "angle", text="Angle")

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        side = str(project.active_side or project.authoring_side)
        angle = float(project.seek_angle if self.angle < 0.0 else self.angle)
        angle = max(0.0, min(90.0, angle))
        if any(str(item.side) == side and abs(float(item.angle) - angle) < 1.0e-4 for item in project.angles):
            self.report({"ERROR"}, "An angle key already exists here")
            return {"CANCELLED"}
        clear_histories(str(project.uuid))
        source = runtime.active_angle(project)
        item = project.angles.add()
        item.uuid = runtime.new_uuid()
        new_uuid = str(item.uuid)
        item.angle = angle
        item.side = side
        layers = []
        for role in (runtime.DISPLAY_ROLE, runtime.BASE_ROLE, runtime.COVERAGE_ROLE):
            layers.append(
                runtime.create_angle_layer_image(
                    project.uuid, item.uuid, angle, int(project.resolution), role, side=side
                )
            )
        _assign_angle_layers(project, item, *layers)
        try:
            if self.duplicate and source is not None:
                if not project.boundary_tracks:
                    runtime.materialize_effective_coverage(project, (source,))
                for resolver, destination in zip(
                    (runtime.resolve_display_image, runtime.resolve_base_image, runtime.resolve_coverage_image),
                    layers,
                ):
                    source_image = resolver(project, source)
                    if source_image is not None:
                        runtime.copy_image_pixels(source_image, destination, grayscale=False)
                item.is_manual = True
            else:
                _bake_project(context, project)
            _sort_angle_items(project)
            _select_angle_uuid(context, project, new_uuid)
        except Exception as exc:
            for image in layers:
                if image.users == 0:
                    bpy.data.images.remove(image)
            index = next(
                (index for index, value in enumerate(project.angles) if value.uuid == new_uuid),
                -1,
            )
            if index >= 0:
                project.angles.remove(index)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_key_move(bpy.types.Operator):
    bl_idname = "quicksdf.key_move"
    bl_label = "Move Angle Key"
    bl_options = {"REGISTER", "UNDO"}

    uuid: StringProperty(name="Key UUID", default="")
    angle: FloatProperty(name="Angle", default=45.0, min=0.0, max=90.0)

    def invoke(self, context, _event):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        item = next(
            (value for value in project.angles if value.uuid == self.uuid),
            runtime.active_angle(project),
        )
        if item is None:
            return {"CANCELLED"}
        self.uuid = str(item.uuid)
        self.angle = float(item.angle)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        item = next((value for value in project.angles if value.uuid == self.uuid), runtime.active_angle(project))
        if item is None or abs(float(item.angle)) < 1.0e-5 or abs(float(item.angle) - 90.0) < 1.0e-5:
            self.report({"ERROR"}, "The 0 and 90 degree endpoints are locked")
            return {"CANCELLED"}
        if any(
            str(value.uuid) != str(item.uuid)
            and str(value.side) == str(item.side)
            and abs(float(value.angle) - self.angle) < 1.0e-4
            for value in project.angles
        ):
            self.report({"ERROR"}, "An angle key already exists here")
            return {"CANCELLED"}
        clear_histories(str(project.uuid))
        uuid = item.uuid
        item.angle = float(self.angle)
        item.retimed = True
        _sort_angle_items(project)
        _select_angle_uuid(context, project, uuid)
        project.dirty = True
        return {"FINISHED"}


class QUICKSDF_OT_key_delete(bpy.types.Operator):
    bl_idname = "quicksdf.key_delete"
    bl_label = "Delete Angle Key"
    bl_options = {"REGISTER", "UNDO"}

    uuid: StringProperty(name="Key UUID", default="")

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        item = next((value for value in project.angles if value.uuid == self.uuid), runtime.active_angle(project))
        if item is None or abs(float(item.angle)) < 1.0e-5 or abs(float(item.angle) - 90.0) < 1.0e-5:
            self.report({"ERROR"}, "The 0 and 90 degree endpoints cannot be deleted")
            return {"CANCELLED"}
        clear_histories(str(project.uuid))
        index = next(index for index, value in enumerate(project.angles) if value.uuid == item.uuid)
        images = (
            runtime.resolve_display_image(project, item),
            runtime.resolve_base_image(project, item),
            runtime.resolve_coverage_image(project, item),
        )
        project.angles.remove(index)
        for image in images:
            if image is not None and image.get(runtime.PROJECT_UUID_KEY) == project.uuid:
                bpy.data.images.remove(image)
        replacement = project.angles[max(0, min(index - 1, len(project.angles) - 1))]
        _select_angle_uuid(context, project, replacement.uuid)
        project.dirty = True
        return {"FINISHED"}


class QUICKSDF_OT_sync_canvas(bpy.types.Operator):
    bl_idname = "quicksdf.sync_canvas"
    bl_label = "Activate Selected Angle"
    bl_description = "Use the selected angle mask in Texture Paint and visible Image Editors"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        angle_item = runtime.active_angle(project)
        if angle_item is not None:
            project.review_angle = angle_item.angle
        return {"FINISHED"} if runtime.sync_canvas(context, project) is not None else {"CANCELLED"}


def _packing_channel(project, output_channel: str):
    from .model import packing_channel_for

    return packing_channel_for(project, str(output_channel).upper())


def _record_aux_image_change(project, item, image, before, label: str) -> bool:
    """Publish one static-mask edit to Quick SDF history and export revision."""

    import numpy as np

    after = runtime.image_rgba8(image)
    if before.shape == after.shape and np.array_equal(before, after):
        return False
    history = _HISTORIES.setdefault(str(project.uuid), History(compression_level=1))
    if not history.push(label, {image.name: before}, {image.name: after}):
        history.clear()
    _UNDO_FENCES.add(str(project.uuid))
    from .model import mark_aux_mask_changed

    mark_aux_mask_changed(project, item)
    return True


class QUICKSDF_OT_packing_customize(bpy.types.Operator):
    bl_idname = "quicksdf.packing_customize"
    bl_label = "Customize Output Packing"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        from .model import ensure_liltoon_packing, mark_packing_changed

        ensure_liltoon_packing(project)
        mark_packing_changed(project, customized=True)
        return {"FINISHED"}


class QUICKSDF_OT_packing_reset_liltoon(bpy.types.Operator):
    bl_idname = "quicksdf.packing_reset_liltoon"
    bl_label = "Reset Packing to lilToon"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        from .model import reset_liltoon_packing

        reset_liltoon_packing(project)
        return {"FINISHED"}


class QUICKSDF_OT_packing_assign_active_mask(bpy.types.Operator):
    bl_idname = "quicksdf.packing_assign_active_mask"
    bl_label = "Use Selected Custom Mask"
    bl_options = {"REGISTER", "UNDO"}

    output_channel: StringProperty(name="Output Channel", default="R")

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        channel = _packing_channel(project, self.output_channel)
        item = runtime.active_aux_mask(project)
        if channel is None:
            self.report({"ERROR"}, f"Output channel {self.output_channel!r} is missing")
            return {"CANCELLED"}
        if item is None or str(getattr(item, "role", "")) != "CUSTOM":
            self.report({"ERROR"}, "Select a Custom Mask first")
            return {"CANCELLED"}
        channel.source_type = "CUSTOM_MASK"
        channel.auxiliary_mask_uuid = str(item.uuid)
        return {"FINISHED"}


class QUICKSDF_OT_aux_mask_edit(bpy.types.Operator):
    bl_idname = "quicksdf.aux_mask_edit"
    bl_label = "Edit Additional Mask"

    mask_uuid: StringProperty(name="Mask UUID", default="", options={"HIDDEN"})

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        try:
            from .studio import enter_aux_mask_edit

            enter_aux_mask_edit(context, project, self.mask_uuid)
        except (RuntimeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_aux_mask_back(bpy.types.Operator):
    bl_idname = "quicksdf.aux_mask_back"
    bl_label = "Back to Face Shadow"

    mask_uuid: StringProperty(name="Mask UUID", default="", options={"HIDDEN"})

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        from .studio import leave_aux_mask_edit

        return {"FINISHED"} if leave_aux_mask_edit(context, project) else {"CANCELLED"}


class QUICKSDF_OT_aux_mask_add(bpy.types.Operator):
    bl_idname = "quicksdf.aux_mask_add"
    bl_label = "Add Custom Mask"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="Custom Mask")
    fill_value: FloatProperty(
        name="Initial Value", default=0.0, min=0.0, max=1.0, subtype="FACTOR"
    )

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        requested = self.name.strip() or "Custom Mask"
        used = {str(item.name) for item in project.aux_masks}
        name = requested
        suffix = 2
        while name in used:
            name = f"{requested} {suffix}"
            suffix += 1
        item = runtime.create_aux_mask(
            project,
            role="CUSTOM",
            name=name,
            fill_value=float(self.fill_value),
        )
        image = runtime.resolve_aux_mask_image(project, item)
        if image is not None:
            image[runtime.AUX_MASK_INITIALIZED_KEY] = True
        from .model import mark_aux_mask_changed

        mark_aux_mask_changed(project, item)
        return {"FINISHED"}


class QUICKSDF_OT_aux_mask_import(bpy.types.Operator):
    bl_idname = "quicksdf.aux_mask_import"
    bl_label = "Import Additional Mask from Image"
    bl_options = {"REGISTER", "UNDO"}

    mask_uuid: StringProperty(name="Mask UUID", default="", options={"HIDDEN"})
    source_image_name: StringProperty(name="Source Image", default="")
    component: EnumProperty(
        name="Channel",
        items=(
            ("LUMINANCE", "Luminance", "Rec.709 luminance from RGB"),
            ("R", "R", "Red channel"),
            ("G", "G", "Green channel"),
            ("B", "B", "Blue channel"),
            ("A", "A", "Alpha channel"),
        ),
        default="LUMINANCE",
    )

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        self.layout.prop_search(self, "source_image_name", bpy.data, "images", text="Source Image")
        self.layout.prop(self, "component")

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        item = runtime.aux_mask_for_uuid(project, self.mask_uuid)
        image = runtime.resolve_aux_mask_image(project, item)
        if item is None or image is None:
            self.report({"ERROR"}, "The selected additional mask is missing")
            return {"CANCELLED"}
        source_image = bpy.data.images.get(str(self.source_image_name))
        if source_image is None:
            self.report({"ERROR"}, "Choose a source image")
            return {"CANCELLED"}
        before = runtime.image_rgba8(image)
        try:
            runtime.copy_image_channel_to_aux(source_image, image, self.component)
            _record_aux_image_change(project, item, image, before, "Import Mask")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            runtime.write_image_rgba8(image, before)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        runtime.sync_canvas(context, project)
        return {"FINISHED"}


class QUICKSDF_OT_aux_mask_fill(bpy.types.Operator):
    bl_idname = "quicksdf.aux_mask_fill"
    bl_label = "Fill Additional Mask"
    bl_options = {"REGISTER", "UNDO"}

    mask_uuid: StringProperty(name="Mask UUID", default="", options={"HIDDEN"})
    value: FloatProperty(name="Value", default=1.0, min=0.0, max=1.0)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        item = runtime.aux_mask_for_uuid(project, self.mask_uuid)
        image = runtime.resolve_aux_mask_image(project, item)
        if item is None or image is None:
            return {"CANCELLED"}
        before = runtime.image_rgba8(image)
        runtime.fill_aux_mask_image(image, self.value)
        _record_aux_image_change(project, item, image, before, "Fill Mask")
        runtime.sync_canvas(context, project)
        return {"FINISHED"}


class QUICKSDF_OT_aux_mask_reset_sdf_area(bpy.types.Operator):
    bl_idname = "quicksdf.aux_mask_reset_sdf_area"
    bl_label = "Reset SDF Area from UV"
    bl_options = {"REGISTER", "UNDO"}

    mask_uuid: StringProperty(name="Mask UUID", default="", options={"HIDDEN"})

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        item = runtime.aux_mask_for_uuid(project, self.mask_uuid)
        if item is None or str(getattr(item, "role", "")) != "SDF_AREA":
            self.report({"ERROR"}, "Select the SDF Area mask")
            return {"CANCELLED"}
        image = runtime.resolve_aux_mask_image(project, item)
        if image is None:
            return {"CANCELLED"}
        before = runtime.image_rgba8(image)
        try:
            _reset_sdf_area_from_uv(context, project, force=True)
            _record_aux_image_change(project, item, image, before, "Reset SDF Area")
        except (RuntimeError, TypeError, ValueError) as exc:
            runtime.write_image_rgba8(image, before)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        runtime.sync_canvas(context, project)
        return {"FINISHED"}


class QUICKSDF_OT_aux_mask_delete(bpy.types.Operator):
    bl_idname = "quicksdf.aux_mask_delete"
    bl_label = "Delete Custom Mask"
    bl_options = {"REGISTER", "UNDO"}

    mask_uuid: StringProperty(name="Mask UUID", default="", options={"HIDDEN"})

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        item = runtime.aux_mask_for_uuid(project, self.mask_uuid)
        if item is None or str(getattr(item, "role", "")) != "CUSTOM":
            self.report({"ERROR"}, "The standard masks cannot be deleted")
            return {"CANCELLED"}
        if any(
            str(getattr(channel, "source_type", "")) == "CUSTOM_MASK"
            and str(getattr(channel, "auxiliary_mask_uuid", "")) == str(item.uuid)
            for channel in project.packing_channels
        ):
            self.report({"ERROR"}, "This mask is used by Output Packing")
            return {"CANCELLED"}
        try:
            from .studio import active_session, leave_aux_mask_edit

            session = active_session(context)
            if session is not None and session.editing_aux_mask_uuid == str(item.uuid):
                leave_aux_mask_edit(context, project)
        except (ImportError, ReferenceError, RuntimeError):
            pass
        index = next(
            (
                index
                for index, candidate in enumerate(project.aux_masks)
                if str(candidate.uuid) == str(item.uuid)
            ),
            -1,
        )
        if index < 0:
            return {"CANCELLED"}
        runtime.remove_aux_mask_image(project, item)
        project.aux_masks.remove(index)
        project.active_aux_mask_index = min(index, len(project.aux_masks) - 1)
        active = runtime.active_aux_mask(project)
        project.active_aux_mask_uuid = str(getattr(active, "uuid", ""))
        from .model import mark_packing_changed

        mark_packing_changed(project, customized=bool(project.packing_customized))
        return {"FINISHED"}


class QUICKSDF_OT_boundary_track_add(bpy.types.Operator):
    bl_idname = "quicksdf.boundary_track_add"
    bl_label = "Add Boundary Track"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        clear_histories(str(project.uuid))
        if not project.boundary_tracks:
            try:
                runtime.materialize_effective_coverage(project)
            except (RuntimeError, ValueError) as exc:
                self.report({"ERROR"}, f"Could not preserve existing paint: {exc}")
                return {"CANCELLED"}
        track = project.boundary_tracks.add()
        track.uuid = runtime.new_uuid()
        track.name = f"Boundary {len(project.boundary_tracks)}"
        track.side = str(getattr(project, "active_side", "RIGHT"))
        project.active_boundary_track_index = len(project.boundary_tracks) - 1
        angle_item = runtime.active_angle(project)
        if angle_item is not None:
            key = track.keys.add()
            key.uuid = runtime.new_uuid()
            key.angle = angle_item.angle
            key.angle_uuid = angle_item.uuid
            key.side = angle_item.side
            key.is_manual = True
            track.active_key_index = 0
        project.dirty = True
        return {"FINISHED"}


class QUICKSDF_OT_boundary_track_remove(bpy.types.Operator):
    bl_idname = "quicksdf.boundary_track_remove"
    bl_label = "Remove Boundary Track"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None or not project.boundary_tracks:
            return {"CANCELLED"}
        clear_histories(str(project.uuid))
        index = max(0, min(project.active_boundary_track_index, len(project.boundary_tracks) - 1))
        project.boundary_tracks.remove(index)
        project.active_boundary_track_index = min(index, len(project.boundary_tracks) - 1)
        try:
            from .boundary import regenerate_boundary_images

            regenerate_boundary_images(project)
        except (RuntimeError, ValueError) as exc:
            self.report({"WARNING"}, f"Track removed, but masks need regeneration: {exc}")
        project.dirty = True
        return {"FINISHED"}


class QUICKSDF_OT_paint_value_toggle(bpy.types.Operator):
    bl_idname = "quicksdf.paint_value_toggle"
    bl_label = "Toggle Light / Shadow"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        project.paint_value = 1 - int(project.paint_value)
        return {"FINISHED"}


class QUICKSDF_OT_paint_value_set(bpy.types.Operator):
    bl_idname = "quicksdf.paint_value_set"
    bl_label = "Choose Light or Shadow"
    bl_options = {"INTERNAL"}

    value: IntProperty(name="Value", default=1, min=0, max=1)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        project.paint_value = int(self.value)
        return {"FINISHED"}


class QUICKSDF_OT_studio_display_mode(bpy.types.Operator):
    bl_idname = "quicksdf.studio_display_mode"
    bl_label = "Change Studio Display"
    bl_options = {"INTERNAL"}

    mode: StringProperty(name="Mode", default="OVERLAY")

    def execute(self, context):
        project = _require_project(self, context)
        if project is None or self.mode not in {"OVERLAY", "MASK", "TOON"}:
            return {"CANCELLED"}
        project.preview_mode = self.mode
        item = runtime.active_angle(project)
        image = runtime.resolve_display_image(project, item) if item is not None else None
        try:
            from .preview import set_preview_image

            if image is not None:
                set_preview_image(project, image)
        except (RuntimeError, ValueError):
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_symmetry_choose(bpy.types.Operator):
    bl_idname = "quicksdf.symmetry_choose"
    bl_label = "Choose Mirror Layout"
    bl_options = {"REGISTER", "UNDO"}

    mode: StringProperty(name="Layout", default="TEXTURE_MIRROR")

    def execute(self, context):
        project = _require_project(self, context)
        if project is None or self.mode not in {"TEXTURE_MIRROR", "ISLAND_PAIR", "OVERLAPPED_UV"}:
            return {"CANCELLED"}
        project.symmetry_candidate = self.mode
        project.symmetry_mode = self.mode
        project.symmetry_requires_confirmation = False
        project.mirror_enabled = True
        return {"FINISHED"}


class QUICKSDF_OT_break_mirror(bpy.types.Operator):
    bl_idname = "quicksdf.break_mirror"
    bl_label = "Break Mirror"
    bl_description = "Create a separate opposite-side lane for asymmetric painting"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        import numpy as np

        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        if not bool(project.mirror_enabled):
            return {"FINISHED"}
        clear_histories(str(project.uuid))
        if not project.boundary_tracks:
            try:
                runtime.materialize_effective_coverage(project)
            except (RuntimeError, ValueError) as exc:
                self.report({"ERROR"}, f"Could not preserve existing paint: {exc}")
                return {"CANCELLED"}
        source_side = str(project.authoring_side)
        target_side = "LEFT" if source_side == "RIGHT" else "RIGHT"
        if any(str(item.side) == target_side for item in project.angles):
            project.mirror_enabled = False
            project.symmetry_mode = "INDEPENDENT"
            return {"FINISHED"}
        mode_name = str(project.symmetry_mode)
        if mode_name == "AUTO":
            mode_name = str(project.symmetry_candidate)
        mode_map = {
            "TEXTURE_MIRROR": "TEXTURE_MIRROR",
            "OVERLAPPED_UV": "OVERLAPPED",
            "ISLAND_PAIR": "ISLAND_PAIR",
        }
        pairs = None
        if mode_name == "ISLAND_PAIR":
            sample = runtime.resolve_display_image(project, next(item for item in project.angles if str(item.side) == source_side))
            pairs = _symmetry_island_pairs(project, (sample.size[1], sample.size[0]))
        from .symmetry import mirror_side_layer

        created_images = []
        created_uuids = []
        try:
            for source in sorted(
                (item for item in project.angles if str(item.side) == source_side),
                key=lambda item: float(item.angle),
            ):
                target = project.angles.add()
                target.uuid = runtime.new_uuid()
                target.angle = float(source.angle)
                target.side = target_side
                created_uuids.append(target.uuid)
                destinations = []
                for role, resolver in (
                    (runtime.DISPLAY_ROLE, runtime.resolve_display_image),
                    (runtime.BASE_ROLE, runtime.resolve_base_image),
                    (runtime.COVERAGE_ROLE, runtime.resolve_coverage_image),
                ):
                    source_image = resolver(project, source)
                    destination = runtime.create_angle_layer_image(
                        project.uuid,
                        target.uuid,
                        float(target.angle),
                        int(project.resolution),
                        role,
                        side=target_side,
                    )
                    created_images.append(destination)
                    rgba = runtime.image_rgba(source_image)
                    mirrored = mirror_side_layer(
                        rgba,
                        mode_map.get(mode_name, "TEXTURE_MIRROR"),
                        island_pairs=pairs,
                    )
                    runtime.write_image_rgba(destination, np.asarray(mirrored, dtype=np.float32))
                    destinations.append(destination)
                _assign_angle_layers(project, target, *destinations)
            project.mirror_enabled = False
            project.symmetry_mode = "INDEPENDENT"
            _sort_angle_items(project)
        except Exception as exc:
            for uuid in reversed(created_uuids):
                index = next((i for i, item in enumerate(project.angles) if item.uuid == uuid), -1)
                if index >= 0:
                    project.angles.remove(index)
            for image in created_images:
                if image.name in bpy.data.images:
                    bpy.data.images.remove(image)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class QUICKSDF_OT_mirror_toggle(bpy.types.Operator):
    bl_idname = "quicksdf.mirror_toggle"
    bl_label = "Toggle Mirror"
    bl_description = "Link or separate the opposite face-light side"

    def invoke(self, context, event):
        project = _project(context)
        if project is not None and bool(project.mirror_enabled):
            # The main Studio control is a reassuring state indicator, not a
            # destructive toggle. Asymmetry is entered explicitly via the
            # Advanced "Break Mirror" action.
            return {"FINISHED"}
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        if bool(project.mirror_enabled):
            return {"FINISHED"}
        clear_histories(str(project.uuid))
        keep_side = str(project.authoring_side)
        remove = [
            index for index, item in enumerate(project.angles) if str(item.side) != keep_side
        ]
        for index in reversed(remove):
            item = project.angles[index]
            images = (
                runtime.resolve_display_image(project, item),
                runtime.resolve_base_image(project, item),
                runtime.resolve_coverage_image(project, item),
            )
            project.angles.remove(index)
            for image in images:
                if image is not None and image.get(runtime.PROJECT_UUID_KEY) == project.uuid:
                    bpy.data.images.remove(image)
        project.mirror_enabled = True
        project.symmetry_mode = str(project.symmetry_candidate or "TEXTURE_MIRROR")
        _sort_angle_items(project)
        if project.angles:
            _select_angle_uuid(context, project, project.angles[0].uuid)
        return {"FINISHED"}


def _symmetry_island_pairs(project, shape):
    import numpy as np

    from .boundary import rasterize_closed_curve, uv_boundary_loops
    from .symmetry import IslandPair

    obj = project.target_object
    uv_layer = obj.data.uv_layers.get(project.uv_map_name) if obj is not None else None
    if uv_layer is None:
        return []
    faces = [
        [tuple(uv_layer.data[index].uv) for index in polygon.loop_indices]
        for polygon in obj.data.polygons
        if polygon.material_index == int(project.material_slot_index)
    ]
    loops = uv_boundary_loops(faces)
    height, width = shape
    records = []
    for loop in loops:
        if len(loop) < 3:
            continue
        mask = np.asarray(rasterize_closed_curve(loop, width, height), dtype=np.bool_).reshape(height, width)
        mask = np.flip(mask, axis=0).copy()
        centroid = np.mean(np.asarray(loop, dtype=np.float64), axis=0)
        records.append((loop, mask, centroid))
    pairs = []
    unused = set(range(len(records)))
    while unused:
        source_index = min(unused)
        unused.remove(source_index)
        source = records[source_index]
        if not unused:
            pairs.append(IslandPair(source[1], source[1]))
            break
        target_point = np.array((1.0 - source[2][0], source[2][1]))
        target_index = min(unused, key=lambda i: float(np.linalg.norm(records[i][2] - target_point)))
        unused.remove(target_index)
        target = records[target_index]
        # Both directions are included because a face layout may place either
        # island on the source side of the positive-light authoring mask.
        pairs.append(IslandPair(source[1], target[1]))
        pairs.append(IslandPair(target[1], source[1]))
    return pairs


class QUICKSDF_OT_clear_overrides(bpy.types.Operator):
    bl_idname = "quicksdf.clear_overrides"
    bl_label = "Clear Paint Overrides"
    bl_description = "Clear alpha override flags for the configured angle range"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        import numpy as np

        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        clear_histories(str(project.uuid))
        from .core import range_target_indices

        values = [item.angle for item in project.angles]
        indices = range_target_indices(values, project.active_angle_index, project.apply_target)
        if not project.boundary_tracks:
            try:
                runtime.materialize_effective_coverage(
                    project,
                    (project.angles[int(index)] for index in indices),
                )
            except (RuntimeError, ValueError) as exc:
                self.report({"ERROR"}, f"Could not read the current paint: {exc}")
                return {"CANCELLED"}
        snapshots = {}
        if project.boundary_tracks:
            for angle_item in project.angles:
                image = runtime.resolve_display_image(project, angle_item)
                if image is not None:
                    snapshots[image.name] = runtime.image_rgba(image)
        for index in indices:
            image = runtime.resolve_display_image(project, project.angles[int(index)])
            if image is not None:
                runtime.clear_image_alpha(image)
        try:
            from .boundary import regenerate_boundary_images

            regenerate_boundary_images(project)
        except (RuntimeError, ValueError) as exc:
            for image_name, rgba in snapshots.items():
                image = bpy.data.images.get(image_name)
                if image is not None:
                    region = np.ones(rgba.shape[:2], dtype=np.bool_)
                    runtime.restore_image_region(image, rgba, region)
            self.report({"ERROR"}, f"Could not clear overrides: {exc}")
            return {"CANCELLED"}
        project.dirty = True
        return {"FINISHED"}


def _gradient_footprint(footprint, ratio: float, falloff: float):
    """Contract a stroke footprint by its exact interior distance."""
    import numpy as np

    if ratio <= 0.0 or not np.any(footprint):
        return footprint.copy()
    from .core import exact_signed_edt

    area = float(np.count_nonzero(footprint))
    equivalent_radius = max(1.0, (area / np.pi) ** 0.5)
    erosion = equivalent_radius * min(0.98, ratio * max(0.01, float(falloff)))
    signed = exact_signed_edt(footprint)
    return footprint & ((-signed) >= erosion)


def _resolve_selected_monotonic(candidate, angles, selected):
    """Resolve violations when the native stroke already invalidated baseline.

    Only selected angle images may change.  A closer light pixel is propagated
    outward when possible; otherwise the closer proposed pixel is clipped.
    """
    import numpy as np

    result = candidate.copy()
    selected_set = {int(index) for index in selected}
    angle_values = np.asarray(angles, dtype=np.float64)
    clipped = np.zeros_like(result, dtype=np.bool_)
    repairs = np.zeros_like(result, dtype=np.bool_)
    for _pass in range(result.shape[0] * 2):
        changed = False
        for sign in (-1, 1):
            indices = np.flatnonzero((angle_values * sign > 0.0) | np.isclose(angle_values, 0.0))
            indices = indices[np.argsort(np.abs(angle_values[indices]), kind="stable")]
            for closer, farther in zip(indices[:-1], indices[1:]):
                invalid = result[closer] & ~result[farther]
                if not np.any(invalid):
                    continue
                if int(farther) in selected_set:
                    result[farther][invalid] = True
                    repairs[farther] |= invalid
                    changed = True
                elif int(closer) in selected_set:
                    result[closer][invalid] = False
                    clipped[closer] |= invalid
                    changed = True
        if not changed:
            break
    return result, clipped, repairs


class QUICKSDF_OT_propagate_overrides(bpy.types.Operator):
    bl_idname = "quicksdf.propagate_overrides"
    bl_label = "Apply Smart Paint"
    bl_description = "Keep the face-shadow keys consistent after one native paint stroke"
    bl_options = {"INTERNAL"}

    invert: BoolProperty(name="Invert Stroke", default=False, options={"HIDDEN"})

    def execute(self, context):
        import numpy as np

        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}

        def finish_stroke(*, no_change: bool = False) -> None:
            try:
                from .studio import restore_stroke_brush, set_projection_hint

                set_projection_hint(context, no_change=no_change)
                restore_stroke_brush(context)
            except (ImportError, AttributeError, ReferenceError, RuntimeError):
                pass

        def complete_active_stroke(active_item, source_image) -> None:
            source_image[runtime.IMAGE_REVISION_KEY] = int(
                source_image.get(runtime.IMAGE_REVISION_KEY, 0)
            ) + 1
            active_item.is_manual = True
            active_item.dirty = True
            project.first_stroke_complete = True
            project.dirty = True
            try:
                from .live_preview import invalidate

                invalidate(str(project.uuid))
            except ImportError:
                pass
            try:
                from .studio import dismiss_first_stroke_hint, tag_studio_redraw

                dismiss_first_stroke_hint()
                tag_studio_redraw()
            except (ImportError, RuntimeError):
                pass

        aux_snapshot = runtime.consume_aux_paint_snapshot(project)
        if aux_snapshot is not None:
            mask_uuid, image_name, before_rgba = aux_snapshot
            item = runtime.aux_mask_for_uuid(project, mask_uuid)
            image = bpy.data.images.get(image_name)
            try:
                if item is None or image is None:
                    raise ValueError("The additional mask disappeared during the stroke")
                current = runtime.image_rgba8(image)
                if current.shape != before_rgba.shape:
                    raise ValueError("The additional mask changed size during the stroke")
                if np.any(current[..., 3] != 255):
                    current[..., 3] = 255
                    runtime.write_image_rgba8(image, current)
                changed = np.any(current[..., :3] != before_rgba[..., :3], axis=2)
                if not np.any(changed):
                    return {"FINISHED"}
                image[runtime.IMAGE_REVISION_KEY] = int(
                    image.get(runtime.IMAGE_REVISION_KEY, 0)
                ) + 1
                _record_aux_image_change(
                    project,
                    item,
                    image,
                    before_rgba,
                    "Paint Additional Mask",
                )
                try:
                    from .studio import tag_studio_redraw

                    tag_studio_redraw()
                except (ImportError, RuntimeError):
                    pass
                return {"FINISHED"}
            except Exception as exc:
                clear_histories(str(project.uuid))
                if image is not None:
                    try:
                        runtime.write_image_rgba8(image, before_rgba)
                    except Exception:
                        pass
                self.report({"ERROR"}, f"Could not finish the mask stroke: {exc}")
                return {"CANCELLED"}
            finally:
                finish_stroke()

        interactive_snapshot = runtime.consume_interactive_paint_snapshot(project)
        if interactive_snapshot is not None:
            active_uuid, display_name, before_rgba, coverage_name, coverage_before = interactive_snapshot
            active_item = runtime.active_angle(project)
            source_image = bpy.data.images.get(display_name)
            coverage_image = bpy.data.images.get(coverage_name) if coverage_name else None
            try:
                if source_image is None or (coverage_name and coverage_image is None):
                    raise ValueError("The active angle paint layers disappeared")
                if active_item is None or str(active_item.uuid) != active_uuid:
                    raise ValueError("The active angle changed before the stroke finished")
                source_rgba = runtime.image_rgba8(source_image)
                if source_rgba.shape != before_rgba.shape or (
                    coverage_before is not None and coverage_before.shape != before_rgba.shape
                ):
                    raise ValueError("The active paint image changed size during the stroke")
                # Work images are always opaque. Erase-alpha Brush Assets may
                # still alter the hidden buffer, so normalize that metadata
                # without treating it as an artist-visible paint action.
                if np.any(source_rgba[..., 3] != 255):
                    source_rgba[..., 3] = 255
                    runtime.write_image_rgba8(source_image, source_rgba)
                touched = np.any(source_rgba[..., :3] != before_rgba[..., :3], axis=2)
                if not np.any(touched):
                    return {"FINISHED"}
                _UNDO_FENCES.add(str(project.uuid))
                before_history = {source_image.name: before_rgba}
                after_history = {source_image.name: source_rgba}
                if coverage_image is not None and coverage_before is not None:
                    coverage_after = coverage_before.copy()
                    coverage_after[..., :3][touched] = 255
                    coverage_after[..., 3] = 255
                    runtime.write_image_rgba8(coverage_image, coverage_after)
                    before_history[coverage_image.name] = coverage_before
                    after_history[coverage_image.name] = coverage_after
                history = _HISTORIES.setdefault(
                    str(project.uuid),
                    History(compression_level=1),
                )
                if not history.push("Paint", before_history, after_history):
                    # Never let Undo silently skip an unrecorded newest stroke
                    # and apply an older entry instead.
                    history.clear()
                complete_active_stroke(active_item, source_image)
                return {"FINISHED"}
            except Exception as exc:
                clear_histories(str(project.uuid))
                try:
                    if source_image is not None:
                        runtime.write_image_rgba8(source_image, before_rgba)
                except Exception:
                    pass
                try:
                    if coverage_image is not None and coverage_before is not None:
                        runtime.write_image_rgba8(coverage_image, coverage_before)
                except Exception:
                    pass
                self.report({"ERROR"}, f"Could not finish the paint stroke: {exc}")
                return {"CANCELLED"}
            finally:
                finish_stroke()

        # Interactive Studio paint deliberately stays on Blender's native fast
        # path. The former implementation copied and rewrote every 1024px angle
        # layer after each stroke, blocking input for seconds and repeatedly
        # rebuilding the preview material. Explicit scripts that captured a
        # snapshot retain the compatibility propagation path below.
        if not runtime.has_paint_snapshot(project):
            active_item = runtime.active_angle(project)
            source_image = runtime.resolve_display_image(project, active_item) if active_item else None
            if source_image is None:
                finish_stroke()
                self.report({"ERROR"}, "The active angle image is missing")
                return {"CANCELLED"}
            complete_active_stroke(active_item, source_image)
            finish_stroke()
            return {"FINISHED"}

        active_item = runtime.active_angle(project)
        source_image = runtime.resolve_display_image(project, active_item)
        if source_image is None:
            finish_stroke()
            self.report({"ERROR"}, "The active angle image is missing")
            return {"CANCELLED"}
        source_rgba = runtime.image_rgba(source_image)
        snapshot = runtime.consume_paint_snapshot(project)
        if snapshot is None or snapshot.shape != source_rgba.shape:
            finish_stroke()
            return {"CANCELLED"}
        touched = np.any(
            np.abs(source_rgba[..., :3] - snapshot[..., :3]) > (0.5 / 255.0), axis=2
        )
        if not np.any(touched):
            finish_stroke(no_change=True)
            return {"FINISHED"}
        _UNDO_FENCES.add(str(project.uuid))
        before_history: dict[str, np.ndarray] = {}
        affected = []
        metadata_before: dict[str, tuple[bool, bool]] = {}
        project_flags_before = (
            bool(getattr(project, "first_stroke_complete", False)),
            bool(getattr(project, "dirty", False)),
        )
        try:
            from .smart_paint import apply_smart_transitions

            side = str(active_item.side)
            items = sorted(
                (item for item in project.angles if str(item.side) == side),
                key=lambda item: float(item.angle),
            )
            active_index = next(index for index, item in enumerate(items) if item.uuid == active_item.uuid)
            angles = np.asarray([float(item.angle) for item in items], dtype=np.float64)
            masks = np.stack(
                [
                    (snapshot[..., 0] >= 0.5)
                    if item.uuid == active_item.uuid
                    else runtime.image_mask(runtime.resolve_display_image(project, item))
                    for item in items
                ],
                axis=0,
            )
            coverage = np.stack(
                [runtime.coverage_mask(runtime.resolve_coverage_image(project, item)) for item in items],
                axis=0,
            )
            before_mask = snapshot[..., 0] >= 0.5
            after_mask = source_rgba[..., 0] >= 0.5
            became_light = touched & ~before_mask & after_mask
            became_shadow = touched & before_mask & ~after_mask
            result = apply_smart_transitions(
                masks,
                coverage,
                angles,
                active_index,
                touched,
                became_light,
                became_shadow,
            )
            for index in result.affected_indices:
                item = items[index]
                display = runtime.resolve_display_image(project, item)
                coverage_image = runtime.resolve_coverage_image(project, item)
                if display is None or coverage_image is None:
                    raise ValueError("A Smart Paint image is missing")
                before_history[display.name] = (
                    runtime.rgba_to_u8(snapshot)
                    if item.uuid == active_item.uuid
                    else runtime.image_rgba8(display)
                )
                before_history[coverage_image.name] = runtime.image_rgba8(coverage_image)
                metadata_before[str(item.uuid)] = (
                    bool(getattr(item, "is_manual", False)),
                    bool(getattr(item, "dirty", False)),
                )
                affected.append((index, item, display, coverage_image))
            for index, item, display, coverage_image in affected:
                key_footprint = result.footprints[index]
                runtime.write_mask_overrides(
                    display,
                    result.masks[index],
                    key_footprint,
                    coverage_image=coverage_image,
                )
                if item.uuid == active_item.uuid:
                    antialiased = runtime.image_rgba(display)
                    antialiased[..., :3][touched] = source_rgba[..., :3][touched]
                    runtime.write_image_rgba(display, antialiased)
            after_history = {
                name: runtime.image_rgba8(bpy.data.images[name]) for name in before_history
            }
            # Publish per-key metadata only after every image write and readback
            # succeeded.  A failed multi-image stroke can then roll pixels back
            # without leaving keys marked as manually edited.
            for _index, item, _display, _coverage_image in affected:
                item.is_manual = True
                item.dirty = True
            history = _HISTORIES.setdefault(
                str(project.uuid), History(compression_level=1)
            )
            if not history.push("Smart Paint", before_history, after_history):
                history.clear()
            try:
                from .live_preview import invalidate

                invalidate(str(project.uuid))
            except ImportError:
                pass
            project.first_stroke_complete = True
            try:
                from .studio import dismiss_first_stroke_hint

                dismiss_first_stroke_hint()
            except (ImportError, RuntimeError):
                pass
            project.dirty = True
        except Exception as exc:
            clear_histories(str(project.uuid))
            if before_history:
                for name, rgba in before_history.items():
                    image = bpy.data.images.get(name)
                    if image is not None:
                        try:
                            runtime.write_image_rgba8(image, rgba)
                        except Exception:
                            pass
            # Collection can fail after earlier angle images were captured but
            # before the active canvas was added. Restore that native stroke as
            # well; a CANCELLED compatibility operation must never leave paint
            # behind merely because ``before_history`` is partially populated.
            if source_image.name not in before_history:
                try:
                    runtime.write_image_rgba(source_image, snapshot)
                except Exception:
                    pass
            for _index, item, _display, _coverage_image in affected:
                previous = metadata_before.get(str(getattr(item, "uuid", "")))
                if previous is not None:
                    try:
                        item.is_manual, item.dirty = previous
                    except (AttributeError, ReferenceError):
                        pass
            try:
                project.first_stroke_complete, project.dirty = project_flags_before
            except (AttributeError, ReferenceError):
                pass
            finish_stroke()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finish_stroke()
        runtime.sync_canvas(context, project)
        return {"FINISHED"}


class QUICKSDF_OT_paint_snapshot(bpy.types.Operator):
    bl_idname = "quicksdf.paint_snapshot"
    bl_label = "Capture Quick SDF Paint Snapshot"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        aux_mask_uuid = ""
        try:
            from .studio import active_session, back_to_paint, is_studio_active

            if is_studio_active(context, str(project.uuid)):
                session = active_session(context)
                aux_mask_uuid = str(
                    getattr(session, "editing_aux_mask_uuid", "")
                    if session is not None
                    else ""
                )
                if not aux_mask_uuid and not back_to_paint(context, project):
                    return {"CANCELLED"}
        except (ImportError, AttributeError, ReferenceError, RuntimeError):
            pass
        try:
            from .studio import prepare_stroke_brush

            prepare_stroke_brush(context, project)
        except (ImportError, AttributeError, ReferenceError, RuntimeError):
            pass
        if not aux_mask_uuid and bool(getattr(project, "onion_enabled", False)):
            project.onion_enabled = False
            runtime.sync_canvas(context, project)
        runtime.discard_interactive_paint_snapshot(project)
        runtime.discard_aux_paint_snapshot(project)
        try:
            if aux_mask_uuid:
                runtime.capture_aux_paint_snapshot(project, aux_mask_uuid)
            else:
                runtime.capture_interactive_paint_snapshot(
                    project,
                    include_coverage=bool(project.boundary_tracks),
                )
        except (RuntimeError, ValueError) as exc:
            try:
                from .studio import restore_stroke_brush

                restore_stroke_brush(context)
            except (ImportError, AttributeError, ReferenceError, RuntimeError):
                pass
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        # The snapshot contains only the selected key. Angle consistency is
        # repaired non-destructively by the existing export pipeline.
        return {"FINISHED"}


class _QuickSDFPaintMacroMixin:
    @classmethod
    def poll(cls, context):
        project = runtime.active_project(getattr(context, "scene", None))
        if project is None:
            return False
        try:
            from .studio import active_session, is_studio_active

            active = is_studio_active(context, str(project.uuid))
            session = active_session(context)
            export_review_active = bool(
                session is not None and getattr(session, "export_review_active", False)
            )
        except (ImportError, ReferenceError, RuntimeError):
            active = False
            export_review_active = False
        editor_paint = getattr(context, "mode", "") == "PAINT_TEXTURE" or (
            getattr(getattr(context, "area", None), "type", "") == "IMAGE_EDITOR"
            and getattr(getattr(context, "space_data", None), "mode", "") == "PAINT"
        )
        return bool(
            active
            and not export_review_active
            and editor_paint
            and getattr(project, "author_tool", "PAINT") == "PAINT"
            and not bool(getattr(project, "job_running", False))
        )


class QUICKSDF_OT_range_paint(_QuickSDFPaintMacroMixin, bpy.types.Macro):
    bl_idname = "quicksdf.range_paint"
    bl_label = "Quick SDF Range Paint"
    bl_description = "Paint the selected angle with Blender's native brush"
    bl_options = {"UNDO", "INTERNAL"}


class QUICKSDF_OT_range_paint_invert(_QuickSDFPaintMacroMixin, bpy.types.Macro):
    bl_idname = "quicksdf.range_paint_invert"
    bl_label = "Quick SDF Inverted Range Paint"
    bl_description = "Temporarily invert the native paint action on the selected angle"
    bl_options = {"UNDO", "INTERNAL"}


def register_macros() -> None:
    """Build paint macros after all component operators have been registered."""
    QUICKSDF_OT_range_paint.define("QUICKSDF_OT_paint_snapshot")
    normal = QUICKSDF_OT_range_paint.define("PAINT_OT_image_paint")
    normal.properties.mode = "NORMAL"
    QUICKSDF_OT_range_paint.define("QUICKSDF_OT_propagate_overrides")
    QUICKSDF_OT_range_paint_invert.define("QUICKSDF_OT_paint_snapshot")
    inverted = QUICKSDF_OT_range_paint_invert.define("PAINT_OT_image_paint")
    inverted.properties.mode = "INVERT"
    propagated = QUICKSDF_OT_range_paint_invert.define("QUICKSDF_OT_propagate_overrides")
    propagated.properties.invert = True


def _history_images(names) -> dict[str, object]:
    result = {}
    for name in names:
        image = bpy.data.images.get(name)
        if image is not None:
            result[name] = runtime.image_rgba8(image)
    return result


def _write_history_images(values) -> None:
    for name, rgba in values.items():
        image = bpy.data.images.get(name)
        if image is None:
            raise ValueError(f"The history image {name!r} is missing")
        runtime.write_image_rgba8(image, rgba)


def _mark_history_images_changed(project, image_names) -> None:
    """Keep persistent mask revisions in step with Quick SDF undo/redo."""

    aux_items = []
    for name in image_names:
        image = bpy.data.images.get(name)
        if image is None or str(image.get(runtime.ROLE_KEY, "")) != runtime.AUX_MASK_ROLE:
            continue
        item = runtime.aux_mask_for_uuid(
            project, str(image.get(runtime.AUX_MASK_UUID_KEY, ""))
        )
        if item is not None and item not in aux_items:
            aux_items.append(item)
    if aux_items:
        for item in aux_items:
            item.revision = int(getattr(item, "revision", 0)) + 1
            item.dirty = True
        project.packing_revision = int(getattr(project, "packing_revision", 0)) + 1
    project.dirty = True


def _history_context_active(context, project) -> bool:
    # Background/API callers have no editor area and retain scripting access.
    if getattr(context, "area", None) is None:
        return True
    try:
        from .studio import is_studio_active

        return bool(is_studio_active(context, str(getattr(project, "uuid", ""))))
    except (AttributeError, ImportError, ReferenceError, RuntimeError):
        return False


class QUICKSDF_OT_history_undo(bpy.types.Operator):
    bl_idname = "quicksdf.history_undo"
    bl_label = "Undo Quick SDF Stroke"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        project = runtime.active_project(getattr(context, "scene", None))
        uuid = str(getattr(project, "uuid", ""))
        history = _HISTORIES.get(uuid)
        return bool(
            project
            and _history_context_active(context, project)
            and ((history and history.can_undo) or uuid in _UNDO_FENCES)
            and not bool(getattr(project, "job_running", False))
        )

    def execute(self, context):
        project = _require_project(self, context)
        uuid = str(project.uuid)
        history = _HISTORIES.get(uuid)
        if history is None or not history.can_undo:
            return {"FINISHED"}
        current = _history_images(history.undo_keys)
        moved = False
        try:
            restored = history.undo(current)
            moved = True
            _write_history_images(restored)
            _mark_history_images_changed(project, restored)
        except Exception as exc:
            # ``History`` is pure array state, while Blender Image writes may
            # fail halfway through. Move the entry back and restore every
            # original buffer so one Ctrl+Z is transactional.
            try:
                if moved:
                    history.redo(current)
            except Exception:
                clear_histories(uuid)
            try:
                _write_history_images(current)
            except Exception:
                clear_histories(uuid)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        runtime.sync_canvas(context, project)
        return {"FINISHED"}


class QUICKSDF_OT_history_redo(bpy.types.Operator):
    bl_idname = "quicksdf.history_redo"
    bl_label = "Redo Quick SDF Stroke"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        project = runtime.active_project(getattr(context, "scene", None))
        uuid = str(getattr(project, "uuid", ""))
        history = _HISTORIES.get(uuid)
        return bool(
            project
            and _history_context_active(context, project)
            and ((history and history.can_redo) or uuid in _UNDO_FENCES)
            and not bool(getattr(project, "job_running", False))
        )

    def execute(self, context):
        project = _require_project(self, context)
        history = _HISTORIES.get(str(project.uuid))
        if history is None or not history.can_redo:
            return {"FINISHED"}
        current = _history_images(history.redo_keys)
        moved = False
        try:
            restored = history.redo(current)
            moved = True
            _write_history_images(restored)
            _mark_history_images_changed(project, restored)
        except Exception as exc:
            try:
                if moved:
                    history.undo(current)
            except Exception:
                clear_histories(str(project.uuid))
            try:
                _write_history_images(current)
            except Exception:
                clear_histories(str(project.uuid))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        runtime.sync_canvas(context, project)
        return {"FINISHED"}


class QUICKSDF_OT_validate(bpy.types.Operator):
    bl_idname = "quicksdf.validate"
    bl_label = "Validate Quick SDF"
    bl_description = "Validate project inputs and monotonic angle transitions"

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        try:
            errors, warnings, report = runtime.validate_project(project, include_monotonic=True)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if errors:
            self.report({"WARNING"}, errors[-1])
        elif warnings:
            self.report({"WARNING"}, warnings[0])
        else:
            self.report({"INFO"}, "Face-shadow keys are ready to export")
        return {"FINISHED"}


def _prepare_threshold_inputs(project):
    """Copy every bpy-backed export layer before work moves to a worker."""

    # Resolve the recipe first so a broken reference is reported against its
    # visible R/G/B/A row instead of being hidden behind a generic project
    # validation error.
    packing = _snapshot_packing_inputs(project)
    previous_status = (
        str(getattr(project, "validation_message", "")),
        str(getattr(project, "warning_message", "")),
        str(getattr(project, "diagnostic_message", "")),
        bool(getattr(project, "has_violations", False)),
    )
    try:
        errors, _warnings, _report = runtime.validate_project(
            project, include_monotonic=False
        )
    finally:
        (
            project.validation_message,
            project.warning_message,
            project.diagnostic_message,
            project.has_violations,
        ) = previous_status
    if errors:
        raise ValueError(errors[0])
    mirror_mode = str(getattr(project, "symmetry_mode", "AUTO"))
    if mirror_mode == "AUTO":
        mirror_mode = str(getattr(project, "symmetry_candidate", "TEXTURE_MIRROR"))
    linked = bool(getattr(project, "mirror_enabled", True)) and mirror_mode != "INDEPENDENT"
    if not linked:
        return {
            "linked": False,
            "right": runtime.project_side_export_layers(project, "RIGHT"),
            "left": runtime.project_side_export_layers(project, "LEFT"),
            "packing": packing,
        }

    available = {
        str(getattr(item, "side", "RIGHT")) for item in getattr(project, "angles", ())
    }
    author_side = str(getattr(project, "authoring_side", "RIGHT"))
    if author_side not in available:
        author_side = "RIGHT" if "RIGHT" in available else "LEFT"
    source = runtime.project_side_export_layers(project, author_side)
    pairs = None
    if mirror_mode == "ISLAND_PAIR":
        pairs = _symmetry_island_pairs(project, source[0].shape[1:])
    mode_map = {
        "OVERLAPPED_UV": "OVERLAPPED",
        "TEXTURE_MIRROR": "TEXTURE_MIRROR",
        "ISLAND_PAIR": "ISLAND_PAIR",
    }
    return {
        "linked": True,
        "author_side": author_side,
        "source": source,
        "mirror_mode": mode_map.get(mirror_mode, "TEXTURE_MIRROR"),
        "island_pairs": pairs,
        "packing": packing,
    }


def _snapshot_packing_inputs(project):
    """Copy the project-local recipe and all referenced masks on the main thread."""

    from .model import aux_mask_for_role
    from .packing import PackingChannelSpec

    channels = {}
    for item in getattr(project, "packing_channels", ()):
        output = str(getattr(item, "output_channel", "")).upper()
        if output not in "RGBA":
            raise ValueError(f"Output Packing has an invalid channel {output!r}")
        if output in channels:
            raise ValueError(f"Output Packing has more than one {output} row")
        channels[output] = item
    missing = [output for output in "RGBA" if output not in channels]
    if missing:
        raise ValueError(f"Output Packing is missing {', '.join(missing)}")

    specs = []
    signals = {}
    expected = (int(project.resolution), int(project.resolution))
    for output in "RGBA":
        channel = channels[output]
        source = str(getattr(channel, "source_type", "")).upper()
        mask_uuid = str(getattr(channel, "auxiliary_mask_uuid", ""))
        if source in {"SDF_AREA", "SHADOW_STRENGTH"}:
            item = aux_mask_for_role(project, source)
            if item is None:
                raise ValueError(f"Packing {output}: {source.replace('_', ' ').title()} is missing")
            mask_uuid = str(item.uuid)
        elif source == "CUSTOM_MASK":
            item = runtime.aux_mask_for_uuid(project, mask_uuid)
            if item is None or str(getattr(item, "role", "")) != "CUSTOM":
                raise ValueError(f"Packing {output}: select a valid Custom Mask")
        else:
            item = None
        if item is not None:
            image = runtime.resolve_aux_mask_image(project, item)
            if image is None:
                raise ValueError(f"Packing {output}: mask image is missing")
            if tuple(map(int, image.size[:])) != (expected[1], expected[0]):
                raise ValueError(
                    f"Packing {output}: mask size {tuple(image.size[:])} does not match {expected[::-1]}"
                )
            signals[mask_uuid] = runtime.image_channel_u16(image, 0)
        try:
            specs.append(
                PackingChannelSpec(
                    source=source,
                    invert=bool(getattr(channel, "invert", False)),
                    constant_value=float(getattr(channel, "constant_value", 0.0)),
                    auxiliary_mask_uuid=mask_uuid,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Packing {output}: {exc}") from exc
    return {"specs": tuple(specs), "signals": signals, "shape": expected}


def _export_revision_token(project):
    """Fingerprint every export-relevant setting and image revision."""

    entries = []
    for item in sorted(
        getattr(project, "angles", ()),
        key=lambda value: (
            str(getattr(value, "side", "RIGHT")),
            float(getattr(value, "angle", 0.0)),
            str(getattr(value, "uuid", "")),
        ),
    ):
        layers = []
        for resolver in (
            runtime.resolve_display_image,
            runtime.resolve_base_image,
            runtime.resolve_coverage_image,
        ):
            image = resolver(project, item)
            layers.append(
                None
                if image is None
                else (
                    str(image.name),
                    tuple(int(value) for value in image.size[:]),
                    int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                )
            )
        entries.append(
            (
                str(getattr(item, "uuid", "")),
                str(getattr(item, "side", "RIGHT")),
                float(getattr(item, "angle", 0.0)),
                tuple(layers),
            )
        )
    aux_entries = []
    for item in sorted(
        getattr(project, "aux_masks", ()),
        key=lambda value: str(getattr(value, "uuid", "")),
    ):
        image = runtime.resolve_aux_mask_image(project, item)
        aux_entries.append(
            (
                str(getattr(item, "uuid", "")),
                str(getattr(item, "role", "")),
                int(getattr(item, "revision", 0)),
                None
                if image is None
                else (
                    str(image.name),
                    tuple(int(value) for value in image.size[:]),
                    int(image.get(runtime.IMAGE_REVISION_KEY, 0)),
                ),
            )
        )
    packing_entries = tuple(
        (
            str(getattr(item, "output_channel", "")),
            str(getattr(item, "source_type", "")),
            str(getattr(item, "auxiliary_mask_uuid", "")),
            bool(getattr(item, "invert", False)),
            float(getattr(item, "constant_value", 0.0)),
        )
        for item in getattr(project, "packing_channels", ())
    )
    return (
        str(getattr(project, "uuid", "")),
        str(getattr(getattr(project, "target_object", None), "name_full", "")),
        int(getattr(project, "material_slot_index", 0)),
        str(getattr(project, "uv_map_name", "")),
        int(getattr(project, "resolution", 0)),
        bool(getattr(project, "mirror_enabled", True)),
        str(getattr(project, "symmetry_mode", "AUTO")),
        str(getattr(project, "symmetry_candidate", "TEXTURE_MIRROR")),
        str(getattr(project, "authoring_side", "RIGHT")),
        int(getattr(project, "packing_revision", 0)),
        packing_entries,
        tuple(aux_entries),
        tuple(entries),
    )


def _prepare_strict_threshold_inputs(project):
    """Preserve the public ``quicksdf.generate`` strict validation contract."""

    from .core import validate_side_monotonic

    errors, _warnings, _report = runtime.validate_project(project, include_monotonic=False)
    if errors:
        raise ValueError(errors[0])
    pairs = None
    mirror_mode = str(getattr(project, "symmetry_mode", "AUTO"))
    if mirror_mode == "AUTO":
        mirror_mode = str(getattr(project, "symmetry_candidate", "TEXTURE_MIRROR"))
    if bool(getattr(project, "mirror_enabled", True)) and mirror_mode == "ISLAND_PAIR":
        sample_item = next(iter(project.angles), None)
        sample = (
            runtime.resolve_display_image(project, sample_item)
            if sample_item is not None
            else None
        )
        if sample is not None:
            pairs = _symmetry_island_pairs(project, (sample.size[1], sample.size[0]))
    right, right_angles, left, left_angles = runtime.project_side_stacks(
        project, island_pairs=pairs
    )
    reports = (
        validate_side_monotonic(right, right_angles),
        validate_side_monotonic(left, left_angles),
    )
    project.has_violations = any(not report.is_valid for report in reports)
    if project.has_violations:
        count = sum(report.violation_pixel_count for report in reports)
        raise ValueError(
            f"Some imported pixels change in the wrong direction ({count} pixels)"
        )
    return right, right_angles, left, left_angles


def _compute_threshold_channels(inputs, cancel_flag=None):
    """Generate canonical R/G planes; ctypes releases the GIL in native EDT."""

    from .core import generate_threshold_pair_channels

    right, right_angles, left, left_angles = inputs
    try:
        from . import native

        if native.available() and native.version() >= 5:
            return native.generate_threshold_pair(
                right, right_angles, left, left_angles, cancel_flag=cancel_flag
            )
    except (ImportError, OSError, AttributeError):
        pass
    return generate_threshold_pair_channels(
        right, right_angles, left, left_angles, validate=True
    )


def _pack_threshold_channels(channels, packing):
    from .packing import PackingSource, pack_rgba16

    signals = dict(packing["signals"])
    signals[PackingSource.RIGHT_THRESHOLD] = channels[..., 0]
    signals[PackingSource.LEFT_THRESHOLD] = channels[..., 1]
    return pack_rgba16(
        signals,
        packing["specs"],
        shape=packing.get("shape", channels.shape[:2]),
    )


def _repair_export_lane(lane, cancel_flag=None):
    """Repair one copied lane and verify the derived result strictly."""

    display, angles, base, coverage = lane
    try:
        from . import native

        repair = native.repair_side_monotonic(
            display, base, coverage, cancel_flag=cancel_flag
        )
    except (ImportError, OSError, AttributeError):
        from .core import repair_side_monotonic

        repair = repair_side_monotonic(display, base, coverage)
    from .core import validate_side_monotonic

    report = validate_side_monotonic(repair.masks, angles)
    if not report.is_valid:
        raise RuntimeError("Automatic angle repair did not produce a valid export stack")
    return repair


def _change_heatmap(changed_mask):
    import numpy as np

    changed = np.asarray(changed_mask, dtype=np.bool_)
    return np.count_nonzero(changed, axis=0).astype(np.float32) / float(changed.shape[0])


def _compute_export_result(inputs, cancel_flag=None):
    """Repair copied stacks, then generate a byte-compatible threshold image."""

    import numpy as np

    from .symmetry import mirror_side_layer, mirror_side_stack

    def cancelled():
        return bool(cancel_flag is not None and int(getattr(cancel_flag, "value", 0)))

    if cancelled():
        raise RuntimeError("Export cancelled")

    if bool(inputs["linked"]):
        source = inputs["source"]
        repair = _repair_export_lane(source, cancel_flag)
        if cancelled():
            raise RuntimeError("Export cancelled")
        mode = inputs["mirror_mode"]
        pairs = inputs.get("island_pairs")
        mirrored = mirror_side_stack(repair.masks, mode, island_pairs=pairs)
        angles = np.array(source[1], copy=True)
        if inputs["author_side"] == "RIGHT":
            threshold_inputs = (repair.masks, angles, mirrored, angles)
        else:
            threshold_inputs = (mirrored, angles, repair.masks, angles)
        source_heatmap = _change_heatmap(repair.changed_mask)
        heatmap = np.maximum(
            source_heatmap,
            mirror_side_layer(source_heatmap, mode, island_pairs=pairs),
        )
        repairs = (repair,)
    else:
        right = _repair_export_lane(inputs["right"], cancel_flag)
        if cancelled():
            raise RuntimeError("Export cancelled")
        left = _repair_export_lane(inputs["left"], cancel_flag)
        threshold_inputs = (
            right.masks,
            inputs["right"][1],
            left.masks,
            inputs["left"][1],
        )
        heatmap = np.maximum(
            _change_heatmap(right.changed_mask),
            _change_heatmap(left.changed_mask),
        )
        repairs = (right, left)

    if cancelled():
        raise RuntimeError("Export cancelled")
    channels = _compute_threshold_channels(threshold_inputs, cancel_flag)
    rgba = _pack_threshold_channels(channels, inputs["packing"])
    if cancelled():
        raise RuntimeError("Export cancelled")
    return {
        "rgba": rgba,
        "heatmap": np.ascontiguousarray(heatmap, dtype=np.float32),
        "changed_sample_count": sum(item.changed_sample_count for item in repairs),
        "changed_pixel_count": sum(item.changed_pixel_count for item in repairs),
        "protected_changed_sample_count": sum(
            item.protected_changed_sample_count for item in repairs
        ),
        "protected_changed_pixel_count": sum(
            item.protected_changed_pixel_count for item in repairs
        ),
    }


def _publish_export_result(project, result) -> None:
    rgba = result["rgba"]
    runtime.update_threshold_preview(project, rgba)
    changed_pixels = int(result["changed_pixel_count"])
    project.export_adjustment_pixel_count = changed_pixels
    project.export_adjustment_sample_count = int(result["changed_sample_count"])
    project.export_adjustment_protected_pixel_count = int(
        result["protected_changed_pixel_count"]
    )
    project.has_violations = changed_pixels > 0
    if changed_pixels:
        runtime.update_export_adjustment_preview(project, result["heatmap"])
        project.validation_message = "Adjusted for export"
    else:
        runtime.clear_export_adjustment_preview(project)
        project.validation_message = "Generated"
    project.diagnostic_message = ""
    project.export_failed = False


def _project_by_uuid(uuid: str):
    for scene in bpy.data.scenes:
        for project in getattr(scene, "quick_sdf_projects", ()):
            if str(project.uuid) == str(uuid):
                return project
    return None


def _finish_export_job(message: str, *, error: bool = False, wait: bool = True) -> None:
    global _EXPORT_JOB
    job = _EXPORT_JOB
    _EXPORT_JOB = None
    if job is None:
        return
    manager = job.get("manager")
    if manager is not None:
        manager.shutdown(wait=wait)
    project = _project_by_uuid(str(job.get("project_uuid", "")))
    if project is not None:
        project.job_running = False
        project.job_progress = 0.0 if error else 1.0
        project.job_message = message
        if error:
            project.diagnostic_message = message
            project.export_failed = True
    # Release executor/thread objects before Blender can immediately close or
    # disable the extension on the following UI event.
    job.clear()
    manager = None
    import gc

    gc.collect()


def _poll_export_job() -> float | None:
    job = _EXPORT_JOB
    if job is None:
        return None
    if "settled_message" in job:
        _finish_export_job(str(job["settled_message"]))
        return None
    from .jobs import JobState

    manager = job["manager"]
    project = _project_by_uuid(str(job["project_uuid"]))
    if project is None:
        shutdown_export_job(
            message="Export cancelled because the project was removed", wait=True
        )
        return None
    state = manager.poll()
    if state in {JobState.PENDING, JobState.RUNNING}:
        project.job_progress = min(0.92, float(project.job_progress) + 0.015)
        project.job_message = "Generating face shadow texture…"
        return 0.1
    try:
        result = manager.take_result()
        project.job_progress = 0.95
        if _export_revision_token(project) != job.get("revision_token"):
            _finish_export_job(
                "Export paused because the project changed; retry to save the latest paint",
                error=True,
            )
            return None
        from .png16 import write_png_rgba16

        written = write_png_rgba16(
            job["path"], result["rgba"], overwrite=bool(job["overwrite"])
        )
        project.output_path = str(written)
    except Exception as exc:
        _finish_export_job(f"Export failed: {exc}", error=True)
        return None
    try:
        _publish_export_result(project, result)
    except Exception as exc:
        # The atomic PNG is already safely on disk. Review imagery is helpful
        # but must never turn a successful file write into a failed Export.
        project.warning_message = f"Exported, but could not update review preview: {exc}"
        project.export_failed = False
    project.dirty = False
    # End and collect the worker before publishing completion. Blender's image
    # and GPU caches then receive one event-loop interval before an immediate
    # Exit/Save/quit can tear down the Studio.
    manager.shutdown(wait=True)
    job["manager"] = None
    if int(result["changed_pixel_count"]):
        job["settled_message"] = "Adjusted angle continuity and exported"
    else:
        job["settled_message"] = f"Exported {written}"
    project.job_progress = 1.0
    project.job_message = "Finalizing export…"
    import gc

    gc.collect()
    return 0.5


def _start_export_job(project, inputs, path: Path, overwrite: bool) -> None:
    global _EXPORT_JOB
    if _EXPORT_JOB is not None:
        raise RuntimeError("Another Quick SDF export is already running")
    from .jobs import GenerationJobManager
    import ctypes

    revision_token = _export_revision_token(project)
    manager = GenerationJobManager()
    cancel_flag = ctypes.c_int(0)
    manager.submit(_compute_export_result, inputs, cancel_flag)
    _EXPORT_JOB = {
        "manager": manager,
        "project_uuid": str(project.uuid),
        "path": Path(path),
        "overwrite": bool(overwrite),
        "cancel_flag": cancel_flag,
        "revision_token": revision_token,
    }
    project.job_running = True
    project.job_progress = 0.01
    project.job_message = "Generating face shadow texture…"
    if not bpy.app.timers.is_registered(_poll_export_job):
        bpy.app.timers.register(_poll_export_job, first_interval=0.05)


def shutdown_export_job(
    project_uuid: str = "", *, message: str = "Export cancelled", wait: bool = True
) -> bool:
    global _EXPORT_JOB
    job = _EXPORT_JOB
    if job is None or (project_uuid and str(job.get("project_uuid", "")) != str(project_uuid)):
        return False
    manager = job.get("manager")
    cancel_flag = job.get("cancel_flag")
    if cancel_flag is not None:
        cancel_flag.value = 1
    if manager is not None:
        manager.cancel()
    _finish_export_job(message, error=True, wait=wait)
    if bpy.app.timers.is_registered(_poll_export_job):
        bpy.app.timers.unregister(_poll_export_job)
    return True


def _generate(project, context):
    inputs = _prepare_strict_threshold_inputs(project)
    packing = _snapshot_packing_inputs(project)
    window_manager = context.window_manager
    window_manager.progress_begin(0, 2)
    try:
        window_manager.progress_update(1)
        channels = _compute_threshold_channels(inputs)
        rgba = _pack_threshold_channels(channels, packing)
        runtime.update_threshold_preview(project, rgba)
        window_manager.progress_update(2)
    finally:
        window_manager.progress_end()
    project.dirty = False
    project.validation_message = "Generated"
    return rgba


def _generate_export(project, context):
    inputs = _prepare_threshold_inputs(project)
    window_manager = context.window_manager
    window_manager.progress_begin(0, 2)
    try:
        window_manager.progress_update(1)
        result = _compute_export_result(inputs)
        window_manager.progress_update(2)
    finally:
        window_manager.progress_end()
    return result


class QUICKSDF_OT_packing_preview_channel(bpy.types.Operator):
    bl_idname = "quicksdf.packing_preview_channel"
    bl_label = "Preview Packed Channel"
    bl_description = "Show the current packed output or one channel as grayscale"

    output_channel: EnumProperty(
        name="Channel",
        items=(
            ("RGB", "RGB", "Packed RGB preview"),
            ("R", "R", "Red channel"),
            ("G", "G", "Green channel"),
            ("B", "B", "Blue channel"),
            ("A", "A", "Alpha channel"),
        ),
        default="RGB",
    )

    def execute(self, context):
        import numpy as np

        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        try:
            result = _generate_export(project, context)
            rgba = np.array(result["rgba"], copy=True, order="C")
            if self.output_channel in "RGBA" and self.output_channel != "RGB":
                index = "RGBA".index(self.output_channel)
                plane = rgba[..., index].copy()
                rgba[..., :3] = plane[..., None]
            rgba[..., 3] = np.uint16(65535)
            image = runtime.update_threshold_preview(project, rgba)
            project.packing_preview_channel = self.output_channel
            from .studio import show_export_adjustment_review

            if not show_export_adjustment_review(context, project, image):
                raise RuntimeError("Open Quick SDF Studio to preview packed channels")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Showing packed {self.output_channel}")
        return {"FINISHED"}


class QUICKSDF_OT_generate(bpy.types.Operator):
    bl_idname = "quicksdf.generate"
    bl_label = "Generate Threshold Texture"
    bl_description = "Generate the 16-bit R/G light-angle threshold image"

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        try:
            _generate(project, context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Threshold texture generated")
        return {"FINISHED"}


class QUICKSDF_OT_export_texture(bpy.types.Operator):
    bl_idname = "quicksdf.export_texture"
    bl_label = "Export Face Shadow Texture"
    bl_description = "Check, generate, and save the finished 16-bit face-shadow PNG"

    filepath: StringProperty(name="File Path", subtype="FILE_PATH", default="")
    overwrite: BoolProperty(name="Overwrite", default=False)
    confirmed: BoolProperty(name="Confirmed", default=False, options={"HIDDEN", "SKIP_SAVE"})
    check_existing: BoolProperty(name="Check Existing", default=True, options={"HIDDEN", "SKIP_SAVE"})
    from_file_selector: BoolProperty(
        name="Chosen in File Browser", default=False, options={"HIDDEN", "SKIP_SAVE"}
    )

    def invoke(self, context, event):
        project = _project(context)
        if project is None:
            return {"CANCELLED"}
        saved = str(getattr(project, "output_path", ""))
        if saved:
            self.filepath = saved
            path = Path(bpy.path.abspath(saved))
            if path.exists() and not self.overwrite:
                self.confirmed = True
                return context.window_manager.invoke_confirm(self, event)
            return self.execute(context)
        self.filepath = f"{project.name.replace(' ', '_')}_FaceShadow.png"
        self.from_file_selector = True
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        path_text = self.filepath or project.output_path
        if not path_text:
            self.report({"ERROR"}, "Choose an output PNG path")
            return {"CANCELLED"}
        path = Path(bpy.path.abspath(path_text))
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        # Keep the chosen destination even when validation, generation or I/O
        # fails so the artist can retry without navigating the file browser.
        project.output_path = str(path)
        project.export_failed = False
        # Blender's file browser performs its own existing-file confirmation.
        # Once it returns to execute, that explicit confirmation is sufficient;
        # script calls still need overwrite=True.
        allow_overwrite = bool(
            self.overwrite or self.confirmed or self.from_file_selector or project.overwrite
        )
        if path.exists() and not allow_overwrite:
            project.export_failed = True
            project.job_message = f"Export failed: File already exists: {path}"
            project.diagnostic_message = project.job_message
            self.report({"ERROR"}, f"File already exists: {path}")
            return {"CANCELLED"}
        if not bpy.app.background and getattr(context, "window", None) is not None:
            try:
                inputs = _prepare_threshold_inputs(project)
                _start_export_job(project, inputs, path, allow_overwrite)
                self.report({"INFO"}, "Generating face shadow texture")
                return {"FINISHED"}
            except (OSError, ValueError, RuntimeError) as exc:
                project.export_failed = True
                project.job_message = f"Export failed: {exc}"
                project.diagnostic_message = project.job_message
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
        from .png16 import write_png_rgba16

        try:
            result = _generate_export(project, context)
            written = write_png_rgba16(path, result["rgba"], overwrite=allow_overwrite)
        except (FileExistsError, OSError, ValueError, RuntimeError) as exc:
            project.export_failed = True
            project.job_message = f"Export failed: {exc}"
            project.diagnostic_message = project.job_message
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        project.output_path = str(written)
        try:
            _publish_export_result(project, result)
        except Exception as exc:
            project.warning_message = f"Exported, but could not update review preview: {exc}"
            project.export_failed = False
        project.dirty = False
        if int(result["changed_pixel_count"]):
            project.job_message = "Adjusted angle continuity and exported"
            from .i18n import tr

            self.report({"INFO"}, tr(project.job_message))
        else:
            project.job_message = f"Exported {written}"
            self.report({"INFO"}, project.job_message)
        return {"FINISHED"}


class QUICKSDF_OT_review_export_adjustments(bpy.types.Operator):
    bl_idname = "quicksdf.review_export_adjustments"
    bl_label = "Review Export Adjustments"
    bl_description = "Show pixels changed only in the exported texture"

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        image = getattr(project, "export_adjustment_image", None)
        if image is None or image.get(runtime.ROLE_KEY) != runtime.EXPORT_ADJUSTMENT_ROLE:
            self.report({"WARNING"}, "Export an adjusted texture before opening its heatmap")
            return {"CANCELLED"}
        try:
            from .studio import show_export_adjustment_review

            if show_export_adjustment_review(context, project, image):
                from .i18n import tr

                self.report(
                    {"INFO"},
                    tr(
                        "Export adjustments are shown read-only; choose an angle to return"
                    ),
                )
                return {"FINISHED"}
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            pass
        windows = list(getattr(context.window_manager, "windows", ()))
        current_window = getattr(context, "window", None)
        if current_window in windows:
            windows.remove(current_window)
            windows.insert(0, current_window)
        for window in windows:
            for area in window.screen.areas:
                if area.type == "IMAGE_EDITOR":
                    space = area.spaces.active
                    if hasattr(space, "ui_mode"):
                        space.ui_mode = "VIEW"
                    elif hasattr(space, "mode"):
                        space.mode = "VIEW"
                    space.image = image
                    area.tag_redraw()
                    from .i18n import tr

                    self.report(
                        {"INFO"},
                        tr(
                            "Export adjustments are shown in the Image Editor; choose an angle to return"
                        ),
                    )
                    return {"FINISHED"}
        self.report({"WARNING"}, "Open Quick SDF Studio to review the export heatmap")
        return {"CANCELLED"}


class QUICKSDF_OT_cancel_job(bpy.types.Operator):
    bl_idname = "quicksdf.cancel_job"
    bl_label = "Cancel"
    bl_description = "Cancel the running Quick SDF generation"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        project = runtime.active_project(getattr(context, "scene", None))
        return bool(project is not None and getattr(project, "job_running", False))

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        return (
            {"FINISHED"}
            if shutdown_export_job(
                str(project.uuid), message="Export cancelled", wait=True
            )
            else {"CANCELLED"}
        )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _write_mask_png(path: Path, mask, overwrite: bool) -> None:
    import numpy as np

    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(mask, dtype=np.uint8) * np.uint8(255)
    height, width = data.shape
    scanlines = b"".join(b"\0" + data[row].tobytes() for row in range(height))
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(scanlines, 6))
    png += _png_chunk(b"IEND", b"")
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temporary.write_bytes(png)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class QUICKSDF_OT_export_mask_sequence(bpy.types.Operator):
    bl_idname = "quicksdf.export_mask_sequence"
    bl_label = "Export Review Masks"
    bl_description = "Export the binary authoring masks as 8-bit grayscale PNG files"

    directory: StringProperty(name="Directory", subtype="DIR_PATH", default="")

    def execute(self, context):
        project = _require_project(self, context)
        if project is None:
            return {"CANCELLED"}
        directory_text = self.directory or context.scene.quick_sdf_settings.mask_sequence_directory
        directory = Path(bpy.path.abspath(directory_text))
        try:
            masks, angles = runtime.project_mask_stack(project)
            for mask, angle in zip(masks, angles):
                sign = "p" if angle >= 0 else "m"
                filename = f"mask_{sign}{abs(int(round(float(angle)))):03d}.png"
                _write_mask_png(directory / filename, mask, bool(project.overwrite))
        except (FileExistsError, OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(project.angles)} review masks")
        return {"FINISHED"}


CLASSES = (
    QUICKSDF_OT_project_create,
    QUICKSDF_OT_project_remove,
    QUICKSDF_OT_set_forward_from_view,
    QUICKSDF_OT_create_and_edit,
    QUICKSDF_OT_studio_enter,
    QUICKSDF_OT_studio_exit,
    QUICKSDF_OT_bake_base,
    QUICKSDF_OT_angle_set,
    QUICKSDF_OT_angle_step,
    QUICKSDF_OT_key_select,
    QUICKSDF_OT_seek_set,
    QUICKSDF_OT_back_to_paint,
    QUICKSDF_OT_key_add,
    QUICKSDF_OT_key_move,
    QUICKSDF_OT_key_delete,
    QUICKSDF_OT_sync_canvas,
    QUICKSDF_OT_packing_customize,
    QUICKSDF_OT_packing_reset_liltoon,
    QUICKSDF_OT_packing_assign_active_mask,
    QUICKSDF_OT_aux_mask_edit,
    QUICKSDF_OT_aux_mask_back,
    QUICKSDF_OT_aux_mask_add,
    QUICKSDF_OT_aux_mask_import,
    QUICKSDF_OT_aux_mask_fill,
    QUICKSDF_OT_aux_mask_reset_sdf_area,
    QUICKSDF_OT_aux_mask_delete,
    QUICKSDF_OT_boundary_track_add,
    QUICKSDF_OT_boundary_track_remove,
    QUICKSDF_OT_paint_value_toggle,
    QUICKSDF_OT_paint_value_set,
    QUICKSDF_OT_studio_display_mode,
    QUICKSDF_OT_symmetry_choose,
    QUICKSDF_OT_break_mirror,
    QUICKSDF_OT_mirror_toggle,
    QUICKSDF_OT_clear_overrides,
    QUICKSDF_OT_propagate_overrides,
    QUICKSDF_OT_paint_snapshot,
    QUICKSDF_OT_range_paint,
    QUICKSDF_OT_range_paint_invert,
    QUICKSDF_OT_history_undo,
    QUICKSDF_OT_history_redo,
    QUICKSDF_OT_validate,
    QUICKSDF_OT_packing_preview_channel,
    QUICKSDF_OT_generate,
    QUICKSDF_OT_export_texture,
    QUICKSDF_OT_review_export_adjustments,
    QUICKSDF_OT_cancel_job,
    QUICKSDF_OT_export_mask_sequence,
)

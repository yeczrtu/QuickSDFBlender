"""Quick SDF preview node group and reversible material assignment."""

from __future__ import annotations

import json
from typing import Any


NODE_GROUP_NAME = "QSDF_Preview_v1"
NODE_GROUP_INTERNAL_NAME = "QSDF_Preview_v1 (Quick SDF Internal)"
NODE_GROUP_OWNER_KEY = "quick_sdf_preview_contract_owner"
MATERIAL_PREFIX = "QSDF Preview - "
RESTORE_KEY = "_quick_sdf_material_restore_v1"
SOURCE_MATERIAL_KEY = "quick_sdf_preview_source_material"
_SAVE_SUSPENDED_PROJECT_UUID = ""


try:
    import bpy
except ImportError:  # pragma: no cover
    bpy = None


def _active_project(scene: Any) -> Any | None:
    projects = getattr(scene, "quick_sdf_projects", None)
    if projects is None:
        projects = getattr(scene, "qsdf_projects", None)
    if not projects:
        return None
    index = int(getattr(scene, "quick_sdf_active_project_index", getattr(scene, "qsdf_active_project_index", 0)))
    return projects[max(0, min(index, len(projects) - 1))]


def _project_uuid(project: Any) -> str:
    value = str(getattr(project, "uuid", "") or "project")
    return value


def _target_object(project: Any) -> Any | None:
    if bpy is None:
        return None
    target = getattr(project, "target_object", None)
    if target is None and getattr(project, "target_object_name", ""):
        target = bpy.data.objects.get(project.target_object_name)
    return target


def _set_project_string(project: Any, name: str, value: str) -> None:
    try:
        setattr(project, name, value)
    except (AttributeError, TypeError):
        try:
            project[name] = value
        except (AttributeError, TypeError):
            pass


if bpy is not None:
    from bpy.app.handlers import persistent

    def _interface_socket(group: Any, name: str, in_out: str, socket_type: str) -> Any:
        for item in group.interface.items_tree:
            if getattr(item, "item_type", "") == "SOCKET" and item.name == name and item.in_out == in_out:
                return item
        return group.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)


    def ensure_preview_node_group() -> Any:
        """Return the fixed preview group, repairing its public interface if needed."""
        group = bpy.data.node_groups.get(NODE_GROUP_NAME)
        if group is not None and not bool(group.get(NODE_GROUP_OWNER_KEY, False)):
            sockets = {
                (getattr(item, "name", ""), getattr(item, "in_out", ""))
                for item in group.interface.items_tree
                if getattr(item, "item_type", "") == "SOCKET"
            }
            required = {
                ("Mask", "INPUT"),
                ("Light Color", "INPUT"),
                ("Shadow Color", "INPUT"),
                ("Shader", "OUTPUT"),
            }
            if required.issubset(sockets):
                # A user supplied a compatible preview contract. Use it exactly
                # as authored instead of destructively rebuilding its nodes.
                return group
            group = bpy.data.node_groups.get(NODE_GROUP_INTERNAL_NAME)
        if group is None:
            name = NODE_GROUP_NAME if bpy.data.node_groups.get(NODE_GROUP_NAME) is None else NODE_GROUP_INTERNAL_NAME
            group = bpy.data.node_groups.new(name, "ShaderNodeTree")
        group[NODE_GROUP_OWNER_KEY] = True
        mask_socket = _interface_socket(group, "Mask", "INPUT", "NodeSocketFloat")
        mask_socket.default_value = 1.0
        mask_socket.min_value = 0.0
        mask_socket.max_value = 1.0
        light_socket = _interface_socket(group, "Light Color", "INPUT", "NodeSocketColor")
        light_socket.default_value = (1.0, 1.0, 1.0, 1.0)
        shadow_socket = _interface_socket(group, "Shadow Color", "INPUT", "NodeSocketColor")
        shadow_socket.default_value = (0.02, 0.02, 0.025, 1.0)
        _interface_socket(group, "Shader", "OUTPUT", "NodeSocketShader")

        nodes = group.nodes
        links = group.links
        input_node = nodes.get("QSDF Group Input") or nodes.new("NodeGroupInput")
        input_node.name = "QSDF Group Input"
        input_node.label = "Quick SDF Inputs"
        input_node.location = (-500, 0)
        mix = nodes.get("QSDF Light Shadow Mix") or nodes.new("ShaderNodeMixRGB")
        mix.name = "QSDF Light Shadow Mix"
        mix.blend_type = "MIX"
        mix.location = (-250, 20)
        surface = nodes.get("QSDF Toon Surface")
        if surface is not None and surface.bl_idname != "ShaderNodeEmission":
            nodes.remove(surface)
            surface = None
        surface = surface or nodes.new("ShaderNodeEmission")
        surface.name = "QSDF Toon Surface"
        surface.location = (0, 20)
        output_node = nodes.get("QSDF Group Output") or nodes.new("NodeGroupOutput")
        output_node.name = "QSDF Group Output"
        output_node.location = (260, 20)

        for link in tuple(links):
            if link.to_node in {mix, surface, output_node}:
                links.remove(link)
        links.new(input_node.outputs["Mask"], mix.inputs["Fac"])
        links.new(input_node.outputs["Shadow Color"], mix.inputs[1])
        links.new(input_node.outputs["Light Color"], mix.inputs[2])
        links.new(mix.outputs["Color"], surface.inputs["Color"])
        surface.inputs["Strength"].default_value = 1.0
        links.new(surface.outputs["Emission"], output_node.inputs["Shader"])
        return group


    def ensure_preview_material(project: Any, image: Any | None = None) -> Any:
        """Create a reversible Original + Paint Overlay material.

        The artist's material is copied rather than replaced with an unrelated
        black emission shader.  A small emission overlay is mixed on top of the
        copied surface, so sculptural shading and material identity stay
        readable while the white/black authoring decision remains obvious.
        """
        uuid = _project_uuid(project)
        source = getattr(project, "original_material", None)
        source_name = source.name if source is not None else ""
        stored_name = getattr(project, "preview_material_name", "")
        if not stored_name:
            try:
                stored_name = project.get("preview_material_name", "")
            except AttributeError:
                stored_name = ""
        material = bpy.data.materials.get(stored_name) if stored_name else None
        if material is not None and str(material.get(SOURCE_MATERIAL_KEY, "")) != source_name:
            # The target slot changed since the last Studio session.  Keeping a
            # stale copy would be surprising, so replace only our temporary ID.
            bpy.data.materials.remove(material)
            material = None
        if material is None:
            material = source.copy() if source is not None else bpy.data.materials.new(
                f"{MATERIAL_PREFIX}{uuid[:8]}"
            )
            material.name = f"{MATERIAL_PREFIX}{uuid[:8]}"
            material[SOURCE_MATERIAL_KEY] = source_name
            material.use_fake_user = False
            _set_project_string(project, "preview_material_name", material.name)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links

        material_outputs = tuple(
            node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"
        )
        output = next(
            (node for node in material_outputs if bool(getattr(node, "is_active_output", False))),
            material_outputs[0] if material_outputs else None,
        )
        if output is None:
            output = nodes.new("ShaderNodeOutputMaterial")
        if hasattr(output, "is_active_output"):
            output.is_active_output = True
        output.name = "QSDF Material Output"
        output.location = (680, 0)
        group_node = nodes.get("QSDF Preview") or nodes.new("ShaderNodeGroup")
        group_node.name = "QSDF Preview"
        group_node.label = NODE_GROUP_NAME
        group_node.node_tree = ensure_preview_node_group()
        group_node.location = (120, -180)
        texture = nodes.get("QSDF Mask") or nodes.new("ShaderNodeTexImage")
        texture.name = "QSDF Mask"
        texture.label = "Current Angle Mask"
        texture.location = (-180, -120)
        texture.interpolation = "Linear"
        texture.extension = "CLIP"
        uv_map = nodes.get("QSDF UV Map") or nodes.new("ShaderNodeUVMap")
        uv_map.name = "QSDF UV Map"
        uv_map.label = "Project UV Map"
        uv_map.location = (-390, -120)
        uv_map.uv_map = str(getattr(project, "uv_map_name", ""))
        if image is not None:
            texture.image = image
            try:
                image.colorspace_settings.name = "Non-Color"
            except (AttributeError, TypeError):
                pass

        surface_link = next((link for link in output.inputs["Surface"].links), None)
        mix_shader = nodes.get("QSDF Original Overlay") or nodes.new("ShaderNodeMixShader")
        mix_shader.name = "QSDF Original Overlay"
        mix_shader.label = "Original + Paint Overlay"
        mix_shader.location = (430, 20)
        # On the first build the existing output link is the copied original
        # shader. On later calls it already points to our mix and is left alone.
        original_socket = None
        def same_node(first: Any, second: Any) -> bool:
            try:
                return int(first.as_pointer()) == int(second.as_pointer())
            except (AttributeError, ReferenceError):
                return first == second

        if surface_link is not None and not same_node(surface_link.from_node, mix_shader):
            original_socket = surface_link.from_socket
        elif mix_shader.inputs[1].is_linked:
            candidate = mix_shader.inputs[1].links[0].from_socket
            # ``bpy`` may return a fresh Python wrapper for the same RNA node.
            # Identity checks therefore let an existing Mix output become its
            # own input on the next preview refresh, producing a black cycle.
            if not same_node(candidate.node, mix_shader):
                original_socket = candidate
        if original_socket is None:
            base = nodes.get("QSDF Fallback Surface") or nodes.new("ShaderNodeBsdfPrincipled")
            base.name = "QSDF Fallback Surface"
            base.location = (80, 180)
            original_socket = base.outputs["BSDF"]

        preview_mode = str(getattr(project, "preview_mode", "OVERLAY"))
        mix_shader.inputs[0].default_value = 0.35 if preview_mode == "OVERLAY" else 1.0
        if "Light Color" in group_node.inputs and "Shadow Color" in group_node.inputs:
            if preview_mode == "MASK":
                group_node.inputs["Light Color"].default_value = (1.0, 1.0, 1.0, 1.0)
                group_node.inputs["Shadow Color"].default_value = (0.0, 0.0, 0.0, 1.0)
            else:
                group_node.inputs["Light Color"].default_value = (1.0, 0.55, 0.22, 1.0)
                group_node.inputs["Shadow Color"].default_value = (0.08, 0.32, 0.72, 1.0)

        for link in tuple(links):
            if (link.to_node == texture and link.to_socket.name == "Vector") or (
                link.to_node == group_node and link.to_socket.name == "Mask"
            ) or (
                link.to_node == output and link.to_socket.name == "Surface"
            ) or (
                link.to_node == mix_shader and link.to_socket in {mix_shader.inputs[1], mix_shader.inputs[2]}
            ):
                links.remove(link)
        links.new(uv_map.outputs["UV"], texture.inputs["Vector"])
        links.new(texture.outputs["Color"], group_node.inputs["Mask"])
        links.new(original_socket, mix_shader.inputs[1])
        links.new(group_node.outputs["Shader"], mix_shader.inputs[2])
        links.new(mix_shader.outputs["Shader"], output.inputs["Surface"])
        return material


    def set_preview_image(project: Any, image: Any) -> Any:
        stored_name = str(getattr(project, "preview_material_name", ""))
        material = bpy.data.materials.get(stored_name) if stored_name else None
        texture = material.node_tree.nodes.get("QSDF Mask") if material is not None and material.use_nodes else None
        if texture is None:
            material = ensure_preview_material(project, image)
            texture = material.node_tree.nodes.get("QSDF Mask")
        if texture is not None:
            texture.image = image
        return material


    def _load_restore_entries(obj: Any) -> list[dict[str, Any]]:
        try:
            raw = obj.get(RESTORE_KEY, "[]")
            entries = json.loads(raw) if isinstance(raw, str) else []
            return entries if isinstance(entries, list) else []
        except (TypeError, ValueError):
            return []


    def _save_restore_entries(obj: Any, entries: list[dict[str, Any]]) -> None:
        if entries:
            obj[RESTORE_KEY] = json.dumps(entries, separators=(",", ":"))
        elif RESTORE_KEY in obj:
            del obj[RESTORE_KEY]


    def _disable_project_flags(uuids: set[str]) -> None:
        if not uuids:
            return
        for scene in bpy.data.scenes:
            projects = getattr(scene, "quick_sdf_projects", getattr(scene, "qsdf_projects", ()))
            for candidate in projects:
                if _project_uuid(candidate) in uuids and hasattr(candidate, "preview_enabled"):
                    candidate.preview_enabled = False
                    if hasattr(candidate, "material_override_active"):
                        candidate.material_override_active = False


    def _project_by_uuid(uuid: str) -> Any | None:
        for scene in bpy.data.scenes:
            projects = getattr(scene, "quick_sdf_projects", getattr(scene, "qsdf_projects", ()))
            for candidate in projects:
                if _project_uuid(candidate) == uuid:
                    return candidate
        return None


    def _entry_material(entry: dict[str, Any]) -> Any | None:
        owner = _project_by_uuid(str(entry.get("uuid", "")))
        if owner is not None and bool(getattr(owner, "material_override_active", False)):
            if bool(getattr(owner, "original_material_was_none", False)):
                return None
            pointer = getattr(owner, "original_material", None)
            if pointer is not None:
                return pointer
        material_name = str(entry.get("material", ""))
        return bpy.data.materials.get(material_name) if material_name else None


    def _restore_slot_entries(obj: Any, slot_index: int) -> int:
        """Unwind every Quick SDF preview already targeting one object slot."""
        entries = _load_restore_entries(obj)
        matching = [entry for entry in entries if int(entry.get("slot", -1)) == slot_index]
        keep = [entry for entry in entries if int(entry.get("slot", -1)) != slot_index]
        restored = 0
        for entry in reversed(matching):
            if not 0 <= slot_index < len(obj.material_slots):
                continue
            slot = obj.material_slots[slot_index]
            slot.link = "OBJECT"
            slot.material = _entry_material(entry)
            old_link = str(entry.get("link", "OBJECT"))
            if old_link in {"OBJECT", "DATA"}:
                slot.link = old_link
            restored += 1
        _save_restore_entries(obj, keep)
        _disable_project_flags({str(entry.get("uuid", "")) for entry in matching})
        return restored


    def assign_preview_material(project: Any, image: Any | None = None) -> Any:
        """Assign preview to one slot, recording enough data for safe restoration.

        The slot is switched to object linking before assignment.  The mesh data
        and therefore every other object sharing that mesh remain untouched.
        """
        obj = _target_object(project)
        if obj is None or obj.type != "MESH":
            raise ValueError("Quick SDF target mesh is unavailable")
        slot_index = int(getattr(project, "material_slot_index", 0))
        if not 0 <= slot_index < len(obj.material_slots):
            raise ValueError("Quick SDF material slot is unavailable")
        # A slot has one visible material; allowing preview overrides to stack
        # makes restoration order ambiguous. Resolve any previous owner first.
        _restore_slot_entries(obj, slot_index)
        entries = _load_restore_entries(obj)
        uuid = _project_uuid(project)
        slot = obj.material_slots[slot_index]
        if hasattr(project, "original_material"):
            project.original_material = slot.material
            project.original_material_was_none = slot.material is None
            project.original_slot_link = slot.link
            project.material_override_active = True
        entries.append(
            {
                "uuid": uuid,
                "slot": slot_index,
                "material": slot.material.name if slot.material else "",
                "link": slot.link,
            }
        )
        _save_restore_entries(obj, entries)
        slot.link = "OBJECT"
        material = ensure_preview_material(project, image)
        slot.material = material
        _set_project_string(project, "preview_material_name", material.name)
        if hasattr(project, "preview_enabled"):
            project.preview_enabled = True
        return material


    def restore_preview_materials(project: Any | None = None, obj: Any | None = None) -> int:
        """Restore recorded slots for one project/object, or every object."""
        uuid = _project_uuid(project) if project is not None else None
        objects = (obj,) if obj is not None else tuple(bpy.data.objects)
        restored = 0
        restored_uuids: set[str] = set()
        for candidate in objects:
            entries = _load_restore_entries(candidate)
            if not entries:
                continue
            keep: list[dict[str, Any]] = []
            # Multiple projects may temporarily target one slot. Restoring all
            # must unwind the stack newest-first so the oldest/base material is
            # the final assignment.
            ordered_entries = list(reversed(entries)) if uuid is None else entries
            for entry in ordered_entries:
                if uuid is not None and entry.get("uuid") != uuid:
                    keep.append(entry)
                    continue
                slot_index = int(entry.get("slot", -1))
                if not 0 <= slot_index < len(candidate.material_slots):
                    continue
                slot = candidate.material_slots[slot_index]
                slot.link = "OBJECT"
                slot.material = _entry_material(entry)
                old_link = str(entry.get("link", "OBJECT"))
                if old_link in {"OBJECT", "DATA"}:
                    slot.link = old_link
                restored += 1
                restored_uuids.add(str(entry.get("uuid", "")))
            _save_restore_entries(candidate, keep)
        _disable_project_flags(restored_uuids)
        if project is not None and hasattr(project, "preview_enabled"):
            project.preview_enabled = False
            project.material_override_active = False
        return restored


    def restore_all_preview_materials() -> int:
        return restore_preview_materials()


    @persistent
    def _save_pre(_unused: Any) -> None:
        """Suspend only the temporary material; keep the transient session."""
        global _SAVE_SUSPENDED_PROJECT_UUID
        try:
            from .studio import active_session

            session = active_session()
        except (ImportError, RuntimeError, ReferenceError):
            session = None
        _SAVE_SUSPENDED_PROJECT_UUID = str(getattr(session, "project_uuid", ""))
        restore_all_preview_materials()


    @persistent
    def _save_post(_unused: Any) -> None:
        global _SAVE_SUSPENDED_PROJECT_UUID
        uuid = _SAVE_SUSPENDED_PROJECT_UUID
        _SAVE_SUSPENDED_PROJECT_UUID = ""
        if not uuid:
            return
        try:
            from .studio import active_session, resolve_session_project
            from . import runtime

            session = active_session()
            project = resolve_session_project() if session is not None else None
            if project is not None and str(project.uuid) == uuid:
                angle = runtime.active_angle(project)
                image = runtime.resolve_angle_image(project, angle) if angle is not None else None
                assign_preview_material(project, image)
        except (ImportError, RuntimeError, ReferenceError, ValueError):
            # Saving must never fail merely because the temporary overlay could
            # not be re-applied. Exit/Restore Materials remains available.
            return


    @persistent
    def _load_pre(_unused: Any) -> None:
        global _SAVE_SUSPENDED_PROJECT_UUID
        _SAVE_SUSPENDED_PROJECT_UUID = ""
        restore_all_preview_materials()


    @persistent
    def _load_post(_unused: Any) -> None:
        # Journals from an interrupted previous process are conservative and
        # can be restored after the new Main is fully available.
        restore_all_preview_materials()


    def register_preview() -> None:
        """Install preview resources and non-resident editor overlays."""
        # Blender exposes ``bpy.data`` as ``_RestrictData`` while an add-on is
        # being enabled.  Creating datablocks here therefore fails with
        # ``'_RestrictData' object has no attribute 'node_groups'``.  The node
        # group is intentionally created lazily by ``ensure_preview_material``
        # when the user enables the preview, at which point normal data access
        # is available.
        handler_pairs = (
            (bpy.app.handlers.save_pre, _save_pre),
            (bpy.app.handlers.save_post, _save_post),
            (bpy.app.handlers.load_pre, _load_pre),
            (bpy.app.handlers.load_post, _load_post),
        )
        for handlers, callback in handler_pairs:
            if callback not in handlers:
                handlers.append(callback)
        from . import ui

        ui.register_draw_handlers()


    def unregister_preview() -> None:
        from . import ui

        ui.unregister_draw_handlers()
        restore_all_preview_materials()
        handler_pairs = (
            (bpy.app.handlers.save_pre, _save_pre),
            (bpy.app.handlers.save_post, _save_post),
            (bpy.app.handlers.load_pre, _load_pre),
            (bpy.app.handlers.load_post, _load_post),
        )
        for handlers, callback in handler_pairs:
            while callback in handlers:
                handlers.remove(callback)


    class QSDF_OT_preview_enable(bpy.types.Operator):
        bl_idname = "quicksdf.preview_enable"
        bl_label = "Enable Toon Preview"
        bl_description = "Temporarily show the current threshold mask as two-tone shading"
        bl_options = {"REGISTER"}

        def execute(self, context):
            project = _active_project(context.scene)
            if project is None:
                return {"CANCELLED"}
            angle_item = None
            angles = getattr(project, "angles", None)
            if angles:
                index = max(0, min(int(getattr(project, "active_angle_index", 0)), len(angles) - 1))
                angle_item = angles[index]
            image = getattr(angle_item, "image", None) if angle_item is not None else None
            if image is None and angle_item is not None and getattr(angle_item, "image_name", ""):
                image = bpy.data.images.get(angle_item.image_name)
            try:
                assign_preview_material(project, image)
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if hasattr(project, "preview_enabled"):
                project.preview_enabled = True
            return {"FINISHED"}


    class QSDF_OT_preview_disable(bpy.types.Operator):
        bl_idname = "quicksdf.preview_disable"
        bl_label = "Disable Toon Preview"
        bl_description = "Restore the material used before Quick SDF preview"
        bl_options = {"REGISTER"}

        def execute(self, context):
            project = _active_project(context.scene)
            count = restore_preview_materials(project) if project is not None else 0
            self.report({"INFO"}, f"Restored {count} material slot{'s' if count != 1 else ''}")
            return {"FINISHED"}


    class QSDF_OT_restore_materials(bpy.types.Operator):
        bl_idname = "quicksdf.restore_materials"
        bl_label = "Restore Materials"
        bl_description = "Emergency restore of every material temporarily replaced by Quick SDF"
        bl_options = {"REGISTER"}

        def execute(self, _context):
            count = restore_all_preview_materials()
            self.report({"INFO"}, f"Restored {count} material slot{'s' if count != 1 else ''}")
            return {"FINISHED"}


    CLASSES = (
        QSDF_OT_preview_enable,
        QSDF_OT_preview_disable,
        QSDF_OT_restore_materials,
    )
else:
    def ensure_preview_node_group() -> None:
        return None


    def ensure_preview_material(_project: Any, _image: Any | None = None) -> None:
        return None


    def set_preview_image(_project: Any, _image: Any) -> None:
        return None


    def assign_preview_material(_project: Any, _image: Any | None = None) -> None:
        raise RuntimeError("Blender is required for material preview")


    def restore_preview_materials(_project: Any | None = None, _obj: Any | None = None) -> int:
        return 0


    def restore_all_preview_materials() -> int:
        return 0


    def register_preview() -> None:
        return None


    def unregister_preview() -> None:
        return None


    CLASSES: tuple[type, ...] = ()


__all__ = [
    "CLASSES",
    "NODE_GROUP_NAME",
    "assign_preview_material",
    "ensure_preview_material",
    "ensure_preview_node_group",
    "restore_all_preview_materials",
    "restore_preview_materials",
    "register_preview",
    "set_preview_image",
    "unregister_preview",
]

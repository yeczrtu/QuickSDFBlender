"""Blender 5.1 render regression for the Quick SDF preview material.

The source material intentionally owns two Material Output nodes.  Quick SDF
must attach to the active output, keep the paint Canvas wired to the preview
texture, and make white/black Canvas changes observable by the render engine.

Viewport shading recovery is exercised by ``blender_studio_smoke.py`` because
background Blender has no VIEW_3D. Keeping rendering here makes this gate fast
and independent of editor timing or GPU cursor overlays.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
import numpy as np  # noqa: E402


def _full_uv_plane() -> bpy.types.Object:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2.0)
    obj = bpy.context.object
    obj.name = "Quick SDF Preview Regression Plane"

    uv_layer = obj.data.uv_layers.active or obj.data.uv_layers.new(name="PreviewUV")
    for loop in obj.data.loops:
        co = obj.data.vertices[loop.vertex_index].co
        uv_layer.data[loop.index].uv = (co.x * 0.5 + 0.5, co.y * 0.5 + 0.5)
    obj.data.uv_layers.active = uv_layer

    material = bpy.data.materials.new("Preview Regression Source")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    source = nodes.new("ShaderNodeEmission")
    source.name = "Source Surface"
    source.inputs["Color"].default_value = (0.18, 0.18, 0.18, 1.0)
    inactive = nodes.new("ShaderNodeOutputMaterial")
    inactive.name = "Inactive Material Output"
    inactive["preview_regression_expected_output"] = False
    active = nodes.new("ShaderNodeOutputMaterial")
    active.name = "Active Material Output"
    active["preview_regression_expected_output"] = True
    links.new(source.outputs["Emission"], active.inputs["Surface"])
    active.is_active_output = True
    assert active.is_active_output and not inactive.is_active_output

    obj.data.materials.append(material)
    return obj


def _camera(scene: bpy.types.Scene) -> None:
    camera_data = bpy.data.cameras.new("Preview Regression Camera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 2.4
    camera = bpy.data.objects.new("Preview Regression Camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0.0, 0.0, 3.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    scene.camera = camera


def _render_center(scene: bpy.types.Scene, label: str) -> np.ndarray:
    output = ROOT / "build" / f"preview_render_smoke_{label}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    scene.render.filepath = str(output)
    assert bpy.ops.render.render(write_still=True) == {"FINISHED"}
    assert output.is_file()
    loaded = bpy.data.images.load(str(output), check_existing=False)
    try:
        width, height = loaded.size[:]
        pixels = np.empty(width * height * 4, dtype=np.float32)
        loaded.pixels.foreach_get(pixels)
        rgba = pixels.reshape(height, width, 4)
        center_x = width // 2
        center_y = height // 2
        patch = rgba[
            max(0, center_y - 3) : min(height, center_y + 4),
            max(0, center_x - 3) : min(width, center_x + 4),
            :3,
        ]
        assert patch.size
        return np.median(patch, axis=(0, 1))
    finally:
        bpy.data.images.remove(loaded)
        output.unlink(missing_ok=True)


def _write_canvas(runtime, image: bpy.types.Image, value: float) -> None:
    width, height = image.size[:]
    rgba = np.ones((height, width, 4), dtype=np.float32)
    rgba[..., :3] = value
    runtime.write_image_rgba(image, rgba)
    actual = runtime.image_rgba(image)
    np.testing.assert_array_equal(actual[..., :3], rgba[..., :3])


assert bpy.app.background
assert bpy.ops.preferences.addon_enable(module="quick_sdf_blender") == {"FINISHED"}
from quick_sdf_blender import runtime  # noqa: E402


obj = _full_uv_plane()
scene = bpy.context.scene
_camera(scene)
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 64
scene.render.resolution_y = 64
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
scene.world.color = (0.02, 0.02, 0.02)
scene.quick_sdf_settings.resolution = 512
scene.quick_sdf_settings.initialization = "WHITE"
assert bpy.ops.quicksdf.project_create() == {"FINISHED"}
project = runtime.active_project(scene)
assert project is not None
project.preview_mode = "MASK"
assert bpy.ops.quicksdf.preview_enable() == {"FINISHED"}

canvas = runtime.resolve_angle_image(project, runtime.active_angle(project))
assert canvas is not None
runtime.sync_canvas(bpy.context, project)
assert scene.tool_settings.image_paint.canvas == canvas

preview_material = obj.material_slots[0].material
assert preview_material is not None and preview_material.use_nodes
nodes = preview_material.node_tree.nodes
mask_node = nodes.get("QSDF Mask")
assert mask_node is not None and mask_node.image == canvas

outputs = [
    node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"
]
active_outputs = [node for node in outputs if node.is_active_output]
assert len(active_outputs) == 1
active_output = active_outputs[0]
assert bool(active_output.get("preview_regression_expected_output", False)), [
    (node.name, bool(node.is_active_output), tuple(node.keys())) for node in outputs
]
surface_links = active_output.inputs["Surface"].links
assert len(surface_links) == 1
assert surface_links[0].from_node.name == "QSDF Original Overlay"
assert all(
    not node.inputs["Surface"].is_linked
    for node in outputs
    if node is not active_output
)

_write_canvas(runtime, canvas, 1.0)
runtime.sync_canvas(bpy.context, project)
assert mask_node.image == canvas
white = _render_center(scene, "white")

_write_canvas(runtime, canvas, 0.0)
runtime.sync_canvas(bpy.context, project)
assert mask_node.image == canvas
black = _render_center(scene, "black")

assert float(np.linalg.norm(white - black)) > 0.35, (white, black)
print("PREVIEW_RENDER_WHITE", white)
print("PREVIEW_RENDER_BLACK", black)
print("[Quick SDF preview render smoke] PASS")

assert bpy.ops.quicksdf.preview_disable() == {"FINISHED"}
assert bpy.ops.preferences.addon_disable(module="quick_sdf_blender") == {"FINISHED"}

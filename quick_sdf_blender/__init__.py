# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick SDF Blender extension entry point."""

from __future__ import annotations

bl_info = {
    "name": "Quick SDF Studio",
    "author": "Quick SDF contributors",
    "version": (0, 2, 0),
    "blender": (5, 1, 0),
    "location": "3D View > Sidebar > Quick SDF",
    "description": "Paint production-ready toon face shadows with an artist-first angle timeline",
    "category": "Paint",
}


def _modules():
    from . import boundary, model, operators, preview, runtime, timeline, ui

    return (model, runtime, operators, boundary, preview, timeline, ui)


def register():
    import bpy

    modules = _modules()
    registered = []
    try:
        for module in modules:
            for cls in getattr(module, "CLASSES", ()):  # registration order is public API
                bpy.utils.register_class(cls)
                registered.append(cls)
        from . import i18n, model, operators, preview, runtime, studio, timeline, tools, ui

        operators.register_macros()
        model.register_properties()
        runtime.register_runtime()
        preview.register_preview()
        i18n.register_translations()
        studio.register_studio()
        timeline.register_timeline()
        tools.register_tools()
        ui.register_keymaps()
    except Exception:
        # Registration can fail after RNA properties, handlers, draw callbacks or
        # keymaps have been installed.  Unwind those resources before removing
        # their PropertyGroup classes; otherwise Blender retains dangling RNA
        # references and a second enable attempt fails.
        from . import i18n, model, operators, preview, runtime, studio, timeline, tools, ui

        operators.shutdown_export_job(message="Export cancelled during add-on cleanup")

        for cleanup in (
            ui.unregister_keymaps,
            studio.unregister_studio,
            tools.unregister_tools,
            timeline.unregister_timeline,
            i18n.unregister_translations,
            preview.unregister_preview,
            runtime.unregister_runtime,
            model.unregister_properties,
        ):
            try:
                cleanup()
            except Exception:
                pass
        for cls in reversed(registered):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
        raise


def unregister():
    import bpy

    from . import i18n, live_preview, model, operators, preview, runtime, studio, timeline, tools, ui

    operators.shutdown_export_job(message="Export cancelled because the add-on was disabled")
    ui.unregister_keymaps()
    studio.unregister_studio()
    tools.unregister_tools()
    timeline.unregister_timeline()
    live_preview.cleanup()
    i18n.unregister_translations()
    preview.unregister_preview()
    runtime.unregister_runtime()
    model.unregister_properties()
    for module in reversed(_modules()):
        for cls in reversed(getattr(module, "CLASSES", ())):
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass


if __name__ == "__main__":
    register()

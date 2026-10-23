"""Model3D add-on: generate and rig 3D models with the local Model3D engine.

Blender imports this package to register the add-on; bpy is only imported in register(), so
the standard-library modules (engine_client, library, tracker) also import in plain Python tests.
"""


def register():
    from . import ui

    ui.register()


def unregister():
    from . import ui

    ui.unregister()

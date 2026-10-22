"""Blender glue: preferences, the sidebar panel, operators, and the timer that imports results.

Network and job work runs on a worker thread (jobs.py); the timer runs on Blender's main
thread, reads the shared JobStatus, redraws the panel, and imports finished models.
"""

import threading
from pathlib import Path

import bpy
from bpy.app.translations import pgettext_iface as iface_
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList

from . import jobs, library
from .engine_client import EngineClient, EngineError
from .translations import TRANSLATIONS

ENGINE_URL = "http://127.0.0.1:8765"
TICK_S = 0.5
STATUS = jobs.JobStatus()
STAGE_TEXT = {"shape": "Building the shape", "paint": "Painting the texture", "rig": "Adding skeleton…"}
_health: dict | None = None  # last answer of GET /v1/health, None when unknown
_was_busy = False


def _preferences(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon is not None else None


def library_root(context) -> Path | None:
    preferences = _preferences(context)
    if preferences is None or not preferences.project_dir:
        return None
    return Path(bpy.path.abspath(preferences.project_dir)) / "data" / "library"


def import_glb(path: Path) -> list:
    """Import a GLB into the current scene, select only its objects, and make the armature active."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [obj for obj in bpy.context.view_layer.objects if obj not in before]
    # The importer also adds the bones' display shape (an icosphere); it is not part of the model.
    shapes = {
        bone.custom_shape for obj in new if obj.type == "ARMATURE" for bone in obj.pose.bones if bone.custom_shape
    }
    new = [obj for obj in new if obj not in shapes]
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in new)
    active = next((obj for obj in new if obj.type == "ARMATURE"), new[0] if new else None)
    if active is not None:
        bpy.context.view_layer.objects.active = active
    return new


def _start(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _load_health() -> None:
    global _health
    try:
        _health = EngineClient(ENGINE_URL, timeout=2).health()
    except EngineError:
        _health = None


def reload_entries(context) -> None:
    """Fill the panel's list from the library folder (small local JSON files)."""
    window_manager = context.window_manager
    window_manager.model3d_entries.clear()
    root = library_root(context)
    for entry in library.list_entries(root) if root is not None else []:
        item = window_manager.model3d_entries.add()
        item.entry_id = entry["entry_id"]
        item.label = entry.get("name", entry["entry_id"])
        item.rigged = bool(entry.get("options", {}).get("rig"))
        item.ready = entry.get("outcome") == "succeeded"


def _selected_entry(context):
    window_manager = context.window_manager
    if 0 <= window_manager.model3d_index < len(window_manager.model3d_entries):
        return window_manager.model3d_entries[window_manager.model3d_index]
    return None


class Model3DPreferences(AddonPreferences):
    bl_idname = __package__

    project_dir: StringProperty(
        name="Model3D folder",
        description="The folder that holds data/library (the Model3D repository)",
        subtype="DIR_PATH",
    )

    def draw(self, context):
        self.layout.prop(self, "project_dir")


class Model3DSettings(PropertyGroup):
    image_path: StringProperty(name="Image", subtype="FILE_PATH")
    quality: EnumProperty(
        name="Quality",
        items=(("draft", "Draft", ""), ("final", "Final", ""), ("high", "High detail", "")),
        default="final",
    )
    texture: BoolProperty(name="Texture", default=True)
    rig: BoolProperty(name="With skeleton", default=False)


class Model3DEntry(PropertyGroup):
    entry_id: StringProperty()
    label: StringProperty()
    rigged: BoolProperty()
    ready: BoolProperty()


class MODEL3D_OT_generate(Operator):
    bl_idname = "model3d.generate"
    bl_label = "Generate"
    bl_description = "Generate a 3D model from the image with the local engine"

    @classmethod
    def poll(cls, context):
        return not STATUS.snapshot()["busy"]

    def execute(self, context):
        root = library_root(context)
        if root is None:
            self.report({"ERROR"}, iface_("Set the Model3D folder in the add-on preferences"))
            return {"CANCELLED"}
        settings = context.scene.model3d
        image = Path(bpy.path.abspath(settings.image_path))
        if not settings.image_path or not image.is_file():
            self.report({"ERROR"}, iface_("Choose an image first"))
            return {"CANCELLED"}
        options = {"texture": settings.texture, "quality": settings.quality, "rig": settings.rig}
        STATUS.update(busy=True)  # at once, so a second click cannot start another job
        _start(jobs.run_generation, EngineClient(ENGINE_URL), root, image, options, STATUS)
        return {"FINISHED"}


class MODEL3D_OT_cancel(Operator):
    bl_idname = "model3d.cancel"
    bl_label = "Cancel"
    bl_description = "Cancel the running job"

    @classmethod
    def poll(cls, context):
        return STATUS.snapshot()["busy"]

    def execute(self, context):
        STATUS.update(cancel_requested=True, message="Cancelling…")
        return {"FINISHED"}


class MODEL3D_OT_refresh_library(Operator):
    bl_idname = "model3d.refresh_library"
    bl_label = "Refresh"
    bl_description = "Read the library again"

    def execute(self, context):
        reload_entries(context)
        _start(_load_health)
        return {"FINISHED"}


class MODEL3D_OT_import_entry(Operator):
    bl_idname = "model3d.import_entry"
    bl_label = "Import"
    bl_description = "Import the selected library model into the scene"

    @classmethod
    def poll(cls, context):
        entry = _selected_entry(context)
        return entry is not None and entry.ready

    def execute(self, context):
        path = library.model_path(library_root(context), _selected_entry(context).entry_id)
        if not path.is_file():
            self.report({"ERROR"}, f"{path} is missing")
            return {"CANCELLED"}
        import_glb(path)
        return {"FINISHED"}


class MODEL3D_OT_add_skeleton(Operator):
    bl_idname = "model3d.add_skeleton"
    bl_label = "Add skeleton"
    bl_description = "Add a skeleton to the selected library model"

    @classmethod
    def poll(cls, context):
        entry = _selected_entry(context)
        return not STATUS.snapshot()["busy"] and entry is not None and entry.ready and not entry.rigged

    def execute(self, context):
        root = library_root(context)
        entry_id = _selected_entry(context).entry_id
        source = next((entry for entry in library.list_entries(root) if entry["entry_id"] == entry_id), None)
        if source is None:
            return {"CANCELLED"}
        STATUS.update(busy=True)
        _start(jobs.run_add_skeleton, EngineClient(ENGINE_URL), root, source, STATUS)
        return {"FINISHED"}


class MODEL3D_UL_entries(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0, flt_flag=0):
        text = item.label + (f" · {iface_('skeleton')}" if item.rigged else "")
        layout.label(text=text, icon="ARMATURE_DATA" if item.rigged else "MESH_DATA", translate=False)


class MODEL3D_PT_main(Panel):
    bl_label = "Model3D"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Model3D"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.model3d
        status = STATUS.snapshot()
        if library_root(context) is None:
            layout.label(text="Set the Model3D folder in the add-on preferences", icon="ERROR")
        column = layout.column()
        column.enabled = not status["busy"]
        column.prop(settings, "image_path")
        column.prop(settings, "quality")
        column.prop(settings, "texture")
        row = column.row()
        row.enabled = _health is None or bool(_health.get("rig"))
        row.prop(settings, "rig")
        if not row.enabled and _health.get("rig_reason"):
            column.label(text=_health["rig_reason"], icon="INFO", translate=False)
        if status["busy"]:
            layout.operator("model3d.cancel", icon="CANCEL")
            stage = STAGE_TEXT.get(status["stage"]) if status["stage"] else None
            if stage is not None:
                layout.progress(factor=status["progress"], text=iface_(stage))
            elif status["message"]:
                layout.label(text=status["message"])
        else:
            layout.operator("model3d.generate", icon="MESH_MONKEY")
            if status["error"] is not None:
                layout.label(text=iface_(status["error"][1]), icon="ERROR", translate=False)
            elif status["message"]:
                layout.label(text=status["message"])


class MODEL3D_PT_library(Panel):
    bl_label = "Library"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Model3D"
    bl_parent_id = "MODEL3D_PT_main"

    def draw(self, context):
        layout = self.layout
        window_manager = context.window_manager
        layout.template_list("MODEL3D_UL_entries", "", window_manager, "model3d_entries", window_manager, "model3d_index")
        row = layout.row(align=True)
        row.operator("model3d.refresh_library", icon="FILE_REFRESH")
        row.operator("model3d.import_entry", icon="IMPORT")
        row.operator("model3d.add_skeleton", icon="ARMATURE_DATA")


def _tick():
    """Main-thread timer: import finished models, reload the list when a job ends, redraw."""
    global _was_busy
    path = STATUS.take_import()
    if path is not None and path.is_file():
        import_glb(path)
    busy = STATUS.snapshot()["busy"]
    if _was_busy and not busy:
        reload_entries(bpy.context)
    _was_busy = busy
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return TICK_S


CLASSES = (
    Model3DPreferences,
    Model3DSettings,
    Model3DEntry,
    MODEL3D_OT_generate,
    MODEL3D_OT_cancel,
    MODEL3D_OT_refresh_library,
    MODEL3D_OT_import_entry,
    MODEL3D_OT_add_skeleton,
    MODEL3D_UL_entries,
    MODEL3D_PT_main,
    MODEL3D_PT_library,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.model3d = PointerProperty(type=Model3DSettings)
    bpy.types.WindowManager.model3d_entries = CollectionProperty(type=Model3DEntry)
    bpy.types.WindowManager.model3d_index = IntProperty(default=-1)
    bpy.app.translations.register(__package__, TRANSLATIONS)
    bpy.app.timers.register(_tick, first_interval=TICK_S, persistent=True)
    _start(_load_health)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    bpy.app.translations.unregister(__package__)
    del bpy.types.WindowManager.model3d_index
    del bpy.types.WindowManager.model3d_entries
    del bpy.types.Scene.model3d
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

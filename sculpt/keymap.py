import os
import re
from os.path import dirname, join

import bpy

from ..debug import DEBUG_KEYMAP

brush_key_path = join(dirname(dirname(__file__)), "src", "key", "BBrush.py")

last_key_path = None
extra_keymaps = []


extra_keymap_names = (
    "Sculpt",
    "3D View Tool: Sculpt, Box Mask",
    "3D View Tool: Sculpt, Lasso Mask",
    "3D View Tool: Sculpt, Line Mask",
    "3D View Tool: Sculpt, Polyline Mask",
    "3D View Tool: Sculpt, Box Hide",
    "3D View Tool: Sculpt, Lasso Hide",
    "3D View Tool: Sculpt, Line Hide",
    "3D View Tool: Sculpt, Polyline Hide",
    "3D View Tool: Sculpt, Box Trim",
    "3D View Tool: Sculpt, Lasso Trim",
    "3D View Tool: Sculpt, Line Trim",
    "3D View Tool: Sculpt, Polyline Trim",
    "3D View Tool: Sculpt, Line Project",
)


def _assign_keymap_properties(kmi, properties):
    for name, value in properties:
        setattr(kmi.properties, name, value)


def _is_extra_bbrush_keymap_item(kmi):
    if kmi.idname == "sculpt.bbrush_face_sets_create_zbrush":
        return kmi.type == "W" and kmi.ctrl
    if kmi.idname == "sculpt.bbrush_toggle_polygroup_display":
        return kmi.type == "F" and kmi.shift
    if kmi.idname == "wm.radial_control":
        return kmi.type == "S"
    return False


def register_extra_bbrush_keymaps(context):
    unregister_extra_bbrush_keymaps()
    keyconfig = context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return

    for name in extra_keymap_names:
        space_type = "EMPTY" if name == "Sculpt" else "VIEW_3D"
        km = keyconfig.keymaps.new(name=name, space_type=space_type, region_type="WINDOW")

        kmi = km.keymap_items.new(
            "sculpt.bbrush_face_sets_create_zbrush",
            type="W",
            value="PRESS",
            ctrl=True,
        )
        extra_keymaps.append((km, kmi))

        kmi = km.keymap_items.new(
            "sculpt.bbrush_toggle_polygroup_display",
            type="F",
            value="PRESS",
            shift=True,
        )
        extra_keymaps.append((km, kmi))

        kmi = km.keymap_items.new("wm.radial_control", type="S", value="PRESS")
        _assign_keymap_properties(kmi, (
            ("data_path_primary", "tool_settings.sculpt.brush.strength"),
            ("data_path_secondary", "tool_settings.sculpt.unified_paint_settings.strength"),
            ("use_secondary", "tool_settings.sculpt.unified_paint_settings.use_unified_strength"),
            ("rotation_path", "tool_settings.sculpt.brush.texture_slot.angle"),
            ("color_path", "tool_settings.sculpt.brush.cursor_color_add"),
            ("fill_color_path", ""),
            ("fill_color_override_path", ""),
            ("fill_color_override_test_path", ""),
            ("zoom_path", ""),
            ("image_id", "tool_settings.sculpt.brush"),
            ("secondary_tex", False),
        ))
        extra_keymaps.append((km, kmi))


def unregister_extra_bbrush_keymaps():
    while extra_keymaps:
        km, kmi = extra_keymaps.pop()
        try:
            km.keymap_items.remove(kmi)
        except ReferenceError:
            pass
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    for km in keyconfig.keymaps:
        for kmi in list(km.keymap_items):
            if _is_extra_bbrush_keymap_item(kmi):
                try:
                    km.keymap_items.remove(kmi)
                except ReferenceError:
                    pass


class BrushKeymap:
    preset_subdir = "keyconfig"

    @classmethod
    def get_key_preset_path(cls, name: str) -> "str|None":
        """
        scripts\modules\bpy_types.py
        """
        ext_valid = getattr(cls, "preset_extensions", {".py", ".xml"})

        filter_ext = lambda ext: ext.lower() in ext_valid
        searchpaths = bpy.utils.preset_paths(cls.preset_subdir)

        # collect paths
        files = []
        for directory in searchpaths:
            files.extend([
                (f, os.path.join(directory, f))
                for f in os.listdir(directory)
                if (not f.startswith("."))
                if ((filter_ext is None) or
                    (filter_ext(os.path.splitext(f)[1])))
            ])

        files.sort(
            key=lambda file_path:
            tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", file_path[0].lower())),
        )
        for (n, path) in files:
            if n[:-3] == name:
                return os.path.normpath(path)
        return None

    @classmethod
    def start_key(cls, context):
        """src/key/BBrush.py"""
        global last_key_path

        last_key = context.window_manager.keyconfigs.active.name
        last_key_path = cls.get_key_preset_path(last_key)
        if DEBUG_KEYMAP:
            print("start_key")
            print("last_key_path", last_key_path)
            print("brush_key_path", brush_key_path)
        bpy.ops.preferences.keyconfig_import("EXEC_DEFAULT", filepath=brush_key_path, keep_original=True)
        register_extra_bbrush_keymaps(context)

    @staticmethod
    def restore_key(context):
        global last_key_path

        active_keyconfig = context.preferences.keymap.active_keyconfig
        if DEBUG_KEYMAP:
            print("restore_key", active_keyconfig, last_key_path)
        unregister_extra_bbrush_keymaps()
        if active_keyconfig == "BBrush":
            bpy.ops.wm.keyconfig_preset_remove("EXEC_DEFAULT", name="BBrush", remove_name=True)
        if last_key_path:
            try:
                bpy.ops.preferences.keyconfig_activate("EXEC_DEFAULT", filepath=last_key_path)
                last_key_path = None
                if DEBUG_KEYMAP:
                    print("brush_key_path", brush_key_path)
                    print("active_keyconfig", context.window_manager.keyconfigs.active.name)
            except Exception as e:
                print("Error", e.args)


def try_restore_keymap():
    """在不是Bbrush模式时
    快捷键任未复位
    尝试修复"""
    context = bpy.context
    from ..utils import is_bbruse_mode
    if not is_bbruse_mode():
        unregister_extra_bbrush_keymaps()
        if context.window_manager.keyconfigs.active.name == "BBrush":
            bpy.ops.wm.keyconfig_preset_remove("EXEC_DEFAULT", name="BBrush", remove_name=True)
            print("try_restore_keymap ok")

"""Factory-startup regression tests for the ZBrush-style B brush popup.

This test never saves preferences. Package registration is performed directly,
equivalent to ``default_set=False`` add-on registration.
"""

import importlib.util
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "bbrush_brush_popup_package_test"


def load_package():
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)
    return package


package = load_package()
package.register()
try:
    module = sys.modules[f"{PACKAGE_NAME}.sculpt.brush_popup"]
    keymap = sys.modules[f"{PACKAGE_NAME}.sculpt.addon_keymap"]

    entries = module.available_sculpt_brushes()
    assert len(entries) >= 60, len(entries)
    assert {entry.name for entry in entries} >= {"Draw", "Clay", "Grab", "Smooth"}

    groups = {}
    for entry in entries:
        groups.setdefault(entry.first_key, []).append(entry)
        assert entry.first_key
        assert entry.second_key
    for group in groups.values():
        shortcuts = [entry.second_key for entry in group]
        assert len(shortcuts) == len(set(shortcuts)), shortcuts

    b_items = [
        item
        for item in keymap.runtime_keymaps
        if item.idname == "sculpt.bbrush_brush_popup" and item.type == "B"
    ]
    assert len(b_items) == 1
    assert b_items[0].active is False
    keymap.set_runtime_keymaps_active(True)
    assert b_items[0].active is True
    keymap.set_runtime_keymaps_active(False)
    assert b_items[0].active is False

    mesh = bpy.data.meshes.new("BbrushPopupTestMesh")
    obj = bpy.data.objects.new("BbrushPopupTestObject", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="SCULPT")
    draw = next(entry for entry in entries if entry.name == "Draw")
    assert module.activate_sculpt_brush(bpy.context, draw)
    assert bpy.context.tool_settings.sculpt.brush.name == "Draw"

    print(
        "BBRUSH_BRUSH_POPUP_OK",
        {
            "brushes": len(entries),
            "groups": len(groups),
            "draw_shortcut": f"{draw.first_key}{draw.second_key}",
            "native_asset_shelf": module._SCULPT_ASSET_SHELF,
            "runtime_b_only": True,
        },
    )
finally:
    package.unregister()

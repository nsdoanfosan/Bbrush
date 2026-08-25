"""Factory-startup GUI event test for popup -> first key -> second key.

The B keymap itself is covered by ``run_brush_popup_tests.py``. This opens a
disposable Blender process, never saves preferences, and exits automatically
after simulating mnemonic events in its own 3D View.
"""

import importlib.util
import json
from pathlib import Path
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "bbrush_brush_popup_gui_test"
state = {"stage": "load"}


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


def view_context():
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    return window, area, region


def send_key(key):
    window, area, region = view_context()
    x = area.x + region.x + region.width // 2
    y = area.y + region.y + region.height // 2
    window.event_simulate(type=key, value="PRESS", x=x, y=y)
    window.event_simulate(type=key, value="RELEASE", x=x, y=y)


def finish(result):
    try:
        package = state.get("package")
        sculpt = state.get("sculpt")
        if sculpt is not None:
            sculpt.brush_runtime = None
        if package is not None:
            package.unregister()
    except Exception:
        result["cleanup_error"] = traceback.format_exc()
    print("BBRUSH_BRUSH_POPUP_GUI=" + json.dumps(result, sort_keys=True), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def check_result():
    try:
        popup = state["popup"]
        active = getattr(bpy.context.tool_settings.sculpt, "brush", None)
        result = {
            "active_brush": active.name if active else None,
            "popup_closed": popup.BbrushBrushPopup._active_instance is None,
            "sequence": "B C L",
        }
        result["passed"] = result["active_brush"] == "Clay" and result["popup_closed"]
        return finish(result)
    except Exception:
        return finish({"passed": False, "stage": "check", "error": traceback.format_exc()})


def send_second_key():
    try:
        state["stage"] = "second_key"
        send_key("L")
        bpy.app.timers.register(check_result, first_interval=0.35)
    except Exception:
        return finish({"passed": False, "stage": state["stage"], "error": traceback.format_exc()})
    return None


def send_first_key():
    try:
        state["stage"] = "first_key"
        popup = state["popup"].BbrushBrushPopup._active_instance
        if popup is None:
            return finish({"passed": False, "stage": "popup_open", "error": "Popup did not open"})
        send_key("C")
        bpy.app.timers.register(send_second_key, first_interval=0.25)
    except Exception:
        return finish({"passed": False, "stage": state["stage"], "error": traceback.format_exc()})
    return None


def setup():
    try:
        state["stage"] = "setup"
        package = load_package()
        state["package"] = package
        package.register()

        sculpt = sys.modules[f"{PACKAGE_NAME}.sculpt"]
        popup = sys.modules[f"{PACKAGE_NAME}.sculpt.brush_popup"]
        keymap = sys.modules[f"{PACKAGE_NAME}.sculpt.addon_keymap"]
        shelf = sys.modules[f"{PACKAGE_NAME}.sculpt.update_brush_shelf"]
        state.update({"sculpt": sculpt, "popup": popup, "keymap": keymap})

        mesh = bpy.data.meshes.new("BbrushPopupGuiMesh")
        mesh.from_pydata(
            [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
            [],
            [(0, 1, 2, 3)],
        )
        obj = bpy.data.objects.new("BbrushPopupGuiObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        window, area, region = view_context()
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.object.mode_set(mode="SCULPT")
            sculpt.brush_runtime = sculpt.BrushRuntime()
            shelf.UpdateBrushShelf.start_brush_shelf(bpy.context)
        keymap.set_runtime_keymaps_active(True)

        state["stage"] = "open"
        with bpy.context.temp_override(window=window, area=area, region=region):
            state["invoke_status"] = sorted(
                bpy.ops.sculpt.bbrush_brush_popup("INVOKE_DEFAULT")
            )
        bpy.app.timers.register(send_first_key, first_interval=0.35)
    except Exception:
        return finish({"passed": False, "stage": state["stage"], "error": traceback.format_exc()})
    return None


bpy.app.timers.register(setup, first_interval=0.15)

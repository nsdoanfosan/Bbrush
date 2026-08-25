"""Registration, keymap, and live 3D View tests for Alt+5/6/7 overlays."""

import importlib.util
import json
from pathlib import Path
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[2]


def quit_later():
    bpy.ops.wm.quit_blender()
    return None


if not bpy.app.background:
    bpy.app.timers.register(quit_later, first_interval=20.0)


def load_addon():
    name = "bbrush_edge_overlay_test"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


module = None
original = None
payload = {}
try:
    module = load_addon()
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    expected = {
        "FIVE": "SHARP",
        "SIX": "SEAM",
        "SEVEN": "BEVEL_WEIGHT",
    }
    overlay_items = []
    legacy_alt5 = []
    for keymap in keyconfig.keymaps:
        for item in keymap.keymap_items:
            if (
                item.idname == "view3d.bbrush_toggle_edge_overlay"
                and item.alt
                and item.type in expected
            ):
                overlay_items.append(
                    {
                        "keymap": keymap.name,
                        "type": item.type,
                        "target": item.properties.target,
                        "active": item.active,
                    }
                )
            if (
                item.idname == "sculpt.bbrush_zbrush_tools_popup"
                and item.alt
                and item.type == "FIVE"
            ):
                legacy_alt5.append(keymap.name)

    assert len(overlay_items) == 3
    assert not legacy_alt5
    assert all(
        item["keymap"] == "3D View"
        and item["target"] == expected[item["type"]]
        and item["active"]
        for item in overlay_items
    )

    if bpy.app.background:
        raise RuntimeError("Run this test without --background for 3D View QA")

    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    overlay = area.spaces.active.overlay
    properties = {
        "SHARP": "show_edge_sharp",
        "SEAM": "show_edge_seams",
        "BEVEL_WEIGHT": "show_edge_bevel_weight",
    }
    original = {name: getattr(overlay, name) for name in properties.values()}
    for name in properties.values():
        setattr(overlay, name, False)

    operator_results = {}
    with bpy.context.temp_override(window=window, area=area, region=region):
        for target, property_name in properties.items():
            before = {name: getattr(overlay, name) for name in properties.values()}
            on_result = bpy.ops.view3d.bbrush_toggle_edge_overlay(target=target)
            after_on = {name: getattr(overlay, name) for name in properties.values()}
            off_result = bpy.ops.view3d.bbrush_toggle_edge_overlay(target=target)
            after_off = {name: getattr(overlay, name) for name in properties.values()}
            operator_results[target] = {
                "on": sorted(on_result),
                "off": sorted(off_result),
                "target_enabled": after_on[property_name],
                "others_unchanged": all(
                    after_on[name] == before[name]
                    for name in properties.values()
                    if name != property_name
                ),
                "restored": after_off == before,
            }

    passed = all(
        item["on"] == ["FINISHED"]
        and item["off"] == ["FINISHED"]
        and item["target_enabled"]
        and item["others_unchanged"]
        and item["restored"]
        for item in operator_results.values()
    )
    payload = {
        "blender": bpy.app.version_string,
        "passed": passed,
        "keymaps": sorted(overlay_items, key=lambda item: item["type"]),
        "legacy_alt5_bindings": legacy_alt5,
        "operators": operator_results,
    }
    print("EDGE_OVERLAY_TEST_JSON=" + json.dumps(payload, sort_keys=True))
    if not passed:
        raise AssertionError("Viewport edge overlay tests failed")
except Exception:
    traceback.print_exc()
    if payload:
        print("EDGE_OVERLAY_TEST_JSON=" + json.dumps(payload, sort_keys=True))
    raise
finally:
    if original is not None:
        for property_name, value in original.items():
            setattr(overlay, property_name, value)
    if module is not None:
        try:
            module.unregister()
        except Exception:
            traceback.print_exc()
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()

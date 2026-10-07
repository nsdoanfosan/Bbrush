"""Disposable factory GUI regression: file loads, auto mode and sculpt shortcuts.

Run with --factory-startup --python; registers default_set=False, never saves
preferences, and saves only a synthetic mesh into an automatically removed temp dir.
"""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import traceback

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
package = addon_utils.enable(ROOT.name, default_set=False, persistent=True)
assert package is not None
# default_set=False doesn't insert an Addon preference entry; add it only to
# this disposable factory process so the real auto-mode preference can be read.
entry = bpy.context.preferences.addons.new()
entry.module = ROOT.name
pref = entry.preferences
pref.always_use_bbrush_sculpt_mode = True
assert not pref.show_shortcut_keys
assert pref.depth_display_mode == "NOT_DISPLAY"
assert pref.view_navigation_gizmo_display_mode == "NOT_DISPLAY"

temp = tempfile.TemporaryDirectory(prefix="bbrush52-qa-")
blend = str(Path(temp.name) / "synthetic.blend")
reg = package.register_module
phase = 0
checks = []
expected_exit_view = None


def viewport():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    return bpy.context.temp_override(window=window, area=area, region=region)


def keymap_check(active):
    items = package.sculpt.addon_keymap.runtime_keymaps
    expected = {
        ("sculpt.bbrush_toggle_face_sets", "F", True, False),
        ("sculpt.bbrush_face_set_from_mask", "W", False, True),
        ("sculpt.bbrush_brush_popup", "B", False, False),
        ("sculpt.bbrush_alt4_popup", "FOUR", False, False),
    }
    actual = {(i.idname, i.type, bool(i.shift), bool(i.ctrl)) for i in items}
    assert expected <= actual, (expected - actual)
    assert all(i.active == active for i in items)


def tick():
    global phase, expected_exit_view
    try:
        with viewport():
            if phase == 0:
                bpy.ops.object.select_all(action="SELECT")
                bpy.ops.object.delete()
                mesh = bpy.data.meshes.new("Synthetic")
                mesh.from_pydata(
                    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                     (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)],
                    [], [(0, 1, 2, 3), (4, 5, 6, 7)])
                obj = bpy.data.objects.new("Synthetic", mesh)
                bpy.context.collection.objects.link(obj)
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                sets = mesh.attributes.new(".sculpt_face_set", "INT", "FACE")
                sets.data.foreach_set("value", [1, 1])
                mask = mesh.attributes.new(".sculpt_mask", "FLOAT", "POINT")
                mask.data.foreach_set("value", [1, 1, 1, 1, 0, 0, 0, 0])
                bpy.ops.wm.save_as_mainfile(filepath=blend)
                bpy.ops.object.mode_set(mode="SCULPT")
            elif phase == 1:
                assert reg.is_bbrush_mode(), "Initial Sculpt entry did not auto-start"
                keymap_check(True)
                checks.append("sculpt_entry_auto")
                overlay = bpy.context.space_data.overlay
                overlay.show_sculpt_face_sets = False
                overlay.show_overlays = False
                overlay.sculpt_mode_face_sets_opacity = 0
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {"FINISHED"}
                assert overlay.show_sculpt_face_sets and overlay.show_overlays
                assert overlay.sculpt_mode_face_sets_opacity == 1
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {"FINISHED"}
                assert not overlay.show_sculpt_face_sets
                checks.append("shift_f_toggle_master_and_opacity")
                runtime = package.sculpt.runtime_shortcuts
                space = bpy.context.space_data
                mesh = bpy.context.object.data
                ids_before = [d.value for d in mesh.attributes['.sculpt_face_set'].data]
                for shading_type in ('SOLID', 'WIREFRAME', 'MATERIAL', 'RENDERED'):
                    for light in ('STUDIO', 'MATCAP', 'FLAT'):
                        space.shading.type = shading_type
                        space.shading.light = light
                        space.shading.color_type = 'OBJECT'
                        space.shading.show_xray = True
                        overlay.show_overlays = False
                        overlay.show_sculpt_face_sets = True
                        overlay.sculpt_mode_face_sets_opacity = 0.15
                        original = runtime._face_set_view_values(space)
                        assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                        assert space.shading.type == 'SOLID'
                        assert space.shading.color_type == 'SINGLE'
                        assert not space.shading.show_xray
                        assert overlay.show_overlays and overlay.show_sculpt_face_sets
                        assert overlay.sculpt_mode_face_sets_opacity == 1
                        assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                        restored = runtime._face_set_view_values(space)
                        original['overlay']['show_sculpt_face_sets'] = False
                        assert restored == original, (restored, original)
                assert ids_before == [d.value for d in mesh.attributes['.sculpt_face_set'].data]
                checks.append('face_set_view_12_shading_states_restore_no_mesh_edits')
                space.shading.type = 'SOLID'
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                # A user disabling overlays while groups are active must be able
                # to repair visibility with one keypress, rather than hide them.
                overlay.show_overlays = False
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                assert overlay.show_overlays and overlay.show_sculpt_face_sets
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                checks.append('face_set_view_repair_intervening_visibility_change')
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                space.shading.single_color = (0.4, 0.5, 0.6)
                user_color = tuple(space.shading.single_color)
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                assert tuple(space.shading.single_color) == user_color
                checks.append('face_set_view_preserves_user_color_edit')
                assert bpy.ops.sculpt.bbrush_face_set_from_mask() == {"FINISHED"}
                mesh = bpy.context.object.data
                values = [d.value for d in mesh.attributes[".sculpt_face_set"].data]
                assert values[0] != 1 and values[1] == 1, values
                mask = mesh.attributes.get(".sculpt_mask")
                assert mask is None or all(d.value == 0 for d in mask.data)
                checks.append("ctrl_w_only_masked_region_clear_mask")
                expected_exit_view = runtime._face_set_view_values(space)
                assert bpy.ops.sculpt.bbrush_toggle_face_sets() == {'FINISHED'}
                bpy.ops.object.mode_set(mode="OBJECT")
            elif phase == 2:
                assert not reg.is_bbrush_mode(), "Sculpt exit did not disable runtime"
                assert not package.sculpt.runtime_shortcuts._face_set_views
                assert package.sculpt.runtime_shortcuts._face_set_view_values(
                    bpy.context.space_data) == expected_exit_view
                checks.append('face_set_view_restored_on_sculpt_exit')
                keymap_check(False)
                checks.append("object_exit_restores_shortcuts")
                bpy.ops.wm.open_mainfile(filepath=blend)
            elif phase == 3:
                assert reg._mode_subscribed
                assert bpy.app.timers.is_registered(reg._watch_sculpt_mode)
                bpy.ops.object.mode_set(mode="SCULPT")
            elif phase == 4:
                assert reg.is_bbrush_mode(), "Auto entry broke after file load"
                keymap_check(True)
                checks.append("sculpt_entry_after_file_load")
                # Also test opening a file that is already in Sculpt Mode.
                bpy.ops.wm.save_as_mainfile(filepath=blend)
                bpy.ops.object.mode_set(mode="OBJECT")
            elif phase == 5:
                assert not reg.is_bbrush_mode()
                bpy.ops.wm.open_mainfile(filepath=blend)
            elif phase == 6:
                assert bpy.context.mode == "SCULPT"
                assert reg.is_bbrush_mode(), "Loading a Sculpt file did not auto-start"
                checks.append("load_already_sculpt_auto")
                print("BBRUSH52_RECOVERY_OK", json.dumps(checks), flush=True)
                bpy.ops.object.mode_set(mode="OBJECT")
                addon_utils.disable(ROOT.name, default_set=False)
                temp.cleanup()
                bpy.ops.wm.quit_blender()
                return None
        phase += 1
        return 1.2
    except Exception:
        traceback.print_exc()
        print("BBRUSH52_RECOVERY_FAILED", phase, checks, flush=True)
        temp.cleanup()
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1.2, persistent=True)

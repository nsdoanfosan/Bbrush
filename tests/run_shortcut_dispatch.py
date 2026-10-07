"""Actual synthetic keyboard dispatch in a disposable factory GUI.

Run with --factory-startup --enable-event-simulate --python. No preferences or
production data are saved; Blender events target this isolated test window only.
"""
import addon_utils
import bpy
import sys
import traceback
from pathlib import Path

bpy.context.preferences.view.show_splash = False
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
keyconfig = Path(bpy.utils.user_resource('SCRIPTS')) / 'presets/keyconfig/keymap_park_modify_20260212.blend.py'
if keyconfig.exists():
    bpy.utils.keyconfig_set(str(keyconfig))
package = addon_utils.enable(ROOT.name, default_set=False)
entry = bpy.context.preferences.addons.new()
entry.module = ROOT.name
entry.preferences.always_use_bbrush_sculpt_mode = True
phase = 0


def tick():
    global phase
    try:
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            if phase == 0:
                window.event_simulate(type='ESC', value='PRESS', x=region.x+region.width//2, y=region.y+region.height//2)
                window.event_simulate(type='ESC', value='RELEASE', x=region.x+region.width//2, y=region.y+region.height//2)
                bpy.ops.object.select_all(action='SELECT')
                bpy.ops.object.delete()
                mesh = bpy.data.meshes.new('SyntheticDispatch')
                mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0),
                                  (2,0,0),(3,0,0),(3,1,0),(2,1,0)],
                                 [], [(0,1,2,3),(4,5,6,7)])
                obj = bpy.data.objects.new('SyntheticDispatch', mesh)
                bpy.context.collection.objects.link(obj)
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                mesh.attributes.new('.sculpt_face_set', 'INT', 'FACE').data.foreach_set('value', [1,1])
                mesh.attributes.new('.sculpt_mask', 'FLOAT', 'POINT').data.foreach_set('value', [1,1,1,1,0,0,0,0])
                bpy.ops.object.mode_set(mode='SCULPT')
            elif phase == 1:
                assert package.register_module.is_bbrush_mode()
                bpy.context.window_manager.keyconfigs.update()
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                area.spaces.active.overlay.show_sculpt_face_sets = False
                window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=region.x+region.width//2, y=region.y+region.height//2)
                window.event_simulate(type='F', value='PRESS', shift=True, x=region.x+region.width//2, y=region.y+region.height//2)
                window.event_simulate(type='F', value='RELEASE', shift=True, x=region.x+region.width//2, y=region.y+region.height//2)
            elif phase == 2:
                assert area.spaces.active.overlay.show_sculpt_face_sets, 'Shift+F was intercepted by another key binding'
                window.event_simulate(type='W', value='PRESS', ctrl=True, x=region.x+region.width//2, y=region.y+region.height//2)
                window.event_simulate(type='W', value='RELEASE', ctrl=True, x=region.x+region.width//2, y=region.y+region.height//2)
            elif phase == 3:
                values = [d.value for d in bpy.context.object.data.attributes['.sculpt_face_set'].data]
                assert values[0] != 1 and values[1] == 1, ('Ctrl+W dispatch failed', values)
                print('BBRUSH_SHORTCUT_DISPATCH_OK Shift+F Ctrl+W', flush=True)
                addon_utils.disable(ROOT.name, default_set=False)
                bpy.ops.wm.quit_blender()
                return None
        phase += 1
        return 1.2
    except Exception:
        traceback.print_exc()
        print('BBRUSH_SHORTCUT_DISPATCH_FAILED', phase, flush=True)
        print('recent_ops', [o.bl_idname for o in bpy.context.window_manager.operators], flush=True)
        print('modal_ops', [o.bl_idname for o in bpy.context.window.modal_operators], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1.2)

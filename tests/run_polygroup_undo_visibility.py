"""Actual hotkey Undo/Redo regression in a disposable factory GUI.

No preferences or production files are saved. Run with --enable-event-simulate.
"""
import sys
import traceback
from pathlib import Path

import addon_utils
import bpy
from mathutils import Quaternion, Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
bpy.context.preferences.view.show_splash = False
keyconfig = Path(bpy.utils.user_resource('SCRIPTS')) / 'presets/keyconfig/keymap_park_modify_20260212.blend.py'
if keyconfig.exists():
    bpy.utils.keyconfig_set(str(keyconfig))
package = addon_utils.enable(ROOT.name, default_set=False)
entry = bpy.context.preferences.addons.new()
entry.module = ROOT.name
entry.preferences.always_use_bbrush_sculpt_mode = True
phase = 0
checks = []
before = after = visibility_before = grouped_before = None


def state():
    mesh = bpy.data.objects['PolygroupQA'].data
    return {name: tuple(d.value for d in mesh.attributes[name].data)
            if mesh.attributes.get(name) else None
            for name in ('.sculpt_face_set', '.sculpt_mask', '.hide_poly')}


def key(window, region, kind, **mods):
    xy = dict(x=region.x + region.width//2, y=region.y + region.height//2)
    window.event_simulate(type=kind, value='PRESS', **mods, **xy)
    window.event_simulate(type=kind, value='RELEASE', **mods, **xy)


def visible():
    s = state()
    return {g for i, g in enumerate(s['.sculpt_face_set'])
            if not s['.hide_poly'] or not s['.hide_poly'][i]}


def click(window, region, group=None, drag=False):
    if group is None:
        x, y = region.x+region.width//2, region.y+region.height*3//4
    else:
        rv = next(a for a in window.screen.areas if a.type == 'VIEW_3D').spaces.active.region_3d
        p = location_3d_to_region_2d(region, rv,
                                    Vector((group*2-2, 0, 0)))
        assert p is not None
        x, y = region.x+int(p.x), region.y+int(p.y)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window.event_simulate(type='LEFTMOUSE', value='PRESS', ctrl=True, shift=True, x=x, y=y)
    if drag:
        x += 25
        def move():
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', ctrl=True, shift=True, x=x, y=y)
        def release():
            window.event_simulate(type='LEFTMOUSE', value='RELEASE', ctrl=True, shift=True, x=x, y=y)
        bpy.app.timers.register(move,first_interval=.15)
        bpy.app.timers.register(release,first_interval=.3)
    else:
        window.event_simulate(type='LEFTMOUSE', value='RELEASE', ctrl=True, shift=True, x=x, y=y)


def tick():
    global phase, before, after, visibility_before, grouped_before
    try:
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            if phase == 0:
                key(window, region, 'ESC')
                bpy.ops.object.select_all(action='SELECT')
                bpy.ops.object.delete()
                mesh = bpy.data.meshes.new('PolygroupQA')
                verts = [(x+dx, dy, 0) for x in (0,2,4)
                         for dx,dy in ((-.7,-.7),(.7,-.7),(.7,.7),(-.7,.7))]
                mesh.from_pydata(verts, [], [tuple(range(i,i+4)) for i in (0,4,8)])
                obj = bpy.data.objects.new('PolygroupQA',mesh)
                bpy.context.collection.objects.link(obj)
                obj.select_set(True)
                bpy.context.view_layer.objects.active=obj
                mesh.attributes.new('.sculpt_face_set','INT','FACE').data.foreach_set('value',[1,1,1])
                mesh.attributes.new('.sculpt_mask','FLOAT','POINT').data.foreach_set('value',[1]*4+[0]*8)
                bpy.ops.object.mode_set(mode='SCULPT')
                rv = bpy.context.region_data
                rv.view_rotation=Quaternion((1,0,0,0))
                rv.view_perspective='ORTHO'
                rv.view_location=(2,0,0)
                rv.view_distance=8
            elif phase == 1:
                bpy.context.window_manager.keyconfigs.update()
                bpy.ops.ed.undo_push(message='QA masked baseline')
                before=state()
                key(window,region,'W',ctrl=True)
            elif phase == 2:
                after=state()
                assert after['.sculpt_face_set'][0] != 1 and after['.sculpt_face_set'][1:]==(1,1),after
                assert not after['.sculpt_mask'] or not any(after['.sculpt_mask']),after
                key(window,region,'Z',ctrl=True)
            elif phase == 3:
                assert state()==before,('undo',state(),before)
                checks.append('Ctrl+W exact one-step Undo restores groups and mask')
                key(window,region,'Z',ctrl=True,shift=True)
            elif phase == 4:
                assert state()==after,('redo',state(),after)
                key(window,region,'Z',ctrl=True)
            elif phase == 5:
                assert state()==before,('second undo',state(),before)
                checks.append('Undo/Redo/Undo stable')
                # Start a fresh three-group fixture, leaving the earlier history intact.
                bpy.ops.object.mode_set(mode='OBJECT')
                mesh=bpy.context.object.data
                mesh.attributes['.sculpt_face_set'].data.foreach_set('value',[1,2,3])
                mesh.attributes['.sculpt_mask'].data.foreach_set('value',[0]*12)
                bpy.ops.object.mode_set(mode='SCULPT')
                bpy.ops.ed.undo_push(message='QA three groups')
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
                click(window,region,1)
            elif phase == 6:
                assert visible()=={1},('isolate',visible(),state())
                click(window,region,1)
            elif phase == 7:
                assert visible()=={2,3},('single invert',visible(),state())
                click(window,region,2)
            elif phase == 8:
                assert visible()=={3},('multiple hide',visible(),state())
                key(window,region,'Z',ctrl=True)
            elif phase == 9:
                assert visible()=={2,3},('visibility undo',visible())
                key(window,region,'Z',ctrl=True,shift=True)
            elif phase == 10:
                assert visible()=={3},('visibility redo',visible())
                checks.append('isolate/single invert/multiple hide plus Undo/Redo')
                click(window,region,None)
            elif phase == 11:
                assert visible()=={1,2,3},('blank show all',visible())
                click(window,region,1)
            elif phase == 12:
                assert visible()=={1}
                visibility_before=state()
                click(window,region,None,drag=True)
            elif phase == 13:
                assert visible()=={2,3},('blank drag invert',visible())
                checks.append('blank click show all/blank drag invert')
                key(window,region,'Z',ctrl=True)
            elif phase == 14:
                assert state()==visibility_before,('invert undo',state(),visibility_before)
                key(window,region,'Z',ctrl=True,shift=True)
            elif phase == 15:
                assert visible()=={2,3}
                # Group Visible without a mask must keep hidden group 1.
                grouped_before=state()
                key(window,region,'W',ctrl=True)
            elif phase == 16:
                s=state()
                assert s['.sculpt_face_set'][0]==1 and s['.sculpt_face_set'][1]==s['.sculpt_face_set'][2]!=1,s
                assert s['.hide_poly']==grouped_before['.hide_poly']
                key(window,region,'Z',ctrl=True)
            elif phase == 17:
                assert state()==grouped_before,('group visible undo',state(),grouped_before)
                checks.append('Group Visible preserves hidden groups and Undo')
                click(window,region,None)
            elif phase == 18:
                # Two Ctrl+W transactions must remain independently undoable.
                bpy.ops.paint.mask_flood_fill('EXEC_DEFAULT', True, mode='VALUE',value=1)
                before=state()
                key(window,region,'W',ctrl=True)
            elif phase == 19:
                after=state()
                assert after!=before
                key(window,region,'W',ctrl=True)
            elif phase == 20:
                assert state()!=after
                key(window,region,'Z',ctrl=True)
            elif phase == 21:
                assert state()==after,('repeated Ctrl+W undo1',state(),after)
                key(window,region,'Z',ctrl=True)
            elif phase == 22:
                assert state()==before,('repeated Ctrl+W undo2',state(),before)
                key(window,region,'Z',ctrl=True,shift=True)
            elif phase == 23:
                assert state()==after
                checks.append('two consecutive Ctrl+W steps Undo/Redo independently')
                # Undo the group, then the native mask action before it.
                key(window,region,'Z',ctrl=True)
            elif phase == 24:
                assert state()==before
                key(window,region,'Z',ctrl=True)
            elif phase == 25:
                assert state()['.sculpt_face_set']==before['.sculpt_face_set']
                assert not any(state()['.sculpt_mask'])
                key(window,region,'Z',ctrl=True,shift=True)
            elif phase == 26:
                assert state()==before,('native mask redo',state(),before)
                key(window,region,'Z',ctrl=True,shift=True)
            elif phase == 27:
                assert state()==after,('group after native redo',state(),after)
                checks.append('native mask action across grouping boundary Undo/Redo')
                bpy.ops.object.mode_set(mode='OBJECT')
                bpy.ops.object.select_all(action='SELECT')
                bpy.ops.object.delete()
                bpy.ops.mesh.primitive_uv_sphere_add(segments=12,ring_count=8)
                bpy.context.object.name='PolygroupQA'
                bpy.ops.object.mode_set(mode='SCULPT')
                bpy.ops.sculpt.dynamic_topology_toggle()
                assert bpy.context.object.use_dynamic_topology_sculpting
            elif phase == 28:
                key(window,region,'W',ctrl=True)
            elif phase == 29:
                assert not bpy.context.object.use_dynamic_topology_sculpting
                after=state()
                assert after['.sculpt_face_set'] and len(set(after['.sculpt_face_set']))==1
                key(window,region,'Z',ctrl=True)
            elif phase == 30:
                assert not bpy.context.object.use_dynamic_topology_sculpting
                assert state()!=after
                key(window,region,'Z',ctrl=True,shift=True)
            elif phase == 31:
                assert state()==after
                checks.append('Dyntopo flush/group Undo/Redo without crash')
                print('BBRUSH_POLYGROUP_OK',checks,flush=True)
                bpy.ops.wm.quit_blender()
                return None
        phase+=1
        return .7
    except Exception:
        traceback.print_exc()
        print('BBRUSH_POLYGROUP_FAILED',phase,checks,flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick,first_interval=1.0)

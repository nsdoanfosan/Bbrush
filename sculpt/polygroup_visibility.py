"""ZBrush Ctrl+Shift clicks, with native Sculpt visibility and Undo."""
import bpy
from bpy_extras import view3d_utils
from mathutils.bvhtree import BVHTree

from .face_sets import BbrushFaceSetFromMask


def _visible_surface_hit(context, coordinate):
    """Raycast only visible evaluated faces: hidden foreground must not block hits."""
    obj = context.active_object
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        hidden = mesh.attributes.get('.hide_poly')
        faces = [tuple(p.vertices) for p in mesh.polygons
                 if hidden is None or not hidden.data[p.index].value]
        if not faces:
            return False
        tree = BVHTree.FromPolygons([v.co for v in mesh.vertices], faces)
        rv = context.region_data
        origin = view3d_utils.region_2d_to_origin_3d(context.region, rv, coordinate)
        direction = view3d_utils.region_2d_to_vector_3d(context.region, rv, coordinate)
        inverse = obj.matrix_world.inverted_safe()
        hit = tree.ray_cast(inverse @ origin, (inverse.to_3x3() @ direction).normalized())
        return hit[0] is not None
    finally:
        evaluated.to_mesh_clear()


def _visibility_state(mesh):
    hidden = mesh.attributes.get('.hide_poly')
    sets = mesh.attributes.get('.sculpt_face_set')
    partially_hidden = False
    visible_groups = set()
    for p in mesh.polygons:
        if hidden is not None and hidden.data[p.index].value:
            partially_hidden = True
        else:
            visible_groups.add(abs(sets.data[p.index].value) if sets else 1)
    return partially_hidden, visible_groups


class BbrushPolygroupVisibility(bpy.types.Operator):
    bl_idname = 'sculpt.bbrush_polygroup_visibility'
    bl_label = 'Polygroup Visibility'
    bl_description = 'Isolate a group; click the sole visible group to invert, or hide a group among several'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return (BbrushFaceSetFromMask.poll(context)
                and context.area is not None and context.area.type == 'VIEW_3D'
                and context.region_data is not None
                and not context.active_object.use_dynamic_topology_sculpting)

    def invoke(self, context, event):
        self.start = (event.mouse_region_x, event.mouse_region_y)
        self.on_surface = _visible_surface_hit(context, self.start)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            return {'CANCELLED'}
        dx = event.mouse_region_x-self.start[0]
        dy = event.mouse_region_y-self.start[1]
        dragged = dx*dx+dy*dy > context.preferences.inputs.drag_threshold_mouse**2
        if event.type == 'MOUSEMOVE' and dragged:
            # Let the existing selection brush decide whether the completed
            # shape crosses the mesh. Empty-space-only drags invert; a drag
            # from the background onto the mesh must retain marquee selection.
            from .update_brush_shelf import UpdateBrushShelf
            UpdateBrushShelf.update_brush_shelf(context, event)
            status = bpy.ops.sculpt.bbrush_shape('INVOKE_DEFAULT', start_coordinate=self.start)
            if 'RUNNING_MODAL' in status:
                return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if not self.on_surface:
                if dragged:
                    bpy.ops.paint.visibility_invert('EXEC_DEFAULT', True)
                else:
                    bpy.ops.paint.hide_show_all('EXEC_DEFAULT', True, action='SHOW')
            else:
                partial, visible_groups = _visibility_state(context.active_object.data)
                if partial and len(visible_groups) == 1:
                    bpy.ops.paint.visibility_invert('EXEC_DEFAULT', True)
                else:
                    # Native TOGGLE isolates when fully visible; HIDE_ACTIVE
                    # removes only the clicked group in a partial selection.
                    bpy.ops.sculpt.face_set_change_visibility(
                        'INVOKE_DEFAULT', True, mode='HIDE_ACTIVE' if partial else 'TOGGLE')
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

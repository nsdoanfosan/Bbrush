"""Ctrl+W grouping as a single Blender mesh Undo transaction."""
from array import array
import hashlib
import uuid

import bpy

from ..utils import is_bbrush_mode
from .zbrush_tools import _object_mode

MASK_ATTRIBUTE = ".sculpt_mask"
FACE_SET_ATTRIBUTE = ".sculpt_face_set"
MASK_EPSILON = 1.0e-6
STEP_KEY = '_bbrush_face_set_step'


def _history_signature(context):
    """Compare actual data across the native Sculpt/Memfile boundary."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return None
    mesh = obj.data
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str((obj.name, mesh.name, obj.mode, obj.use_dynamic_topology_sculpting, tuple(obj.matrix_world))).encode())
    for collection, field, typecode, size in (
        (mesh.vertices, 'co', 'f', 3), (mesh.loops, 'vertex_index', 'i', 1),
    ):
        values = array(typecode, [0])*len(collection)*size
        collection.foreach_get(field, values)
        digest.update(values.tobytes())
    # Include UVs, color paint, edge flags and custom attributes too. A real
    # change to any of those must never be mistaken for an empty checkpoint.
    for attribute in sorted(mesh.attributes, key=lambda a: a.name):
        digest.update(repr((attribute.name,attribute.domain,attribute.data_type,len(attribute.data))).encode())
        if not attribute.data:
            continue
        prop = next(p for p in attribute.data[0].bl_rna.properties if p.identifier != 'rna_type')
        field = prop.identifier
        size = getattr(prop, 'array_length', 0) or 1
        typecode = {'FLOAT':'f','INT':'i','BOOLEAN':'b'}.get(prop.type)
        if typecode:
            values = array(typecode,[0])*len(attribute.data)*size
            attribute.data.foreach_get(field,values)
            digest.update(values.tobytes())
        else:
            digest.update(repr([getattr(d,field) for d in attribute.data]).encode())
    digest.update(repr([m.name if m else None for m in mesh.materials]).encode())
    digest.update(repr(dict(obj.items())).encode())
    digest.update(repr(dict(mesh.items())).encode())
    # Alt+4 brush/settings changes are also meaningful Undo actions.
    sculpt = context.scene.tool_settings.sculpt
    brush = getattr(sculpt, 'brush', None)
    unified = getattr(sculpt, 'unified_paint_settings', None) or getattr(context.scene.tool_settings, 'unified_paint_settings', None)
    for owner in (sculpt, brush, unified):
        if owner is None:
            continue
        for prop in owner.bl_rna.properties:
            if prop.identifier != 'rna_type' and prop.type in {'BOOLEAN','INT','FLOAT','ENUM','STRING'}:
                value = getattr(owner, prop.identifier)
                digest.update(repr((prop.identifier, tuple(value) if getattr(prop, 'is_array', False) else value)).encode())
    digest.update(repr({k: context.scene[k] for k in context.scene.keys() if k != STEP_KEY}).encode())
    return digest.digest()


class BbrushHistoryStep(bpy.types.Operator):
    bl_idname = 'sculpt.bbrush_history_step'
    bl_label = 'Bbrush Undo / Redo'
    bl_description = 'Undo or redo one action across native Sculpt and mesh transaction boundaries'
    redo: bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return BbrushFaceSetFromMask.poll(context)

    def execute(self, context):
        marker = context.scene.get(STEP_KEY, '')
        before = _history_signature(context) if marker.endswith(':before') else None
        operation = bpy.ops.ed.redo if self.redo else bpy.ops.ed.undo
        status = operation('EXEC_DEFAULT')
        # A memfile checkpoint is required after native Sculpt operations. It
        # carries exactly the preceding action's state and is not another user
        # action. Only cross it when Blender restored identical actual data.
        if marker.endswith(':before'):
            for _ in range(4):
                if (status != {'FINISHED'} or _history_signature(context) != before
                        or not operation.poll()):
                    break
                status = operation('EXEC_DEFAULT')
        return status


def _attribute_values(attribute, typecode):
    values = array(typecode, [0]) * len(attribute.data)
    attribute.data.foreach_get("value", values)
    return values


def _face_set_mode(mesh):
    attribute = mesh.attributes.get(MASK_ATTRIBUTE)
    if attribute is not None and max(_attribute_values(attribute, "f"), default=0) > MASK_EPSILON:
        return "MASKED"
    return "ALL"


def register():
    pass


def unregister():
    pass


class BbrushFaceSetFromMask(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_face_set_from_mask"
    bl_label = "Face Set from Mask"
    bl_description = "Group masked visible faces and clear the mask, or group visible faces when unmasked"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "sculpt_object", None) or context.active_object
        if obj is None or obj.type != "MESH" or obj.mode != "SCULPT":
            cls.poll_message_set("Available for a mesh in Sculpt Mode")
            return False
        if not is_bbrush_mode():
            cls.poll_message_set("Bbrush mode is not active")
            return False
        return True

    def execute(self, context):
        obj = context.active_object
        if any(m.type == 'MULTIRES' and m.sculpt_levels > 0 for m in obj.modifiers):
            self.report({'WARNING'}, 'Set Multires Sculpt Levels to 0 before grouping the base mesh')
            return {'CANCELLED'}
        if obj.use_dynamic_topology_sculpting:
            # Flush live BMesh before any Face Set transaction. Keep the Dyntopo
            # toggle as a separate native undo boundary (never undo mixed data).
            status = bpy.ops.sculpt.dynamic_topology_toggle()
            if status != {'FINISHED'} or obj.use_dynamic_topology_sculpting:
                return {'CANCELLED'}
            bpy.ops.ed.undo_push(message="Before Bbrush Face Set (Dyntopo disabled)")
            self.report({"INFO"}, "Dyntopo disabled for Face Set creation")
        # Commit preceding native Sculpt nodes before the Python mesh edit.
        # Otherwise an Undo may decode only their visibility/mask nodes and
        # leave the newly assigned Face Sets in the live mesh.
        with _object_mode(obj):
            mesh = obj.data
            mask = mesh.attributes.get(MASK_ATTRIBUTE)
            masks = _attribute_values(mask, 'f') if mask else None
            hidden = mesh.attributes.get('.hide_poly')
            masked = masks is not None and max(masks, default=0) > MASK_EPSILON
            if not any((hidden is None or not hidden.data[p.index].value)
                       and (not masked or any(masks[v] > .5 for v in p.vertices))
                       for p in mesh.polygons):
                self.report({'WARNING'}, 'No visible faces covered by the mask')
                return {'CANCELLED'}
        token = uuid.uuid4().hex
        context.scene[STEP_KEY] = token + ':before'
        bpy.ops.ed.undo_push(message="Before Bbrush Face Set")
        status = bpy.ops.sculpt.bbrush_face_set_from_mask_apply("EXEC_DEFAULT")
        if status == {'FINISHED'}:
            context.scene[STEP_KEY] = token + ':after'
        return status


class BbrushFaceSetFromMaskApply(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_face_set_from_mask_apply"
    bl_label = "Apply Face Set from Mask"
    bl_description = "Internal Face Set creation step"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return BbrushFaceSetFromMask.poll(context) and not context.active_object.use_dynamic_topology_sculpting

    def execute(self, context):
        obj = context.active_object
        # Native Sculpt Face Set undo does not track Python mask-layer removal.
        # Flush Sculpt and edit both layers in Object Mode inside the public
        # UNDO operator. Blender owns all before/after data, without undo_post.
        with _object_mode(obj):
            mesh = obj.data
            mask = mesh.attributes.get(MASK_ATTRIBUTE)
            masks = _attribute_values(mask, "f") if mask else None
            masked = masks is not None and max(masks, default=0) > MASK_EPSILON
            hidden = mesh.attributes.get(".hide_poly")
            selected = [
                (hidden is None or not hidden.data[p.index].value)
                and (not masked or any(masks[v] > .5 for v in p.vertices))
                for p in mesh.polygons
            ]
            if not any(selected):
                self.report({"WARNING"}, "No visible faces covered by the mask")
                return {"CANCELLED"}
            sets = mesh.attributes.get(FACE_SET_ATTRIBUTE)
            values = _attribute_values(sets, "i") if sets else array("i", [1])*len(mesh.polygons)
            new_group = max((abs(v) for v in values), default=0)+1
            for i, choose in enumerate(selected):
                if choose:
                    values[i] = new_group
            if sets is None:
                sets = mesh.attributes.new(FACE_SET_ATTRIBUTE, "INT", "FACE")
            sets.data.foreach_set("value", values)
            if masked:
                # Keep the layer so native Sculpt and Redo never hold a dangling
                # attribute reference; zero values consume the mask normally.
                mesh.attributes[MASK_ATTRIBUTE].data.foreach_set("value", array("f", [0])*len(mesh.vertices))
            mesh.update()
        return {"FINISHED"}

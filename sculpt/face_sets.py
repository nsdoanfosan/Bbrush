from array import array
import hashlib

import bpy
from bpy.app.handlers import persistent

from ..utils import is_bbrush_mode


MASK_ATTRIBUTE = ".sculpt_mask"
FACE_SET_ATTRIBUTE = ".sculpt_face_set"
MASK_EPSILON = 1.0e-6


# A masked Ctrl+W consumes the mask. Blender's native undo can restore that
# transient selection mask before restoring the Face Set, so keep the compact
# pre-operation Face Set state until the next undo completes.
_pending_face_set_undo = None


def _mask_max_value(mesh):
    """Return the strongest sculpt-mask value, or None when no mask exists."""
    attribute = mesh.attributes.get(MASK_ATTRIBUTE)
    if attribute is None or attribute.domain != "POINT" or attribute.data_type != "FLOAT":
        return None

    values = array("f", [0.0]) * len(attribute.data)
    if values:
        attribute.data.foreach_get("value", values)
    return max(values, default=0.0)


def _face_set_mode(mesh):
    """Choose the native creation mode for the current sculpt-mask state."""
    max_mask = _mask_max_value(mesh)
    return "MASKED" if max_mask is not None and max_mask > MASK_EPSILON else "ALL"


def _attribute_values(attribute, typecode):
    values = array(typecode, [0]) * len(attribute.data)
    if values:
        attribute.data.foreach_get("value", values)
    return values


def _face_set_snapshot(mesh):
    """Capture Face Set IDs compactly; None means the attribute did not exist."""
    attribute = mesh.attributes.get(FACE_SET_ATTRIBUTE)
    if attribute is None:
        return None
    if attribute.domain != "FACE" or attribute.data_type != "INT":
        return None
    return _attribute_values(attribute, "i")


def _mask_fingerprint(mesh):
    """Identify the exact selection mask that a masked Ctrl+W consumed."""
    attribute = mesh.attributes.get(MASK_ATTRIBUTE)
    if attribute is None or attribute.domain != "POINT" or attribute.data_type != "FLOAT":
        return None
    values = _attribute_values(attribute, "f")
    digest = hashlib.blake2b(values.tobytes(), digest_size=16).digest()
    return len(values), digest


def _restore_face_set_snapshot(mesh, values):
    attribute = mesh.attributes.get(FACE_SET_ATTRIBUTE)

    if values is None:
        if attribute is not None:
            mesh.attributes.remove(attribute)
        return True

    if len(values) != len(mesh.polygons):
        return False

    if attribute is not None and (
        attribute.domain != "FACE" or attribute.data_type != "INT"
    ):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(
            name=FACE_SET_ATTRIBUTE,
            type="INT",
            domain="FACE",
        )
    if len(attribute.data) != len(values):
        return False

    if values:
        attribute.data.foreach_set("value", values)
    return True


def _remember_face_set_undo(mesh, face_sets, mask_fingerprint):
    global _pending_face_set_undo
    _pending_face_set_undo = {
        "mesh_name": mesh.name,
        "face_sets": face_sets,
        "mask_fingerprint": mask_fingerprint,
    }


def _forget_face_set_undo():
    global _pending_face_set_undo
    _pending_face_set_undo = None


def _clear_mask_in_current_undo_step(mesh):
    """Remove the consumed mask without creating a second native undo entry."""
    attribute = mesh.attributes.get(MASK_ATTRIBUTE)
    if attribute is None:
        return
    mesh.attributes.remove(attribute)
    mesh.update()


def _set_dyntopo_enabled(obj, enabled):
    """Toggle Dyntopo only when needed and verify the resulting state."""
    current = bool(getattr(obj, "use_dynamic_topology_sculpting", False))
    if current == enabled:
        return True

    try:
        result = bpy.ops.sculpt.dynamic_topology_toggle()
    except RuntimeError:
        return False
    return (
        "FINISHED" in result
        and bool(getattr(obj, "use_dynamic_topology_sculpting", False)) == enabled
    )


@persistent
def _bbrush_face_set_undo_post(*_args):
    """Make one undo restore pre-Ctrl+W Face Sets without reviving the mask."""
    global _pending_face_set_undo

    pending = _pending_face_set_undo
    _pending_face_set_undo = None
    if pending is None:
        return

    mesh = bpy.data.meshes.get(pending["mesh_name"])
    if mesh is None:
        return

    # Only correct the undo that restored the exact mask consumed by Ctrl+W.
    # This prevents a later, unrelated undo from changing Face Sets.
    if _mask_fingerprint(mesh) != pending["mask_fingerprint"]:
        return

    if not _restore_face_set_snapshot(mesh, pending["face_sets"]):
        return

    _clear_mask_in_current_undo_step(mesh)
    mesh.update()


def register():
    if _bbrush_face_set_undo_post not in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.append(_bbrush_face_set_undo_post)


def unregister():
    _forget_face_set_undo()
    if _bbrush_face_set_undo_post in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.remove(_bbrush_face_set_undo_post)


class BbrushFaceSetFromMask(bpy.types.Operator):
    """Prepare Dyntopo safely, then run the undoable Face Set operation."""

    bl_idname = "sculpt.bbrush_face_set_from_mask"
    bl_label = "Face Set from Mask"
    bl_description = (
        "Create a face set from the sculpt mask and clear it, or use the whole mesh when unmasked"
    )
    bl_options = {"REGISTER"}

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
        obj = getattr(context, "sculpt_object", None) or context.active_object
        disabled_dyntopo = bool(
            getattr(obj, "use_dynamic_topology_sculpting", False)
        )
        if disabled_dyntopo and not _set_dyntopo_enabled(obj, False):
            self.report({"ERROR"}, "Could not disable Dyntopo for Face Set creation")
            return {"CANCELLED"}

        if disabled_dyntopo:
            try:
                bpy.ops.ed.undo_push(message="Before Bbrush Face Set")
            except RuntimeError as exc:
                _set_dyntopo_enabled(obj, True)
                self.report({"ERROR"}, f"Could not prepare Face Set undo: {exc}")
                return {"CANCELLED"}

        # The undoable operator starts only after Dyntopo has flushed its live
        # BMesh. This keeps Dyntopo state out of the Face Set undo transaction;
        # Blender 5.1 can crash when both are stored in the same undo step.
        result = bpy.ops.sculpt.bbrush_face_set_from_mask_apply("EXEC_DEFAULT")
        if "FINISHED" not in result:
            if disabled_dyntopo:
                _set_dyntopo_enabled(obj, True)
            return result

        if disabled_dyntopo:
            try:
                bpy.ops.ed.undo_push(message="Bbrush Face Set")
            except RuntimeError as exc:
                self.report({"WARNING"}, f"Face Set undo was not recorded: {exc}")
            self.report(
                {"INFO"},
                "Face Set created; Dyntopo was disabled to keep Undo stable",
            )
        return result


class BbrushFaceSetFromMaskApply(bpy.types.Operator):
    """Create a Face Set on a regular Sculpt mesh as one undo step."""

    bl_idname = "sculpt.bbrush_face_set_from_mask_apply"
    bl_label = "Apply Face Set from Mask"
    bl_description = "Internal undoable Face Set creation step"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        if not BbrushFaceSetFromMask.poll(context):
            return False
        obj = getattr(context, "sculpt_object", None) or context.active_object
        if getattr(obj, "use_dynamic_topology_sculpting", False):
            cls.poll_message_set("Dyntopo must be disabled before applying Face Sets")
            return False
        return True

    def execute(self, context):
        obj = getattr(context, "sculpt_object", None) or context.active_object
        _forget_face_set_undo()
        mode = _face_set_mode(obj.data)
        previous_face_sets = _face_set_snapshot(obj.data) if mode == "MASKED" else None
        consumed_mask = _mask_fingerprint(obj.data) if mode == "MASKED" else None

        result = {"CANCELLED"}
        native_error = None
        try:
            result = bpy.ops.sculpt.face_sets_create("EXEC_DEFAULT", mode=mode)
            if "FINISHED" in result and mode == "MASKED":
                _clear_mask_in_current_undo_step(obj.data)
                _remember_face_set_undo(
                    obj.data, previous_face_sets, consumed_mask
                )
        except RuntimeError as exc:
            native_error = exc
        if native_error is not None:
            self.report(
                {"ERROR"},
                f"Could not create face set from mask: {native_error}",
            )
            return {"CANCELLED"}

        if "FINISHED" not in result:
            self.report({"WARNING"}, "Face set creation was cancelled")
            return {"CANCELLED"}

        source = "sculpt mask" if mode == "MASKED" else "whole mesh"
        suffix = " and cleared mask" if mode == "MASKED" else ""
        self.report({"INFO"}, f"Created face set from {source}{suffix}")
        return {"FINISHED"}

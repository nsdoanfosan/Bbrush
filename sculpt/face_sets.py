from array import array

import bpy

from ..utils import is_bbrush_mode


MASK_ATTRIBUTE = ".sculpt_mask"
MASK_EPSILON = 1.0e-6


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


class BbrushFaceSetFromMask(bpy.types.Operator):
    """Create a face set from the mask, then clear the consumed mask."""

    bl_idname = "sculpt.bbrush_face_set_from_mask"
    bl_label = "Face Set from Mask"
    bl_description = (
        "Create a face set from the sculpt mask and clear it, or use the whole mesh when unmasked"
    )
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
        obj = getattr(context, "sculpt_object", None) or context.active_object
        mode = _face_set_mode(obj.data)

        try:
            result = bpy.ops.sculpt.face_sets_create("EXEC_DEFAULT", mode=mode)
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not create face set from mask: {exc}")
            return {"CANCELLED"}

        if "FINISHED" not in result:
            self.report({"WARNING"}, "Face set creation was cancelled")
            return {"CANCELLED"}

        if mode == "MASKED":
            try:
                clear_result = bpy.ops.paint.mask_flood_fill(
                    "EXEC_DEFAULT", mode="VALUE", value=0.0
                )
            except RuntimeError as exc:
                self.report({"ERROR"}, f"Face set created, but mask clear failed: {exc}")
                return {"CANCELLED"}

            if "FINISHED" not in clear_result:
                self.report({"WARNING"}, "Face set created, but mask clear was cancelled")
                return {"CANCELLED"}

        source = "sculpt mask" if mode == "MASKED" else "whole mesh"
        suffix = " and cleared mask" if mode == "MASKED" else ""
        self.report({"INFO"}, f"Created face set from {source}{suffix}")
        return {"FINISHED"}

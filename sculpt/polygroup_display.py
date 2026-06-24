import bpy

def _get_sculpt_mask_attribute(mesh):
    return mesh.attributes.get(".sculpt_mask") or mesh.attributes.get("sculpt_mask")


def _attribute_has_nonzero_mask_values(attr, count):
    data_type = getattr(attr, "data_type", "FLOAT")
    if data_type == "BOOLEAN":
        try:
            import numpy as np

            buf = np.zeros(count, dtype=np.bool_)
            attr.data.foreach_get("value", buf)
            return bool(buf.any())
        except Exception:
            for i in range(count):
                try:
                    if bool(attr.data[i].value):
                        return True
                except (AttributeError, TypeError, ValueError, IndexError):
                    continue
            return False

    try:
        import numpy as np

        buf = np.zeros(count, dtype=np.float32)
        attr.data.foreach_get("value", buf)
        return float(np.max(np.abs(buf))) > 1e-6
    except Exception:
        pass

    for i in range(count):
        try:
            if abs(float(attr.data[i].value)) > 1e-6:
                return True
        except (AttributeError, TypeError, ValueError, IndexError):
            continue
    return False


def sculpt_mesh_has_nonzero_mask(context):
    obj = context.sculpt_object
    if not obj or obj.type != "MESH":
        return False
    mesh = obj.data
    try:
        context.view_layer.update()
    except Exception:
        pass

    attr = _get_sculpt_mask_attribute(mesh)
    if attr is None:
        return False
    try:
        count = len(attr.data)
    except Exception:
        count = len(mesh.vertices)
    return _attribute_has_nonzero_mask_values(attr, count)


def sculpt_mesh_has_hidden_geometry(context):
    obj = context.sculpt_object
    if not obj or obj.type != "MESH":
        return False
    mesh = obj.data
    try:
        context.view_layer.update()
    except Exception:
        pass
    return any(vertex.hide for vertex in mesh.vertices)


def _get_sculpt_face_set_attribute(mesh):
    return mesh.attributes.get(".sculpt_face_set") or mesh.attributes.get("sculpt_face_set")


def _ensure_visible_face_set_created(context, result):
    obj = context.sculpt_object
    if not obj or obj.type != "MESH":
        return result
    if _get_sculpt_face_set_attribute(obj.data) is not None:
        return result
    try:
        return bpy.ops.sculpt.face_sets_create("EXEC_DEFAULT", True, mode="ALL")
    except RuntimeError:
        return result


def _view3d_overlays(context):
    screen = getattr(context, "screen", None)
    if screen is None:
        return []
    spaces = []
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type == "VIEW_3D":
                spaces.append(space)
    return [space.overlay for space in spaces]


class TogglePolygroupDisplay(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_toggle_polygroup_display"
    bl_label = "Toggle Face Sets Display"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.mode == "SCULPT"

    def execute(self, context):
        overlays = _view3d_overlays(context)
        if not overlays:
            return {"CANCELLED"}
        state = not overlays[0].show_sculpt_face_sets
        for overlay in overlays:
            overlay.show_sculpt_face_sets = state
            overlay.sculpt_mode_face_sets_opacity = 1.0
        return {"FINISHED"}


class MaskToFaceSetZBrush(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_face_sets_create_zbrush"
    bl_label = "Face Set from Mask or Visible"
    bl_description = "ZBrush Ctrl+W: make a Face Set from mask, or from visible geometry if no mask exists"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "SCULPT"

    def execute(self, context):
        try:
            if sculpt_mesh_has_nonzero_mask(context):
                result = bpy.ops.sculpt.face_sets_create("EXEC_DEFAULT", True, mode="MASKED")
                if result != {"FINISHED"}:
                    return result
                result = bpy.ops.paint.mask_flood_fill(
                    "EXEC_DEFAULT", True, mode="VALUE", value=0.0
                )
            else:
                result = bpy.ops.sculpt.face_sets_create("EXEC_DEFAULT", True, mode="VISIBLE")
                result = _ensure_visible_face_set_created(context, result)
        except RuntimeError:
            return {"CANCELLED"}

        for overlay in _view3d_overlays(context):
            overlay.show_sculpt_face_sets = True
            overlay.sculpt_mode_face_sets_opacity = 1.0
        return result


def _face_set_change_visibility_invoke(context, mode):
    try:
        if context.area and context.region:
            with context.temp_override(
                window=context.window,
                area=context.area,
                region=context.region,
                scene=context.scene,
            ):
                return bpy.ops.sculpt.face_set_change_visibility(
                    "INVOKE_DEFAULT", True, mode=mode
                )
        return bpy.ops.sculpt.face_set_change_visibility("INVOKE_DEFAULT", True, mode=mode)
    except RuntimeError:
        return {"CANCELLED"}


def sculpt_face_set_ctrl_shift_click_invoke(context):
    if sculpt_mesh_has_hidden_geometry(context):
        return _face_set_change_visibility_invoke(context, "HIDE_ACTIVE")
    return _face_set_change_visibility_invoke(context, "TOGGLE")

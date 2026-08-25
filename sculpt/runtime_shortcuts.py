import bpy

from ..utils import is_bbrush_mode


def _poll_bbrush_sculpt(cls, context):
    obj = getattr(context, "sculpt_object", None) or context.active_object
    if obj is None or obj.type != "MESH" or obj.mode != "SCULPT":
        cls.poll_message_set("Available for a mesh in Sculpt Mode")
        return False
    if not is_bbrush_mode():
        cls.poll_message_set("Bbrush mode is not active")
        return False
    return True


def _view3d_space(context):
    space = getattr(context, "space_data", None)
    if space is not None and getattr(space, "type", None) == "VIEW_3D":
        return space

    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type == "VIEW_3D":
            return area.spaces.active
    return None


def _active_sculpt_tool(context):
    workspace = getattr(context, "workspace", None)
    if workspace is None:
        return None
    return workspace.tools.from_space_view3d_mode("SCULPT", create=False)


def _tag_view3d(context):
    area = getattr(context, "area", None)
    if area is not None and area.type == "VIEW_3D":
        area.tag_redraw()


class BbrushActivateTransformGizmo(bpy.types.Operator):
    """Activate Blender's mask-aware Sculpt Transform gizmo."""

    bl_idname = "sculpt.bbrush_activate_transform_gizmo"
    bl_label = "Bbrush Transform Gizmo"
    bl_description = (
        "Show the Sculpt Transform gizmo; transforms affect only unmasked geometry"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        from . import brush_runtime

        space = _view3d_space(context)
        if space is None:
            self.report({"WARNING"}, "No 3D View found")
            return {"CANCELLED"}

        try:
            result = bpy.ops.wm.tool_set_by_id(
                "EXEC_DEFAULT", name="builtin.transform", space_type="VIEW_3D"
            )
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not activate Transform gizmo: {exc}")
            return {"CANCELLED"}

        if "FINISHED" not in result:
            self.report({"WARNING"}, "Transform gizmo activation was cancelled")
            return {"CANCELLED"}

        # ZBrush-style Gizmo 3D behavior: transform the complete unmasked region.
        context.scene.tool_settings.sculpt.transform_mode = "ALL_VERTICES"
        space.show_gizmo = True
        if hasattr(space, "show_gizmo_tool"):
            space.show_gizmo_tool = True

        # Initialize once per sculpt object. T only hides the gizmo, so W can
        # show it again without discarding a pivot placed deliberately by Alt-click.
        object_pointer = context.active_object.as_pointer()
        pivot_object = getattr(brush_runtime, "transform_pivot_object", None)
        if pivot_object != object_pointer:
            bpy.ops.sculpt.set_pivot_position("EXEC_DEFAULT", mode="UNMASKED")
            brush_runtime.transform_pivot_object = object_pointer

        _tag_view3d(context)
        self.report({"INFO"}, "Bbrush Transform gizmo active")
        return {"FINISHED"}


class BbrushDeactivateTransformGizmo(bpy.types.Operator):
    """Hide the Bbrush Transform gizmo and return to the Sculpt brush."""

    bl_idname = "sculpt.bbrush_deactivate_transform_gizmo"
    bl_label = "Hide Bbrush Transform Gizmo"
    bl_description = "Hide the Transform gizmo and return to the Sculpt brush"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        try:
            result = bpy.ops.wm.tool_set_by_id(
                "EXEC_DEFAULT", name="builtin.brush", space_type="VIEW_3D"
            )
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not hide Transform gizmo: {exc}")
            return {"CANCELLED"}

        if "FINISHED" not in result:
            self.report({"WARNING"}, "Transform gizmo deactivation was cancelled")
            return {"CANCELLED"}

        _tag_view3d(context)
        self.report({"INFO"}, "Bbrush Transform gizmo hidden")
        return {"FINISHED"}


class BbrushSetTransformPivotSurface(bpy.types.Operator):
    """Place the Sculpt Transform pivot on the Alt-clicked surface."""

    bl_idname = "sculpt.bbrush_set_transform_pivot_surface"
    bl_label = "Set Transform Pivot on Surface"
    bl_description = "Place the active Bbrush Transform gizmo on the clicked surface"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if not _poll_bbrush_sculpt(cls, context):
            return False
        tool = _active_sculpt_tool(context)
        if tool is None or tool.idname != "builtin.transform":
            cls.poll_message_set("Activate the Bbrush Transform gizmo with W first")
            return False
        return True

    def invoke(self, context, event):
        try:
            result = bpy.ops.sculpt.set_pivot_position(
                "EXEC_DEFAULT",
                mode="SURFACE",
                mouse_x=event.mouse_region_x,
                mouse_y=event.mouse_region_y,
            )
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not place Transform pivot: {exc}")
            return {"CANCELLED"}

        if "FINISHED" not in result:
            self.report({"WARNING"}, "Alt-click did not hit the sculpt surface")
            return {"CANCELLED"}

        _tag_view3d(context)
        self.report({"INFO"}, "Transform pivot placed on surface")
        return {"FINISHED"}


class BbrushToggleFaceSets(bpy.types.Operator):
    """Toggle the colored Face Sets (ZBrush Polygroup) overlay."""

    bl_idname = "sculpt.bbrush_toggle_face_sets"
    bl_label = "Toggle Face Sets"
    bl_description = "Show or hide Face Set colors (ZBrush Polygroups)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        space = _view3d_space(context)
        if space is None:
            self.report({"WARNING"}, "No 3D View found")
            return {"CANCELLED"}

        overlay = space.overlay
        overlay.show_sculpt_face_sets = not overlay.show_sculpt_face_sets

        _tag_view3d(context)

        state = "shown" if overlay.show_sculpt_face_sets else "hidden"
        self.report({"INFO"}, f"Face Set colors {state}")
        return {"FINISHED"}

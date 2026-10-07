import bpy
from bpy.app.handlers import persistent

from ..utils import is_bbrush_mode


# View-only state. Never change Face Set IDs or Mesh's unexposed default-color
# field just to display the default (white) group.
_face_set_views = {}
_FACE_SET_BASE_COLOR = (0.50, 0.72, 0.95)


def _face_set_view_values(space):
    return {
        "shading": {name: (tuple(getattr(space.shading, name)) if name == "single_color"
                           else getattr(space.shading, name))
                    for name in ("type", "light", "color_type", "single_color", "show_xray")},
        "overlay": {name: getattr(space.overlay, name)
                    for name in ("show_overlays", "show_sculpt_face_sets",
                                 "sculpt_mode_face_sets_opacity")},
    }


def _apply_face_set_view(space):
    # Native sculpt colors multiply the surface color. A neutral, lightly tinted
    # base keeps every group readable, including Blender's white default group.
    space.shading.type = "SOLID"
    space.shading.light = "STUDIO"
    space.shading.color_type = "SINGLE"
    space.shading.single_color = _FACE_SET_BASE_COLOR
    space.shading.show_xray = False
    space.overlay.show_overlays = True
    space.overlay.show_sculpt_face_sets = True
    space.overlay.sculpt_mode_face_sets_opacity = 1.0


def _face_set_view_is_readable(space):
    return (space.shading.type == "SOLID" and space.shading.light == "STUDIO"
            and space.shading.color_type == "SINGLE"
            and max(space.shading.single_color) > 0.1
            and not space.shading.show_xray and space.overlay.show_overlays
            and space.overlay.show_sculpt_face_sets
            and space.overlay.sculpt_mode_face_sets_opacity > 0.1)


def _restore_face_set_view(state, *, hide=False):
    space, original, applied = state
    try:
        current = _face_set_view_values(space)
        for group, values in original.items():
            for name, value in values.items():
                # Preserve unrelated edits made by the user while viewing groups.
                if current[group][name] == applied[group][name]:
                    setattr(getattr(space, group), name, value)
        if hide:
            space.overlay.show_sculpt_face_sets = False
    except ReferenceError:
        pass  # The user closed this editor or loaded another file.


@persistent
def reset_face_set_views(_unused=None):
    for state in _face_set_views.values():
        _restore_face_set_view(state)
    _face_set_views.clear()


def register_face_set_views():
    if reset_face_set_views not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(reset_face_set_views)


def unregister_face_set_views():
    reset_face_set_views()
    if reset_face_set_views in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(reset_face_set_views)


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


EDGE_OVERLAY_TARGETS = {
    "SHARP": ("show_edge_sharp", "Sharp edges"),
    "SEAM": ("show_edge_seams", "Seams"),
    "BEVEL_WEIGHT": ("show_edge_bevel_weight", "Bevel weights"),
}


class BbrushToggleEdgeOverlay(bpy.types.Operator):
    """Toggle one mesh edge-mark overlay in the current 3D View."""

    bl_idname = "view3d.bbrush_toggle_edge_overlay"
    bl_label = "Toggle Edge Overlay"
    bl_description = "Show or hide a mesh edge-mark overlay in this 3D View"
    bl_options = {"REGISTER"}

    target: bpy.props.EnumProperty(
        items=[
            (identifier, label, f"Toggle viewport display of {label.lower()}")
            for identifier, (_property, label) in EDGE_OVERLAY_TARGETS.items()
        ]
    )

    @classmethod
    def poll(cls, context):
        if _view3d_space(context) is None:
            cls.poll_message_set("Available in a 3D View")
            return False
        return True

    def execute(self, context):
        space = _view3d_space(context)
        if space is None:
            self.report({"WARNING"}, "No 3D View found")
            return {"CANCELLED"}

        property_name, label = EDGE_OVERLAY_TARGETS[self.target]
        overlay = space.overlay
        visible = not getattr(overlay, property_name)
        setattr(overlay, property_name, visible)
        _tag_view3d(context)

        state = "shown" if visible else "hidden"
        self.report({"INFO"}, f"{label} {state}")
        return {"FINISHED"}


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

        key = space.as_pointer()
        previous = _face_set_views.get(key)
        # The first press always enters a readable color view, even if Blender's
        # native checkbox was already on. Subsequent presses restore the view.
        if previous is not None and _face_set_view_is_readable(space):
            _restore_face_set_view(previous, hide=True)
            del _face_set_views[key]
            visible = False
        else:
            original = previous[1] if previous else _face_set_view_values(space)
            _apply_face_set_view(space)
            _face_set_views[key] = (space, original, _face_set_view_values(space))
            visible = True

        _tag_view3d(context)

        state = "shown" if visible else "hidden"
        self.report({"INFO"}, f"Face Set colors {state}")
        return {"FINISHED"}

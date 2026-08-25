import bpy

from ...utils import is_bbrush_mode


DEFERRED_FEATURES = {
    "VDM_FROM_MESH": (
        "From Mesh",
        "Blender has no sculpt-brush VDM capture equivalent; the port needs a custom vector-displacement asset pipeline",
    ),
    "VDM_TO_MESH": (
        "To Mesh",
        "Blender cannot reconstruct a sculptable plane from a brush VDM natively; this remains a custom asset operator",
    ),
    "SPOTLIGHT": (
        "Spotlight Projection",
        "Reference images exist in Blender, but projecting one through the sculpt brush requires a custom screen-projection sampler",
    ),
    "USE_GLOBAL": (
        "Use Global",
        "Blender Dyntopo settings are global; per-brush Sculptris settings need a brush-state layer",
    ),
    "UNDIVIDE_RATIO": (
        "UnDivide Ratio",
        "Blender exposes a combined refine method and one detail threshold, not ZBrush's independent decimation threshold",
    ),
    "BACK_MASK_INT": (
        "BackMaskInt",
        "Front Faces Only is live; partial back-face influence requires a custom normal-weighted automask",
    ),
    "TOPOLOGY_RANGE": (
        "Topological Range",
        "Blender Topology Auto-Masking is live, but it does not expose ZBrush's geodesic range control",
    ),
    "TOPOLOGY_SMOOTH": (
        "Topological Smooth",
        "Blender Topology Auto-Masking does not expose a boundary smoothing distance",
    ),
    "CLIP_RADIUS": (
        "BRadius",
        "Blender Trim changes topology; a compatible Clip tool must instead push existing vertices in view space",
    ),
    "CLIP_POLYGROUP": (
        "PolyGroup",
        "This will assign a Face Set to geometry moved by the future topology-preserving Clip operator",
    ),
    "LAZY_SNAP": (
        "LazySnap",
        "Blender does not reconnect a new stroke to the previous endpoint; the modal stroke bridge is pending",
    ),
    "CURVE_SMOOTHNESS": (
        "Curve Smoothness",
        "Paint Curve control points are not writable through Blender's public RNA; smoothing needs custom curve state",
    ),
    "CURVE_SNAP": (
        "Curve Snap",
        "Blender Paint Curves are screen-space and have no editable snap-to-surface mode",
    ),
    "CURVE_SMOOTH": (
        "Smooth Curve",
        "Blender exposes Paint Curve draw/edit operators but no whole-curve smoothing operator",
    ),
    "FRAME_MESH": (
        "Frame Mesh",
        "Reusable sculpt curves from borders, Face Sets, and crease edges need a projected-curve builder",
    ),
    "FRAME_BORDER": (
        "Border",
        "Open-border detection will map to Blender boundary edges when Frame Mesh is implemented",
    ),
    "FRAME_POLYGROUPS": (
        "Polygroups",
        "ZBrush Polygroups map to Blender Face Set boundaries for Frame Mesh",
    ),
    "FRAME_CREASED": (
        "Creased edges",
        "Creased edges will use Blender's edge crease attribute in the Frame Mesh builder",
    ),
    "CURVE_INTENSITY": (
        "Curve Intensity",
        "Blender has no native brush-strength falloff over Paint Curve progress",
    ),
    "CURVE_SIZE": (
        "Curve Size",
        "Blender has no native brush-radius falloff over Paint Curve progress",
    ),
}


def poll_bbrush_sculpt(cls, context):
    obj = getattr(context, "sculpt_object", None) or context.active_object
    if obj is None or obj.type != "MESH" or obj.mode != "SCULPT":
        cls.poll_message_set("Available for a mesh in Sculpt Mode")
        return False
    if not is_bbrush_mode():
        cls.poll_message_set("Bbrush mode is not active")
        return False
    return True


def active_sculpt_brush(context):
    tool_settings = getattr(context, "tool_settings", None)
    sculpt = getattr(tool_settings, "sculpt", None)
    return getattr(sculpt, "brush", None) if sculpt is not None else None


class BbrushAlt4Deferred(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_deferred"
    bl_label = "Alt+4 Port Status"
    bl_description = "Explain the implementation status of this ZBrush control"
    bl_options = {"INTERNAL"}

    feature: bpy.props.EnumProperty(
        items=[
            (identifier, title, description)
            for identifier, (title, description) in DEFERRED_FEATURES.items()
        ]
    )

    @classmethod
    def poll(cls, context):
        return poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        title, description = DEFERRED_FEATURES[self.feature]
        self.report({"INFO"}, f"{title}: {description}")
        return {"FINISHED"}


class BbrushAlt4ToggleDyntopo(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_toggle_dyntopo"
    bl_label = "Toggle Sculptris Pro"
    bl_description = "Toggle Blender Dynamic Topology, the native Sculptris Pro equivalent"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        try:
            result = bpy.ops.sculpt.dynamic_topology_toggle("EXEC_DEFAULT")
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not toggle Dynamic Topology: {exc}")
            return {"CANCELLED"}
        return result if "FINISHED" in result else {"CANCELLED"}


class BbrushAlt4ToggleDetailMode(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_toggle_detail_mode"
    bl_label = "Toggle Adaptive Size"
    bl_description = "Switch Dyntopo detail between brush-relative and view-relative sizing"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        sculpt = context.tool_settings.sculpt
        sculpt.detail_type_method = (
            "RELATIVE" if sculpt.detail_type_method == "BRUSH" else "BRUSH"
        )
        return {"FINISHED"}


class BbrushAlt4ToggleCombined(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_toggle_combined"
    bl_label = "Toggle Combined"
    bl_description = "Combine Dyntopo subdivision and edge collapse, or subdivide only"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        sculpt = context.tool_settings.sculpt
        sculpt.detail_refine_method = (
            "SUBDIVIDE"
            if sculpt.detail_refine_method == "SUBDIVIDE_COLLAPSE"
            else "SUBDIVIDE_COLLAPSE"
        )
        return {"FINISHED"}


class BbrushAlt4ToggleAutomask(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_toggle_automask"
    bl_label = "Toggle Auto Mask"
    bl_options = {"REGISTER"}

    target: bpy.props.EnumProperty(
        items=[
            ("FACE_SETS", "Mask By Polygroups", "Limit each stroke to its initial Face Set"),
            ("TOPOLOGY", "Topological", "Limit each stroke to connected topology"),
            ("BACKFACE", "BackfaceMask", "Affect only vertices facing the view"),
        ]
    )

    @classmethod
    def poll(cls, context):
        return poll_bbrush_sculpt(cls, context)

    def execute(self, context):
        sculpt = context.tool_settings.sculpt
        if self.target == "FACE_SETS":
            sculpt.use_automasking_face_sets = not sculpt.use_automasking_face_sets
        elif self.target == "TOPOLOGY":
            sculpt.use_automasking_topology = not sculpt.use_automasking_topology
        else:
            brush = active_sculpt_brush(context)
            if brush is None:
                self.report({"WARNING"}, "No editable sculpt brush is active")
                return {"CANCELLED"}
            brush.use_frontface = not brush.use_frontface
        return {"FINISHED"}


class BbrushAlt4ToggleStrokeMode(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_toggle_stroke_mode"
    bl_label = "Toggle Stroke Mode"
    bl_options = {"REGISTER"}

    mode: bpy.props.EnumProperty(
        items=[
            ("CURVE", "Curve Mode", "Use Blender's reusable Paint Curve stroke"),
            ("LINE", "AsLine", "Apply brush dabs along a screen-space line"),
        ]
    )

    @classmethod
    def poll(cls, context):
        if not poll_bbrush_sculpt(cls, context):
            return False
        if active_sculpt_brush(context) is None:
            cls.poll_message_set("No editable sculpt brush is active")
            return False
        return True

    def execute(self, context):
        brush = active_sculpt_brush(context)
        brush.stroke_method = "SPACE" if brush.stroke_method == self.mode else self.mode
        return {"FINISHED"}


class BbrushAlt4PaintCurveSnapshot(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_paint_curve_snapshot"
    bl_label = "Snapshot Curve"
    bl_description = "Apply the current Blender Paint Curve and keep it available for reuse"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not poll_bbrush_sculpt(cls, context):
            return False
        brush = active_sculpt_brush(context)
        if brush is None or brush.stroke_method != "CURVE" or brush.paint_curve is None:
            cls.poll_message_set("Curve Mode needs an active Paint Curve")
            return False
        return True

    def execute(self, context):
        try:
            result = bpy.ops.paintcurve.draw("EXEC_DEFAULT")
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not apply Paint Curve: {exc}")
            return {"CANCELLED"}
        return result if "FINISHED" in result else {"CANCELLED"}


class BbrushAlt4PaintCurveDelete(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_paint_curve_delete"
    bl_label = "Delete Curve"
    bl_description = "Replace the active Paint Curve with a new empty curve"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not poll_bbrush_sculpt(cls, context):
            return False
        brush = active_sculpt_brush(context)
        if brush is None or brush.stroke_method != "CURVE":
            cls.poll_message_set("Curve Mode is not active")
            return False
        return True

    def execute(self, context):
        brush = active_sculpt_brush(context)
        try:
            result = bpy.ops.paintcurve.new("EXEC_DEFAULT")
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not clear Paint Curve: {exc}")
            return {"CANCELLED"}
        if "FINISHED" not in result or brush.paint_curve is None:
            self.report({"WARNING"}, "Blender did not create an empty Paint Curve")
            return {"CANCELLED"}
        return {"FINISHED"}


CLASSES = (
    BbrushAlt4Deferred,
    BbrushAlt4ToggleDyntopo,
    BbrushAlt4ToggleDetailMode,
    BbrushAlt4ToggleCombined,
    BbrushAlt4ToggleAutomask,
    BbrushAlt4ToggleStrokeMode,
    BbrushAlt4PaintCurveSnapshot,
    BbrushAlt4PaintCurveDelete,
)

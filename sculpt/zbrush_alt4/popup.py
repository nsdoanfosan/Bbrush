import bpy

from .operators import active_sculpt_brush, poll_bbrush_sculpt
from .undo import ensure_undo_baseline


def _deferred(layout, text, feature, *, depress=False):
    op = layout.operator(
        "sculpt.bbrush_alt4_deferred", text=text, depress=depress
    )
    op.feature = feature
    return op


def _numeric(layout, text, target, value):
    if isinstance(value, int):
        value_text = str(value)
    else:
        value_text = f"{value:.3g}"
    op = layout.operator(
        "sculpt.bbrush_alt4_set_numeric",
        text=f"{text}  {value_text}",
    )
    op.target = target
    return op


def _dyntopo_active(context):
    obj = getattr(context, "sculpt_object", None) or context.active_object
    return bool(getattr(obj, "use_dynamic_topology_sculpting", False))


def _draw_brush(layout):
    box = layout.box()
    box.label(text="Brush")
    row = box.row(align=True)
    _deferred(row, "From Mesh", "VDM_FROM_MESH")
    _deferred(row, "To Mesh", "VDM_TO_MESH")
    _deferred(box, "Spotlight Projection", "SPOTLIGHT")


def _draw_sculptris(layout, context):
    sculpt = context.tool_settings.sculpt
    active = _dyntopo_active(context)
    adaptive = sculpt.detail_type_method == "BRUSH"
    combined = sculpt.detail_refine_method == "SUBDIVIDE_COLLAPSE"

    box = layout.box()
    box.label(text="Sculptris Pro  /  Dyntopo")
    row = box.row(align=True)
    row.operator(
        "sculpt.bbrush_alt4_toggle_dyntopo",
        text="Enable",
        depress=active,
    )
    _deferred(row, "Use Global", "USE_GLOBAL", depress=True)

    controls = box.column(align=True)
    controls.enabled = active
    row = controls.row(align=True)
    row.operator(
        "sculpt.bbrush_alt4_toggle_detail_mode",
        text="Adaptive Size",
        depress=adaptive,
    )
    row.operator(
        "sculpt.bbrush_alt4_toggle_combined",
        text="Combined",
        depress=combined,
    )

    if adaptive:
        _numeric(
            controls,
            "SubDivide Size",
            "DETAIL_PERCENT",
            sculpt.detail_percent,
        )
    else:
        _numeric(
            controls,
            "SubDivide Size",
            "DETAIL_SIZE",
            sculpt.detail_size,
        )
    _deferred(
        controls,
        "UnDivide Ratio  (separate threshold pending)",
        "UNDIVIDE_RATIO",
    )


def _draw_automasking(layout, context):
    sculpt = context.tool_settings.sculpt
    brush = active_sculpt_brush(context)

    box = layout.box()
    box.label(text="Auto Masking")

    op = box.operator(
        "sculpt.bbrush_alt4_toggle_automask",
        text=f"Mask By Polygroups  {100 if sculpt.use_automasking_face_sets else 0}",
        depress=sculpt.use_automasking_face_sets,
    )
    op.target = "FACE_SETS"

    row = box.row(align=True)
    op = row.operator(
        "sculpt.bbrush_alt4_toggle_automask",
        text="BackfaceMask",
        depress=bool(brush and brush.use_frontface),
    )
    op.target = "BACKFACE"
    _deferred(row, "BackMaskInt", "BACK_MASK_INT")

    op = box.operator(
        "sculpt.bbrush_alt4_toggle_automask",
        text="Topological",
        depress=sculpt.use_automasking_topology,
    )
    op.target = "TOPOLOGY"
    row = box.row(align=True)
    _deferred(row, "Range  5", "TOPOLOGY_RANGE")
    _deferred(row, "Smooth  10", "TOPOLOGY_SMOOTH")


def _draw_clip(layout):
    box = layout.box()
    box.label(text="Clip Brush Modifiers")
    row = box.row(align=True)
    _deferred(row, "BRadius", "CLIP_RADIUS")
    _deferred(row, "PolyGroup", "CLIP_POLYGROUP")


def _draw_stroke(layout, context):
    brush = active_sculpt_brush(context)
    box = layout.box()
    box.label(text="Stroke")

    if brush is None:
        box.label(text="No editable sculpt brush is active", icon="INFO")
        return

    row = box.row(align=True)
    row.operator(
        "sculpt.bbrush_alt4_toggle_lazy_mouse",
        text="LazyMouse",
        depress=brush.use_smooth_stroke,
    )
    relative = row.row(align=True)
    relative.enabled = False
    relative.label(text="Relative (native)")

    _numeric(box, "LazyStep / Spacing", "SPACING", brush.spacing)
    lazy = box.column(align=True)
    lazy.enabled = brush.use_smooth_stroke
    _numeric(
        lazy,
        "LazySmooth",
        "SMOOTH_FACTOR",
        brush.smooth_stroke_factor,
    )
    _numeric(
        lazy,
        "LazyRadius",
        "SMOOTH_RADIUS",
        brush.smooth_stroke_radius,
    )
    _deferred(box, "LazySnap  0", "LAZY_SNAP")

    row = box.row(align=True)
    op = row.operator(
        "sculpt.bbrush_alt4_toggle_stroke_mode",
        text="Curve Mode",
        depress=brush.stroke_method == "CURVE",
    )
    op.mode = "CURVE"
    op = row.operator(
        "sculpt.bbrush_alt4_toggle_stroke_mode",
        text="AsLine",
        depress=brush.stroke_method == "LINE",
    )
    op.mode = "LINE"

    curve_controls = box.column(align=True)
    curve_controls.enabled = brush.stroke_method in {"CURVE", "LINE"}
    _numeric(curve_controls, "CurveStep / Spacing", "SPACING", brush.spacing)
    _deferred(curve_controls, "Curve Smoothness", "CURVE_SMOOTHNESS")

    row = box.row(align=True)
    _deferred(row, "Snap", "CURVE_SNAP")
    row.operator("sculpt.bbrush_alt4_paint_curve_delete", text="Delete")
    row = box.row(align=True)
    row.operator("sculpt.bbrush_alt4_paint_curve_snapshot", text="Snapshot")
    _deferred(row, "Smooth", "CURVE_SMOOTH")

    row = box.row(align=True)
    _deferred(row, "Frame Mesh", "FRAME_MESH")
    _deferred(row, "Border", "FRAME_BORDER")
    row = box.row(align=True)
    _deferred(row, "Polygroups", "FRAME_POLYGROUPS")
    _deferred(row, "Creased edges", "FRAME_CREASED")

    row = box.row(align=True)
    _deferred(row, "Intensity", "CURVE_INTENSITY", depress=True)
    _deferred(row, "Size", "CURVE_SIZE")


class BbrushAlt4Popup(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_alt4_popup"
    bl_label = "ZBrush Alt+4 Brush Controls"
    bl_description = (
        "Open Bbrush's ZBrush-style brush, Dyntopo, automasking, clip, and stroke controls"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return poll_bbrush_sculpt(cls, context)

    def invoke(self, context, event):
        ensure_undo_baseline(context)
        return context.window_manager.invoke_popup(self, width=300)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        _draw_brush(layout)
        _draw_sculptris(layout, context)
        _draw_automasking(layout, context)
        _draw_clip(layout)
        _draw_stroke(layout, context)
        layout.label(
            text="Working controls use Blender 5.1 native behavior",
            icon="CHECKMARK",
        )

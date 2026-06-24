"""Shift hold activates a secondary sculpt brush; release restores primary."""

import bpy

_FALLBACK_ESSENTIALS_SMOOTH_REF = (
    "ESSENTIALS",
    "",
    "brushes/essentials_brushes-mesh_sculpt.blend/Brush/Smooth",
)


def _read_brush_asset_triple(context):
    ref = context.tool_settings.sculpt.brush_asset_reference
    return (
        ref.asset_library_type,
        ref.asset_library_identifier,
        ref.relative_asset_identifier,
    )


def _activate_brush_asset(context, triple):
    if not triple:
        return False
    try:
        ret = bpy.ops.brush.asset_activate(
            asset_library_type=triple[0],
            asset_library_identifier=triple[1],
            relative_asset_identifier=triple[2],
        )
        return "FINISHED" in ret
    except RuntimeError:
        return False


def sync_shift_secondary_brush(context, event):
    if bpy.app.version < (5, 1, 0) or event is None:
        return
    if event.type not in {"LEFT_SHIFT", "RIGHT_SHIFT"}:
        return

    from . import brush_runtime

    if event.value == "RELEASE":
        if not event.shift and brush_runtime.shift_secondary_active:
            brush_runtime.shift_secondary_brush_ref = _read_brush_asset_triple(context)
            _activate_brush_asset(context, brush_runtime.shift_primary_saved_ref)
            brush_runtime.shift_secondary_active = False
        return

    if event.ctrl or event.alt or brush_runtime.shift_secondary_active:
        return

    brush_runtime.shift_primary_saved_ref = _read_brush_asset_triple(context)
    if not _activate_brush_asset(context, brush_runtime.shift_secondary_brush_ref):
        _activate_brush_asset(context, _FALLBACK_ESSENTIALS_SMOOTH_REF)
    brush_runtime.shift_secondary_active = True


def clear_shift_secondary_override(context):
    if bpy.app.version < (5, 1, 0):
        return

    from . import brush_runtime

    if not getattr(brush_runtime, "shift_secondary_active", False):
        return
    brush_runtime.shift_secondary_brush_ref = _read_brush_asset_triple(context)
    _activate_brush_asset(context, brush_runtime.shift_primary_saved_ref)
    brush_runtime.shift_secondary_active = False

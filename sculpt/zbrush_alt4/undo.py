import json
import time

import bpy
from bpy.app.handlers import persistent


STATE_KEY = "_bbrush_alt4_undo_state_v1"

_last_payload_by_scene = {}
_syncing = False


def _active_sculpt_brush(context):
    tool_settings = getattr(context, "tool_settings", None)
    sculpt = getattr(tool_settings, "sculpt", None)
    return getattr(sculpt, "brush", None) if sculpt is not None else None


def _paint_curve_name(brush):
    paint_curve = getattr(brush, "paint_curve", None)
    return paint_curve.name if paint_curve is not None else None


def capture_state(context):
    sculpt = context.tool_settings.sculpt
    brush = _active_sculpt_brush(context)
    obj = getattr(context, "sculpt_object", None) or context.active_object
    state = {
        "version": 1,
        "sculpt": {
            "detail_type_method": sculpt.detail_type_method,
            "detail_refine_method": sculpt.detail_refine_method,
            "detail_percent": sculpt.detail_percent,
            "detail_size": sculpt.detail_size,
            "use_automasking_face_sets": sculpt.use_automasking_face_sets,
            "use_automasking_topology": sculpt.use_automasking_topology,
        },
        "brush": None,
        "object": None,
    }
    if brush is not None:
        state["brush"] = {
            "name": brush.name,
            "use_frontface": brush.use_frontface,
            "use_smooth_stroke": brush.use_smooth_stroke,
            "stroke_method": brush.stroke_method,
            "spacing": brush.spacing,
            "smooth_stroke_factor": brush.smooth_stroke_factor,
            "smooth_stroke_radius": brush.smooth_stroke_radius,
            "paint_curve": _paint_curve_name(brush),
        }
    if obj is not None and obj.type == "MESH":
        state["object"] = {
            "name": obj.name,
            "use_dynamic_topology_sculpting": bool(
                getattr(obj, "use_dynamic_topology_sculpting", False)
            ),
        }
    return state


def _encode_state(state):
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def _push(message):
    try:
        return "FINISHED" in bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        return False


def ensure_undo_baseline(context, message="Bbrush Alt+4 state"):
    scene = context.scene
    payload = _encode_state(capture_state(context))
    if scene.get(STATE_KEY) != payload:
        scene[STATE_KEY] = payload
        if not _push(message):
            return False
    _last_payload_by_scene[scene.name_full] = payload
    return True


def run_undoable_change(context, message, change):
    if not ensure_undo_baseline(context, f"{message} (before)"):
        raise RuntimeError("Blender could not create the Alt+4 Undo baseline")

    before = _encode_state(capture_state(context))
    change()
    after = _encode_state(capture_state(context))
    if after == before:
        return False

    scene = context.scene
    scene[STATE_KEY] = after
    if not _push(message):
        scene[STATE_KEY] = before
        _apply_payload(scene, before)
        raise RuntimeError("Blender could not create the Alt+4 Undo step")

    _last_payload_by_scene[scene.name_full] = after
    return True


def run_undoable_data_change(context, message, change):
    if not ensure_undo_baseline(context, f"{message} (before)"):
        raise RuntimeError("Blender could not create the Alt+4 Undo baseline")

    change()
    state = capture_state(context)
    state["action_token"] = time.time_ns()
    payload = _encode_state(state)
    scene = context.scene
    scene[STATE_KEY] = payload
    if not _push(message):
        raise RuntimeError("Blender could not create the Alt+4 Undo step")
    _last_payload_by_scene[scene.name_full] = payload


def _set_enum(owner, name, value):
    if value is not None and hasattr(owner, name):
        try:
            setattr(owner, name, value)
        except (AttributeError, TypeError, ValueError):
            pass


def _apply_brush_state(brush_state):
    if not brush_state:
        return
    brush = bpy.data.brushes.get(brush_state.get("name", ""))
    if brush is None:
        return

    for name in (
        "use_frontface",
        "use_smooth_stroke",
        "spacing",
        "smooth_stroke_factor",
        "smooth_stroke_radius",
    ):
        if name in brush_state and hasattr(brush, name):
            setattr(brush, name, brush_state[name])
    _set_enum(brush, "stroke_method", brush_state.get("stroke_method"))

    curve_name = brush_state.get("paint_curve")
    paint_curves = getattr(bpy.data, "paint_curves", None)
    if paint_curves is not None:
        curve = paint_curves.get(curve_name) if curve_name else None
        try:
            brush.paint_curve = curve
        except (AttributeError, TypeError, RuntimeError):
            pass


def _apply_object_state(object_state):
    if not object_state:
        return
    obj = bpy.data.objects.get(object_state.get("name", ""))
    target = object_state.get("use_dynamic_topology_sculpting")
    if obj is None or target is None:
        return
    current = bool(getattr(obj, "use_dynamic_topology_sculpting", False))
    if current == target:
        return
    if bpy.context.active_object != obj or obj.mode != "SCULPT":
        return
    try:
        bpy.ops.sculpt.dynamic_topology_toggle("EXEC_DEFAULT")
    except RuntimeError:
        pass


def _apply_payload(scene, payload):
    global _syncing
    if _syncing:
        return
    try:
        state = json.loads(payload)
    except (TypeError, ValueError):
        return
    if state.get("version") != 1:
        return

    _syncing = True
    try:
        sculpt = scene.tool_settings.sculpt
        sculpt_state = state.get("sculpt", {})
        _set_enum(
            sculpt,
            "detail_type_method",
            sculpt_state.get("detail_type_method"),
        )
        _set_enum(
            sculpt,
            "detail_refine_method",
            sculpt_state.get("detail_refine_method"),
        )
        for name in (
            "detail_percent",
            "detail_size",
            "use_automasking_face_sets",
            "use_automasking_topology",
        ):
            if name in sculpt_state and hasattr(sculpt, name):
                setattr(sculpt, name, sculpt_state[name])
        _apply_brush_state(state.get("brush"))
        _apply_object_state(state.get("object"))
    finally:
        _syncing = False


@persistent
def sync_after_undo(_unused):
    for scene in bpy.data.scenes:
        payload = scene.get(STATE_KEY)
        if not isinstance(payload, str):
            continue
        if _last_payload_by_scene.get(scene.name_full) == payload:
            continue
        _apply_payload(scene, payload)
        _last_payload_by_scene[scene.name_full] = payload


@persistent
def sync_after_redo(_unused):
    sync_after_undo(_unused)


def register():
    if sync_after_undo not in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.append(sync_after_undo)
    if sync_after_redo not in bpy.app.handlers.redo_post:
        bpy.app.handlers.redo_post.append(sync_after_redo)


def unregister():
    if sync_after_undo in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.remove(sync_after_undo)
    if sync_after_redo in bpy.app.handlers.redo_post:
        bpy.app.handlers.redo_post.remove(sync_after_redo)
    _last_payload_by_scene.clear()

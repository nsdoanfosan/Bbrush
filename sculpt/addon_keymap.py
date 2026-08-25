import bpy

addon_keymaps = []
runtime_keymaps = []

MASK_KEYS = [
    ("sculpt.mask_filter", {"type": "NUMPAD_PLUS", "value": "PRESS", "ctrl": True, "repeat": True},
     {"filter_type": "GROW", "auto_iteration_count": True}),
    ("sculpt.mask_filter", {"type": "NUMPAD_MINUS", "value": "PRESS", "ctrl": True, "repeat": True},
     {"filter_type": "SHRINK", "auto_iteration_count": True}),
    ("sculpt.mask_filter", {"type": "UP_ARROW", "value": "PRESS", "ctrl": True, "repeat": True},
     {"filter_type": "CONTRAST_INCREASE", "auto_iteration_count": True}),
    ("sculpt.mask_filter", {"type": "NUMPAD_ASTERIX", "value": "PRESS", "ctrl": True, "repeat": True},
     {"filter_type": "CONTRAST_INCREASE", "auto_iteration_count": True}),
    ("sculpt.mask_filter", {"type": "DOWN_ARROW", "value": "PRESS", "ctrl": True, "repeat": True},
     {"filter_type": "CONTRAST_DECREASE", "auto_iteration_count": True}),
    ("sculpt.mask_filter", {"type": "NUMPAD_SLASH", "value": "PRESS", "ctrl": True, "repeat": True},
     {"filter_type": "CONTRAST_DECREASE", "auto_iteration_count": True}),
]

UPDATE_BRUSH_SHELF_KEYS = [
    ("sculpt.bbrush_update_brush_shelf", {"type": "LEFT_CTRL", "value": "ANY", "any": True}, None),
    ("sculpt.bbrush_update_brush_shelf", {"type": "RIGHT_CTRL", "value": "ANY", "any": True}, None),
    ("sculpt.bbrush_update_brush_shelf", {"type": "LEFT_ALT", "value": "ANY", "any": True}, None),
    ("sculpt.bbrush_update_brush_shelf", {"type": "RIGHT_ALT", "value": "ANY", "any": True}, None),
    ("sculpt.bbrush_update_brush_shelf", {"type": "LEFT_SHIFT", "value": "ANY", "any": True}, None),
    ("sculpt.bbrush_update_brush_shelf", {"type": "RIGHT_SHIFT", "value": "ANY", "any": True}, None),
]

RUNTIME_KEYS = [
    (
        "sculpt.bbrush_activate_transform_gizmo",
        {"type": "W", "value": "PRESS"},
        None,
    ),
    (
        "sculpt.bbrush_face_set_from_mask",
        {"type": "W", "value": "PRESS", "shift": True},
        None,
    ),
    (
        "sculpt.bbrush_face_set_from_mask",
        {"type": "W", "value": "PRESS", "ctrl": True},
        None,
    ),
    (
        "wm.radial_control",
        {"type": "S", "value": "PRESS"},
        {
            "data_path_primary": "tool_settings.sculpt.brush.strength",
            "data_path_secondary": "tool_settings.sculpt.unified_paint_settings.strength",
            "use_secondary": "tool_settings.sculpt.unified_paint_settings.use_unified_strength",
            "rotation_path": "tool_settings.sculpt.brush.texture_slot.angle",
            "color_path": "tool_settings.sculpt.brush.cursor_color_add",
            "image_id": "tool_settings.sculpt.brush",
            "secondary_tex": False,
            "release_confirm": False,
        },
    ),
    (
        "sculpt.bbrush_toggle_face_sets",
        {"type": "F", "value": "PRESS", "shift": True},
        None,
    ),
    (
        "sculpt.bbrush_deactivate_transform_gizmo",
        {"type": "T", "value": "PRESS"},
        None,
    ),
    (
        "sculpt.bbrush_set_transform_pivot_surface",
        {"type": "LEFTMOUSE", "value": "PRESS", "alt": True},
        None,
    ),
]

TOOL_KEYMAP_NAMES = (
    "3D View Tool: Sculpt, Box Mask",
    "3D View Tool: Sculpt, Lasso Mask",
    "3D View Tool: Sculpt, Line Mask",
    "3D View Tool: Sculpt, Polyline Mask",
    "3D View Tool: Sculpt, Box Hide",
    "3D View Tool: Sculpt, Lasso Hide",
    "3D View Tool: Sculpt, Line Hide",
    "3D View Tool: Sculpt, Polyline Hide",
    "3D View Tool: Sculpt, Box Trim",
)


def _add_keymap_item(
        km, idname, event, properties=None, *, head=False, runtime_only=False):
    kmi = km.keymap_items.new(
        idname,
        event["type"],
        event["value"],
        any=event.get("any", False),
        head=head,
    )
    for key in ("ctrl", "alt", "shift", "repeat"):
        if key in event:
            setattr(kmi, key, event[key])
    if properties:
        for prop, value in properties.items():
            setattr(kmi.properties, prop, value)
    addon_keymaps.append((km, kmi))
    if runtime_only:
        kmi.active = False
        runtime_keymaps.append(kmi)
    return kmi


def _register_sculpt_keymap(kc):
    km = kc.keymaps.new(name="Sculpt", space_type="EMPTY", region_type="WINDOW")

    # ZBrush-style overrides are only active while the Bbrush runtime is active.
    # At other times Blender's default Sculpt shortcuts remain untouched.
    for idname, event, properties in RUNTIME_KEYS:
        _add_keymap_item(
            km, idname, event, properties, head=True, runtime_only=True)

    _add_keymap_item(km, "sculpt.bbrush_left_mouse", {"type": "LEFTMOUSE", "value": "PRESS", "any": True})
    _add_keymap_item(km, "sculpt.bbrush_right_mouse", {"type": "RIGHTMOUSE", "value": "PRESS", "any": True})

    for idname, event, properties in UPDATE_BRUSH_SHELF_KEYS:
        _add_keymap_item(km, idname, event, properties)

    for idname, event, properties in MASK_KEYS:
        _add_keymap_item(km, idname, event, properties)


def _register_tool_keymaps(kc):
    for name in TOOL_KEYMAP_NAMES:
        km = kc.keymaps.new(name=name, space_type="VIEW_3D", region_type="WINDOW")
        _add_keymap_item(km, "sculpt.bbrush_left_mouse", {"type": "LEFTMOUSE", "value": "PRESS", "any": True})


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return

    _register_sculpt_keymap(kc)
    _register_tool_keymaps(kc)
    # Blender 5.1+ addon keyconfig does not support modal keymaps.
    # Gear-style rotate/move/zoom switching relies on default modal keymaps;
    # view3d_event() in left/right mouse still handles basic view navigation.


def set_runtime_keymaps_active(active):
    for item in runtime_keymaps:
        item.active = bool(active)


def unregister():
    set_runtime_keymaps_active(False)
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    runtime_keymaps.clear()

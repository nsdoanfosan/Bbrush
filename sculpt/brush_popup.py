"""ZBrush-style brush popup and two-stage mnemonic selection."""

from dataclasses import dataclass, replace
from math import ceil
from pathlib import Path
import re

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..utils import get_context_mode, is_bbrush_mode, refresh_ui


_SCULPT_ASSET_FILE = "essentials_brushes-mesh_sculpt.blend"
_KEY_FALLBACK = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_DIGIT_EVENTS = {
    "ZERO": "0",
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "SIX": "6",
    "SEVEN": "7",
    "EIGHT": "8",
    "NINE": "9",
    "NUMPAD_0": "0",
    "NUMPAD_1": "1",
    "NUMPAD_2": "2",
    "NUMPAD_3": "3",
    "NUMPAD_4": "4",
    "NUMPAD_5": "5",
    "NUMPAD_6": "6",
    "NUMPAD_7": "7",
    "NUMPAD_8": "8",
    "NUMPAD_9": "9",
}


@dataclass(frozen=True)
class BrushPopupEntry:
    name: str
    first_key: str
    second_key: str = ""
    relative_identifier: str = ""
    local_name: str = ""


def _event_key(event):
    """Return an ASCII mnemonic for a Blender keyboard event."""
    event_type = event.type
    if len(event_type) == 1 and "A" <= event_type <= "Z":
        return event_type
    return _DIGIT_EVENTS.get(event_type)


def _first_ascii_key(name):
    match = re.search(r"[A-Za-z0-9]", name)
    return match.group(0).upper() if match else "#"


def _mnemonic_candidates(name, first_key):
    words = re.findall(r"[A-Za-z0-9]+", name.upper())
    candidates = []
    if len(words) > 1:
        candidates.extend(word[0] for word in words[1:] if word)
    candidates.extend(char for char in "".join(words)[1:] if char != first_key)
    candidates.extend(_KEY_FALLBACK)

    seen = set()
    for candidate in candidates:
        if candidate == first_key or candidate in seen:
            continue
        seen.add(candidate)
        yield candidate


def assign_brush_mnemonics(entries):
    """Assign a unique second key within each first-letter brush group."""
    grouped = {}
    for entry in sorted(entries, key=lambda item: item.name.casefold()):
        grouped.setdefault(entry.first_key, []).append(entry)

    assigned = []
    for first_key in sorted(grouped):
        used = set()
        for entry in grouped[first_key]:
            second_key = next(
                (key for key in _mnemonic_candidates(entry.name, first_key) if key not in used),
                "",
            )
            if second_key:
                used.add(second_key)
            assigned.append(replace(entry, second_key=second_key))
    return sorted(assigned, key=lambda item: item.name.casefold())


def _essentials_sculpt_path():
    return (
        Path(bpy.utils.system_resource("DATAFILES"))
        / "assets"
        / "brushes"
        / _SCULPT_ASSET_FILE
    )


def available_sculpt_brushes():
    """Return Essentials assets plus loaded local/custom Sculpt brushes."""
    entries = []
    seen = set()
    asset_file = _essentials_sculpt_path()
    if asset_file.is_file():
        with bpy.data.libraries.load(str(asset_file), assets_only=True) as (data_from, _data_to):
            names = list(data_from.brushes)
        for raw_name in names:
            name = raw_name.strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                BrushPopupEntry(
                    name=name,
                    first_key=_first_ascii_key(name),
                    relative_identifier=(
                        f"brushes/{_SCULPT_ASSET_FILE}/Brush/{raw_name}"
                    ),
                )
            )

    for brush in bpy.data.brushes:
        if not getattr(brush, "use_paint_sculpt", False):
            continue
        name = brush.name.strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        entries.append(
            BrushPopupEntry(
                name=name,
                first_key=_first_ascii_key(name),
                local_name=brush.name,
            )
        )

    return assign_brush_mnemonics(entries)


def activate_sculpt_brush(context, entry):
    """Activate an Essentials asset or an already-loaded local Sculpt brush."""
    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.brush")
    except RuntimeError:
        # Background tests have no VIEW_3D tool context. Asset activation still works.
        pass

    status = {"CANCELLED"}
    if entry.relative_identifier:
        try:
            status = bpy.ops.brush.asset_activate(
                asset_library_type="ESSENTIALS",
                relative_asset_identifier=entry.relative_identifier,
            )
        except RuntimeError:
            status = {"CANCELLED"}

    if "FINISHED" not in status:
        brush = bpy.data.brushes.get(entry.local_name or entry.name)
        sculpt = getattr(context.tool_settings, "sculpt", None)
        if brush is None or sculpt is None or not getattr(brush, "use_paint_sculpt", False):
            return False
        sculpt.brush = brush

    from . import brush_runtime
    if brush_runtime is not None:
        brush_runtime.brush_mode = "SCULPT"
    refresh_ui(context)
    return True


def _draw_rect(x, y, width, height, color):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    vertices = (
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
    )
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": vertices},
        indices=((0, 1, 2), (0, 2, 3)),
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_text(text, x, y, size, color):
    blf.size(0, size)
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _fit_text(text, max_width, size):
    blf.size(0, size)
    if blf.dimensions(0, text)[0] <= max_width:
        return text
    shortened = text
    while shortened and blf.dimensions(0, shortened + "...")[0] > max_width:
        shortened = shortened[:-1]
    return shortened + "..." if shortened else ""


class BbrushBrushPopup(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_brush_popup"
    bl_label = "Bbrush Brush Popup"
    bl_description = "Choose a Sculpt brush at the cursor with ZBrush-style mnemonic keys"

    _active_instance = None

    @classmethod
    def poll(cls, context):
        if not is_bbrush_mode():
            cls.poll_message_set("Bbrush mode is not active")
            return False
        if get_context_mode(context) != "SCULPT":
            cls.poll_message_set("Available in Sculpt Mode only")
            return False
        if context.area is None or context.area.type != "VIEW_3D":
            cls.poll_message_set("Run this from the 3D View")
            return False
        if context.region is None or context.region.type != "WINDOW":
            cls.poll_message_set("Run this over the 3D View")
            return False
        return True

    def invoke(self, context, event):
        from .update_brush_shelf import UpdateBrushShelf

        if self.__class__._active_instance is not None:
            self.__class__._active_instance._cleanup(context)

        UpdateBrushShelf.update_brush_shelf(context, event)
        self._entries = available_sculpt_brushes()
        if not self._entries:
            self.report({"WARNING"}, "No Sculpt brushes were found")
            return {"CANCELLED"}

        self._filter_key = None
        self._mouse = (event.mouse_region_x, event.mouse_region_y)
        self._anchor = self._mouse
        self._area_pointer = context.area.as_pointer()
        self._region_pointer = context.region.as_pointer()
        self._button_rects = []
        self._hovered = None
        self._handler = bpy.types.SpaceView3D.draw_handler_add(
            self._draw,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
        self.__class__._active_instance = self
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not self._same_view(context) or not is_bbrush_mode() or get_context_mode(context) != "SCULPT":
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type == "MOUSEMOVE":
            self._mouse = (event.mouse_region_x, event.mouse_region_y)
            self._update_hover()
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self._mouse = (event.mouse_region_x, event.mouse_region_y)
            entry = self._entry_at_mouse()
            if entry is None:
                self._cleanup(context)
                return {"CANCELLED"}
            return self._select(context, entry)

        if event.value == "PRESS" and event.type in {"BACK_SPACE", "DEL"}:
            self._filter_key = None
            self._hovered = None
            self._layout(context)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.value == "PRESS" and event.type in {"RET", "NUMPAD_ENTER"}:
            visible = self._visible_entries()
            if len(visible) == 1:
                return self._select(context, visible[0])
            return {"RUNNING_MODAL"}

        if event.value == "PRESS" and not (event.ctrl or event.alt or event.oskey):
            key = _event_key(event)
            if key is not None:
                if self._filter_key is None:
                    if any(entry.first_key == key for entry in self._entries):
                        self._filter_key = key
                        self._hovered = None
                        self._layout(context)
                        context.area.tag_redraw()
                else:
                    entry = next(
                        (
                            item
                            for item in self._visible_entries()
                            if item.second_key == key
                        ),
                        None,
                    )
                    if entry is not None:
                        return self._select(context, entry)
                return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def cancel(self, context):
        self._cleanup(context)

    def _same_view(self, context):
        return (
            context.area is not None
            and context.region is not None
            and context.area.as_pointer() == self._area_pointer
            and context.region.as_pointer() == self._region_pointer
        )

    def _visible_entries(self):
        if self._filter_key is None:
            return self._entries
        return [entry for entry in self._entries if entry.first_key == self._filter_key]

    def _layout(self, context):
        entries = self._visible_entries()
        ui_scale = max(0.75, context.preferences.system.ui_scale)
        margin = 12.0 * ui_scale
        padding = 8.0 * ui_scale
        header_height = 45.0 * ui_scale
        row_height = 30.0 * ui_scale
        max_rows = max(
            4,
            int((context.region.height - margin * 2.0 - header_height - padding * 2.0) / row_height),
        )
        columns = max(1, ceil(len(entries) / max_rows))
        rows = min(max_rows, len(entries))
        available_width = max(
            180.0,
            context.region.width - margin * 2.0 - padding * 2.0,
        )
        cell_width = min(230.0 * ui_scale, available_width / columns)
        panel_width = cell_width * columns + padding * 2.0
        panel_height = header_height + row_height * rows + padding * 2.0

        anchor_x, anchor_y = self._anchor
        panel_x = min(max(margin, anchor_x), context.region.width - panel_width - margin)
        panel_y = min(max(margin, anchor_y - panel_height), context.region.height - panel_height - margin)
        panel_x = max(margin, panel_x)
        panel_y = max(margin, panel_y)

        rects = []
        content_top = panel_y + panel_height - header_height - padding
        for index, entry in enumerate(entries):
            column = index // max_rows
            row = index % max_rows
            x = panel_x + padding + column * cell_width
            y = content_top - (row + 1) * row_height
            rects.append((x, y, cell_width, row_height, entry))

        self._panel_rect = (panel_x, panel_y, panel_width, panel_height)
        self._button_rects = rects
        self._row_height = row_height
        self._cell_width = cell_width
        self._ui_scale = ui_scale

    def _update_hover(self):
        self._hovered = self._entry_at_mouse()

    def _entry_at_mouse(self):
        mouse_x, mouse_y = self._mouse
        for x, y, width, height, entry in self._button_rects:
            if x <= mouse_x <= x + width and y <= mouse_y <= y + height:
                return entry
        return None

    def _select(self, context, entry):
        if not activate_sculpt_brush(context, entry):
            self.report({"ERROR"}, f"Could not activate Sculpt brush: {entry.name}")
            self._cleanup(context)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Sculpt brush: {entry.name}")
        self._cleanup(context)
        return {"FINISHED"}

    def _cleanup(self, context):
        handler = getattr(self, "_handler", None)
        if handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except (ReferenceError, ValueError):
                pass
            self._handler = None
        if self.__class__._active_instance is self:
            self.__class__._active_instance = None
        area = getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()

    def _draw(self):
        context = bpy.context
        if not self._same_view(context):
            return
        self._layout(context)
        self._update_hover()

        panel_x, panel_y, panel_width, panel_height = self._panel_rect
        scale = self._ui_scale
        active = getattr(getattr(context.tool_settings, "sculpt", None), "brush", None)
        active_name = active.name if active else ""

        gpu.state.blend_set("ALPHA")
        try:
            _draw_rect(panel_x, panel_y, panel_width, panel_height, (0.035, 0.04, 0.05, 0.97))
            _draw_rect(
                panel_x,
                panel_y + panel_height - 45.0 * scale,
                panel_width,
                45.0 * scale,
                (0.075, 0.085, 0.105, 1.0),
            )

            if self._filter_key is None:
                title = "B  Brushes"
                hint = "Type first letter, or click a brush"
            else:
                title = f"B > {self._filter_key}  Brushes"
                hint = "Press the second key  |  Backspace: all  |  Esc: close"
            _draw_text(
                title,
                panel_x + 12.0 * scale,
                panel_y + panel_height - 20.0 * scale,
                int(15 * scale),
                (1, 1, 1, 1),
            )
            _draw_text(
                hint,
                panel_x + 12.0 * scale,
                panel_y + panel_height - 37.0 * scale,
                int(11 * scale),
                (0.65, 0.7, 0.78, 1),
            )

            for x, y, width, height, entry in self._button_rects:
                if entry is self._hovered:
                    color = (0.20, 0.42, 0.68, 0.95)
                elif entry.name == active_name:
                    color = (0.15, 0.31, 0.48, 0.92)
                else:
                    color = (0.09, 0.10, 0.12, 0.92)
                _draw_rect(x + 1, y + 1, width - 2, height - 2, color)

                badge_size = 20.0 * scale
                badge_y = y + (height - badge_size) * 0.5
                _draw_rect(x + 5.0 * scale, badge_y, badge_size, badge_size, (0.22, 0.25, 0.30, 1.0))
                _draw_text(
                    entry.first_key,
                    x + 11.0 * scale,
                    badge_y + 5.0 * scale,
                    int(11 * scale),
                    (0.9, 0.93, 1, 1),
                )
                _draw_rect(x + 28.0 * scale, badge_y, badge_size, badge_size, (0.34, 0.46, 0.64, 1.0))
                _draw_text(
                    entry.second_key or "-",
                    x + 34.0 * scale,
                    badge_y + 5.0 * scale,
                    int(11 * scale),
                    (1, 1, 1, 1),
                )

                text_x = x + 53.0 * scale
                label = _fit_text(entry.name, max(20.0, width - 59.0 * scale), int(12 * scale))
                _draw_text(label, text_x, y + 9.0 * scale, int(12 * scale), (0.94, 0.95, 0.97, 1))
        finally:
            gpu.state.blend_set("NONE")


def cancel_active_popup():
    active = BbrushBrushPopup._active_instance
    if active is not None:
        active._cleanup(bpy.context)


classes = (BbrushBrushPopup,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    cancel_active_popup()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

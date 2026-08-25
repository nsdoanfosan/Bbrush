"""Open Blender's native Sculpt brush library with ZBrush-style mnemonics."""

from dataclasses import dataclass, replace
from pathlib import Path
import re

import bpy

from ..utils import get_context_mode, is_bbrush_mode, refresh_ui


_SCULPT_ASSET_FILE = "essentials_brushes-mesh_sculpt.blend"
_SCULPT_ASSET_SHELF = "VIEW3D_AST_brush_sculpt"
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
                (
                    key
                    for key in _mnemonic_candidates(entry.name, first_key)
                    if key not in used
                ),
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
    """Return Essentials assets plus already-loaded local Sculpt brushes."""
    entries = []
    seen = set()
    asset_file = _essentials_sculpt_path()
    if asset_file.is_file():
        with bpy.data.libraries.load(str(asset_file), assets_only=True) as (
            data_from,
            _data_to,
        ):
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
        if (
            brush is None
            or sculpt is None
            or not getattr(brush, "use_paint_sculpt", False)
        ):
            return False
        sculpt.brush = brush

    from . import brush_runtime

    if brush_runtime is not None:
        brush_runtime.brush_mode = "SCULPT"
    refresh_ui(context)
    return True


class BbrushBrushPopup(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_brush_popup"
    bl_label = "Bbrush Brush Library"
    bl_description = "Open Blender's native Sculpt brush asset library"

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

        active = self.__class__._active_instance
        if active is not None:
            try:
                active._cleanup(context)
            except ReferenceError:
                self.__class__._active_instance = None

        UpdateBrushShelf.update_brush_shelf(context, event)
        self._entries = available_sculpt_brushes()
        self._filter_key = None
        self._starting_brush = self._active_brush_pointer(context)
        self.__class__._active_instance = self

        try:
            status = bpy.ops.wm.call_asset_shelf_popover(
                "INVOKE_DEFAULT",
                name=_SCULPT_ASSET_SHELF,
            )
        except RuntimeError as exc:
            self._cleanup(context)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        if "CANCELLED" in status:
            self._cleanup(context)
            return {"CANCELLED"}

        context.window_manager.modal_handler_add(self)
        self._set_status(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not is_bbrush_mode() or get_context_mode(context) != "SCULPT":
            self._cleanup(context)
            return {"FINISHED"}

        if event.value == "PRESS" and event.type in {"ESC", "RIGHTMOUSE"}:
            self._cleanup(context)
            return {"PASS_THROUGH", "FINISHED"}

        if self._active_brush_pointer(context) != self._starting_brush:
            self._cleanup(context)
            return {"PASS_THROUGH", "FINISHED"}

        if event.value == "PRESS" and event.type in {"BACK_SPACE", "DEL"}:
            self._filter_key = None
            self._set_status(context)
            return {"RUNNING_MODAL"}

        if event.value == "PRESS" and not (
            event.ctrl or event.alt or event.oskey
        ):
            key = _event_key(event)
            if key is not None:
                if self._filter_key is None:
                    if self._set_first_key(context, key):
                        return {"RUNNING_MODAL"}
                else:
                    entry = next(
                        (
                            item
                            for item in self._filtered_entries()
                            if item.second_key == key
                        ),
                        None,
                    )
                    if entry is not None:
                        if activate_sculpt_brush(context, entry):
                            self.report({"INFO"}, f"Sculpt brush: {entry.name}")
                            self._cleanup(context)
                            return {"FINISHED"}
                    elif self._set_first_key(context, key):
                        return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def cancel(self, context):
        self._cleanup(context)

    def _set_first_key(self, context, key):
        if not any(entry.first_key == key for entry in self._entries):
            return False
        self._filter_key = key
        self._set_status(context)
        return True

    def _filtered_entries(self):
        if self._filter_key is None:
            return self._entries
        return [
            entry
            for entry in self._entries
            if entry.first_key == self._filter_key
        ]

    def _set_status(self, context):
        workspace = getattr(context, "workspace", None)
        if workspace is None:
            return
        if self._filter_key is None:
            text = "Bbrush: type a brush's first letter, or use the native library"
        else:
            choices = "   ".join(
                f"{entry.second_key}: {entry.name}"
                for entry in self._filtered_entries()
            )
            text = f"Bbrush {self._filter_key}  |  {choices}"
        workspace.status_text_set(text[:400])

    @staticmethod
    def _active_brush_pointer(context):
        sculpt = getattr(context.tool_settings, "sculpt", None)
        brush = getattr(sculpt, "brush", None) if sculpt else None
        return brush.as_pointer() if brush else 0

    def _cleanup(self, context):
        if self.__class__._active_instance is self:
            self.__class__._active_instance = None
        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            workspace.status_text_set(None)


def cancel_active_popup():
    active = BbrushBrushPopup._active_instance
    if active is not None:
        try:
            active._cleanup(bpy.context)
        except ReferenceError:
            BbrushBrushPopup._active_instance = None


classes = (BbrushBrushPopup,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    cancel_active_popup()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

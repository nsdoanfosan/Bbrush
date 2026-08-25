"""ZBrush Alt+5 inspired deform and PolyGroup tools for Bbrush.

The first implementation deliberately maps PolyGroups to Blender Sculpt Face Sets.
Geometry-changing operators are undoable and never save Blender preferences.
"""

from array import array
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from math import floor, radians
import sys

import bmesh
import bpy
from bpy.props import (
    BoolProperty,
    BoolVectorProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
)


FACE_SET_ATTRIBUTE = ".sculpt_face_set"
MASK_ATTRIBUTE = ".sculpt_mask"
_addon_keymaps = []


def _bbrush_is_running():
    """Work both as a Bbrush submodule and as a standalone live-dev module."""
    for name, module in tuple(sys.modules.items()):
        if not name.endswith(".Bbrush.sculpt"):
            continue
        if getattr(module, "brush_runtime", None) is not None:
            return True
    return False


def _sculpt_mesh(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" and obj.mode == "SCULPT" else None


def _settings(context):
    return context.window_manager.bbrush_zbrush_tools


def _enabled_axes(settings):
    return {axis for enabled, axis in zip(settings.axes, "XYZ") if enabled}


class _SculptOperator:
    @classmethod
    def poll(cls, context):
        if _sculpt_mesh(context) is None:
            cls.poll_message_set("Available for a mesh in Sculpt Mode")
            return False
        if not _bbrush_is_running():
            cls.poll_message_set("Start Bbrush first")
            return False
        return True


@contextmanager
def _object_mode(obj):
    old_mode = obj.mode
    if old_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        yield
    finally:
        if obj.name in bpy.context.view_layer.objects and obj.mode != old_mode:
            bpy.ops.object.mode_set(mode=old_mode)


def _mask_values(mesh):
    values = array("f", [0.0]) * len(mesh.vertices)
    attribute = mesh.attributes.get(MASK_ATTRIBUTE)
    if attribute is not None and attribute.domain == "POINT":
        attribute.data.foreach_get("value", values)
    return values


def _face_set_values(mesh):
    values = array("i", [1]) * len(mesh.polygons)
    attribute = mesh.attributes.get(FACE_SET_ATTRIBUTE)
    if attribute is not None and attribute.domain == "FACE":
        attribute.data.foreach_get("value", values)
    return values


def _write_face_sets(mesh, values):
    attribute = mesh.attributes.get(FACE_SET_ATTRIBUTE)
    if attribute is not None and (attribute.domain != "FACE" or attribute.data_type != "INT"):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(FACE_SET_ATTRIBUTE, "INT", "FACE")
    attribute.data.foreach_set("value", array("i", values))
    mesh.update()


def _edge_faces(mesh):
    result = defaultdict(list)
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            result[mesh.loops[loop_index].edge_index].append(polygon.index)
    return result


def _component_ids(face_count, edge_faces, can_join=None):
    adjacency = [[] for _ in range(face_count)]
    for edge_index, faces in edge_faces.items():
        if len(faces) != 2:
            continue
        a, b = faces
        if can_join is not None and not can_join(edge_index, a, b):
            continue
        adjacency[a].append(b)
        adjacency[b].append(a)

    ids = [0] * face_count
    next_id = 1
    for seed in range(face_count):
        if ids[seed]:
            continue
        ids[seed] = next_id
        queue = deque((seed,))
        while queue:
            face = queue.popleft()
            for neighbor in adjacency[face]:
                if ids[neighbor]:
                    continue
                ids[neighbor] = next_id
                queue.append(neighbor)
        next_id += 1
    return ids, next_id - 1


def _run_filter(operator, context, filter_type, amount, *, preserve_shape=False):
    settings = _settings(context)
    axes = _enabled_axes(settings)
    if not axes:
        operator.report({"WARNING"}, "Enable at least one axis")
        return {"CANCELLED"}
    kwargs = {
        "type": filter_type,
        "strength": amount / 100.0,
        "deform_axis": axes,
        "orientation": settings.orientation,
        "iteration_count": 1,
    }
    if filter_type == "SURFACE_SMOOTH":
        kwargs["surface_smooth_shape_preservation"] = 0.75 if preserve_shape else 0.25
        kwargs["surface_smooth_current_vertex"] = 0.5
    try:
        bpy.ops.sculpt.mesh_filter("EXEC_DEFAULT", **kwargs)
    except RuntimeError as exc:
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}
    return {"FINISHED"}


def _blocked_topology_reason(obj):
    if obj.data.shape_keys is not None:
        return "This prototype does not change mirrored topology while Shape Keys exist"
    if any(mod.type == "MULTIRES" and mod.total_levels > 0 for mod in obj.modifiers):
        return "This prototype does not change mirrored topology with Multires levels"
    return None


class BBRUSH_PG_zbrush_tools(bpy.types.PropertyGroup):
    axes: BoolVectorProperty(
        name="Axes",
        size=3,
        default=(True, False, False),
        description="Global deformation axes (ZBrush Deformation convention)",
    )
    orientation: EnumProperty(
        name="Space",
        items=(("WORLD", "World", "Use global axes"), ("LOCAL", "Local", "Use object axes")),
        default="WORLD",
    )
    polish_features: FloatProperty(name="Polish By Features", default=10.0, min=-100.0, max=100.0)
    polish_groups: FloatProperty(name="Polish By Groups", default=10.0, min=-100.0, max=100.0)
    relax: FloatProperty(name="Relax", default=10.0, min=-100.0, max=100.0)
    inflate: FloatProperty(name="Inflate", default=10.0, min=-100.0, max=100.0)
    preserve_volume: BoolProperty(
        name="Preserve Volume",
        default=True,
        description="Prefer surface smoothing that preserves the original shape",
    )
    resym_source: EnumProperty(
        name="ReSym Source",
        items=(
            ("AVERAGE", "Average", "Average both sides"),
            ("NEGATIVE", "Negative", "Use the negative side as source"),
            ("POSITIVE", "Positive", "Use the positive side as source"),
        ),
        default="AVERAGE",
    )
    resym_threshold: FloatProperty(
        name="Threshold",
        default=0.05,
        min=0.0,
        soft_max=1.0,
        subtype="DISTANCE",
    )
    max_angle: FloatProperty(name="Max Angle", default=45.0, min=0.0, max=180.0, description="Degrees")
    polish_gp: FloatProperty(
        name="PolishGP",
        default=0.0,
        min=0.0,
        max=1.0,
        description="Smooth the mask before creating a Face Set",
    )
    uv_epsilon: FloatProperty(name="UV Tolerance", default=0.00001, min=0.0000001, max=0.01)


class BBRUSH_OT_zbrush_tools_popup(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_tools_popup"
    bl_label = "Bbrush ZBrush Tools"
    bl_description = "Open ZBrush Alt+5 inspired Deform and PolyGroup tools"

    def invoke(self, context, _event):
        return context.window_manager.invoke_popup(self, width=390)

    def execute(self, _context):
        return {"FINISHED"}

    def draw(self, context):
        settings = _settings(context)
        layout = self.layout

        deform = layout.box()
        deform.label(text="Deform")
        header = deform.row(align=True)
        header.prop(settings, "orientation", text="")
        for index, axis in enumerate("XYZ"):
            header.prop(settings, "axes", index=index, text=axis, toggle=True)

        row = deform.row(align=True)
        row.operator("sculpt.bbrush_zbrush_mirror", text="Mirror", icon="MOD_MIRROR")
        row.prop(settings, "preserve_volume", text="Preserve", toggle=True)

        self._amount_row(deform, settings, "polish_features", "sculpt.bbrush_zbrush_polish_features")
        self._amount_row(deform, settings, "polish_groups", "sculpt.bbrush_zbrush_polish_groups")
        self._amount_row(deform, settings, "relax", "sculpt.bbrush_zbrush_relax")

        row = deform.row(align=True)
        row.operator("sculpt.bbrush_zbrush_smart_resym", text="Smart ReSym", icon="MOD_MIRROR")
        row.prop(settings, "resym_source", text="")
        row.prop(settings, "resym_threshold", text="")

        self._amount_row(deform, settings, "inflate", "sculpt.bbrush_zbrush_inflate")

        groups = layout.box()
        groups.label(text="PolyGroup  →  Sculpt Face Sets")
        grid = groups.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        grid.operator("sculpt.bbrush_zbrush_auto_groups", text="Auto Groups")
        grid.operator("sculpt.bbrush_zbrush_uv_groups", text="UV Groups")
        grid.operator("sculpt.bbrush_zbrush_auto_groups_uv", text="Auto Groups With UV")
        grid.operator("sculpt.bbrush_zbrush_merge_stray_groups", text="Merge Stray Groups")

        row = groups.row(align=True)
        row.operator("sculpt.bbrush_zbrush_groups_normals", text="Groups By Normals")
        row.prop(settings, "max_angle", text="Max Angle")

        row = groups.row(align=True)
        row.operator("sculpt.bbrush_zbrush_group_masked", text="Group Masked")
        row.prop(settings, "polish_gp", text="PolishGP")
        groups.operator("sculpt.bbrush_zbrush_group_masked_clear", text="Group Masked Clear Mask")

        note = layout.column(align=True)
        note.scale_y = 0.8
        note.label(text="Prototype: Polish and Smart ReSym use Blender-native approximations", icon="INFO")

    @staticmethod
    def _amount_row(layout, settings, prop_name, operator_id):
        row = layout.row(align=True)
        row.prop(settings, prop_name, slider=True)
        row.operator(operator_id, text="Apply")


class BBRUSH_OT_zbrush_mirror(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_mirror"
    bl_label = "Mirror"
    bl_description = "Reflect the current mesh around its origin; mask controls deformation influence"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _sculpt_mesh(context)
        settings = _settings(context)
        axes = [index for index, enabled in enumerate(settings.axes) if enabled]
        if not axes:
            self.report({"WARNING"}, "Enable at least one axis")
            return {"CANCELLED"}
        reason = _blocked_topology_reason(obj)
        if reason:
            self.report({"ERROR"}, reason)
            return {"CANCELLED"}

        mesh = obj.data
        masks = _mask_values(mesh)
        fully_unmasked = not masks or max(masks, default=0.0) <= 0.000001
        matrix = obj.matrix_world.copy()
        inverse = matrix.inverted_safe()
        pivot = matrix.translation.copy()
        with _object_mode(obj):
            for vertex, mask in zip(mesh.vertices, masks):
                influence = 1.0 - max(0.0, min(1.0, mask))
                if influence <= 0.0:
                    continue
                reflected = vertex.co.copy()
                if settings.orientation == "WORLD":
                    reflected = matrix @ reflected
                    for axis in axes:
                        reflected[axis] = (2.0 * pivot[axis]) - reflected[axis]
                    reflected = inverse @ reflected
                else:
                    for axis in axes:
                        reflected[axis] = -reflected[axis]
                vertex.co = vertex.co.lerp(reflected, influence)

            if fully_unmasked and len(axes) % 2:
                bm = bmesh.new()
                bm.from_mesh(mesh)
                bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
                bm.to_mesh(mesh)
                bm.free()
            mesh.update()
        return {"FINISHED"}


class BBRUSH_OT_zbrush_polish_features(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_polish_features"
    bl_label = "Polish By Features"
    bl_description = "Polish while favoring Face Set borders and the original surface shape"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        result = _run_filter(self, context, "RELAX_FACE_SETS", settings.polish_features)
        if result == {"FINISHED"}:
            return _run_filter(
                self,
                context,
                "SURFACE_SMOOTH",
                settings.polish_features * 0.35,
                preserve_shape=True,
            )
        return result


class BBRUSH_OT_zbrush_polish_groups(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_polish_groups"
    bl_label = "Polish By Groups"
    bl_description = "Polish each Sculpt Face Set while preserving its borders"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _run_filter(self, context, "RELAX_FACE_SETS", _settings(context).polish_groups)


class BBRUSH_OT_zbrush_relax(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_relax"
    bl_label = "Relax"
    bl_description = "Redistribute the mesh surface while retaining sculpted form"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        filter_type = "SURFACE_SMOOTH" if settings.preserve_volume else "RELAX"
        return _run_filter(self, context, filter_type, settings.relax, preserve_shape=True)


class BBRUSH_OT_zbrush_inflate(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_inflate"
    bl_label = "Inflate"
    bl_description = "Move vertices along their normals; negative values deflate"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _run_filter(self, context, "INFLATE", _settings(context).inflate)


class BBRUSH_OT_zbrush_smart_resym(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_smart_resym"
    bl_label = "Smart ReSym"
    bl_description = "Re-symmetrize paired vertices with Blender Symmetrize Snap"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _sculpt_mesh(context)
        settings = _settings(context)
        axes = [axis for enabled, axis in zip(settings.axes, "XYZ") if enabled]
        if not axes:
            self.report({"WARNING"}, "Enable at least one axis")
            return {"CANCELLED"}
        reason = _blocked_topology_reason(obj)
        if reason:
            self.report({"ERROR"}, reason)
            return {"CANCELLED"}

        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.select_all(action="SELECT")
            for axis in axes:
                source = settings.resym_source
                direction = ("POSITIVE_" if source == "POSITIVE" else "NEGATIVE_") + axis
                factor = 0.5 if source == "AVERAGE" else 1.0
                bpy.ops.mesh.symmetry_snap(
                    direction=direction,
                    threshold=settings.resym_threshold,
                    factor=factor,
                    use_center=True,
                )
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            bpy.ops.object.mode_set(mode="SCULPT")
        return {"FINISHED"}


class _GroupOperator(_SculptOperator):
    bl_options = {"REGISTER", "UNDO"}

    def calculate(self, mesh, settings):
        raise NotImplementedError

    def execute(self, context):
        obj = _sculpt_mesh(context)
        with _object_mode(obj):
            try:
                values, group_count, message = self.calculate(obj.data, _settings(context))
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            _write_face_sets(obj.data, values)
        self.report({"INFO"}, message or f"Created {group_count} Sculpt Face Sets")
        return {"FINISHED"}


class BBRUSH_OT_zbrush_auto_groups(_GroupOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_auto_groups"
    bl_label = "Auto Groups"
    bl_description = "Create one Sculpt Face Set per disconnected mesh island"

    def calculate(self, mesh, _settings):
        values, count = _component_ids(len(mesh.polygons), _edge_faces(mesh))
        return values, count, None


class BBRUSH_OT_zbrush_uv_groups(_GroupOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_uv_groups"
    bl_label = "UV Groups"
    bl_description = "Create Sculpt Face Sets from UV tiles"

    def calculate(self, mesh, _settings):
        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            raise ValueError("The active mesh has no UV map")
        tile_ids = {}
        values = []
        crossed = 0
        for polygon in mesh.polygons:
            tiles = {
                (floor(uv_layer.data[loop_index].uv.x), floor(uv_layer.data[loop_index].uv.y))
                for loop_index in polygon.loop_indices
            }
            if len(tiles) > 1:
                crossed += 1
            tile = min(tiles)
            values.append(tile_ids.setdefault(tile, len(tile_ids) + 1))
        message = f"Created {len(tile_ids)} UV-tile Face Sets"
        if crossed:
            message += f"; {crossed} faces crossed tile borders"
        return values, len(tile_ids), message


class BBRUSH_OT_zbrush_auto_groups_uv(_GroupOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_auto_groups_uv"
    bl_label = "Auto Groups With UV"
    bl_description = "Split topology islands further at UV discontinuities"

    def calculate(self, mesh, settings):
        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            raise ValueError("The active mesh has no UV map")
        edge_faces = _edge_faces(mesh)
        face_uv = {}
        for polygon in mesh.polygons:
            face_uv[polygon.index] = {
                mesh.loops[loop_index].vertex_index: uv_layer.data[loop_index].uv.copy()
                for loop_index in polygon.loop_indices
            }

        def uv_continuous(edge_index, face_a, face_b):
            edge = mesh.edges[edge_index]
            for vertex in edge.vertices:
                uv_a = face_uv[face_a].get(vertex)
                uv_b = face_uv[face_b].get(vertex)
                if uv_a is None or uv_b is None or (uv_a - uv_b).length > settings.uv_epsilon:
                    return False
            return True

        values, count = _component_ids(len(mesh.polygons), edge_faces, uv_continuous)
        return values, count, None


class BBRUSH_OT_zbrush_groups_normals(_GroupOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_groups_normals"
    bl_label = "Groups By Normals"
    bl_description = "Create Face Sets separated where neighboring face normals exceed Max Angle"

    def calculate(self, mesh, settings):
        edge_faces = _edge_faces(mesh)
        limit = radians(settings.max_angle)

        def normals_match(_edge_index, face_a, face_b):
            return mesh.polygons[face_a].normal.angle(mesh.polygons[face_b].normal, 0.0) <= limit

        values, count = _component_ids(len(mesh.polygons), edge_faces, normals_match)
        return values, count, None


class BBRUSH_OT_zbrush_merge_stray_groups(_GroupOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_merge_stray_groups"
    bl_label = "Merge Stray Groups"
    bl_description = "Merge isolated one-face Face Sets into their strongest neighboring set"

    def calculate(self, mesh, _settings):
        values = list(_face_set_values(mesh))
        counts = Counter(values)
        edge_faces = _edge_faces(mesh)
        neighbors = defaultdict(Counter)
        for faces in edge_faces.values():
            if len(faces) != 2:
                continue
            a, b = faces
            if values[a] != values[b]:
                neighbors[a][values[b]] += 1
                neighbors[b][values[a]] += 1
        merged = 0
        for face_index, group_id in enumerate(tuple(values)):
            if counts[group_id] != 1 or not neighbors[face_index]:
                continue
            values[face_index] = neighbors[face_index].most_common(1)[0][0]
            merged += 1
        count = len(set(values))
        return values, count, f"Merged {merged} isolated one-face groups"


class _GroupMasked(_SculptOperator):
    bl_options = {"REGISTER", "UNDO"}
    clear_mask = False

    def execute(self, context):
        obj = _sculpt_mesh(context)
        mesh = obj.data
        original_mask = _mask_values(mesh)
        if not original_mask or max(original_mask, default=0.0) <= 0.000001:
            self.report({"WARNING"}, "Mask part of the model first")
            return {"CANCELLED"}

        polish = _settings(context).polish_gp
        if polish > 0.0:
            bpy.ops.sculpt.mask_filter(
                filter_type="SMOOTH",
                iterations=max(1, round(polish * 10.0)),
                auto_iteration_count=False,
            )
        try:
            bpy.ops.sculpt.face_sets_create("EXEC_DEFAULT", mode="MASKED")
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        if self.clear_mask:
            bpy.ops.paint.mask_flood_fill(mode="VALUE", value=0.0)
        elif polish > 0.0:
            with _object_mode(obj):
                attribute = mesh.attributes.get(MASK_ATTRIBUTE)
                if attribute is not None:
                    attribute.data.foreach_set("value", original_mask)
                    mesh.update()
        return {"FINISHED"}


class BBRUSH_OT_zbrush_group_masked(_GroupMasked, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_group_masked"
    bl_label = "Group Masked"
    bl_description = "Create a Sculpt Face Set from the mask and preserve the original mask"


class BBRUSH_OT_zbrush_group_masked_clear(_GroupMasked, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_group_masked_clear"
    bl_label = "Group Masked Clear Mask"
    bl_description = "Create a Sculpt Face Set from the mask, then clear the mask"
    clear_mask = True


classes = (
    BBRUSH_PG_zbrush_tools,
    BBRUSH_OT_zbrush_tools_popup,
    BBRUSH_OT_zbrush_mirror,
    BBRUSH_OT_zbrush_polish_features,
    BBRUSH_OT_zbrush_polish_groups,
    BBRUSH_OT_zbrush_relax,
    BBRUSH_OT_zbrush_inflate,
    BBRUSH_OT_zbrush_smart_resym,
    BBRUSH_OT_zbrush_auto_groups,
    BBRUSH_OT_zbrush_uv_groups,
    BBRUSH_OT_zbrush_auto_groups_uv,
    BBRUSH_OT_zbrush_groups_normals,
    BBRUSH_OT_zbrush_merge_stray_groups,
    BBRUSH_OT_zbrush_group_masked,
    BBRUSH_OT_zbrush_group_masked_clear,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bbrush_zbrush_tools = PointerProperty(type=BBRUSH_PG_zbrush_tools)

    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is not None:
        keymap = keyconfig.keymaps.new(name="Sculpt", space_type="EMPTY")
        item = keymap.keymap_items.new(
            BBRUSH_OT_zbrush_tools_popup.bl_idname,
            type="FIVE",
            value="PRESS",
            alt=True,
        )
        _addon_keymaps.append((keymap, item))


def unregister():
    for keymap, item in reversed(_addon_keymaps):
        try:
            keymap.keymap_items.remove(item)
        except (ReferenceError, RuntimeError):
            pass
    _addon_keymaps.clear()
    if hasattr(bpy.types.WindowManager, "bbrush_zbrush_tools"):
        del bpy.types.WindowManager.bbrush_zbrush_tools
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


if __name__ == "__main__":
    register()

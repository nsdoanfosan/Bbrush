"""ZBrush Alt+5 deform and PolyGroup equivalents for Bbrush.

PolyGroups are represented by Blender Sculpt Face Sets. Geometry-changing
operators are undoable and never save Blender preferences.
"""

from array import array
from collections import defaultdict, deque
from contextlib import contextmanager
import hashlib
from math import ceil, floor, radians
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree
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
    return array("i", (max(1, abs(value)) for value in values))


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


def _loose_part_ids(mesh):
    """Return face components connected by any shared mesh vertex.

    ZBrush Auto Groups operates on self-contained polygon shells, not only on
    pairs of faces sharing a manifold edge. Using vertex connectivity keeps a
    non-manifold shell together and treats a vertex-only contact as connected
    topology, matching Blender's own loose-part definition.
    """
    vertex_faces = [[] for _vertex in mesh.vertices]
    for polygon in mesh.polygons:
        for vertex in polygon.vertices:
            vertex_faces[vertex].append(polygon.index)

    ids = [0] * len(mesh.polygons)
    next_id = 1
    for seed in range(len(mesh.polygons)):
        if ids[seed]:
            continue
        ids[seed] = next_id
        queue = deque((seed,))
        while queue:
            face = queue.popleft()
            for vertex in mesh.polygons[face].vertices:
                for neighbor in vertex_faces[vertex]:
                    if ids[neighbor]:
                        continue
                    ids[neighbor] = next_id
                    queue.append(neighbor)
        next_id += 1
    return ids, next_id - 1


def _run_filter(
    operator,
    context,
    filter_type,
    amount,
    *,
    preserve_shape=False,
    use_axes=True,
):
    settings = _settings(context)
    axes = _enabled_axes(settings) if use_axes else {"X", "Y", "Z"}
    if not axes:
        operator.report({"WARNING"}, "Enable at least one axis")
        return {"CANCELLED"}
    kwargs = {
        "type": filter_type,
        "strength": amount / 100.0,
        "deform_axis": axes,
        "orientation": "WORLD",
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
        return "This operation is disabled while Shape Keys exist"
    if any(mod.type == "MULTIRES" and mod.total_levels > 0 for mod in obj.modifiers):
        return "This operation is disabled with active Multires levels"
    return None


def _vertex_graph(mesh, face_sets, include_creases):
    neighbors = [set() for _ in mesh.vertices]
    feature_neighbors = [set() for _ in mesh.vertices]
    edge_faces = _edge_faces(mesh)

    crease_values = array("f", [0.0]) * len(mesh.edges)
    if include_creases:
        crease = mesh.attributes.get("crease_edge")
        if crease is not None and crease.domain == "EDGE":
            crease.data.foreach_get("value", crease_values)

    for edge in mesh.edges:
        a, b = edge.vertices
        neighbors[a].add(b)
        neighbors[b].add(a)
        faces = edge_faces.get(edge.index, ())
        is_group_border = (
            len(faces) == 2
            and abs(face_sets[faces[0]]) != abs(face_sets[faces[1]])
        )
        is_feature = len(faces) != 2 or is_group_border or crease_values[edge.index] > 0.000001
        if is_feature:
            feature_neighbors[a].add(b)
            feature_neighbors[b].add(a)

    pinned_by_visibility = [False] * len(mesh.vertices)
    hidden = mesh.attributes.get(".hide_poly")
    if hidden is not None and hidden.domain == "FACE":
        hidden_faces = array("b", [0]) * len(mesh.polygons)
        hidden.data.foreach_get("value", hidden_faces)
        seen_hidden = [False] * len(mesh.vertices)
        for polygon in mesh.polygons:
            if not hidden_faces[polygon.index]:
                continue
            for vertex in polygon.vertices:
                seen_hidden[vertex] = True
        # Pin the seam as well as fully hidden geometry. Moving a shared seam
        # vertex would otherwise deform the hidden portion indirectly.
        pinned_by_visibility = seen_hidden

    return neighbors, feature_neighbors, pinned_by_visibility


def _polish_step(coords, neighbors, feature_neighbors, pinned, masks, factor):
    result = [co.copy() for co in coords]
    for index, co in enumerate(coords):
        influence = 1.0 - max(0.0, min(1.0, masks[index]))
        if influence <= 0.0 or pinned[index]:
            continue
        feature = feature_neighbors[index]
        if feature:
            # A clean boundary is a curve. Endpoints, corners, junctions, and
            # non-manifold feature vertices stay pinned instead of rounding over.
            if len(feature) != 2:
                continue
            source = feature
        else:
            source = neighbors[index]
        if not source:
            continue
        target = sum(
            (coords[neighbor] for neighbor in source),
            Vector((0.0, 0.0, 0.0)),
        ) / len(source)
        result[index] = co + ((target - co) * factor * influence)
    return result


def _apply_feature_polish(operator, context, amount, preserve, include_creases):
    if amount <= 0.0:
        return {"FINISHED"}
    obj = _sculpt_mesh(context)
    reason = _blocked_topology_reason(obj)
    if reason:
        operator.report({"ERROR"}, reason)
        return {"CANCELLED"}

    mesh = obj.data
    masks = _mask_values(mesh)
    face_sets = _face_set_values(mesh)
    with _object_mode(obj):
        neighbors, features, pinned = _vertex_graph(mesh, face_sets, include_creases)
        coords = [vertex.co.copy() for vertex in mesh.vertices]
        iterations = max(1, ceil(amount / 10.0))
        strength = min(0.5, max(0.025, (amount / 100.0) * 0.5))
        for _iteration in range(iterations):
            coords = _polish_step(coords, neighbors, features, pinned, masks, strength)
            if preserve:
                # Taubin's opposite-sign pass counters ordinary Laplacian shrink.
                coords = _polish_step(coords, neighbors, features, pinned, masks, -strength)
        for vertex, coordinate in zip(mesh.vertices, coords):
            vertex.co = coordinate
        mesh.update()
    mode = "features and creases" if include_creases else "Face Set borders"
    operator.report({"INFO"}, f"Polished with protected {mode}")
    return {"FINISHED"}


def _symmetry_attribute_name(axis):
    return f".bbrush_symmetry_partner_{axis.lower()}"


def _topology_fingerprint(obj, axis):
    mesh = obj.data
    digest = hashlib.sha256()
    digest.update(f"{len(mesh.vertices)}:{len(mesh.edges)}:{len(mesh.polygons)}:{axis}".encode())
    for edge in mesh.edges:
        a, b = sorted(edge.vertices)
        digest.update(f"{a},{b};".encode())
    # Smart ReSym axes are global. Object rotation therefore belongs to the map contract.
    basis = obj.matrix_world.to_3x3()
    digest.update(
        ",".join(f"{basis[row][column]:.6f}" for row in range(3) for column in range(3)).encode()
    )
    return digest.hexdigest()


def _load_symmetry_map(obj, axis):
    mesh = obj.data
    name = _symmetry_attribute_name(axis)
    attribute = mesh.attributes.get(name)
    fingerprint = _topology_fingerprint(obj, axis)
    if (
        attribute is None
        or attribute.domain != "POINT"
        or attribute.data_type != "INT"
        or mesh.get(f"bbrush_symmetry_fingerprint_{axis.lower()}") != fingerprint
    ):
        return None
    values = array("i", [-1]) * len(mesh.vertices)
    attribute.data.foreach_get("value", values)
    if any(value >= len(mesh.vertices) for value in values):
        return None
    return list(values)


def _store_symmetry_map(obj, axis, partners):
    mesh = obj.data
    name = _symmetry_attribute_name(axis)
    attribute = mesh.attributes.get(name)
    if attribute is not None and (attribute.domain != "POINT" or attribute.data_type != "INT"):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(name, "INT", "POINT")
    attribute.data.foreach_set("value", array("i", partners))
    mesh[f"bbrush_symmetry_fingerprint_{axis.lower()}"] = _topology_fingerprint(obj, axis)


def _face_alignment(face_a, face_b, partners, center, coordinates, axis_index):
    if len(face_a) != len(face_b):
        return None
    size = len(face_a)
    best = None
    for direction in (-1, 1):
        for offset in range(size):
            mapped = [face_b[(offset + direction * index) % size] for index in range(size)]
            valid = True
            known = 0
            score = 0.0
            for vertex_a, vertex_b in zip(face_a, mapped):
                if partners[vertex_a] not in (-1, vertex_b) or partners[vertex_b] not in (-1, vertex_a):
                    valid = False
                    break
                if center[vertex_a] != center[vertex_b]:
                    valid = False
                    break
                if center[vertex_a] and vertex_a != vertex_b:
                    valid = False
                    break
                if partners[vertex_a] == vertex_b:
                    known += 1
                mirrored = coordinates[vertex_a].copy()
                mirrored[axis_index] = -mirrored[axis_index]
                score += (mirrored - coordinates[vertex_b]).length_squared
            if valid and known and (best is None or score < best[0]):
                best = (score, mapped)
    return None if best is None else best[1]


def _topological_symmetry_map(obj, axis, threshold):
    mesh = obj.data
    axis_index = "XYZ".index(axis)
    matrix = obj.matrix_world
    pivot = matrix.translation
    coordinates = [(matrix @ vertex.co) - pivot for vertex in mesh.vertices]
    center = [abs(co[axis_index]) <= threshold for co in coordinates]
    partners = [-1] * len(mesh.vertices)
    for index, is_center in enumerate(center):
        if is_center:
            partners[index] = index

    faces = [tuple(polygon.vertices) for polygon in mesh.polygons]
    edge_to_faces = defaultdict(list)
    for face_index, vertices in enumerate(faces):
        for index, vertex in enumerate(vertices):
            edge_to_faces[tuple(sorted((vertex, vertices[(index + 1) % len(vertices)])))].append(face_index)
    face_partner = [-1] * len(faces)
    queue = deque()

    def assign_face_pair(face_a_index, face_b_index):
        if face_a_index == face_b_index:
            return False
        if (
            face_partner[face_a_index] == face_b_index
            and face_partner[face_b_index] == face_a_index
        ):
            return False
        if face_partner[face_a_index] not in (-1, face_b_index):
            return False
        if face_partner[face_b_index] not in (-1, face_a_index):
            return False
        mapped = _face_alignment(
            faces[face_a_index],
            faces[face_b_index],
            partners,
            center,
            coordinates,
            axis_index,
        )
        if mapped is None:
            return False
        for vertex_a, vertex_b in zip(faces[face_a_index], mapped):
            partners[vertex_a] = vertex_b
            partners[vertex_b] = vertex_a
        face_partner[face_a_index] = face_b_index
        face_partner[face_b_index] = face_a_index
        queue.append((face_a_index, face_b_index, mapped))
        return True

    # A manifold edge on the symmetry plane has one face on either side and
    # gives an unambiguous topological seed even after large surface distortion.
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacent = edge_to_faces.get(tuple(sorted((a, b))), ())
        if center[a] and center[b] and len(adjacent) == 2:
            assign_face_pair(adjacent[0], adjacent[1])

    while queue:
        face_a_index, face_b_index, mapped = queue.popleft()
        face_a = faces[face_a_index]
        for index, vertex_a in enumerate(face_a):
            next_a = face_a[(index + 1) % len(face_a)]
            vertex_b = mapped[index]
            next_b = mapped[(index + 1) % len(mapped)]
            adjacent_a = edge_to_faces.get(tuple(sorted((vertex_a, next_a))), ())
            adjacent_b = edge_to_faces.get(tuple(sorted((vertex_b, next_b))), ())
            other_a = next((face for face in adjacent_a if face != face_a_index), None)
            other_b = next((face for face in adjacent_b if face != face_b_index), None)
            if other_a is None or other_b is None:
                continue
            if other_a == face_b_index and other_b == face_a_index:
                continue
            assign_face_pair(other_a, other_b)
    return partners


def _spatial_symmetry_map(obj, axis, threshold, existing=None):
    mesh = obj.data
    axis_index = "XYZ".index(axis)
    matrix = obj.matrix_world
    pivot = matrix.translation
    coordinates = [(matrix @ vertex.co) - pivot for vertex in mesh.vertices]
    partners = list(existing) if existing is not None else [-1] * len(mesh.vertices)
    positive = [index for index, co in enumerate(coordinates) if co[axis_index] > threshold]
    tree = KDTree(len(positive))
    for slot, vertex_index in enumerate(positive):
        tree.insert(coordinates[vertex_index], slot)
    tree.balance()
    used = set()
    for index, co in enumerate(coordinates):
        if partners[index] >= 0:
            continue
        if abs(co[axis_index]) <= threshold:
            partners[index] = index
            continue
        if co[axis_index] > 0.0 or not positive:
            continue
        mirrored = co.copy()
        mirrored[axis_index] = -mirrored[axis_index]
        _found, slot, distance = tree.find(mirrored)
        if slot is None or slot in used or distance > threshold:
            continue
        partner = positive[slot]
        if partners[partner] >= 0:
            continue
        partners[index] = partner
        partners[partner] = index
        used.add(slot)
    return partners


def _build_symmetry_map(obj, axis, threshold):
    partners = _topological_symmetry_map(obj, axis, threshold)
    unmatched = sum(value < 0 for value in partners)
    if unmatched:
        # Spatial matching fills loose shells and topology that has no center seam.
        partners = _spatial_symmetry_map(obj, axis, threshold, partners)
    _store_symmetry_map(obj, axis, partners)
    return partners


def _apply_symmetry_map(obj, axis, partners, masks, source_mode):
    axis_index = "XYZ".index(axis)
    matrix = obj.matrix_world.copy()
    inverse = matrix.inverted_safe()
    pivot = matrix.translation.copy()
    coordinates = [matrix @ vertex.co for vertex in obj.data.vertices]
    matched_pairs = 0
    centers = 0
    unmatched = 0

    def reflected(coordinate):
        result = coordinate.copy()
        result[axis_index] = (2.0 * pivot[axis_index]) - result[axis_index]
        return result

    for index, partner in enumerate(partners):
        if partner < 0:
            unmatched += 1
            continue
        if partner == index:
            centers += 1
            influence = 1.0 - max(0.0, min(1.0, masks[index]))
            coordinates[index][axis_index] += (pivot[axis_index] - coordinates[index][axis_index]) * influence
            continue
        if index > partner:
            continue
        matched_pairs += 1
        mask_a = max(0.0, min(1.0, masks[index]))
        mask_b = max(0.0, min(1.0, masks[partner]))

        source = None
        if abs(mask_a - mask_b) > 0.001:
            source = index if mask_a > mask_b else partner
        elif source_mode != "AVERAGE":
            want_positive = source_mode == "POSITIVE"
            a_positive = coordinates[index][axis_index] >= pivot[axis_index]
            source = index if a_positive == want_positive else partner

        if source is not None:
            target = partner if source == index else index
            target_mask = mask_b if target == partner else mask_a
            desired = reflected(coordinates[source])
            coordinates[target] = coordinates[target].lerp(desired, 1.0 - target_mask)
            continue

        mirrored_b = reflected(coordinates[partner])
        averaged_a = (coordinates[index] + mirrored_b) * 0.5
        averaged_b = reflected(averaged_a)
        coordinates[index] = coordinates[index].lerp(averaged_a, 1.0 - mask_a)
        coordinates[partner] = coordinates[partner].lerp(averaged_b, 1.0 - mask_b)

    for vertex, coordinate in zip(obj.data.vertices, coordinates):
        vertex.co = inverse @ coordinate
    obj.data.update()
    return matched_pairs, centers, unmatched


class BBRUSH_PG_zbrush_tools(bpy.types.PropertyGroup):
    axes: BoolVectorProperty(
        name="Axes",
        size=3,
        default=(True, False, False),
        description="Global deformation axes (ZBrush Deformation convention)",
    )
    polish_features: FloatProperty(name="Polish By Features", default=10.0, min=0.0, max=100.0)
    polish_groups: FloatProperty(name="Polish By Groups", default=10.0, min=0.0, max=100.0)
    relax: FloatProperty(name="Relax", default=10.0, min=0.0, max=100.0)
    inflate: FloatProperty(name="Inflate", default=10.0, min=-100.0, max=100.0)
    polish_features_preserve: BoolProperty(
        name="Preserve Volume",
        default=True,
        description="Closed-circle mode: maintain shape and volume while polishing features",
    )
    polish_groups_preserve: BoolProperty(
        name="Preserve Volume",
        default=True,
        description="Closed-circle mode: maintain volume while polishing Face Set borders",
    )
    relax_preserve: BoolProperty(
        name="Preserve Form",
        default=True,
        description="Open-circle mode: relax while maintaining the overall sculpted form",
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
    max_angle: FloatProperty(name="Max Angle", default=45.0, min=1.0, max=90.0, description="Degrees")
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
        header.label(text="Axes: Mirror / Smart ReSym / Inflate")
        for index, axis in enumerate("XYZ"):
            header.prop(settings, "axes", index=index, text=axis, toggle=True)

        row = deform.row(align=True)
        row.operator("sculpt.bbrush_zbrush_mirror", text="Mirror", icon="MOD_MIRROR")

        self._amount_row(
            deform,
            settings,
            "polish_features",
            "sculpt.bbrush_zbrush_polish_features",
            "polish_features_preserve",
        )
        self._amount_row(
            deform,
            settings,
            "polish_groups",
            "sculpt.bbrush_zbrush_polish_groups",
            "polish_groups_preserve",
        )
        self._amount_row(
            deform,
            settings,
            "relax",
            "sculpt.bbrush_zbrush_relax",
            "relax_preserve",
            closed_when_true=False,
        )

        row = deform.row(align=True)
        row.operator("sculpt.bbrush_zbrush_smart_resym", text="Smart ReSym", icon="MOD_MIRROR")

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
        note.label(text="PolyGroups are stored as Blender Sculpt Face Sets", icon="INFO")

    @staticmethod
    def _amount_row(
        layout,
        settings,
        prop_name,
        operator_id,
        mode_property=None,
        *,
        closed_when_true=True,
    ):
        row = layout.row(align=True)
        row.prop(settings, prop_name, slider=True)
        if mode_property is not None:
            is_closed = getattr(settings, mode_property) == closed_when_true
            icon = "RADIOBUT_ON" if is_closed else "RADIOBUT_OFF"
            row.prop(settings, mode_property, text="", toggle=True, icon=icon)
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
                reflected = matrix @ reflected
                for axis in axes:
                    reflected[axis] = (2.0 * pivot[axis]) - reflected[axis]
                reflected = inverse @ reflected
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
        return _apply_feature_polish(
            self,
            context,
            settings.polish_features,
            settings.polish_features_preserve,
            include_creases=True,
        )


class BBRUSH_OT_zbrush_polish_groups(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_polish_groups"
    bl_label = "Polish By Groups"
    bl_description = "Polish each Sculpt Face Set while preserving its borders"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        return _apply_feature_polish(
            self,
            context,
            settings.polish_groups,
            settings.polish_groups_preserve,
            include_creases=False,
        )


class BBRUSH_OT_zbrush_relax(_SculptOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_relax"
    bl_label = "Relax"
    bl_description = "Redistribute the mesh surface while retaining sculpted form"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        filter_type = "RELAX" if settings.relax_preserve else "SMOOTH"
        return _run_filter(
            self,
            context,
            filter_type,
            settings.relax,
            preserve_shape=settings.relax_preserve,
            use_axes=False,
        )


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
    bl_description = "Restore original mirror partners by topology; a masked side is kept as the source"
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

        masks = _mask_values(obj.data)
        total_pairs = total_centers = total_unmatched = 0
        with _object_mode(obj):
            for axis in axes:
                partners = _load_symmetry_map(obj, axis)
                if partners is None:
                    partners = _build_symmetry_map(obj, axis, settings.resym_threshold)
                pairs, centers, unmatched = _apply_symmetry_map(
                    obj,
                    axis,
                    partners,
                    masks,
                    settings.resym_source,
                )
                total_pairs += pairs
                total_centers += centers
                total_unmatched += unmatched
        self.report(
            {"INFO"},
            f"Smart ReSym: {total_pairs} pairs, {total_centers} center points, "
            f"{total_unmatched} unmatched",
        )
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
        values, count = _loose_part_ids(mesh)
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
            return (
                mesh.polygons[face_a].normal.angle(mesh.polygons[face_b].normal, 0.0)
                <= limit + 1.0e-6
            )

        values, count = _component_ids(len(mesh.polygons), edge_faces, normals_match)
        return values, count, None


class BBRUSH_OT_zbrush_merge_stray_groups(_GroupOperator, bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_merge_stray_groups"
    bl_label = "Merge Stray Groups"
    bl_description = "Merge isolated one-face and one-row Face Set components into their strongest neighbor"

    def calculate(self, mesh, _settings):
        values = list(_face_set_values(mesh))
        edge_faces = _edge_faces(mesh)
        face_neighbors = [set() for _ in mesh.polygons]
        for faces in edge_faces.values():
            if len(faces) == 2:
                a, b = faces
                face_neighbors[a].add(b)
                face_neighbors[b].add(a)

        merged_components = 0
        merged_faces = 0
        for _pass in range(8):
            group_area = defaultdict(float)
            for polygon, group_id in zip(mesh.polygons, values):
                group_area[group_id] += polygon.area

            visited = set()
            candidates = []
            for seed in range(len(mesh.polygons)):
                if seed in visited:
                    continue
                group_id = values[seed]
                component = []
                queue = deque((seed,))
                visited.add(seed)
                while queue:
                    face = queue.popleft()
                    component.append(face)
                    for neighbor in face_neighbors[face]:
                        if neighbor not in visited and values[neighbor] == group_id:
                            visited.add(neighbor)
                            queue.append(neighbor)

                one_row = all(
                    any(values[neighbor] != group_id for neighbor in face_neighbors[face])
                    for face in component
                )
                if len(component) == 1 or one_row:
                    candidates.append((min(component), group_id, component))

            changed = False
            for _seed, group_id, component in sorted(candidates):
                if any(values[face] != group_id for face in component):
                    continue
                component_set = set(component)
                shared_length = defaultdict(float)
                for edge in mesh.edges:
                    faces = edge_faces.get(edge.index, ())
                    if len(faces) != 2:
                        continue
                    a, b = faces
                    if (a in component_set) == (b in component_set):
                        continue
                    outside = b if a in component_set else a
                    outside_group = values[outside]
                    if outside_group != group_id:
                        vertex_a, vertex_b = edge.vertices
                        shared_length[outside_group] += (
                            mesh.vertices[vertex_a].co - mesh.vertices[vertex_b].co
                        ).length
                if not shared_length:
                    continue
                target = max(
                    shared_length,
                    key=lambda candidate: (
                        shared_length[candidate],
                        group_area[candidate],
                        -candidate,
                    ),
                )
                for face in component:
                    values[face] = target
                merged_components += 1
                merged_faces += len(component)
                changed = True
            if not changed:
                break
        count = len(set(values))
        return (
            values,
            count,
            f"Merged {merged_components} stray components ({merged_faces} faces)",
        )


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
        with _object_mode(obj):
            scores = [
                sum(original_mask[vertex] for vertex in polygon.vertices) / len(polygon.vertices)
                for polygon in mesh.polygons
            ]
            if polish > 0.0:
                edge_faces = _edge_faces(mesh)
                neighbors = [set() for _ in mesh.polygons]
                for faces in edge_faces.values():
                    if len(faces) == 2:
                        a, b = faces
                        neighbors[a].add(b)
                        neighbors[b].add(a)
                for _iteration in range(max(1, ceil(polish * 10.0))):
                    next_scores = list(scores)
                    for face, adjacent in enumerate(neighbors):
                        if adjacent:
                            average = sum(scores[neighbor] for neighbor in adjacent) / len(adjacent)
                            next_scores[face] = (scores[face] + average) * 0.5
                    scores = next_scores

            selected = [score >= 0.5 for score in scores]
            selected_count = sum(selected)
            if not selected_count:
                self.report({"WARNING"}, "The current mask does not cover any complete polygon")
                return {"CANCELLED"}

            values = list(_face_set_values(mesh))
            new_group = max(values, default=0) + 1
            for face, is_selected in enumerate(selected):
                if is_selected:
                    values[face] = new_group
            _write_face_sets(mesh, values)

            if self.clear_mask:
                attribute = mesh.attributes.get(MASK_ATTRIBUTE)
                if attribute is not None:
                    attribute.data.foreach_set("value", array("f", [0.0]) * len(mesh.vertices))
                    mesh.update()
        self.report({"INFO"}, f"Created Face Set {new_group} from {selected_count} masked faces")
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

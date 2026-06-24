import math
from collections import defaultdict, deque

import bpy


def _active_mesh_object(context):
    obj = context.sculpt_object or context.object
    if obj and obj.type == "MESH":
        return obj
    return None


def _mode_set(mode):
    try:
        if bpy.context.mode != mode:
            bpy.ops.object.mode_set(mode=mode)
        return True
    except RuntimeError:
        return False


def _mesh_update(context, obj):
    obj.data.update()
    try:
        context.view_layer.update()
    except Exception:
        pass


def _ensure_mask_attribute(mesh):
    attr = mesh.attributes.get(".sculpt_mask") or mesh.attributes.get("sculpt_mask")
    if attr is not None:
        return attr
    for name in (".sculpt_mask", "sculpt_mask"):
        try:
            return mesh.attributes.new(name, "FLOAT", "POINT")
        except RuntimeError:
            continue
    return None


def _ensure_face_set_attribute(mesh):
    attr = mesh.attributes.get(".sculpt_face_set") or mesh.attributes.get("sculpt_face_set")
    if attr is not None:
        return attr
    for name in (".sculpt_face_set", "sculpt_face_set"):
        try:
            return mesh.attributes.new(name, "INT", "FACE")
        except RuntimeError:
            continue
    return None


def _face_set_attribute(mesh):
    return mesh.attributes.get(".sculpt_face_set") or mesh.attributes.get("sculpt_face_set")


def _edge_faces(mesh):
    edge_to_faces = defaultdict(list)
    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            edge_to_faces[tuple(sorted(edge_key))].append(poly.index)
    return edge_to_faces


def _face_neighbors(mesh):
    neighbors = [set() for _ in mesh.polygons]
    for faces in _edge_faces(mesh).values():
        if len(faces) < 2:
            continue
        for face_index in faces:
            neighbors[face_index].update(other for other in faces if other != face_index)
    return neighbors


def _write_face_sets(mesh, groups):
    attr = _ensure_face_set_attribute(mesh)
    if attr is None:
        return False
    for face_index, group_id in enumerate(groups):
        attr.data[face_index].value = int(group_id)
    mesh.update()
    return True


def _connected_face_groups(mesh):
    neighbors = _face_neighbors(mesh)
    groups = [0] * len(mesh.polygons)
    group_id = 1
    for start in range(len(mesh.polygons)):
        if groups[start]:
            continue
        queue = deque([start])
        groups[start] = group_id
        while queue:
            face = queue.popleft()
            for other in neighbors[face]:
                if not groups[other]:
                    groups[other] = group_id
                    queue.append(other)
        group_id += 1
    return groups


def _normal_face_groups(mesh, max_angle_radians):
    neighbors = _face_neighbors(mesh)
    groups = [0] * len(mesh.polygons)
    group_id = 1
    cos_limit = math.cos(max_angle_radians)
    normals = [poly.normal.normalized() for poly in mesh.polygons]
    for start in range(len(mesh.polygons)):
        if groups[start]:
            continue
        queue = deque([start])
        groups[start] = group_id
        while queue:
            face = queue.popleft()
            normal = normals[face]
            for other in neighbors[face]:
                if groups[other]:
                    continue
                if normal.dot(normals[other]) >= cos_limit:
                    groups[other] = group_id
                    queue.append(other)
        group_id += 1
    return groups


def _uv_face_groups(mesh):
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return _connected_face_groups(mesh)

    uv_edge_to_faces = defaultdict(list)
    for poly in mesh.polygons:
        loops = list(poly.loop_indices)
        count = len(loops)
        for i, loop_index in enumerate(loops):
            next_loop = loops[(i + 1) % count]
            uv1 = tuple(round(v, 5) for v in uv_layer.data[loop_index].uv)
            uv2 = tuple(round(v, 5) for v in uv_layer.data[next_loop].uv)
            uv_edge_to_faces[tuple(sorted((uv1, uv2)))].append(poly.index)

    neighbors = [set() for _ in mesh.polygons]
    for faces in uv_edge_to_faces.values():
        if len(faces) < 2:
            continue
        for face_index in faces:
            neighbors[face_index].update(other for other in faces if other != face_index)

    groups = [0] * len(mesh.polygons)
    group_id = 1
    for start in range(len(mesh.polygons)):
        if groups[start]:
            continue
        queue = deque([start])
        groups[start] = group_id
        while queue:
            face = queue.popleft()
            for other in neighbors[face]:
                if not groups[other]:
                    groups[other] = group_id
                    queue.append(other)
        group_id += 1
    return groups


def _set_mask_from_vertices(mesh, vertex_indices):
    attr = _ensure_mask_attribute(mesh)
    if attr is None:
        return False
    selected = set(vertex_indices)
    for vertex in mesh.vertices:
        attr.data[vertex.index].value = 1.0 if vertex.index in selected else 0.0
    mesh.update()
    return True


def _border_vertices(mesh):
    verts = set()
    for edge_key, faces in _edge_faces(mesh).items():
        if len(faces) == 1:
            verts.update(edge_key)
    return verts


def _face_set_border_vertices(mesh):
    attr = _face_set_attribute(mesh)
    if attr is None:
        return set()
    face_sets = [int(item.value) for item in attr.data]
    verts = set()
    for edge_key, faces in _edge_faces(mesh).items():
        if len(faces) == 1:
            verts.update(edge_key)
        elif len({face_sets[index] for index in faces}) > 1:
            verts.update(edge_key)
    return verts


def _crease_vertices(mesh):
    verts = set()
    attr = (
        mesh.attributes.get("crease_edge")
        or mesh.attributes.get("edge_creases")
        or mesh.attributes.get(".edge_crease")
    )
    if attr is not None:
        for edge in mesh.edges:
            try:
                if float(attr.data[edge.index].value) > 1e-6:
                    verts.update(edge.vertices)
            except Exception:
                pass
    for edge in mesh.edges:
        if getattr(edge, "use_edge_sharp", False):
            verts.update(edge.vertices)
    return verts


class BbrushZBrushPopupProperties(bpy.types.PropertyGroup):
    max_angle: bpy.props.FloatProperty(
        name="MaxAngle",
        default=math.radians(35.0),
        min=math.radians(1.0),
        max=math.radians(180.0),
        subtype="ANGLE",
        unit="ROTATION",
    )
    polish_strength: bpy.props.FloatProperty(name="Polish", default=0.25, min=0.0, max=1.0)
    relax_strength: bpy.props.FloatProperty(name="Relax", default=0.25, min=0.0, max=1.0)
    inflate_strength: bpy.props.FloatProperty(name="Inflate", default=0.25, min=-1.0, max=1.0)


class BbrushZBrushPopup(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zbrush_popup"
    bl_label = "BBrush ZBrush Tools"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _active_mesh_object(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=230)

    def draw(self, context):
        layout = self.layout
        props = context.scene.bbrush_zbrush_popup

        box = layout.box()
        box.label(text="Deform")
        row = box.row(align=True)
        row.operator("sculpt.bbrush_zb_mirror", text="Mirror")
        row.operator("sculpt.bbrush_zb_smart_resym", text="Smart ReSym")
        box.operator("sculpt.bbrush_zb_mesh_filter", text="Polish By Features").filter_type = "SHARPEN"
        box.operator("sculpt.bbrush_zb_mesh_filter", text="Polish By Groups").filter_type = "RELAX_FACE_SETS"
        row = box.row(align=True)
        op = row.operator("sculpt.bbrush_zb_mesh_filter", text="Relax")
        op.filter_type = "RELAX"
        op.strength_property = "relax_strength"
        op = row.operator("sculpt.bbrush_zb_mesh_filter", text="Inflate")
        op.filter_type = "INFLATE"
        op.strength_property = "inflate_strength"
        box.prop(props, "polish_strength", slider=True)
        box.prop(props, "relax_strength", slider=True)
        box.prop(props, "inflate_strength", slider=True)

        box = layout.box()
        box.label(text="PolyGroup")
        row = box.row(align=True)
        row.operator("sculpt.bbrush_zb_face_sets_auto", text="Auto Groups").mode = "CONNECTED"
        row.operator("sculpt.bbrush_zb_face_sets_auto", text="UV Groups").mode = "UV"
        box.operator("sculpt.bbrush_zb_face_sets_auto", text="Auto Groups With UV").mode = "UV"
        box.operator("sculpt.bbrush_zb_merge_stray_groups", text="Merge Stray Groups")
        row = box.row(align=True)
        row.operator("sculpt.bbrush_zb_face_sets_auto", text="Groups By Normals").mode = "NORMALS"
        row.prop(props, "max_angle", text="")
        row = box.row(align=True)
        row.operator("sculpt.bbrush_face_sets_create_zbrush", text="Group Masked")
        row.operator("sculpt.bbrush_face_sets_create_zbrush", text="Group Masked Clear Mask")

        box = layout.box()
        box.label(text="MaskByFeature")
        row = box.row(align=True)
        row.operator("sculpt.bbrush_zb_mask_by_feature", text="Border").feature = "BORDER"
        row.operator("sculpt.bbrush_zb_mask_by_feature", text="Groups").feature = "GROUPS"
        row.operator("sculpt.bbrush_zb_mask_by_feature", text="Crease").feature = "CREASE"


class BbrushZbMeshFilter(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zb_mesh_filter"
    bl_label = "BBrush Mesh Filter"
    bl_options = {"REGISTER", "UNDO"}

    filter_type: bpy.props.EnumProperty(
        items=[
            ("SHARPEN", "Sharpen", ""),
            ("RELAX", "Relax", ""),
            ("RELAX_FACE_SETS", "Relax Face Sets", ""),
            ("INFLATE", "Inflate", ""),
        ],
        default="RELAX",
    )
    strength_property: bpy.props.StringProperty(default="polish_strength")

    @classmethod
    def poll(cls, context):
        return context.mode == "SCULPT" and _active_mesh_object(context) is not None

    def execute(self, context):
        props = context.scene.bbrush_zbrush_popup
        strength = getattr(props, self.strength_property, props.polish_strength)
        try:
            return bpy.ops.sculpt.mesh_filter(
                "EXEC_DEFAULT",
                type=self.filter_type,
                strength=strength,
                iteration_count=1,
            )
        except RuntimeError:
            return {"CANCELLED"}


class BbrushZbMirror(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zb_mirror"
    bl_label = "Mirror"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _active_mesh_object(context)
        if obj is None:
            return {"CANCELLED"}
        for mod in obj.modifiers:
            if mod.type == "MIRROR":
                mod.show_viewport = not mod.show_viewport
                return {"FINISHED"}
        old_mode = context.mode
        _mode_set("OBJECT")
        mod = obj.modifiers.new("BBrush Mirror", "MIRROR")
        mod.use_clip = True
        mod.use_bisect_axis[0] = True
        try:
            bpy.ops.object.mode_set(mode=old_mode)
        except RuntimeError:
            pass
        return {"FINISHED"}


class BbrushZbSmartResym(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zb_smart_resym"
    bl_label = "Smart ReSym"
    bl_options = {"REGISTER", "UNDO"}

    direction: bpy.props.EnumProperty(
        items=[
            ("NEGATIVE_X", "-X to +X", ""),
            ("POSITIVE_X", "+X to -X", ""),
        ],
        default="NEGATIVE_X",
    )

    def execute(self, context):
        obj = _active_mesh_object(context)
        if obj is None:
            return {"CANCELLED"}
        old_mode = context.mode
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.symmetrize(direction=self.direction, threshold=0.001)
        except RuntimeError:
            return {"CANCELLED"}
        finally:
            try:
                bpy.ops.object.mode_set(mode=old_mode)
            except RuntimeError:
                pass
        return {"FINISHED"}


class BbrushZbFaceSetsAuto(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zb_face_sets_auto"
    bl_label = "Create Face Sets"
    bl_options = {"REGISTER", "UNDO"}

    mode: bpy.props.EnumProperty(
        items=[
            ("CONNECTED", "Connected", ""),
            ("UV", "UV Islands", ""),
            ("NORMALS", "Normals", ""),
        ],
        default="CONNECTED",
    )

    def execute(self, context):
        obj = _active_mesh_object(context)
        if obj is None:
            return {"CANCELLED"}
        mesh = obj.data
        if self.mode == "UV":
            groups = _uv_face_groups(mesh)
        elif self.mode == "NORMALS":
            groups = _normal_face_groups(mesh, context.scene.bbrush_zbrush_popup.max_angle)
        else:
            groups = _connected_face_groups(mesh)
        if not _write_face_sets(mesh, groups):
            return {"CANCELLED"}
        _mesh_update(context, obj)
        return {"FINISHED"}


class BbrushZbMergeStrayGroups(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zb_merge_stray_groups"
    bl_label = "Merge Stray Groups"
    bl_options = {"REGISTER", "UNDO"}

    max_faces: bpy.props.IntProperty(name="Max Faces", default=8, min=1, max=1000)

    def execute(self, context):
        obj = _active_mesh_object(context)
        if obj is None:
            return {"CANCELLED"}
        mesh = obj.data
        attr = _face_set_attribute(mesh)
        if attr is None:
            return {"CANCELLED"}
        values = [int(item.value) for item in attr.data]
        counts = defaultdict(int)
        for value in values:
            counts[value] += 1
        neighbors = _face_neighbors(mesh)
        changed = False
        for face_index, value in enumerate(list(values)):
            if counts[value] > self.max_faces:
                continue
            neighbor_values = [values[n] for n in neighbors[face_index] if counts[values[n]] > counts[value]]
            if not neighbor_values:
                continue
            replacement = max(set(neighbor_values), key=neighbor_values.count)
            attr.data[face_index].value = replacement
            changed = True
        if changed:
            _mesh_update(context, obj)
            return {"FINISHED"}
        return {"CANCELLED"}


class BbrushZbMaskByFeature(bpy.types.Operator):
    bl_idname = "sculpt.bbrush_zb_mask_by_feature"
    bl_label = "Mask By Feature"
    bl_options = {"REGISTER", "UNDO"}

    feature: bpy.props.EnumProperty(
        items=[
            ("BORDER", "Border", ""),
            ("GROUPS", "Groups", ""),
            ("CREASE", "Crease", ""),
        ],
        default="BORDER",
    )

    def execute(self, context):
        obj = _active_mesh_object(context)
        if obj is None:
            return {"CANCELLED"}
        mesh = obj.data
        if self.feature == "GROUPS":
            verts = _face_set_border_vertices(mesh)
        elif self.feature == "CREASE":
            verts = _crease_vertices(mesh)
        else:
            verts = _border_vertices(mesh)
        if not _set_mask_from_vertices(mesh, verts):
            return {"CANCELLED"}
        _mesh_update(context, obj)
        return {"FINISHED"}


classes = (
    BbrushZBrushPopupProperties,
    BbrushZBrushPopup,
    BbrushZbMeshFilter,
    BbrushZbMirror,
    BbrushZbSmartResym,
    BbrushZbFaceSetsAuto,
    BbrushZbMergeStrayGroups,
    BbrushZbMaskByFeature,
)

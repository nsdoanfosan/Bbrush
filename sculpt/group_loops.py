"""ZBrush-style Group Loops for Blender Sculpt Face Sets."""

from __future__ import annotations

import bmesh
import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras import view3d_utils
from mathutils.bvhtree import BVHTree

from ..utils import is_bbrush_mode


FACE_SET_ATTRIBUTE = ".sculpt_face_set"


def _poll_bbrush_sculpt(cls, context):
    obj = getattr(context, "sculpt_object", None) or context.active_object
    if obj is None or obj.type != "MESH" or obj.mode != "SCULPT":
        cls.poll_message_set("Available for a mesh in Sculpt Mode")
        return False
    if not is_bbrush_mode():
        cls.poll_message_set("Bbrush mode is not active")
        return False
    return True


def _topology_blocker(obj):
    if obj.data.shape_keys is not None:
        return "Group Loops is disabled on meshes with Shape Keys"
    if obj.data.users > 1:
        return "Make the mesh data single-user before changing topology"
    if getattr(obj, "use_dynamic_topology_sculpting", False):
        return "Disable Dyntopo before creating Group Loops"
    if any(modifier.type == "MULTIRES" for modifier in obj.modifiers):
        return "Remove or apply the Multires modifier before changing topology"
    return None


def _face_set_under_cursor(context, event, obj):
    region = getattr(context, "region", None)
    region_data = getattr(context, "region_data", None)
    area = getattr(context, "area", None)
    if (
        area is None
        or area.type != "VIEW_3D"
        or region is None
        or region.type != "WINDOW"
        or region_data is None
    ):
        return None, "Run Group Loops with the cursor over the 3D View"

    coordinate = (event.mouse_region_x, event.mouse_region_y)
    ray_origin_world = view3d_utils.region_2d_to_origin_3d(
        region, region_data, coordinate
    )
    ray_direction_world = view3d_utils.region_2d_to_vector_3d(
        region, region_data, coordinate
    )

    inverse = obj.matrix_world.inverted_safe()
    ray_origin_local = inverse @ ray_origin_world
    ray_direction_local = (inverse.to_3x3() @ ray_direction_world).normalized()
    hit, _location, _normal, face_index = obj.ray_cast(
        ray_origin_local,
        ray_direction_local,
        depsgraph=context.evaluated_depsgraph_get(),
    )
    if not hit or face_index < 0:
        return None, "Place the cursor over a visible Face Set"

    attribute = obj.data.attributes.get(FACE_SET_ATTRIBUTE)
    if (
        attribute is None
        or attribute.domain != "FACE"
        or attribute.data_type != "INT"
    ):
        return None, "The mesh has no Face Sets"
    if face_index >= len(attribute.data):
        return None, "The evaluated surface does not map to a base-mesh Face Set"

    face_set_id = abs(attribute.data[face_index].value)
    if face_set_id == 0:
        return None, "The face under the cursor has no valid Face Set"
    return face_set_id, None


def _target_boundary_edges(target_faces):
    target = set(target_faces)
    boundary_edges = []
    skipped_non_manifold = 0

    candidate_edges = {edge for face in target_faces for edge in face.edges}
    for edge in candidate_edges:
        linked_faces = edge.link_faces
        if len(linked_faces) > 2:
            skipped_non_manifold += 1
            continue

        target_count = sum(face in target for face in linked_faces)
        is_face_set_border = target_count and target_count < len(linked_faces)
        is_open_border = len(linked_faces) == 1 and target_count == 1
        if is_face_set_border or is_open_border:
            boundary_edges.append(edge)

    return boundary_edges, skipped_non_manifold


def _boundary_reference_scale(boundary_edges):
    """Return a topology-relative inset depth for the selected border."""
    depths = []
    fallback_lengths = []
    for edge in boundary_edges:
        edge_vector = edge.verts[1].co - edge.verts[0].co
        edge_length_squared = edge_vector.length_squared
        if edge_length_squared <= 1.0e-20:
            continue
        fallback_lengths.append(edge_length_squared ** 0.5)
        for face in edge.link_faces:
            center = face.calc_center_median()
            parameter = (center - edge.verts[0].co).dot(edge_vector)
            parameter = max(0.0, min(1.0, parameter / edge_length_squared))
            closest = edge.verts[0].co + edge_vector * parameter
            depth = (center - closest).length
            if depth > 1.0e-10:
                depths.append(depth)

    values = sorted(depths or (length * 0.5 for length in fallback_lengths))
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) * 0.5


def _boundary_neighbors(boundary_edges):
    neighbors = {}
    for edge in boundary_edges:
        vert_a, vert_b = edge.verts
        neighbors.setdefault(vert_a, set()).add(vert_b)
        neighbors.setdefault(vert_b, set()).add(vert_a)
    return neighbors


def _surface_projected_curve_pass(bm, neighbors, surface_tree, factor):
    """Move boundary vertices tangentially, then return them to the source surface."""
    targets = {}
    bm.normal_update()

    for vert, linked in neighbors.items():
        # End points and multi-way junctions are feature anchors. ZBrush also
        # keeps these transitions firmer than ordinary two-edge contour points.
        if not vert.is_valid or len(linked) != 2:
            continue

        average = sum((item.co for item in linked), vert.co.copy() * 0.0) / 2.0
        delta = average - vert.co
        normal = vert.normal.normalized()
        tangent_delta = delta - normal * delta.dot(normal)
        candidate = vert.co + tangent_delta * factor
        nearest = surface_tree.find_nearest(candidate)
        targets[vert] = nearest[0] if nearest is not None else candidate

    for vert, coordinate in targets.items():
        vert.co = coordinate


def _closed_boundary_components(neighbors):
    components = []
    unseen = set(neighbors)

    while unseen:
        seed = unseen.pop()
        component = {seed}
        pending = [seed]
        while pending:
            vert = pending.pop()
            for linked in neighbors[vert]:
                if linked not in unseen:
                    continue
                unseen.remove(linked)
                component.add(linked)
                pending.append(linked)

        if len(component) >= 3 and all(
            len(neighbors[vert]) == 2 for vert in component
        ):
            center = sum(
                (vert.co for vert in component),
                seed.co.copy() * 0.0,
            ) / len(component)
            radius_squared = sum(
                (vert.co - center).length_squared for vert in component
            ) / len(component)
            components.append((component, center, radius_squared ** 0.5))

    return components


def _restore_component_scale(components, surface_tree):
    """Counter Laplacian shrink while retaining each contour's overall form."""
    for component, original_center, original_radius in components:
        current_center = sum(
            (vert.co for vert in component),
            original_center.copy() * 0.0,
        ) / len(component)
        current_radius_squared = sum(
            (vert.co - current_center).length_squared for vert in component
        ) / len(component)
        current_radius = current_radius_squared ** 0.5
        if current_radius <= 1.0e-12:
            continue

        scale = original_radius / current_radius
        targets = {}
        for vert in component:
            candidate = original_center + (vert.co - current_center) * scale
            nearest = surface_tree.find_nearest(candidate)
            targets[vert] = nearest[0] if nearest is not None else candidate
        for vert, coordinate in targets.items():
            vert.co = coordinate


def _polish_boundary(
    bm, boundary_edges, surface_tree, strength, preserve_form=True
):
    """Shape-preserving contour relaxation with source-surface projection."""
    if strength <= 0.0:
        return

    neighbors = _boundary_neighbors(boundary_edges)
    closed_components = (
        _closed_boundary_components(neighbors) if preserve_form else []
    )
    iterations = max(1, round(1.0 + strength * 7.0))
    factor = 0.14 + strength * 0.24

    for _iteration in range(iterations):
        _surface_projected_curve_pass(
            bm, neighbors, surface_tree, factor
        )
        _restore_component_scale(closed_components, surface_tree)


def _polish_band(bm, new_verts, surface_tree, strength):
    """Relax the generated strip while retaining the original sculpted form."""
    if strength <= 0.0 or not new_verts:
        return

    iterations = max(1, round(1.0 + strength * 3.0))
    factor = 0.08 + strength * 0.16
    for _iteration in range(iterations):
        bmesh.ops.smooth_laplacian_vert(
            bm,
            verts=new_verts,
            lambda_factor=factor,
            lambda_border=factor,
            use_x=True,
            use_y=True,
            use_z=True,
            preserve_volume=True,
        )
        for vert in new_verts:
            if not vert.is_valid:
                continue
            nearest = surface_tree.find_nearest(vert.co)
            if nearest is not None:
                vert.co = nearest[0]


def _show_face_sets(context):
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        return
    space = area.spaces.active
    if getattr(space, "type", None) != "VIEW_3D":
        return
    space.overlay.show_sculpt_face_sets = True
    area.tag_redraw()


class BbrushGroupLoops(bpy.types.Operator):
    """Create a loop band around the Face Set under the cursor."""

    bl_idname = "sculpt.bbrush_group_loops"
    bl_label = "Bbrush Group Loops"
    bl_description = "Create ZBrush-style loops around the Face Set under the cursor"
    # Sculpt's automatic UNDO transaction does not capture a Mesh/BMesh
    # topology replacement reliably. Commit one explicit post-operation
    # snapshot instead, so Ctrl+Z and Ctrl+Shift+Z each move exactly one step.
    bl_options = {"REGISTER"}

    width: FloatProperty(
        name="Loop Width",
        description=(
            "Loop-band width relative to the polygons beside the Face Set border"
        ),
        default=0.3,
        min=0.01,
        max=0.95,
        subtype="FACTOR",
    )
    loops: IntProperty(
        name="Loops",
        description=(
            "Number of support loops added on each side of the Face Set border"
        ),
        default=2,
        min=1,
        max=16,
    )
    profile: FloatProperty(
        name="Profile",
        description="Cross-section profile of the loop band",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    polish: FloatProperty(
        name="Polish",
        description=(
            "Volume-preserving Face Set border polish before loop creation, "
            "followed by surface-projected band relaxation"
        ),
        default=0.4,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    preserve_form: BoolProperty(
        name="Preserve Form",
        description=(
            "Counter contour shrink while polishing, similar to ZBrush's "
            "volume-preserving Polish by Groups mode"
        ),
        default=True,
    )
    target_face_set: IntProperty(
        name="Face Set",
        description="Face Set selected under the cursor",
        default=0,
        min=0,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    object_name: StringProperty(
        options={"HIDDEN", "SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        return _poll_bbrush_sculpt(cls, context)

    def invoke(self, context, event):
        obj = getattr(context, "sculpt_object", None) or context.active_object
        blocker = _topology_blocker(obj)
        if blocker:
            self.report({"ERROR"}, blocker)
            return {"CANCELLED"}

        if not bpy.app.background and not bpy.ops.ed.undo_push.poll():
            self.report({"ERROR"}, "Group Loops needs an editor undo context")
            return {"CANCELLED"}

        face_set_id, error = _face_set_under_cursor(context, event, obj)
        if error:
            self.report({"WARNING"}, error)
            return {"CANCELLED"}

        self.target_face_set = face_set_id
        self.object_name = obj.name
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, _context):
        layout = self.layout
        layout.label(text=f"Target Face Set: {self.target_face_set}", icon="FACESEL")
        column = layout.column(align=True)
        column.prop(self, "width")
        column.prop(self, "loops")
        column.prop(self, "profile")
        column.prop(self, "polish")
        column.prop(self, "preserve_form")
        layout.label(text="Keeps the existing Face Set border.", icon="INFO")

    def execute(self, context):
        obj = getattr(context, "sculpt_object", None) or context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "No active sculpt mesh")
            return {"CANCELLED"}
        if self.object_name and obj.name != self.object_name:
            self.report({"ERROR"}, "The active sculpt object changed")
            return {"CANCELLED"}
        if self.target_face_set <= 0:
            self.report({"ERROR"}, "No target Face Set was selected")
            return {"CANCELLED"}

        blocker = _topology_blocker(obj)
        if blocker:
            self.report({"ERROR"}, blocker)
            return {"CANCELLED"}

        if not bpy.app.background and not bpy.ops.ed.undo_push.poll():
            self.report({"ERROR"}, "Group Loops needs an editor undo context")
            return {"CANCELLED"}

        bm = bmesh.new(use_operators=True)
        try:
            # Work on an independent BMesh and commit once at the end. Keeping
            # Sculpt Mode active avoids the split undo history caused by
            # Sculpt -> Edit -> Sculpt mode changes inside one operator.
            bm.from_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.verts.ensure_lookup_table()

            face_set_layer = bm.faces.layers.int.get(FACE_SET_ATTRIBUTE)
            if face_set_layer is None:
                raise RuntimeError("The mesh has no BMesh Face Set layer")

            target_faces = [
                face
                for face in bm.faces
                if abs(face[face_set_layer]) == self.target_face_set
            ]
            if not target_faces:
                raise RuntimeError(
                    f"Face Set {self.target_face_set} is no longer present"
                )

            boundary_edges, skipped_non_manifold = _target_boundary_edges(target_faces)
            if not boundary_edges:
                raise RuntimeError("The selected Face Set has no editable boundary")

            source_surface = (
                BVHTree.FromBMesh(bm) if self.polish > 0.0 else None
            )
            if source_surface is not None:
                _polish_boundary(
                    bm,
                    boundary_edges,
                    source_surface,
                    self.polish,
                    self.preserve_form,
                )

            reference_scale = _boundary_reference_scale(boundary_edges)
            if reference_scale <= 1.0e-10:
                raise RuntimeError("The Face Set boundary has no usable local scale")
            bevel_offset = reference_scale * self.width

            bevel_result = bmesh.ops.bevel(
                bm,
                geom=boundary_edges,
                offset=bevel_offset,
                offset_type="OFFSET",
                profile_type="SUPERELLIPSE",
                # A Face Set border must remain a real center edge. Two bevel
                # segments per requested loop keep the strip symmetric, with
                # the existing group boundary between its two equal halves.
                segments=self.loops * 2,
                profile=self.profile,
                affect="EDGES",
                clamp_overlap=True,
                material=-1,
                loop_slide=True,
                mark_seam=False,
                mark_sharp=False,
                harden_normals=True,
                face_strength_mode="NONE",
                miter_outer="PATCH",
                miter_inner="ARC",
                spread=0.0,
                vmesh_method="ADJ",
            )

            new_faces = [
                face for face in bevel_result.get("faces", []) if face.is_valid
            ]
            new_verts = [
                vert for vert in bevel_result.get("verts", []) if vert.is_valid
            ]
            if not new_faces:
                raise RuntimeError(
                    "The boundary could not produce a loop band; try a smaller Width"
                )

            if source_surface is not None:
                _polish_band(
                    bm,
                    new_verts,
                    source_surface,
                    self.polish,
                )

            bm.to_mesh(obj.data)
            obj.data.update()
            context.view_layer.update()

            # Rebuild Sculpt's PBVH after the single mesh commit. Undo is
            # recorded once immediately below, after this rebuild succeeds.
            try:
                bpy.ops.sculpt.optimize()
            except RuntimeError:
                pass

            # Push the completed topology once. With no automatic UNDO flag,
            # the previous stack item is the intact pre-operation mesh and
            # redo returns to this committed result without an extra no-op.
            if not bpy.app.background:
                bpy.ops.ed.undo_push(message="Bbrush Group Loops")

            result_message = (
                f"Created {len(new_faces)} Group Loop faces around Face Set "
                f"{self.target_face_set} from {len(boundary_edges)} boundary edges"
            )
            if skipped_non_manifold:
                result_message += (
                    f"; skipped {skipped_non_manifold} non-manifold edges"
                )
        except Exception as exc:
            self.report({"ERROR"}, f"Could not create Group Loops: {exc}")
            return {"CANCELLED"}
        finally:
            bm.free()

        _show_face_sets(context)
        self.report({"INFO"}, result_message)
        return {"FINISHED"}

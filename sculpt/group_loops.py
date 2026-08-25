"""ZBrush-style Group Loops for Blender Sculpt Face Sets."""

from __future__ import annotations

import bmesh
import bpy
from bpy.props import FloatProperty, IntProperty, StringProperty
from bpy_extras import view3d_utils

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
    bl_options = {"REGISTER", "UNDO"}

    width: FloatProperty(
        name="Width",
        description="Width of the generated loop band in scene units",
        default=0.02,
        min=0.000001,
        soft_max=0.25,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    loops: IntProperty(
        name="Loops",
        description="Number of segments across the generated boundary band",
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
        description="Relax newly generated vertices after creating the band",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
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
        layout.label(text="Creates a new Face Set for the band.", icon="INFO")

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

        error_message = None
        result_message = None
        start_mode = obj.mode

        try:
            bpy.ops.object.mode_set(mode="EDIT")
            bm = bmesh.from_edit_mesh(obj.data)
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

            new_face_set_id = max(
                (abs(face[face_set_layer]) for face in bm.faces), default=0
            ) + 1

            bevel_result = bmesh.ops.bevel(
                bm,
                geom=boundary_edges,
                offset=self.width,
                offset_type="OFFSET",
                profile_type="SUPERELLIPSE",
                segments=self.loops,
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

            if self.polish > 0.0 and new_verts:
                bmesh.ops.smooth_vert(
                    bm,
                    verts=new_verts,
                    factor=self.polish * 0.5,
                    use_axis_x=True,
                    use_axis_y=True,
                    use_axis_z=True,
                )

            for face in new_faces:
                face[face_set_layer] = new_face_set_id

            bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
            result_message = (
                f"Created {len(new_faces)} Group Loop faces as Face Set "
                f"{new_face_set_id} from {len(boundary_edges)} boundary edges"
            )
            if skipped_non_manifold:
                result_message += (
                    f"; skipped {skipped_non_manifold} non-manifold edges"
                )
        except Exception as exc:
            error_message = str(exc)
        finally:
            if start_mode == "SCULPT" and obj.mode == "EDIT":
                try:
                    bpy.ops.object.mode_set(mode="SCULPT")
                except RuntimeError as exc:
                    if error_message is None:
                        error_message = f"Could not return to Sculpt Mode: {exc}"

        if error_message is not None:
            self.report({"ERROR"}, f"Could not create Group Loops: {error_message}")
            return {"CANCELLED"}

        _show_face_sets(context)
        self.report({"INFO"}, result_message)
        return {"FINISHED"}

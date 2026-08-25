"""ZBrush-style Mask By Feature UI and sculpt operator."""

from array import array

import bpy

from .mask_features import crease_boundary_mask
from ..utils import is_bbrush_mode


MASK_ATTRIBUTE = ".sculpt_mask"
CREASE_ATTRIBUTE = "crease_edge"


def _mask_values(mesh):
    attribute = mesh.attributes.get(MASK_ATTRIBUTE)
    if attribute is None:
        return None
    if attribute.domain != "POINT" or attribute.data_type != "FLOAT":
        raise RuntimeError(f"{MASK_ATTRIBUTE} must be a FLOAT point attribute")
    values = array("f", [0.0]) * len(attribute.data)
    if values:
        attribute.data.foreach_get("value", values)
    return values


def _write_mask_values(obj, values):
    mesh = obj.data
    if len(values) != len(mesh.vertices):
        raise RuntimeError("mask vertex count no longer matches the sculpt mesh")

    attribute = mesh.attributes.get(MASK_ATTRIBUTE)
    if attribute is None:
        attribute = mesh.attributes.new(
            name=MASK_ATTRIBUTE,
            type="FLOAT",
            domain="POINT",
        )
    elif attribute.domain != "POINT" or attribute.data_type != "FLOAT":
        raise RuntimeError(f"{MASK_ATTRIBUTE} must be a FLOAT point attribute")

    if values:
        attribute.data.foreach_set("value", values)
    mesh.update()
    mesh.update_gpu_tag()
    obj.update_tag(refresh={"DATA"})


def _restore_mask(obj, original_values):
    mesh = obj.data
    if original_values is None:
        attribute = mesh.attributes.get(MASK_ATTRIBUTE)
        if attribute is not None:
            mesh.attributes.remove(attribute)
            mesh.update()
            obj.update_tag(refresh={"DATA"})
        return
    _write_mask_values(obj, original_values)


def _native_boundary_mask(boundary_mode, propagation_steps):
    result = bpy.ops.sculpt.mask_from_boundary(
        "EXEC_DEFAULT",
        mix_mode="MIX",
        mix_factor=1.0,
        settings_source="OPERATOR",
        boundary_mode=boundary_mode,
        propagation_steps=propagation_steps,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"{boundary_mode} boundary masking was cancelled")


def _crease_mask(mesh, threshold, propagation_steps):
    crease_attribute = mesh.attributes.get(CREASE_ATTRIBUTE)
    if (
            crease_attribute is None
            or crease_attribute.domain != "EDGE"
            or crease_attribute.data_type != "FLOAT"):
        return array("f", [0.0]) * len(mesh.vertices), 0

    crease_values = array("f", [0.0]) * len(mesh.edges)
    edge_vertices = array("i", [0]) * (len(mesh.edges) * 2)
    if crease_values:
        crease_attribute.data.foreach_get("value", crease_values)
        mesh.edges.foreach_get("vertices", edge_vertices)

    crease_count = sum(value >= threshold for value in crease_values)
    values = crease_boundary_mask(
        len(mesh.vertices),
        edge_vertices,
        crease_values,
        threshold,
        propagation_steps,
    )
    return values, crease_count


def _merge_max(target, source):
    if len(target) != len(source):
        raise RuntimeError("feature masks have different vertex counts")
    for index, value in enumerate(source):
        if value > target[index]:
            target[index] = value


def _uses_multires_sculpt(obj):
    return any(
        modifier.type == "MULTIRES"
        and modifier.show_viewport
        and modifier.sculpt_levels > 0
        for modifier in obj.modifiers
    )


class BbrushMaskByFeatureSettings(bpy.types.PropertyGroup):
    use_border: bpy.props.BoolProperty(
        name="Border",
        description="Mask open mesh boundaries",
        default=True,
    )
    use_groups: bpy.props.BoolProperty(
        name="Groups",
        description="Mask boundaries between Blender Face Sets (ZBrush Polygroups)",
        default=True,
    )
    use_crease: bpy.props.BoolProperty(
        name="Crease",
        description="Mask edges whose Blender crease weight reaches the threshold",
        default=True,
    )
    propagation_steps: bpy.props.IntProperty(
        name="Width",
        description="Number of connected edge steps used for the boundary falloff",
        default=2,
        min=1,
        max=20,
    )
    crease_threshold: bpy.props.FloatProperty(
        name="Crease Threshold",
        description="Minimum edge crease weight treated as a masking boundary",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


class BbrushMaskByFeature(bpy.types.Operator):
    """Create one sculpt mask from selected topology features."""

    bl_idname = "sculpt.bbrush_mask_by_feature"
    bl_label = "Mask By Feature"
    bl_description = "Mask mesh borders, Face Set boundaries, and creased edges"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "sculpt_object", None) or context.active_object
        if obj is None or obj.type != "MESH" or obj.mode != "SCULPT":
            cls.poll_message_set("Available for a mesh in Sculpt Mode")
            return False
        if not is_bbrush_mode():
            cls.poll_message_set("Bbrush mode is not active")
            return False
        if getattr(obj, "use_dynamic_topology_sculpting", False):
            cls.poll_message_set("Disable Dynamic Topology before using Mask By Feature")
            return False
        if _uses_multires_sculpt(obj):
            cls.poll_message_set("Set Multires Sculpt Levels to 0 before using Mask By Feature")
            return False
        return True

    def execute(self, context):
        obj = getattr(context, "sculpt_object", None) or context.active_object
        mesh = obj.data
        settings = context.window_manager.bbrush_mask_by_feature
        enabled = settings.use_border or settings.use_groups or settings.use_crease
        if not enabled:
            self.report({"WARNING"}, "Enable Border, Groups, or Crease")
            return {"CANCELLED"}

        try:
            original_values = _mask_values(mesh)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        combined = array("f", [0.0]) * len(mesh.vertices)
        crease_count = 0
        try:
            for enabled_feature, boundary_mode in (
                    (settings.use_border, "MESH"),
                    (settings.use_groups, "FACE_SETS")):
                if not enabled_feature:
                    continue
                _native_boundary_mask(boundary_mode, settings.propagation_steps)
                feature_values = _mask_values(mesh)
                if feature_values is None:
                    raise RuntimeError(f"{boundary_mode} boundary did not create a sculpt mask")
                _merge_max(combined, feature_values)

            if settings.use_crease:
                crease_values, crease_count = _crease_mask(
                    mesh,
                    settings.crease_threshold,
                    settings.propagation_steps,
                )
                _merge_max(combined, crease_values)

            _write_mask_values(obj, combined)
            # Rebuild the sculpt BVH so direct crease-mask attribute writes are
            # visible immediately without changing mode or user preferences.
            bpy.ops.sculpt.optimize("EXEC_DEFAULT")
        except (RuntimeError, ValueError) as exc:
            try:
                _restore_mask(obj, original_values)
                bpy.ops.sculpt.optimize("EXEC_DEFAULT")
            except RuntimeError:
                pass
            self.report({"ERROR"}, f"Mask By Feature failed: {exc}")
            return {"CANCELLED"}

        if settings.use_crease and crease_count == 0:
            self.report({"INFO"}, "Mask created; no edges met the Crease threshold")
        else:
            self.report({"INFO"}, "Mask created from selected features")
        return {"FINISHED"}


class VIEW3D_PT_bbrush_mask_by_feature(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = "Mask By Feature"
    bl_ui_units_x = 11

    @classmethod
    def poll(cls, context):
        return context.mode == "SCULPT" and is_bbrush_mode()

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.bbrush_mask_by_feature

        row = layout.row(align=True)
        action = row.column()
        action.scale_y = 3.0
        action.operator(BbrushMaskByFeature.bl_idname, text="Mask By Feature", icon="MOD_MASK")

        features = row.column(align=True)
        features.prop(settings, "use_border", toggle=True)
        features.prop(settings, "use_groups", toggle=True)
        features.prop(settings, "use_crease", toggle=True)

        layout.separator()
        layout.prop(settings, "propagation_steps")
        crease = layout.column()
        crease.enabled = settings.use_crease
        crease.prop(settings, "crease_threshold")


def _draw_mask_menu(self, context):
    if not is_bbrush_mode():
        return
    self.layout.separator()
    self.layout.operator(BbrushMaskByFeature.bl_idname, icon="MOD_MASK")


classes = (
    BbrushMaskByFeatureSettings,
    BbrushMaskByFeature,
    VIEW3D_PT_bbrush_mask_by_feature,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bbrush_mask_by_feature = bpy.props.PointerProperty(
        type=BbrushMaskByFeatureSettings
    )
    bpy.types.VIEW3D_MT_mask.append(_draw_mask_menu)


def unregister():
    bpy.types.VIEW3D_MT_mask.remove(_draw_mask_menu)
    del bpy.types.WindowManager.bbrush_mask_by_feature
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

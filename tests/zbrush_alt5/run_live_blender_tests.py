"""Live-GUI integration tests for the Bbrush ZBrush Alt+5 operators.

Run this inside an already open Blender GUI in which Bbrush is active. The
script registers the feature branch as a standalone development module, tests
only objects it creates in a temporary collection, restores the user's active
object/mode/selection, and never saves preferences.
"""

from array import array
import importlib.util
from math import radians
from pathlib import Path
import sys

import bmesh
import bpy


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "sculpt" / "zbrush_tools.py"
MODULE_NAME = "bbrush_alt5_dev"


def load_module():
    old = sys.modules.get(MODULE_NAME)
    if old is not None:
        try:
            old.unregister()
        except Exception:
            pass
    sys.modules.pop(MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def run():
    module = load_module()
    bbrush_sculpt = next(
        (
            loaded
            for name, loaded in sys.modules.items()
            if name.endswith(".Bbrush.sculpt") and hasattr(loaded, "brush_runtime")
        ),
        None,
    )
    if bbrush_sculpt is None:
        raise RuntimeError("The Bbrush Sculpt module is not loaded")
    original_brush_runtime = bbrush_sculpt.brush_runtime
    if original_brush_runtime is None:
        # Operator polls intentionally require Bbrush runtime mode. The QA
        # sentinel exercises the public bpy.ops path without starting or
        # persisting a user runtime session.
        bbrush_sculpt.brush_runtime = object()
    original_active = bpy.context.view_layer.objects.active
    original_selected = list(bpy.context.selected_objects)
    original_mode = original_active.mode if original_active else "OBJECT"
    qa_collection = bpy.data.collections.new("__BBRUSH_ALT5_QA__")
    bpy.context.scene.collection.children.link(qa_collection)
    qa_objects = []
    checks = {}

    def make_mesh(name, vertices, faces):
        mesh = bpy.data.meshes.new(name + "_Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        qa_collection.objects.link(obj)
        qa_objects.append(obj)
        return obj

    def make_grid(name, width, height):
        vertices = [
            (float(x), float(y), 0.0)
            for y in range(height)
            for x in range(width)
        ]
        faces = []
        for y in range(height - 1):
            for x in range(width - 1):
                a = y * width + x
                faces.append((a, a + 1, a + 1 + width, a + width))
        return make_mesh(name, vertices, faces)

    def activate_sculpt(obj):
        active = bpy.context.active_object
        if active is not None and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="SCULPT")

    settings = bpy.context.window_manager.bbrush_zbrush_tools
    try:
        mirror = make_mesh(
            "__BBRUSH_QA_Mirror",
            [(1, 0, 0), (2, 0, 0), (1, 1, 0)],
            [(0, 1, 2)],
        )
        mirror.location.x = 3.0
        mirror.rotation_euler.z = radians(37.0)
        bpy.context.view_layer.update()
        mask = mirror.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        mask.data.foreach_set("value", array("f", [1.0, 0.0, 0.0]))
        before = [vertex.co.copy() for vertex in mirror.data.vertices]
        before_world = [mirror.matrix_world @ coordinate for coordinate in before]
        pivot = mirror.matrix_world.translation.copy()
        activate_sculpt(mirror)
        settings.axes = (True, False, False)
        status = bpy.ops.sculpt.bbrush_zbrush_mirror()
        bpy.ops.object.mode_set(mode="OBJECT")
        after = [vertex.co.copy() for vertex in mirror.data.vertices]
        after_world = [mirror.matrix_world @ coordinate for coordinate in after]
        expected_world = []
        for coordinate in before_world:
            reflected = coordinate.copy()
            reflected.x = 2.0 * pivot.x - reflected.x
            expected_world.append(reflected)
        checks["mirror"] = {
            "status": sorted(status),
            "masked_locked": (after_world[0] - before_world[0]).length < 1.0e-6,
            "unmasked_reflected": (
                (after_world[1] - expected_world[1]).length < 1.0e-6
                and (after_world[2] - expected_world[2]).length < 1.0e-6
            ),
        }

        sym = make_grid("__BBRUSH_QA_SmartReSym", 5, 5)
        for vertex in sym.data.vertices:
            vertex.co.x -= 2.0
            if vertex.co.x > 0.0:
                vertex.co.y += 5.0 + vertex.co.x
                vertex.co.z += 3.0
        mask = sym.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        mask_values = array(
            "f", [1.0 if vertex.co.x < -0.0001 else 0.0 for vertex in sym.data.vertices]
        )
        mask.data.foreach_set("value", mask_values)
        locked_before = {
            vertex.index: vertex.co.copy()
            for vertex in sym.data.vertices
            if mask_values[vertex.index] > 0.5
        }
        activate_sculpt(sym)
        settings.axes = (True, False, False)
        settings.resym_threshold = 0.00001
        status = bpy.ops.sculpt.bbrush_zbrush_smart_resym()
        bpy.ops.object.mode_set(mode="OBJECT")
        partners = module._load_symmetry_map(sym, "X")
        max_error = 0.0
        for index, partner in enumerate(partners):
            if partner < 0:
                continue
            a = sym.data.vertices[index].co
            b = sym.data.vertices[partner].co
            error = (
                abs(a.x)
                if index == partner
                else max(abs(a.x + b.x), abs(a.y - b.y), abs(a.z - b.z))
            )
            max_error = max(max_error, error)
        checks["smart_resym"] = {
            "status": sorted(status),
            "all_vertices_matched": all(partner >= 0 for partner in partners),
            "max_symmetry_error": max_error,
            "masked_source_locked": all(
                (sym.data.vertices[index].co - coordinate).length < 1.0e-6
                for index, coordinate in locked_before.items()
            ),
        }

        polish = make_grid("__BBRUSH_QA_Polish", 5, 5)
        face_sets = [
            1 if polygon.center.x < 2.0 else 2
            for polygon in polish.data.polygons
        ]
        module._write_face_sets(polish.data, face_sets)
        boundary_index = 2 + 2 * 5
        polish.data.vertices[boundary_index].co.x += 0.75
        polish.data.vertices[boundary_index].co.z += 0.5
        polish_before = polish.data.vertices[boundary_index].co.copy()
        activate_sculpt(polish)
        settings.polish_groups = 35.0
        settings.polish_groups_preserve = True
        status = bpy.ops.sculpt.bbrush_zbrush_polish_groups()
        bpy.ops.object.mode_set(mode="OBJECT")
        polish_after = polish.data.vertices[boundary_index].co.copy()
        checks["polish_groups"] = {
            "status": sorted(status),
            "boundary_faired": (polish_after - polish_before).length > 1.0e-5,
            "face_sets_unchanged": (
                list(module._face_set_values(polish.data)) == face_sets
            ),
        }

        auto = make_mesh(
            "__BBRUSH_QA_AutoGroups",
            [
                (0, 0, 0),
                (1, 0, 0),
                (0, 1, 0),
                (-1, 0, 0),
                (0, -1, 0),
                (3, 0, 0),
                (4, 0, 0),
                (3, 1, 0),
            ],
            [(0, 1, 2), (0, 3, 4), (5, 6, 7)],
        )
        activate_sculpt(auto)
        status = bpy.ops.sculpt.bbrush_zbrush_auto_groups()
        bpy.ops.object.mode_set(mode="OBJECT")
        values = list(module._face_set_values(auto.data))
        checks["auto_groups"] = {
            "status": sorted(status),
            "groups": values,
            "correct_shells": values[0] == values[1] != values[2],
        }

        grouped = make_grid("__BBRUSH_QA_GroupMasked", 4, 4)
        mask = grouped.data.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        mask_values = array(
            "f",
            [1.0 if (vertex.index % 4) <= 1 else 0.0 for vertex in grouped.data.vertices],
        )
        mask.data.foreach_set("value", mask_values)
        activate_sculpt(grouped)
        settings.polish_gp = 0.0
        status = bpy.ops.sculpt.bbrush_zbrush_group_masked()
        bpy.ops.object.mode_set(mode="OBJECT")
        preserved = array("f", [0.0]) * len(grouped.data.vertices)
        grouped.data.attributes[".sculpt_mask"].data.foreach_get("value", preserved)
        first_groups = list(module._face_set_values(grouped.data))
        activate_sculpt(grouped)
        clear_status = bpy.ops.sculpt.bbrush_zbrush_group_masked_clear()
        bpy.ops.object.mode_set(mode="OBJECT")
        cleared = array("f", [0.0]) * len(grouped.data.vertices)
        grouped.data.attributes[".sculpt_mask"].data.foreach_get("value", cleared)
        checks["group_masked"] = {
            "status": sorted(status),
            "clear_status": sorted(clear_status),
            "created_new_group": len(set(first_groups)) == 2,
            "mask_preserved_first": list(preserved) == list(mask_values),
            "mask_zero_after_clear": max(cleared, default=0.0) == 0.0,
        }

        relax = make_grid("__BBRUSH_QA_Relax", 6, 6)
        relax.data.vertices[2 + 2 * 6].co.x += 0.65
        relax.data.vertices[2 + 2 * 6].co.y -= 0.35
        relax.data.vertices[3 + 3 * 6].co.z += 0.4
        relax_before = [vertex.co.copy() for vertex in relax.data.vertices]
        counts_before = (len(relax.data.vertices), len(relax.data.polygons))
        activate_sculpt(relax)
        settings.relax = 20.0
        settings.relax_preserve = True
        status = bpy.ops.sculpt.bbrush_zbrush_relax()
        bpy.ops.object.mode_set(mode="OBJECT")
        relax_middle = [vertex.co.copy() for vertex in relax.data.vertices]
        activate_sculpt(relax)
        settings.relax_preserve = False
        aggressive_status = bpy.ops.sculpt.bbrush_zbrush_relax()
        bpy.ops.object.mode_set(mode="OBJECT")
        relax_after = [vertex.co.copy() for vertex in relax.data.vertices]
        checks["relax"] = {
            "status": sorted(status),
            "aggressive_status": sorted(aggressive_status),
            "preserving_mode_changed_mesh": max(
                (after - before).length
                for before, after in zip(relax_before, relax_middle)
            ) > 1.0e-6,
            "aggressive_mode_changed_mesh": max(
                (after - before).length
                for before, after in zip(relax_middle, relax_after)
            ) > 1.0e-6,
            "topology_unchanged": counts_before
            == (len(relax.data.vertices), len(relax.data.polygons)),
        }

        mesh = bpy.data.meshes.new("__BBRUSH_QA_Inflate_Mesh")
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
        bm.to_mesh(mesh)
        bm.free()
        inflate = bpy.data.objects.new("__BBRUSH_QA_Inflate", mesh)
        qa_collection.objects.link(inflate)
        qa_objects.append(inflate)
        radius_before = sum(vertex.co.length for vertex in mesh.vertices) / len(mesh.vertices)
        activate_sculpt(inflate)
        settings.axes = (True, True, True)
        settings.inflate = 10.0
        status = bpy.ops.sculpt.bbrush_zbrush_inflate()
        bpy.ops.object.mode_set(mode="OBJECT")
        radius_after = sum(vertex.co.length for vertex in mesh.vertices) / len(mesh.vertices)
        checks["inflate"] = {
            "status": sorted(status),
            "radius_before": radius_before,
            "radius_after": radius_after,
            "expanded": radius_after > radius_before,
        }
    finally:
        active = bpy.context.active_object
        if active is not None and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(qa_objects):
            mesh = obj.data if obj.type == "MESH" else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        if qa_collection.name in bpy.data.collections:
            bpy.data.collections.remove(qa_collection)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in original_selected:
            if obj.name in bpy.context.view_layer.objects:
                obj.select_set(True)
        if original_active and original_active.name in bpy.context.view_layer.objects:
            bpy.context.view_layer.objects.active = original_active
            if original_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=original_mode)
        bbrush_sculpt.brush_runtime = original_brush_runtime

    boolean_results = []
    for check in checks.values():
        for key, value in check.items():
            if key not in {
                "status",
                "clear_status",
                "aggressive_status",
                "groups",
                "radius_before",
                "radius_after",
                "max_symmetry_error",
            }:
                boolean_results.append(value)
    return {
        "checks": checks,
        "all_passed": (
            all(boolean_results)
            and checks["smart_resym"]["max_symmetry_error"] < 1.0e-5
        ),
        "restored_active": (
            bpy.context.active_object.name if bpy.context.active_object else None
        ),
        "restored_mode": (
            bpy.context.active_object.mode if bpy.context.active_object else None
        ),
        "qa_collection_exists": "__BBRUSH_ALT5_QA__" in bpy.data.collections,
        "preferences_saved": False,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))

"""Undo regression suite for every mutating ZBrush Alt+5 operator.

Run in a disposable Blender GUI process (Undo requires a live editor context):
  blender --factory-startup --no-window-focus --python tests/zbrush_alt5/run_undo_tests.py

This script never saves user preferences. Each operator must restore its exact
pre-operation mesh state with one Undo invocation.
"""

from array import array
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import traceback
from types import ModuleType

import bmesh
import bpy


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "sculpt" / "zbrush_tools.py"


def load_module():
    runtime = ModuleType("undo_qa.Bbrush.sculpt")
    runtime.brush_runtime = object()
    sys.modules[runtime.__name__] = runtime

    name = "bbrush_alt5_undo_qa"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def mesh_object(name, vertices, faces):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def grid(name, width, height):
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
    return mesh_object(name, vertices, faces)


def ico_sphere(name):
    mesh = bpy.data.meshes.new(name + "Mesh")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def activate_sculpt(obj):
    active = bpy.context.active_object
    if active is not None and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="SCULPT")


def write_mask(mesh, values):
    attribute = mesh.attributes.get(".sculpt_mask")
    if attribute is None:
        attribute = mesh.attributes.new(".sculpt_mask", "FLOAT", "POINT")
    attribute.data.foreach_set("value", array("f", values))
    mesh.update()


def write_uvs(mesh, face_uvs):
    layer = mesh.uv_layers.new(name="UVMap")
    for polygon, uvs in zip(mesh.polygons, face_uvs):
        for loop_index, uv in zip(polygon.loop_indices, uvs):
            layer.data[loop_index].uv = uv
    mesh.update()


def read_attribute(mesh, name, typecode, field="value"):
    attribute = mesh.attributes.get(name)
    if attribute is None:
        return None
    values = array(typecode, [0]) * len(attribute.data)
    attribute.data.foreach_get(field, values)
    return tuple(values)


def snapshot(obj):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    uv_values = None
    if uv_layer is not None:
        uv_values = tuple(
            (round(item.uv.x, 7), round(item.uv.y, 7))
            for item in uv_layer.data
        )
    partner_attributes = {
        name: read_attribute(mesh, name, "i")
        for name in sorted(mesh.attributes.keys())
        if name.startswith(".bbrush_symmetry_partner_")
    }
    custom_properties = {
        key: mesh[key]
        for key in sorted(mesh.keys())
        if key.startswith("bbrush_symmetry_fingerprint_")
    }
    return {
        "counts": (len(mesh.vertices), len(mesh.edges), len(mesh.polygons)),
        "coordinates": tuple(
            tuple(round(component, 7) for component in vertex.co)
            for vertex in mesh.vertices
        ),
        "polygons": tuple(tuple(polygon.vertices) for polygon in mesh.polygons),
        "face_sets": read_attribute(mesh, ".sculpt_face_set", "i"),
        "mask": read_attribute(mesh, ".sculpt_mask", "f"),
        "partners": partner_attributes,
        "custom_properties": custom_properties,
        "uvs": uv_values,
    }


def remove_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        return
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def editor_operator(operator, *args, **kwargs):
    """Run an editor-level operator with an explicit live VIEW_3D context."""
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        area = next((candidate for candidate in screen.areas if candidate.type == "VIEW_3D"), None)
        if area is None:
            continue
        region = next(
            (candidate for candidate in area.regions if candidate.type == "WINDOW"),
            None,
        )
        if region is None:
            continue
        with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
            return operator(*args, **kwargs)
    raise RuntimeError("A live VIEW_3D context is required for Blender Undo QA")


def run_case(name, setup, operation):
    return {
        "object_name": name,
        "setup": setup,
        "operation": operation,
    }


def build_cases():
    module = load_module()
    bpy.context.preferences.edit.use_global_undo = True
    settings = bpy.context.window_manager.bbrush_zbrush_tools
    results = {}

    def mirror_setup(name):
        settings.axes = (True, False, False)
        return mesh_object(name, [(1, 0, 0), (2, 0, 0), (1, 1, 0)], [(0, 1, 2)])

    results["mirror"] = run_case(
        "UndoMirror", mirror_setup, bpy.ops.sculpt.bbrush_zbrush_mirror
    )

    def polish_features_setup(name):
        settings.polish_features = 35.0
        settings.polish_features_preserve = True
        obj = grid(name, 5, 5)
        obj.data.vertices[2 + 2 * 5].co.z = 1.0
        crease = obj.data.attributes.new("crease_edge", "FLOAT", "EDGE")
        values = array("f", [0.0]) * len(obj.data.edges)
        edge_index = next(
            edge.index for edge in obj.data.edges if set(edge.vertices) == {2, 7}
        )
        values[edge_index] = 1.0
        crease.data.foreach_set("value", values)
        return obj

    results["polish_features"] = run_case(
        "UndoPolishFeatures",
        polish_features_setup,
        bpy.ops.sculpt.bbrush_zbrush_polish_features,
    )

    def polish_groups_setup(name):
        settings.polish_groups = 35.0
        settings.polish_groups_preserve = False
        obj = grid(name, 5, 5)
        obj.data.vertices[2 + 2 * 5].co.x += 0.75
        module._write_face_sets(
            obj.data,
            [1 if polygon.center.x < 2.0 else 2 for polygon in obj.data.polygons],
        )
        return obj

    results["polish_groups"] = run_case(
        "UndoPolishGroups",
        polish_groups_setup,
        bpy.ops.sculpt.bbrush_zbrush_polish_groups,
    )

    def relax_setup(name, preserving):
        settings.relax = 20.0
        settings.relax_preserve = preserving
        obj = grid(name, 6, 6)
        obj.data.vertices[2 + 2 * 6].co.x += 0.65
        obj.data.vertices[3 + 3 * 6].co.z += 0.4
        return obj

    results["relax_open_circle"] = run_case(
        "UndoRelaxOpen",
        lambda name: relax_setup(name, True),
        bpy.ops.sculpt.bbrush_zbrush_relax,
    )
    results["relax_closed_circle"] = run_case(
        "UndoRelaxClosed",
        lambda name: relax_setup(name, False),
        bpy.ops.sculpt.bbrush_zbrush_relax,
    )

    def smart_resym_setup(name):
        settings.axes = (True, False, False)
        settings.resym_threshold = 0.00001
        obj = grid(name, 5, 5)
        for vertex in obj.data.vertices:
            vertex.co.x -= 2.0
            if vertex.co.x > 0.0:
                vertex.co.y += 5.0 + vertex.co.x
                vertex.co.z += 3.0
        write_mask(
            obj.data,
            [1.0 if vertex.co.x < -0.0001 else 0.0 for vertex in obj.data.vertices],
        )
        return obj

    results["smart_resym"] = run_case(
        "UndoSmartReSym",
        smart_resym_setup,
        bpy.ops.sculpt.bbrush_zbrush_smart_resym,
    )

    def inflate_setup(name):
        settings.axes = (True, True, True)
        settings.inflate = 10.0
        return ico_sphere(name)

    results["inflate"] = run_case(
        "UndoInflate", inflate_setup, bpy.ops.sculpt.bbrush_zbrush_inflate
    )

    def auto_groups_setup(name):
        return mesh_object(
            name,
            [
                (0, 0, 0),
                (1, 0, 0),
                (0, 1, 0),
                (3, 0, 0),
                (4, 0, 0),
                (3, 1, 0),
            ],
            [(0, 1, 2), (3, 4, 5)],
        )

    results["auto_groups"] = run_case(
        "UndoAutoGroups",
        auto_groups_setup,
        bpy.ops.sculpt.bbrush_zbrush_auto_groups,
    )

    def uv_groups_setup(name):
        obj = mesh_object(
            name,
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (2, 0, 0), (3, 0, 0), (2, 1, 0)],
            [(0, 1, 2), (3, 4, 5)],
        )
        write_uvs(
            obj.data,
            (
                ((0.0, 0.0), (0.4, 0.0), (0.0, 0.4)),
                ((1.0, 0.0), (1.4, 0.0), (1.0, 0.4)),
            ),
        )
        return obj

    results["uv_groups"] = run_case(
        "UndoUVGroups", uv_groups_setup, bpy.ops.sculpt.bbrush_zbrush_uv_groups
    )

    def auto_groups_uv_setup(name):
        settings.uv_epsilon = 1.0e-6
        obj = mesh_object(
            name,
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
            [(0, 1, 2), (1, 3, 2)],
        )
        write_uvs(
            obj.data,
            (
                ((0.0, 0.0), (0.4, 0.0), (0.0, 0.4)),
                ((0.6, 0.0), (0.9, 0.4), (0.6, 0.4)),
            ),
        )
        return obj

    results["auto_groups_with_uv"] = run_case(
        "UndoAutoGroupsUV",
        auto_groups_uv_setup,
        bpy.ops.sculpt.bbrush_zbrush_auto_groups_uv,
    )

    def merge_stray_setup(name):
        obj = grid(name, 5, 4)
        module._write_face_sets(
            obj.data,
            [2 if polygon.index // 4 == 1 else 1 for polygon in obj.data.polygons],
        )
        return obj

    results["merge_stray_groups"] = run_case(
        "UndoMergeStray",
        merge_stray_setup,
        bpy.ops.sculpt.bbrush_zbrush_merge_stray_groups,
    )

    def groups_normals_setup(name):
        settings.max_angle = 45.0
        return mesh_object(
            name,
            [(0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 0), (0, 0, 1), (0, 1, 1)],
            [(0, 2, 3, 1), (0, 1, 5, 4)],
        )

    results["groups_by_normals"] = run_case(
        "UndoGroupsNormals",
        groups_normals_setup,
        bpy.ops.sculpt.bbrush_zbrush_groups_normals,
    )

    def group_masked_setup(name):
        settings.polish_gp = 0.4
        obj = grid(name, 4, 4)
        write_mask(
            obj.data,
            [1.0 if (vertex.index % 4) <= 1 else 0.0 for vertex in obj.data.vertices],
        )
        return obj

    results["group_masked"] = run_case(
        "UndoGroupMasked",
        group_masked_setup,
        bpy.ops.sculpt.bbrush_zbrush_group_masked,
    )
    results["group_masked_clear"] = run_case(
        "UndoGroupMaskedClear",
        group_masked_setup,
        bpy.ops.sculpt.bbrush_zbrush_group_masked_clear,
    )

    return results


class UndoRunner:
    """Yield to Blender's event loop between Execute and Undo."""

    def __init__(self, cases):
        self.cases = list(cases.items())
        self.index = 0
        self.phase = "SETUP_SAVE"
        self.current = None
        self.results = {}
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bbrush-alt5-undo-")
        self.fixture_path = None

    def finish_case(self, result):
        key, descriptor = self.cases[self.index]
        result.pop("before", None)
        self.results[key] = result
        remove_object(descriptor["object_name"])
        self.current = None
        self.fixture_path = None
        self.index += 1
        self.phase = "SETUP_SAVE"

    def finish_suite(self):
        failures = {
            name: result
            for name, result in self.results.items()
            if not result.get("changed") or not result.get("restored_once")
        }
        print("ALT5_UNDO_RESULTS=" + json.dumps(self.results, sort_keys=True))
        if failures:
            print("ALT5_UNDO_TESTS_FAILED=" + json.dumps(failures, sort_keys=True))
        else:
            print("ALT5_UNDO_TESTS_OK")
        self.temp_dir.cleanup()
        bpy.ops.wm.quit_blender()
        return None

    def __call__(self):
        try:
            if self.index >= len(self.cases):
                return self.finish_suite()

            key, descriptor = self.cases[self.index]
            name = descriptor["object_name"]
            if self.phase == "SETUP_SAVE":
                obj = descriptor["setup"](name)
                activate_sculpt(obj)
                bpy.context.view_layer.update()
                before = snapshot(obj)
                # Saving the disposable fixture creates an authoritative base
                # state and clears unrelated undo history. A manual undo_push
                # does not capture data-API object creation reliably.
                self.fixture_path = str(Path(self.temp_dir.name) / f"{name}.blend")
                bpy.ops.wm.save_as_mainfile(
                    filepath=self.fixture_path,
                    check_existing=False,
                    compress=False,
                )
                self.current = {"before": before}
                self.phase = "REOPEN_BASELINE"
                return 0.1

            if self.phase == "REOPEN_BASELINE":
                self.phase = "EXECUTE"
                bpy.ops.wm.open_mainfile(filepath=self.fixture_path, load_ui=False)
                return 0.2

            if self.phase == "EXECUTE":
                obj = bpy.data.objects.get(name)
                if obj is None:
                    raise RuntimeError(f"Saved Undo fixture did not reload: {name}")
                activate_sculpt(obj)
                bpy.context.view_layer.update()
                if snapshot(obj) != self.current["before"]:
                    raise RuntimeError(f"Saved Undo fixture changed on reload: {name}")
                status = editor_operator(descriptor["operation"], "EXEC_DEFAULT")
                self.current.update(
                    {
                        "operator_status": sorted(status),
                        "changed": snapshot(obj) != self.current["before"],
                    }
                )
                self.phase = "PUSH_OPERATOR_STEP"
                return 0.1

            if self.phase == "PUSH_OPERATOR_STEP":
                # bpy.ops calls originating in a Python timer do not pass
                # through Blender's UI event handler, so emulate the single
                # post-operation step that bl_options={'UNDO'} creates for a
                # real button/hotkey invocation.
                editor_operator(
                    bpy.ops.ed.undo_push,
                    message=f"Bbrush Undo QA operator: {name}",
                )
                self.phase = "UNDO_ONCE"
                return 0.1

            if self.phase == "UNDO_ONCE":
                undo_status = editor_operator(bpy.ops.ed.undo)
                restored_obj = bpy.data.objects.get(name)
                after_undo = snapshot(restored_obj) if restored_obj is not None else None
                restored_once = after_undo == self.current["before"]
                self.current.update(
                    {
                        "undo_status": sorted(undo_status),
                        "restored_once": restored_once,
                        "restored_twice": False,
                        "differing_keys_after_one_undo": (
                            [
                                key
                                for key in self.current["before"]
                                if after_undo.get(key) != self.current["before"][key]
                            ]
                            if after_undo is not None
                            else ["object_missing"]
                        ),
                        "second_undo_status": None,
                        "second_undo_error": None,
                    }
                )
                if restored_once:
                    self.finish_case(self.current)
                    return 0.1
                self.phase = "UNDO_TWICE"
                return 0.1

            try:
                second_status = editor_operator(bpy.ops.ed.undo)
                restored_obj = bpy.data.objects.get(name)
                self.current["second_undo_status"] = sorted(second_status)
                self.current["restored_twice"] = (
                    restored_obj is not None
                    and snapshot(restored_obj) == self.current["before"]
                )
            except RuntimeError as exc:
                self.current["second_undo_error"] = str(exc)
            self.finish_case(self.current)
            return 0.1
        except Exception:
            traceback.print_exc()
            self.results[key] = {"test_error": traceback.format_exc()}
            return self.finish_suite()


if bpy.app.background:
    raise RuntimeError("Undo QA requires a disposable Blender GUI process")
else:
    cases = build_cases()
    missing_undo = []
    for name, descriptor in cases.items():
        operator_class = bpy.types.Operator.bl_rna_get_subclass_py(
            descriptor["operation"].idname()
        )
        if operator_class is None or "UNDO" not in operator_class.bl_options:
            missing_undo.append(name)
    if missing_undo:
        raise AssertionError(f"Operators missing UNDO registration: {missing_undo}")
    bpy.app.timers.register(UndoRunner(cases), first_interval=0.5, persistent=True)

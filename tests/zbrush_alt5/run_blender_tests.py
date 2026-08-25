"""Pure-data regression tests for the ZBrush Alt+5 port.

Run with:
  blender --background --factory-startup --python tests/zbrush_alt5/run_blender_tests.py

The script intentionally does not save preferences and avoids background Sculpt operators.
"""

from array import array
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "sculpt" / "zbrush_tools.py"


def load_module():
    name = "bbrush_alt5_kernel_tests"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def mesh_object(name, vertices, faces):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def grid(name, width, height):
    vertices = [(float(x), float(y), 0.0) for y in range(height) for x in range(width)]
    faces = []
    for y in range(height - 1):
        for x in range(width - 1):
            a = y * width + x
            faces.append((a, a + 1, a + 1 + width, a + width))
    return mesh_object(name, vertices, faces)


def test_topological_smart_resym(module):
    obj = grid("Symmetry", 5, 5)
    for vertex in obj.data.vertices:
        vertex.co.x -= 2.0
        if vertex.co.x > 0.0:
            vertex.co.y += 4.0 + vertex.co.x
            vertex.co.z += 3.0

    partners = module._topological_symmetry_map(obj, "X", 0.00001)
    assert all(partner >= 0 for partner in partners), partners
    module._apply_symmetry_map(
        obj,
        "X",
        partners,
        array("f", [0.0]) * len(obj.data.vertices),
        "AVERAGE",
    )
    for index, partner in enumerate(partners):
        if index > partner:
            continue
        a = obj.data.vertices[index].co
        b = obj.data.vertices[partner].co
        if index == partner:
            assert abs(a.x) < 1.0e-6
        else:
            assert abs(a.x + b.x) < 1.0e-6
            assert abs(a.y - b.y) < 1.0e-6
            assert abs(a.z - b.z) < 1.0e-6

    # A more strongly masked side is the locked source, as in ZBrush SmartReSym.
    locked_before = {}
    masks = array("f", [0.0]) * len(obj.data.vertices)
    for vertex in obj.data.vertices:
        if vertex.co.x < -0.0001:
            vertex.co.z += 2.0 + vertex.co.y * 0.1
            masks[vertex.index] = 1.0
            locked_before[vertex.index] = vertex.co.copy()
    module._apply_symmetry_map(obj, "X", partners, masks, "AVERAGE")
    for index, before in locked_before.items():
        assert (obj.data.vertices[index].co - before).length < 1.0e-6
        partner = partners[index]
        a = obj.data.vertices[index].co
        b = obj.data.vertices[partner].co
        assert abs(a.x + b.x) < 1.0e-6
        assert abs(a.y - b.y) < 1.0e-6
        assert abs(a.z - b.z) < 1.0e-6


def test_feature_polish_graph(module):
    obj = grid("Polish", 5, 5)
    mesh = obj.data
    groups = [1 if polygon.center.x < 2.0 else 2 for polygon in mesh.polygons]
    neighbors, features, pinned = module._vertex_graph(mesh, groups, False)
    boundary_vertex = 2 + (2 * 5)
    assert len(features[boundary_vertex]) == 2

    coords = [vertex.co.copy() for vertex in mesh.vertices]
    coords[boundary_vertex].x += 0.75
    result = module._polish_step(
        coords,
        neighbors,
        features,
        pinned,
        array("f", [0.0]) * len(coords),
        0.5,
    )
    expected = sum(
        (coords[index] for index in features[boundary_vertex]),
        Vector((0.0, 0.0, 0.0)),
    ) / 2.0
    assert (result[boundary_vertex] - coords[boundary_vertex]).dot(
        expected - coords[boundary_vertex]
    ) > 0.0

    masked = array("f", [0.0]) * len(coords)
    masked[boundary_vertex] = 1.0
    masked_result = module._polish_step(coords, neighbors, features, pinned, masked, 0.5)
    assert (masked_result[boundary_vertex] - coords[boundary_vertex]).length == 0.0


def test_polish_features_honors_creases(module):
    obj = grid("CreaseFeature", 3, 3)
    mesh = obj.data
    crease = mesh.attributes.new("crease_edge", "FLOAT", "EDGE")
    crease_values = array("f", [0.0]) * len(mesh.edges)
    edge_index = next(
        edge.index for edge in mesh.edges if set(edge.vertices) == {1, 4}
    )
    crease_values[edge_index] = 1.0
    crease.data.foreach_set("value", crease_values)
    face_sets = [1] * len(mesh.polygons)
    _neighbors, group_features, _pinned = module._vertex_graph(
        mesh, face_sets, False
    )
    _neighbors, crease_features, _pinned = module._vertex_graph(
        mesh, face_sets, True
    )
    assert 4 not in group_features[1]
    assert 4 in crease_features[1] and 1 in crease_features[4]


def test_polish_volume_modes(module):
    mesh = bpy.data.meshes.new("PolishVolumeMesh")
    import bmesh

    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    bm.to_mesh(mesh)
    bm.free()
    neighbors, features, pinned = module._vertex_graph(
        mesh, [1] * len(mesh.polygons), False
    )
    initial = [vertex.co.copy() for vertex in mesh.vertices]
    masks = array("f", [0.0]) * len(initial)

    aggressive = [coordinate.copy() for coordinate in initial]
    preserving = [coordinate.copy() for coordinate in initial]
    for _iteration in range(5):
        aggressive = module._polish_step(
            aggressive, neighbors, features, pinned, masks, 0.25
        )
        preserving = module._polish_step(
            preserving, neighbors, features, pinned, masks, 0.25
        )
        preserving = module._polish_step(
            preserving, neighbors, features, pinned, masks, -0.25
        )

    initial_radius = sum(co.length for co in initial) / len(initial)
    aggressive_radius = sum(co.length for co in aggressive) / len(aggressive)
    preserving_radius = sum(co.length for co in preserving) / len(preserving)
    assert aggressive_radius < preserving_radius, (
        initial_radius,
        aggressive_radius,
        preserving_radius,
    )
    assert abs(initial_radius - preserving_radius) < abs(
        initial_radius - aggressive_radius
    )


def test_auto_groups_uses_loose_parts(module):
    # Faces touching at a vertex are one topological shell. A third face with
    # no shared vertex is a separate shell. Edge-manifold-only traversal would
    # incorrectly return three groups here.
    obj = mesh_object(
        "LooseParts",
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
    values, count, _ = module.BBRUSH_OT_zbrush_auto_groups.calculate(
        None, obj.data, None
    )
    assert count == 2, (values, count)
    assert values[0] == values[1] and values[0] != values[2]


def test_uv_group_contracts(module):
    obj = mesh_object(
        "UVGroups",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
        [(0, 1, 2), (1, 3, 2)],
    )
    uv_layer = obj.data.uv_layers.new(name="UVMap")
    # Both faces initially occupy the same UDIM tile, but their shared-edge UVs
    # are discontinuous. UV Groups should join them; Auto Groups With UV should
    # split them.
    face_uvs = (
        ((0.0, 0.0), (0.4, 0.0), (0.0, 0.4)),
        ((0.6, 0.0), (0.9, 0.4), (0.6, 0.4)),
    )
    for polygon, uvs in zip(obj.data.polygons, face_uvs):
        for loop_index, uv in zip(polygon.loop_indices, uvs):
            uv_layer.data[loop_index].uv = uv

    tile_values, tile_count, _ = module.BBRUSH_OT_zbrush_uv_groups.calculate(
        None, obj.data, None
    )
    assert tile_count == 1 and tile_values[0] == tile_values[1]

    continuous_values, continuous_count, _ = (
        module.BBRUSH_OT_zbrush_auto_groups_uv.calculate(
            None, obj.data, SimpleNamespace(uv_epsilon=1.0e-6)
        )
    )
    assert continuous_count == 2
    assert continuous_values[0] != continuous_values[1]

    # Moving the second face into a neighboring UDIM tile splits UV Groups too.
    for loop_index in obj.data.polygons[1].loop_indices:
        uv_layer.data[loop_index].uv.x += 1.0
    tile_values, tile_count, _ = module.BBRUSH_OT_zbrush_uv_groups.calculate(
        None, obj.data, None
    )
    assert tile_count == 2 and tile_values[0] != tile_values[1]


def test_merge_stray_one_row(module):
    obj = grid("Stray", 5, 4)
    mesh = obj.data
    values = []
    for polygon in mesh.polygons:
        row = polygon.index // 4
        values.append(2 if row == 1 else 1)
    module._write_face_sets(mesh, values)
    result, count, message = module.BBRUSH_OT_zbrush_merge_stray_groups.calculate(None, mesh, None)
    assert count == 1, (result, count, message)
    assert set(result) == {1}


def test_groups_by_normals_threshold(module):
    obj = mesh_object(
        "Normals",
        [(0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 0), (0, 0, 1), (0, 1, 1)],
        [(0, 2, 3, 1), (0, 1, 5, 4)],
    )
    split, count, _ = module.BBRUSH_OT_zbrush_groups_normals.calculate(
        None, obj.data, SimpleNamespace(max_angle=45.0)
    )
    assert count == 2 and split[0] != split[1]
    joined, count, _ = module.BBRUSH_OT_zbrush_groups_normals.calculate(
        None, obj.data, SimpleNamespace(max_angle=90.0)
    )
    assert count == 1 and joined[0] == joined[1]


def main():
    module = load_module()
    test_topological_smart_resym(module)
    test_feature_polish_graph(module)
    test_polish_features_honors_creases(module)
    test_polish_volume_modes(module)
    test_auto_groups_uses_loose_parts(module)
    test_uv_group_contracts(module)
    test_merge_stray_one_row(module)
    test_groups_by_normals_threshold(module)
    print("ALT5_KERNEL_TESTS_OK")


main()

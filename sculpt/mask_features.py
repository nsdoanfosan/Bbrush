"""Topology helpers for ZBrush-style Mask By Feature."""

from array import array


def crease_boundary_mask(
        vertex_count, edge_vertices, edge_crease_values, threshold, propagation_steps):
    """Return a point mask propagated from creased edges.

    The falloff matches Blender's native boundary auto-mask: boundary vertices
    are fully masked and every propagation step follows the mesh by one edge.
    """
    edge_count = len(edge_crease_values)
    if len(edge_vertices) != edge_count * 2:
        raise ValueError("edge vertex and crease arrays have different lengths")
    if vertex_count < 0:
        raise ValueError("vertex_count must be non-negative")
    if propagation_steps < 1:
        raise ValueError("propagation_steps must be at least one")

    values = array("f", [0.0]) * vertex_count
    if vertex_count == 0 or edge_count == 0:
        return values

    distances = array("b", [-1]) * vertex_count
    frontier = []
    for edge_index, crease in enumerate(edge_crease_values):
        if crease < threshold:
            continue
        offset = edge_index * 2
        for vertex in (edge_vertices[offset], edge_vertices[offset + 1]):
            if vertex < 0 or vertex >= vertex_count:
                raise ValueError(f"edge references invalid vertex index {vertex}")
            if distances[vertex] == -1:
                distances[vertex] = 0
                frontier.append(vertex)

    if not frontier:
        return values

    # Compact CSR adjacency avoids a Python list allocation for every vertex on
    # high-resolution sculpt meshes.
    offsets = array("I", [0]) * (vertex_count + 1)
    for edge_index in range(edge_count):
        offset = edge_index * 2
        vertex_a = edge_vertices[offset]
        vertex_b = edge_vertices[offset + 1]
        if not (0 <= vertex_a < vertex_count and 0 <= vertex_b < vertex_count):
            raise ValueError("edge references a vertex outside the mesh")
        offsets[vertex_a + 1] += 1
        offsets[vertex_b + 1] += 1

    for vertex in range(vertex_count):
        offsets[vertex + 1] += offsets[vertex]

    cursor = array("I", offsets[:-1])
    neighbors = array("I", [0]) * (edge_count * 2)
    for edge_index in range(edge_count):
        edge_offset = edge_index * 2
        vertex_a = edge_vertices[edge_offset]
        vertex_b = edge_vertices[edge_offset + 1]
        neighbors[cursor[vertex_a]] = vertex_b
        cursor[vertex_a] += 1
        neighbors[cursor[vertex_b]] = vertex_a
        cursor[vertex_b] += 1

    for distance in range(1, propagation_steps):
        next_frontier = []
        for vertex in frontier:
            for neighbor_offset in range(offsets[vertex], offsets[vertex + 1]):
                neighbor = neighbors[neighbor_offset]
                if distances[neighbor] != -1:
                    continue
                distances[neighbor] = distance
                next_frontier.append(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier

    step_count = float(propagation_steps)
    for vertex, distance in enumerate(distances):
        if distance < 0:
            continue
        falloff = 1.0 - (distance / step_count)
        values[vertex] = falloff * falloff
    return values

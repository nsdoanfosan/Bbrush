# ZBrush Alt+5 Tools: Blender Porting Specification

Status: implemented on `feature/zbrush-alt5-tools`; pure-kernel and live Blender
GUI integration tests pass on Blender 5.1.2.

Target baseline:

- Bbrush 1.7.2 local Blender 5.1 compatibility build
- Blender 5.1.2 on Windows
- Popup shortcut: `Alt+5`, active only while Bbrush Sculpt mode is active
- Canonical group data: Blender Sculpt Face Sets

## 1. Scope captured from the ZBrush palette

The supplied ZBrush popup contains 13 actions and their modifiers.

| Section | ZBrush action | Parameters visible in the palette |
| --- | --- | --- |
| Deform | Mirror | X/Y/Z axes |
| Deform | Polish By Features | amount, algorithm circle |
| Deform | Polish By Groups | amount, algorithm circle |
| Deform | Relax | amount, algorithm circle |
| Deform | Smart ReSym | X/Y/Z axes |
| Deform | Inflate | signed amount, X/Y/Z axes |
| PolyGroup | Auto Groups | none |
| PolyGroup | UV Groups | active UV map |
| PolyGroup | Auto Groups With UV | active UV map |
| PolyGroup | Merge Stray Groups | none |
| PolyGroup | Groups By Normals | Max Angle |
| PolyGroup | Group Masked | PolishGP |
| PolyGroup | Group Masked Clear Mask | PolishGP |

ZBrush Deformation axes are global axes. Masks affect actions from the
Deformation palette, including partial mask weights. This differs from many
Blender mesh operators, which default to object-local axes and ignore Sculpt
masks.

## 2. Confirmed ZBrush behavior and Blender mapping

| Action | Confirmed ZBrush behavior | Blender 5.1 starting point | Port decision |
| --- | --- | --- | --- |
| Mirror | Reflects the current tool on the enabled axes; this is a flip, not Mirror-and-Weld or a duplicated half. | Edit/Object Mirror flips geometry but does not apply Sculpt masks. | Custom mask-aware reflection around the object origin on ZBrush-compatible world axes. |
| Polish By Features | Polishes the whole surface while treating PolyGroup borders and creased edges as features. One circle mode preserves volume; the other is stronger and can shrink the mesh. | Mesh Filter has Smooth, Surface Smooth, Sharpen, masks, visibility, and axis limits. | Feature-aware two-pass filter: interior surface polish plus tangential boundary fairing. Use Face Set borders and crease edges as barriers. |
| Polish By Groups | Polishes using PolyGroup borders only. Volume-preserving and shrinking modes exist. | Face Sets are the closest PolyGroup representation; Relax Face Sets only fixes boundaries and is not the complete ZBrush operation. | Reuse the same feature-aware kernel but use only Face Set boundaries. |
| Relax | Evens the mesh while retaining sculptural form/detail. Its open-circle mode maintains form; the closed-circle mode smooths without maintaining volume. | Mesh Filter `RELAX` evens quad distribution without deforming volume; `SMOOTH` is the shrinking counterpart. | Open circle calls native `RELAX`; closed circle calls native `SMOOTH`. Both honor Sculpt masks and visibility. |
| Smart ReSym | Restores corresponding points of an originally mirrored mesh, can handle large distortion, does not require a topology-changing mirror copy, and honors a masked side as the locked source. | Blender's spatial symmetry snap is insufficient after large distortion. | Build and persist a topology partner-index map, with spatial fallback only for unmatched loose shells. The more strongly masked side is copied as the protected source. |
| Inflate | Signed outward/inward displacement; mask and axis restrictions apply. | Mesh Filter `INFLATE` moves vertices along normals and respects Sculpt masks, auto-masking, visibility, orientation, and axis restrictions. | Thin wrapper around native Mesh Filter with ZBrush-style signed amount and modal preview. |
| Auto Groups | One PolyGroup per disconnected mesh shell inside the current SubTool. | Face Sets represent the result, but background invocation of Blender's initializer is unstable in the tested build. | Custom vertex-connectivity traversal matching loose mesh shells, including non-manifold and vertex-touching topology. |
| UV Groups | One group for each unique UV region/tile. | No native Face Set operator groups all faces by UDIM tile ID. | Custom, vectorized face labeling from the active UV map's tile coordinates. Faces crossing a tile boundary are reported. |
| Auto Groups With UV | Groups by topology and UV continuity. | Initialize by UV Seams only sees seam attributes and may miss imported UV discontinuities. | Detect actual loop-UV discontinuities, not only marked seams, then find face components across UV-continuous edges. Loose parts split naturally. |
| Merge Stray Groups | Cleans isolated one-polygon groups and groups only one polygon row thick. Intended to clean automatic grouping noise. | No direct equivalent. | Deterministic label cleanup on the face-adjacency graph, with preview and a one-ring default. |
| Groups By Normals | Creates groups from surface curvature; Max Angle is the break tolerance. | Initialize Face Sets `NORMALS` exists, but its exposed threshold is normalized rather than a degree-valued Max Angle contract. | Compute dihedral breaks in degrees and group faces across edges whose angle is within the requested tolerance. |
| Group Masked | Creates a PolyGroup from the masked region. `PolishGP` smooths the new group boundary; high values can cross concavities. | Sculpt masks are point-domain floats and Face Sets are face-domain integers. | Derive face mask scores, smooth only the temporary scores for `PolishGP`, create a new Face Set, and leave the original mask unchanged. |
| Group Masked Clear Mask | Same grouping operation, then clears the mask. | Face Sets Create `MASKED` plus Mask Flood Fill value 0. | Extend the existing `BbrushFaceSetFromMask` path and clear only after successful group creation. |

## 3. Important semantic distinctions

### Mirror is not Symmetrize

`Mirror` flips the whole mesh. `Smart ReSym` pairs existing vertices and restores
symmetry. Blender's `Symmetrize` cuts one side, deletes the other, copies a
mirrored half, and merges the center; it is therefore not the default mapping
for either command.

### PolyGroups are Face Sets, not Vertex Groups

ZBrush PolyGroups are per-face colored selection regions on one continuous
mesh. Blender Face Sets have the same sculpting role. Vertex Groups are
point-domain weight collections and would lose face-boundary semantics.

The implementation must use these Blender internal attributes:

- `.sculpt_face_set`: `INT`, `FACE` domain
- `.sculpt_mask`: `FLOAT`, `POINT` domain; `0` is unmasked and `1` is fully masked
- `.hide_poly`: `BOOLEAN`, `FACE` domain for visibility when present

Face Set IDs must be positive and nonzero on output. Algorithms should compare
absolute IDs when reading older data, and must not encode visibility by changing
group IDs.

### UV Groups and Auto Groups With UV are different

The implementation contract is:

- `UV Groups`: faces are grouped by UV tile/UDIM coordinate. Separate islands in
  the same tile share a group.
- `Auto Groups With UV`: each UV-continuous topological component gets a group.
  Separate islands in the same tile remain separate.

This distinction matches Maxon's descriptions and gives both buttons a useful,
non-overlapping result.

## 4. Proposed package layout

```text
sculpt/
  zbrush_tools/
    __init__.py          # register/unregister only
    properties.py        # popup state and explicit volume/axis settings
    popup.py             # Alt+5 instanced popup panel
    deform.py            # Mirror, Polish, Relax, Smart ReSym, Inflate
    polygroups.py        # all Face Set creation and cleanup operators
    attributes.py        # validated access to mask, face set, hide, crease, UV
    adjacency.py         # shared face/edge graph utilities
    symmetry.py          # partner-map creation, validation, and application
    keymap.py            # Bbrush-runtime-only Alt+5 binding
tests/
  zbrush_alt5/
    README.md
    test_attributes.py
    test_polygroups.py
    test_deform.py
    fixtures/
docs/
  zbrush_alt5_porting_spec.md
```

The first implementation should keep this feature isolated from
`sculpt/face_sets.py`. Once behavior is verified, the existing Ctrl/Shift+W
operator can call the shared Group Masked service without changing its public
operator ID.

## 5. Operator and UI contract

Implemented public IDs:

```text
sculpt.bbrush_zbrush_tools_popup
sculpt.bbrush_zbrush_mirror
sculpt.bbrush_zbrush_polish_features
sculpt.bbrush_zbrush_polish_groups
sculpt.bbrush_zbrush_relax
sculpt.bbrush_zbrush_smart_resym
sculpt.bbrush_zbrush_inflate
sculpt.bbrush_zbrush_auto_groups
sculpt.bbrush_zbrush_uv_groups
sculpt.bbrush_zbrush_auto_groups_uv
sculpt.bbrush_zbrush_merge_stray_groups
sculpt.bbrush_zbrush_groups_normals
sculpt.bbrush_zbrush_group_masked
sculpt.bbrush_zbrush_group_masked_clear
```

UI rules:

1. `Alt+5` opens a compact instanced popup only in a mesh Sculpt context while
   Bbrush mode is active.
2. Register the shortcut in the add-on keyconfig and activate it through the
   existing Bbrush runtime toggle. Do not edit or save the user's keymap.
3. Detect an existing user `Alt+5` binding and report the conflict; never delete
   another keymap item.
4. Keep a normal menu/top-bar entry as a discoverable fallback.
5. Deformation rows expose an amount slider and explicit `Apply` action; each
   application is one Blender Undo step.
6. Use explicit tooltips such as `Preserve Volume`. Do not assume that a filled
   or hollow ZBrush circle means the same algorithm for every row.
7. All operators must report unsupported states before changing data.

## 6. Shared deformation rules

For each point, the effective influence is:

```text
influence = visible * (1 - sculpt_mask) * operator_weight
```

where `visible` is zero for hidden geometry and partial mask weights blend the
result. Axis limiting is applied to the displacement vector, not to the normal
calculation.

Every modal deformation must cache the initial coordinates once and recompute
the preview from that immutable state. It must never compound the previous
preview frame.

Topology, polygon order, UVs, materials, color attributes, and Face Set IDs must
remain unchanged for every Deform command.

### Mirror

- Default pivot plane: object origin.
- Default orientation: world, matching ZBrush Deformation global axes.
- Optional orientation: local.
- Reflect all enabled axes in one matrix operation.
- Transform all Shape Key coordinates consistently or refuse the operation with
  a clear report; never alter only the Basis key.
- Partially masked reflection can fold faces. Show a warning when mask values are
  neither all zero nor all one on a face.

### Polish kernel

Feature edges are:

- `Polish By Features`: Face Set border OR nonzero crease edge.
- `Polish By Groups`: Face Set border only.

The kernel has two passes:

1. Smooth interior vertices without crossing feature-edge adjacency.
2. Fair feature vertices along their boundary tangent. Pin endpoints, junctions,
   non-manifold feature vertices, and explicit feature corners.

The preserving mode uses a surface/HC/Taubin-style correction. The aggressive
mode uses ordinary Laplacian smoothing and may contract. Boundary vertices must
not leak from one Face Set to another.

### Relax

Use native Sculpt Mesh Filter `RELAX` first because it is C-level, mask-aware,
visibility-aware, axis-aware, and intended to redistribute quads while
preserving volume. Use `SMOOTH` only for the alternate shrinking mode after the
ZBrush circle behavior has been measured.

### Smart ReSym

Store a partner map as a point-domain integer attribute:

```text
.bbrush_symmetry_partner_x
.bbrush_symmetry_partner_y
.bbrush_symmetry_partner_z
```

Each value is the mirrored partner vertex index; center vertices point to
themselves and unmatched vertices use `-1`. Store a topology fingerprint beside
the map and invalidate it if vertex count or connectivity changes.

Application modes:

- Masked source side: copy the protected side to its partner.
- Explicit negative-to-positive or positive-to-negative: copy the chosen source.
- No locked/source side: average each pair to a symmetric result.

The operator must report matched, center, and unmatched vertex counts.

### Inflate

Call native Sculpt Mesh Filter `INFLATE` and pass the enabled deformation axes
and world orientation. Calibrate the displayed `-100..100` value against ZBrush
on a unit fixture instead of assuming Blender's strength uses the same scale.

## 7. PolyGroup algorithms

### Auto Groups

Use native Face Set initialization by loose parts. Connectivity is based on the
mesh shell, not separate Blender objects.

### UV Groups

For each polygon, inspect all active UV-loop coordinates:

```text
tile_u = floor(u)
tile_v = floor(v)
tile_id = 1001 + tile_u + 10 * tile_v
```

A face whose corners resolve to multiple tiles is invalid for this operation and
must be counted and reported. Negative UV tiles are allowed internally but
should not be labeled as standard UDIMs in the UI.

### Auto Groups With UV

Two faces may join through a shared edge only when the UV coordinates of both
edge endpoints match across the two face corners within epsilon. This detects
real UV discontinuities even when Blender's edge seam flag is absent.

Default UV epsilon: `1e-6`. Non-manifold edges are group boundaries.

### Merge Stray Groups

Operate on connected components of each Face Set ID.

Default one-ring cleanup candidates:

- a component containing one face; or
- a component where every face touches its component boundary, making it one
  face row thick.

Choose the replacement group by the greatest shared boundary length. Resolve
ties by larger neighboring surface area, then lower Face Set ID, so results are
deterministic. Iterate with a fixed pass cap and provide a dry-run count before
commit.

### Groups By Normals

Compute adjacent polygon normal angle on every manifold edge. An edge is a
boundary when:

```text
acos(clamp(dot(n_a, n_b), -1, 1)) > max_angle
```

Mesh boundary and non-manifold edges also stop connectivity. Label connected
face components after removing these adjacency links.

### Group Masked and PolishGP

The Sculpt mask has the same polarity in both applications. Start from the
point-domain mask and derive face membership using the average corner mask with
a default threshold of `0.5` until ZBrush's exact soft-mask rule is measured.

For `PolishGP > 0`:

1. Copy the original mask array.
2. Smooth the temporary mask on the vertex graph.
3. Threshold and apply a graph closing/majority pass to fair concave boundaries.
4. Create the new Face Set.
5. Restore the original mask for Group Masked, or clear it for Group Masked
   Clear Mask.

No geometry coordinates change during PolishGP; only the created group boundary
changes.

## 8. Unsupported-state policy

Before mutation, every operator checks:

- active object is a mesh and the expected mode is available;
- Bbrush mode is active for the popup and its shortcut;
- Dynamic Topology state and whether the operation is compatible;
- active Multiresolution modifier and sculpt level;
- Shape Keys for coordinate-changing operations;
- required UV map, Face Set, mask, or crease data;
- linked/library data and editability;
- non-manifold conditions that change the algorithm contract.

No operator may silently apply modifiers, remesh, change topology, or duplicate
an object. A future opt-in backup/duplicate option can be added separately.

## 9. Verification matrix

| Test | Fixture | Required assertion |
| --- | --- | --- |
| Mirror axes | asymmetric mesh, X/Y/Z and multi-axis | reflected coordinates match the chosen plane; topology and attributes unchanged |
| Mirror mask | hard and 0.5 masks | full mask is fixed; partial mask is a linear blend; warning emitted for folded faces |
| Polish feature split | hard-surface mesh with crease and Face Sets | no smoothing adjacency crosses a feature; volume-preserving mode changes volume less than aggressive mode |
| Polish boundary | deliberately jagged Face Set border | boundary curve becomes smoother without changing Face Set ownership unexpectedly |
| Relax | distorted quad grid with high-frequency displacement | edge-length variance decreases and vertex/face counts are unchanged |
| Smart ReSym | initially symmetric mesh distorted beyond spatial threshold | cached partner mode restores symmetry and preserves the locked masked side exactly |
| Inflate | sphere and open plane, positive/negative | displacement sign follows normals; masks, hidden faces, and axes are respected |
| Auto Groups | multiple loose shells plus touching cases | one Face Set per defined shell |
| UV Groups | two islands in one tile and islands in two UDIMs | same-tile islands share an ID; different tiles do not |
| Auto Groups With UV | unmarked imported UV split | actual loop-UV discontinuity creates separate groups |
| Merge Stray | one-face island, one-row strip, ambiguous neighbors | intended components merge; tie-breaking is deterministic |
| Groups By Normals | stepped and curved surfaces at boundary angles | split occurs only above Max Angle, with an epsilon test at equality |
| Group Masked | hard and soft masks | new Face Set follows the measured threshold contract |
| Group Masked Clear | nonempty mask | Face Set is created first and mask becomes exactly zero only after success |
| Undo/Cancel | every action | one Undo restores all coordinates and attributes; modal `Esc` restores preview state |

Performance gates for the Python implementation should be measured at 100k and
1M faces. Native Blender operators are preferred whenever they satisfy the data
contract.

## 10. Remaining numeric-parity limits

Maxon's public documentation specifies the functional behavior but not every
numeric kernel constant. The implementation therefore guarantees the operation
contract below, while exact vertex-for-vertex ZBrush parity remains out of scope:

1. Exact numeric scaling of Polish, Relax, and Inflate sliders.
2. Soft-mask-to-polygon membership rule used by Group Masked.
3. Exact spatial/morphological radius represented by PolishGP.
4. The full cleanup rule and iteration order of Merge Stray Groups.

The circle behavior itself is fixed from Maxon's current documentation: closed
Polish circles preserve volume, open Polish circles may contract; open Relax
maintains form, while closed Relax smooths without maintaining volume.

## 11. Local Blender 5.1.2 API evidence

The installed Blender reports these relevant operators:

- `sculpt.mesh_filter`: `SMOOTH`, `INFLATE`, `RELAX`,
  `RELAX_FACE_SETS`, `SURFACE_SMOOTH`, `SHARPEN`, and other types; strength,
  iteration count, X/Y/Z axis flags, and Local/World/View orientation.
- `sculpt.face_sets_init`: `LOOSE_PARTS`, `MATERIALS`, `NORMALS`, `UV_SEAMS`,
  `CREASES`, `BEVEL_WEIGHT`, `SHARP_EDGES`, `FACE_SET_BOUNDARIES`.
- `sculpt.face_sets_create`: `MASKED`, `VISIBLE`, `ALL`, `SELECTION`.
- `mesh.symmetry_snap`: direction, threshold, factor, and center.
- `sculpt.mask_filter`: smooth, sharpen, grow, shrink, and contrast filters.

Direct creation probes confirmed `.sculpt_face_set` as an internal `INT/FACE`
attribute and `.sculpt_mask` as an internal `FLOAT/POINT` attribute.

Headless `--factory-startup` registration tests remain safe, but direct
`sculpt.face_sets_init` execution crashed this installed Blender 5.1.2 in a
background process. Unit tests should exercise pure data kernels headlessly;
Sculpt operator integration tests must run in a real GUI context until that
Blender issue is resolved.

## 12. Verification completed

- Safe package and standalone registration under `--factory-startup`; no user
  preferences were saved.
- Pure-data regression coverage for topology Smart ReSym, masked source locking,
  feature/crease polish, volume-mode distinction, loose-part grouping, UV tile
  grouping, UV continuity, one-row stray merging, and normal-angle thresholds.
- Live Blender GUI coverage for rotated-object global Mirror with masks,
  large-distortion Smart ReSym, Polish By Groups, Auto Groups, both Group Masked
  variants, both Relax circle modes, and native Inflate.
- The live suite uses only temporary `__BBRUSH_ALT5_QA__` data and restores the
  original active object, selection, mode, and Bbrush runtime state.

## 13. Primary references

- Maxon ZBrush Tool > Deformation:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/tool/polymesh/deformation/deformation.html
- Maxon ZBrush Deformations and global axes:
  https://help.maxon.net/zbr/en-us/Content/html/user-guide/3d-modeling/modeling-basics/deformations/deformations.html
- Maxon ZBrush Polish Features:
  https://help.maxon.net/zbr/en-us/Content/html/user-guide/3d-modeling/hard-surface/polish-features/polish-features.html
- Maxon ZBrush Tool > Polygroups:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/tool/polymesh/polygroups/polygroups.html
- Maxon ZBrush Tool > Masking:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/tool/polymesh/masking/masking.html
- Maxon ZBrush Python GUI API for querying slider limits/modifiers:
  https://developers.maxon.net/docs/zbrush/py/2026_1_0/api/zbr_cmds_gui.html
- Blender 5.1 Mesh Filter:
  https://docs.blender.org/manual/en/5.1/sculpt_paint/sculpting/tools/mesh_filter.html
- Blender 5.1 Face Sets:
  https://docs.blender.org/manual/en/5.1/sculpt_paint/sculpting/editing/face_sets.html
- Blender Sculpt masks and Face Set attributes:
  https://docs.blender.org/manual/en/5.1/sculpt_paint/sculpting/introduction/visibility_masking_face_sets.html
- Blender 5.1 Snap to Symmetry:
  https://docs.blender.org/manual/en/5.1/modeling/meshes/editing/mesh/snap_symmetry.html
- Blender 5.1 Mirror:
  https://docs.blender.org/manual/en/5.1/modeling/meshes/editing/mesh/mirror.html
- Blender 5.1 Symmetrize:
  https://docs.blender.org/manual/en/5.1/modeling/meshes/editing/mesh/symmetrize.html
- Blender 5.1 Multiresolution:
  https://docs.blender.org/manual/en/5.1/modeling/modifiers/generate/multiresolution.html

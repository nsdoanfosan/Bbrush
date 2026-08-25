# ZBrush Alt+4 Brush Controls: Blender Porting Specification

Status: phase-one native bridge implemented on `feature/zbrush-alt4-tools`.

Target baseline:

- Bbrush 1.7.2 local Blender compatibility build
- Blender 5.1.2 on Windows
- Popup shortcut: `Alt+4`, active only while Bbrush Sculpt mode is active
- ZBrush PolyGroups map to Blender Sculpt Face Sets
- ZBrush editable curves map initially to Blender Paint Curves

## Scope captured from the supplied palette

The supplied popup combines controls from five ZBrush sub-palettes. These are
independent systems even though they appear in one floating palette.

| Section | Control | Confirmed ZBrush behavior | Blender 5.1 port decision |
| --- | --- | --- | --- |
| Brush | From Mesh | Captures the selected mesh as a Vector Displacement Mesh brush source. A regular flat, evenly subdivided quad plane is the expected authoring base. | Custom VDM asset pipeline required. Blender sculpt brush textures do not natively capture a three-component displacement field from a mesh. |
| Brush | To Mesh | Converts the selected mesh stored by the brush back into a new editable SubTool. | Custom reconstruction operator required. |
| Brush | Spotlight Projection | When enabled, Spotlight images feed sculpt or PolyPaint projection. When disabled, the images remain reference-only. | Custom view-projection sampler required. Blender reference images and brush textures exist, but there is no single native equivalent. |
| Sculptris Pro | Enable | Compatible strokes tessellate triangles locally and can decimate locally. Sculptris Pro works on raw polygons and is not a subdivision-level workflow. | Implemented with Blender Dynamic Topology toggle. |
| Sculptris Pro | Use Global | Chooses the global Stroke-palette Sculptris settings or settings stored on the current ZBrush brush. | Blender Dyntopo is global. The popup exposes the global path; per-brush state requires a later brush-state layer. |
| Sculptris Pro | Adaptive Size | Brush radius modulates tessellation. A smaller brush creates denser topology; a larger brush creates larger polygons. | Implemented as Dyntopo `BRUSH` detail. Off maps to `RELATIVE` detail. |
| Sculptris Pro | Combined | Mixes tessellation and decimation during a stroke. Off performs tessellation without the combined decimation pass. | Implemented as `SUBDIVIDE_COLLAPSE` versus `SUBDIVIDE`. |
| Sculptris Pro | SubDivide Size | Sets tessellation density. Lower values produce more, smaller triangles. | Implemented with `detail_percent` in Brush Detail and `detail_size` in Relative Detail. Numeric scales are not identical. |
| Sculptris Pro | UnDivide Ratio | Sets a separate decimation threshold. Higher values allow larger triangles. Smooth brushes also use it. | No independent Blender property. The combined refine method is working, but an exact second threshold needs a custom remeshing kernel. |
| Auto Masking | Mask By Polygroups | Virtually masks by PolyGroup. At 100, only the group where the stroke starts is editable. Lower values can let other groups respond with reduced influence. | Binary native path implemented with Face Set Auto-Masking. Values between 1 and 99 need a custom group-distance falloff. |
| Auto Masking | BackfaceMask | Masks vertices according to normals facing away from the view, preventing a brush from leaking through thin surfaces. | Implemented with the active brush's Front Faces Only setting. |
| Auto Masking | BackMaskInt | Sets the maximum strength of BackfaceMask. | Blender's Front Faces Only is binary. Partial influence needs a normal-weighted custom automask. |
| Auto Masking | Topological | Restricts influence by mesh connectivity so nearby but disconnected surfaces, such as opposite lips, are protected. | Implemented with Topology Auto-Masking. |
| Auto Masking | Range | Sets how far along topology ZBrush evaluates influence, relative to brush size. | Blender does not expose this geodesic-range parameter. |
| Auto Masking | Smooth | Controls smoothing/evaluation distance for the topological mask transition. | Blender does not expose the equivalent transition distance. |
| Clip Brush Modifiers | BRadius | Partially pushes clipped polygons depending on brush radius and their distance from the camera. | Requires the custom topology-preserving Clip operator. |
| Clip Brush Modifiers | PolyGroup | Creates a PolyGroup for geometry pushed by Circle or Rectangle Clip. | Will create a Blender Face Set for moved faces. |
| Stroke | LazyMouse | Pulls the applied brush point behind the cursor on a virtual string for controlled, smooth strokes. | Implemented with Stabilize Stroke. |
| Stroke | Relative | Makes LazyStep relative to brush size. | Blender spacing is inherently a percentage of brush diameter, so this behavior is always active. |
| Stroke | LazyStep | Applies the brush at discrete intervals along the lazy stroke. | Implemented with brush Spacing. The numeric scales differ. |
| Stroke | LazySmooth | Controls the strength of LazyMouse smoothing. | Implemented with Stabilize Stroke Factor. |
| Stroke | LazyRadius | Controls virtual-string length. Longer is steadier but needs more cursor travel. | Implemented with Stabilize Stroke Radius. |
| Stroke | LazySnap | Starts a new stroke from the previous stroke endpoint when the cursor begins within a detection radius. It supports rotating the model between stroke segments. | Custom persistent stroke-end state and modal input bridge required. |
| Stroke | Curve Mode | Applies the active brush along an editable curve and can update the result while the curve is edited. It is stored per brush in ZBrush. | Implemented initially with Blender Paint Curve stroke mode. Blender's edit/reapply semantics are narrower. |
| Stroke | AsLine | Constrains the lazy curve to a straight line while dragging. | Implemented with Blender Line stroke mode. |
| Stroke | CurveStep | Controls curve point spacing and resulting roundness. Lower values make denser, smoother curves. | Partially mapped to brush Spacing; exact curve sampling needs custom curve state. |
| Stroke | Curve Smoothness | Controls how aggressively ZBrush relaxes the curve while it is drawn. | No writable Paint Curve point API; custom curve state required. |
| Stroke | Snap | Snaps an editable curve to the underlying surface during manipulation and brush application. | Blender Paint Curves are screen-space. Surface projection must be implemented separately. |
| Stroke | Delete | Deletes all current editable curves. | Implemented by replacing the active Paint Curve with a new empty curve through Blender's native operator. |
| Stroke | Snapshot | Applies the current curve's sculpt/paint result and leaves the curve available for another application. | Implemented with `paintcurve.draw`. |
| Stroke | Smooth | Repeatedly relaxes the current curve. | Blender exposes no whole-Paint-Curve smooth operator. |
| Stroke | Frame Mesh | Creates editable curves from selected mesh features. | Custom projected-curve builder required. |
| Stroke | Border | Includes open mesh borders in Frame Mesh. | Will read non-manifold boundary edges. |
| Stroke | Polygroups | Includes PolyGroup boundaries in Frame Mesh. | Will read Blender Face Set boundary edges. |
| Stroke | Creased edges | Includes creased edges in Frame Mesh. | Will read Blender's edge crease attribute. |
| Stroke | Intensity | Allows brush strength to vary from the beginning to the end of a curve. Off applies constant strength. | Custom curve-progress strength sampling required. |
| Stroke | Size | Allows brush size/elevation to vary along the curve using Curve Falloff. | Custom curve-progress radius sampling required. |

## Phase-one behavior

The `Alt+4` popup is registered only in Bbrush's runtime keymap. Leaving Bbrush
mode deactivates the shortcut and restores Blender's default Sculpt keymap.

Working native controls mutate Blender's own Sculpt or active Brush properties,
so changes remain visible in Blender's standard UI:

- Dynamic Topology toggle, detail method, refine method, and detail size;
- Face Set, Topology, and front-face automasking;
- Stabilize Stroke, brush spacing, stabilization factor, and radius;
- Curve and Line stroke methods;
- Paint Curve delete and snapshot.

Controls that have no honest Blender equivalent are intentionally status
operators. Clicking one reports the missing semantic and the required custom
implementation instead of changing an unrelated Blender setting.

## Architecture

```text
sculpt/
  zbrush_alt4/
    __init__.py       registration boundary
    operators.py      native bridge operators and explicit deferred contracts
    popup.py          Alt+4 palette layout and native property bindings
```

The package is isolated so the later VDM, projection, clip, stroke, and curve
kernels can be added without coupling them to Bbrush navigation or mouse-event
code.

## Required phase-two kernels

1. VDM capture/reconstruction with a fixed tangent convention and round-trip
   fixture.
2. Spotlight screen projection for color and scalar/vector displacement.
3. Fractional PolyGroup and backface automasking with deterministic falloff.
4. Topology-preserving Clip Curve/Circle/Rectangle in view space, followed by
   optional Face Set assignment.
5. LazySnap persistent endpoint state that survives view navigation.
6. A Bbrush-owned surface curve representation for smooth, snap, frame, and
   strength/size-over-progress behavior.

## Verification contract

- Registration must succeed in Blender 5.1.2 with `default_set=False`.
- No factory-startup process may save user preferences.
- `Alt+4` is inactive before Bbrush starts and active after Bbrush starts.
- Native buttons must change the corresponding Blender RNA property exactly
  once per invocation and remain undo-safe where geometry changes.
- Popup drawing must succeed with a normal asset brush and with no active brush.
- Deferred buttons must report their status and must not mutate mesh data.
- Live QA must reload all `sculpt.zbrush_alt4` submodules before re-registering
  Bbrush, then invoke the popup in a real 3D View Sculpt context.

## Primary references

- Maxon ZBrush VDM From Mesh / To Mesh:
  https://help.maxon.net/zbp/en-us/Content/html/5_sculpting-and-painting_brush-8-brushes-menu-settings.html
- Maxon ZBrush Brush Samples / Spotlight Projection:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/brush/samples/samples.html
- Maxon ZBrush Sculptris Pro:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/stroke/sculptris-pro/sculptris-pro.html
- Maxon ZBrush Sculptris global and per-brush settings:
  https://help.maxon.net/zbr/en-us/Content/html/user-guide/3d-modeling/modeling-basics/creating-meshes/sculptris-pro/global-settings/global-settings.html
- Maxon ZBrush Auto Masking:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/brush/auto-masking/auto-masking.html
- Maxon ZBrush Clip Brushes:
  https://help.maxon.net/zbr/en-us/Content/html/user-guide/3d-modeling/hard-surface/clip-brushes/clip-brushes.html
- Maxon ZBrush Lazy Mouse and LazySnap:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/stroke/lazy-mouse/lazy-mouse.html
- Maxon ZBrush Curve mode:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/stroke/curve/curve.html
- Maxon ZBrush Curve Functions:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/stroke/curve-functions/curve-functions.html
- Maxon ZBrush Curve Modifiers:
  https://help.maxon.net/zbr/en-us/Content/html/reference-guide/stroke/curve-modifiers/curve-modifiers.html
- Blender 5.1 Dyntopo:
  https://docs.blender.org/manual/en/5.1/sculpt_paint/sculpting/tool_settings/dyntopo.html
- Blender 5.1 Auto-Masking:
  https://docs.blender.org/manual/en/5.1/sculpt_paint/sculpting/controls.html
- Blender 5.1 Stroke and Paint Curve behavior:
  https://docs.blender.org/manual/en/5.1/sculpt_paint/brush/stroke.html

# Bbrush 1.7.2 (local Blender 5.1 compatibility build)

This package is the project-owned source for PARK's installed Bbrush extension.
The Blender extension directory is connected to this folder with a Windows
directory junction so changes remain reviewable and Git-ready.

## Mask to Face Set

In Sculpt Mode, `Ctrl+W` runs **Face Set from Mask**, matching ZBrush. `Shift+W`
remains as a compatibility alias. The command creates a new face set from the
current non-empty sculpt mask and then clears that consumed mask. If no mask
exists (or the mask is entirely zero), it creates one new face set from the whole
mesh.

The other ZBrush-style Sculpt Mode shortcuts are:

- `W`: activate Blender's combined Sculpt Transform gizmo. The gizmo transforms
  only unmasked geometry and initializes at the unmasked region's center.
- `T`: hide the Transform gizmo and return to the Sculpt brush. W shows it again
  at the retained pivot.
- `Alt+Left Click`: while the Transform gizmo is active, place its Sculpt pivot
  on the clicked mesh surface.
- `S`: interactive brush strength adjustment.
- `Shift+F`: toggle Face Set/Polygroup colors.

These overrides are enabled only while Bbrush mode is active. Outside
Bbrush mode, Blender's original Sculpt shortcuts continue to work.

## Defaults

Bbrush starts automatically when Sculpt Mode is entered. The shortcut help
overlay, monkey navigation image/tips, and silhouette display are hidden by
default and can still be enabled in the add-on preferences.

## Registration QA

Validation must use `default_set=False` and must never save preferences from a
`--factory-startup` Blender process.

## Feature development

The implemented and verified contract for the ZBrush-style `Alt+5` Deform and
PolyGroup popup is in
[`docs/zbrush_alt5_porting_spec.md`](docs/zbrush_alt5_porting_spec.md). The
specification records the exact command scope, Blender 5.1 mappings, attribute
contracts, unsupported-state policy, remaining numeric-parity limits, and the
completed headless/live verification matrix.

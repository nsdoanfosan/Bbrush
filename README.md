# Bbrush 1.7.3 (local Blender 5.1 compatibility build)

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
- `Ctrl+Shift+G`: with the cursor over a Face Set, open Group Loops settings
  and create a ZBrush-style boundary band. The generated band receives its own
  new Face Set color.

These overrides are enabled only while Bbrush mode is active. Outside
Bbrush mode, Blender's original Sculpt shortcuts continue to work.

## Group Loops

Group Loops treats the Face Set under the mouse cursor as the selected ZBrush
PolyGroup. Width, loop count, profile, and optional polish are set in a small
confirmation dialog before the topology is changed. Face Set colors are shown
automatically after a successful operation.

The command requires a base mesh with at least two Face Sets. It intentionally
blocks Shape Keys, linked mesh data, Dyntopo, and Multires because those states
cannot safely accept this topology change.

## Defaults

Bbrush starts automatically when Sculpt Mode is entered. The shortcut help
overlay, monkey navigation image/tips, and silhouette display are hidden by
default and can still be enabled in the add-on preferences.

## Registration QA

Validation must use `default_set=False` and must never save preferences from a
`--factory-startup` Blender process.

"""Safe standalone and package registration smoke tests.

Run only with Blender --background --factory-startup. This script registers
with default_set=False semantics and never saves user preferences.
"""

import importlib.util
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "sculpt" / "zbrush_tools.py"


def load(name, path, package=False):
    kwargs = {"submodule_search_locations": [str(path.parent)]} if package else {}
    spec = importlib.util.spec_from_file_location(name, path, **kwargs)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tools = load("bbrush_alt5_registration_test", MODULE)
tools.register()
assert hasattr(bpy.ops.sculpt, "bbrush_zbrush_tools_popup")
assert hasattr(bpy.types.WindowManager, "bbrush_zbrush_tools")
tools.unregister()
assert not hasattr(bpy.types.WindowManager, "bbrush_zbrush_tools")
print("ALT5_STANDALONE_REGISTER_OK")

package = load("bbrush_alt5_package_test", ROOT / "__init__.py", package=True)
package.register()
assert hasattr(bpy.ops.sculpt, "bbrush_zbrush_tools_popup")
package.unregister()
print("ALT5_PACKAGE_REGISTER_OK")

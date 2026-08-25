import bpy

from .operators import CLASSES as OPERATOR_CLASSES
from .popup import BbrushAlt4Popup


CLASSES = (*OPERATOR_CLASSES, BbrushAlt4Popup)
register_classes, unregister_classes = bpy.utils.register_classes_factory(CLASSES)


def register():
    register_classes()


def unregister():
    unregister_classes()

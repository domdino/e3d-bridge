"""E3D Bridge - export Blender scenes to a folder Element 3D imports cleanly.

Registration only. All logic lives in the sibling modules.
"""

from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()

# -*- coding: utf-8 -*-
"""Ladybug Tools for Blender.

Port of the Ladybug Tools (https://www.ladybug.tools/) environmental analysis
workflows to Blender. The pure-Python ladybug libraries are bundled as wheels;
the Radiance-dependent parts (sky matrix, ray intersection) are re-implemented
natively with numpy and Blender's BVHTree.
"""

bl_info = {
    'name': 'Ladybug Tools',
    'author': 'Leandro',
    'version': (0, 5, 0),
    'blender': (4, 2, 0),
    'location': 'View3D > Sidebar > Ladybug',
    'description': 'Sun path, solar radiation and climate analysis (Ladybug Tools)',
    'category': '3D View',
}

_LADYBUG_IMPORT_ERROR = None
try:
    import ladybug  # noqa: F401
    import ladybug_geometry  # noqa: F401
    import ladybug_radiance  # noqa: F401
except Exception as exc:  # noqa: BLE001
    _LADYBUG_IMPORT_ERROR = exc

try:
    import bpy  # noqa: F401
    _HAS_BPY = True
except ImportError:  # plain Python (tests of the core modules)
    _HAS_BPY = False

if _LADYBUG_IMPORT_ERROR is None and _HAS_BPY:
    from . import props, ops, ui  # noqa: E402


def register():
    if not _HAS_BPY:
        raise ImportError('bpy is required to register the Ladybug Tools add-on')
    if _LADYBUG_IMPORT_ERROR is not None:
        raise ImportError(
            'Ladybug libraries are not available: {}. Install the extension '
            'from its .zip so the bundled wheels are set up.'.format(
                _LADYBUG_IMPORT_ERROR))
    props.register()
    ops.register()
    ui.register()


def unregister():
    if _LADYBUG_IMPORT_ERROR is not None or not _HAS_BPY:
        return
    ui.unregister()
    ops.unregister()
    props.unregister()

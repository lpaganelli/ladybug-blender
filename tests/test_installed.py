"""Smoke test for the extension installed through Blender's extension system.

    blender -b --python tests/test_installed.py -- <epw>
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
epw = argv[0] if argv else os.path.join(HERE, 'test_sao_paulo.epw')

mods = [m for m in sys.modules if m.endswith('ladybug_tools') and m.startswith('bl_ext')]
print('extension modules:', mods)
assert mods, 'extension not loaded'
import ladybug, ladybug_radiance  # noqa: E401,E402
print('ladybug from', ladybug.__file__)
assert hasattr(bpy.ops, 'ladybug') and hasattr(bpy.ops.ladybug, 'draw_sunpath')

# keep preferences (and therefore the enabled extension), just empty the scene
bpy.ops.wm.read_homefile(use_empty=True)
p = bpy.context.scene.ladybug
p.epw_path = epw
assert p.epw_loaded
assert bpy.ops.ladybug.draw_sunpath() == {'FINISHED'}
bpy.ops.mesh.primitive_plane_add(size=10)
assert bpy.ops.ladybug.incident_radiation() == {'FINISHED'}
res = bpy.data.objects['Plane · Radiation']
vals = [d.value for d in res.data.attributes['LB Radiation'].data]
print('radiation on plane:', vals)
assert 1500 < vals[0] < 2600
print('INSTALLED EXTENSION OK')

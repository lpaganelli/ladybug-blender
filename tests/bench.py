"""Benchmark ray casting throughput for annual studies.

    blender -b --factory-startup --python tests/bench.py -- <lbpy_path> <epw>
"""
import os
import sys
import time

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
if argv and argv[0] not in sys.path:
    sys.path.insert(0, argv[0])
sys.path.insert(0, ROOT)
epw = argv[1] if len(argv) > 1 else os.path.join(HERE, 'test_sao_paulo.epw')

import ladybug_tools  # noqa: E402
ladybug_tools.register()
bpy.ops.wm.read_factory_settings(use_empty=True)
p = bpy.context.scene.ladybug
p.epw_path = epw
p.st_context = 'VISIBLE'

# 40x40 ground grid + a few boxes
bpy.ops.mesh.primitive_plane_add(size=40)
ground = bpy.context.active_object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.subdivide(number_cuts=49)  # 2500 faces
bpy.ops.object.mode_set(mode='OBJECT')
for i, (x, y) in enumerate([(-8, -8), (8, 8), (-8, 8), (8, -8), (0, 0)]):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, 5))
    bpy.context.active_object.scale = (5, 5, 10)
bpy.ops.object.select_all(action='DESELECT')
bpy.context.view_layer.objects.active = ground

for label, (sm, ed) in {'one day': ('6', '6'), 'annual': ('1', '12')}.items():
    p.ap_st_month, p.ap_st_day = sm, 21 if label == 'one day' else 1
    p.ap_end_month, p.ap_end_day = ed, 21 if label == 'one day' else 31
    t0 = time.time()
    bpy.ops.ladybug.direct_sun_hours()
    print('direct sun hours [%s]: %.1fs' % (label, time.time() - t0))

t0 = time.time()
bpy.ops.ladybug.incident_radiation()
print('incident radiation [annual, 145 patches]: %.1fs' % (time.time() - t0))
p.st_high_density = True
t0 = time.time()
bpy.ops.ladybug.incident_radiation()
print('incident radiation [annual, 577 patches]: %.1fs' % (time.time() - t0))

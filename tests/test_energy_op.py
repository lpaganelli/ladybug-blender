"""The Simulate operator inside Blender (short run period via LB_ENERGY_DAYS).

    blender -b --factory-startup --python tests/test_energy_op.py -- <lbpy> <bonsai_site_packages> <ifc> <epw> <energyplus_dir>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
argv = sys.argv[sys.argv.index('--') + 1:]
for p in (argv[0], argv[1], ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
ifc, epw, ep_dir = argv[2], argv[3], argv[4]
os.environ['LB_ENERGY_DAYS'] = '7'

import bpy  # noqa: E402
import ladybug_tools  # noqa: E402
ladybug_tools.register()
bpy.ops.wm.read_factory_settings(use_empty=True)
p = bpy.context.scene.ladybug
p.epw_path = epw
p.hb_ifc_path = ifc
p.en_ep_path = ep_dir
p.en_folder = os.path.join(HERE, 'out', 'energy_op')
assert bpy.ops.ladybug.ifc_to_honeybee() == {'FINISHED'}
assert bpy.ops.ladybug.energy_simulate() == {'FINISHED'}
from ladybug_tools.ops.energy import _LAST_RUN  # noqa: E402
summary = _LAST_RUN['summary']
assert summary and all(5 < s['mean'] < 40 for s in summary.values())
rooms = [o for o in bpy.data.objects if o.get('hb_room') and 'openings' not in o.name]
assert all('hb_hours_hot' in o for o in rooms), 'rooms not colored'
assert any(o.name.startswith('HB Energy Legend') for o in bpy.data.objects)
p.en_metric = 'mean'
assert bpy.ops.ladybug.energy_color() == {'FINISHED'}
assert all('hb_mean' in o for o in rooms)
assert bpy.ops.ladybug.energy_report() == {'FINISHED'}
print('rooms:', {s['name']: round(s['mean'], 1) for s in summary.values()})
print('ENERGY OPERATOR OK')

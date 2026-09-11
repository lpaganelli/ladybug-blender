"""Build a Honeybee model from an IFC and report what came out.

    blender -b --factory-startup --python tests/test_ifc_bridge.py -- <lbpy> <bonsai_site_packages> <ifc> [<out.hbjson>]

``bonsai_site_packages`` is the folder where Bonsai installed ifcopenshell
(e.g. %APPDATA%/Blender Foundation/Blender/4.5/extensions/.local/lib/python3.11/site-packages).
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
argv = sys.argv[sys.argv.index('--') + 1:]
for p in (argv[0], argv[1], ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
ifc = argv[2]
out = argv[3] if len(argv) > 3 else os.path.join(HERE, 'out', 'model.hbjson')
os.makedirs(os.path.dirname(out), exist_ok=True)

from ladybug_tools.core.ifc_bridge import IfcToHoneybee  # noqa: E402

t0 = time.time()
bridge = IfcToHoneybee(ifc)
try:
    model = bridge.build()
except Exception:
    for w in bridge.warnings[:20]:
        print('  -', w)
    raise
path = bridge.write_hbjson(out)
rep = bridge.report
print('built in %.1fs -> %s (%.1f MB)' % (time.time() - t0, path, os.path.getsize(path) / 1e6))
print('location:', rep['location'], 'timings:', rep['timings'])
print('rooms %d, faces %d, apertures %d, doors %d, shades %d' % (
    rep['rooms_total'], rep['faces'], rep['apertures'], rep['doors'], rep['shades']))
print('adjacent pairs %d, adiabatic %d' % (rep['adjacent_pairs'], rep['adiabatic']))
print('boundary conditions:', rep['boundary_conditions'])
print('face types:', rep['face_types'])
print('constructions: %d opaque, %d window' % (rep['constructions'], rep['window_constructions']))
for r in rep['rooms']:
    print('  {:<22s} faces {:3d} solid {!s:5s} vol {:8.1f} ifc {!s:>8s}  {}'.format(
        r['name'][:22], r['faces'], r['solid'], r['volume'],
        round(r['ifc_volume'], 1) if r['ifc_volume'] else '-', r['counts']))
print('warnings (%d):' % len(rep['warnings']))
for w in rep['warnings'][:40]:
    print('  -', w)
# validate with honeybee's own checks
issues = model.check_all(raise_exception=False, detailed=False)
print('honeybee check_all:', 'OK' if not issues else issues[:2000])
json.dump(rep, open(os.path.join(os.path.dirname(out), 'ifc_bridge_report.json'), 'w'),
          indent=1, default=str)

# ---- the Blender operator + visualization ----
import bpy  # noqa: E402
import ladybug_tools  # noqa: E402
ladybug_tools.register()
bpy.ops.wm.read_factory_settings(use_empty=True)
p = bpy.context.scene.ladybug
p.hb_ifc_path = ifc
p.hb_draw = True
assert bpy.ops.ladybug.ifc_to_honeybee() == {'FINISHED'}
rooms = [o for o in bpy.data.objects if o.get('hb_room') and 'openings' not in o.name]
print('Blender objects: %d rooms, context: %s' % (len(rooms), 'HB Context' in bpy.data.objects))
assert len(rooms) == rep['rooms_total']
assert abs(p.latitude - rep['location']['latitude']) < 1e-6
p.hb_color_by = 'TYPE'
assert bpy.ops.ladybug.hb_redraw() == {'FINISHED'}
blend = os.path.join(os.path.dirname(out), 'ifc_model.blend')
bpy.ops.wm.save_as_mainfile(filepath=blend)
print('saved', blend)
print('DONE')

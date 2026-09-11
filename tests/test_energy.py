"""IFC -> Honeybee -> EnergyPlus end to end (headless Blender 4.5 with Bonsai's ifcopenshell).

    blender -b --factory-startup --python tests/test_energy.py -- <lbpy> <bonsai_site_packages> <ifc> <epw> <energyplus_dir>
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
argv = sys.argv[sys.argv.index('--') + 1:]
for p in (argv[0], argv[1], ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
ifc, epw, ep_dir = argv[2], argv[3], argv[4]
folder = os.path.join(HERE, 'out', 'energy')

from ladybug_tools.core.ifc_bridge import IfcToHoneybee  # noqa: E402
from ladybug_tools.core import energy_sim  # noqa: E402

bridge = IfcToHoneybee(ifc, include_context=True)
model = bridge.build()
kinds = energy_sim.prepare_model(model, hvac='FREE_RUNNING')
print('space kinds:', {model.rooms[i].display_name: k for i, k in enumerate(kinds.values())})
t0 = time.time()
sql, err, secs = energy_sim.run(model, epw, folder, ep_dir)
print('EnergyPlus finished in %.0f s -> %s' % (secs, sql))
print('err summary:', energy_sim.read_err_summary(err))
colls, summary = energy_sim.read_results(sql, model)
print('{:<22s} {:>6s} {:>6s} {:>6s} {:>8s} {:>8s} {:>8s}'.format(
    'room', 'mean', 'min', 'max', 'h>26C', 'h<18C', '%ok'))
for ident, s in summary.items():
    print('{:<22s} {:6.1f} {:6.1f} {:6.1f} {:8d} {:8d} {:8.1f}'.format(
        s['name'][:22], s['mean'], s['min'], s['max'], s['hours_hot'], s['hours_cold'],
        s['pct_comfort']))
assert len(summary) == len(model.rooms), 'missing zones in results'
assert all(10 < s['mean'] < 35 for s in summary.values()), 'implausible temperatures'
print('DONE')

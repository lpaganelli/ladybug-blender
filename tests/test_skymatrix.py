"""Sanity checks for the native sky matrix (run with Blender's python).

Physical check: the radiation reaching an unobstructed horizontal surface
computed from the sky patches must match the cumulative global horizontal
irradiance of the Wea (within a few percent, given the coarse Tregenza dome).
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

import numpy as np  # noqa: E402
from ladybug.wea import Wea  # noqa: E402
from ladybug_tools.core import skymatrix as sm  # noqa: E402


def main(epw_path):
    wea = Wea.from_epw_file(epw_path)
    t0 = time.time()
    mtx = sm.SkyMatrix(wea)
    direct = np.array(mtx.direct_values)
    diffuse = np.array(mtx.diffuse_values)
    dt = time.time() - t0
    vecs = mtx.patch_vectors
    cos_z = vecs[:, 2]

    ghi_from_sky = float(((direct + diffuse) * cos_z).sum())
    ghi_wea = sum(wea.global_horizontal_irradiance.values) / 1000.0
    dhi_wea = sum(wea.diffuse_horizontal_irradiance.values) / 1000.0
    dhi_from_sky = float((diffuse * cos_z).sum())
    dni_wea = sum(wea.direct_normal_irradiance.values) / 1000.0

    print('sky matrix computed in %.2fs (%d patches)' % (dt, len(direct)))
    print('annual DNI  (kWh/m2): wea=%.1f  sky patches sum=%.1f' % (dni_wea, direct.sum()))
    print('annual DHI  (kWh/m2): wea=%.1f  from sky=%.1f' % (dhi_wea, dhi_from_sky))
    print('annual GHI  (kWh/m2): wea=%.1f  from sky=%.1f  (%.1f%% diff)' % (
        ghi_wea, ghi_from_sky, 100 * (ghi_from_sky - ghi_wea) / ghi_wea))
    print('max patch total: %.1f kWh/m2' % (direct + diffuse).max())
    print('metadata:', mtx.metadata[:4])
    assert abs(dhi_from_sky - dhi_wea) / dhi_wea < 0.02, 'diffuse normalization broken'
    assert abs(direct.sum() - dni_wea) / dni_wea < 0.001, 'direct energy not conserved'
    assert abs(ghi_from_sky - ghi_wea) / ghi_wea < 0.05, 'GHI mismatch too large'

    # --- against Radiance gendaymtx reference values (tests/fixtures) ---
    fixture = os.path.join(HERE, 'fixtures', 'gendaymtx_reference.json')
    if os.path.isfile(fixture) and os.path.basename(epw_path) == 'test_sao_paulo.epw':
        import json
        with open(fixture) as f:
            ref = json.load(f)
        for name, case in ref.items():
            if not isinstance(case, dict):
                continue
            if case['high_density'] or 'annual' not in name or 'clear' not in name:
                continue
            r_tot = np.array(case['direct']) + np.array(case['diffuse'])
            corr = float(np.corrcoef(r_tot, direct + diffuse)[0, 1])
            tot = abs(float(np.sum(direct + diffuse)) - float(np.sum(r_tot))) / float(np.sum(r_tot))
            print('vs gendaymtx (%s): patch correlation %.4f, total diff %.2f%%' % (
                name, corr, 100 * tot))
            assert corr > 0.99, corr
            assert tot < 0.01, tot
            for surf, (a, _b) in case['surfaces'].items():
                n = {'horizontal': (0, 0, 1), 'north': (0, 1, 0), 'east': (1, 0, 0),
                     'south': (0, -1, 0), 'west': (-1, 0, 0)}.get(surf)
                if n is None:
                    continue
                n = np.array(n, dtype=float)
                sky = direct + diffuse
                ground = np.full(len(sky), float(np.sum(sky)) / len(sky) * 0.2)
                mine = float(np.sum(sky * np.maximum(vecs @ n, 0)) +
                             np.sum(ground * np.maximum((vecs * [1, 1, -1]) @ n, 0)))
                assert abs(mine - a) / a < 0.03, (surf, mine, a)
    print('OK')


if __name__ == '__main__':
    main(sys.argv[-1])

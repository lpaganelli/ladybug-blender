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
    print('OK')


if __name__ == '__main__':
    main(sys.argv[-1])

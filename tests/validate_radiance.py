"""Validate the native sky matrix against Radiance's gendaymtx.

Runs ladybug_radiance.SkyMatrix (which shells out to the official gendaymtx)
and core/skymatrix.SkyMatrix on the same Wea, then compares them patch by
patch and on unobstructed surfaces. Writes the reference values to
tests/fixtures/gendaymtx_reference.json so the regular test-suite can check
against them without a Radiance install.

    python tests/validate_radiance.py <radiance_root> <epw> [--write]

``radiance_root`` is the folder that contains ``bin/gendaymtx.exe``.
Requires ladybug packages on sys.path (or Blender's python with the wheels).
"""
import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

import numpy as np  # noqa: E402
from ladybug.analysisperiod import AnalysisPeriod  # noqa: E402
from ladybug.epw import EPW  # noqa: E402
from ladybug.wea import Wea  # noqa: E402
from ladybug_radiance.config import folders  # noqa: E402

from ladybug_tools.core import skymatrix as native  # noqa: E402

FIXTURE = os.path.join(HERE, 'fixtures', 'gendaymtx_reference.json')


def _run_gendaymtx(wea_file, density, flag):
    """Run gendaymtx and return per-patch broadband radiance (W/sr/m2), ground first.

    The header is parsed by looking for the blank line after ``FORMAT=``,
    so this works with Radiance 5.x and 6.x (6.0 added a ``LATLONG=`` line,
    which shifts fixed-offset parsers such as ladybug_radiance 0.2.x by one
    patch).
    """
    exe = os.path.join(folders.radbin_path, 'gendaymtx.exe')
    out = subprocess.run([exe, '-m', str(density), flag, '-O1', '-A', wea_file],
                         capture_output=True, text=True, check=True).stdout
    lines = out.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith('FORMAT=')) + 2
    nrows = int(next(l for l in lines if l.startswith('NROWS=')).split('=')[1])
    vals = []
    for l in lines[start:start + nrows]:
        r, g, b = (float(x) for x in l.split())
        vals.append(0.265074126 * r + 0.670114631 * g + 0.064811243 * b)
    assert len(vals) == nrows, 'expected {} rows, got {}'.format(nrows, len(vals))
    return np.array(vals)


def reference_sky(wea, high_density, workdir):
    """Sky patch radiation (kWh/m2) from the real gendaymtx, same units as Ladybug."""
    density = 2 if high_density else 1
    wea_file = wea.write(os.path.join(workdir, 'validate_{}'.format(density)))
    duration = len(wea) / wea.timestep
    from ladybug.viewsphere import view_sphere
    if high_density:
        rows = view_sphere.REINHART_PATCHES_PER_ROW + (1,)
        coeffs = view_sphere.REINHART_COEFFICIENTS
    else:
        rows = view_sphere.TREGENZA_PATCHES_PER_ROW + (1,)
        coeffs = view_sphere.TREGENZA_COEFFICIENTS
    omega = np.repeat(np.array(coeffs), rows)
    out = []
    for flag in ('-d', '-s'):
        rad = _run_gendaymtx(wea_file, density, flag)[1:]  # drop the ground patch
        out.append(rad * omega * duration / 1000.0)
    return out[0], out[1]


def surface_radiation(direct, diffuse, vecs, normal, ground_reflectance=0.2):
    """Unobstructed incident radiation on a surface (same math as the studies)."""
    sky = direct + diffuse
    ground = np.full(len(sky), float(np.sum(sky)) / len(sky) * ground_reflectance)
    gvecs = vecs * np.array([1.0, 1.0, -1.0])
    cos_s = np.maximum(vecs @ normal, 0.0)
    cos_g = np.maximum(gvecs @ normal, 0.0)
    return float(np.sum(sky * cos_s) + np.sum(ground * cos_g))


SURFACES = {
    'horizontal': (0, 0, 1), 'north': (0, 1, 0), 'east': (1, 0, 0),
    'south': (0, -1, 0), 'west': (-1, 0, 0), 'tilt30N': (0, math.sin(math.radians(30)),
                                                          math.cos(math.radians(30))),
}


def compare(name, wea, high_density, fixture, workdir):
    ref_dir, ref_dif = reference_sky(wea, high_density, workdir)
    mine = native.SkyMatrix(wea, 0, high_density)
    my_dir, my_dif = np.array(mine.direct_values), np.array(mine.diffuse_values)
    vecs = mine.patch_vectors

    tot_ref, tot_my = ref_dir + ref_dif, my_dir + my_dif
    scale = float(np.max(tot_ref))
    rows = []
    for label, r, m in (('direct', ref_dir, my_dir), ('diffuse', ref_dif, my_dif),
                        ('total', tot_ref, tot_my)):
        diff = m - r
        rows.append('  {:8s} sum ref {:8.1f}  native {:8.1f}  ({:+.2f}%) | '
                    'patch RMSE {:.2f}% of max, max |diff| {:.2f}% of max'.format(
                        label, float(np.sum(r)), float(np.sum(m)),
                        100 * (float(np.sum(m)) - float(np.sum(r))) / max(float(np.sum(r)), 1e-9),
                        100 * float(np.sqrt(np.mean(diff ** 2))) / scale,
                        100 * float(np.max(np.abs(diff))) / scale))
    corr = float(np.corrcoef(tot_ref, tot_my)[0, 1])
    print('{} [{} patches]  patch correlation {:.5f}'.format(name, len(vecs), corr))
    for r in rows:
        print(r)
    print('  unobstructed surfaces (kWh/m2): ref / native / diff')
    surf = {}
    worst = 0.0
    for s, n in SURFACES.items():
        n = np.array(n, dtype=float)
        a = surface_radiation(ref_dir, ref_dif, vecs, n)
        b = surface_radiation(my_dir, my_dif, vecs, n)
        d = 100 * (b - a) / max(a, 1e-9)
        worst = max(worst, abs(d))
        surf[s] = [a, b]
        print('    {:10s} {:8.1f} {:8.1f} {:+6.2f}%'.format(s, a, b, d))
    fixture[name] = {
        'high_density': high_density, 'direct': ref_dir.tolist(),
        'diffuse': ref_dif.tolist(), 'surfaces': surf,
    }
    return corr, worst


def cloudy_wea(epw_path):
    """Second test climate: Zhang-Huang radiation from a varying cloud cover."""
    epw = EPW(epw_path)
    cc = epw.total_sky_cover.duplicate()
    vals = []
    for i in range(len(cc)):
        vals.append(int(round(5 + 5 * math.sin(i / 7.0) * math.cos(i / 311.0))))
    cc.values = [min(10, max(0, v)) for v in vals]
    return Wea.from_zhang_huang_solar(
        epw.location, cc, epw.relative_humidity, epw.dry_bulb_temperature,
        epw.wind_speed)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    write = '--write' in sys.argv
    rad_root, epw_path = args[0], args[1]
    folders.radiance_path = rad_root
    print('gendaymtx:', os.path.join(folders.radbin_path, 'gendaymtx.exe'))

    fixture = {'epw': os.path.basename(epw_path)}
    results = []
    workdir = tempfile.mkdtemp(prefix='lb_validate_')
    clear = Wea.from_epw_file(epw_path)
    cloudy = cloudy_wea(epw_path)
    june = clear.filter_by_analysis_period(AnalysisPeriod(6, 1, 0, 6, 30, 23))
    results.append(compare('clear-sky annual, Tregenza', clear, False, fixture, workdir))
    results.append(compare('clear-sky annual, Reinhart', clear, True, fixture, workdir))
    results.append(compare('clear-sky June, Tregenza', june, False, fixture, workdir))
    results.append(compare('cloudy annual, Tregenza', cloudy, False, fixture, workdir))

    worst_corr = min(c for c, _w in results)
    worst_surf = max(w for _c, w in results)
    print('\nworst patch correlation {:.5f}; worst surface difference {:.2f}%'.format(
        worst_corr, worst_surf))
    if write:
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with open(FIXTURE, 'w') as f:
            json.dump(fixture, f)
        print('wrote', FIXTURE)


if __name__ == '__main__':
    main()

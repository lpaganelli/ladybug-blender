"""Validate the BVH ray casting against Radiance (oconv + rcontrib).

Builds the same scene as test_headless.py (ground grid, tower, tilted roof),
then computes direct sun hours and incident radiation for the ground sensors
with both ladybug_radiance.intersection (Radiance) and core/intersect.py
(Blender BVHTree), and compares them sensor by sensor.

    blender -b --factory-startup --python tests/validate_rcontrib.py -- <lbpy> <epw> <radiance_root> [--write]
"""
import json
import os
import sys
import tempfile
import time

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
argv = sys.argv[sys.argv.index('--') + 1:]
lbpy, epw, rad_root = argv[0], argv[1], argv[2]
write = '--write' in argv
for path in (lbpy, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import numpy as np  # noqa: E402
from ladybug.analysisperiod import AnalysisPeriod  # noqa: E402
from ladybug.sunpath import Sunpath  # noqa: E402
from ladybug.wea import Wea  # noqa: E402
from ladybug_geometry.geometry3d import Mesh3D, Point3D, Vector3D  # noqa: E402
from ladybug_radiance.config import folders  # noqa: E402
import ladybug_radiance.intersection as lbi  # noqa: E402

from ladybug_tools.core import intersect, skymatrix  # noqa: E402

FIXTURE = os.path.join(HERE, 'fixtures', 'rcontrib_reference.json')

folders.radiance_path = rad_root
lbi.OCONV_EXE = os.path.join(folders.radbin_path, 'oconv.exe')
lbi.RCONTRIB_EXE = os.path.join(folders.radbin_path, 'rcontrib.exe')
lbi.OBJ2MESH_EXE = os.path.join(folders.radbin_path, 'obj2mesh.exe')


def build_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    ground = bpy.context.active_object
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.subdivide(number_cuts=39)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 4))
    tower = bpy.context.active_object
    tower.scale = (4, 4, 8)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.mesh.primitive_plane_add(size=6, location=(8, -6, 3))
    roof = bpy.context.active_object
    roof.rotation_euler = (0.5, 0, 0)
    return ground, [ground, tower, roof]


def lb_mesh(ob, depsgraph):
    """World-space ladybug Mesh3D of a Blender mesh object."""
    verts, polys = intersect.gather_world_polygons([ob], depsgraph)
    return Mesh3D([Point3D(*v) for v in verts], [tuple(f) for f in polys])


def main():
    ground, context_objs = build_scene()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    pts, nrs = intersect.study_points(ground, depsgraph, by_vertex=False)
    offset = 0.01
    lb_ctx = [lb_mesh(ob, depsgraph) for ob in context_objs]
    lb_pts = [Point3D(*p) for p in pts]
    lb_nrs = [Vector3D(*n) for n in nrs]
    bvh = intersect.build_bvh(context_objs, depsgraph)
    workdir = tempfile.mkdtemp(prefix='lb_rcontrib_')
    report = {}

    # ---- direct sun hours, Jun 21 and Dec 21 ----
    wea = Wea.from_epw_file(epw)
    sp = Sunpath.from_location(wea.location)
    for month, day in ((6, 21), (12, 21)):
        ap = AnalysisPeriod(month, day, 0, month, day, 23)
        suns = [sp.calculate_sun_from_hoy(h) for h in ap.hoys]
        suns = [s for s in suns if s.is_during_day]
        vecs = np.array([(s.sun_vector_reversed.x, s.sun_vector_reversed.y,
                          s.sun_vector_reversed.z) for s in suns])
        lb_vecs = [s.sun_vector_reversed for s in suns]
        t0 = time.time()
        ref = np.array(lbi.intersection_matrix(
            lb_vecs, lb_pts, lb_nrs, lb_ctx, offset, numericalize=False,
            sim_folder=os.path.join(workdir, 'sun_{}'.format(month))))
        t_ref = time.time() - t0
        t0 = time.time()
        mine = intersect.intersection_matrix(bvh, vecs, pts, nrs, offset, numericalize=False)
        t_mine = time.time() - t0
        ref_h, my_h = np.sum(ref, axis=1), np.sum(mine, axis=1)
        agree = float(np.mean(ref == mine))
        diff = my_h - ref_h
        name = 'sun hours {}/{}'.format(day, month)
        print('{}: {} sensors x {} suns | ray agreement {:.3%} | hours: mean |diff| {:.3f} h, '
              'max |diff| {:.1f} h, sensors differing {} | rcontrib {:.1f}s, BVH {:.2f}s'.format(
                  name, len(pts), len(vecs), agree, float(np.mean(np.abs(diff))),
                  float(np.max(np.abs(diff))), int(np.sum(diff != 0)), t_ref, t_mine))
        report[name] = {'ray_agreement': agree, 'ref_hours': ref_h.tolist(),
                        'mean_abs_diff': float(np.mean(np.abs(diff))),
                        'max_abs_diff': float(np.max(np.abs(diff)))}

    # ---- incident radiation, annual ----
    sky = skymatrix.SkyMatrix(wea)
    t0 = time.time()
    ref = np.array(lbi.sky_intersection_matrix(
        sky, lb_pts, lb_nrs, lb_ctx, offset, numericalize=True,
        sim_folder=os.path.join(workdir, 'sky')))
    t_ref = time.time() - t0
    t0 = time.time()
    # ladybug mirrors the ground patches through the origin (-v); the add-on
    # mirrors them in Z. Both give the same radiation (the ground hemisphere is
    # uniform) but the columns only line up with ladybug's convention.
    sky_vecs = sky.patch_vectors
    vectors = np.vstack([sky_vecs, -sky_vecs])
    mine = intersect.intersection_matrix(bvh, vectors, pts, nrs, offset, numericalize=True)
    t_mine = time.time() - t0
    ref_rad = intersect.radiation_from_intersection(sky, ref)
    my_rad = intersect.radiation_from_intersection(sky, mine)
    diff = my_rad - ref_rad
    rel = np.abs(diff) / max(float(np.max(ref_rad)), 1e-9)
    print('radiation annual: matrix RMSE {:.4f} (cos units) | kWh/m2: mean {:.1f} vs {:.1f}, '
          'mean |diff| {:.2f}% of max, max |diff| {:.2f}% of max | rcontrib {:.1f}s, BVH {:.2f}s'.format(
              float(np.sqrt(np.mean((mine - ref) ** 2))), float(np.mean(my_rad)),
              float(np.mean(ref_rad)), 100 * float(np.mean(rel)), 100 * float(np.max(rel)),
              t_ref, t_mine))
    report['radiation annual'] = {
        'ref_kwh': ref_rad.tolist(), 'mean_rel_diff': float(np.mean(rel)),
        'max_rel_diff': float(np.max(rel))}
    if write:
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with open(FIXTURE, 'w') as f:
            json.dump(report, f)
        print('wrote', FIXTURE)


main()

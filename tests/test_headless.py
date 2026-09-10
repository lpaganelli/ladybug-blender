"""End-to-end test of the extension inside Blender (background mode).

Usage (dev mode, wheels not installed):
    blender -b --factory-startup --python tests/test_headless.py -- <lbpy_path> <epw>

``lbpy_path`` is a folder containing the ladybug packages (pip --target),
needed only when the extension is not installed through Blender's extension
system.
"""
import os
import sys
import time

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
lbpy = argv[0] if len(argv) > 0 else ''
epw = argv[1] if len(argv) > 1 else os.path.join(HERE, 'test_sao_paulo.epw')
out_blend = os.path.join(HERE, 'out', 'test_result.blend')
os.makedirs(os.path.dirname(out_blend), exist_ok=True)

if lbpy and lbpy not in sys.path:
    sys.path.insert(0, lbpy)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ladybug_tools  # noqa: E402

ladybug_tools.register()


def values(ob, attr):
    return [d.value for d in ob.data.attributes[attr].data]


def result_of(source, study):
    ob = bpy.data.objects.get('{} · {}'.format(source.name, study))
    assert ob is not None, 'missing result {} · {}'.format(source.name, study)
    coll = bpy.data.collections.get('{} (LB)'.format(source.name))
    assert coll is not None and ob.name in coll.objects
    assert ob.get('ladybug_source') == source.name
    return ob


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    p = scene.ladybug

    p.epw_path = epw
    assert p.epw_loaded, 'EPW not loaded'
    print('Location:', p.city, p.latitude, p.longitude, p.time_zone)

    # --- geometry: a ground plane, a tall box shading it, a tilted roof ---
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = 'Ground'
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.subdivide(number_cuts=39)
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 4))
    tower = bpy.context.active_object
    tower.name = 'Tower'
    tower.scale = (4, 4, 8)
    bpy.ops.object.transform_apply(scale=True)

    bpy.ops.mesh.primitive_plane_add(size=6, location=(8, -6, 3))
    roof = bpy.context.active_object
    roof.name = 'Roof'
    roof.rotation_euler = (0.5, 0, 0)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.subdivide(number_cuts=11)
    bpy.ops.object.mode_set(mode='OBJECT')

    # --- sun path ---
    scene.cursor.location = (-15, 15, 0)
    p.sp_radius = 10
    p.sp_sun_step = 3
    assert bpy.ops.ladybug.draw_sunpath() == {'FINISHED'}
    assert 'LB Analemmas' in bpy.data.objects
    assert 'LB Sun' in bpy.data.objects

    p.sun_month, p.sun_day, p.sun_hour = '12', 21, 15.0
    assert bpy.ops.ladybug.set_sun() == {'FINISHED'}
    sun_ob = bpy.data.objects['LB Sun']
    print('Sun light location:', tuple(round(v, 2) for v in sun_ob.location))
    assert sun_ob.location.z > 0

    # --- direct sun hours on the ground, shaded by the tower ---
    p.ap_st_month, p.ap_st_day, p.ap_st_hour = '6', 21, 0
    p.ap_end_month, p.ap_end_day, p.ap_end_hour = '6', 21, 23
    p.st_context = 'VISIBLE'
    bpy.ops.object.select_all(action='DESELECT')
    ground.select_set(True)
    bpy.context.view_layer.objects.active = ground
    t0 = time.time()
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    res = result_of(ground, 'Sun Hours')
    hours = values(res, 'LB Sun Hours')
    print('Direct sun hours (Jun 21): min %.1f max %.1f avg %.1f  [%.1fs]' % (
        min(hours), max(hours), sum(hours) / len(hours), time.time() - t0))
    assert max(hours) > 8, 'open ground should see most of the day'
    assert min(hours) < max(hours), 'tower should cast a shadow'
    assert 'LB Color' in res.data.color_attributes
    assert 'LB Sun Hours' not in ground.data.attributes, 'source must stay untouched'
    assert not ground.hide_get(), 'source must stay visible'
    assert bpy.context.active_object == ground
    # result copy is offset along +Z by the sensor offset
    assert abs(res.data.vertices[0].co.z - p.st_offset) < 1e-6
    # one legend per batch, in the LB Results root, tagged with the study type
    coll = bpy.data.collections['Ground (LB)']
    assert not any('Legend' in o.name for o in coll.objects)
    legend = bpy.data.objects['LB Legend · Sun Hours Legend']
    assert legend.name in bpy.data.collections['LB Results'].objects
    assert legend['ladybug_study'] == 'Sun Hours'
    # upright legend: its faces lie in a vertical plane (all vertices same y)
    ys = {round(v.co.y, 5) for v in legend.data.vertices}
    assert len(ys) == 1, ys
    zs = [v.co.z for v in legend.data.vertices]
    assert abs((max(zs) - min(zs)) - p.lg_size) < 1e-4
    # re-running replaces the result instead of duplicating it
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    assert len([o for o in coll.objects if 'Sun Hours' in o.name and o.type == 'MESH'
                and 'Legend' not in o.name]) == 1
    # running with the result active resolves back to the source
    res = result_of(ground, 'Sun Hours')  # fresh object after the re-run
    bpy.context.view_layer.objects.active = res
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    assert bpy.data.objects.get('Ground · Sun Hours · Sun Hours') is None

    # --- incident radiation, annual, on the tilted roof ---
    p.ap_st_month, p.ap_st_day, p.ap_st_hour = '1', 1, 0
    p.ap_end_month, p.ap_end_day, p.ap_end_hour = '12', 31, 23
    bpy.ops.object.select_all(action='DESELECT')
    roof.select_set(True)
    bpy.context.view_layer.objects.active = roof
    t0 = time.time()
    assert bpy.ops.ladybug.incident_radiation() == {'FINISHED'}
    rad = values(result_of(roof, 'Radiation'), 'LB Radiation')
    print('Annual radiation on roof: min %.0f max %.0f avg %.0f kWh/m2 [%.1fs]' % (
        min(rad), max(rad), sum(rad) / len(rad), time.time() - t0))
    assert 800 < max(rad) < 2600, 'unrealistic annual radiation'

    # also on the tower (faces in every orientation) to check north facade < south
    bpy.ops.object.select_all(action='DESELECT')
    tower.select_set(True)
    bpy.context.view_layer.objects.active = tower
    assert bpy.ops.ladybug.incident_radiation() == {'FINISHED'}
    tres = result_of(tower, 'Radiation')
    vals = values(tres, 'LB Radiation')
    normals = [tuple(round(c) for c in pl.normal) for pl in tres.data.polygons]
    by_normal = dict(zip(normals, vals))
    print('Tower facades kWh/m2:', {k: round(v) for k, v in by_normal.items()})
    # southern hemisphere: the north facade (0,1,0) gets more sun than south (0,-1,0)
    assert by_normal[(0, 1, 0)] > by_normal[(0, -1, 0)]
    assert by_normal[(0, 0, 1)] > by_normal[(0, 1, 0)]
    # both studies of the tower share one sub-collection
    tcoll = bpy.data.collections['Tower (LB)']
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    assert 'Tower · Sun Hours' in tcoll.objects and 'Tower · Radiation' in tcoll.objects

    # --- switch which study type is visible ---
    p.st_show = 'Radiation'
    assert bpy.data.objects['Tower · Sun Hours'].hide_get()
    assert not bpy.data.objects['Tower · Radiation'].hide_get()
    assert all(o.hide_get() for o in tcoll.objects if 'Sun Hours' in o.name)
    p.st_show = 'Sun Hours'
    assert not bpy.data.objects['Tower · Sun Hours'].hide_get()
    assert bpy.data.objects['Tower · Radiation'].hide_get()
    p.st_show = 'ALL'
    assert not any(o.hide_get() for o in tcoll.objects)

    # --- a result copy must never shade other studies, the source still does ---
    # ground below the tower: the tower's result copies are generated -> ignored,
    # but the tower itself keeps blocking, so the shadow persists
    bpy.ops.object.select_all(action='DESELECT')
    ground.select_set(True)
    bpy.context.view_layer.objects.active = ground
    assert bpy.ops.ladybug.incident_radiation() == {'FINISHED'}
    grad = values(result_of(ground, 'Radiation'), 'LB Radiation')
    assert min(grad) < 0.5 * max(grad), 'tower shadow missing on the ground'

    # --- study object with a topology-changing modifier (subdivision) ---
    bpy.ops.mesh.primitive_plane_add(size=6, location=(-8, -8, 1))
    slab = bpy.context.active_object
    slab.name = 'Slab'
    mod = slab.modifiers.new('Subdiv', 'SUBSURF')
    mod.subdivision_type = 'SIMPLE'
    mod.levels = 4
    bpy.ops.object.select_all(action='DESELECT')
    slab.select_set(True)
    bpy.context.view_layer.objects.active = slab
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    baked = result_of(slab, 'Sun Hours')
    assert len(baked.data.polygons) == 256
    assert len(slab.data.polygons) == 1, 'source must keep its modifier stack'
    print('Modifier bake OK: %d faces' % len(baked.data.polygons))

    # --- batch: tower + roof in one run, each shading the other ---
    bpy.ops.object.select_all(action='DESELECT')
    tower.select_set(True)
    roof.select_set(True)
    bpy.context.view_layer.objects.active = roof
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    assert result_of(tower, 'Sun Hours') and result_of(roof, 'Sun Hours')
    assert bpy.context.active_object == roof
    assert tower.select_set is not None and tower.select_get() and roof.select_get()
    meta = result_of(roof, 'Sun Hours')
    assert meta['lb_period'] and meta['lb_north'] == p.north
    # shared scale: identical values in different objects get identical colors
    rt, rr = result_of(tower, 'Sun Hours'), result_of(roof, 'Sun Hours')
    vt = values(rt, 'LB Sun Hours')
    vr = values(rr, 'LB Sun Hours')
    ct = [tuple(round(x, 3) for x in c.color) for c in rt.data.color_attributes['LB Color'].data]
    cr = [tuple(round(x, 3) for x in c.color) for c in rr.data.color_attributes['LB Color'].data]
    # map face values to a corner color of that face
    def face_colors(ob, cols):
        return {round(pl.index, 0): cols[pl.loop_start] for pl in ob.data.polygons}
    fct, fcr = face_colors(rt, ct), face_colors(rr, cr)
    # the global maximum (tower top) gets the hot end of the scale; the roof's
    # own maximum is lower, so on a shared scale it must NOT get that color
    it = max(range(len(vt)), key=lambda i: vt[i])
    ir = max(range(len(vr)), key=lambda i: vr[i])
    assert vt[it] > vr[ir] + 1, (vt[it], vr[ir])
    assert fct[it] != fcr[ir], 'roof max must not share the color of the global max'
    assert len([o for o in bpy.data.objects if o.name.startswith('LB Legend · Sun Hours')]) >= 1

    # --- period explorer: recolor cached results without ray tracing ---
    # radiation: the intersection matrix is period-independent, any period works
    p.ex_live = False
    p.ex_mode, p.ex_month, p.ex_st_hour, p.ex_end_hour = 'MONTH', 6, 0, 23
    t0 = time.time()
    assert bpy.ops.ladybug.explore_period() == {'FINISHED'}
    dt_explore = time.time() - t0
    june = values(result_of(roof, 'Radiation'), 'LB Radiation')
    annual = rad
    # the roof tilts south: in June (winter) it gets only a few % of its annual total
    assert 0.005 < sum(june) / sum(annual) < 0.2, sum(june) / sum(annual)
    assert 'LB Legend · Radiation Legend' in bpy.data.objects
    print('Explorer (June, all cached results): %.2fs' % dt_explore)
    # sun hours: traced for Jun 21 only -> asking for December is "partial"
    p.ex_mode, p.ex_month, p.ex_day = 'DAY', 6, 21
    assert bpy.ops.ladybug.explore_period() == {'FINISHED'}
    same = values(result_of(ground, 'Sun Hours'), 'LB Sun Hours')
    assert abs(max(same) - 11.0) < 1e-6
    p.ex_st_hour, p.ex_end_hour = 12, 23  # afternoon only
    assert bpy.ops.ladybug.explore_period() == {'FINISHED'}
    pm = values(result_of(ground, 'Sun Hours'), 'LB Sun Hours')
    assert max(pm) < max(same), (max(pm), max(same))
    p.ex_st_hour, p.ex_end_hour = 0, 23
    # full-year sun trace enables any period
    p.st_cache_year = True
    p.ap_st_month, p.ap_st_day, p.ap_end_month, p.ap_end_day = '6', 21, '6', 21
    bpy.ops.object.select_all(action='DESELECT')
    ground.select_set(True)
    bpy.context.view_layer.objects.active = ground
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    p.ex_mode, p.ex_month, p.ex_day = 'DAY', 12, 21
    assert bpy.ops.ladybug.explore_period() == {'FINISHED'}
    dec = values(result_of(ground, 'Sun Hours'), 'LB Sun Hours')
    assert max(dec) > 12, max(dec)  # summer day is longer than the June one
    p.ex_mode = 'YEAR'
    assert bpy.ops.ladybug.explore_period() == {'FINISHED'}
    yr = values(result_of(ground, 'Sun Hours'), 'LB Sun Hours')
    assert max(yr) > 4000, max(yr)
    p.st_cache_year = False
    p.ap_st_month, p.ap_st_day, p.ap_end_month, p.ap_end_day = '1', 1, '12', 31

    # --- rebuild legend with new settings, without recomputing ---
    p.lg_size = 5.0
    p.lg_orientation = 'FLAT'
    p.lg_use_range, p.lg_min, p.lg_max = True, 0.0, 1000.0
    rt = result_of(tower, 'Sun Hours')
    assert bpy.ops.ladybug.rebuild_legend() == {'FINISHED'}
    legend = bpy.data.objects['LB Legend · Sun Hours Legend']
    zs = {round(v.co.z, 5) for v in legend.data.vertices}
    assert len(zs) == 1, 'flat legend must lie in one z'
    ys = [v.co.y for v in legend.data.vertices]
    assert abs((max(ys) - min(ys)) - 5.0) < 1e-4
    label = bpy.data.objects['LB Legend · Sun Hours Label 0'].data.body
    assert label == '0', label  # step of 100 h between labels -> no decimals
    top = bpy.data.objects['LB Legend · Sun Hours Label 10'].data.body
    assert top == '1000', top  # custom range applied to the legend
    p.lg_use_range = False
    p.lg_size = 2.0
    p.lg_orientation = 'UPRIGHT'
    assert bpy.ops.ladybug.rebuild_legend() == {'FINISHED'}

    # --- sensor grid: solid gets Remesh, open surface gets Subdivision ---
    bpy.ops.mesh.primitive_cube_add(size=4, location=(10, 10, 2))
    box = bpy.context.active_object
    box.name = 'Box'
    bpy.ops.mesh.primitive_plane_add(size=4, location=(16, 10, 0))
    sheet = bpy.context.active_object
    sheet.name = 'Sheet'
    bpy.ops.object.select_all(action='DESELECT')
    box.select_set(True)
    sheet.select_set(True)
    p.st_cell_size = 0.5
    assert bpy.ops.ladybug.sensor_grid() == {'FINISHED'}
    assert box.modifiers['LB Sensor Grid'].type == 'REMESH'
    assert sheet.modifiers['LB Sensor Grid'].type == 'SUBSURF'
    dg = bpy.context.evaluated_depsgraph_get()
    nb = len(box.evaluated_get(dg).to_mesh().polygons)
    ns = len(sheet.evaluated_get(dg).to_mesh().polygons)
    box.evaluated_get(dg).to_mesh_clear()
    sheet.evaluated_get(dg).to_mesh_clear()
    print('Sensor grid: box %d faces, sheet %d faces' % (nb, ns))
    # 4 m box at 0.5 m cells: depth 3 -> 7x7 per side (~0.57 m), remesh pads a bit
    assert 6 * 36 <= nb <= 6 * 81, nb
    assert ns == 64, ns
    assert bpy.ops.ladybug.direct_sun_hours() == {'FINISHED'}
    assert len(result_of(box, 'Sun Hours').data.polygons) == nb
    assert bpy.ops.ladybug.remove_sensor_grid() == {'FINISHED'}
    assert 'LB Sensor Grid' not in box.modifiers

    # --- clear results of one object, then everything ---
    bpy.context.view_layer.objects.active = slab
    assert bpy.ops.ladybug.clear_results(only_active=True) == {'FINISHED'}
    assert bpy.data.collections.get('Slab (LB)') is None
    assert bpy.data.collections.get('Tower (LB)') is not None

    # --- climate graphics ---
    scene.cursor.location = (20, 20, 0)
    p.viz_radius = 6
    assert bpy.ops.ladybug.sky_dome() == {'FINISHED'}
    scene.cursor.location = (20, 0, 0)
    assert bpy.ops.ladybug.radiation_rose() == {'FINISHED'}
    scene.cursor.location = (20, -20, 0)
    assert bpy.ops.ladybug.wind_rose() == {'FINISHED'}
    for name in ('LB Sky Dome', 'LB Radiation Rose', 'LB Wind Rose'):
        assert name in bpy.data.objects, name
    # graphics drawn at an elevated cursor keep their legend at that height
    scene.cursor.location = (20, -40, 7.5)
    assert bpy.ops.ladybug.wind_rose() == {'FINISHED'}
    lg = bpy.data.objects['LB Wind Rose Legend']
    zs = [v.co.z for v in lg.data.vertices]
    assert abs(min(zs) - 7.5) < 1e-4, min(zs)
    assert abs((max(zs) - min(zs)) - p.lg_size) < 1e-4
    scene.cursor.location = (20, -20, 0)
    assert bpy.ops.ladybug.wind_rose() == {'FINISHED'}

    def rose_extent():
        ob = bpy.data.objects['LB Wind Rose']
        c = scene.cursor.location
        return max(((v.co.x - c.x) ** 2 + (v.co.y - c.y) ** 2) ** 0.5
                   for v in ob.data.vertices)
    annual_extent = rose_extent()
    assert annual_extent > 0.6 * p.viz_radius, annual_extent
    # a one-day period must still fill the compass
    p.ap_st_month, p.ap_st_day, p.ap_end_month, p.ap_end_day = '12', 21, '12', 21
    assert bpy.ops.ladybug.wind_rose() == {'FINISHED'}
    day_extent = rose_extent()
    assert day_extent > 0.6 * p.viz_radius, day_extent
    p.ap_st_month, p.ap_st_day, p.ap_end_month, p.ap_end_day = '1', 1, '12', 31
    # calm rose with non-speed data must not raise
    p.wr_show_calm = True
    p.wr_data = 'dry_bulb_temperature'
    assert bpy.ops.ladybug.wind_rose() == {'FINISHED'}
    p.wr_data = 'wind_speed'
    assert bpy.ops.ladybug.wind_rose() == {'FINISHED'}
    p.wr_show_calm = False

    p.dome_projection = 'Stereographic'
    scene.cursor.location = (35, 20, 0)
    assert bpy.ops.ladybug.sky_dome() == {'FINISHED'}

    # --- animation ---
    scene.cursor.location = (0, 0, 0)
    assert bpy.ops.ladybug.animate_sun() == {'FINISHED'}
    print('Animation frames:', scene.frame_start, scene.frame_end)

    bpy.ops.wm.save_as_mainfile(filepath=out_blend)
    print('Saved', out_blend)

    assert bpy.ops.ladybug.clear_results(only_active=False) == {'FINISHED'}
    assert len(bpy.data.collections['LB Results'].children) == 0
    print('ALL TESTS PASSED')


main()

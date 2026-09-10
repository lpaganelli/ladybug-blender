# -*- coding: utf-8 -*-
"""Direct sun hours and incident radiation studies on mesh objects.

Results never touch the source objects. Each study bakes the evaluated
geometry of every selected source into a result object stored under::

    Ladybug / LB Results / <source> (LB) / <source> · <study>

together with its legend, so hiding the sub-collection hides one object's
results and every study keeps its own legend. Result objects are offset
slightly along their normals (the sensor offset) so they do not z-fight with
the source, which stays visible and keeps shading other studies.

Studies run in batch: every selected mesh (or the source of a selected
result copy) is analysed, each one shaded by all the others.
"""
import math
import time
import uuid

import bpy
import numpy as np

from ladybug.analysisperiod import AnalysisPeriod
from ladybug.color import Colorset
from ladybug.datatype.energyintensity import Radiation
from ladybug.datatype.energyflux import Irradiance
from ladybug.datatype.time import Time
from ladybug.legend import Legend
from ladybug_geometry.geometry3d import Plane, Point3D, Vector3D

from . import common
from ..core import blender_geom as bg
from ..core import intersect

RESULT_ATTR = 'LB Color'
SOURCE_PROP = 'ladybug_source'
STUDY_PROP = 'ladybug_study'
RESULTS_COLLECTION = 'Results'
GRID_MODIFIER = 'LB Sensor Grid'
SENSOR_WARNING = 50000


# ---------------------------------------------------------------------------
# selection helpers
# ---------------------------------------------------------------------------
def _source_objects(context):
    """Meshes to analyse: selected meshes, or sources of selected result copies."""
    sources = []
    seen = set()

    def add(ob):
        if ob is not None and ob.type == 'MESH' and ob.name not in seen:
            seen.add(ob.name)
            sources.append(ob)

    candidates = list(context.selected_objects)
    if context.active_object is not None and context.active_object not in candidates:
        candidates.append(context.active_object)
    for ob in candidates:
        if ob.type != 'MESH':
            continue
        src_name = ob.get(SOURCE_PROP)
        if src_name:
            add(bpy.data.objects.get(src_name))
        elif not bg.is_generated(ob):
            add(ob)
    if not sources:
        raise ValueError('Select one or more mesh objects as study geometry')
    return sources


def _context_pool(context, sources):
    """Objects that can shade the studies (never result copies or legends)."""
    p = common.props(context)
    if p.st_context == 'SELECTED':
        pool = list(context.selected_objects)
    elif p.st_context == 'VISIBLE':
        pool = list(context.visible_objects)
    else:
        pool = []
    pool = [o for o in pool if o.type == 'MESH' and not bg.is_generated(o)]
    for src in sources:  # studies always shade each other in a batch
        if src not in pool:
            pool.append(src)
    return pool


def _bvh_for(context, pool, study, depsgraph, cache):
    """BVH of the pool, minus the study itself when self shading is off."""
    p = common.props(context)
    if p.st_include_self:
        if 'all' not in cache:
            cache['all'] = intersect.build_bvh(pool, depsgraph)
        return cache['all']
    return intersect.build_bvh([o for o in pool if o != study], depsgraph)


# ---------------------------------------------------------------------------
# result objects
# ---------------------------------------------------------------------------
def _results_collection(context, source):
    root = bg.get_collection(context, RESULTS_COLLECTION)
    return bg.get_child_collection(root, '{} (LB)'.format(source.name))


def _remove_result(coll, name):
    for ob in list(coll.objects):
        if ob.name == name or ob.name.startswith(name + ' '):
            bg.remove_object(ob)


def _make_result_object(context, source, study_label, offset):
    """Bake the evaluated geometry of ``source`` into a new result object."""
    depsgraph = context.evaluated_depsgraph_get()
    ob_eval = source.evaluated_get(depsgraph)
    name = '{} · {}'.format(source.name, study_label)
    coll = _results_collection(context, source)
    _remove_result(coll, name)
    me = bpy.data.meshes.new_from_object(
        ob_eval, preserve_all_data_layers=False, depsgraph=depsgraph)
    me.name = name
    # sensor offset in world units -> local units (uniform scale assumed)
    scale = source.matrix_world.to_scale()
    avg_scale = (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0 or 1.0
    bg.offset_mesh_along_normals(me, offset / avg_scale)
    ob = bpy.data.objects.new(name, me)
    ob.matrix_world = source.matrix_world.copy()
    coll.objects.link(ob)
    bg.tag_generated(ob)
    ob[SOURCE_PROP] = source.name
    ob[STUDY_PROP] = study_label
    return ob, coll


def _store_metadata(context, ob, sky=None):
    """Record the study parameters on the result object for reproducibility."""
    p = common.props(context)
    ob['lb_period'] = common.period_label(context)
    ob['lb_timestep'] = int(p.ap_timestep)
    ob['lb_offset'] = p.st_offset
    ob['lb_north'] = p.north
    ob['lb_context'] = p.st_context
    ob['lb_by_vertex'] = p.st_by_vertex
    ob['lb_location'] = '{} ({:.3f}, {:.3f}, tz {:g})'.format(
        p.city or 'manual', p.latitude, p.longitude, p.time_zone)
    if sky is not None:
        ob['lb_sky'] = p.st_sky
        ob['lb_epw'] = p.epw_path if p.st_sky == 'EPW' else ''
        ob['lb_sky_patches'] = sky.patch_count
        ob['lb_ground_reflectance'] = p.st_ground_reflectance


def _legend_plane(context, items):
    """Base plane for the batch legend from the panel settings."""
    p = common.props(context)
    if p.lg_position == 'CURSOR':
        c = context.scene.cursor.location
        origin = Point3D(c.x, c.y, c.z)
    else:  # beside the bounding box of every result in the batch
        boxes = [bg.bbox_world(ob) for ob, _coll, _vals in items]
        max_x = max(b[1].x for b in boxes)
        min_y = min(b[0].y for b in boxes)
        min_z = min(b[0].z for b in boxes)
        margin = p.lg_size * 0.25
        origin = Point3D(max_x + margin, min_y, min_z)
    if p.lg_orientation == 'UPRIGHT':
        return Plane(n=Vector3D(0, -1, 0), o=origin, x=Vector3D(1, 0, 0))
    return Plane(o=origin)


# study type -> (data type, unit, default colors, value attribute)
STUDY_KINDS = {
    'Sun Hours': (Time, 'hr', Colorset.ecotect, 'LB Sun Hours'),
    'Radiation': (Radiation, 'kWh/m2', None, 'LB Radiation'),
    'Irradiance': (Irradiance, 'W/m2', None, 'LB Irradiance'),
}


def _decimals_for(lmin, lmax, segments):
    """Decimal places so that consecutive legend labels differ."""
    step = abs(lmax - lmin) / max(segments - 1, 1)
    if step >= 10:
        return 0
    if step >= 1:
        return 1
    if step >= 0.1:
        return 2
    return 3


def _colorize_batch(context, items, study_label, period=None):
    """Color every result of a batch on one shared scale and draw one legend.

    ``items`` is a list of (result_object, collection, values). Values are
    stored on the result mesh so the legend can be rebuilt later.
    """
    p = common.props(context)
    data_cls, unit, colorset, attr_value_name = STUDY_KINDS[study_label]
    default_colors = colorset() if colorset else None
    period = period or common.period_label(context)
    all_values = np.concatenate([vals for _ob, _coll, vals in items])

    l_par = common.legend_parameters(context, title=unit, default_colors=default_colors)
    if l_par.min is None:
        l_par.min = 0.0
    if l_par.max is None:
        l_par.max = float(np.max(all_values))
    if l_par.max <= l_par.min:
        l_par.max = l_par.min + 1.0
    l_par.decimal_count = _decimals_for(l_par.min, l_par.max, l_par.segment_count)
    seg_h = p.lg_size / l_par.segment_count
    l_par.segment_height = seg_h
    l_par.segment_width = seg_h * 0.6
    l_par.text_height = seg_h * 0.35
    l_par.base_plane = _legend_plane(context, items)

    legend = Legend(list(all_values), l_par)
    color_range = legend.color_range
    for ob, _coll, values in items:
        domain = 'POINT' if len(values) == len(ob.data.vertices) and \
            len(values) != len(ob.data.polygons) else 'FACE'
        colors = [bg.lb_color(color_range.color(float(v))) for v in values]
        bg.set_float_attribute(ob.data, attr_value_name, values, domain)
        bg.set_color_attribute(ob.data, RESULT_ATTR, colors, domain)
        bg.assign_material(ob, bg.color_attribute_material(RESULT_ATTR))

    root = bg.get_collection(context, RESULTS_COLLECTION)
    name = 'LB Legend · {}'.format(study_label)
    bg.remove_objects_by_prefix(root, name)
    if p.lg_show:
        title = '{} ({})\n{}\n{} object(s)'.format(
            data_cls().name, unit, period, len(items))
        for legend_ob in bg.legend_objects(legend, name, root, title=title):
            legend_ob[STUDY_PROP] = study_label
            legend_ob[SOURCE_PROP] = ', '.join(ob[SOURCE_PROP] for ob, _c, _v in items)
    return legend


def _stored_values(ob, study_label):
    attr = ob.data.attributes.get(STUDY_KINDS[study_label][3])
    if attr is None:
        return None
    vals = np.empty(len(attr.data), dtype=np.float32)
    attr.data.foreach_get('value', vals)
    return vals.astype(np.float64)


def apply_study_visibility(context):
    """Show only the result objects of the study type chosen in the panel."""
    p = common.props(context)
    wanted = p.st_show
    for ob in bpy.data.objects:
        study = ob.get(STUDY_PROP)
        if not study:
            continue
        hide = wanted != 'ALL' and study != wanted
        try:
            ob.hide_set(hide)
        except RuntimeError:  # object not in the current view layer
            pass
        ob.hide_render = hide


def _finish_batch(context, sources, results):
    """Keep sources selected and active so a re-run works; select results too."""
    for ob in results:
        ob.select_set(True)
    for ob in sources:
        ob.select_set(True)
    active = context.active_object
    if active is None or active.get(SOURCE_PROP) or active not in sources:
        context.view_layer.objects.active = sources[0]
    bg.show_attribute_colors(context)
    apply_study_visibility(context)


# ---------------------------------------------------------------------------
# visibility cache & period explorer
# ---------------------------------------------------------------------------
_VIS_CACHE = {}
CACHE_PROP = 'lb_cache_id'


def _cache_put(ob, kind, **data):
    key = uuid.uuid4().hex
    ob[CACHE_PROP] = key
    data['kind'] = kind
    _VIS_CACHE[key] = data


def _cache_get(ob):
    key = ob.get(CACHE_PROP)
    return _VIS_CACHE.get(key) if key else None


def _hoy_mask(hoys, ap):
    """Boolean mask of the hoys that fall inside an AnalysisPeriod."""
    wanted = np.array(ap.hoys)
    # match on the hour-of-year rounded to the timestep grid
    return np.isin(np.round(hoys, 4), np.round(wanted, 4))


def explorer_period(context):
    """AnalysisPeriod described by the Period Explorer settings."""
    p = common.props(context)
    ts = int(p.ap_timestep)
    if p.ex_mode == 'YEAR':
        return AnalysisPeriod(1, 1, p.ex_st_hour, 12, 31, p.ex_end_hour, ts)
    if p.ex_mode == 'MONTH':
        m = p.ex_month
        days = AnalysisPeriod.NUMOFDAYSEACHMONTH[m - 1]
        return AnalysisPeriod(m, 1, p.ex_st_hour, m, days, p.ex_end_hour, ts)
    if p.ex_mode == 'DAY':
        m = p.ex_month
        d = min(p.ex_day, AnalysisPeriod.NUMOFDAYSEACHMONTH[m - 1])
        return AnalysisPeriod(m, d, p.ex_st_hour, m, d, p.ex_end_hour, ts)
    return common.analysis_period(context)


def explore_period(context, objects=None):
    """Recolor cached results for the explorer period. Returns a summary string."""
    p = common.props(context)
    ap = explorer_period(context)
    label = common.period_label(context, ap)
    pool = objects if objects is not None else [
        o for o in bpy.data.objects
        if o.type == 'MESH' and o.get(STUDY_PROP) in STUDY_KINDS and o.get(SOURCE_PROP)
        and ' Legend' not in o.name]
    if p.st_show != 'ALL':
        pool = [o for o in pool if o.get(STUDY_PROP) == p.st_show]

    done, skipped, partial = [], [], []
    sky = None
    for study in STUDY_KINDS:
        items = []
        for ob in pool:
            if ob.get(STUDY_PROP) != study:
                continue
            cache = _cache_get(ob)
            if cache is None:
                skipped.append(ob.name)
                continue
            if cache['kind'] == 'sun':
                mask = _hoy_mask(cache['hoys'], ap)
                mtx = np.unpackbits(cache['matrix'], axis=1, count=cache['count'])
                values = np.sum(mtx[:, mask], axis=1) / cache['timestep']
                # daytime hours of the period that were never traced
                sp = common.sunpath(context)
                traced = set(np.round(cache['hoys'], 4).tolist())
                missing = [h for h in ap.hoys if round(h, 4) not in traced
                           and sp.calculate_sun_from_hoy(h).is_during_day]
                if missing:
                    partial.append(ob.name)
            else:
                if sky is None:
                    sky = common.sky_matrix(context, ap)
                if sky.patch_count != cache['patches']:
                    skipped.append(ob.name)
                    continue
                sky.north = cache['north']
                rad = intersect.radiation_from_intersection(sky, cache['matrix'])
                values = rad * 1000.0 / sky.wea_duration if study == 'Irradiance' else rad
            coll = bpy.data.collections.get('{} (LB)'.format(ob.get(SOURCE_PROP)))
            ob['lb_period'] = label
            items.append((ob, coll, values))
        if items:
            _colorize_batch(context, items, study, label)
            done.extend(o.name for o, _c, _v in items)
    apply_study_visibility(context)
    msg = '{}: {} result(s) updated'.format(label, len(done))
    if partial:
        msg += ' | {} with hours outside the traced range (enable Compute Full Year)'.format(
            len(partial))
    if skipped:
        msg += ' | {} without cache (run the study again)'.format(len(skipped))
    return msg, bool(done)


class LB_OT_explore_period(bpy.types.Operator):
    """Recolor cached results for the explorer period without ray tracing"""
    bl_idname = 'ladybug.explore_period'
    bl_label = 'Apply Period'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            msg, ok = explore_period(context)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO' if ok else 'WARNING'}, msg)
        return {'FINISHED'} if ok else {'CANCELLED'}


class _BatchProgress(object):
    """Progress bar spanning several objects."""

    def __init__(self, context, count):
        self.reporter = common.ProgressReporter(context)
        self.count = max(count, 1)
        self.index = 0

    def __call__(self, fraction):
        self.reporter((self.index + fraction) / self.count)

    def next(self):
        self.index += 1

    def end(self):
        self.reporter.end()


# ---------------------------------------------------------------------------
# studies
# ---------------------------------------------------------------------------
class LB_OT_direct_sun_hours(bpy.types.Operator):
    """Hours of direct sun on the selected meshes for the analysis period"""
    bl_idname = 'ladybug.direct_sun_hours'
    bl_label = 'Direct Sun Hours'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        try:
            sources = _source_objects(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        t0 = time.time()
        sp = common.sunpath(context)
        ap = common.analysis_period(context)
        # trace the whole year when asked, so the Period Explorer can show
        # any period later without ray tracing again
        trace_ap = AnalysisPeriod(timestep=ap.timestep) if p.st_cache_year else ap
        hoys, vectors = [], []
        for hoy in trace_ap.hoys:
            sun = sp.calculate_sun_from_hoy(hoy)
            if sun.is_during_day:
                v = sun.sun_vector_reversed
                vectors.append((v.x, v.y, v.z))
                hoys.append(hoy)
        if not vectors:
            self.report({'ERROR'}, 'No daytime hours in the analysis period')
            return {'CANCELLED'}
        vectors = np.array(vectors)
        hoys = np.array(hoys)
        period_mask = _hoy_mask(hoys, ap)
        if not period_mask.any():
            self.report({'ERROR'}, 'No daytime hours in the analysis period')
            return {'CANCELLED'}

        pool = _context_pool(context, sources)
        bvh_cache = {}
        progress = _BatchProgress(context, len(sources))
        items, n_sensors = [], 0
        try:
            for source in sources:
                result, coll = _make_result_object(context, source, 'Sun Hours',
                                                   p.st_offset)
                # fresh depsgraph: the result object was just created (and a
                # previous copy possibly removed)
                depsgraph = context.evaluated_depsgraph_get()
                bvh = _bvh_for(context, pool, source, depsgraph, bvh_cache)
                pts, nrs = intersect.study_points(result, depsgraph, p.st_by_vertex)
                mtx = intersect.intersection_matrix(
                    bvh, vectors, pts, nrs, 0.0, numericalize=False, progress=progress)
                # np.sum (not the .sum method) keeps working when another add-on
                # has reloaded numpy in-process (DeepBump), which breaks methods.
                hours = np.sum(mtx[:, period_mask], axis=1) / ap.timestep
                _store_metadata(context, result)
                _cache_put(result, 'sun', matrix=np.packbits(mtx, axis=1),
                           count=mtx.shape[1], hoys=hoys, timestep=ap.timestep)
                items.append((result, coll, hours))
                n_sensors += len(pts)
                progress.next()
        finally:
            progress.end()
        _colorize_batch(context, items, 'Sun Hours')
        _finish_batch(context, sources, [ob for ob, _c, _v in items])
        hours = np.concatenate([v for _o, _c, v in items])
        self.report({'INFO'}, '{} object(s), {} sensors x {} suns in {:.1f}s | avg {:.1f} h, max {:.1f} h'.format(
            len(items), n_sensors, len(vectors), time.time() - t0,
            float(np.mean(hours)), float(np.max(hours))))
        return {'FINISHED'}


class LB_OT_incident_radiation(bpy.types.Operator):
    """Cumulative solar radiation (kWh/m2) on the selected meshes for the period"""
    bl_idname = 'ladybug.incident_radiation'
    bl_label = 'Incident Radiation'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        try:
            sources = _source_objects(context)
            sky = common.sky_matrix(context)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        t0 = time.time()
        label = 'Irradiance' if p.st_irradiance else 'Radiation'
        unit = STUDY_KINDS[label][1]

        pool = _context_pool(context, sources)
        bvh_cache = {}
        progress = _BatchProgress(context, len(sources))
        items, n_sensors, total = [], 0, 0.0
        try:
            sky.progress = progress
            sky.data  # compute (cached afterwards)
            vectors = intersect.sky_vectors(sky)
            for source in sources:
                result, coll = _make_result_object(context, source, label, p.st_offset)
                depsgraph = context.evaluated_depsgraph_get()
                bvh = _bvh_for(context, pool, source, depsgraph, bvh_cache)
                pts, nrs = intersect.study_points(result, depsgraph, p.st_by_vertex)
                mtx = intersect.intersection_matrix(
                    bvh, vectors, pts, nrs, 0.0, numericalize=True, progress=progress)
                rad = intersect.radiation_from_intersection(sky, mtx)
                values = rad * 1000.0 / sky.wea_duration if p.st_irradiance else rad
                _store_metadata(context, result, sky)
                _cache_put(result, 'sky', matrix=mtx.astype(np.float32),
                           patches=sky.patch_count, north=sky.north)
                if not p.st_by_vertex and not p.st_irradiance:
                    areas = np.empty(len(result.data.polygons))
                    result.data.polygons.foreach_get('area', areas)
                    scale = np.array(result.matrix_world.to_scale())
                    areas *= abs(scale[0] * scale[1])
                    total += float(np.sum(values * areas))
                items.append((result, coll, values))
                n_sensors += len(pts)
                progress.next()
        finally:
            sky.progress = None
            progress.end()
        _colorize_batch(context, items, label)
        _finish_batch(context, sources, [ob for ob, _c, _v in items])
        values = np.concatenate([v for _o, _c, v in items])
        extra = ' | total {:.0f} kWh'.format(total) if total else ''
        self.report({'INFO'}, '{} object(s), {} sensors x {} patches in {:.1f}s | avg {:.1f} {}{}'.format(
            len(items), n_sensors, len(vectors), time.time() - t0,
            float(np.mean(values)), unit, extra))
        return {'FINISHED'}


class LB_OT_rebuild_legend(bpy.types.Operator):
    """Recolor existing results and redraw their legend with the current legend settings, without recomputing"""
    bl_idname = 'ladybug.rebuild_legend'
    bl_label = 'Rebuild Legend'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        # result copies in the selection (or of selected sources); else all of them
        selected = set()
        for ob in context.selected_objects:
            if ob.get(STUDY_PROP) and ob.type == 'MESH':
                selected.add(ob.name)
            elif ob.type == 'MESH':
                src = ob.get(SOURCE_PROP, ob.name)
                for r in bpy.data.objects:
                    if r.get(SOURCE_PROP) == src and r.get(STUDY_PROP) and r.type == 'MESH':
                        selected.add(r.name)
        pool = [bpy.data.objects[n] for n in selected] if selected else [
            o for o in bpy.data.objects
            if o.type == 'MESH' and o.get(STUDY_PROP) and o.get(SOURCE_PROP)]
        pool = [o for o in pool if o.get(STUDY_PROP) in STUDY_KINDS
                and o.name.find(' Legend') < 0]
        if p.st_show != 'ALL':
            pool = [o for o in pool if o.get(STUDY_PROP) == p.st_show]
        if not pool:
            self.report({'ERROR'}, 'No study results found to rebuild')
            return {'CANCELLED'}

        rebuilt = []
        for label in STUDY_KINDS:
            items = []
            for ob in pool:
                if ob.get(STUDY_PROP) != label:
                    continue
                vals = _stored_values(ob, label)
                if vals is None:
                    continue
                coll = bpy.data.collections.get('{} (LB)'.format(ob.get(SOURCE_PROP)))
                items.append((ob, coll, vals))
            if not items:
                continue
            period = items[0][0].get('lb_period') or None
            _colorize_batch(context, items, label, period)
            rebuilt.append('{} ({} objects)'.format(label, len(items)))
        apply_study_visibility(context)
        self.report({'INFO'}, 'Legend rebuilt: {}'.format(', '.join(rebuilt)))
        return {'FINISHED'}


class LB_OT_clear_results(bpy.types.Operator):
    """Delete study results (all of them, or only those of the selected objects)"""
    bl_idname = 'ladybug.clear_results'
    bl_label = 'Clear Results'
    bl_options = {'REGISTER', 'UNDO'}

    only_active: bpy.props.BoolProperty(
        name='Selected Only', default=False,
        description='Remove only the results of the selected objects')

    def execute(self, context):
        root = bpy.data.collections.get('LB {}'.format(RESULTS_COLLECTION))
        if root is None:
            self.report({'INFO'}, 'No results to clear')
            return {'CANCELLED'}
        if self.only_active:
            try:
                sources = _source_objects(context)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            cleared = []
            for src in sources:
                coll = bpy.data.collections.get('{} (LB)'.format(src.name))
                if coll is not None:
                    bg.clear_collection(coll, recursive=True)
                    bpy.data.collections.remove(coll)
                    cleared.append(src.name)
            if not cleared:
                self.report({'INFO'}, 'No results for the selected objects')
                return {'CANCELLED'}
            self.report({'INFO'}, 'Cleared results of {}'.format(', '.join(cleared)))
        else:
            bg.clear_collection(root, recursive=True)
            self.report({'INFO'}, 'All results cleared')
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# sensor grid
# ---------------------------------------------------------------------------
def _local_dims(ob):
    """Bounding box size of the mesh in local units."""
    xs, ys, zs = zip(*ob.bound_box)
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def _evaluated_face_count(context, ob):
    depsgraph = context.evaluated_depsgraph_get()
    ob_eval = ob.evaluated_get(depsgraph)
    me = ob_eval.to_mesh()
    try:
        return len(me.polygons)
    finally:
        ob_eval.to_mesh_clear()


def _max_edge_length(me):
    if len(me.edges) == 0:
        return 0.0
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    ev = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get('vertices', ev)
    ev = ev.reshape(-1, 2)
    d = np.linalg.norm(co[ev[:, 0]] - co[ev[:, 1]], axis=1)
    return float(np.max(d))


def add_sensor_grid(context, ob, cell_size):
    """Add (or retune) the analysis grid modifier of a mesh object.

    Solid meshes get a Remesh (Sharp) modifier whose octree depth gives cells
    close to ``cell_size``; open surfaces (planes, single faces), on which
    Remesh produces nothing, get a simple Subdivision instead.

    Returns (mode, evaluated_face_count).
    """
    scale = ob.matrix_world.to_scale()
    avg_scale = (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0 or 1.0
    cell_local = cell_size / avg_scale
    dims = _local_dims(ob)
    max_dim = max(dims) or cell_local

    mod = ob.modifiers.get(GRID_MODIFIER)
    if mod is not None:
        ob.modifiers.remove(mod)

    # Remesh Sharp gives about (2^depth - 1) cells across the bounding box;
    # pick the depth whose cell size is closest (in ratio) to the target
    def cell_at(d):
        return max_dim / (2 ** d - 1)
    depth = min(range(1, 11), key=lambda d: abs(math.log(cell_at(d) / cell_local)))
    mod = ob.modifiers.new(GRID_MODIFIER, 'REMESH')
    mod.mode = 'SHARP'
    mod.octree_depth = depth
    mod.use_remove_disconnected = False
    mod.show_in_editmode = False
    faces = _evaluated_face_count(context, ob)
    if faces > 0:
        return 'REMESH', faces

    # open surface: fall back to simple subdivision of the existing faces
    ob.modifiers.remove(mod)
    max_edge = _max_edge_length(ob.data) or cell_local
    levels = int(math.ceil(math.log2(max(max_edge / cell_local, 1.0))))
    levels = max(1, min(levels, 6))
    mod = ob.modifiers.new(GRID_MODIFIER, 'SUBSURF')
    mod.subdivision_type = 'SIMPLE'
    mod.levels = levels
    mod.render_levels = levels
    mod.show_in_editmode = False
    return 'SUBDIVIDE', _evaluated_face_count(context, ob)


class LB_OT_sensor_grid(bpy.types.Operator):
    """Add an analysis grid (Remesh) to the selected meshes with a target cell size"""
    bl_idname = 'ladybug.sensor_grid'
    bl_label = 'Add Sensor Grid'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        try:
            sources = _source_objects(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        total, fallback = 0, []
        for ob in sources:
            mode, faces = add_sensor_grid(context, ob, p.st_cell_size)
            total += faces
            if mode == 'SUBDIVIDE':
                fallback.append(ob.name)
        msg = '{} object(s): about {} sensors at {:g} m'.format(
            len(sources), total, p.st_cell_size)
        if fallback:
            msg += ' | open surfaces subdivided instead of remeshed: {}'.format(
                ', '.join(fallback))
        if total > SENSOR_WARNING:
            self.report({'WARNING'}, msg + ' | large grid, studies may take a while')
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class LB_OT_remove_sensor_grid(bpy.types.Operator):
    """Remove the analysis grid modifier from the selected meshes"""
    bl_idname = 'ladybug.remove_sensor_grid'
    bl_label = 'Remove Sensor Grid'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            sources = _source_objects(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        n = 0
        for ob in sources:
            mod = ob.modifiers.get(GRID_MODIFIER)
            if mod is not None:
                ob.modifiers.remove(mod)
                n += 1
        self.report({'INFO'}, 'Removed grid from {} object(s)'.format(n))
        return {'FINISHED'}


CLASSES = (LB_OT_direct_sun_hours, LB_OT_incident_radiation, LB_OT_rebuild_legend,
           LB_OT_explore_period, LB_OT_clear_results, LB_OT_sensor_grid,
           LB_OT_remove_sensor_grid)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

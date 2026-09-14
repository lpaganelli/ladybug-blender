# -*- coding: utf-8 -*-
"""EnergyPlus simulation of the Honeybee model and result display."""
import os

import bpy
import numpy as np

from ladybug.datatype.temperature import Temperature
from ladybug.datatype.time import Time
from ladybug.datatype.fraction import Fraction
from ladybug.legend import Legend


from . import common
from .honeybee import _LAST_MODEL
from ..core import blender_geom as bg

_LAST_RUN = {'sql': None, 'err': None, 'summary': None, 'collections': None, 'kinds': None}

METRICS = {
    'mean': ('Mean Operative Temperature', Temperature, 'C', 'mean'),
    'max': ('Max Operative Temperature', Temperature, 'C', 'max'),
    'min': ('Min Operative Temperature', Temperature, 'C', 'min'),
    'hours_hot': ('Hours Above Comfort', Time, 'hr', 'hours_hot'),
    'hours_cold': ('Hours Below Comfort', Time, 'hr', 'hours_cold'),
    'pct_comfort': ('Comfortable Hours', Fraction, '%', 'pct_comfort'),
}


def find_energyplus():
    """Newest EnergyPlus install folder on this machine, or ''."""
    candidates = []
    for root in ('C:\\', '/usr/local', '/Applications'):
        try:
            for name in os.listdir(root):
                if name.lower().startswith('energyplus'):
                    candidates.append(os.path.join(root, name))
        except OSError:
            pass
    candidates = [c for c in candidates if os.path.isfile(os.path.join(c, 'energyplus.exe'))
                  or os.path.isfile(os.path.join(c, 'energyplus'))]
    return sorted(candidates)[-1] if candidates else ''


def _scene_context_faces(context):
    """World-space polygons of 'HB Context' and any mesh tagged hb_shade, or None."""
    objs = [o for o in bpy.data.objects if o.type == 'MESH' and
            (o.name.startswith('HB Context') or o.get('hb_shade'))]
    if not objs:
        return None
    from ..core.intersect import gather_world_polygons
    depsgraph = context.evaluated_depsgraph_get()
    faces = []
    for ob in objs:
        if ob.hide_get():
            continue
        verts, polys = gather_world_polygons([ob], depsgraph)
        faces.extend([verts[i] for i in poly] for poly in polys)
    return faces


class LB_OT_energy_simulate(bpy.types.Operator):
    """Run an annual EnergyPlus simulation of the last Honeybee model (blocks Blender for a few minutes)"""
    bl_idname = 'ladybug.energy_simulate'
    bl_label = 'Simulate (EnergyPlus)'
    bl_options = {'REGISTER'}

    def execute(self, context):
        p = common.props(context)
        model = _LAST_MODEL['model']
        if model is None:
            self.report({'ERROR'}, 'Run IFC to Honeybee first')
            return {'CANCELLED'}
        ep = bpy.path.abspath(p.en_ep_path) or find_energyplus()
        if not ep or not os.path.isdir(ep):
            self.report({'ERROR'}, 'EnergyPlus folder not found; set it in the panel')
            return {'CANCELLED'}
        epw = common.epw_path(context)
        if not epw or not os.path.isfile(epw):
            self.report({'ERROR'}, 'Load an EPW weather file first')
            return {'CANCELLED'}
        from ..core import energy_sim
        folder = bpy.path.abspath(p.en_folder) if p.en_folder else os.path.join(
            os.path.dirname(_LAST_MODEL['path'] or epw), 'energyplus_run')
        progress = common.ProgressReporter(context)
        try:
            progress(0.05)
            # context shades come from the scene, so "HB Context" (and any mesh
            # tagged hb_shade) can be edited by hand before simulating
            scene_ctx = _scene_context_faces(context)
            if scene_ctx is not None:
                model.remove_shades()  # orphaned context only; room shades untouched
                model.add_shades(energy_sim.scene_shades_from_faces(scene_ctx))
            kinds = energy_sim.prepare_model(
                model, hvac=p.en_hvac, vent_min_indoor=p.en_vent_min_indoor,
                vent_min_outdoor=p.en_vent_min_outdoor, vent_max_outdoor=p.en_vent_max_outdoor,
                operable_fraction=p.en_operable_default,
                window_openings=energy_sim.parse_window_openings(p.en_window_openings))
            progress(0.1)
            run_period = None
            days = os.environ.get('LB_ENERGY_DAYS')  # short runs for automated tests
            if days:
                from honeybee_energy.simulation.runperiod import RunPeriod
                from ladybug.dt import Date
                run_period = RunPeriod(Date(1, 1), Date(1, min(int(days), 31)))
            sql, err, secs = energy_sim.run(model, epw, folder, ep, timestep=p.en_timestep,
                                            run_period=run_period, north=p.north)
            progress(0.9)
            colls, summary = energy_sim.read_results(
                sql, model, comfort_low=p.en_comfort_low, comfort_high=p.en_comfort_high)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, str(exc)[:500])
            return {'CANCELLED'}
        finally:
            progress.end()
        _LAST_RUN.update(sql=sql, err=err, summary=summary, collections=colls, kinds=kinds)
        errs = energy_sim.read_err_summary(err)
        for m in errs['messages']:
            print('[Ladybug] EnergyPlus:', m)
        if not summary:
            self.report({'ERROR'}, 'EnergyPlus produced no zone results; see {} '
                                   '({} severe)'.format(err, errs['severe']))
            return {'CANCELLED'}
        color_rooms(context, p.en_metric)
        worst = max(summary.values(), key=lambda s: s['hours_hot'])
        self.report({'INFO'}, '{} zones in {:.0f}s | {} warnings, {} severe | hottest: {} ({} h > {:g} C)'.format(
            len(summary), secs, errs['warnings'], errs['severe'], worst['name'],
            worst['hours_hot'], p.en_comfort_high))
        return {'FINISHED'}


def color_rooms(context, metric_key):
    """Color the HB room objects by a per-room result and draw a legend."""
    summary = _LAST_RUN['summary']
    if not summary:
        return 0
    p = common.props(context)
    title, dtype, unit, field = METRICS[metric_key]
    rooms = {o['hb_room']: o for o in bpy.data.objects
             if o.get('hb_room') and o.type == 'MESH' and 'openings' not in o.name}
    items = [(rooms[k], s[field]) for k, s in summary.items() if k in rooms]
    if not items:
        return 0
    values = [v for _o, v in items]
    l_par = common.legend_parameters(context, title=unit)
    if l_par.min is None:
        l_par.min = float(min(values))
    if l_par.max is None:
        l_par.max = float(max(values))
    if l_par.max <= l_par.min:
        l_par.max = l_par.min + 1.0
    l_par.decimal_count = 1 if (l_par.max - l_par.min) < 50 else 0
    seg_h = p.lg_size / l_par.segment_count
    l_par.segment_height, l_par.segment_width, l_par.text_height = seg_h, seg_h * 0.6, seg_h * 0.35
    from .studies import _legend_plane  # same placement rules as the solar studies
    l_par.base_plane = _legend_plane(context, [(o, None, v) for o, v in items])
    legend = Legend(values, l_par)
    rng = legend.color_range
    for ob, v in items:
        color = bg.lb_color(rng.color(float(v)))
        n = len(ob.data.polygons)
        bg.set_color_attribute(ob.data, 'LB Color', [color] * n, 'FACE')
        ob['hb_' + field] = float(v)
    coll = bg.get_collection(context, 'Honeybee')
    bg.remove_objects_by_prefix(coll, 'HB Energy Legend')
    if p.lg_show:
        for lo in bg.legend_objects(legend, 'HB Energy Legend', coll,
                                    title='{} ({})\n{}'.format(title, unit, dtype().name)):
            lo['ladybug_study'] = 'Energy'
    bg.show_attribute_colors(context)
    return len(items)


class LB_OT_energy_color(bpy.types.Operator):
    """Recolor the rooms by another result metric"""
    bl_idname = 'ladybug.energy_color'
    bl_label = 'Show Metric'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = color_rooms(context, common.props(context).en_metric)
        if not n:
            self.report({'ERROR'}, 'No simulation results (run Simulate first)')
            return {'CANCELLED'}
        self.report({'INFO'}, '{} rooms colored'.format(n))
        return {'FINISHED'}


class LB_OT_energy_report(bpy.types.Operator):
    """Print the per-room summary to the Info editor and console"""
    bl_idname = 'ladybug.energy_report'
    bl_label = 'Report'

    def execute(self, context):
        summary = _LAST_RUN['summary']
        if not summary:
            self.report({'ERROR'}, 'No simulation results (run Simulate first)')
            return {'CANCELLED'}
        p = common.props(context)
        kinds = _LAST_RUN['kinds'] or {}
        for ident, s in sorted(summary.items(), key=lambda kv: -kv[1]['hours_hot']):
            line = '{:<20s} {:<8s} mean {:4.1f} C  min {:4.1f}  max {:4.1f} | {:4d} h > {:g} C, {:4d} h < {:g} C, {:5.1f}% ok'.format(
                s['name'][:20], kinds.get(ident, ''), s['mean'], s['min'], s['max'],
                s['hours_hot'], p.en_comfort_high, s['hours_cold'], p.en_comfort_low,
                s['pct_comfort'])
            print('[Ladybug]', line)
            self.report({'INFO'}, line)
        return {'FINISHED'}


CLASSES = (LB_OT_energy_simulate, LB_OT_energy_color, LB_OT_energy_report)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

# -*- coding: utf-8 -*-
"""EPW loading and inspection operators."""
import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import common
from ..core import cache


class LB_OT_load_epw(bpy.types.Operator, ImportHelper):
    """Choose an EPW weather file and read its location"""
    bl_idname = 'ladybug.load_epw'
    bl_label = 'Load EPW'
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = '.epw'
    filter_glob: StringProperty(default='*.epw', options={'HIDDEN'})

    def execute(self, context):
        p = common.props(context)
        p.epw_path = self.filepath  # triggers the update callback
        if not p.epw_loaded:
            self.report({'ERROR'}, 'Could not read EPW file')
            return {'CANCELLED'}
        self.report({'INFO'}, 'Loaded {} ({:.2f}, {:.2f})'.format(
            p.city, p.latitude, p.longitude))
        return {'FINISHED'}


class LB_OT_reload_epw(bpy.types.Operator):
    """Re-read the EPW file and clear cached sky matrices"""
    bl_idname = 'ladybug.reload_epw'
    bl_label = 'Reload'

    def execute(self, context):
        cache.clear()
        p = common.props(context)
        p.epw_path = p.epw_path
        if p.epw_loaded:
            self.report({'INFO'}, 'EPW reloaded')
            return {'FINISHED'}
        self.report({'ERROR'}, 'Could not read EPW file')
        return {'CANCELLED'}


class LB_OT_location_from_epw(bpy.types.Operator):
    """Set the location back to the EPW header"""
    bl_idname = 'ladybug.location_from_epw'
    bl_label = 'From EPW'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        if not p.epw_path:
            self.report({'ERROR'}, 'No EPW loaded')
            return {'CANCELLED'}
        p.epw_path = p.epw_path  # re-runs the header read
        if not p.epw_loaded:
            self.report({'ERROR'}, 'Could not read EPW file')
            return {'CANCELLED'}
        self.report({'INFO'}, 'Location from EPW: {} ({:.2f}, {:.2f})'.format(p.city, p.latitude, p.longitude))
        return {'FINISHED'}


class LB_OT_epw_summary(bpy.types.Operator):
    """Print a summary of the EPW data to the Info editor and console"""
    bl_idname = 'ladybug.epw_summary'
    bl_label = 'EPW Summary'

    def execute(self, context):
        try:
            epw = common.get_epw(context)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        ap = common.analysis_period(context, 1)
        dbt = epw.dry_bulb_temperature.filter_by_analysis_period(ap)
        rh = epw.relative_humidity.filter_by_analysis_period(ap)
        ws = epw.wind_speed.filter_by_analysis_period(ap)
        ghr = epw.global_horizontal_radiation.filter_by_analysis_period(ap)
        dnr = epw.direct_normal_radiation.filter_by_analysis_period(ap)
        lines = [
            '{} {} | lat {:.2f} lon {:.2f} tz {} | elev {:.0f} m | {}'.format(
                epw.location.city, epw.location.country, epw.location.latitude,
                epw.location.longitude, epw.location.time_zone,
                epw.location.elevation, common.period_label(context)),
            'Source: {} | station {}'.format(
                epw.location.source or '?', epw.location.station_id or '?'),
            'Dry bulb: min {:.1f} / avg {:.1f} / max {:.1f} C'.format(
                dbt.min, dbt.average, dbt.max),
            'Relative humidity: avg {:.0f} %'.format(rh.average),
            'Wind speed: avg {:.1f} / max {:.1f} m/s'.format(ws.average, ws.max),
            'Radiation: global horizontal {:.0f} kWh/m2, direct normal {:.0f} kWh/m2'.format(
                ghr.total / 1000.0, dnr.total / 1000.0),
        ]
        # which real years were stitched into this typical year, month by month
        try:
            years = epw.years.values
            per_month = []
            for m in range(1, 13):
                ys = sorted({int(y) for y, dt in zip(years, epw.years.datetimes)
                             if dt.month == m})
                per_month.append('/'.join(str(y) for y in ys) if ys else '?')
            if len(set(per_month)) > 1:
                lines.append('Years by month (Jan-Dec): ' + ', '.join(per_month))
            else:
                lines.append('Year: {}'.format(per_month[0]))
        except Exception:  # noqa: BLE001
            pass
        # COMMENTS 1/2 usually say where the radiation comes from (measured, ERA5...)
        for label, text in (('Comments 1', epw.comments_1), ('Comments 2', epw.comments_2)):
            text = (text or '').strip()
            if text:
                lines.append('{}: {}'.format(label, text[:400]))
        for ln in lines:
            print('[Ladybug]', ln)
            self.report({'INFO'}, ln)
        return {'FINISHED'}


PRESETS = [
    ('YEAR', 'Year', 'Whole year, every hour'),
    ('SUMMER_SOLSTICE', 'Summer Solstice', 'Longest day of the year at this latitude'),
    ('WINTER_SOLSTICE', 'Winter Solstice', 'Shortest day of the year at this latitude'),
    ('EQUINOX', 'Equinox', 'March 21'),
    ('SUMMER', 'Summer', 'Three summer months at this latitude'),
    ('WINTER', 'Winter', 'Three winter months at this latitude'),
]


class LB_OT_period_preset(bpy.types.Operator):
    """Set the analysis period to a common preset (hemisphere-aware)"""
    bl_idname = 'ladybug.period_preset'
    bl_label = 'Period Preset'
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(name='Preset', items=PRESETS, default='YEAR')

    def execute(self, context):
        p = common.props(context)
        south = p.latitude < 0
        if self.preset == 'YEAR':
            st, end = (1, 1), (12, 31)
        elif self.preset == 'SUMMER_SOLSTICE':
            st = end = (12, 21) if south else (6, 21)
        elif self.preset == 'WINTER_SOLSTICE':
            st = end = (6, 21) if south else (12, 21)
        elif self.preset == 'EQUINOX':
            st = end = (3, 21)
        elif self.preset == 'SUMMER':
            st, end = ((12, 1), (2, 28)) if south else ((6, 1), (8, 31))
        else:  # WINTER
            st, end = ((6, 1), (8, 31)) if south else ((12, 1), (2, 28))
        p.ap_st_month, p.ap_st_day = str(st[0]), st[1]
        p.ap_end_month, p.ap_end_day = str(end[0]), end[1]
        p.ap_st_hour, p.ap_end_hour = 0, 23
        self.report({'INFO'}, 'Period: {}'.format(common.period_label(context)))
        return {'FINISHED'}


CLASSES = (LB_OT_load_epw, LB_OT_reload_epw, LB_OT_location_from_epw, LB_OT_epw_summary,
           LB_OT_period_preset)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

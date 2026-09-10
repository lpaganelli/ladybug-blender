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
        lines = [
            '{} {} | lat {:.2f} lon {:.2f} tz {} | {}'.format(
                epw.location.city, epw.location.country, epw.location.latitude,
                epw.location.longitude, epw.location.time_zone,
                common.period_label(context)),
            'Dry bulb: min {:.1f} / avg {:.1f} / max {:.1f} C'.format(
                dbt.min, dbt.average, dbt.max),
            'Relative humidity: avg {:.0f} %'.format(rh.average),
            'Wind speed: avg {:.1f} / max {:.1f} m/s'.format(ws.average, ws.max),
            'Global horizontal radiation: {:.0f} kWh/m2'.format(ghr.total / 1000.0),
        ]
        for ln in lines:
            print('[Ladybug]', ln)
            self.report({'INFO'}, ln)
        return {'FINISHED'}


CLASSES = (LB_OT_load_epw, LB_OT_reload_epw, LB_OT_epw_summary)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

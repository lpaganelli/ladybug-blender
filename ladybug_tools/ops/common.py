# -*- coding: utf-8 -*-
"""Shared helpers for operators."""
import bpy

from ladybug.analysisperiod import AnalysisPeriod
from ladybug.color import Colorset
from ladybug.legend import LegendParameters
from ladybug.location import Location
from ladybug.sunpath import Sunpath
from ladybug_geometry.geometry3d import Point3D

from ..core import cache


def props(context):
    return context.scene.ladybug


def epw_path(context):
    p = props(context)
    path = bpy.path.abspath(p.epw_path) if p.epw_path else ''
    return path


def get_epw(context):
    """Parsed EPW or raise a ValueError with a user-friendly message."""
    path = epw_path(context)
    if not path:
        raise ValueError('Select an EPW weather file first')
    return cache.get_epw(path)


def location(context):
    """ladybug Location from the scene properties (EPW or manual)."""
    p = props(context)
    return Location(p.city or 'Site', '', p.country or '', p.latitude, p.longitude,
                    p.time_zone, p.elevation)


def sunpath(context):
    p = props(context)
    return Sunpath.from_location(location(context), north_angle=p.north)


def analysis_period(context, timestep=None):
    p = props(context)
    ts = int(p.ap_timestep) if timestep is None else timestep
    return AnalysisPeriod(int(p.ap_st_month), p.ap_st_day, p.ap_st_hour,
                          int(p.ap_end_month), p.ap_end_day, p.ap_end_hour, ts)


def period_label(context, ap=None):
    if ap is None:
        ap = analysis_period(context, 1)
    return '{}/{} {}h - {}/{} {}h'.format(ap.st_day, ap.st_month, ap.st_hour,
                                          ap.end_day, ap.end_month, ap.end_hour)


def legend_parameters(context, title=None, default_colors=None):
    """LegendParameters built from the scene legend settings."""
    p = props(context)
    colors = getattr(Colorset, p.lg_colorset)() if p.lg_colorset != 'original' \
        else default_colors
    lmin = p.lg_min if p.lg_use_range else None
    lmax = p.lg_max if p.lg_use_range else None
    lp = LegendParameters(min=lmin, max=lmax, segment_count=p.lg_segments,
                          colors=colors, title=title)
    return lp


def cursor_point(context):
    c = context.scene.cursor.location
    return Point3D(c.x, c.y, c.z)


def sky_matrix(context, ap=None):
    """Native sky matrix for the current settings (cached).

    ``ap`` overrides the analysis period of the panel.
    """
    from ..core.skymatrix import SkyMatrix
    p = props(context)
    if ap is None:
        ap = analysis_period(context)
    hoys = None if ap.is_annual else tuple(ap.hoys)
    if p.st_sky == 'EPW':
        path = epw_path(context)
        if not path:
            raise ValueError('Select an EPW weather file or use the Clear Sky option')
        key = ('EPW', path, cache._stamp(path), hoys, ap.timestep, p.st_high_density)

        def factory():
            wea = cache.get_wea(path, ap.timestep)
            return SkyMatrix.from_wea(wea, hoys, 0, p.st_high_density)
    else:
        loc = location(context)
        key = ('CLEAR', loc.latitude, loc.longitude, loc.time_zone, p.st_clearness,
               hoys, ap.timestep, p.st_high_density)

        def factory():
            return SkyMatrix.from_ashrae_clear_sky(
                loc, p.st_clearness, hoys, 0, p.st_high_density, timestep=ap.timestep)
    sky = cache.get_sky(key, factory)
    sky.north = p.north
    sky.ground_reflectance = p.st_ground_reflectance
    return sky


class ProgressReporter(object):
    """Window-manager progress bar wrapper usable as a callback."""

    def __init__(self, context, steps=100):
        self.wm = context.window_manager
        self.steps = steps
        self.active = False
        try:
            self.wm.progress_begin(0, steps)
            self.active = True
        except Exception:
            self.active = False

    def __call__(self, fraction):
        if self.active:
            self.wm.progress_update(int(fraction * self.steps))

    def end(self):
        if self.active:
            self.wm.progress_end()

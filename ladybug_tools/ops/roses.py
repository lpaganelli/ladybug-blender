# -*- coding: utf-8 -*-
"""Climate graphics: sky dome, radiation rose and wind rose."""
import math

import bpy

from ladybug.windrose import WindRose
from ladybug_geometry.geometry2d import Point2D
from ladybug_geometry.geometry3d import Plane, Point3D, Vector3D
from ladybug_radiance.visualize.skydome import SkyDome
from ladybug_radiance.visualize.radrose import RadiationRose

from . import common
from ..core import blender_geom as bg


def _place_legend(context, legend, center, radius):
    """Size and place a graphic's legend beside it, following the Legend panel."""
    p = common.props(context)
    l_par = legend.legend_parameters
    seg_h = p.lg_size / l_par.segment_count
    l_par.segment_height = seg_h
    l_par.segment_width = seg_h * 0.6
    l_par.text_height = seg_h * 0.35
    if p.lg_position == 'CURSOR':
        origin = Point3D(center.x + radius * 1.3, center.y - radius, center.z)
    else:
        origin = Point3D(center.x + radius * 1.3, center.y - radius, center.z)
    if p.lg_orientation == 'UPRIGHT':
        l_par.base_plane = Plane(n=Vector3D(0, -1, 0), o=origin, x=Vector3D(1, 0, 0))
    else:
        l_par.base_plane = Plane(o=origin)
    return legend


class LB_OT_sky_dome(bpy.types.Operator):
    """Draw the sky matrix as a colored dome at the 3D cursor"""
    bl_idname = 'ladybug.sky_dome'
    bl_label = 'Sky Dome'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        try:
            sky = common.sky_matrix(context)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        center = common.cursor_point(context)
        projection = None if p.dome_projection == 'NONE' else p.dome_projection
        l_par = common.legend_parameters(context)
        dome = SkyDome(sky, l_par, p.st_irradiance, center, p.viz_radius, projection)
        mesh, compass, graphic, title, values = dome.draw(p.rad_type)
        coll = bg.get_collection(context, 'Sky Dome', clear=True)
        bg.mesh_from_lb(mesh, 'LB Sky Dome', coll)
        bg.compass_objects(compass, 'LB Sky Dome', coll, z=center.z)
        if p.lg_show:
            legend = _place_legend(context, graphic.legend, center, p.viz_radius)
            bg.legend_objects(legend, 'LB Sky Dome', coll, title=title)
        bg.show_attribute_colors(context)
        self.report({'INFO'}, 'Sky dome: max patch {:.1f} {}'.format(
            max(values), 'W/m2' if p.st_irradiance else 'kWh/m2'))
        return {'FINISHED'}


class LB_OT_radiation_rose(bpy.types.Operator):
    """Draw a radiation rose (radiation by orientation) at the 3D cursor"""
    bl_idname = 'ladybug.radiation_rose'
    bl_label = 'Radiation Rose'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        try:
            sky = common.sky_matrix(context)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        center = common.cursor_point(context)
        l_par = common.legend_parameters(context)
        rose = RadiationRose(sky, None, p.rr_directions, p.rr_tilt, l_par,
                             p.st_irradiance, center, p.viz_radius)
        mesh, lines, compass, graphic, title = rose.draw(p.rad_type)
        coll = bg.get_collection(context, 'Radiation Rose', clear=True)
        bg.mesh_from_lb(mesh, 'LB Radiation Rose', coll)
        bg.curve_object('LB Radiation Rose Lines',
                        [bg.lb_segment_pts(s) for s in lines], coll,
                        material=bg.simple_material('LB Compass', (0.25, 0.25, 0.25, 1)))
        bg.compass_objects(compass, 'LB Radiation Rose', coll, z=center.z)
        if p.lg_show:
            legend = _place_legend(context, graphic.legend, center, p.viz_radius)
            bg.legend_objects(legend, 'LB Radiation Rose', coll, title=title)
        bg.show_attribute_colors(context)
        vals = rose.total_values
        best = max(range(len(vals)), key=lambda i: vals[i])
        self.report({'INFO'}, 'Max {:.1f} at direction {} of {}'.format(
            vals[best], best, p.rr_directions))
        return {'FINISHED'}


class LB_OT_wind_rose(bpy.types.Operator):
    """Draw a wind rose from the EPW wind data at the 3D cursor"""
    bl_idname = 'ladybug.wind_rose'
    bl_label = 'Wind Rose'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        try:
            epw = common.get_epw(context)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        ap = common.analysis_period(context, 1)
        wdir = epw.wind_direction.filter_by_analysis_period(ap)
        data = getattr(epw, p.wr_data).filter_by_analysis_period(ap)
        center = common.cursor_point(context)
        rose = WindRose(wdir, data, p.wr_directions)
        rose.legend_parameters = common.legend_parameters(context)
        # the calm rose (zero-speed hours) only exists for wind speed data
        rose.show_zeros = p.wr_show_calm and p.wr_data == 'wind_speed'
        rose.show_freq = p.wr_show_freq
        rose.north = p.north
        rose.base_point = Point2D(center.x, center.y)
        # bin the frequencies so the busiest direction spans wr_rings rings,
        # whatever the period (a single day has at most 24 hours per direction)
        max_freq = max(rose.real_freq_max, 1)
        rose.frequency_hours = max(1, math.ceil(max_freq / p.wr_rings))
        intervals = max(1, math.ceil(max_freq / rose.frequency_hours))
        rose.frequency_intervals_compass = intervals
        # scale the frequency spacing so the compass radius matches viz_radius
        theta = math.pi / p.wr_directions
        rose.frequency_spacing_distance = p.viz_radius * math.cos(theta) / intervals
        mesh2d = rose.colored_mesh
        coll = bg.get_collection(context, 'Wind Rose', clear=True)
        bg.mesh_from_mesh2d(mesh2d, 'LB Wind Rose', coll, z=center.z)
        lines = [bg.lb_segment_pts(s, center.z) for s in rose.orientation_lines]
        closed = [False] * len(lines)
        for poly in rose.frequency_lines:
            lines.append(bg._pts3(poly.vertices, center.z))
            closed.append(True)
        bg.curve_object('LB Wind Rose Lines', lines, coll, closed,
                        material=bg.simple_material('LB Compass', (0.25, 0.25, 0.25, 1)))
        bg.compass_objects(rose.compass, 'LB Wind Rose', coll, z=center.z)
        if p.lg_show:
            title = '{}\n{}\n{}'.format(
                data.header.data_type.name, epw.location.city,
                common.period_label(context))
            legend = _place_legend(context, rose.legend, center, p.viz_radius)
            bg.legend_objects(legend, 'LB Wind Rose', coll, title=title)
        bg.show_attribute_colors(context)
        prevailing = ', '.join('{:g}'.format(d) for d in rose.prevailing_direction)
        self.report({'INFO'}, 'Prevailing direction: {} deg | calm hours: {}'.format(
            prevailing, rose.zero_count))
        return {'FINISHED'}


CLASSES = (LB_OT_sky_dome, LB_OT_radiation_rose, LB_OT_wind_rose)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

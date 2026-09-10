# -*- coding: utf-8 -*-
"""Sun path drawing and sun light positioning."""
import math

import bpy
from mathutils import Vector

from ladybug.compass import Compass
from ladybug.dt import DateTime
from ladybug_geometry.geometry2d import Point2D

from . import common
from ..core import blender_geom as bg

SUN_LIGHT = 'LB Sun'
SUN_MARKER = 'LB Sun Marker'


def _sun_for_props(context):
    p = common.props(context)
    sp = common.sunpath(context)
    hour = int(p.sun_hour)
    minute = int(round((p.sun_hour - hour) * 60))
    dt = DateTime(int(p.sun_month), p.sun_day, hour, min(minute, 59))
    return sp.calculate_sun_from_date_time(dt, p.sp_solar_time), dt


def _place_sun_light(context, sun, origin, radius, strength):
    """Create/update the sun light and marker for a ladybug Sun."""
    coll = bg.get_collection(context, 'Sun')
    light_ob = bpy.data.objects.get(SUN_LIGHT)
    if light_ob is None or light_ob.type != 'LIGHT':
        light = bpy.data.lights.new(SUN_LIGHT, 'SUN')
        light_ob = bpy.data.objects.new(SUN_LIGHT, light)
        coll.objects.link(light_ob)
        bg.tag_generated(light_ob)
    light_ob.data.energy = strength
    light_ob.data.angle = math.radians(0.53)
    vec = sun.sun_vector  # points from the sun down to the scene
    direction = Vector((vec.x, vec.y, vec.z))
    pos = sun.position_3d(origin, radius)
    light_ob.location = (pos.x, pos.y, pos.z)
    light_ob.rotation_mode = 'QUATERNION'
    light_ob.rotation_quaternion = direction.to_track_quat('-Z', 'Y')
    light_ob.hide_viewport = not sun.is_during_day

    marker = bpy.data.objects.get(SUN_MARKER)
    if marker is None:
        me = bpy.data.meshes.new(SUN_MARKER)
        import bmesh
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.0)
        bm.to_mesh(me)
        bm.free()
        marker = bpy.data.objects.new(SUN_MARKER, me)
        coll.objects.link(marker)
        bg.tag_generated(marker)
        bg.assign_material(marker, bg.simple_material('LB Sun', (1.0, 0.75, 0.1, 1.0), 3.0))
    marker.location = (pos.x, pos.y, pos.z)
    s = radius * 0.03
    marker.scale = (s, s, s)
    marker.hide_viewport = not sun.is_during_day
    marker.hide_render = not sun.is_during_day
    return light_ob, marker


class LB_OT_draw_sunpath(bpy.types.Operator):
    """Draw the sun path (analemmas, day arcs, compass, suns) at the 3D cursor"""
    bl_idname = 'ladybug.draw_sunpath'
    bl_label = 'Draw Sun Path'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        sp = common.sunpath(context)
        origin = common.cursor_point(context)
        radius = p.sp_radius
        coll = bg.get_collection(context, 'Sun Path', clear=True)
        bevel = p.sp_bevel

        # hourly analemmas
        if p.sp_analemmas:
            plines = sp.hourly_analemma_polyline3d(
                origin, radius, p.sp_daytime_only, p.sp_solar_time)
            pts = [bg.lb_polyline_pts(pl) for pl in plines]
            bg.curve_object('LB Analemmas', pts, coll, bevel=bevel,
                            material=bg.simple_material('LB Analemma', (0.55, 0.55, 0.6, 1)))

        # day arcs: solstices + equinox always, all months optional
        if p.sp_day_arcs:
            arcs, closed = [], []
            for month in range(1, 13):
                arc = sp.day_arc3d(month, 21, origin, radius, daytime_only=True)
                if arc is None:
                    continue
                pts, cyc = bg.lb_arc_pts(arc, 96)
                arcs.append(pts)
                closed.append(cyc)
            if arcs:
                bg.curve_object('LB Day Arcs', arcs, coll, closed, bevel=bevel,
                                material=bg.simple_material('LB Day Arc', (0.95, 0.55, 0.1, 1)))

        # compass
        if p.sp_compass:
            compass = Compass(radius, Point2D(origin.x, origin.y), p.north)
            bg.compass_objects(compass, 'LB Sun Path', coll, z=origin.z)

        # sun points for the analysis period (dupli-verts of a small sphere)
        if p.sp_suns:
            ap = common.analysis_period(context, 1)
            verts = []
            for hoy in ap.hoys[::p.sp_sun_step]:
                sun = sp.calculate_sun_from_hoy(hoy, p.sp_solar_time)
                if sun.is_during_day or not p.sp_daytime_only:
                    pt = sun.position_3d(origin, radius)
                    verts.append((pt.x, pt.y, pt.z))
            if verts:
                me = bpy.data.meshes.new('LB Suns')
                me.from_pydata(verts, [], [])
                suns = bpy.data.objects.new('LB Suns', me)
                coll.objects.link(suns)
                bg.tag_generated(suns)
                suns.instance_type = 'VERTS'
                import bmesh
                sphere = bpy.data.meshes.new('LB Sun Point')
                bm = bmesh.new()
                bmesh.ops.create_icosphere(bm, subdivisions=1, radius=radius * 0.012)
                bm.to_mesh(sphere)
                bm.free()
                pt_ob = bpy.data.objects.new('LB Sun Point', sphere)
                coll.objects.link(pt_ob)
                bg.tag_generated(pt_ob)
                pt_ob.parent = suns
                bg.assign_material(pt_ob, bg.simple_material('LB Sun', (1.0, 0.75, 0.1, 1.0), 3.0))

        # position the sun light for the currently chosen date/time
        sun, _ = _sun_for_props(context)
        _place_sun_light(context, sun, origin, radius, p.sun_strength)

        self.report({'INFO'}, 'Sun path drawn for lat {:.2f}, lon {:.2f}'.format(
            p.latitude, p.longitude))
        return {'FINISHED'}


class LB_OT_set_sun(bpy.types.Operator):
    """Aim a Sun light at the scene for the chosen date and time"""
    bl_idname = 'ladybug.set_sun'
    bl_label = 'Set Sun Light'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        sun, dt = _sun_for_props(context)
        origin = common.cursor_point(context)
        _place_sun_light(context, sun, origin, p.sp_radius, p.sun_strength)
        msg = '{} -> altitude {:.1f}, azimuth {:.1f}'.format(
            dt, sun.altitude, sun.azimuth)
        if not sun.is_during_day:
            msg += ' (sun below horizon)'
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class LB_OT_animate_sun(bpy.types.Operator):
    """Keyframe the Sun light across the chosen day (sunrise to sunset)"""
    bl_idname = 'ladybug.animate_sun'
    bl_label = 'Animate Sun (Day)'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        sp = common.sunpath(context)
        origin = common.cursor_point(context)
        month, day = int(p.sun_month), p.sun_day
        fph = p.anim_frames_per_hour
        light_ob = None
        frame = 1
        first = None
        for step in range(24 * fph + 1):
            hour = step / fph
            h = int(hour)
            m = int(round((hour - h) * 60))
            if h > 23:
                break
            sun = sp.calculate_sun_from_date_time(
                DateTime(month, day, h, min(m, 59)), p.sp_solar_time)
            if not sun.is_during_day:
                continue
            light_ob, marker = _place_sun_light(context, sun, origin, p.sp_radius,
                                                p.sun_strength)
            for ob in (light_ob, marker):
                ob.keyframe_insert('location', frame=frame)
            light_ob.keyframe_insert('rotation_quaternion', frame=frame)
            if first is None:
                first = frame
            frame += 1
        if light_ob is None:
            self.report({'ERROR'}, 'The sun never rises on that day')
            return {'CANCELLED'}
        context.scene.frame_start = first
        context.scene.frame_end = frame - 1
        self.report({'INFO'}, 'Keyframed {} frames ({} per hour)'.format(
            frame - first, fph))
        return {'FINISHED'}


CLASSES = (LB_OT_draw_sunpath, LB_OT_set_sun, LB_OT_animate_sun)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

# -*- coding: utf-8 -*-
"""Draw a Honeybee Model in Blender: rooms colored by boundary condition."""
import bpy

from . import blender_geom as bg

BC_COLORS = {
    'Outdoors': (0.35, 0.62, 0.92, 1.0),
    'Ground': (0.55, 0.38, 0.22, 1.0),
    'Surface': (0.45, 0.80, 0.45, 1.0),
    'Adiabatic': (0.92, 0.55, 0.75, 1.0),
    'Other': (0.6, 0.6, 0.6, 1.0),
}
TYPE_COLORS = {
    'Wall': (0.85, 0.75, 0.55, 1.0),
    'Floor': (0.5, 0.5, 0.55, 1.0),
    'RoofCeiling': (0.75, 0.35, 0.3, 1.0),
    'AirBoundary': (0.6, 0.9, 0.95, 1.0),
}
APERTURE_COLOR = (0.55, 0.85, 1.0, 1.0)
DOOR_COLOR = (0.7, 0.45, 0.25, 1.0)
SHADE_COLOR = (0.75, 0.75, 0.75, 1.0)


def _face_to_mesh_data(face3d, verts, faces, colors, color):
    """Append a ladybug Face3D (triangulated if it has holes) to mesh buffers."""
    if face3d.has_holes:
        mesh = face3d.triangulated_mesh3d
        base = len(verts)
        verts.extend((p.x, p.y, p.z) for p in mesh.vertices)
        for f in mesh.faces:
            faces.append(tuple(base + i for i in f))
            colors.append(color)
    else:
        base = len(verts)
        verts.extend((p.x, p.y, p.z) for p in face3d.boundary)
        faces.append(tuple(range(base, base + len(face3d.boundary))))
        colors.append(color)


def _new_object(name, verts, faces, colors, coll):
    if not faces:
        return None
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    bg.tag_generated(ob)
    bg.set_color_attribute(me, 'LB Color', colors, 'FACE')
    bg.assign_material(ob, bg.color_attribute_material('LB Color'))
    return ob


def draw_model(context, model, color_by='BC'):
    """Create Blender objects for every Room (faces + sub-faces) and Shade."""
    coll = bg.get_collection(context, 'Honeybee', clear=True)
    made = 0
    for room in model.rooms:
        verts, faces, colors = [], [], []
        sverts, sfaces, scolors = [], [], []
        for face in room.faces:
            if color_by == 'TYPE':
                color = TYPE_COLORS.get(str(face.type), BC_COLORS['Other'])
            else:
                color = BC_COLORS.get(face.boundary_condition.name, BC_COLORS['Other'])
            geo = face.punched_geometry if face.has_sub_faces else face.geometry
            _face_to_mesh_data(geo, verts, faces, colors, color)
            for ap in face.apertures:
                _face_to_mesh_data(ap.geometry, sverts, sfaces, scolors, APERTURE_COLOR)
            for dr in face.doors:
                _face_to_mesh_data(dr.geometry, sverts, sfaces, scolors, DOOR_COLOR)
        ob = _new_object('HB {}'.format(room.display_name), verts, faces, colors, coll)
        if ob is not None:
            ob['hb_room'] = room.identifier
            ob['hb_story'] = room.story or ''
            ob['hb_volume'] = round(room.volume, 2)
            ob['hb_floor_area'] = round(room.floor_area, 2)
            made += 1
        if sfaces:
            sub = _new_object('HB {} openings'.format(room.display_name), sverts, sfaces,
                              scolors, coll)
            sub['hb_room'] = room.identifier
    if model.orphaned_shades:
        verts, faces, colors = [], [], []
        for sh in model.orphaned_shades:
            _face_to_mesh_data(sh.geometry, verts, faces, colors, SHADE_COLOR)
        _new_object('HB Context', verts, faces, colors, coll)
    bg.show_attribute_colors(context)
    return made

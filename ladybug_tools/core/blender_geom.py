# -*- coding: utf-8 -*-
"""Helpers to turn Ladybug geometry/graphics into Blender objects."""
import bpy
import numpy as np
from mathutils import Vector

from ladybug_geometry.geometry3d import Point3D, Mesh3D, Plane

ROOT_COLLECTION = 'Ladybug'
GENERATED_TAG = 'ladybug_generated'


def tag_generated(ob):
    """Mark an object as created by this add-on (excluded from study contexts)."""
    ob[GENERATED_TAG] = True
    return ob


def is_generated(ob):
    return bool(ob.get(GENERATED_TAG, False))


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------
def root_collection(context):
    coll = bpy.data.collections.get(ROOT_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(ROOT_COLLECTION)
    if coll.name not in context.scene.collection.children:
        context.scene.collection.children.link(coll)
    return coll


def get_collection(context, name, clear=False):
    """Get (or create) a sub-collection of the Ladybug collection."""
    root = root_collection(context)
    full = 'LB {}'.format(name)
    coll = bpy.data.collections.get(full)
    if coll is None:
        coll = bpy.data.collections.new(full)
    if coll.name not in root.children:
        root.children.link(coll)
    if clear:
        clear_collection(coll)
    return coll


def get_child_collection(parent, name):
    """Get (or create) a collection named ``name`` linked under ``parent``."""
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in parent.children:
        parent.children.link(coll)
    return coll


def clear_collection(coll, recursive=False):
    for ob in list(coll.objects):
        remove_object(ob)
    if recursive:
        for child in list(coll.children):
            clear_collection(child, True)
            bpy.data.collections.remove(child)


def remove_object(ob):
    data = ob.data
    bpy.data.objects.remove(ob, do_unlink=True)
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
        elif isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)


def remove_objects_by_prefix(coll, prefix):
    for ob in list(coll.objects):
        if ob.name.startswith(prefix):
            remove_object(ob)


# ---------------------------------------------------------------------------
# colors / materials / attributes
# ---------------------------------------------------------------------------
def lb_color(color):
    """Ladybug Color -> RGBA floats."""
    return (color.r / 255.0, color.g / 255.0, color.b / 255.0, 1.0)


def _face_colors_to_corners(me, colors):
    """Expand per-face RGBA colors to per-corner (loop) colors."""
    n_poly = len(me.polygons)
    n_loop = len(me.loops)
    starts = np.empty(n_poly, dtype=np.int64)
    totals = np.empty(n_poly, dtype=np.int64)
    me.polygons.foreach_get('loop_start', starts)
    me.polygons.foreach_get('loop_total', totals)
    poly_of_loop = np.repeat(np.arange(n_poly), totals)
    loop_idx = np.repeat(starts - np.cumsum(totals) + totals, totals) + \
        np.arange(int(np.sum(totals)))
    corner = np.empty((n_loop, 4), dtype=np.float32)
    corner[loop_idx] = np.asarray(colors, dtype=np.float32)[poly_of_loop]
    return corner


def set_color_attribute(me, name, colors, domain='FACE'):
    """Write RGBA colors to a FLOAT_COLOR attribute on a mesh.

    Blender only displays color attributes stored on the POINT or CORNER
    domains, so per-face colors are expanded to face corners.
    """
    colors = np.asarray(colors, dtype=np.float32)
    if domain == 'FACE':
        colors = _face_colors_to_corners(me, colors)
        domain = 'CORNER'
    # a plain (non-color) attribute with the same name would block creation
    old = me.attributes.get(name)
    if old is not None and (old.domain != domain or old.data_type != 'FLOAT_COLOR'):
        me.attributes.remove(old)
    ca = me.color_attributes.get(name)
    if ca is None:
        ca = me.color_attributes.new(name, 'FLOAT_COLOR', domain)
    ca.data.foreach_set('color', colors.ravel())
    try:
        idx = me.color_attributes.keys().index(name)
        me.color_attributes.active_color_index = idx
        me.color_attributes.render_color_index = idx
    except Exception:
        pass
    me.update()
    return ca


def set_float_attribute(me, name, values, domain='FACE'):
    """Write float values to a FLOAT attribute on a mesh."""
    at = me.attributes.get(name)
    if at is not None and (at.domain != domain or at.data_type != 'FLOAT'):
        me.attributes.remove(at)
        at = None
    if at is None:
        at = me.attributes.new(name, 'FLOAT', domain)
    at.data.foreach_set('value', np.asarray(values, dtype=np.float32))
    return at


def color_attribute_material(attr_name='LB Color'):
    """Material that displays a color attribute (works in Solid/EEVEE/Cycles)."""
    mat_name = 'LB {}'.format(attr_name)
    mat = bpy.data.materials.get(mat_name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get('Principled BSDF')
    try:
        col = nodes.new('ShaderNodeVertexColor')
        col.layer_name = attr_name
    except Exception:
        col = nodes.new('ShaderNodeAttribute')
        col.attribute_name = attr_name
    col.location = (-400, 0)
    if bsdf is not None:
        links.new(col.outputs['Color'], bsdf.inputs['Base Color'])
        if 'Emission Color' in bsdf.inputs:
            links.new(col.outputs['Color'], bsdf.inputs['Emission Color'])
            bsdf.inputs['Emission Strength'].default_value = 0.35
        bsdf.inputs['Roughness'].default_value = 0.9
    return mat


def simple_material(name, rgba, emission=1.0):
    """Flat-colored material for lines, suns, compass, etc."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        emi = nt.nodes.new('ShaderNodeEmission')
        emi.inputs['Color'].default_value = rgba
        emi.inputs['Strength'].default_value = emission
        nt.links.new(emi.outputs['Emission'], out.inputs['Surface'])
        mat.diffuse_color = rgba
    return mat


def assign_material(ob, mat):
    if ob.data is None or not hasattr(ob.data, 'materials'):
        return
    ob.data.materials.clear()
    ob.data.materials.append(mat)


def show_attribute_colors(context):
    """Switch Solid viewports to show vertex/attribute colors."""
    screen = getattr(context, 'screen', None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type == 'VIEW_3D' and space.shading.type == 'SOLID':
                space.shading.color_type = 'VERTEX'


# ---------------------------------------------------------------------------
# meshes
# ---------------------------------------------------------------------------
def mesh_from_lb(mesh3d, name, coll, colors=None, attr_name='LB Color'):
    """Create a Blender mesh object from a ladybug Mesh3D (optionally colored)."""
    verts = [(p.x, p.y, p.z) for p in mesh3d.vertices]
    faces = [tuple(f) for f in mesh3d.faces]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    tag_generated(ob)
    cols = colors if colors is not None else mesh3d.colors
    if cols:
        rgba = [lb_color(c) for c in cols]
        domain = 'FACE' if len(rgba) == len(faces) else 'POINT'
        set_color_attribute(me, attr_name, rgba, domain)
        assign_material(ob, color_attribute_material(attr_name))
    return ob


def mesh_from_mesh2d(mesh2d, name, coll, z=0.0, colors=None, attr_name='LB Color'):
    plane = Plane(o=Point3D(0, 0, z))
    mesh3d = Mesh3D.from_mesh2d(mesh2d, plane)
    cols = colors if colors is not None else mesh2d.colors
    return mesh_from_lb(mesh3d, name, coll, colors=cols, attr_name=attr_name)


def offset_mesh_along_normals(me, distance):
    """Move every vertex of a mesh along its normal by ``distance`` (local units)."""
    if distance == 0 or len(me.vertices) == 0:
        return
    n = len(me.vertices)
    co = np.empty(n * 3)
    nr = np.empty(n * 3)
    me.vertices.foreach_get('co', co)
    me.vertex_normals.foreach_get('vector', nr)
    co += nr * distance
    me.vertices.foreach_set('co', co)
    me.update()


def bbox_world(ob):
    """World-space (min Point3D, max Point3D) of an object."""
    corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return Point3D(min(xs), min(ys), min(zs)), Point3D(max(xs), max(ys), max(zs))


# ---------------------------------------------------------------------------
# curves & text
# ---------------------------------------------------------------------------
def _pts3(points, z=0.0):
    out = []
    for p in points:
        if hasattr(p, 'z'):
            out.append((p.x, p.y, p.z))
        else:
            out.append((p.x, p.y, z))
    return out


def curve_object(name, polylines, coll, closed=False, bevel=0.0, material=None):
    """Create a curve object from a list of point lists (each a polyline).

    ``closed`` may be a bool or a list of bools (one per polyline).
    """
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = bevel
    if bevel > 0:
        cu.bevel_resolution = 2
        cu.use_fill_caps = True
    if isinstance(closed, bool):
        closed = [closed] * len(polylines)
    for pts, cyc in zip(polylines, closed):
        if len(pts) < 2:
            continue
        sp = cu.splines.new('POLY')
        sp.points.add(len(pts) - 1)
        for bp, (x, y, z) in zip(sp.points, pts):
            bp.co = (x, y, z, 1.0)
        sp.use_cyclic_u = cyc
    ob = bpy.data.objects.new(name, cu)
    coll.objects.link(ob)
    tag_generated(ob)
    if material is not None:
        assign_material(ob, material)
    return ob


def lb_polyline_pts(polyline):
    return _pts3(polyline.vertices)


def lb_segment_pts(segment, z=0.0):
    return _pts3((segment.p1, segment.p2), z)


def lb_arc_pts(arc, divisions=72, z=0.0):
    """Sample a ladybug Arc2D/Arc3D into points."""
    if getattr(arc, 'is_circle', False):
        n = divisions
        pts = [arc.point_at_angle(2 * np.pi * i / n) for i in range(n)]
        return _pts3(pts, z), True
    pts = arc.subdivide_evenly(divisions)
    return _pts3(pts, z), False


def text_object(name, text, location, size, coll, align='CENTER', material=None,
                rotation=(0.0, 0.0, 0.0)):
    cu = bpy.data.curves.new(name, 'FONT')
    cu.body = text
    cu.size = size
    cu.align_x = align
    cu.align_y = 'CENTER'
    ob = bpy.data.objects.new(name, cu)
    ob.location = location
    ob.rotation_euler = rotation
    coll.objects.link(ob)
    tag_generated(ob)
    if material is not None:
        assign_material(ob, material)
    return ob


# ---------------------------------------------------------------------------
# legend & compass
# ---------------------------------------------------------------------------
def legend_objects(legend, name, coll, title=None):
    """Create colored legend bar + labels from a ladybug Legend."""
    objs = []
    mesh3d = legend.segment_mesh
    cols = mesh3d.colors if mesh3d.colors else legend.segment_colors
    bar = mesh_from_lb(mesh3d, '{} Legend'.format(name), coll, colors=cols)
    objs.append(bar)
    th = legend.legend_parameters.text_height
    txt_mat = simple_material('LB Text', (0.05, 0.05, 0.05, 1.0))
    for i, (txt, plane) in enumerate(
            zip(legend.segment_text, legend.segment_text_location)):
        o = plane.o
        objs.append(text_object('{} Label {}'.format(name, i), txt, (o.x, o.y, o.z),
                                th, coll, align='LEFT', material=txt_mat))
    ttl = title if title is not None else legend.title
    if ttl:
        o = legend.title_location.o
        objs.append(text_object('{} Title'.format(name), ttl, (o.x, o.y, o.z), th,
                                coll, align='LEFT', material=txt_mat))
    return objs


def compass_objects(compass, name, coll, z=0.0, text_scale=1.0):
    """Create compass circles, ticks and labels from a ladybug Compass."""
    mat = simple_material('LB Compass', (0.25, 0.25, 0.25, 1.0))
    polylines, closed = [], []
    for arc in compass.all_boundary_circles:
        pts, cyc = lb_arc_pts(arc, 72, z)
        polylines.append(pts)
        closed.append(cyc)
    for seg in compass.major_azimuth_ticks + compass.minor_azimuth_ticks:
        polylines.append(lb_segment_pts(seg, z))
        closed.append(False)
    objs = [curve_object('{} Compass'.format(name), polylines, coll, closed,
                         material=mat)]
    size = compass.radius * 0.06 * text_scale
    for txt, pt in zip(compass.MAJOR_TEXT, compass.major_azimuth_points):
        objs.append(text_object('{} {}'.format(name, txt), txt, (pt.x, pt.y, z),
                                size, coll, material=mat))
    for txt, pt in zip(compass.MINOR_TEXT, compass.minor_azimuth_points):
        objs.append(text_object('{} {}'.format(name, txt), txt, (pt.x, pt.y, z),
                                size * 0.5, coll, material=mat))
    return objs

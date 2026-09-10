# -*- coding: utf-8 -*-
"""Ray/geometry intersection using Blender's BVHTree.

Replaces ``ladybug_radiance.intersection`` (which shells out to Radiance's
``rcontrib``) with in-process ray casting against the evaluated scene meshes.
"""
import math

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def gather_world_polygons(objects, depsgraph):
    """Collect world-space vertices and polygons from evaluated mesh objects.

    Returns:
        (vertices, polygons): list of (x, y, z) tuples and list of index tuples.
    """
    all_verts = []
    all_polys = []
    offset = 0
    for ob in objects:
        if ob.type != 'MESH':
            continue
        ob_eval = ob.evaluated_get(depsgraph)
        me = ob_eval.to_mesh()
        try:
            if len(me.polygons) == 0:
                continue
            co = np.empty(len(me.vertices) * 3, dtype=np.float64)
            me.vertices.foreach_get('co', co)
            co = co.reshape(-1, 3)
            mw = np.array(ob_eval.matrix_world, dtype=np.float64)
            world = co @ mw[:3, :3].T + mw[:3, 3]
            all_verts.extend(map(tuple, world))
            for p in me.polygons:
                all_polys.append(tuple(v + offset for v in p.vertices))
            offset += len(me.vertices)
        finally:
            ob_eval.to_mesh_clear()
    return all_verts, all_polys


def build_bvh(objects, depsgraph, epsilon=0.0):
    """Build a single world-space BVHTree from several mesh objects.

    Returns None when there is no geometry.
    """
    verts, polys = gather_world_polygons(objects, depsgraph)
    if not polys:
        return None
    return BVHTree.FromPolygons(verts, polys, epsilon=epsilon)


def study_points(obj, depsgraph, by_vertex=False):
    """World-space sensor points and unit normals for a study object.

    Args:
        obj: Blender mesh object.
        by_vertex: True to use vertices, False to use polygon centers.

    Returns:
        (points, normals): numpy arrays of shape (M, 3).
    """
    ob_eval = obj.evaluated_get(depsgraph)
    me = ob_eval.to_mesh()
    try:
        mw = np.array(ob_eval.matrix_world, dtype=np.float64)
        rot = mw[:3, :3]
        nrm_mat = np.linalg.inv(rot).T
        if by_vertex:
            n = len(me.vertices)
            pts = np.empty(n * 3)
            nrs = np.empty(n * 3)
            me.vertices.foreach_get('co', pts)
            me.vertices.foreach_get('normal', nrs)
        else:
            n = len(me.polygons)
            pts = np.empty(n * 3)
            nrs = np.empty(n * 3)
            me.polygons.foreach_get('center', pts)
            me.polygons.foreach_get('normal', nrs)
        pts = pts.reshape(-1, 3) @ rot.T + mw[:3, 3]
        nrs = nrs.reshape(-1, 3) @ nrm_mat.T
        lengths = np.linalg.norm(nrs, axis=1)
        lengths[lengths == 0] = 1.0
        nrs = nrs / lengths[:, None]
    finally:
        ob_eval.to_mesh_clear()
    return pts, nrs


def intersection_matrix(bvh, vectors, points, normals, offset_distance=0.0,
                        numericalize=False, progress=None):
    """Compute which vectors are visible from each point.

    Args:
        bvh: BVHTree of the context geometry (or None for no obstruction).
        vectors: numpy (N, 3) unit vectors pointing *away* from the points
            (towards the sun or sky patch).
        points: numpy (M, 3) sensor points.
        normals: numpy (M, 3) unit normals of the sensors.
        offset_distance: distance to move the sensors along their normals
            before casting the rays.
        numericalize: if True the matrix holds the cosine of the angle between
            normal and vector (0 when blocked); otherwise booleans.
        progress: optional callable receiving a float 0-1.

    Returns:
        numpy array of shape (M, N).
    """
    vectors = np.asarray(vectors, dtype=np.float64)
    points = np.asarray(points, dtype=np.float64)
    normals = np.asarray(normals, dtype=np.float64)
    m, n = len(points), len(vectors)
    cosines = normals @ vectors.T  # (M, N)
    if numericalize:
        result = np.zeros((m, n), dtype=np.float64)
    else:
        result = np.zeros((m, n), dtype=bool)

    if bvh is None:
        visible = cosines > 0
        if numericalize:
            result[visible] = cosines[visible]
        else:
            result[visible] = True
        return result

    mu_vecs = [Vector(v) for v in vectors]
    ray_cast = bvh.ray_cast
    origins = points + normals * offset_distance
    # Also nudge each ray along its own direction so that sensors lying exactly
    # on a context surface (e.g. a face center on the ground plane) do not
    # register a zero-distance hit against that surface.
    nudge = max(float(offset_distance), 1e-4)
    nudged = [v * nudge for v in mu_vecs]
    for i in range(m):
        if progress is not None and i % 200 == 0:
            progress(i / m)
        origin = Vector(origins[i])
        row = cosines[i]
        for j in np.nonzero(row > 0)[0]:
            hit = ray_cast(origin + nudged[j], mu_vecs[j])
            if hit[2] is None:  # no face index -> unobstructed
                result[i, j] = row[j] if numericalize else True
    return result


def rotate_vectors_z(vectors, angle_degrees):
    """Rotate (N, 3) vectors counterclockwise around Z by an angle in degrees."""
    if angle_degrees == 0:
        return np.asarray(vectors, dtype=np.float64)
    a = math.radians(angle_degrees)
    c, s = math.cos(a), math.sin(a)
    rot = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    return np.asarray(vectors, dtype=np.float64) @ rot.T


def sky_vectors(sky_matrix):
    """Sky + ground patch vectors for a sky matrix, rotated by its north angle.

    Returns numpy (2N, 3): first the sky patches, then the mirrored ground patches.
    """
    sky = rotate_vectors_z(sky_matrix.patch_vectors, sky_matrix.north)
    ground = sky * np.array([1.0, 1.0, -1.0])
    return np.vstack([sky, ground])


def radiation_from_intersection(sky_matrix, int_matrix):
    """Total incident radiation (kWh/m2) per sensor from a numericalized matrix."""
    direct = np.array(sky_matrix.direct_values)
    diffuse = np.array(sky_matrix.diffuse_values)
    sky_rad = direct + diffuse
    grd_val = float(np.sum(sky_rad)) / len(sky_rad) * sky_matrix.ground_reflectance
    all_rad = np.concatenate([sky_rad, np.full(len(sky_rad), grd_val)])
    return int_matrix @ all_rad

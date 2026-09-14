# -*- coding: utf-8 -*-
"""IFC -> Honeybee model bridge (prototype).

Reads an IFC file with ifcopenshell (the one bundled by Bonsai) and builds a
``honeybee.model.Model``:

* every ``IfcSpace`` becomes a ``Room``;
* every ``IfcRelSpaceBoundary`` of the space with a surface becomes a ``Face``
  (type from the bounding element and the face normal, boundary condition from
  the boundary's INTERNAL/EXTERNAL flag and the elevation);
* boundaries of ``IfcWindow`` / ``IfcDoor`` become ``Aperture`` / ``Door`` in the
  wall face that contains them;
* ``IfcMaterialLayerSet`` + ``Pset_MaterialThermal`` become Honeybee
  ``OpaqueConstruction`` objects, so the energy model gets the real layers;
* building elements that bound no space (roofs, parapets, water tank...) are
  added as context ``Shade`` geometry.

The module has no Blender dependency and can run in any Python that has
ifcopenshell, ladybug_geometry and honeybee(-energy) importable.
"""
import math
import re
import time
from collections import Counter

import numpy as np

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import ifcopenshell.util.placement as up
import ifcopenshell.util.unit as uu

from ladybug_geometry.geometry3d import Face3D, Point3D, Polyface3D, Vector3D
from honeybee.model import Model
from honeybee.room import Room
from honeybee.face import Face
from honeybee.aperture import Aperture
from honeybee.door import Door
from honeybee.shade import Shade
from honeybee.facetype import Wall, Floor, RoofCeiling, face_types
from honeybee.boundarycondition import boundary_conditions as bcs
from honeybee.typing import clean_string
from honeybee_energy.material.opaque import EnergyMaterial
from honeybee_energy.material.glazing import EnergyWindowMaterialSimpleGlazSys
from honeybee_energy.construction.opaque import OpaqueConstruction
from honeybee_energy.construction.window import WindowConstruction

TOL = 0.01
ANGLE_TOL = 1.0
EXCLUDE_DEFAULT = ('piscina', 'pool')
CONTEXT_CLASSES = ('IfcRoof', 'IfcSlab', 'IfcWall', 'IfcColumn', 'IfcBeam', 'IfcMember', 'IfcCurtainWall')


def _ident(text, prefix=''):
    """Honeybee-safe identifier from any text."""
    s = clean_string(str(text)) if text else 'x'
    return (prefix + s)[:100]


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------
def _curve_points(curve):
    """2D/3D points of a boundary curve (polyline, composite, indexed)."""
    pts = []
    if curve.is_a('IfcPolyline'):
        pts = [tuple(p.Coordinates) for p in curve.Points]
    elif curve.is_a('IfcCompositeCurve'):
        for seg in curve.Segments:
            sub = _curve_points(seg.ParentCurve)
            if seg.SameSense is False:
                sub = sub[::-1]
            pts.extend(sub)
    elif curve.is_a('IfcIndexedPolyCurve'):
        coords = curve.Points.CoordList
        if curve.Segments:
            for seg in curve.Segments:
                idx = list(seg[0]) if hasattr(seg, '__getitem__') else list(seg.wrappedValue)
                pts.extend(tuple(coords[i - 1]) for i in idx)
        else:
            pts = [tuple(c) for c in coords]
    elif curve.is_a('IfcTrimmedCurve'):
        # arcs: approximate by the trim points only
        for t in (curve.Trim1, curve.Trim2):
            for v in t:
                if v.is_a('IfcCartesianPoint'):
                    pts.append(tuple(v.Coordinates))
    return pts


def _dedupe(points, tol):
    out = []
    for p in points:
        if not out or max(abs(a - b) for a, b in zip(p, out[-1])) > tol:
            out.append(p)
    if len(out) > 1 and max(abs(a - b) for a, b in zip(out[0], out[-1])) <= tol:
        out.pop()
    return out


def boundary_face3d(rel, space_matrix, scale, tol=TOL):
    """World-space Face3D (meters) of an IfcRelSpaceBoundary, or None."""
    cg = rel.ConnectionGeometry
    if cg is None:
        return None
    surf = cg.SurfaceOnRelatingElement
    if surf is None or not surf.is_a('IfcCurveBoundedPlane'):
        return None
    m = space_matrix @ up.get_axis2placement(surf.BasisSurface.Position)
    raw = _curve_points(surf.OuterBoundary)
    if len(raw) < 3:
        return None
    pts = []
    for p in raw:
        x, y = p[0], p[1]
        z = p[2] if len(p) > 2 else 0.0
        w = m @ np.array([x, y, z, 1.0])
        pts.append((w[0] * scale, w[1] * scale, w[2] * scale))
    pts = _dedupe(pts, tol)
    if len(pts) < 3:
        return None
    holes = []
    for hole in (surf.InnerBoundaries or []):
        hraw = _curve_points(hole)
        hp = []
        for p in hraw:
            w = m @ np.array([p[0], p[1], p[2] if len(p) > 2 else 0.0, 1.0])
            hp.append((w[0] * scale, w[1] * scale, w[2] * scale))
        hp = _dedupe(hp, tol)
        if len(hp) >= 3:
            holes.append([Point3D(*q) for q in hp])
    try:
        face = Face3D([Point3D(*q) for q in pts], holes=holes or None)
        face = face.remove_colinear_vertices(tol)
    except Exception:  # noqa: BLE001 (degenerate boundary)
        return None
    if face.area < tol * tol:
        return None
    return face


def _is_sound(face, tol=TOL, min_area=0.02):
    """True when a face survives EnergyPlus' vertex merging (>= 3 distinct vertices)."""
    if face.area < min_area:
        return False
    pts = list(face.boundary)
    distinct = []
    for p in pts:
        if all(p.distance_to_point(q) > tol for q in distinct):
            distinct.append(p)
    return len(distinct) >= 3


def merge_coplanar(faces, tol=TOL, angle_tol=0.02):
    """Group faces by plane and join each group along shared edges.

    ``Face3D.join_coplanar_faces`` expects one coplanar group, so the
    grouping is done here (normal direction + plane distance).
    """
    groups = []
    for face in faces:
        n, d = face.normal, face.plane.k
        for g in groups:
            gn, gd, members = g
            if n.dot(gn) > 1 - angle_tol and abs(d - gd) <= tol:
                members.append(face)
                break
        else:
            groups.append((n, d, [face]))
    out = []
    for _n, _d, members in groups:
        if len(members) == 1:
            out.append(members[0])
            continue
        target = sum(m.area for m in members)
        try:
            joined = Face3D.join_coplanar_faces(members, tol)
        except Exception:  # noqa: BLE001
            joined = []
        if not joined or abs(sum(j.area for j in joined) - target) > 0.05 * target:
            # edge joining fails on triangulations with T-junctions (CSG bodies):
            # fall back to a boolean union in the plane
            joined = _union_coplanar(members, tol, target)
        if not joined:
            out.extend(members)
            continue
        for j in joined:
            try:
                out.append(j.remove_colinear_vertices(tol))
            except Exception:  # noqa: BLE001
                out.append(j)
    return out


def _convex_pieces(face, tol):
    """Convex, hole-free, planar pieces of a face for EnergyPlus shading.

    EnergyPlus writes faces with holes as one self-touching polygon and its
    shadow clipping (Sutherland-Hodgman) is only exact for convex casters;
    non-convex casters are flagged severe and slow the run down.
    """
    # holes are window/door openings: for shading the wall is solid anyway
    p = Face3D(face.boundary, face.plane) if face.has_holes else face
    if not p.is_self_intersecting:
        return [p]  # non-convex is tolerated (a severe warning, not an error)
    try:
        mesh = p.triangulated_mesh3d
        tris = [Face3D(tuple(mesh.vertices[k] for k in f)) for f in mesh.faces]
    except Exception:  # noqa: BLE001
        return []
    return [t for t in tris if _is_sound(t, tol, min_area=0.05)]


def _union_coplanar(members, tol, target_area):
    """Boolean union of coplanar faces, as Face3Ds with holes; [] on failure."""
    from ladybug_geometry.geometry2d import Polygon2D
    plane = members[0].plane
    polys = []
    for m in members:
        polys.append(Polygon2D(tuple(plane.xyz_to_xy(v) for v in m.boundary)))
        for hole in (m.holes or ()):
            polys.append(Polygon2D(tuple(plane.xyz_to_xy(v) for v in hole)))
    try:
        union = Polygon2D.boolean_union_all(polys, tol)
        if not union:
            return []
        faces = [Face3D(tuple(plane.xy_to_xyz(v) for v in p.vertices), plane=plane)
                 for p in union if p.area > tol * tol]
        faces = Face3D.merge_faces_to_holes(faces, tol)
    except Exception:  # noqa: BLE001
        return []
    got = sum(f.area for f in faces)
    if not faces or abs(got - target_area) > 0.05 * target_area:
        return []
    if any(f.is_self_intersecting for f in faces):
        return []
    return faces


def element_faces(f, element, settings, scale_already=True):
    """Triangulated world-space Face3Ds of an IFC product (meters)."""
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
    except Exception:  # noqa: BLE001
        return []
    v = shape.geometry.verts
    fc = shape.geometry.faces
    pts = [Point3D(v[i], v[i + 1], v[i + 2]) for i in range(0, len(v), 3)]
    faces = []
    for i in range(0, len(fc), 3):
        a, b, c = pts[fc[i]], pts[fc[i + 1]], pts[fc[i + 2]]
        try:
            tri = Face3D((a, b, c))
            if tri.area > 1e-6:
                faces.append(tri)
        except Exception:  # noqa: BLE001
            pass
    return faces


# ---------------------------------------------------------------------------
# materials / constructions
# ---------------------------------------------------------------------------
class ConstructionLibrary(object):
    """Builds Honeybee constructions from IFC materials and layer sets."""

    def __init__(self, ifc_file, scale):
        self.f = ifc_file
        self.scale = scale
        self.materials = {}
        self.constructions = {}
        self.window_constructions = {}
        self.warnings = []
        self._thermal = {}
        for m in ifc_file.by_type('IfcMaterial'):
            ps = ue.get_psets(m)
            th = ps.get('Pset_MaterialThermal', {})
            co = ps.get('Pset_MaterialCommon', {})
            self._thermal[m.id()] = {
                'name': m.Name,
                'k': th.get('ThermalConductivity'),
                'cp': th.get('SpecificHeatCapacity'),
                'rho': co.get('MassDensity'),
            }

    def material(self, ifc_material, thickness_m):
        """EnergyMaterial for an IfcMaterial at a given thickness (meters)."""
        props = self._thermal.get(ifc_material.id(), {'name': ifc_material.Name})
        name = props.get('name') or 'Material'
        k = props.get('k') or 0.5
        rho = props.get('rho') or 1000.0
        cp = props.get('cp') or 1000.0
        if not props.get('k'):
            self.warnings.append('material "{}" has no thermal properties, using defaults'.format(name))
        thickness_m = max(float(thickness_m), 0.003)
        key = (name, round(thickness_m, 4))
        if key not in self.materials:
            ident = _ident('{}_{}mm'.format(name, int(round(thickness_m * 1000))))
            mat = EnergyMaterial(ident, thickness_m, max(float(k), 0.001), max(float(rho), 1.0),
                                 max(float(cp), 100.0))
            mat.display_name = '{} {} mm'.format(name, int(round(thickness_m * 1000)))
            self.materials[key] = mat
        return self.materials[key]

    def construction_for(self, element, default_thickness=0.1):
        """OpaqueConstruction from the element's material (layer set or single)."""
        mat = ue.get_material(element)
        if mat is None:
            return None
        if mat.is_a('IfcMaterialLayerSetUsage'):
            mat = mat.ForLayerSet
        if mat.is_a('IfcMaterialProfileSetUsage'):
            mat = mat.ForProfileSet
        if mat.is_a('IfcMaterialProfileSet'):
            # walls exported as profile extrusions (ArchiCAD "parametric" bodies)
            # lose their layers; recover the layer set with the same name,
            # "Alvenaria 140 (190 x 1590)" -> "Alvenaria 140"
            base = re.sub(r'\s*\([^)]*\)\s*$', '', mat.Name or '')
            layer_set = next((ls for ls in self.f.by_type('IfcMaterialLayerSet')
                              if (ls.LayerSetName or '') == base), None)
            if layer_set is not None:
                mat = layer_set
            else:
                m = re.search(r'\((\d+(?:\.\d+)?)\s*[x×]', mat.Name or '')
                thick = float(m.group(1)) if m else None
                if thick is not None:
                    default_thickness = thick / 1000.0 if thick > 5 else thick
                profiles = [p.Material for p in (mat.MaterialProfiles or []) if p.Material]
                mat = profiles[0] if profiles else None
                if mat is None:
                    return None
        layers = []
        if mat.is_a('IfcMaterialLayerSet'):
            for layer in mat.MaterialLayers:
                if layer.Material is None:
                    continue
                layers.append((layer.Material, layer.LayerThickness * self.scale))
            name = mat.LayerSetName or ' + '.join(l.Material.Name for l in mat.MaterialLayers if l.Material)
        elif mat.is_a('IfcMaterial'):
            layers.append((mat, default_thickness))
            name = mat.Name
        elif mat.is_a('IfcMaterialConstituentSet'):
            for c in mat.MaterialConstituents or []:
                if c.Material is not None:
                    layers.append((c.Material, default_thickness / max(len(mat.MaterialConstituents), 1)))
            name = mat.Name or 'constituents'
        else:
            return None
        if not layers:
            return None
        key = tuple((m.id(), round(t, 4)) for m, t in layers)
        if key not in self.constructions:
            mats = [self.material(m, t) for m, t in layers]
            con = OpaqueConstruction(_ident(name, 'IFC_'), mats)
            con.display_name = name
            self.constructions[key] = con
        return self.constructions[key]

    def reversed(self, construction):
        """The same construction with its layers reversed (for the other side)."""
        key = ('rev', construction.identifier)
        if key not in self.constructions:
            rev = OpaqueConstruction(_ident(construction.identifier + '_Rev'),
                                     list(reversed(construction.materials)))
            rev.display_name = (construction.display_name or construction.identifier) + ' (rev)'
            self.constructions[key] = rev
        return self.constructions[key]

    def window_construction(self, element):
        """Simple glazing system for a window/door, from ThermalTransmittance if any."""
        ps = ue.get_psets(element)
        common = next((v for k, v in ps.items() if k.startswith('Pset_') and k.endswith('Common')), {})
        u = common.get('ThermalTransmittance') or 0.0
        if u <= 0:
            u = 5.7  # single clear glass
        shgc = 0.8 if u > 4 else 0.6
        key = (round(u, 2), shgc)
        if key not in self.window_constructions:
            glz = EnergyWindowMaterialSimpleGlazSys(_ident('IFC_glazing_U{:.1f}'.format(u)), u, shgc)
            con = WindowConstruction(_ident('IFC_window_U{:.1f}'.format(u)), [glz])
            con.display_name = 'Window U={:.1f}'.format(u)
            self.window_constructions[key] = con
        return self.window_constructions[key]


# ---------------------------------------------------------------------------
# the bridge
# ---------------------------------------------------------------------------
class IfcToHoneybee(object):
    """Build a Honeybee Model from an IFC file.

    Args:
        ifc_path: path to the .ifc file.
        exclude: iterable of lowercase substrings; spaces whose name/long name
            contains one of them are skipped (default: pools).
        include_context: add unbounded building elements as Shade geometry.
        ground_level: elevation (m) at or below which floors touch the ground.
    """

    def __init__(self, ifc_path, exclude=EXCLUDE_DEFAULT, include_context=True,
                 ground_level=0.0, tolerance=TOL, context_reach=10.0, glass_doors='',
                 local_coords=True):
        self.path = ifc_path
        self.local_coords = local_coords  # undo the IfcSite rotation, carry it as north
        self.exclude = tuple(e.lower() for e in exclude)
        # door names that are glazed ("PA06, PA09") or explicitly not ("-PA10");
        # unlisted exterior sliding/pivot doors are assumed glazed
        self.glass_doors, self.opaque_doors = set(), set()
        for tok in (glass_doors or '').replace(';', ',').split(','):
            tok = tok.strip().upper()
            if tok.startswith('-'):
                self.opaque_doors.add(tok[1:].strip())
            elif tok:
                self.glass_doors.add(tok)
        self.include_context = include_context
        self.context_reach = context_reach
        self.ground_level = ground_level
        self.tol = tolerance
        self.f = ifcopenshell.open(ifc_path)
        self.scale = uu.calculate_unit_scale(self.f)
        self.settings = ifcopenshell.geom.settings()
        self.settings.set(self.settings.USE_WORLD_COORDS, True)
        self.lib = ConstructionLibrary(self.f, self.scale)
        self.warnings = []
        self.report = {}
        self.model = None
        self._room_bnds = {}  # room identifier -> [(Face3D, physical, external)]
        self._small_openings = set()
        self.site_rotation, self.site_offset = self._site_placement()
        self.location = self._site_location()

    # ---- metadata ----
    def _site_placement(self):
        """Rotation (degrees, counterclockwise) and offset of the IfcSite placement.

        ArchiCAD exports the survey-point north as a rotation of the site
        instead of a TrueNorth; with ``local_coords`` the geometry is put back
        into the project's own axes and that angle becomes the north.
        """
        sites = self.f.by_type('IfcSite')
        if not sites or sites[0].ObjectPlacement is None:
            return 0.0, (0.0, 0.0, 0.0)
        try:
            m = up.get_local_placement(sites[0].ObjectPlacement)
        except Exception:  # noqa: BLE001
            return 0.0, (0.0, 0.0, 0.0)
        angle = math.degrees(math.atan2(m[1, 0], m[0, 0]))
        offset = tuple(float(v) * self.scale for v in m[:3, 3])
        return angle, offset

    def _site_location(self):
        sites = self.f.by_type('IfcSite')
        if not sites:
            return None
        s = sites[0]

        def dms(v):
            if not v:
                return None
            deg, mn, sec = v[0], v[1], v[2]
            frac = v[3] / 1e6 if len(v) > 3 else 0.0
            sign = -1 if deg < 0 or (deg == 0 and mn < 0) else 1
            return sign * (abs(deg) + abs(mn) / 60.0 + (abs(sec) + frac) / 3600.0)
        # true north: counterclockwise degrees from the model +Y axis (Ladybug convention)
        north = 0.0
        for ctx in self.f.by_type('IfcGeometricRepresentationContext'):
            if getattr(ctx, 'TrueNorth', None) and ctx.ContextType == 'Model':
                x, y = ctx.TrueNorth.DirectionRatios[:2]
                north = math.degrees(math.atan2(-x, y)) % 360
                break
        if self.local_coords and abs(self.site_rotation) > 0.01:
            # site rotated by r puts north on +Y; in local axes north is at -r
            north = (north - self.site_rotation) % 360
        return {'latitude': dms(s.RefLatitude), 'longitude': dms(s.RefLongitude),
                'elevation': (s.RefElevation or 0.0) * self.scale, 'name': s.Name,
                'north': round(north, 3)}

    # ---- rooms ----
    def _space_polyface(self, space):
        faces = element_faces(self.f, space, self.settings)
        if not faces:
            return None
        try:
            return Polyface3D.from_faces(faces, self.tol)
        except Exception:  # noqa: BLE001
            return None

    def _face_type(self, element, face3d):
        n = face3d.normal
        if element is not None and element.is_a('IfcRoof'):
            return face_types.roof_ceiling
        if element is not None and element.is_a('IfcSlab'):
            return face_types.floor if n.z < -0.5 else face_types.roof_ceiling
        if element is not None and element.is_a('IfcWall'):
            return face_types.wall
        # by orientation
        if n.z <= -0.5:
            return face_types.floor
        if n.z >= 0.5:
            return face_types.roof_ceiling
        return face_types.wall

    def _boundary_condition(self, rel, face3d, ftype):
        if rel.InternalOrExternalBoundary == 'EXTERNAL' or rel.PhysicalOrVirtualBoundary == 'VIRTUAL':
            if ftype == face_types.floor and face3d.max.z <= self.ground_level + 0.1:
                return bcs.ground
            if ftype == face_types.floor and face3d.max.z <= self.ground_level + 0.1:
                return bcs.ground
            return bcs.outdoors
        return None  # internal: solved by adjacency later, else adiabatic

    def _build_room(self, space):
        """Room from the IfcSpace solid, classified by its space boundaries.

        The zone solid exported by the BIM tool is closed and has the right
        volume, so it gives clean Honeybee geometry. The IfcRelSpaceBoundary
        surfaces (which lie on the same inner wall surfaces) are then used only
        to say what each face is: bounding element, internal/external,
        construction, and which windows/doors sit on it.
        """
        long = space.LongName or space.Name or 'Space'
        name = '{} {}'.format(space.Name, long).strip() if space.Name and space.Name != long else long
        rels = [r for r in (space.BoundedBy or []) if r.is_a('IfcRelSpaceBoundary')]
        counts = Counter()

        # ---- solid geometry ----
        tris = element_faces(self.f, space, self.settings)
        if not tris:
            self.warnings.append('space "{}" has no 3D geometry, skipped'.format(name))
            return None
        merged = merge_coplanar(tris, self.tol)
        try:
            poly = Polyface3D.from_faces(merged, self.tol)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append('space "{}": cannot build solid ({}), skipped'.format(name, exc))
            return None
        if not poly.is_solid:
            counts['not_solid'] += 1
        room = Room.from_polyface3d(_ident(name, 'Room_'), poly, ground_depth=self.ground_level)
        self._room_bnds[room.identifier] = []
        try:
            room.remove_colinear_vertices_envelope(self.tol)
        except Exception:  # noqa: BLE001
            pass

        # ---- boundary surfaces of this space (for classification) ----
        smat = up.get_local_placement(space.ObjectPlacement)
        bnds, subs = [], []
        for rel in rels:
            g = boundary_face3d(rel, smat, self.scale, self.tol)
            if g is None:
                counts['boundary_without_geometry'] += 1
                continue
            el = rel.RelatedBuildingElement
            if el is not None and (el.is_a('IfcWindow') or el.is_a('IfcDoor')):
                subs.append((rel, el, g))
            elif el is not None and el.is_a('IfcOpeningElement'):
                counts['openings_ignored'] += 1
            else:
                bnds.append((rel, el, g))
                self._room_bnds[room.identifier].append(
                    (g, rel.PhysicalOrVirtualBoundary == 'PHYSICAL',
                     rel.InternalOrExternalBoundary == 'EXTERNAL'))

        open_tops = []
        for i, face in enumerate(room.faces):
            fg = face.geometry
            matches = []
            for rel, el, g in bnds:
                if abs(abs(fg.normal.dot(g.normal)) - 1.0) > 0.03:
                    continue
                if abs(fg.plane.distance_to_point(g.center)) > 0.05:
                    continue
                on_a = fg.is_point_on_face(fg.plane.closest_point(g.center), 0.05)
                on_b = g.is_point_on_face(g.plane.closest_point(fg.center), 0.05)
                if on_a or on_b:
                    matches.append((g.area, rel, el, g))
            face.display_name = '{} · face {}'.format(name, i)
            if not matches:
                counts['faces_unmatched'] += 1
                face.user_data = {'ifc_class': None, 'internal': False, 'matched': False}
                continue
            matches.sort(key=lambda m: -m[0])
            _area, rel, el, g = matches[0]
            internal = all(m[1].InternalOrExternalBoundary == 'INTERNAL' for m in matches)
            physical = any(m[1].PhysicalOrVirtualBoundary == 'PHYSICAL' for m in matches)
            face.user_data = {
                'ifc_guid': el.GlobalId if el is not None else None,
                'ifc_class': el.is_a() if el is not None else 'virtual',
                'ifc_name': el.Name if el is not None else None,
                'internal': internal, 'physical': physical, 'matched': True,
                'boundaries': len(matches),
            }
            if el is not None:
                face.display_name = '{} · {}'.format(name, el.Name)
                if el.is_a('IfcRoof') and fg.normal.z > 0.2:  # sloped roof, not a gable wall
                    face.type = face_types.roof_ceiling
                con = self.lib.construction_for(el)
                if con is not None:
                    face.properties.energy.construction = con
            else:
                face.display_name = '{} · virtual'.format(name)
            if not internal:
                if face.type == face_types.floor and fg.max.z <= self.ground_level + 0.1:
                    face.boundary_condition = bcs.ground
                else:
                    face.boundary_condition = bcs.outdoors
                if not physical and (el is None or el.is_a('IfcVirtualElement')) \
                        and face.type == face_types.roof_ceiling and fg.normal.z > 0.5:
                    open_tops.append(face)  # no element above: skylight or a sliver
            counts[str(face.type)] += 1

        # a virtual exterior top: the whole top of a light well is its skylight;
        # a small piece next to slab/void faces is the strip under a wall of the
        # zone above (no zone there), which touches nothing outdoors
        top_area = sum(f.area for f in room.faces if f.type == face_types.roof_ceiling)
        for face in open_tops:
            if top_area and face.area < 0.4 * top_area:
                face.boundary_condition = bcs.adiabatic
                counts['virtual_slivers'] += 1
                continue
            try:
                fg = face.geometry
                c = fg.center
                glass = Face3D([pt.move((c - pt) * 0.04) for pt in fg.boundary], fg.plane)
                ap = Aperture(_ident('{}_skylight'.format(face.identifier)), glass)
                ap.properties.energy.construction = self.lib.window_construction(space)
                ap.display_name = 'Claraboia'
                ap.user_data = {'ifc_class': 'skylight'}
                face.add_aperture(ap)
                counts['skylights'] += 1
                self.warnings.append('{}: open top without element treated as skylight'.format(name))
            except Exception:  # noqa: BLE001
                pass

        # ---- windows / doors ----
        for rel, el, g in subs:
            w, h = getattr(el, 'OverallWidth', None), getattr(el, 'OverallHeight', None)
            if w and h:
                nominal = float(w) * float(h) * self.scale * self.scale
                if g.area < 0.7 * nominal and el.Name not in self._small_openings:
                    self._small_openings.add(el.Name)
                    self.warnings.append(
                        '{}: boundary of {} "{}" is {:.1f} m2 for a {:.1f} m2 element; if the leaf '
                        'is open in the model, close it before exporting'.format(
                            name, el.is_a(), el.Name, g.area, nominal))
            hosts = self._host_faces(room.faces, g)
            if not hosts:
                counts['orphan_openings'] += 1
                self.warnings.append('{}: no host face for {} "{}"'.format(name, el.is_a(), el.Name))
                continue
            placed = 0
            for k, parent in enumerate(hosts):
                # an opening across two wall faces (a pilaster splits the wall)
                # is clipped to each of them
                gp = self._project_onto(parent.geometry, g, clip=len(hosts) > 1)
                if gp is None:
                    continue
                gp = self._clear_overlap(gp, parent)
                if gp is None:  # exported twice, or overlapping boundaries
                    counts['duplicate_openings'] += 1
                    continue
                try:
                    gp = gp.remove_colinear_vertices(self.tol)
                except Exception:  # noqa: BLE001
                    pass
                pieces = [gp]
                if el.is_a('IfcDoor') and len(gp.vertices) > 4:
                    # EnergyPlus fenestration takes 4 vertices at most; honeybee
                    # triangulates but its pieces lose is_glass, so split here
                    mesh = gp.triangulated_mesh3d
                    pieces = [Face3D(tuple(mesh.vertices[j] for j in f), gp.plane)
                              for f in mesh.faces]
                    pieces = [t for t in pieces if _is_sound(t, self.tol, 0.01)]
                for j, piece in enumerate(pieces):
                    ident = _ident('{}_{}_{}_{}_{}'.format(parent.identifier, el.Name, rel.id(), k, j))
                    if el.is_a('IfcDoor'):
                        glass = self._is_glass_door(el, rel)
                        obj = Door(ident, piece, is_glass=glass)
                        if glass:
                            obj.properties.energy.construction = self.lib.window_construction(el)
                            counts['glass_doors'] += 1
                        else:
                            obj.properties.energy.construction = self.lib.construction_for(el) or \
                                parent.properties.energy.construction
                        parent.add_door(obj)
                        counts['doors'] += 1
                    else:
                        obj = Aperture(ident, piece)
                        obj.properties.energy.construction = self.lib.window_construction(el)
                        parent.add_aperture(obj)
                        counts['apertures'] += 1
                    obj.display_name = el.Name
                    obj.user_data = {'ifc_guid': el.GlobalId, 'ifc_class': el.is_a()}
                placed += 1
            if not placed and not counts.get('duplicate_openings'):
                counts['orphan_openings'] += 1

        room.display_name = name
        room.story = self._storey_name(space)
        qto = ue.get_psets(space, qtos_only=True).get('Qto_SpaceBaseQuantities', {})
        room.user_data = {'ifc_guid': space.GlobalId, 'ifc_volume': qto.get('NetVolume'),
                          'ifc_area': qto.get('NetFloorArea')}
        self.report.setdefault('rooms', []).append({
            'name': name, 'faces': len(room.faces), 'solid': bool(poly.is_solid),
            'volume': round(room.volume, 2), 'ifc_volume': qto.get('NetVolume'),
            'counts': dict(counts)})
        return room

    # ---- adjacency across wall thickness ----
    def _pair_internal_faces(self, rooms, max_gap=0.5):
        """Pair internal faces of different rooms that face each other across a wall.

        First-level space boundaries sit on the room side of each wall, so the
        two faces of an interior wall are parallel and a wall thickness apart
        rather than coincident. Honeybee's own solve_adjacency only pairs
        coincident faces, so this does the pairing geometrically.
        """
        pairs, air = 0, 0
        # every face that is not already paired or on the ground: the exporter's
        # INTERNAL/EXTERNAL flag is not trusted (ArchiCAD marks a ceiling under
        # an attic zone EXTERNAL), what counts is a facing room face nearby
        candidates = [(r, f) for r in rooms for f in r.faces
                      if f.boundary_condition.name not in ('Surface', 'Ground')]
        owner = {id(f): r for r, f in candidates}
        for i, (ra, fa) in enumerate(candidates):
            if fa.boundary_condition.name == 'Surface':
                continue
            ga = fa.geometry
            best, best_gap = None, None
            for rb, fb in candidates[i + 1:]:
                if rb is ra or fb.boundary_condition.name == 'Surface':
                    continue
                gb = fb.geometry
                if ga.normal.dot(gb.normal) > -0.98:
                    continue
                gap = ga.plane.distance_to_point(gb.center)
                if gap < -self.tol or gap > max_gap:
                    continue
                # overlap: project b onto a's plane and test centers
                pb = Face3D([ga.plane.closest_point(p) for p in gb.boundary], ga.plane)
                if not (ga.is_point_on_face(pb.center, 0.05) or
                        pb.is_point_on_face(ga.center, 0.05)):
                    continue
                if best is None or gap < best_gap:
                    best, best_gap = fb, gap
            if best is None:
                continue
            coincident = best_gap <= self.tol * 2
            try:
                fa.set_adjacency(best, max_gap)
            except Exception:  # sub-faces do not match: drop them and retry
                fa.remove_sub_faces()
                best.remove_sub_faces()
                fa.set_adjacency(best, max_gap)
            for f in (fa, best):
                f.user_data = dict(f.user_data or {})
                f.user_data['gap'] = round(best_gap, 3)
                f.user_data['internal'] = True
            # EnergyPlus requires both sides of an interzone surface to use the
            # same construction (it reverses the layers itself): share the one
            # that came from the IFC, or the wall's construction for its doors
            con_a = fa.properties.energy.construction if fa.properties.energy.is_construction_set_on_object else None
            con_b = best.properties.energy.construction if best.properties.energy.is_construction_set_on_object else None
            con = con_a or con_b
            if con is not None:
                fa.properties.energy.construction = con
                best.properties.energy.construction = con if con.is_symmetric \
                    else self.lib.reversed(con)
            for f in (fa, best):
                for sub in f.doors:
                    sub.properties.energy.construction = None
                for sub in f.apertures:
                    sub.properties.energy.construction = None
            both_walls = fa.type == face_types.wall and best.type == face_types.wall
            both_virtual = self._is_virtual_at(ra, fa.geometry) and \
                self._is_virtual_at(owner.get(id(best)), best.geometry)
            if coincident and not fa.has_sub_faces and not best.has_sub_faces and \
                    (both_walls or both_virtual):
                # two zones touching without an element between them: open plan,
                # or a void in a slab (double height, skylight well over a room)
                for f in (fa, best):
                    f.type = face_types.air_boundary
                    f.properties.energy.construction = None  # use the air boundary construction
                air += 1
            pairs += 1
        self.report['air_boundaries'] = air
        return pairs

    @staticmethod
    def _storey_name(space):
        for rel in (space.Decomposes or []):
            return rel.RelatingObject.Name
        c = ue.get_container(space)
        return c.Name if c is not None else None

    def _voids_to_air(self, rooms):
        """Surface pairs with no element on either side (a void in a slab between
        a room and its light well, a zone limit in open plan) become air boundaries."""
        faces = {f.identifier: (r, f) for r in rooms for f in r.faces}
        n = 0
        for room in rooms:
            for face in room.faces:
                if face.boundary_condition.name != 'Surface' or face.type == face_types.air_boundary:
                    continue
                other = faces.get(face.boundary_condition.boundary_condition_object)
                if other is None or face.has_sub_faces or other[1].has_sub_faces:
                    continue
                if self._is_virtual_at(room, face.geometry) and self._is_virtual_at(other[0], other[1].geometry):
                    for f in (face, other[1]):
                        f.type = face_types.air_boundary
                        f.properties.energy.construction = None
                    n += 1
        return n

    def _is_virtual_at(self, room, geo):
        """True when the room's boundaries at the face center are virtual only
        (a void in a slab, an open zone limit), i.e. no element sits there."""
        if room is None:
            return False
        pt = geo.center
        virtual, physical = False, False
        for g, is_physical, _ext in self._room_bnds.get(room.identifier, []):
            if abs(abs(g.normal.dot(geo.normal)) - 1) > 0.05 or abs(g.plane.distance_to_point(pt)) > 0.05:
                continue
            if g.is_point_on_face(g.plane.closest_point(pt), self.tol * 5):
                if is_physical:
                    physical = True
                else:
                    virtual = True
        return virtual and not physical

    @staticmethod
    def _faces_excluded_space(geo, polys, probes=(0.05, 0.2, 0.4)):
        """True if a point just beyond the face (through the wall) is inside an excluded zone."""
        for d in probes:
            pt = geo.center.move(geo.normal * d)
            for poly in polys:
                if (poly.min.x <= pt.x <= poly.max.x and poly.min.y <= pt.y <= poly.max.y and
                        poly.min.z <= pt.z <= poly.max.z and poly.is_point_inside(pt)):
                    return True
        return False

    def _host_faces(self, faces, g):
        """Wall/roof faces of the room that a window/door boundary lies on.

        Usually one; two when the opening straddles a split in the wall
        (a pilaster or a zone notch divides the wall into two faces).
        """
        from ladybug_geometry.geometry2d import Polygon2D
        hosts = []
        for face in faces:
            if face.type != face_types.wall and face.type != face_types.roof_ceiling:
                continue
            plane = face.geometry.plane
            if abs(abs(plane.n.dot(g.normal)) - 1) > 0.05:
                continue
            d = abs(plane.distance_to_point(g.center))
            if d > 0.3:
                continue
            if face.geometry.is_point_on_face(plane.closest_point(g.center), self.tol * 10):
                hosts.append((0, d, face))
                continue
            # not centered on this face: does it still overlap it?
            try:
                fp = face.geometry.boundary_polygon2d
                gp = Polygon2D(tuple(plane.xyz_to_xy(plane.closest_point(p)) for p in g.boundary))
                inter = fp.boolean_intersect(gp, self.tol)
                area = sum(p.area for p in inter)
            except Exception:  # noqa: BLE001
                area = 0.0
            if area > max(0.05, 0.1 * g.area):
                hosts.append((1, d, face))
        hosts.sort(key=lambda h: (h[0], h[1]))
        return [h[2] for h in hosts]

    def _project_onto(self, parent_geo, g, clip=False):
        """Boundary projected onto the host face plane, clipped to the face if asked."""
        from ladybug_geometry.geometry2d import Polygon2D
        plane = parent_geo.plane
        pts = [plane.closest_point(p) for p in g.boundary]
        try:
            proj = Face3D(pts, plane)
            proj = proj.remove_colinear_vertices(self.tol)
        except Exception:  # noqa: BLE001
            return None
        if proj.normal.dot(parent_geo.normal) < 0:
            proj = proj.flip()
        if clip and not parent_geo.is_sub_face(proj, self.tol, ANGLE_TOL):
            try:
                fp = parent_geo.boundary_polygon2d
                qp = Polygon2D(tuple(plane.xyz_to_xy(p) for p in proj.boundary))
                pieces = [Face3D(tuple(plane.xy_to_xyz(v) for v in poly.vertices), plane)
                          for poly in fp.boolean_intersect(qp, self.tol)]
                pieces = [p for p in pieces if p.area >= 0.05]
            except Exception:  # noqa: BLE001
                pieces = []
            if not pieces:
                return None
            proj = max(pieces, key=lambda p: p.area)
            if proj.normal.dot(parent_geo.normal) < 0:
                proj = proj.flip()
        if not parent_geo.is_sub_face(proj, self.tol, ANGLE_TOL):
            # shrink slightly towards its center to clear coincident edges
            c = proj.center
            pts = [p.move((c - p) * 0.02) for p in proj.boundary]
            proj = Face3D(pts, plane)
            if not parent_geo.is_sub_face(proj, self.tol, ANGLE_TOL):
                return None
        return proj

    def _clear_overlap(self, gp, parent, max_shrink=0.12):
        """Shrink a sub-face a little when it overlaps an existing one (frames
        touching, e.g. a window right above another); None if it is a duplicate."""
        existing = [s.geometry for s in parent.sub_faces]
        if not any(gp.is_overlapping(s, self.tol) for s in existing):
            return gp
        c = gp.center
        for k in range(1, 7):
            factor = k * max_shrink / 6.0
            shrunk = Face3D([p.move((c - p) * factor) for p in gp.boundary], gp.plane)
            if not any(shrunk.is_overlapping(s, self.tol) for s in existing):
                return shrunk
        return None

    def _is_glass_door(self, el, rel):
        """Glazed door: listed by name, or an exterior sliding/pivot door."""
        name = (el.Name or '').upper()
        if name in self.opaque_doors:
            return False
        if name in self.glass_doors:
            return True
        if rel.InternalOrExternalBoundary != 'EXTERNAL':
            return False
        typ = ue.get_type(el)
        words = ' '.join(filter(None, [
            el.Name, el.ObjectType, el.Description,
            typ.Name if typ else '', typ.Description if typ else '',
            str(getattr(typ, 'OperationType', '') or ''),
            str(getattr(el, 'OperationType', '') or '')])).lower()
        return any(w in words for w in ('correr', 'sliding', 'vidro', 'glass', 'pivot'))

    # ---- context ----
    def _context_shades(self, bounded_ids, merge_limit=2000, rooms=None, reach=None):
        """Unbounded elements as shades, limited to those near the rooms.

        ``reach`` (meters) drops elements whose bounding box is farther than
        that from the rooms' bounding box; EnergyPlus shadow calculations
        scale with the number of shading surfaces, so far context is costly
        and irrelevant.
        """
        shades = []
        box = None
        if rooms and reach is not None:
            mins = [r.min for r in rooms]
            maxs = [r.max for r in rooms]
            box = (min(p.x for p in mins) - reach, min(p.y for p in mins) - reach,
                   min(p.z for p in mins) - reach, max(p.x for p in maxs) + reach,
                   max(p.y for p in maxs) + reach, max(p.z for p in maxs) + reach)
        # room faces per plane, to subtract from bounded elements (roof eaves,
        # walls extending past the zones) so only their exposed parts shade
        room_faces = [f.geometry for r in (rooms or []) for f in r.faces]
        room_geos = [r.geometry for r in (rooms or [])]

        def inside_room(pt):
            for g in room_geos:
                if (g.min.x - self.tol <= pt.x <= g.max.x + self.tol and
                        g.min.y - self.tol <= pt.y <= g.max.y + self.tol and
                        g.min.z - self.tol <= pt.z <= g.max.z + self.tol and
                        g.is_point_inside(pt)):
                    return True
            return False

        for cls in CONTEXT_CLASSES:
            cls_faces = []
            for el in self.f.by_type(cls):
                bounded = el.id() in bounded_ids
                if bounded and cls not in ('IfcRoof', 'IfcSlab', 'IfcWall'):
                    continue
                tris = element_faces(self.f, el, self.settings)
                if not tris:
                    continue
                if box is not None:
                    xs = [p.x for t in tris for p in t.boundary]
                    ys = [p.y for t in tris for p in t.boundary]
                    zs = [p.z for t in tris for p in t.boundary]
                    if (max(xs) < box[0] or min(xs) > box[3] or max(ys) < box[1] or
                            min(ys) > box[4] or max(zs) < box[2] or min(zs) > box[5]):
                        continue
                merged = tris
                if len(tris) <= merge_limit:  # coplanar merge is quadratic
                    merged = merge_coplanar(tris, self.tol)
                merged = [g for g in merged if _is_sound(g, self.tol, min_area=0.05)]
                if box is not None:
                    floor_z = box[2] + reach  # lowest room level
                    # undersides and surfaces below the rooms cannot shade them
                    merged = [g for g in merged
                              if g.normal.z > -0.7 and g.max.z > floor_z + 0.3]
                if bounded:
                    # keep only what sticks out beyond the room faces (eaves)
                    merged = self._subtract_room_faces(merged, room_faces)
                cls_faces.extend(merged)
            # join coplanar pieces across elements (adjacent walls, slab strips)
            # and drop what lies inside a zone: it cannot shade the outside
            if len(cls_faces) <= merge_limit:
                cls_faces = merge_coplanar(cls_faces, self.tol)
            for i, g in enumerate(cls_faces):
                if not _is_sound(g, self.tol, min_area=0.1) or inside_room(g.center):
                    continue
                pieces = _convex_pieces(g, self.tol)
                for j, piece in enumerate(pieces):
                    sh = Shade(_ident('{}_{}_{}'.format(cls, i, j)), piece, is_detached=True)
                    sh.display_name = cls[3:]
                    sh.user_data = {'ifc_class': cls}
                    shades.append(sh)
        return shades

    def _unify_adjacent_constructions(self, rooms):
        """Give both sides of every interzone pair the same (or reversed) construction.

        EnergyPlus rejects interzone surfaces whose constructions differ, and
        Honeybee's own solve_adjacency does not touch constructions.
        """
        by_id = {}
        for room in rooms:
            for face in room.faces:
                by_id[face.identifier] = face
                for sub in face.sub_faces:
                    by_id[sub.identifier] = sub
        done = set()
        for room in rooms:
            for face in room.faces:
                bc = face.boundary_condition
                if bc.name != 'Surface' or face.identifier in done:
                    continue
                other = by_id.get(bc.boundary_condition_object)
                if other is None:
                    continue
                done.add(face.identifier)
                done.add(other.identifier)
                ea, eb = face.properties.energy, other.properties.energy
                con_a = ea.construction if ea.is_construction_set_on_object else None
                con_b = eb.construction if eb.is_construction_set_on_object else None
                con = con_a or con_b
                if con is not None:
                    face.properties.energy.construction = con
                    other.properties.energy.construction = con if con.is_symmetric \
                        else self.lib.reversed(con)
                # interior doors/windows fall back to the wall's construction set
                for f in (face, other):
                    for sub in f.sub_faces:
                        sub.properties.energy.construction = None

    def _subtract_room_faces(self, faces, room_faces, min_area=0.1):
        """Remove from context faces the parts covered by (coplanar) room faces."""
        out = []
        for g in faces:
            overlapping = [
                rf for rf in room_faces
                if abs(abs(rf.normal.dot(g.normal)) - 1.0) < 0.03 and
                abs(g.plane.distance_to_point(rf.center)) < 0.05 and
                (g.is_point_on_face(g.plane.closest_point(rf.center), 0.05) or
                 rf.is_point_on_face(rf.plane.closest_point(g.center), 0.05))]
            if not overlapping:
                out.append(g)
                continue
            try:
                # the room face lies on the inner side; project it onto g's plane
                proj = [Face3D([g.plane.closest_point(p) for p in rf.boundary], g.plane)
                        for rf in overlapping]
                pieces = g.coplanar_difference(proj, self.tol, 0.03)
            except Exception:  # noqa: BLE001
                continue  # boolean failed: drop the element face rather than double it
            for piece in pieces:
                if piece.area >= min_area and _is_sound(piece, self.tol, min_area):
                    out.append(piece)
        return out

    # ---- main ----
    def build(self):
        rooms = []
        bounded_ids = set()
        excluded_polys = []
        self.timings = {}
        t_rooms = time.time()
        for space in self.f.by_type('IfcSpace'):
            label = '{} {}'.format(space.Name or '', space.LongName or '').lower()
            if any(x in label for x in self.exclude):
                self.warnings.append('space "{}" excluded by name'.format(label.strip()))
                # keep its solid: faces of neighbours that look into it are exterior
                tris = element_faces(self.f, space, self.settings)
                if tris:
                    try:
                        excluded_polys.append(Polyface3D.from_faces(merge_coplanar(tris, self.tol), self.tol))
                    except Exception:  # noqa: BLE001
                        pass
                continue
            for r in (space.BoundedBy or []):
                if r.RelatedBuildingElement is not None:
                    bounded_ids.add(r.RelatedBuildingElement.id())
            room = self._build_room(space)
            if room is not None:
                rooms.append(room)
        if not rooms:
            raise ValueError('No usable IfcSpace with space boundaries found')
        self.timings['rooms'] = round(time.time() - t_rooms, 1)

        t_adj = time.time()
        # split coincident faces so a slab under several rooms (attic floor)
        # gets one piece per room; walls across a thickness are not coincident
        # and are paired by _pair_internal_faces
        try:
            Room.intersect_adjacency(rooms, self.tol, ANGLE_TOL)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append('coplanar split failed: {}'.format(exc))
        adj = Room.solve_adjacency(rooms, self.tol)
        n_adj = len(adj.get('adjacent_faces', []))
        n_adj += self._pair_internal_faces(rooms)
        self.report['air_boundaries'] = self.report.get('air_boundaries', 0) + self._voids_to_air(rooms)
        # after the coincident split, a small exterior top piece with only a
        # virtual boundary at its center is the strip under a wall of the zone
        # above (the zone above stops at the wall): nothing outdoors touches it
        n_slivers = 0
        for room in rooms:
            tops = [f for f in room.faces if f.type == face_types.roof_ceiling]
            top_area = sum(f.area for f in tops)
            for f in tops:
                if f.boundary_condition.name == 'Outdoors' and not f.has_sub_faces and                         top_area and f.area < 0.4 * top_area and self._is_virtual_at(room, f.geometry):
                    f.boundary_condition = bcs.adiabatic
                    n_slivers += 1
        if n_slivers:
            self.warnings.append('{} small virtual top pieces set adiabatic (under walls of the zone above)'.format(n_slivers))
        self._unify_adjacent_constructions(rooms)
        self.timings['adjacency'] = round(time.time() - t_adj, 1)
        # internal boundaries that found no neighbour become adiabatic; an
        # adiabatic face cannot carry doors/apertures, so those are dropped
        n_adiabatic, dropped, n_to_excluded = 0, 0, 0
        for room in rooms:
            for face in room.faces:
                ud = face.user_data or {}
                if not ud.get('matched') and face.boundary_condition.name != 'Surface':
                    ud['unmatched_outdoors'] = True  # no boundary, no neighbour: assume exterior
                if ud.get('internal') and face.boundary_condition.name != 'Surface':
                    if self._faces_excluded_space(face.geometry, excluded_polys):
                        # an open yard, a pool, an open garage: outdoors after all
                        face.boundary_condition = bcs.outdoors
                        ud['internal'] = False
                        ud['faces_excluded_space'] = True
                        n_to_excluded += 1
                        continue
                    if face.has_sub_faces:
                        dropped += len(face.apertures) + len(face.doors)
                        face.remove_sub_faces()
                    face.boundary_condition = bcs.adiabatic
                    n_adiabatic += 1
        if dropped:
            self.warnings.append(
                '{} interior doors/windows dropped from unmatched internal faces'.format(dropped))
        if n_to_excluded:
            self.warnings.append(
                '{} faces towards excluded spaces set to Outdoors'.format(n_to_excluded))

        t_ctx = time.time()
        shades = self._context_shades(bounded_ids, rooms=rooms, reach=self.context_reach) \
            if self.include_context else []
        self.timings['context'] = round(time.time() - t_ctx, 1)
        model = Model(_ident(self.f.by_type('IfcProject')[0].Name or 'ifc_model'),
                      rooms, orphaned_shades=shades, units='Meters',
                      tolerance=self.tol, angle_tolerance=ANGLE_TOL)
        model.display_name = self.f.by_type('IfcProject')[0].Name or 'IFC model'
        if self.local_coords and (abs(self.site_rotation) > 0.01 or any(abs(v) > self.tol for v in self.site_offset)):
            # world = R(site) * local + T  ->  local = R(-site) * (world - T)
            if any(abs(v) > self.tol for v in self.site_offset):
                model.move(Vector3D(*[-v for v in self.site_offset]))
            if abs(self.site_rotation) > 0.01:
                model.rotate_xy(-self.site_rotation, Point3D(0, 0, 0))
            self.warnings.append('site placement undone: rotation {:.3f} deg -> north {:.3f}'.format(
                self.site_rotation, self.location['north'] if self.location else 0.0))
        self.model = model
        bc_counts = Counter(f.boundary_condition.name for r in rooms for f in r.faces)
        type_counts = Counter(str(f.type) for r in rooms for f in r.faces)
        self.report.update({
            'rooms_total': len(rooms),
            'faces': sum(len(r.faces) for r in rooms),
            'apertures': sum(len(f.apertures) for r in rooms for f in r.faces),
            'doors': sum(len(f.doors) for r in rooms for f in r.faces),
            'glass_doors': sum(1 for r in rooms for f in r.faces for d in f.doors if d.is_glass),
            'adjacent_pairs': n_adj, 'adiabatic': n_adiabatic,
            'boundary_conditions': dict(bc_counts), 'face_types': dict(type_counts),
            'shades': len(shades),
            'constructions': len(self.lib.constructions),
            'window_constructions': len(self.lib.window_constructions),
            'location': self.location,
            'timings': self.timings,
            'warnings': self.warnings + self.lib.warnings,
        })
        return model

    def write_hbjson(self, path):
        import json
        import os
        if self.model is None:
            self.build()
        folder, name = os.path.split(path)
        name = re.sub(r'\.hbjson$', '', name)
        return self.model.to_hbjson(name, folder or '.')

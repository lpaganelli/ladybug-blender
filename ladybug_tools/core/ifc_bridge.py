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
CONTEXT_CLASSES = ('IfcRoof', 'IfcSlab', 'IfcWall', 'IfcColumn', 'IfcCurtainWall')


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
        try:
            joined = Face3D.join_coplanar_faces(members, tol)
        except Exception:  # noqa: BLE001
            joined = []
        if not joined:
            out.extend(members)
            continue
        for j in joined:
            try:
                out.append(j.remove_colinear_vertices(tol))
            except Exception:  # noqa: BLE001
                out.append(j)
    return out


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
                 ground_level=0.0, tolerance=TOL, context_reach=10.0):
        self.path = ifc_path
        self.exclude = tuple(e.lower() for e in exclude)
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
        self.location = self._site_location()

    # ---- metadata ----
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
        return {'latitude': dms(s.RefLatitude), 'longitude': dms(s.RefLongitude),
                'elevation': (s.RefElevation or 0.0) * self.scale, 'name': s.Name}

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
            counts[str(face.type)] += 1

        # ---- windows / doors ----
        for rel, el, g in subs:
            parent = self._parent_face(room.faces, g)
            if parent is None:
                counts['orphan_openings'] += 1
                self.warnings.append('{}: no host face for {} "{}"'.format(name, el.is_a(), el.Name))
                continue
            gp = self._project_onto(parent.geometry, g)
            if gp is None:
                counts['orphan_openings'] += 1
                continue
            # skip duplicates (a window exported twice, or overlapping boundaries)
            if any(gp.is_overlapping(s.geometry, self.tol) for s in parent.sub_faces):
                counts['duplicate_openings'] += 1
                continue
            ident = _ident('{}_{}_{}'.format(parent.identifier, el.Name, rel.id()))
            if el.is_a('IfcDoor'):
                obj = Door(ident, gp, is_glass=False)
                obj.properties.energy.construction = self.lib.construction_for(el) or \
                    parent.properties.energy.construction
                parent.add_door(obj)
                counts['doors'] += 1
            else:
                obj = Aperture(ident, gp)
                obj.properties.energy.construction = self.lib.window_construction(el)
                parent.add_aperture(obj)
                counts['apertures'] += 1
            obj.display_name = el.Name
            obj.user_data = {'ifc_guid': el.GlobalId, 'ifc_class': el.is_a()}

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
        # internal faces, plus faces that matched no boundary at all (open-plan
        # zone limits without a wall are exported without a boundary by some tools)
        candidates = [(r, f) for r in rooms for f in r.faces
                      if ((f.user_data or {}).get('internal') or
                          not (f.user_data or {}).get('matched'))
                      and f.boundary_condition.name != 'Surface']
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
            if coincident and not fa.has_sub_faces and not best.has_sub_faces and \
                    fa.type == face_types.wall and best.type == face_types.wall:
                # two zones touching without a wall between them: open plan
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

    def _parent_face(self, faces, g):
        best, best_d = None, None
        for face in faces:
            if face.type != face_types.wall and face.type != face_types.roof_ceiling:
                continue
            plane = face.geometry.plane
            if abs(plane.n.dot(g.normal)) < 0.95 and abs(plane.n.dot(g.normal)) > -0.95:
                if abs(abs(plane.n.dot(g.normal)) - 1) > 0.05:
                    continue
            d = abs(plane.distance_to_point(g.center))
            if d > 0.3:
                continue
            if not face.geometry.is_point_on_face(plane.closest_point(g.center), self.tol * 10):
                continue
            if best is None or d < best_d:
                best, best_d = face, d
        return best

    def _project_onto(self, parent_geo, g):
        plane = parent_geo.plane
        pts = [plane.closest_point(p) for p in g.boundary]
        try:
            proj = Face3D(pts, plane)
            proj = proj.remove_colinear_vertices(self.tol)
        except Exception:  # noqa: BLE001
            return None
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
        for cls in CONTEXT_CLASSES:
            for el in self.f.by_type(cls):
                if el.id() in bounded_ids:
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
                for i, g in enumerate(merged):
                    sh = Shade(_ident('{}_{}_{}'.format(cls, el.id(), i)), g, is_detached=True)
                    sh.display_name = el.Name or cls
                    sh.user_data = {'ifc_guid': el.GlobalId, 'ifc_class': cls}
                    shades.append(sh)
        return shades

    # ---- main ----
    def build(self):
        rooms = []
        bounded_ids = set()
        self.timings = {}
        t_rooms = time.time()
        for space in self.f.by_type('IfcSpace'):
            label = '{} {}'.format(space.Name or '', space.LongName or '').lower()
            if any(x in label for x in self.exclude):
                self.warnings.append('space "{}" excluded by name'.format(label.strip()))
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
        adj = Room.solve_adjacency(rooms, self.tol)
        n_adj = len(adj.get('adjacent_faces', []))
        n_adj += self._pair_internal_faces(rooms)
        self.timings['adjacency'] = round(time.time() - t_adj, 1)
        # internal boundaries that found no neighbour become adiabatic; an
        # adiabatic face cannot carry doors/apertures, so those are dropped
        n_adiabatic, dropped = 0, 0
        for room in rooms:
            for face in room.faces:
                ud = face.user_data or {}
                if not ud.get('matched') and face.boundary_condition.name != 'Surface':
                    ud['unmatched_outdoors'] = True  # no boundary, no neighbour: assume exterior
                if ud.get('internal') and face.boundary_condition.name != 'Surface':
                    if face.has_sub_faces:
                        dropped += len(face.apertures) + len(face.doors)
                        face.remove_sub_faces()
                    face.boundary_condition = bcs.adiabatic
                    n_adiabatic += 1
        if dropped:
            self.warnings.append(
                '{} interior doors/windows dropped from unmatched internal faces'.format(dropped))

        t_ctx = time.time()
        shades = self._context_shades(bounded_ids, rooms=rooms, reach=self.context_reach) \
            if self.include_context else []
        self.timings['context'] = round(time.time() - t_ctx, 1)
        model = Model(_ident(self.f.by_type('IfcProject')[0].Name or 'ifc_model'),
                      rooms, orphaned_shades=shades, units='Meters',
                      tolerance=self.tol, angle_tolerance=ANGLE_TOL)
        model.display_name = self.f.by_type('IfcProject')[0].Name or 'IFC model'
        self.model = model
        bc_counts = Counter(f.boundary_condition.name for r in rooms for f in r.faces)
        type_counts = Counter(str(f.type) for r in rooms for f in r.faces)
        self.report.update({
            'rooms_total': len(rooms),
            'faces': sum(len(r.faces) for r in rooms),
            'apertures': sum(len(f.apertures) for r in rooms for f in r.faces),
            'doors': sum(len(f.doors) for r in rooms for f in r.faces),
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

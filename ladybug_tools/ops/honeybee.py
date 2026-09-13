# -*- coding: utf-8 -*-
"""IFC -> Honeybee model operators (needs ifcopenshell, e.g. from Bonsai)."""
import os

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from . import common

_LAST_MODEL = {'model': None, 'report': None, 'path': None}


def ifcopenshell_available():
    try:
        import ifcopenshell  # noqa: F401
        return True
    except ImportError:
        return False


def bonsai_ifc_path():
    """Path of the IFC currently loaded in Bonsai, if any."""
    try:
        import bonsai.tool as tool
        return tool.Ifc.get_path() or ''
    except Exception:  # noqa: BLE001
        return ''


class LB_OT_ifc_pick(bpy.types.Operator, ImportHelper):
    """Choose the IFC file to convert"""
    bl_idname = 'ladybug.ifc_pick'
    bl_label = 'Pick IFC'
    filename_ext = '.ifc'
    filter_glob: StringProperty(default='*.ifc', options={'HIDDEN'})

    def execute(self, context):
        common.props(context).hb_ifc_path = self.filepath
        return {'FINISHED'}


class LB_OT_ifc_to_honeybee(bpy.types.Operator):
    """Build a Honeybee model from the IFC (spaces -> rooms) and draw it"""
    bl_idname = 'ladybug.ifc_to_honeybee'
    bl_label = 'IFC to Honeybee'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = common.props(context)
        if not ifcopenshell_available():
            self.report({'ERROR'}, 'ifcopenshell not found: install Bonsai in this Blender')
            return {'CANCELLED'}
        path = bpy.path.abspath(p.hb_ifc_path) or bonsai_ifc_path()
        if not path or not os.path.isfile(path):
            self.report({'ERROR'}, 'Pick an IFC file (or load one in Bonsai)')
            return {'CANCELLED'}
        from ..core.ifc_bridge import IfcToHoneybee
        from ..core import hb_viz
        exclude = [s.strip().lower() for s in p.hb_exclude.split(',') if s.strip()]
        try:
            bridge = IfcToHoneybee(path, exclude=exclude, include_context=p.hb_context,
                                   ground_level=p.hb_ground_level, glass_doors=p.hb_glass_doors)
            model = bridge.build()
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, 'IFC bridge failed: {}'.format(exc))
            return {'CANCELLED'}
        _LAST_MODEL.update(model=model, report=bridge.report, path=path)
        loc = bridge.location
        if loc and loc.get('latitude') is not None and not p.epw_loaded:
            p.latitude, p.longitude = loc['latitude'], loc['longitude']
        if loc and loc.get('north'):  # IFC TrueNorth, when the BIM tool set one
            p.north = loc['north']
        if p.hb_draw:
            hb_viz.draw_model(context, model, p.hb_color_by)
        rep = bridge.report
        msg = '{} rooms, {} faces, {} apertures, {} doors ({} glass), {} shades | BC {} | {} warnings'.format(
            rep['rooms_total'], rep['faces'], rep['apertures'], rep['doors'],
            rep.get('glass_doors', 0), rep['shades'], rep['boundary_conditions'], len(rep['warnings']))
        for w in rep['warnings']:
            print('[Ladybug] IFC bridge:', w)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class LB_OT_hbjson_export(bpy.types.Operator, ImportHelper):
    """Write the last Honeybee model to an HBJSON file"""
    bl_idname = 'ladybug.hbjson_export'
    bl_label = 'Export HBJSON'
    filename_ext = '.hbjson'
    filter_glob: StringProperty(default='*.hbjson', options={'HIDDEN'})

    def invoke(self, context, event):
        if _LAST_MODEL['path']:
            base = os.path.splitext(os.path.basename(_LAST_MODEL['path']))[0]
            self.filepath = os.path.join(os.path.dirname(_LAST_MODEL['path']), base + '.hbjson')
        return super().invoke(context, event)

    def execute(self, context):
        model = _LAST_MODEL['model']
        if model is None:
            self.report({'ERROR'}, 'Run IFC to Honeybee first')
            return {'CANCELLED'}
        folder, name = os.path.split(self.filepath)
        name = name[:-7] if name.lower().endswith('.hbjson') else name
        out = model.to_hbjson(name, folder)
        self.report({'INFO'}, 'Wrote {} ({:.1f} MB)'.format(out, os.path.getsize(out) / 1e6))
        return {'FINISHED'}


class LB_OT_hb_redraw(bpy.types.Operator):
    """Redraw the last Honeybee model with the chosen coloring"""
    bl_idname = 'ladybug.hb_redraw'
    bl_label = 'Redraw'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        model = _LAST_MODEL['model']
        if model is None:
            self.report({'ERROR'}, 'Run IFC to Honeybee first')
            return {'CANCELLED'}
        from ..core import hb_viz
        n = hb_viz.draw_model(context, model, common.props(context).hb_color_by)
        self.report({'INFO'}, '{} rooms drawn'.format(n))
        return {'FINISHED'}


CLASSES = (LB_OT_ifc_pick, LB_OT_ifc_to_honeybee, LB_OT_hbjson_export, LB_OT_hb_redraw)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

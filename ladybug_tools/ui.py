# -*- coding: utf-8 -*-
"""N-panel UI (3D Viewport > Sidebar > Ladybug)."""
import bpy


class LBPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ladybug'


class LB_PT_weather(LBPanel, bpy.types.Panel):
    bl_label = 'Weather & Location'
    bl_idname = 'LB_PT_weather'

    def draw(self, context):
        p = context.scene.ladybug
        layout = self.layout
        row = layout.row(align=True)
        row.prop(p, 'epw_path', text='', placeholder='weather.epw')
        row.operator('ladybug.load_epw', text='', icon='FILEBROWSER')
        row.operator('ladybug.reload_epw', text='', icon='FILE_REFRESH')
        if p.epw_loaded:
            layout.label(text='{} {}'.format(p.city, p.country), icon='WORLD')
            layout.operator('ladybug.epw_summary', icon='INFO')
        else:
            layout.label(text='No EPW loaded (manual location)', icon='ERROR')
        col = layout.column(align=True)
        col.prop(p, 'latitude')
        col.prop(p, 'longitude')
        col.prop(p, 'time_zone')
        col.prop(p, 'elevation')
        layout.prop(p, 'north')
        row = layout.row(align=True)
        row.label(text='Location:')
        row.operator('ladybug.location_from_epw', icon='WORLD')
        row.operator('ladybug.location_from_ifc', icon='HOME')


class LB_PT_period(LBPanel, bpy.types.Panel):
    bl_label = 'Analysis Period'
    bl_idname = 'LB_PT_period'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.ladybug
        layout = self.layout
        grid = layout.grid_flow(columns=3, align=True)
        for ident, label, _desc in (('YEAR', 'Year', ''), ('SUMMER', 'Summer', ''),
                                    ('WINTER', 'Winter', ''),
                                    ('SUMMER_SOLSTICE', 'Sum. Solstice', ''),
                                    ('WINTER_SOLSTICE', 'Win. Solstice', ''),
                                    ('EQUINOX', 'Equinox', '')):
            grid.operator('ladybug.period_preset', text=label).preset = ident
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(p, 'ap_st_month', text='')
        row.prop(p, 'ap_st_day', text='Day')
        row.prop(p, 'ap_st_hour', text='Hour')
        row = col.row(align=True)
        row.prop(p, 'ap_end_month', text='')
        row.prop(p, 'ap_end_day', text='Day')
        row.prop(p, 'ap_end_hour', text='Hour')
        layout.prop(p, 'ap_timestep')


class LB_PT_sunpath(LBPanel, bpy.types.Panel):
    bl_label = 'Sun Path'
    bl_idname = 'LB_PT_sunpath'

    def draw(self, context):
        p = context.scene.ladybug
        layout = self.layout
        layout.prop(p, 'sp_radius')
        col = layout.column(align=True)
        col.prop(p, 'sp_analemmas')
        col.prop(p, 'sp_day_arcs')
        col.prop(p, 'sp_compass')
        row = col.row(align=True)
        row.prop(p, 'sp_suns')
        row.prop(p, 'sp_sun_step', text='every')
        col.prop(p, 'sp_daytime_only')
        col.prop(p, 'sp_solar_time')
        col.prop(p, 'sp_bevel')
        layout.operator('ladybug.draw_sunpath', icon='LIGHT_SUN')

        box = layout.box()
        box.label(text='Sun Position', icon='TIME')
        row = box.row(align=True)
        row.prop(p, 'sun_month', text='')
        row.prop(p, 'sun_day', text='Day')
        box.prop(p, 'sun_hour')
        box.prop(p, 'sun_strength')
        row = box.row(align=True)
        row.operator('ladybug.set_sun', icon='LIGHT_SUN')
        row = box.row(align=True)
        row.prop(p, 'anim_frames_per_hour')
        row.operator('ladybug.animate_sun', text='Animate', icon='PLAY')


class LB_PT_studies(LBPanel, bpy.types.Panel):
    bl_label = 'Solar Studies'
    bl_idname = 'LB_PT_studies'

    def draw(self, context):
        p = context.scene.ladybug
        layout = self.layout
        studies = [o for o in context.selected_objects
                   if o.type == 'MESH' and not o.get('ladybug_generated')]
        sources = {o.get('ladybug_source') for o in context.selected_objects
                   if o.get('ladybug_source')}
        n = len(studies) + len(sources - {o.name for o in studies})
        ob = context.active_object
        if n > 1:
            layout.label(text='Study: {} objects'.format(n), icon='MESH_GRID')
        elif n == 1 or (ob is not None and ob.type == 'MESH'):
            name = studies[0].name if studies else (
                next(iter(sources)) if sources else ob.get('ladybug_source', ob.name))
            layout.label(text='Study: {}'.format(name), icon='MESH_GRID')
        else:
            layout.label(text='Select mesh objects as study geometry', icon='ERROR')

        box = layout.box()
        box.label(text='Sensor Grid', icon='GRID')
        row = box.row(align=True)
        row.prop(p, 'st_cell_size')
        row = box.row(align=True)
        row.operator('ladybug.sensor_grid', text='Add Grid', icon='MOD_REMESH')
        row.operator('ladybug.remove_sensor_grid', text='Remove', icon='X')

        col = layout.column(align=True)
        col.prop(p, 'st_context')
        col.prop(p, 'st_include_self')
        col.prop(p, 'st_offset')
        col.prop(p, 'st_by_vertex')
        col.prop(p, 'st_cache_year')
        col.prop(p, 'st_persist_cache')
        layout.operator('ladybug.direct_sun_hours', icon='OUTLINER_OB_LIGHT')

        box = layout.box()
        box.label(text='Sky', icon='WORLD')
        box.prop(p, 'st_sky', text='')
        if p.st_sky == 'CLEAR':
            box.prop(p, 'st_clearness')
        box.prop(p, 'st_high_density')
        box.prop(p, 'st_ground_reflectance')
        box.prop(p, 'st_irradiance')
        layout.operator('ladybug.incident_radiation', icon='SHADING_RENDERED')

        box = layout.box()
        box.label(text='Results', icon='HIDE_OFF')
        box.prop(p, 'st_show', expand=True)
        box.operator('ladybug.rebuild_legend', icon='FILE_REFRESH')
        row = box.row(align=True)
        row.operator('ladybug.clear_results', text='Clear Selected', icon='TRASH').only_active = True
        row.operator('ladybug.clear_results', text='Clear All', icon='TRASH').only_active = False


class LB_PT_explorer(LBPanel, bpy.types.Panel):
    bl_label = 'Period Explorer'
    bl_idname = 'LB_PT_explorer'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.ladybug
        layout = self.layout
        layout.label(text='Recolor results for another period (no ray tracing)',
                     icon='TIME')
        layout.prop(p, 'ex_mode', expand=True)
        col = layout.column(align=True)
        if p.ex_mode in {'MONTH', 'DAY'}:
            col.prop(p, 'ex_month', slider=True)
        if p.ex_mode == 'DAY':
            col.prop(p, 'ex_day', slider=True)
        if p.ex_mode != 'PERIOD':
            row = col.row(align=True)
            row.prop(p, 'ex_st_hour', slider=True)
            row.prop(p, 'ex_end_hour', slider=True)
        row = layout.row(align=True)
        row.prop(p, 'ex_live')
        row.operator('ladybug.explore_period', icon='PLAY')


class LB_PT_climate(LBPanel, bpy.types.Panel):
    bl_label = 'Climate Graphics'
    bl_idname = 'LB_PT_climate'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.ladybug
        layout = self.layout
        layout.prop(p, 'viz_radius')
        layout.prop(p, 'rad_type')
        box = layout.box()
        box.prop(p, 'dome_projection')
        box.operator('ladybug.sky_dome', icon='SPHERE')
        box = layout.box()
        row = box.row(align=True)
        row.prop(p, 'rr_directions')
        row.prop(p, 'rr_tilt')
        box.operator('ladybug.radiation_rose', icon='ORIENTATION_VIEW')
        box = layout.box()
        box.prop(p, 'wr_data', text='')
        row = box.row(align=True)
        row.prop(p, 'wr_directions')
        row.prop(p, 'wr_rings')
        col = box.column(align=True)
        col.prop(p, 'wr_show_freq')
        row = col.row()
        row.active = p.wr_data == 'wind_speed'
        row.prop(p, 'wr_show_calm')
        box.operator('ladybug.wind_rose', icon='FORCE_WIND')


class LB_PT_honeybee(LBPanel, bpy.types.Panel):
    bl_label = 'Honeybee (IFC)'
    bl_idname = 'LB_PT_honeybee'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        from .ops.honeybee import ifcopenshell_available
        p = context.scene.ladybug
        layout = self.layout
        if not ifcopenshell_available():
            layout.label(text='Needs Bonsai (ifcopenshell) in this Blender', icon='ERROR')
        row = layout.row(align=True)
        row.prop(p, 'hb_ifc_path', text='', placeholder='IFC loaded in Bonsai')
        row.operator('ladybug.ifc_pick', text='', icon='FILEBROWSER')
        col = layout.column(align=True)
        col.prop(p, 'hb_exclude')
        col.prop(p, 'hb_glass_doors', placeholder='PA06, PA09, -PA10')
        col.prop(p, 'hb_ground_level')
        col.prop(p, 'hb_context')
        col.prop(p, 'hb_local_coords')
        col.prop(p, 'hb_draw')
        layout.operator('ladybug.ifc_to_honeybee', icon='HOME')
        row = layout.row(align=True)
        row.prop(p, 'hb_color_by', text='')
        row.operator('ladybug.hb_redraw', text='', icon='FILE_REFRESH')
        row = layout.row(align=True)
        row.operator('ladybug.hbjson_export', icon='EXPORT')
        row.operator('ladybug.hb_openings', icon='PRESET')

        box = layout.box()
        box.label(text='EnergyPlus', icon='LIGHT_DATA')
        box.prop(p, 'en_ep_path', text='', placeholder='auto-detect EnergyPlus')
        box.prop(p, 'en_hvac', text='')
        if p.en_hvac == 'FREE_RUNNING':
            col = box.column(align=True)
            col.prop(p, 'en_vent_min_indoor')
            row = col.row(align=True)
            row.prop(p, 'en_vent_min_outdoor')
            row.prop(p, 'en_vent_max_outdoor')
            col.prop(p, 'en_operable_default')
            col.prop(p, 'en_window_openings', placeholder='JA01=0, JA02=0.5, JA04=0.75')
        row = box.row(align=True)
        row.prop(p, 'en_comfort_low')
        row.prop(p, 'en_comfort_high')
        box.prop(p, 'en_timestep')
        box.operator('ladybug.energy_simulate', icon='PLAY')
        row = box.row(align=True)
        row.prop(p, 'en_metric', text='')
        row.operator('ladybug.energy_color', text='', icon='COLOR')
        row.operator('ladybug.energy_report', text='', icon='INFO')


class LB_PT_legend(LBPanel, bpy.types.Panel):
    bl_label = 'Legend'
    bl_idname = 'LB_PT_legend'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.ladybug
        layout = self.layout
        layout.prop(p, 'lg_colorset')
        layout.prop(p, 'lg_segments')
        layout.prop(p, 'lg_use_range')
        row = layout.row(align=True)
        row.active = p.lg_use_range
        row.prop(p, 'lg_min')
        row.prop(p, 'lg_max')
        layout.prop(p, 'lg_show')
        col = layout.column(align=True)
        col.active = p.lg_show
        col.prop(p, 'lg_size')
        col.prop(p, 'lg_position', text='')
        col.prop(p, 'lg_orientation', text='')
        layout.operator('ladybug.rebuild_legend', icon='FILE_REFRESH')


CLASSES = (LB_PT_weather, LB_PT_period, LB_PT_sunpath, LB_PT_studies, LB_PT_explorer,
           LB_PT_climate, LB_PT_honeybee, LB_PT_legend)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)

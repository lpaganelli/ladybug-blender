# -*- coding: utf-8 -*-
"""Scene properties for the Ladybug Tools panel."""
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)

COLORSETS = [
    ('original', 'Original', 'Ladybug default blue-red'),
    ('nuanced', 'Nuanced', ''),
    ('multi_colored', 'Multi Colored', ''),
    ('ecotect', 'Ecotect', ''),
    ('view_study', 'View Study', ''),
    ('shadow_study', 'Shadow Study', ''),
    ('glare_study', 'Glare Study', ''),
    ('annual_comfort', 'Annual Comfort', ''),
    ('thermal_comfort', 'Thermal Comfort', ''),
    ('benefit_harm', 'Benefit / Harm', ''),
    ('cloud_cover', 'Cloud Cover', ''),
    ('black_to_white', 'Black to White', ''),
    ('blue_green_red', 'Blue Green Red', ''),
    ('viridis', 'Viridis', ''),
    ('cividis', 'Cividis', ''),
    ('parula', 'Parula', ''),
]

MONTHS = [(str(i), m, '') for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov',
     'Dec'], start=1)]


def _epw_changed(self, context):
    """Load the EPW header when the path changes."""
    path = bpy.path.abspath(self.epw_path)
    if not path:
        self.epw_loaded = False
        return
    try:
        from .core import cache
        epw = cache.get_epw(path)
    except Exception as exc:  # noqa: BLE001
        print('[Ladybug] Could not read EPW:', exc)
        self.epw_loaded = False
        return
    loc = epw.location
    self.city = loc.city or ''
    self.country = loc.country or ''
    self.latitude = float(loc.latitude)
    self.longitude = float(loc.longitude)
    self.time_zone = float(loc.time_zone)
    self.elevation = float(loc.elevation)
    self.epw_loaded = True


def _show_changed(self, context):
    from .ops.studies import apply_study_visibility
    apply_study_visibility(context)


def _explorer_changed(self, context):
    if not self.ex_live:
        return
    from .ops.studies import explore_period
    try:
        explore_period(context)
    except Exception as exc:  # noqa: BLE001 (never raise from a property update)
        print('[Ladybug] Period explorer:', exc)


class LBSceneProps(bpy.types.PropertyGroup):
    # ---- weather / location ----
    # plain string (no FILE_PATH subtype) so the panel shows a single browse
    # button: the Load EPW operator, which filters *.epw files
    epw_path: StringProperty(
        name='EPW File', update=_epw_changed,
        description='EnergyPlus Weather file (.epw); use the folder button to browse')
    epw_loaded: BoolProperty(default=False)
    city: StringProperty(name='City', default='')
    country: StringProperty(name='Country', default='')
    latitude: FloatProperty(name='Latitude', default=-23.55, min=-90, max=90,
                            precision=4)
    longitude: FloatProperty(name='Longitude', default=-46.63, min=-180, max=180,
                             precision=4)
    time_zone: FloatProperty(name='Time Zone', default=-3, min=-12, max=14,
                             precision=1)
    elevation: FloatProperty(name='Elevation', default=0, unit='LENGTH')
    north: FloatProperty(
        name='North', default=0.0, min=-360, max=360,
        description='Counterclockwise angle (degrees) from +Y to project north')

    # ---- analysis period ----
    ap_st_month: EnumProperty(name='Start Month', items=MONTHS, default='1')
    ap_st_day: IntProperty(name='Start Day', default=1, min=1, max=31)
    ap_st_hour: IntProperty(name='Start Hour', default=0, min=0, max=23)
    ap_end_month: EnumProperty(name='End Month', items=MONTHS, default='12')
    ap_end_day: IntProperty(name='End Day', default=31, min=1, max=31)
    ap_end_hour: IntProperty(name='End Hour', default=23, min=0, max=23)
    ap_timestep: EnumProperty(
        name='Timestep', items=[(str(t), '{}/h'.format(t), '') for t in (1, 2, 4, 6)],
        default='1')

    # ---- sun path ----
    sp_radius: FloatProperty(name='Radius', default=20.0, min=0.01, unit='LENGTH')
    sp_daytime_only: BoolProperty(name='Daytime Only', default=True)
    sp_solar_time: BoolProperty(name='Solar Time', default=False)
    sp_analemmas: BoolProperty(name='Hourly Analemmas', default=True)
    sp_day_arcs: BoolProperty(name='Monthly Day Arcs', default=True)
    sp_suns: BoolProperty(name='Sun Points (period)', default=True)
    sp_sun_step: IntProperty(name='Sun Every (h)', default=1, min=1, max=24)
    sp_compass: BoolProperty(name='Compass', default=True)
    sp_bevel: FloatProperty(name='Line Thickness', default=0.0, min=0.0,
                            unit='LENGTH')

    # ---- sun position ----
    sun_month: EnumProperty(name='Month', items=MONTHS, default='6')
    sun_day: IntProperty(name='Day', default=21, min=1, max=31)
    sun_hour: FloatProperty(name='Hour', default=12.0, min=0.0, max=23.99, step=50,
                            precision=2)
    sun_strength: FloatProperty(name='Sun Strength', default=3.0, min=0.0)
    anim_frames_per_hour: IntProperty(name='Frames per Hour', default=4, min=1,
                                      max=60)

    # ---- studies ----
    st_sky: EnumProperty(
        name='Sky', items=[('EPW', 'EPW Weather', 'Perez sky from EPW radiation'),
                           ('CLEAR', 'ASHRAE Clear Sky', 'Clear sky from location')],
        default='EPW')
    st_clearness: FloatProperty(name='Sky Clearness', default=1.0, min=0.0, max=1.2)
    st_context: EnumProperty(
        name='Context', items=[
            ('SELECTED', 'Selected Objects', 'Other selected mesh objects shade the study'),
            ('VISIBLE', 'All Visible', 'Every visible mesh object shades the study'),
            ('NONE', 'None', 'No obstruction other than the study mesh itself')],
        default='SELECTED')
    st_include_self: BoolProperty(
        name='Self Shading', default=True,
        description='Let the study mesh shade itself')
    st_offset: FloatProperty(
        name='Offset', default=0.01, min=0.0, unit='LENGTH', precision=4,
        description='Distance the result copy (and its sensors) is pushed along the '
                    'normals of the source geometry')
    st_by_vertex: BoolProperty(name='Per Vertex', default=False,
                               description='Compute at vertices instead of faces')
    st_high_density: BoolProperty(name='High Density Sky (Reinhart)', default=False)
    st_ground_reflectance: FloatProperty(name='Ground Reflectance', default=0.2,
                                         min=0.0, max=1.0)
    st_irradiance: BoolProperty(name='Average Irradiance (W/m2)', default=False)
    st_cache_year: BoolProperty(
        name='Compute Full Year', default=False,
        description='Direct Sun Hours: trace every sun position of the year so the '
                    'Period Explorer can show any period without recomputing')
    st_persist_cache: BoolProperty(
        name='Save Cache in File', default=True,
        description='Store the visibility matrix on each result object so the '
                    'Period Explorer works after reopening the .blend (larger file)')
    # ---- honeybee / IFC bridge ----
    hb_ifc_path: StringProperty(name='IFC File', default='',
                                description='IFC to convert (empty: the one loaded in Bonsai)')
    hb_exclude: StringProperty(
        name='Exclude', default='piscina, pool',
        description='Comma-separated words; spaces whose name contains one are skipped')
    hb_context: BoolProperty(name='Context Shades', default=True,
                             description='Add unbounded roofs, slabs and walls as shading')
    hb_ground_level: FloatProperty(name='Ground Level', default=0.0, unit='LENGTH',
                                   description='Floors at or below this height touch the ground')
    hb_draw: BoolProperty(name='Draw Rooms', default=True)
    hb_color_by: EnumProperty(
        name='Color By', default='BC',
        items=[('BC', 'Boundary Condition', 'Outdoors, Ground, Surface, Adiabatic'),
               ('TYPE', 'Face Type', 'Wall, Floor, RoofCeiling, AirBoundary')])
    # ---- period explorer (recolors cached results, no ray tracing) ----
    ex_mode: EnumProperty(
        name='Explore', update=_explorer_changed,
        items=[('YEAR', 'Year', 'Whole year'),
               ('MONTH', 'Month', 'One month'),
               ('DAY', 'Day', 'One day'),
               ('PERIOD', 'Period', 'The Analysis Period panel')],
        default='MONTH')
    ex_month: IntProperty(name='Month', default=6, min=1, max=12,
                          update=_explorer_changed)
    ex_day: IntProperty(name='Day', default=21, min=1, max=31,
                        update=_explorer_changed)
    ex_st_hour: IntProperty(name='From', default=0, min=0, max=23,
                            update=_explorer_changed)
    ex_end_hour: IntProperty(name='To', default=23, min=0, max=23,
                             update=_explorer_changed)
    ex_live: BoolProperty(name='Live Update', default=True,
                          description='Recolor while dragging the sliders')
    st_cell_size: FloatProperty(
        name='Cell Size', default=0.5, min=0.01, unit='LENGTH', precision=3,
        description='Target size of each sensor cell of the analysis grid')
    st_show: EnumProperty(
        name='Show', update=_show_changed,
        description='Which study results (and legends) are visible',
        items=[('ALL', 'All', 'Show every result'),
               ('Sun Hours', 'Sun Hours', 'Only direct sun hours results'),
               ('Radiation', 'Radiation', 'Only incident radiation results'),
               ('Irradiance', 'Irradiance', 'Only average irradiance results')],
        default='ALL')

    # ---- legend ----
    lg_colorset: EnumProperty(name='Colors', items=COLORSETS, default='original')
    lg_use_range: BoolProperty(name='Custom Range', default=False)
    lg_min: FloatProperty(name='Min', default=0.0)
    lg_max: FloatProperty(name='Max', default=10.0)
    lg_segments: IntProperty(name='Segments', default=11, min=2, max=64)
    lg_show: BoolProperty(name='Draw Legend', default=True)
    lg_size: FloatProperty(
        name='Legend Height', default=2.0, min=0.05, unit='LENGTH',
        description='Total height of the legend bar in scene units')
    lg_position: EnumProperty(
        name='Position', default='BESIDE',
        items=[('BESIDE', 'Beside Results', 'Next to the bounding box of the batch'),
               ('CURSOR', '3D Cursor', 'At the 3D cursor')])
    lg_orientation: EnumProperty(
        name='Orientation', default='UPRIGHT',
        items=[('UPRIGHT', 'Upright', 'Standing in the XZ plane, facing -Y'),
               ('FLAT', 'Flat', 'Lying on the XY plane (Ladybug default)')])

    # ---- climate graphics ----
    viz_radius: FloatProperty(name='Radius', default=10.0, min=0.01, unit='LENGTH')
    dome_projection: EnumProperty(
        name='Projection', items=[
            ('NONE', '3D Dome', ''), ('Orthographic', 'Orthographic', ''),
            ('Stereographic', 'Stereographic', ''), ('Equidistant', 'Equidistant', ''),
            ('Equisolid', 'Equisolid', '')],
        default='NONE')
    rad_type: EnumProperty(
        name='Radiation', items=[('total', 'Total', ''), ('direct', 'Direct', ''),
                                 ('diffuse', 'Diffuse', '')], default='total')
    rr_directions: IntProperty(name='Directions', default=36, min=3, max=180)
    rr_tilt: FloatProperty(name='Tilt', default=0.0, min=0.0, max=89.0)
    wr_directions: IntProperty(name='Directions', default=16, min=3, max=72)
    wr_show_calm: BoolProperty(name='Show Calm Hours', default=False)
    wr_rings: IntProperty(name='Frequency Rings', default=5, min=1, max=20,
                          description='Rings between the center and the busiest direction')
    wr_show_freq: BoolProperty(name='Show Frequency', default=True)
    wr_data: EnumProperty(
        name='Data', items=[('wind_speed', 'Wind Speed', ''),
                            ('dry_bulb_temperature', 'Dry Bulb Temperature', ''),
                            ('relative_humidity', 'Relative Humidity', '')],
        default='wind_speed')


def register():
    bpy.utils.register_class(LBSceneProps)
    bpy.types.Scene.ladybug = PointerProperty(type=LBSceneProps)


def unregister():
    del bpy.types.Scene.ladybug
    bpy.utils.unregister_class(LBSceneProps)

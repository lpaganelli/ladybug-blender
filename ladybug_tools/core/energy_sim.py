# -*- coding: utf-8 -*-
"""Energy simulation of a Honeybee model with EnergyPlus (phase 3).

* residential programs (people, lights, equipment, infiltration, setpoints)
  assigned from the room name in Portuguese or English;
* free-running (natural ventilation through the windows) or ideal-air HVAC;
* IDF written by honeybee-energy, EnergyPlus run from the folder chosen in
  the add-on preferences, results read back from the SQLite output.

No Blender dependency.
"""
import os
import re
import shutil
import time

from ladybug.epw import EPW
from ladybug.sql import SQLiteResult
from honeybee_energy.config import folders
from honeybee_energy.programtype import ProgramType
from honeybee_energy.load.people import People
from honeybee_energy.load.lighting import Lighting
from honeybee_energy.load.equipment import ElectricEquipment
from honeybee_energy.load.infiltration import Infiltration
from honeybee_energy.load.setpoint import Setpoint
from honeybee_energy.schedule.ruleset import ScheduleRuleset
from honeybee_energy.lib.scheduletypelimits import fractional, temperature, activity_level
from honeybee_energy.ventcool.control import VentilationControl
from honeybee_energy.ventcool.opening import VentilationOpening
from honeybee_energy.simulation.parameter import SimulationParameter
from honeybee_energy.writer import model_to_idf, energyplus_idf_version
from honeybee_energy.run import run_idf

SPACE_KINDS = (
    ('bedroom', ('quarto', 'suite', 'suíte', 'dorm', 'bedroom', 'closet')),
    ('living', ('sala', 'estar', 'living', 'jantar', 'escrit', 'office', 'tv')),
    ('kitchen', ('cozinha', 'copa', 'kitchen')),
    ('bath', ('banh', 'w.c', 'wc', 'lavabo', 'bath', 'toilet')),
    ('garage', ('garagem', 'garage', 'abrigo')),
    ('attic', ('atico', 'ático', 'forro', 'sotao', 'sótão', 'attic', 'plenum', 'entreforro')),
    ('service', ('lavanderia', 'varal', 'servi', 'circul', 'hall', 'corredor',
                 'depos', 'despensa', 'laundry', 'storage', 'corridor')),
)


def classify_space(name):
    """Space kind ('bedroom', 'living', ...) from a room name; 'service' if unknown."""
    n = (name or '').lower()
    for kind, words in SPACE_KINDS:
        if any(w in n for w in words):
            return kind
    return 'service'


def _sched(identifier, hours, type_limit=None):
    """ScheduleRuleset from 24 hourly values (same every day)."""
    return ScheduleRuleset.from_daily_values(identifier, hours, 1, type_limit or fractional)


def _hours(default, **spans):
    """24 values: ``default`` except the given ``h7_9=1.0`` style spans."""
    vals = [default] * 24
    for key, value in spans.items():
        a, b = key[1:].split('_')
        for h in range(int(a), int(b)):
            vals[h] = value
    return vals


def residential_programs():
    """Simple residential ProgramTypes keyed by space kind (SI units, per m2)."""
    occ = {
        'bedroom': _sched('Res Bedroom Occ', _hours(0.0, h0_7=1.0, h7_8=0.5, h20_22=0.5, h22_24=1.0)),
        'living': _sched('Res Living Occ', _hours(0.1, h7_9=0.5, h12_14=0.5, h18_23=1.0)),
        'kitchen': _sched('Res Kitchen Occ', _hours(0.0, h7_8=1.0, h12_13=1.0, h19_20=1.0)),
        'bath': _sched('Res Bath Occ', _hours(0.0, h6_8=1.0, h21_23=1.0)),
        'service': _sched('Res Service Occ', _hours(0.0, h9_10=0.5, h15_16=0.5)),
        'garage': _sched('Res Garage Occ', _hours(0.0)),
        'attic': _sched('Res Attic Occ', _hours(0.0)),
    }
    lights = {
        'bedroom': _sched('Res Bedroom Lights', _hours(0.0, h6_8=0.5, h19_23=1.0)),
        'living': _sched('Res Living Lights', _hours(0.0, h6_8=0.5, h18_23=1.0)),
        'kitchen': _sched('Res Kitchen Lights', _hours(0.0, h6_8=1.0, h12_13=0.5, h18_21=1.0)),
        'bath': _sched('Res Bath Lights', _hours(0.0, h6_8=1.0, h21_23=1.0)),
        'service': _sched('Res Service Lights', _hours(0.0, h9_10=0.5, h18_20=0.5)),
        'garage': _sched('Res Garage Lights', _hours(0.0, h18_20=0.3)),
        'attic': _sched('Res Attic Lights', _hours(0.0)),
    }
    equip = {
        'bedroom': _sched('Res Bedroom Equip', _hours(0.2, h20_24=0.6)),
        'living': _sched('Res Living Equip', _hours(0.2, h18_23=1.0)),
        'kitchen': _sched('Res Kitchen Equip', _hours(0.2, h7_8=1.0, h12_13=1.0, h19_20=1.0)),
        'bath': _sched('Res Bath Equip', _hours(0.0, h6_8=1.0, h21_23=1.0)),
        'service': _sched('Res Service Equip', _hours(0.1, h9_11=1.0)),
        'garage': _sched('Res Garage Equip', _hours(0.0)),
        'attic': _sched('Res Attic Equip', _hours(0.0)),
    }
    always = _sched('Res Always On', _hours(1.0))
    activity = _sched('Res Activity 110W', _hours(110.0), activity_level)
    heat_sp = _sched('Res Heating 18C', _hours(18.0), temperature)
    cool_sp = _sched('Res Cooling 26C', _hours(26.0), temperature)
    setpoint = Setpoint('Res Setpoint', heat_sp, cool_sp)

    # per-area loads (W/m2, people/m2, m3/s per m2 of exterior surface)
    loads = {  # kind: (people, lights, equipment, infiltration)
        'bedroom': (0.10, 5.0, 3.0, 0.0002),
        'living': (0.08, 6.0, 8.0, 0.0002),
        'kitchen': (0.05, 8.0, 25.0, 0.0003),
        'bath': (0.10, 8.0, 5.0, 0.0002),
        'service': (0.03, 4.0, 4.0, 0.0003),
        'garage': (0.0, 2.0, 1.0, 0.0010),
        'attic': (0.0, 0.0, 0.0, 0.0030),  # ventilated roof space: leaky, no loads
    }
    programs = {}
    for kind, (ppl, lgt, eqp, inf) in loads.items():
        label = kind.capitalize()
        programs[kind] = ProgramType(
            'Res {}'.format(label),
            people=People('Res {} People'.format(label), ppl, occ[kind], activity),
            lighting=Lighting('Res {} Lighting'.format(label), lgt, lights[kind]),
            electric_equipment=ElectricEquipment('Res {} Equipment'.format(label), eqp, equip[kind]),
            infiltration=Infiltration('Res {} Infiltration'.format(label), inf, always),
            setpoint=setpoint)
    return programs


def parse_window_openings(text):
    """'JA01=0, JA02=0.5, JA04=0.75' -> {'JA01': 0.0, ...} (fractions of operable area)."""
    out = {}
    for part in re.split(r'[,;\n]+', text or ''):
        if '=' not in part:
            continue
        key, val = part.split('=', 1)
        try:
            v = float(val.strip().replace('%', ''))
        except ValueError:
            continue
        if v > 1.0:  # percentages
            v /= 100.0
        out[key.strip().upper()] = max(0.0, min(v, 1.0))
    return out


def operable_fraction_for(aperture, openings, default=0.5):
    """Operable fraction of an aperture from its (IFC) name and a name -> fraction map."""
    name = (aperture.display_name or aperture.identifier or '').upper()
    best, best_len = None, -1
    for key, frac in openings.items():
        if key and key in name and len(key) > best_len:
            best, best_len = frac, len(key)
    return default if best is None else best


def prepare_model(model, hvac='FREE_RUNNING', vent_min_indoor=22.0,
                  vent_min_outdoor=16.0, vent_max_outdoor=32.0, operable_fraction=0.5,
                  window_openings=None):
    """Assign residential programs and either natural ventilation or ideal air.

    ``window_openings`` maps window names (or name prefixes, e.g. the IFC
    type 'JA02') to the fraction of their area that opens; 0 means fixed
    glazing. Windows not listed use ``operable_fraction``.

    Returns a dict room identifier -> space kind.
    """
    programs = residential_programs()
    openings = window_openings or {}
    kinds = {}
    for room in model.rooms:
        kind = classify_space(room.display_name or room.identifier)
        kinds[room.identifier] = kind
        room.properties.energy.program_type = programs[kind]
        if hvac == 'IDEAL_AIR':
            if kind not in ('garage', 'service', 'attic'):
                room.properties.energy.add_default_ideal_air()
        else:  # free running: windows open when it is warm inside and mild outside
            room.properties.energy.hvac = None
            room.properties.energy.window_vent_control = VentilationControl(
                min_indoor_temperature=vent_min_indoor,
                min_outdoor_temperature=vent_min_outdoor,
                max_outdoor_temperature=vent_max_outdoor)
            for face in room.faces:
                for ap in face.apertures:
                    if ap.boundary_condition.name != 'Outdoors':
                        continue
                    frac = operable_fraction_for(ap, openings, operable_fraction)
                    if frac <= 0:
                        ap.is_operable = False
                        continue
                    ap.is_operable = True
                    ap.properties.energy.vent_opening = VentilationOpening(
                        fraction_area_operable=frac)
                # glazed exterior doors (sliding doors) open too; listed by
                # name like windows ("PA09=0.5"), default operable fraction
                for dr in face.doors:
                    if dr.boundary_condition.name != 'Outdoors' or not dr.is_glass:
                        continue
                    frac = operable_fraction_for(dr, openings, operable_fraction)
                    if frac > 0:
                        dr.properties.energy.vent_opening = VentilationOpening(
                            fraction_area_operable=frac)
    return kinds


def ground_temperature_idf(epw, depth_preference=(0.5, 2.0, 4.0)):
    """Site:GroundTemperature:BuildingSurface from the EPW header (18 C fallback)."""
    monthly = None
    try:
        gt = epw.monthly_ground_temperature
        for d in depth_preference:
            if d in gt:
                monthly = list(gt[d].values)
                break
        if monthly is None and gt:
            monthly = list(next(iter(gt.values())).values)
    except Exception:  # noqa: BLE001
        monthly = None
    if not monthly or len(monthly) != 12 or any(v is None or v < -30 or v > 50 for v in monthly):
        monthly = [18.0] * 12
    return 'Site:GroundTemperature:BuildingSurface,\n  ' + \
        ',\n  '.join('{:.2f}'.format(v) for v in monthly) + ';'


DEFAULT_OUTPUTS = (
    'Zone Mean Air Temperature',
    'Zone Operative Temperature',
    'Zone Air Relative Humidity',
    'Zone Windows Total Transmitted Solar Radiation Energy',
)


def write_idf(model, epw_path, folder, outputs=DEFAULT_OUTPUTS, timestep=4,
              run_period=None, north=0.0):
    """Write in.idf (model + simulation parameters + site location).

    ``north`` is the Ladybug north angle (counterclockwise degrees from +Y).
    """
    os.makedirs(folder, exist_ok=True)
    sim_par = SimulationParameter()
    sim_par.timestep = timestep
    sim_par.north_angle = float(north) % 360
    sim_par.shadow_calculation.calculation_frequency = 30  # days between shadow updates
    # no solar reflections from context: much cheaper with many shading surfaces
    sim_par.shadow_calculation.solar_distribution = 'FullExterior'
    sim_par.output.reporting_frequency = 'Hourly'
    sim_par.output.add_zone_energy_use()
    for out in outputs:
        sim_par.output.add_output(out)
    if run_period is not None:
        sim_par.run_period = run_period
    epw = EPW(epw_path)
    location_idf = epw.location.to_idf()
    idf_str = '\n\n'.join((energyplus_idf_version(), sim_par.to_idf(), location_idf,
                           ground_temperature_idf(epw), model_to_idf(model)))
    idf_path = os.path.join(folder, 'in.idf')
    with open(idf_path, 'w', encoding='utf-8') as f:
        f.write(idf_str)
    return idf_path


def run(model, epw_path, folder, energyplus_path, outputs=DEFAULT_OUTPUTS, timestep=4,
        run_period=None, north=0.0):
    """Write the IDF, run EnergyPlus and return (sql_path, err_path, seconds)."""
    if not folders.energyplus_path or os.path.normpath(folders.energyplus_path) != \
            os.path.normpath(energyplus_path):
        folders.energyplus_path = energyplus_path
    if os.path.isdir(folder):
        for name in ('eplusout.sql', 'eplusout.err', 'in.idf'):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
    t0 = time.time()
    idf = write_idf(model, epw_path, folder, outputs, timestep, run_period, north)
    sql, zsz, rdd, html, err = run_idf(idf, epw_path, expand_objects=True, silent=True)
    fatal = []
    if err and os.path.isfile(err):
        with open(err, encoding='utf-8', errors='ignore') as f:
            for ln in f:
                if '** Severe  **' in ln or '**  Fatal  **' in ln:
                    fatal.append(ln.strip().replace('** Severe  ** ', '').replace(
                        '**  Fatal  ** ', ''))
    if sql is None or any('Fatal' in ln or 'terminates' in ln for ln in fatal):
        msg = 'EnergyPlus failed'
        if fatal:
            msg += ': ' + ' | '.join(fatal[:4])
        raise RuntimeError(msg)
    return sql, err, time.time() - t0


def scene_shades_from_faces(face_lists):
    """Honeybee Shades from lists of (x, y, z) vertex tuples (edited context)."""
    from honeybee.shade import Shade
    from ladybug_geometry.geometry3d import Face3D, Point3D
    shades = []
    for i, verts in enumerate(face_lists):
        try:
            g = Face3D([Point3D(*v) for v in verts])
            if g.area < 0.01:
                continue
            shades.append(Shade('Context_{}'.format(i), g, is_detached=True))
        except Exception:  # noqa: BLE001
            continue
    return shades


def read_results(sql_path, model, output='Zone Operative Temperature',
                 comfort_low=18.0, comfort_high=26.0):
    """Per-room hourly collections and comfort summary from the SQLite output.

    Returns (collections, summary): collections maps room identifier -> hourly
    data collection; summary maps room identifier -> dict of statistics.
    """
    sql = SQLiteResult(sql_path)
    colls = sql.data_collections_by_output_name(output)
    by_zone = {}
    for c in colls:
        zone = c.header.metadata.get('Zone', '').upper()
        by_zone[zone] = c
    results, summary = {}, {}
    for room in model.rooms:
        c = by_zone.get(room.identifier.upper())
        if c is None:
            continue
        results[room.identifier] = c
        vals = c.values
        n = len(vals)
        hot = sum(1 for v in vals if v > comfort_high)
        cold = sum(1 for v in vals if v < comfort_low)
        summary[room.identifier] = {
            'name': room.display_name, 'mean': sum(vals) / n, 'min': min(vals),
            'max': max(vals), 'hours_hot': hot, 'hours_cold': cold,
            'pct_comfort': 100.0 * (n - hot - cold) / n,
        }
    return results, summary


def read_err_summary(err_path, limit=8):
    """Count and list the first warnings/severe messages of eplusout.err."""
    if not err_path or not os.path.isfile(err_path):
        return {'warnings': 0, 'severe': 0, 'messages': []}
    warnings = severe = 0
    msgs = []
    with open(err_path, encoding='utf-8', errors='ignore') as f:
        for ln in f:
            s = ln.strip()
            if s.startswith('** Warning **'):
                warnings += 1
                if len(msgs) < limit:
                    msgs.append(s[14:].strip())
            elif s.startswith('** Severe  **') or s.startswith('**  Fatal  **'):
                severe += 1
                msgs.insert(0, s)
    return {'warnings': warnings, 'severe': severe, 'messages': msgs[:limit]}

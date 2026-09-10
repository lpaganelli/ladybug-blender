"""Generate a synthetic but physically plausible EPW for testing.

Uses Ladybug's ASHRAE clear-sky model for radiation and simple sinusoidal
temperature/wind so that all operators have data to work with. Run with any
Python that can import ladybug (e.g. Blender's python with the extension
wheels on sys.path).
"""
import math
import os
import sys

from ladybug.epw import EPW
from ladybug.location import Location
from ladybug.wea import Wea


def make_epw(path, city='Sao Paulo', lat=-23.55, lon=-46.63, tz=-3, elev=760):
    loc = Location(city, 'SP', 'BRA', lat, lon, tz, elev)
    epw = EPW.from_missing_values()
    epw.location = loc
    wea = Wea.from_ashrae_clear_sky(loc, sky_clearness=0.9)
    epw.direct_normal_radiation.values = [
        round(v) for v in wea.direct_normal_irradiance.values]
    epw.diffuse_horizontal_radiation.values = [
        round(v) for v in wea.diffuse_horizontal_irradiance.values]
    epw.global_horizontal_radiation.values = [
        round(v) for v in wea.global_horizontal_irradiance.values]
    temps, rh, wdir, wspd = [], [], [], []
    for i in range(8760):
        hour = i % 24
        doy = i // 24
        seasonal = 4.0 * math.cos(2 * math.pi * (doy - 20) / 365.0)  # warm in Jan (south)
        daily = 5.0 * math.cos(2 * math.pi * (hour - 15) / 24.0)
        temps.append(round(20.0 + seasonal + daily, 1))
        rh.append(round(max(30, min(95, 75 - 3 * daily)), 0))
        wdir.append(round((135 + 40 * math.sin(2 * math.pi * i / 173.0)) % 360))
        wspd.append(round(max(0.0, 3.0 + 2.0 * math.sin(2 * math.pi * i / 37.0)), 1))
    epw.dry_bulb_temperature.values = temps
    epw.relative_humidity.values = rh
    epw.wind_direction.values = wdir
    epw.wind_speed.values = wspd
    epw.dew_point_temperature.values = [t - 5 for t in temps]
    epw.atmospheric_station_pressure.values = [101325 - elev * 11] * 8760
    epw.write(path)
    return path


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), 'test_sao_paulo.epw')
    print('Wrote', make_epw(out))

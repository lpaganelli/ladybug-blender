# -*- coding: utf-8 -*-
"""Native sky matrix computed with numpy.

This is a port of the logic of Radiance's ``gendaymtx`` (Perez all-weather sky
luminance distribution + direct sun distributed to the nearest patches) so that
Ladybug radiation studies can run inside Blender without a Radiance install.

The resulting object is duck-type compatible with
``ladybug_radiance.skymatrix.SkyMatrix``: it exposes ``data``, ``direct_values``,
``diffuse_values``, ``metadata``, ``wea_duration``, ``north``, ``high_density``,
``ground_reflectance`` and ``benefit_matrix`` so that ``SkyDome``,
``RadiationRose`` and ``RadiationDome`` from ladybug_radiance accept it.

Units follow Ladybug conventions: each patch value is the cumulative radiation
(kWh/m2) received by a surface facing the patch over the Wea period.
"""
import math

import numpy as np

from ladybug.wea import Wea
from ladybug.sunpath import Sunpath
from ladybug.viewsphere import view_sphere

# Perez et al. (1993) all-weather sky luminance coefficients.
# Rows: sky clearness bins. Columns: a1..a4, b1..b4, c1..c4, d1..d4, e1..e4
PEREZ_COEFF = np.array([
    # 1.000 - 1.065
    [1.3525, -0.2576, -0.2690, -1.4366, -0.7670, 0.0007, 1.2734, -0.1233,
     2.8000, 0.6004, 1.2375, 1.0000, 1.8734, 0.6297, 0.9738, 0.2809,
     0.0356, -0.1246, -0.5718, 0.9938],
    # 1.065 - 1.230
    [-1.2219, -0.7730, 1.4148, 1.1016, -0.2054, 0.0367, -3.9128, 0.9156,
     6.9750, 0.1774, 6.4477, -0.1239, -1.5798, -0.5081, -1.7812, 0.1080,
     0.2624, 0.0672, -0.2190, -0.4285],
    # 1.230 - 1.500
    [-1.1000, -0.2515, 0.8952, 0.0156, 0.2782, -0.1812, -4.5000, 1.1766,
     24.7219, -13.0812, -37.7000, 34.8438, -5.0000, 1.5218, 3.9229, -2.6204,
     -0.0156, 0.1597, 0.4199, -0.5562],
    # 1.500 - 1.950
    [-0.5484, -0.6654, -0.2672, 0.7117, 0.7234, -0.6219, -5.6812, 2.6297,
     33.3389, -18.3000, -62.2500, 52.0781, -3.5000, 0.0016, 1.1477, 0.1062,
     0.4659, -0.3296, -0.0876, -0.0329],
    # 1.950 - 2.800
    [-0.6000, -0.3566, -2.5000, 2.3250, 0.2937, 0.0496, -5.6812, 1.8415,
     21.0000, -4.7656, -21.5906, 7.2492, -3.5000, -0.1554, 1.4062, 0.3988,
     0.0032, 0.0766, -0.0656, -0.1294],
    # 2.800 - 4.500
    [-1.0156, -0.3670, 1.0078, 1.4051, 0.2875, -0.5328, -3.8500, 3.3750,
     14.0000, -0.9999, -7.1406, 7.5469, -3.4000, -0.1078, -1.0750, 1.5702,
     -0.0672, 0.4016, 0.3017, -0.4844],
    # 4.500 - 6.200
    [-1.0000, 0.0211, 0.5025, -0.5119, -0.3000, 0.1922, 0.7023, -1.6317,
     19.0000, -5.0000, 1.2438, -1.9094, -4.0000, 0.0250, 0.3844, 0.2656,
     1.0468, -0.3788, -2.4517, 1.4656],
    # 6.200 - inf
    [-1.0500, 0.0289, 0.4260, 0.3590, -0.3250, 0.1156, 0.7781, 0.0025,
     31.0625, -14.5000, -46.1148, 55.3750, -7.2312, 0.4050, 13.3500, 0.6234,
     1.5000, -0.6426, 1.8564, 0.5636],
])
CLEARNESS_BINS = np.array([1.065, 1.230, 1.500, 1.950, 2.800, 4.500, 6.200])
SOLAR_CONSTANT = 1367.0  # W/m2


def _eccentricity(doy):
    """Earth-sun distance correction factor (Spencer 1971)."""
    da = 2.0 * math.pi * (doy - 1) / 365.0
    return (1.00011 + 0.034221 * math.cos(da) + 0.00128 * math.sin(da) +
            0.000719 * math.cos(2 * da) + 0.000077 * math.sin(2 * da))


def patch_vectors(high_density=False):
    """Numpy array (N, 3) of unit vectors for the Tregenza/Reinhart dome patches."""
    vecs = view_sphere.reinhart_dome_vectors if high_density else \
        view_sphere.tregenza_dome_vectors
    return np.array([(v.x, v.y, v.z) for v in vecs], dtype=np.float64)


def patch_solid_angles(high_density=False):
    """Numpy array (N,) of steradians for each dome patch.

    Computed from the ladybug row tables directly because the ladybug
    ``tregenza_solid_angles``/``reinhart_solid_angles`` properties share a
    cache slot and return the wrong length once both have been used.
    """
    if high_density:
        rows = view_sphere.REINHART_PATCHES_PER_ROW + (1,)
        coeffs = view_sphere.REINHART_COEFFICIENTS
    else:
        rows = view_sphere.TREGENZA_PATCHES_PER_ROW + (1,)
        coeffs = view_sphere.TREGENZA_COEFFICIENTS
    return np.repeat(np.array(coeffs, dtype=np.float64), rows)


def perez_relative_luminance(cos_zeta, gamma, a, b, c, d, e):
    """Perez relative luminance for patches.

    Args:
        cos_zeta: array of cosines of patch zenith angles.
        gamma: array of angles (radians) between patch and sun.
    """
    cz = np.maximum(cos_zeta, 0.01)
    lv = (1.0 + a * np.exp(b / cz)) * \
        (1.0 + c * np.exp(d * gamma) + e * np.cos(gamma) ** 2)
    return np.maximum(lv, 0.0)


def perez_params(zenith, brightness, clearness):
    """Compute Perez a, b, c, d, e coefficients for a sky condition."""
    idx = int(np.searchsorted(CLEARNESS_BINS, clearness, side='right'))
    k = PEREZ_COEFF[idx]
    z, dl = zenith, brightness
    a = k[0] + k[1] * z + dl * (k[2] + k[3] * z)
    b = k[4] + k[5] * z + dl * (k[6] + k[7] * z)
    e = k[16] + k[17] * z + dl * (k[18] + k[19] * z)
    if idx == 0:
        c = math.exp((dl * (k[8] + k[9] * z)) ** k[10]) - k[11]
        d = -math.exp(dl * (k[12] + k[13] * z)) + k[14] + dl * k[15]
    else:
        c = k[8] + k[9] * z + dl * (k[10] + k[11] * z)
        d = k[12] + k[13] * z + dl * (k[14] + k[15] * z)
    return a, b, c, d, e


def compute_patch_radiation(wea, high_density=False, progress=None):
    """Cumulative direct and diffuse radiation (kWh/m2) per sky patch.

    Returns:
        (direct, diffuse) numpy arrays of length 145 (Tregenza) or 577 (Reinhart).
    """
    vecs = patch_vectors(high_density)
    omega = patch_solid_angles(high_density)
    cos_zeta = vecs[:, 2]
    n_patch = len(vecs)

    dts = wea.datetimes
    dni = np.array(wea.direct_normal_irradiance.values, dtype=np.float64)
    dhi = np.array(wea.diffuse_horizontal_irradiance.values, dtype=np.float64)
    timestep = wea.timestep

    sp = Sunpath.from_location(wea.location)
    sp.is_leap_year = wea.is_leap_year

    direct = np.zeros(n_patch)
    diffuse = np.zeros(n_patch)
    n = len(dts)
    for i, dt in enumerate(dts):
        if progress is not None and i % 500 == 0:
            progress(i / n)
        if dni[i] <= 0 and dhi[i] <= 0:
            continue
        sun = sp.calculate_sun_from_date_time(dt)
        alt = sun.altitude
        if alt <= 0:
            continue
        sv = sun.sun_vector_reversed
        sun_vec = np.array((sv.x, sv.y, sv.z))
        dots = vecs @ sun_vec

        # ---- diffuse (Perez all-weather) ----
        if dhi[i] > 0:
            zen = math.radians(90.0 - alt)
            zdeg = 90.0 - alt
            air_mass = 1.0 / (math.cos(zen) + 0.15 * (93.885 - zdeg) ** -1.253)
            i0 = SOLAR_CONSTANT * _eccentricity(dt.doy)
            brightness = max(dhi[i] * air_mass / i0, 0.01)
            z3 = 1.041 * zen ** 3
            clearness = ((dhi[i] + dni[i]) / dhi[i] + z3) / (1.0 + z3)
            clearness = min(clearness, 12.0)
            a, b, c, d, e = perez_params(zen, brightness, clearness)
            gamma = np.arccos(np.clip(dots, -1.0, 1.0))
            lv = perez_relative_luminance(cos_zeta, gamma, a, b, c, d, e)
            norm = float(np.sum(lv * omega * cos_zeta))
            if norm > 0:
                diffuse += lv * omega * (dhi[i] / norm)

        # ---- direct (nearest 3 patches, gendaymtx weighting) ----
        if dni[i] > 0:
            near = np.argpartition(-dots, 3)[:3]
            w = 1.0 / (1.002 - dots[near])
            w /= float(np.sum(w))
            direct[near] += w * dni[i]

    factor = 1.0 / timestep / 1000.0  # W/m2 per timestep -> kWh/m2
    return direct * factor, diffuse * factor


class SkyMatrix(object):
    """Sky matrix computed natively (no Radiance needed).

    Args:
        wea: A ladybug Wea object.
        north: Counterclockwise degrees between North and +Y. (Default: 0).
        high_density: True for the Reinhart (577 patch) sky. (Default: False).
        ground_reflectance: Average ground reflectance 0-1. (Default: 0.2).
    """

    def __init__(self, wea, north=0, high_density=False, ground_reflectance=0.2):
        assert isinstance(wea, Wea), 'Expected Wea. Got {}.'.format(type(wea))
        self._wea = wea
        self.north = north
        self.high_density = high_density
        self.ground_reflectance = ground_reflectance
        self._direct_values = None
        self._diffuse_values = None
        self._metadata = None
        self.benefit_matrix = None
        self.progress = None

    @classmethod
    def from_epw(cls, epw_file, hoys=None, north=0, high_density=False,
                 ground_reflectance=0.2, timestep=1):
        wea = Wea.from_epw_file(epw_file, timestep)
        if hoys is not None:
            wea = wea.filter_by_hoys(hoys)
        return cls(wea, north, high_density, ground_reflectance)

    @classmethod
    def from_wea(cls, wea, hoys=None, north=0, high_density=False,
                 ground_reflectance=0.2):
        if hoys is not None:
            wea = wea.filter_by_hoys(hoys)
        return cls(wea, north, high_density, ground_reflectance)

    @classmethod
    def from_ashrae_clear_sky(cls, location, sky_clearness=1, hoys=None, north=0,
                              high_density=False, ground_reflectance=0.2, timestep=1):
        wea = Wea.from_ashrae_clear_sky(location, sky_clearness, timestep)
        if hoys is not None:
            wea = wea.filter_by_hoys(hoys)
        return cls(wea, north, high_density, ground_reflectance)

    # ---- properties mirroring ladybug_radiance.SkyMatrix ----
    @property
    def wea(self):
        return self._wea

    @property
    def north(self):
        return self._north

    @north.setter
    def north(self, value):
        self._north = float(value) % 360

    @property
    def wea_duration(self):
        return len(self._wea) / self._wea.timestep

    @property
    def patch_vectors(self):
        """Numpy (N, 3) unit vectors of the sky patches (unrotated by north)."""
        return patch_vectors(self.high_density)

    @property
    def patch_count(self):
        return 577 if self.high_density else 145

    @property
    def direct_values(self):
        if self._direct_values is None:
            self.compute_sky()
        return self._direct_values

    @property
    def diffuse_values(self):
        if self._diffuse_values is None:
            self.compute_sky()
        return self._diffuse_values

    @property
    def total_values(self):
        return tuple(a + b for a, b in zip(self.direct_values, self.diffuse_values))

    @property
    def metadata(self):
        if self._metadata is None:
            self.compute_sky()
        return self._metadata

    @property
    def data(self):
        if self._metadata is None:
            self.compute_sky()
        return (self._metadata, self._direct_values, self._diffuse_values)

    def compute_sky(self):
        direct, diffuse = compute_patch_radiation(
            self._wea, self.high_density, self.progress)
        self._direct_values = tuple(float(v) for v in direct)
        self._diffuse_values = tuple(float(v) for v in diffuse)
        metadata = [self.north, self.ground_reflectance]
        dts = self._wea.direct_normal_irradiance.datetimes
        metadata.extend([dts[0], dts[-1]])
        for key, val in self._wea.direct_normal_irradiance.header.metadata.items():
            metadata.append('{} : {}'.format(key, val))
        self._metadata = tuple(metadata)

    def __len__(self):
        return self.patch_count

    def __repr__(self):
        return 'Native SkyMatrix [{} patches] ({} hours)'.format(
            self.patch_count, int(self.wea_duration))

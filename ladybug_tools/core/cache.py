# -*- coding: utf-8 -*-
"""In-memory caches for parsed EPW files, Wea objects and sky matrices."""
import os

from ladybug.epw import EPW
from ladybug.wea import Wea

_EPW = {}
_WEA = {}
_SKY = {}


def _stamp(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def get_epw(path):
    """Parsed EPW object for a path (cached by modification time)."""
    path = os.path.normpath(path)
    stamp = _stamp(path)
    hit = _EPW.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    epw = EPW(path)
    epw.location  # force parsing so errors surface here
    _EPW[path] = (stamp, epw)
    return epw


def get_wea(path, timestep=1):
    path = os.path.normpath(path)
    key = (path, timestep)
    stamp = _stamp(path)
    hit = _WEA.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    wea = Wea.from_epw_file(path, timestep)
    _WEA[key] = (stamp, wea)
    return wea


def get_sky(key, factory):
    """Sky matrix cached under an arbitrary hashable key."""
    sky = _SKY.get(key)
    if sky is None:
        sky = factory()
        _SKY[key] = sky
    return sky


def clear():
    _EPW.clear()
    _WEA.clear()
    _SKY.clear()

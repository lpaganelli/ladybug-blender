"""Operators of the Ladybug Tools extension."""
from . import weather, sunpath, studies, roses

MODULES = (weather, sunpath, studies, roses)


def register():
    for m in MODULES:
        m.register()


def unregister():
    for m in reversed(MODULES):
        m.unregister()

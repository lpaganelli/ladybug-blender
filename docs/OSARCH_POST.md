# Draft: OSArch community post

**Title:** Ladybug Tools for Blender: sun path, direct sun hours and incident radiation as a native Blender Extension (no Radiance)

Hi all,

I'm an architect in Brazil working with ArchiCAD → FBX → Blender, and I wanted
Ladybug-style solar analysis inside Blender without Rhino, Grasshopper or a
Radiance install. The result is a Blender Extension (4.2+, tested on 4.5 LTS
and 5.2 LTS):

<https://github.com/lpaganelli/ladybug-blender>

What it does today (v0.4):

- Sun path (analemmas, day arcs, compass, sun points), a real Sun light aimed
  at any date/time, and a one-day animation.
- Direct Sun Hours and Incident Radiation (kWh/m² or W/m²) on any selected
  meshes, in batch, each shading the others. A "Sensor Grid" button adds a
  Remesh tuned to a target cell size, which works nicely on FBX solids.
- Sky Dome, Radiation Rose and Wind Rose from the EPW.
- Results are baked into result copies (the source geometry is never
  touched), with one shared scale and legend per batch, and a Period
  Explorer that recolors everything for another month/day/hour range
  without re-tracing (the visibility matrix is cached).

How it's built: the pure-Python Ladybug libraries are bundled as wheels and
used as they are. Only the two Radiance-dependent parts were rewritten:
`gendaymtx` became a numpy Perez all-weather sky matrix (validated by energy
conservation against the Wea), and `rcontrib` became ray casting with
Blender's BVHTree. So it is a radiation engine without inter-reflection:
fine for incident radiation, sun hours and sky view; not for daylighting.

Roadmap: a patch-by-patch comparison with the real `gendaymtx`, 2D climate
charts, and, the part I care most about, a Blender/IFC → Honeybee model
bridge exporting HBJSON, with Honeybee-Energy through the EnergyPlus Python
API. If anyone has experience mapping IfcSpace / IfcWall / IfcWindow to
Honeybee rooms, faces and apertures with Bonsai, I'd love to compare notes.

License is AGPL-3.0, inherited from Ladybug Tools. Feedback, bug reports and
EPW files from other climates are very welcome.

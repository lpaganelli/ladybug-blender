# Draft: OSArch community post

**Title:** Ladybug Tools for Blender: sun path, direct sun hours and incident radiation as a native Blender Extension (no Radiance)

Hi all,

I'm an architect in Brazil, not a programmer. My workflow is ArchiCAD → FBX →
Blender, and I wanted Ladybug-style solar analysis inside Blender without
Rhino, Grasshopper or a Radiance install. So I built this with the help of an
AI assistant (Claude Fable, via Claude Code): I described what I needed and
checked the results against what I know as an architect, the assistant wrote
the code and the tests. Full disclosure, so you know what you're looking at
and can review the code with that in mind.

<https://github.com/lpaganelli/ladybug-blender>

I tested it on a small in-house project (images below) and it worked well:
remesh the FBX solids into a sensor grid, select roofs and facades, run the
studies, drag a month slider. Please treat it as an early tool from a
non-developer, not a validated engine.

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
`gendaymtx` became a numpy Perez all-weather sky matrix (checked by energy
conservation against the Wea), and `rcontrib` became ray casting with
Blender's BVHTree. So it is a radiation engine without inter-reflection:
fine for incident radiation, sun hours and sky view; not for daylighting.
A patch-by-patch comparison with the real `gendaymtx` is the next step, and
I'd welcome anyone who can run that comparison independently.

Where I'd like to go next is a Blender/IFC → Honeybee model bridge
(IfcSpace → Room, IfcWall/IfcSlab/IfcRoof → Face, IfcWindow/IfcDoor →
Aperture/Door, HBJSON export), with Honeybee-Energy through the EnergyPlus
Python API. I don't have much experience with Bonsai or IFC yet, which is
exactly why I'm posting here: if you have mapped IFC to Honeybee rooms,
faces and apertures, or know the pitfalls, I'd love to hear from you.

License is AGPL-3.0, inherited from Ladybug Tools. Feedback, bug reports,
code review and EPW files from other climates are very welcome.

*(images: sun path over the model; direct sun hours on the roofs with the
shared legend; incident radiation on the facades; the Period Explorer panel)*

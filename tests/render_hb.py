"""Render the Honeybee model scene (tests/out/ifc_model.blend) with Workbench."""
import os

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'VERTEX'
scene.display.shading.show_object_outline = True
scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
ctx = bpy.data.objects.get('HB Context')
if ctx is not None:
    ctx.hide_render = True  # look inside: rooms only
rooms = [o for o in bpy.data.objects if o.get('hb_room')]
pts = [o.matrix_world @ Vector(c) for o in rooms for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center = (lo + hi) / 2
size = max(hi - lo)
cam_data = bpy.data.cameras.new('Cam')
cam = bpy.data.objects.new('Cam', cam_data)
scene.collection.objects.link(cam)
cam.location = center + Vector((-1.2, -1.6, 1.1)) * size
cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
cam_data.lens = 40
scene.camera = cam
scene.render.filepath = os.path.join(HERE, 'out', 'render_hb_rooms.png')
bpy.ops.render.render(write_still=True)
print('rendered', scene.render.filepath)

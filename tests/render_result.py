"""Render the test scene produced by test_headless.py with Workbench.

    blender -b tests/out/test_result.blend --python tests/render_result.py
"""
import math
import os

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'VERTEX'
scene.display.shading.show_object_outline = True
scene.display.shading.show_shadows = False
scene.render.resolution_x = 1600
scene.render.resolution_y = 1000
scene.render.film_transparent = False
scene.frame_set(20)

# give curves some thickness so they show up in the render
for cu in bpy.data.curves:
    if isinstance(cu, bpy.types.Curve) and cu.bevel_depth == 0:
        cu.bevel_depth = 0.04


def frame_all(cam_name, location, target, ortho_scale=None):
    cam_data = bpy.data.cameras.new(cam_name)
    cam = bpy.data.objects.new(cam_name, cam_data)
    scene.collection.objects.link(cam)
    cam.location = location
    direction = Vector(target) - Vector(location)
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    if ortho_scale:
        cam_data.type = 'ORTHO'
        cam_data.ortho_scale = ortho_scale
    else:
        cam_data.lens = 35
    cam_data.clip_end = 1000
    return cam


views = {
    'overview': frame_all('CamOverview', (55, -70, 60), (8, 0, 0)),
    'top': frame_all('CamTop', (8, 0, 120), (8, 0, 0), ortho_scale=80),
    'sunpath': frame_all('CamSun', (-40, -10, 25), (-15, 15, 4)),
}
for name, cam in views.items():
    scene.camera = cam
    scene.render.filepath = os.path.join(OUT, 'render_{}.png'.format(name))
    bpy.ops.render.render(write_still=True)
    print('rendered', scene.render.filepath)

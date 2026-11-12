"""
Preview renders (Cycles, CPU) of the built hair on the reference avatar.

    python blender/render_previews.py [--views front,left34,...] [--size 720x900] [--samples 48] [--uv]
    blender -b -P blender/render_previews.py -- [same options]

--uv also writes previews/uv_layout.png (the texture with the UV wireframe on top).
"""
import math
import os
import sys

import bpy
from mathutils import Vector

NAME = "CherryWolfHime_Hair"
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]


def opt(flag, default):
    return ARGS[ARGS.index(flag) + 1] if flag in ARGS else default


# name: (azimuth deg from the front, + = character's left; elevation deg; distance; target z; lens)
VIEWS = {
    "front": (0, 4, 13.5, 1.75, 85),
    "left34": (38, 8, 13.5, 1.75, 85),
    "right34": (-38, 8, 13.5, 1.75, 85),
    "left": (90, 4, 13.5, 1.75, 85),
    "back": (180, 6, 13.5, 1.75, 85),
    "head_front": (0, 2, 7.2, 2.45, 85),
    "head_left34": (40, 10, 7.2, 2.45, 85),
    "head_right34": (-40, 10, 7.2, 2.45, 85),
    "head_back": (180, 8, 7.2, 2.4, 85),
    "head_left": (90, 4, 7.2, 2.4, 85),
    "top": (20, 62, 7.5, 2.75, 85),
}


def uv_layout(path):
    """Colour texture with the UV wireframe drawn over it."""
    import numpy as np
    me = bpy.data.objects[NAME].data
    img = me.materials[0].node_tree.nodes["Image Texture"].image
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers[0].data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2) * (w - 1, h - 1)
    for poly in me.polygons:
        pts = uv[poly.loop_start:poly.loop_start + poly.loop_total]
        for a, b in zip(pts, np.roll(pts, -1, axis=0)):
            n = int(max(abs(b - a).max(), 1)) + 1
            xs = np.linspace(a[0], b[0], n).round().astype(int)
            ys = np.linspace(a[1], b[1], n).round().astype(int)
            px[ys, xs, :3] = (1.0, .92, .75)
    out = bpy.data.images.new("uv_layout", w, h, alpha=False)
    out.pixels.foreach_set(px.ravel())
    out.filepath_raw = path
    out.file_format = "PNG"
    out.save()
    print("wrote", path)


def main():
    blend = opt("--blend", os.path.join(ROOT, "export", NAME + ".blend"))
    out = opt("--out", os.path.join(ROOT, "previews"))
    views = opt("--views", "front,left34,back,head_front,head_left34,head_right34,head_back,top").split(",")
    w, h = (int(v) for v in opt("--size", "720x900").split("x"))
    os.makedirs(out, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=blend)
    if "--uv" in ARGS:
        uv_layout(os.path.join(out, "uv_layout.png"))
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = int(opt("--samples", "48"))
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 4
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "JPEG"
    sc.render.image_settings.quality = 92
    sc.view_settings.view_transform = "Standard"                  # show texture colours as authored
    world = bpy.data.worlds.new("PreviewWorld")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (.60, .62, .66, 1)
    bg.inputs[1].default_value = .42
    sc.world = world
    for name, energy, rot in (("key", 2.1, (52, 0, -28)), ("fill", .7, (60, 0, 70)),
                              ("rim", 1.2, (-55, 0, 200))):
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        light.angle = math.radians(25)
        ob = bpy.data.objects.new(name, light)
        sc.collection.objects.link(ob)
        ob.rotation_euler = [math.radians(a) for a in rot]
    cam = bpy.data.objects.new("PreviewCam", bpy.data.cameras.new("PreviewCam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    for v in views:
        az, el, dist, tz, lens = VIEWS[v]
        a, e = math.radians(az), math.radians(el)
        target = Vector((0, 0.1, tz))
        cam.location = target + dist * Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam.data.lens = lens
        sc.render.filepath = os.path.join(out, v + ".jpg")
        bpy.ops.render.render(write_still=True)
        print("rendered", sc.render.filepath)


if __name__ == "__main__":
    main()

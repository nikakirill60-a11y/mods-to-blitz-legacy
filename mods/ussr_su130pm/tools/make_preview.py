#!/usr/bin/env python3
"""Render a small 3/4 preview of the finished SU-130PM mod.

This is a pure-Pillow software renderer: it reads the generated GLB, follows the
node translations, samples the body/track textures at each triangle's UV
centroid and paints the triangles from far to near.  It is only a visual sanity
check - the game uses its own renderer - but it catches bad UVs, missing meshes
and a broken turret/gun hierarchy without opening Godot or Blender.

Usage:  python3 mods/ussr_su130pm/tools/make_preview.py
        (needs Pillow: python3 -m pip install pillow)
"""

import math
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  python3 -m pip install pillow")

from make_glb import load_glb, read_accessor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MOD = os.path.join(ROOT, "mods", "ussr_su130pm")
ASSETS = os.path.join(MOD, "assets", "tanks")
GLB = os.path.join(ASSETS, "ussr_su130pm.glb")
BODY = os.path.join(ASSETS, "ussr_su130pm.png")
TRACK = os.path.join(ASSETS, "ussr_su130pm_track.png")
DST = os.path.join(MOD, "preview.png")

W, H = 1200, 760
MARGIN = 70
EYE = (7.0, 4.2, 9.0)
TARGET = (0.0, 1.25, 0.0)
LIGHT = (0.45, 0.82, 0.36)


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a):
    l = math.sqrt(dot(a, a)) or 1.0
    return (a[0] / l, a[1] / l, a[2] / l)


class Sampler:
    def __init__(self, path, tiled):
        self.im = Image.open(path).convert("RGB")
        self.px = self.im.load()
        self.w, self.h = self.im.size
        self.tiled = tiled

    def get(self, u, v):
        if self.tiled:
            u, v = u % 1.0, v % 1.0
        else:
            u = min(0.999999, max(0.0, u))
            v = min(0.999999, max(0.0, v))
        return self.px[int(u * self.w), int(v * self.h)]


def collect(gltf, binary):
    """Return [(world triangle, uv triangle, is_track)] for every visible mesh."""
    tris = []

    def walk(index, offset):
        node = gltf["nodes"][index]
        t = tuple(offset[k] + node.get("translation", [0, 0, 0])[k] for k in range(3))
        if "mesh" in node:
            mesh = gltf["meshes"][node["mesh"]]
            prim = mesh["primitives"][0]
            attrs = prim["attributes"]
            if "TEXCOORD_0" in attrs:
                pos = read_accessor(gltf, binary, attrs["POSITION"])
                uv = read_accessor(gltf, binary, attrs["TEXCOORD_0"])
                idx = [i[0] for i in read_accessor(gltf, binary, prim["indices"])]
                name = mesh["name"]
                is_track = "track" in name.lower() and "tracks" not in name.lower()
                for i in range(0, len(idx), 3):
                    a, b, c = idx[i], idx[i + 1], idx[i + 2]
                    world = tuple(tuple(pos[v][k] + t[k] for k in range(3)) for v in (a, b, c))
                    uvw = (uv[a], uv[b], uv[c])
                    tris.append((world, uvw, is_track))
        for child in node.get("children", []):
            walk(child, t)

    for root in gltf["scenes"][0]["nodes"]:
        walk(root, (0.0, 0.0, 0.0))
    return tris


def main():
    gltf, binary = load_glb(GLB)
    body = Sampler(BODY, False)
    track = Sampler(TRACK, True)
    tris = collect(gltf, binary)
    if not tris:
        sys.exit("no textured meshes in %s" % GLB)

    forward = norm(sub(TARGET, EYE))
    right = norm(cross(forward, (0.0, 1.0, 0.0)))
    up = norm(cross(right, forward))
    light = norm(LIGHT)

    def project(p):
        q = sub(p, TARGET)
        return dot(q, right), dot(q, up)

    xs, ys = [], []
    for world, _, _ in tris:
        for p in world:
            x, y = project(p)
            xs.append(x)
            ys.append(y)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    scale = min((W - 2 * MARGIN) / (x1 - x0), (H - 2 * MARGIN) / (y1 - y0))
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    def screen(p):
        x, y = project(p)
        return (W / 2.0 + (x - cx) * scale,
                H / 2.0 - (y - cy) * scale - 12.0)

    # Background + soft ground shadow.
    img = Image.new("RGB", (W, H), (239, 241, 236))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    ground = [screen((x, 0.0, z)) for x, z in
              ((-1.9, -3.3), (1.9, -3.3), (1.9, 3.3), (-1.9, 3.3))]
    gx0, gy0 = min(p[0] for p in ground), min(p[1] for p in ground)
    gx1, gy1 = max(p[0] for p in ground), max(p[1] for p in ground)
    sd.ellipse([gx0, gy0 - 8, gx1, gy1 + 8], fill=(28, 34, 24, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB")
    draw = ImageDraw.Draw(img)

    painted = []
    for world, uvw, is_track in tris:
        n = cross(sub(world[1], world[0]), sub(world[2], world[0]))
        if dot(n, n) < 1e-14:
            continue
        shade = 0.50 + 0.55 * abs(dot(norm(n), light))
        u = sum(v[0] for v in uvw) / 3.0
        v = sum(v[1] for v in uvw) / 3.0
        colour = (track if is_track else body).get(u, v)
        colour = tuple(min(255, int(c * shade)) for c in colour)
        poly = [screen(p) for p in world]
        depth = dot(sub(world[0], EYE), forward)
        painted.append((depth, poly, colour))

    for _, poly, colour in sorted(painted, key=lambda x: -x[0]):
        draw.polygon(poly, fill=colour)

    caption = "SU-130PM  |  tier VIII  |  USSR  |  130 mm M-65S"
    draw.text((MARGIN, H - 34), caption, fill=(52, 60, 48))
    draw.text((MARGIN, 24), "mods/ussr_su130pm - preview", fill=(70, 80, 64))
    img.save(DST, "PNG", optimize=True)
    print("written %s (%d textured triangles, %d bytes)"
          % (DST, len(painted), os.path.getsize(DST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

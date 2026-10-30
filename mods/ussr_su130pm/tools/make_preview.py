#!/usr/bin/env python3
"""Render a 3/4 preview of the finished SU-130PM mod.

Pure-Pillow / pure-Python software renderer: it reads the generated GLB, follows
the node translations, walks every visible triangle through a z-buffer and
samples the body / track textures per pixel at the interpolated UVs, adding a
soft Lambert term so the shapes read.  It is only a visual sanity check - the
game uses its own renderer - but it catches wrong UV orientation, missing
meshes, a broken turret / gun hierarchy and seams between the atlas islands
without opening Godot or Blender.

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
EYE = (7.4, 4.6, 9.6)
TARGET = (0.0, 1.25, 0.0)
LIGHT = (0.42, 0.84, 0.35)
BACKGROUND = (238, 240, 235)


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a):
    length = math.sqrt(dot(a, a)) or 1.0
    return (a[0] / length, a[1] / length, a[2] / length)


class Sampler:
    """Texture lookup with an optional integer repeat (tracks tile ~10x)."""

    def __init__(self, path, tiled):
        self.im = Image.open(path).convert("RGB")
        self.px = self.im.load()
        self.w, self.h = self.im.size
        self.tiled = tiled

    def get(self, u, v):
        v = 1.0 - v                       # glTF / OpenGL -> image rows
        if self.tiled:
            u -= math.floor(u)
            v -= math.floor(v)
        u = min(0.9999, max(0.0, u))
        v = min(0.9999, max(0.0, v))
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
                is_track = "track_mtr" in str(prim.get("material", ""))
                if prim.get("material") is not None:
                    is_track = "track" in gltf["materials"][prim["material"]]["name"]
                for i in range(0, len(idx), 3):
                    a, b, c = idx[i], idx[i + 1], idx[i + 2]
                    world = tuple(tuple(pos[v][k] + t[k] for k in range(3)) for v in (a, b, c))
                    tris.append((world, (uv[a], uv[b], uv[c]), is_track))
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
    scale = min((W - 2 * 70) / (x1 - x0), (H - 2 * 70) / (y1 - y0))
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    def screen(p):
        x, y = project(p)
        return (W / 2.0 + (x - cx) * scale + 40.0,
                H / 2.0 - (y - cy) * scale - 18.0)

    def depth(p):
        return dot(sub(p, EYE), forward)

    # Background + soft ground shadow.
    img = Image.new("RGB", (W, H), BACKGROUND)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    ground = [screen((x, 0.0, z)) for x, z in
              ((-1.9, -3.6), (1.9, -3.6), (1.9, 3.4), (-1.9, 3.4))]
    gx0, gy0 = min(p[0] for p in ground), min(p[1] for p in ground)
    gx1, gy1 = max(p[0] for p in ground), max(p[1] for p in ground)
    sd.ellipse([gx0, gy0 - 10, gx1, gy1 + 10], fill=(30, 36, 26, 95))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB")

    zbuf = [1e18] * (W * H)
    px = img.load()

    for world, uvw, is_track in tris:
        normal = cross(sub(world[1], world[0]), sub(world[2], world[0]))
        if dot(normal, normal) < 1e-14:
            continue
        unit = norm(normal)
        # back-face culling: the model is closed, so facing-away triangles are inside
        if dot(unit, forward) > 0.05:
            continue
        shade = 0.58 + 0.58 * abs(dot(unit, light))
        p0, p1, p2 = (screen(p) for p in world)
        d0, d1, d2 = (depth(p) for p in world)
        area = (p1[0] - p2[0]) * (p0[1] - p2[1]) + (p2[1] - p1[1]) * (p0[0] - p2[0])
        if abs(area) < 1e-9:
            continue
        minx = max(0, int(min(p0[0], p1[0], p2[0])))
        maxx = min(W - 1, int(max(p0[0], p1[0], p2[0])) + 1)
        miny = max(0, int(min(p0[1], p1[1], p2[1])))
        maxy = min(H - 1, int(max(p0[1], p1[1], p2[1])) + 1)
        if minx > maxx or miny > maxy:
            continue
        texture = track if is_track else body
        (ua, va), (ub, vb), (uc, vc) = uvw
        inv = 1.0 / area
        for y in range(miny, maxy + 1):
            row = y * W
            for x in range(minx, maxx + 1):
                l0 = ((p1[0] - p2[0]) * (y - p2[1]) + (p2[1] - p1[1]) * (x - p2[0])) * inv
                l1 = ((p2[0] - p0[0]) * (y - p2[1]) + (p0[1] - p2[1]) * (x - p2[0])) * inv
                l2 = 1.0 - l0 - l1
                if l0 < -1e-6 or l1 < -1e-6 or l2 < -1e-6:
                    continue
                z = l0 * d0 + l1 * d1 + l2 * d2
                if z >= zbuf[row + x]:
                    continue
                zbuf[row + x] = z
                colour = texture.get(l0 * ua + l1 * ub + l2 * uc,
                                     l0 * va + l1 * vb + l2 * vc)
                px[x, y] = (min(255, int(colour[0] * shade)),
                            min(255, int(colour[1] * shade)),
                            min(255, int(colour[2] * shade)))

    draw = ImageDraw.Draw(img)
    caption = "SU-130PM  |  tier VIII  |  USSR  |  130 mm M-65S"
    draw.text((70, H - 40), caption, fill=(52, 60, 48))
    draw.text((70, 26), "mods/ussr_su130pm - original UV layout, original atlas", fill=(70, 80, 64))
    img.save(DST, "PNG", optimize=True)
    print("written %s (%d textured triangles, %d bytes)"
          % (DST, len(tris), os.path.getsize(DST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Paint the SU-130PM textures for the World of Tanks Blitz mod.

tank5/CU-130PM.glb has no texture coordinates, so the game atlas
tank5/R159_SU_130PM.dx11.dds cannot be mapped onto it (there is nothing that
says which pixel belongs to which triangle).  make_glb.py therefore generates
its own UV layout and this script paints a texture for that layout:

  ussr_su130pm.png             2048 x 2048 RGB   body: hull | turret / gun / wheels
  ussr_su130pm_track.png        256 x  256 RGBA  one track link (U across, V along)
  ussr_su130pm_icon.png                      garage icon, from @2x.packed.webp
  ussr_su130pm_icon_small.png                tech-tree icon, from @22x.packed.webp

The body colour ramp is derived from the mean colour of the original DDS atlas
(with a small green shift to match the garage icon).  A high-pass filtered copy
of the atlas is tiled in as fine surface grain: it carries real scratches and
panel noise, but it is deliberately NOT UV-aligned - the original decals,
hatches and markings live only in the lost UV layout and cannot be recovered.

Everything uses a fixed random seed and fixed input files, so running the script
twice gives byte-identical PNGs.

Usage:  python3 mods/ussr_su130pm/tools/make_textures.py
        (needs Pillow: python3 -m pip install pillow)
"""

import os
import random
import sys

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  python3 -m pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank5")
DST = os.path.join(ROOT, "mods", "ussr_su130pm", "assets", "tanks")

ATLAS = os.path.join(SRC, "R159_SU_130PM.dx11.dds")
ICON_BIG = os.path.join(SRC, "ussr-R159_SU_130PM@2x.packed.webp")
ICON_SMALL = os.path.join(SRC, "ussr-R159_SU_130PM@22x.packed.webp")

SIZE = 2048
SEED = 1305

# regions in pixels (x0, y0, x1, y1) - must match the UV regions in make_glb.py
REGION_HULL = (0, 0, 1024, 2048)
REGION_TURRET = (1024, 0, 2048, 1024)
REGION_GUN = (1024, 1024, 2048, 1536)
REGION_WHEEL = (1024, 1536, 2048, 2048)

GUN_METAL = ((24, 26, 28), (54, 57, 60), (104, 108, 112))
DARK_STEEL = ((18, 19, 20), (38, 40, 41), (82, 84, 86))


def armour_ramp():
    """(dark, mid, light) from the mean colour of the original DDS atlas."""
    with Image.open(ATLAS) as atlas:
        mean = ImageStat.Stat(atlas.convert("RGB")).mean
    # The atlas is a dark olive/brown; nudge green up to match the in-game icon.
    mid = (int(mean[0]), int(mean[1]) + 6, max(0, int(mean[2]) - 2))
    dark = tuple(max(0, int(c * 0.35)) for c in mid)
    light = tuple(min(255, int(c * 2.0)) for c in mid)
    return dark, mid, light


def atlas_grain(size):
    """Tiled high-pass noise from the real atlas (surface grain, not decals)."""
    with Image.open(ATLAS) as atlas:
        patch = atlas.convert("L").crop((256, 256, 1792, 1792))
    patch = patch.resize((512, 512), Image.BILINEAR)
    blur = patch.filter(ImageFilter.GaussianBlur(4))
    hp = ImageChops.subtract(patch, blur, scale=1.0, offset=128)
    hp = hp.point(lambda v: int(128 + (v - 128) * 0.45))
    out = Image.new("L", size)
    for y in range(0, size[1], 512):
        for x in range(0, size[0], 512):
            out.paste(hp, (x, y))
    return out.crop((0, 0, size[0], size[1]))


def noise_layer(rng, size, cell, amplitude):
    """Smooth value noise: a coarse random grid blown up with bicubic filtering."""
    w, h = size
    gw, gh = max(2, w // cell + 2), max(2, h // cell + 2)
    grid = Image.new("L", (gw, gh))
    grid.putdata([int(128 + rng.uniform(-amplitude, amplitude)) for _ in range(gw * gh)])
    big = grid.resize((gw * cell, gh * cell), Image.BICUBIC)
    return big.crop((cell // 2, cell // 2, cell // 2 + w, cell // 2 + h))


def grain_layer(rng, size, amplitude, tile=256):
    """Per-pixel grain, built from one random tile (grain has no visible repeat)."""
    t = Image.new("L", (tile, tile))
    t.putdata([int(128 + rng.gauss(0, amplitude)) for _ in range(tile * tile)])
    t = t.filter(ImageFilter.GaussianBlur(0.6))
    out = Image.new("L", size)
    for y in range(0, size[1], tile):
        for x in range(0, size[0], tile):
            out.paste(t, (x, y))
    return out


def mix(layers):
    """Sum signed layers around 128."""
    out = Image.new("L", layers[0].size, 128)
    for layer in layers:
        out = ImageChops.add(out, layer, scale=1.0, offset=-128)
    return out


def weathered(rng, size, ramp, scratches, streaks, surface_grain):
    w, h = size
    shade = mix([
        noise_layer(rng, size, 256, 22),
        noise_layer(rng, size, 64, 12),
        noise_layer(rng, size, 16, 6),
        grain_layer(rng, size, 7),
        surface_grain,
    ])
    # grime streaks running down (rain marks), soft and dark
    streak = Image.new("L", size, 0)
    sdraw = ImageDraw.Draw(streak)
    for _ in range(streaks):
        x = rng.randrange(w)
        y = rng.randrange(h)
        sdraw.line([(x, y), (x + rng.randint(-6, 6), y + rng.randint(h // 16, h // 5))],
                   fill=rng.randint(14, 30), width=rng.randint(2, 7))
    streak = streak.filter(ImageFilter.GaussianBlur(3))
    shade = ImageChops.subtract(shade, streak)
    # scratches / chipped paint, thin and bright
    draw = ImageDraw.Draw(shade)
    for _ in range(scratches):
        x, y = rng.randrange(w), rng.randrange(h)
        length = rng.randint(6, 40)
        dx, dy = rng.uniform(-1, 1), rng.uniform(-1, 1)
        draw.line([(x, y), (x + dx * length, y + dy * length)],
                  fill=rng.randint(170, 215), width=1)
    shade = shade.filter(ImageFilter.GaussianBlur(0.4))
    return ImageOps.colorize(shade, black=ramp[0], mid=ramp[1], white=ramp[2])


def body_texture(rng, ramp):
    grain = atlas_grain((SIZE, SIZE))
    img = Image.new("RGB", (SIZE, SIZE))
    for region, colours, scratches, streaks in (
            (REGION_HULL, ramp, 900, 160),
            (REGION_TURRET, ramp, 450, 80),
            (REGION_GUN, GUN_METAL, 200, 20),
            (REGION_WHEEL, DARK_STEEL, 250, 10)):
        x0, y0, x1, y1 = region
        part = weathered(rng, (x1 - x0, y1 - y0), colours, scratches, streaks,
                         grain.crop((0, 0, x1 - x0, y1 - y0)))
        img.paste(part, (x0, y0))
    return img


def track_texture(rng):
    """One track link: U (x) runs across the belt, V (y) along it."""
    n = 256
    base = weathered(rng, (n, n), DARK_STEEL, 40, 0,
                     atlas_grain((n, n))).convert("RGBA")
    d = ImageDraw.Draw(base)
    # gap between two links at the tile border
    d.rectangle([0, 0, n, 9], fill=(14, 15, 15, 255))
    d.rectangle([0, n - 10, n, n], fill=(14, 15, 15, 255))
    # raised grouser across the link: lit top edge, body, shadow
    d.rectangle([10, 104, n - 10, 112], fill=(108, 111, 110, 255))
    d.rectangle([10, 113, n - 10, 146], fill=(70, 73, 72, 255))
    d.rectangle([10, 147, n - 10, 156], fill=(20, 21, 21, 255))
    # guide horn in the middle of the belt
    d.rectangle([116, 40, 140, 216], fill=(58, 60, 59, 255))
    d.rectangle([116, 40, 120, 216], fill=(104, 107, 106, 255))
    # pin bosses at both edges
    for x in (4, n - 22):
        d.rectangle([x, 12, x + 18, n - 13], fill=(43, 45, 44, 255))
    return base


def crop_icon(path, pad):
    with Image.open(path) as im:
        icon = im.convert("RGBA")
    box = icon.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box:
        box = (max(0, box[0] - pad), max(0, box[1] - pad),
               min(icon.width, box[2] + pad), min(icon.height, box[3] + pad))
        icon = icon.crop(box)
    return icon


def save(img, name, note):
    path = os.path.join(DST, name)
    img.save(path, "PNG", optimize=True)
    print("%-34s %dx%d %s (%s)" % (name, img.width, img.height, img.mode, note))


def main():
    rng = random.Random(SEED)
    os.makedirs(DST, exist_ok=True)
    ramp = armour_ramp()
    print("armour ramp from atlas: %s / %s / %s" % ramp)
    save(body_texture(rng, ramp), "ussr_su130pm.png", "generated body texture")
    save(track_texture(rng), "ussr_su130pm_track.png", "generated track link")
    save(crop_icon(ICON_BIG, 8), "ussr_su130pm_icon.png", "from @2x.packed.webp")
    save(crop_icon(ICON_SMALL, 4), "ussr_su130pm_icon_small.png", "from @22x.packed.webp")


if __name__ == "__main__":
    main()

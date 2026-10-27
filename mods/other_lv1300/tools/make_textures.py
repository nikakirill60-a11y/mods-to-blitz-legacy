#!/usr/bin/env python3
"""Paint the LV-1300 Uranus textures for the World of Tanks Blitz mod.

tank3/lv1300.glb has no texture coordinates, so the game atlas
tank3/Oth46_LV1300.dx11.png cannot be mapped onto it (there is nothing that says
which pixel belongs to which triangle).  make_glb.py therefore generates its own
UV layout and this script paints a texture for that layout:

  other_lv1300.png        2048 x 2048 RGB   body: hull | turret / gun / wheels
  other_lv1300_track.png   256 x  256 RGBA  one track link (U across, V along)
  other_lv1300_icon.png    garage icon, from other-Oth46_LV1300.packed.webp

The body colours are sampled from the original atlas (the bare blue armour plate
and the dark steel parts), and the surface is weathered paint: large soft colour
variation, fine grain, a few scratches and grime streaks.  There are no decals or
panel details - those live only in the original UV layout, which is lost.

Everything is generated from a fixed random seed, so running the script twice
gives byte-identical files.

Usage:  python3 mods/other_lv1300/tools/make_textures.py
        (needs Pillow: python3 -m pip install pillow)
"""

import os
import random
import sys

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  python3 -m pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank3")
DST = os.path.join(ROOT, "mods", "other_lv1300", "assets", "tanks")

SIZE = 2048
SEED = 1300

# (dark, mid, light) colour ramps.  "mid" of the armour is the median colour of the
# bare blue plate of Oth46_LV1300.dx11.png (99, 125, 140), the steel parts use the
# median of its dark metal areas (41..57).
ARMOUR = ((50, 66, 78), (99, 125, 140), (168, 188, 199))
GUN_METAL = ((30, 33, 37), (64, 70, 76), (128, 134, 139))
DARK_STEEL = ((26, 27, 28), (55, 57, 58), (110, 112, 113))

# regions in pixels (x0, y0, x1, y1) - must match the UV regions in make_glb.py
REGION_HULL = (0, 0, 1024, 2048)
REGION_TURRET = (1024, 0, 2048, 1024)
REGION_GUN = (1024, 1024, 2048, 1536)
REGION_WHEEL = (1024, 1536, 2048, 2048)


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


def weathered(rng, size, ramp, scratches, streaks):
    w, h = size
    shade = mix([
        noise_layer(rng, size, 256, 22),
        noise_layer(rng, size, 64, 12),
        noise_layer(rng, size, 16, 6),
        grain_layer(rng, size, 7),
    ])
    draw = ImageDraw.Draw(shade)
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
    for _ in range(scratches):
        x, y = rng.randrange(w), rng.randrange(h)
        length = rng.randint(6, 40)
        dx, dy = rng.uniform(-1, 1), rng.uniform(-1, 1)
        draw.line([(x, y), (x + dx * length, y + dy * length)],
                  fill=rng.randint(170, 215), width=1)
    shade = shade.filter(ImageFilter.GaussianBlur(0.4))
    return ImageOps.colorize(shade, black=ramp[0], mid=ramp[1], white=ramp[2])


def body_texture(rng):
    img = Image.new("RGB", (SIZE, SIZE))
    for region, ramp, scratches, streaks in (
            (REGION_HULL, ARMOUR, 900, 160),
            (REGION_TURRET, ARMOUR, 450, 80),
            (REGION_GUN, GUN_METAL, 200, 20),
            (REGION_WHEEL, DARK_STEEL, 250, 10)):
        x0, y0, x1, y1 = region
        img.paste(weathered(rng, (x1 - x0, y1 - y0), ramp, scratches, streaks), (x0, y0))
    return img


def track_texture(rng):
    """One track link: U (x) runs across the belt, V (y) along it."""
    n = 256
    base = weathered(rng, (n, n), DARK_STEEL, 40, 0).convert("RGBA")
    d = ImageDraw.Draw(base)
    # gap between two links at the tile border
    d.rectangle([0, 0, n, 9], fill=(16, 16, 17, 255))
    d.rectangle([0, n - 10, n, n], fill=(16, 16, 17, 255))
    # raised grouser across the link: lit top edge, body, shadow
    d.rectangle([10, 104, n - 10, 112], fill=(122, 124, 125, 255))
    d.rectangle([10, 113, n - 10, 146], fill=(78, 80, 81, 255))
    d.rectangle([10, 147, n - 10, 156], fill=(22, 22, 23, 255))
    # guide horn in the middle of the belt
    d.rectangle([116, 40, 140, 216], fill=(64, 66, 67, 255))
    d.rectangle([116, 40, 120, 216], fill=(112, 114, 115, 255))
    # pin bosses at both edges
    for x in (4, n - 22):
        d.rectangle([x, 12, x + 18, n - 13], fill=(48, 50, 51, 255))
    return base


def main():
    rng = random.Random(SEED)
    os.makedirs(DST, exist_ok=True)

    body = body_texture(rng)
    body.save(os.path.join(DST, "other_lv1300.png"), "PNG", optimize=True)
    print("other_lv1300.png        %dx%d RGB  (generated)" % body.size)

    track = track_texture(rng)
    track.save(os.path.join(DST, "other_lv1300_track.png"), "PNG", optimize=True)
    print("other_lv1300_track.png  %dx%d RGBA (generated)" % track.size)

    icon = Image.open(os.path.join(SRC, "other-Oth46_LV1300.packed.webp")).convert("RGBA")
    # the tank sits in the left part of a 256x128 canvas: crop to the visible pixels
    # plus a small border, like the other garage icons of this repository
    box = icon.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box:
        pad = 4
        box = (max(0, box[0] - pad), max(0, box[1] - pad),
               min(icon.width, box[2] + pad), min(icon.height, box[3] + pad))
        icon = icon.crop(box)
    icon.save(os.path.join(DST, "other_lv1300_icon.png"), "PNG", optimize=True)
    print("other_lv1300_icon.png   %dx%d RGBA (from other-Oth46_LV1300.packed.webp)" % icon.size)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prepare the SU-130PM textures for the World of Tanks Blitz mod.

The source model (tank5/CU-130PM.glb) keeps the original UV layout, so the
original 2048x2048 atlas tank5/R159_SU_130PM.dx11.dds fits it one to one.  The
only thing that has to be done to it is the usual vertical flip:

  * Direct3D textures (everything in `tank1/` ... `tank5/`) are stored with the
    V axis pointing down, glTF / OpenGL UVs use V pointing up.  Shipping the file
    as it is makes every decal, hatch and weld line appear upside down and
    smeared - exactly the "texture is applied wrong" symptom.
  * Proof from the mods that are already in the repository: the base colour
    images embedded in the source MS-11 and 60TP glTF files are pixel-identical
    to the *V-flipped* files from tank4/ and tank1/, and those embedded images
    are addressed by the glTF UVs.  `make_textures.py` of `ussr_ms11` and
    `poland_60tp` flip for the same reason.

There is no separate track atlas in `tank5/` (the game keeps it in a file we do
not have), but the model's track UVs are a proper strip: U runs across the belt,
V runs along it and repeats about ten times around the loop, i.e. one repeat is
~1.25 m of belt.  `track_texture()` paints a seamless strip with eight links in
it so the pitch comes out at the real ~0.16 m.

Output (all files land in assets/tanks/):

  ussr_su130pm.png             2048 x 2048 RGB   original atlas, V-flipped
  ussr_su130pm_track.png        256 x  256 RGBA  eight track links
  ussr_su130pm_icon.png                       garage icon, from @2x.packed.webp
  ussr_su130pm_icon_small.png                 tech-tree icon, from @22x.packed.webp

Everything uses fixed input files and a fixed random seed, so running the script
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

SEED = 1305
TRACK_SIZE = (256, 512)    # across the belt (U) x along it (V)
TRACK_LINKS = 8            # links per V repeat, see the docstring


def body_texture():
    """The game atlas, flipped into glTF / OpenGL orientation."""
    with Image.open(ATLAS) as atlas:
        image = atlas.convert("RGB").transpose(Image.FLIP_TOP_BOTTOM)
    return image


def atlas_mean():
    with Image.open(ATLAS) as atlas:
        return ImageStat.Stat(atlas.convert("RGB")).mean


def noise_layer(rng, size, cell, amplitude):
    """Smooth value noise: a coarse random grid blown up with bicubic filtering."""
    w, h = size
    gw, gh = max(2, w // cell + 2), max(2, h // cell + 2)
    grid = Image.new("L", (gw, gh))
    grid.putdata([int(128 + rng.uniform(-amplitude, amplitude)) for _ in range(gw * gh)])
    big = grid.resize((gw * cell, gh * cell), Image.BICUBIC)
    return big.crop((cell // 2, cell // 2, cell // 2 + w, cell // 2 + h))


def grain_layer(rng, size, amplitude, tile=128):
    """Per-pixel grain, built from one random tile that is seamless by wrapping."""
    t = Image.new("L", (tile, tile))
    t.putdata([int(128 + rng.gauss(0, amplitude)) for _ in range(tile * tile)])
    t = t.filter(ImageFilter.GaussianBlur(0.6))
    out = Image.new("L", size)
    for y in range(0, size[1], tile):
        for x in range(0, size[0], tile):
            out.paste(t, (x, y))
    return out.crop((0, 0, size[0], size[1]))


def colorize(shade, ramp):
    """Map an 0..255 grey shade onto a dark / mid / light colour ramp."""
    out = Image.new("RGB", shade.size)
    lut = shade.load()
    w, h = shade.size
    for y in range(h):
        for x in range(w):
            v = lut[x, y] / 255.0
            if v < 0.5:
                k = v * 2.0
                colour = tuple(int(ramp[0][c] + (ramp[1][c] - ramp[0][c]) * k) for c in range(3))
            else:
                k = (v - 0.5) * 2.0
                colour = tuple(int(ramp[1][c] + (ramp[2][c] - ramp[1][c]) * k) for c in range(3))
            out.putpixel((x, y), colour)
    return out


def grain(rng, size, cell, amplitude, streaks):
    """Seamless weathered metal: value noise + grain + soft grime streaks."""
    shade = Image.new("L", size, 128)
    for layer in (noise_layer(rng, size, cell, amplitude),
                  noise_layer(rng, size, max(4, cell // 4), amplitude * 0.5),
                  grain_layer(rng, size, 7)):
        shade = ImageChops.add(shade, layer, scale=1.0, offset=-128)
    streak = Image.new("L", size, 0)
    sdraw = ImageDraw.Draw(streak)
    for _ in range(streaks):
        x = rng.randrange(size[0])
        y = rng.randrange(size[1])
        sdraw.line([(x, y), (x + rng.randint(-3, 3), y + rng.randint(20, 70))],
                   fill=rng.randint(12, 26), width=rng.randint(2, 6))
    streak = streak.filter(ImageFilter.GaussianBlur(3))
    return ImageChops.subtract(shade, streak)


def shade_rect(paint, box, value):
    """Set a grey level inside a rectangle of the structure layer."""
    ImageDraw.Draw(paint).rectangle(box, fill=value)


def track_texture(rng):
    """One tile = the full belt width (U) x TRACK_LINKS links along the belt (V).

    The atlas in `tank5/` has no track fragment (the game kept the tracks in a
    separate texture that was not shipped with this model), so the links are
    painted: a weathered steel strip, then per link a dark gap, a lit grouser
    edge, the grouser body, its shadow, a guide horn in the middle and the pin
    bosses at both edges.  Everything wraps around the tile border, so the strip
    repeats seamlessly along the belt.

    The tile is twice as tall as it is wide on purpose: one V repeat covers
    about 1.25 m of belt while the belt itself is ~0.41 m wide, so a square tile
    would stretch every link.
    """
    size = TRACK_SIZE
    w, h = size
    mean = atlas_mean()
    # steel is a bit lighter and more neutral than the olive hull paint
    mid = (min(255, int(mean[0] * 1.05)), min(255, int(mean[1] * 1.05)),
           min(255, int(mean[2] * 1.15)))
    ramp = (tuple(int(c * 0.55) for c in mid), mid,
            tuple(min(255, int(c * 1.8)) for c in mid))

    detail = grain(rng, size, 64, 20, 90)
    paint = Image.new("L", size, 128)
    pitch = h // TRACK_LINKS
    horn_w = 26
    for link in range(TRACK_LINKS):
        y = link * pitch
        shade_rect(paint, [0, y, w, y + 4], 26)                    # gap between links
        shade_rect(paint, [0, y + 5, w, y + 7], 176)               # lit edge of the gap
        shade_rect(paint, [7, y + 8, w - 7, y + 12], 186)          # grouser, lit top
        shade_rect(paint, [7, y + 13, w - 7, y + 15], 104)         # grouser, shadow line
        shade_rect(paint, [7, y + 16, w - 7, y + pitch - 12], 150)  # grouser body
        shade_rect(paint, [7, y + pitch - 12, w - 7, y + pitch - 7], 96)   # under the grouser
        shade_rect(paint, [7, y + pitch - 7, w - 7, y + pitch - 6], 168)   # pin row highlight
        for x in (2, w - 13):                                       # pin bosses
            shade_rect(paint, [x, y + 8, x + 11, y + pitch - 8], 92)
            shade_rect(paint, [x, y + 8, x + 3, y + pitch - 8], 178)
    # both layers are centred on 128: add them around that value
    shade = ImageChops.add(detail, paint, scale=1.0, offset=-128)
    img = colorize(shade, ramp).convert("RGBA")

    # guide horns and a little rust, composited so the grain stays visible
    details = Image.new("RGBA", size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(details)
    for link in range(TRACK_LINKS):
        y = link * pitch
        dd.rectangle([w // 2 - horn_w // 2, y + 6, w // 2 + horn_w // 2, y + pitch - 6],
                     fill=(58, 61, 60, 205))
        dd.rectangle([w // 2 - horn_w // 2, y + 6, w // 2 - horn_w // 2 + 4, y + pitch - 6],
                     fill=(124, 127, 125, 215))
        dd.rectangle([w // 2 + horn_w // 2 - 4, y + 6, w // 2 + horn_w // 2, y + pitch - 6],
                     fill=(24, 25, 25, 215))
        for _ in range(6):
            x = rng.randrange(10, w - 10)
            spot = y + rng.randrange(8, pitch - 10)
            r = rng.randint(1, 4)
            dd.ellipse([x, spot, x + r, spot + r], fill=(74, 52, 32, 80))
    img = Image.alpha_composite(img, details)

    # darker towards both sides of the belt: fakes the thickness of the links
    edge = Image.new("L", size, 0)
    ed = ImageDraw.Draw(edge)
    for x in range(14):
        value = int(72 * (1.0 - x / 14.0))
        ed.line([(x, 0), (x, h)], fill=value)
        ed.line([(w - 1 - x, 0), (w - 1 - x, h)], fill=value)
    dark = Image.new("RGBA", size, (10, 10, 10, 255))
    img = Image.composite(dark, img, edge)
    img.putalpha(255)
    return img


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
    body = body_texture()
    save(body, "ussr_su130pm.png", "original atlas, V-flipped")
    save(track_texture(rng), "ussr_su130pm_track.png", "%d links per repeat" % TRACK_LINKS)
    save(crop_icon(ICON_BIG, 8), "ussr_su130pm_icon.png", "from @2x.packed.webp")
    save(crop_icon(ICON_SMALL, 4), "ussr_su130pm_icon_small.png", "from @22x.packed.webp")


if __name__ == "__main__":
    main()

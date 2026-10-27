#!/usr/bin/env python3
"""Prepare the 60TP textures for the World of Tanks Blitz mod.

The textures shipped in `tank1/` come straight out of the game data and are
stored in Direct3D orientation, i.e. with the V texture axis flipped relative
to the glTF / OpenGL convention that the UVs inside `60TP.glb` use.  Using them
as-is makes the camouflage, the decals and the panel lines appear upside down
and smeared across the model (the hull looks like it is wearing the turret's
texels), which is exactly the "texture is applied wrong" symptom.

Proof that the flip is required: the base colour images embedded in the source
`tank1/60TP.glb` are byte-identical to the V-flipped files from `tank1/`, not to
the files as they are stored.

This script therefore
  * V-flips the base colour textures so they match the UVs,
  * renaming them to the names the mod (and `poland_60tp.tres`) expects,
  * converts the garage icon from WebP (it is a .png file with WebP payload)
    to a real PNG.

Usage:  python3 mods/poland_60tp/tools/make_textures.py
        (needs Pillow: python3 -m pip install pillow)
"""

import os
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  python3 -m pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank1")
DST = os.path.join(ROOT, "mods", "poland_60tp", "assets", "tanks")

# source file -> (destination file, flip vertically?, RGBA output?)
JOBS = [
    ("Pl15_60TP_BC.dx11_4.png", "poland_60tp.png", True, False),
    ("Pl15_60TP_track_BC.dx11_1.png", "poland_60tp_track.png", True, True),
    ("60TP_Lewandowskiego_In-Game_Icon.png", "poland_60tp_icon.png", False, True),
]


def main():
    os.makedirs(DST, exist_ok=True)
    for source_name, target_name, flip, rgba in JOBS:
        source = os.path.join(SRC, source_name)
        target = os.path.join(DST, target_name)
        if not os.path.exists(source):
            print("skip (missing): %s" % source)
            continue
        image = Image.open(source)
        fmt = image.format
        if flip:
            image = image.transpose(Image.FLIP_TOP_BOTTOM)
        if rgba and image.mode != "RGBA":
            image = image.convert("RGBA")
        elif not rgba and image.mode != "RGB":
            image = image.convert("RGB")
        image.save(target, "PNG", optimize=True)
        print("%-40s %-24s %s -> %s  %s" % (
            source_name, fmt, image.size, target_name, image.mode))


if __name__ == "__main__":
    main()

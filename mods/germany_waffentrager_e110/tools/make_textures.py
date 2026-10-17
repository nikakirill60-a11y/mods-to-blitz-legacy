#!/usr/bin/env python3
"""Prepare the Waffentrager E 110 textures for the World of Tanks Blitz mod.

Unlike the 60TP assets, the textures supplied in `tank2/` describe exactly the
UV layout of `Waffentrager_E110.glb` and must NOT be flipped: the decals on the
source texture (the "ACHTUNG" stencil on top of the gun cradle, for example) read
correctly on the model with the image as it is stored, and flipping it turns them
upside down.  This was verified by projecting the decal UV island back onto the
model and checking that the text reads left to right and the right way up.

So this script only renames/converts:

  Waffentrager_E100_TLXXL_tank_01_AM.png   -> germany_waffentrager_e110.png        (body)
  E100_TLXXL_track_segment_AM.png         -> germany_waffentrager_e110_track.png  (tracks)
  Waffentrager_E100_TL_light_01_AM.png    -> germany_waffentrager_e110_light.png  (lamps)

The garage icon is already a normal PNG and is copied as-is.

Usage:  python3 mods/germany_waffentrager_e110/tools/make_textures.py
        (needs Pillow: python3 -m pip install pillow)
"""

import os
import shutil
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  python3 -m pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank2")
DST = os.path.join(ROOT, "mods", "germany_waffentrager_e110", "assets", "tanks")

# source file -> (destination file, output mode)
JOBS = [
    ("Waffentrager_E100_TLXXL_tank_01_AM.png", "germany_waffentrager_e110.png", "RGB"),
    ("E100_TLXXL_track_segment_AM.png", "germany_waffentrager_e110_track.png", "RGBA"),
    ("Waffentrager_E100_TL_light_01_AM.png", "germany_waffentrager_e110_light.png", "RGB"),
    ("Waffentrager_E110_icon.png", "germany_waffentrager_e110_icon.png", "RGBA"),
]


def main():
    os.makedirs(DST, exist_ok=True)
    for source_name, target_name, mode in JOBS:
        source = os.path.join(SRC, source_name)
        target = os.path.join(DST, target_name)
        if not os.path.exists(source):
            print("skip (missing): %s" % source)
            continue
        image = Image.open(source)
        source_format = image.format
        source_size = image.size
        if image.mode != mode:
            image = image.convert(mode)
        image.save(target, "PNG", optimize=True)
        print("%-42s %-5s %-12s -> %-38s %s" % (
            source_name, source_format, "%dx%d" % source_size, target_name, mode))


if __name__ == "__main__":
    main()

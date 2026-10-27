#!/usr/bin/env python3
"""Build ussr_su130pm_install.zip in the repository root (deterministic).

The archive is laid out like the game folder (assets/tanks, assets/data/tanks_mod)
plus README.txt.  The .import files stay in the repository only - the game
creates its own on first launch.

Usage:  python3 mods/ussr_su130pm/tools/make_zip.py
"""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MOD = os.path.join(ROOT, "mods", "ussr_su130pm")
DST = os.path.join(ROOT, "ussr_su130pm_install.zip")

FILES = [
    ("tools/README.txt", "README.txt"),
    ("assets/tanks/ussr_su130pm.glb", "assets/tanks/ussr_su130pm.glb"),
    ("assets/tanks/ussr_su130pm.png", "assets/tanks/ussr_su130pm.png"),
    ("assets/tanks/ussr_su130pm_track.png", "assets/tanks/ussr_su130pm_track.png"),
    ("assets/tanks/ussr_su130pm_icon.png", "assets/tanks/ussr_su130pm_icon.png"),
    ("assets/tanks/ussr_su130pm_icon_small.png", "assets/tanks/ussr_su130pm_icon_small.png"),
    ("tanks_mod/ussr_su130pm.tres", "assets/data/tanks_mod/ussr_su130pm.tres"),
]


def main():
    with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in FILES:
            info = zipfile.ZipInfo(arc, date_time=(2026, 10, 27, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            with open(os.path.join(MOD, src), "rb") as fh:
                zf.writestr(info, fh.read(), compresslevel=9)
    print("written %s (%d bytes)" % (DST, os.path.getsize(DST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

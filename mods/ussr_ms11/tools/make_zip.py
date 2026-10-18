#!/usr/bin/env python3
"""Build ussr_ms11_install.zip in the repository root (deterministic).

The archive is laid out like the game folder (assets/tanks, assets/data/tanks_mod),
plus README.txt and the drum test variants in variants/ (outside assets/, so they
are never loaded by accident).

Usage:  python3 mods/ussr_ms11/tools/make_zip.py
"""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MOD = os.path.join(ROOT, "mods", "ussr_ms11")
DST = os.path.join(ROOT, "ussr_ms11_install.zip")

FILES = [
    ("tools/README.txt", "README.txt"),
    ("assets/tanks/ussr_ms11.glb", "assets/tanks/ussr_ms11.glb"),
    ("assets/tanks/ussr_ms11.png", "assets/tanks/ussr_ms11.png"),
    ("assets/tanks/ussr_ms11_track.png", "assets/tanks/ussr_ms11_track.png"),
    ("assets/tanks/ussr_ms11_icon.png", "assets/tanks/ussr_ms11_icon.png"),
    ("tanks_mod/ussr_ms11.tres", "assets/data/tanks_mod/ussr_ms11.tres"),
    ("variants/fire_mode_1/ussr_ms11.tres", "variants/fire_mode_1/ussr_ms11.tres"),
    ("variants/fire_mode_3/ussr_ms11.tres", "variants/fire_mode_3/ussr_ms11.tres"),
    ("variants/no_drum/ussr_ms11.tres", "variants/no_drum/ussr_ms11.tres"),
]


def main():
    with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in FILES:
            info = zipfile.ZipInfo(arc, date_time=(2026, 10, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            with open(os.path.join(MOD, src), "rb") as fh:
                zf.writestr(info, fh.read(), compresslevel=9)
    print("written %s (%d bytes)" % (DST, os.path.getsize(DST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

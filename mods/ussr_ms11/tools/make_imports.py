#!/usr/bin/env python3
"""Generate the Godot .import side car files for the MS-11 mod.

The other mods in this repository ship the .import files of their assets, so the
mod folder can be dropped into a Godot project unchanged.  The hash inside the
file name is the md5 of the res:// path of the source file, and the uid has to be
unique inside the project.

Usage:  python3 mods/ussr_ms11/tools/make_imports.py
"""

import hashlib
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MOD = os.path.join(ROOT, "mods", "ussr_ms11")
ASSETS = os.path.join(MOD, "assets", "tanks")
RES_DIR = "res://mods/ussr_ms11/assets/tanks"

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

TEXTURE_PARAMS = """compress/mode=2
compress/high_quality=true
compress/lossy_quality=0.7
compress/uastc_level=0
compress/rdo_quality_loss=0.0
compress/hdr_compression=1
compress/normal_map=2
compress/channel_pack=0
mipmaps/generate=true
mipmaps/limit=4
roughness/mode=1
roughness/src_normal=""
process/channel_remap/red=0
process/channel_remap/green=1
process/channel_remap/blue=2
process/channel_remap/alpha=3
process/fix_alpha_border=false
process/premult_alpha=false
process/normal_map_invert_y=false
process/hdr_as_srgb=false
process/hdr_clamp_exposure=false
process/size_limit=0
detect_3d/compress_to=0
"""

SCENE_PARAMS = """nodes/root_type=""
nodes/root_name=""
nodes/root_script=null
mesh_library/use_node_names_as_mesh_names=false
array_mesh/deduplicate_surfaces=true
nodes/apply_root_scale=true
nodes/root_scale={root_scale}
nodes/import_as_skeleton_bones=false
nodes/use_name_suffixes=true
nodes/use_node_type_suffixes=true
meshes/ensure_tangents=true
meshes/generate_lods=true
meshes/create_shadow_meshes=false
meshes/light_baking=1
meshes/lightmap_texel_size=0.2
meshes/force_disable_compression=false
skins/use_named_skins=true
animation/import=true
animation/fps=30
animation/trimming=false
animation/remove_immutable_tracks=true
animation/import_rest_as_RESET=false
import_script/path=""
materials/extract=0
materials/extract_format=0
materials/extract_path=""
_subresources={{}}
gltf/naming_version=1
gltf/embedded_image_handling=0
gltf/texture_map_mode=1
"""


def make_uid(seed):
    """Random but reproducible uid in the Godot text format (base36, 13 characters)."""
    rnd = random.Random(seed)
    value = rnd.randrange(36 ** 12, 2 ** 63)
    out = ""
    while value:
        value, rest = divmod(value, 36)
        out = ALPHABET[rest] + out
    return out


def import_id(res_path):
    return hashlib.md5(res_path.encode("utf-8")).hexdigest()


def texture_import(name, uid):
    res_path = "%s/%s" % (RES_DIR, name)
    digest = import_id(res_path)
    base = "res://.godot/imported/%s-%s" % (name, digest)
    return """[remap]

importer="texture"
type="CompressedTexture2D"
uid="uid://{uid}"
path.bptc="{base}.bptc.ctex"
path.astc="{base}.astc.ctex"
metadata={{
"imported_formats": ["s3tc_bptc", "etc2_astc"],
"vram_texture": true
}}

[deps]

source_file="{res_path}"
dest_files=["{base}.bptc.ctex", "{base}.astc.ctex"]

[params]

{params}""".format(uid=uid, base=base, res_path=res_path, params=TEXTURE_PARAMS)


def scene_import(name, uid, root_scale):
    res_path = "%s/%s" % (RES_DIR, name)
    digest = import_id(res_path)
    base = "res://.godot/imported/%s-%s.scn" % (name, digest)
    return """[remap]

importer="scene"
importer_version=1
type="PackedScene"
uid="uid://{uid}"
path="{base}"

[deps]

source_file="{res_path}"
dest_files=["{base}"]

[params]

{params}""".format(uid=uid, base=base, res_path=res_path,
                    params=SCENE_PARAMS.format(root_scale=root_scale))


def main():
    targets = [
        ("ussr_ms11.png", "texture"),
        ("ussr_ms11_track.png", "texture"),
        ("ussr_ms11_icon.png", "texture"),
        ("ussr_ms11.glb", "scene"),
    ]
    for name, kind in targets:
        if not os.path.exists(os.path.join(ASSETS, name)):
            print("missing asset: %s" % name)
            return 1
        uid = make_uid("ussr_ms11:" + name)
        if kind == "texture":
            content = texture_import(name, uid)
        else:
            # the model is authored in metres, so no root scale is applied
            content = scene_import(name, uid, 1.0)
        path = os.path.join(ASSETS, name + ".import")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print("written %s (uid://%s)" % (path, uid))
    return 0


if __name__ == "__main__":
    sys.exit(main())

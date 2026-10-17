#!/usr/bin/env python3
"""Prepare the 60TP Lewandowskiego model for the World of Tanks Blitz mod.

The source model (tank1/60TP.glb) is a plain Blender export: every node carries a
-90 deg yaw around Y and the whole scene is flat (no hull / turret / gun hierarchy).
The Blitz engine expects a specific node layout, the same one the other mods in
this repository use:

    <tank name>                     (root, no mesh)
    |-- hull_vis                   (hull mesh)  -> wheelL1..N, wheelR1..N
    |-- <tank>_hull_phys           (collision box)
    |-- <tank>_module_tracks_40    (suspension collision box)
    |-- trackL / trackR            (animated track meshes)
    `-- turret01                   (turret mesh)
        |-- <tank>_armor_turret_*  (armour plates)
        |-- <tank>_gun_mantlet     (visible mantlet, taken from the source model)
        |-- <tank>_gun_mask_250    (invisible gun collision volume)
        `-- gun01                  (gun mesh)
            `-- gun01_shoot_pos    (muzzle marker)

Nodes whose names contain `phys`, `armor_`, `mask_` or `shoot_pos` are logic
nodes: the engine uses them for collision / armour / muzzle logic and does not
draw them.  Everything that has to be visible therefore must NOT be named that
way, which is why the mantlet keeps its own `gun_mantlet` node.

This script rewrites the glb so it matches that layout:
  * the stray -90 deg yaw is removed (the mesh data is already Y-up / Z-forward),
  * the turret and the gun are re-parented and their pivots are baked into the
    vertex data (turret ring / gun trunnion),
  * a muzzle marker, the hull and suspension collision boxes and the armour
    plates are generated.

Usage:  python3 mods/poland_60tp/tools/make_glb.py
"""

import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank1", "60TP.glb")
DST = os.path.join(ROOT, "mods", "poland_60tp", "assets", "tanks", "poland_60tp.glb")

TANK_NAME = "poland_60tp"

# Armour layout of the 60TP (Blitz): hull 120/100/90, turret 280/160/100.
HULL_PLATES = [("armor_hull_120", 120.0), ("armor_hull_100", 100.0), ("armor_hull_90", 90.0)]
TURRET_PLATES = [("armor_turret_280", 280.0), ("armor_turret_160", 160.0), ("armor_turret_100", 100.0)]

GL_TRIANGLES = 4
GL_UNSIGNED_SHORT = 5123
GL_FLOAT = 5126
GL_ARRAY_BUFFER = 34962
GL_ELEMENT_ARRAY_BUFFER = 34963


# --------------------------------------------------------------------------------------
# glb container helpers
# --------------------------------------------------------------------------------------
def load_glb(path):
    with open(path, "rb") as fh:
        data = fh.read()
    magic, version, length = struct.unpack("<III", data[:12])
    if magic != 0x46546C67:
        raise ValueError("not a glb file: %s" % path)
    offset, chunks = 12, []
    while offset < length:
        chunk_len, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        chunks.append((chunk_type, data[offset + 8:offset + 8 + chunk_len]))
        offset += 8 + chunk_len
    gltf = json.loads(chunks[0][1].decode("utf-8"))
    binary = chunks[1][1] if len(chunks) > 1 else b""
    return gltf, bytearray(binary)


def save_glb(path, gltf, binary):
    json_chunk = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    bin_chunk = bytes(binary) + b"\x00" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk)
        fh.write(struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk)


# --------------------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------------------
def accessor_bounds(gltf, accessor_index):
    acc = gltf["accessors"][accessor_index]
    return list(acc["min"]), list(acc["max"])


def translate_mesh(gltf, binary, mesh_index, offset):
    """Shift every vertex of a mesh by `offset` and refresh the accessor bounds."""
    for prim in gltf["meshes"][mesh_index]["primitives"]:
        acc = gltf["accessors"][prim["attributes"]["POSITION"]]
        view = gltf["bufferViews"][acc["bufferView"]]
        base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        count = acc["count"]
        values = struct.unpack_from("<%df" % (count * 3), binary, base)
        moved = []
        for i in range(count):
            moved.extend((values[i * 3] + offset[0],
                          values[i * 3 + 1] + offset[1],
                          values[i * 3 + 2] + offset[2]))
        struct.pack_into("<%df" % (count * 3), binary, base, *moved)
        mins = [min(values[i * 3 + k] for i in range(count)) + offset[k] for k in range(3)]
        maxs = [max(values[i * 3 + k] for i in range(count)) + offset[k] for k in range(3)]
        acc["min"] = mins
        acc["max"] = maxs


def append_buffer(gltf, binary, blob):
    """Append raw bytes to the binary chunk, 4 byte aligned, returns the byte offset."""
    while len(binary) % 4:
        binary.append(0)
    offset = len(binary)
    binary.extend(blob)
    return offset


def add_box(gltf, binary, materials, name, low, high, material=None):
    """Create an axis aligned box mesh and return its index."""
    x0, y0, z0 = low
    x1, y1, z1 = high
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    # per face: 4 corners (ccw) + normal
    faces = [
        ((0, 3, 2, 1), (0.0, 0.0, -1.0)),   # -Z
        ((4, 5, 6, 7), (0.0, 0.0, 1.0)),    # +Z
        ((0, 1, 5, 4), (0.0, -1.0, 0.0)),   # -Y
        ((3, 7, 6, 2), (0.0, 1.0, 0.0)),    # +Y
        ((0, 4, 7, 3), (-1.0, 0.0, 0.0)),   # -X
        ((1, 2, 6, 5), (1.0, 0.0, 0.0)),    # +X
    ]
    positions, normals, indices = [], [], []
    for quad, normal in faces:
        start = len(positions) // 3
        for corner in quad:
            positions.extend(corners[corner])
            normals.extend(normal)
        indices.extend([start, start + 1, start + 2, start, start + 2, start + 3])

    pos_blob = struct.pack("<%df" % len(positions), *positions)
    nrm_blob = struct.pack("<%df" % len(normals), *normals)
    idx_blob = struct.pack("<%dH" % len(indices), *indices)

    pos_off = append_buffer(gltf, binary, pos_blob)
    nrm_off = append_buffer(gltf, binary, nrm_blob)
    idx_off = append_buffer(gltf, binary, idx_blob)

    pos_view = len(gltf["bufferViews"])
    gltf["bufferViews"].append({"buffer": 0, "byteLength": len(pos_blob), "byteOffset": pos_off, "target": GL_ARRAY_BUFFER})
    nrm_view = len(gltf["bufferViews"])
    gltf["bufferViews"].append({"buffer": 0, "byteLength": len(nrm_blob), "byteOffset": nrm_off, "target": GL_ARRAY_BUFFER})
    idx_view = len(gltf["bufferViews"])
    gltf["bufferViews"].append({"buffer": 0, "byteLength": len(idx_blob), "byteOffset": idx_off, "target": GL_ELEMENT_ARRAY_BUFFER})

    pos_acc = len(gltf["accessors"])
    gltf["accessors"].append({
        "bufferView": pos_view, "componentType": GL_FLOAT, "count": len(positions) // 3,
        "type": "VEC3",
        "min": [min(positions[i::3]) for i in range(3)],
        "max": [max(positions[i::3]) for i in range(3)],
    })
    nrm_acc = len(gltf["accessors"])
    gltf["accessors"].append({
        "bufferView": nrm_view, "componentType": GL_FLOAT, "count": len(normals) // 3, "type": "VEC3",
    })
    idx_acc = len(gltf["accessors"])
    gltf["accessors"].append({
        "bufferView": idx_view, "componentType": GL_UNSIGNED_SHORT, "count": len(indices), "type": "SCALAR",
    })

    prim = {"attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc}, "indices": idx_acc}
    if material is not None:
        prim["material"] = material
    mesh_index = len(gltf["meshes"])
    gltf["meshes"].append({"name": name, "primitives": [prim]})
    return mesh_index


def find_mesh(gltf, name):
    for index, mesh in enumerate(gltf["meshes"]):
        if mesh.get("name") == name:
            return index
    raise KeyError(name)


# --------------------------------------------------------------------------------------
# main conversion
# --------------------------------------------------------------------------------------
def main():
    gltf, binary = load_glb(SRC)

    # -- 1. drop the stray -90 deg yaw, the mesh data is already Y-up / -Z forward ----
    for node in gltf["nodes"]:
        node.pop("rotation", None)

    hull_mesh = find_mesh(gltf, "1_hull_batch_1")
    turret_mesh = find_mesh(gltf, "1_turret_01_batch_1")
    gun_mesh = find_mesh(gltf, "1_gun_02_batch_1")
    mask_mesh = find_mesh(gltf, "1_gun_02_mask_batch_1")
    track_l_mesh = find_mesh(gltf, "1_chassis_track_L_batch_1")
    track_r_mesh = find_mesh(gltf, "1_chassis_track_R_batch_1")

    def bounds(mesh_index):
        prim = gltf["meshes"][mesh_index]["primitives"][0]
        return accessor_bounds(gltf, prim["attributes"]["POSITION"])

    hull_min, hull_max = bounds(hull_mesh)
    turret_min, turret_max = bounds(turret_mesh)
    gun_min, gun_max = bounds(gun_mesh)
    mask_min, mask_max = bounds(mask_mesh)
    track_l_min, track_l_max = bounds(track_l_mesh)
    track_r_min, track_r_max = bounds(track_r_mesh)

    # turret ring: centre of the turret footprint, at the height of the hull roof
    turret_pivot = ((turret_min[0] + turret_max[0]) / 2.0, turret_min[1], (turret_min[2] + turret_max[2]) / 2.0)
    # gun trunnion: where the barrel meets the mantlet
    gun_axis_y = (gun_min[1] + gun_max[1]) / 2.0
    trunnion = (0.0, gun_axis_y, mask_min[2])
    # muzzle: tip of the barrel
    muzzle = (0.0, gun_axis_y, gun_min[2])

    # -- 2. bake the pivots into the vertex data -----------------------------------
    translate_mesh(gltf, binary, turret_mesh, tuple(-v for v in turret_pivot))
    translate_mesh(gltf, binary, gun_mesh, tuple(-v for v in trunnion))
    translate_mesh(gltf, binary, mask_mesh, tuple(-v for v in turret_pivot))

    # -- 3. generated meshes ---------------------------------------------------------
    # The logic meshes below deliberately carry NO material.  That is the convention
    # used by every other mod in this repository (armour plates, collision boxes and
    # gun masks there are material-less as well) and it keeps them out of the
    # rendered image - a helper material would instead paint translucent slabs over
    # the hull and make the camouflage look broken.
    half = 0.03
    shoot_pos_mesh = add_box(gltf, binary, gltf["materials"], "poland_60tp_shoot_pos",
                             (-half, -half, -half), (half, half, half), None)
    hull_phys_mesh = add_box(gltf, binary, gltf["materials"], "poland_60tp_hull_phys_mesh",
                              (hull_min[0] + 0.05, hull_min[1], hull_min[2] + 0.05),
                              (hull_max[0] - 0.05, hull_max[1] + 0.05, hull_max[2] - 0.05), None)
    tracks_phys_mesh = add_box(gltf, binary, gltf["materials"], "poland_60tp_tracks_phys_mesh",
                               (min(track_l_min[0], track_r_min[0]), 0.0, min(track_l_min[2], track_r_min[2])),
                               (max(track_l_max[0], track_r_max[0]), max(track_l_max[1], track_r_max[1]),
                                max(track_l_max[2], track_r_max[2])), None)

    # armour plates: thin slabs sitting on the hull / turret skin
    plate_t = 0.04
    hull_front = add_box(gltf, binary, gltf["materials"], "poland_60tp_hull_front",
                         (hull_min[0], hull_min[1], hull_min[2] - plate_t),
                         (hull_max[0], hull_max[1], hull_min[2]), None)
    hull_side_l = add_box(gltf, binary, gltf["materials"], "poland_60tp_hull_side_l",
                           (hull_min[0] - plate_t, hull_min[1], hull_min[2]),
                           (hull_min[0], hull_max[1], hull_max[2]), None)
    hull_side_r = add_box(gltf, binary, gltf["materials"], "poland_60tp_hull_side_r",
                           (hull_max[0], hull_min[1], hull_min[2]),
                           (hull_max[0] + plate_t, hull_max[1], hull_max[2]), None)
    hull_rear = add_box(gltf, binary, gltf["materials"], "poland_60tp_hull_rear",
                        (hull_min[0], hull_min[1], hull_max[2]),
                        (hull_max[0], hull_max[1], hull_max[2] + plate_t), None)

    def turret_box(name, low, high):
        """Box given in tank space, stored in the turret frame."""
        return add_box(gltf, binary, gltf["materials"], name,
                       tuple(low[k] - turret_pivot[k] for k in range(3)),
                       tuple(high[k] - turret_pivot[k] for k in range(3)), None)

    # invisible collision volume for the gun, the same thing the reference mods call
    # <tank>_gun_mask_<mm>.  It is stored in the turret frame like the mantlet.
    gun_mask_mesh = add_box(gltf, binary, gltf["materials"], "poland_60tp_gun_mask_mesh",
                            (gun_min[0] - turret_pivot[0], gun_axis_y - 0.25 - turret_pivot[1],
                             gun_min[2] - turret_pivot[2]),
                            (gun_max[0] - turret_pivot[0], gun_axis_y + 0.25 - turret_pivot[1],
                             mask_min[2] - turret_pivot[2]), None)

    turret_front = turret_box("poland_60tp_turret_front",
                               (turret_min[0], turret_min[1], turret_min[2] - plate_t),
                               (turret_max[0], turret_max[1], turret_min[2]))
    turret_side_l = turret_box("poland_60tp_turret_side_l",
                                (turret_min[0] - plate_t, turret_min[1], turret_min[2]),
                                (turret_min[0], turret_max[1], turret_max[2]))
    turret_side_r = turret_box("poland_60tp_turret_side_r",
                                (turret_max[0], turret_min[1], turret_min[2]),
                                (turret_max[0] + plate_t, turret_max[1], turret_max[2]))
    turret_rear = turret_box("poland_60tp_turret_rear",
                              (turret_min[0], turret_min[1], turret_max[2]),
                              (turret_max[0], turret_max[1], turret_max[2] + plate_t))

    # -- 5. rebuild the node tree ----------------------------------------------------
    wheel_meshes = {}
    for side, prefix in (("L", "1_chassis_wheel_L_"), ("R", "1_chassis_wheel_R_")):
        for number in range(1, 12):
            name = "%s%02d_batch_0" % (prefix, number)
            try:
                wheel_meshes[(side, number)] = find_mesh(gltf, name)
            except KeyError:
                pass

    nodes = []

    def add_node(node):
        nodes.append(node)
        return len(nodes) - 1

    root = add_node({"name": TANK_NAME, "children": []})
    hull_vis = add_node({"name": "hull_vis", "mesh": hull_mesh, "children": []})
    hull_phys = add_node({"name": "%s_hull_phys" % TANK_NAME, "mesh": hull_phys_mesh, "children": []})
    module_tracks = add_node({"name": "%s_module_tracks_40" % TANK_NAME, "mesh": tracks_phys_mesh, "children": []})
    track_l = add_node({"name": "trackL", "mesh": track_l_mesh, "children": []})
    track_r = add_node({"name": "trackR", "mesh": track_r_mesh, "children": []})
    turret = add_node({"name": "turret01", "mesh": turret_mesh, "translation": list(turret_pivot), "children": []})
    gun = add_node({"name": "gun01", "mesh": gun_mesh,
                    "translation": [trunnion[0] - turret_pivot[0],
                                    trunnion[1] - turret_pivot[1],
                                    trunnion[2] - turret_pivot[2]],
                    "children": []})
    shoot_pos = add_node({"name": "gun01_shoot_pos", "mesh": shoot_pos_mesh,
                          "translation": [muzzle[0] - trunnion[0], muzzle[1] - trunnion[1], muzzle[2] - trunnion[2]],
                          "children": []})
    # The source mantlet (`1_gun_02_mask_batch_1`) is real, visible artwork and it is
    # what bridges the gap between the barrel and the turret front.  It must NOT be
    # called `*_gun_mask_*`: the engine reserves that name for the invisible gun
    # collision volume and never draws those nodes, which is exactly why the barrel
    # used to look cut off.  It is stored in the turret frame with an identity
    # transform, and the gun trunnion sits on its front face, so the barrel keeps
    # meeting the mantlet while the gun elevates.
    gun_mantlet = add_node({"name": "%s_gun_mantlet" % TANK_NAME, "mesh": mask_mesh, "children": []})
    gun_mask = add_node({"name": "%s_gun_mask_250" % TANK_NAME, "mesh": gun_mask_mesh, "children": []})

    for (side, number), mesh_index in sorted(wheel_meshes.items()):
        wheel = add_node({"name": "wheel%s%d" % (side, number), "mesh": mesh_index, "children": []})
        nodes[hull_vis]["children"].append(wheel)

    nodes[gun]["children"].append(shoot_pos)
    nodes[turret]["children"].extend([gun, gun_mantlet, gun_mask])

    # turret armour plates live in the turret frame
    for (suffix, _thickness), mesh_index in zip(TURRET_PLATES,
                                                (turret_front, turret_side_l, turret_side_r, turret_rear)):
        plate = add_node({"name": "%s_%s" % (TANK_NAME, suffix), "mesh": mesh_index, "children": []})
        nodes[turret]["children"].append(plate)

    nodes[root]["children"].extend([hull_phys, module_tracks, hull_vis, track_l, track_r, turret])

    # hull armour plates are separate scene roots
    hull_plate_nodes = []
    for (suffix, _thickness), mesh_index in zip(HULL_PLATES, (hull_front, hull_side_l, hull_side_r, hull_rear)):
        plate = add_node({"name": "%s_%s" % (TANK_NAME, suffix), "mesh": mesh_index, "children": []})
        hull_plate_nodes.append(plate)

    gltf["nodes"] = nodes
    gltf["scenes"] = [{"name": "Scene", "nodes": [root] + hull_plate_nodes}]

    # -- 6. keep the buffer length in sync -------------------------------------------
    gltf["buffers"][0]["byteLength"] = len(binary)

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    save_glb(DST, gltf, binary)
    print("written %s (%.1f MB)" % (DST, os.path.getsize(DST) / 1024.0 / 1024.0))
    print("  turret pivot : %s" % (tuple(round(v, 3) for v in turret_pivot),))
    print("  gun trunnion : %s" % (tuple(round(v, 3) for v in trunnion),))
    print("  muzzle       : %s" % (tuple(round(v, 3) for v in muzzle),))


if __name__ == "__main__":
    sys.exit(main())

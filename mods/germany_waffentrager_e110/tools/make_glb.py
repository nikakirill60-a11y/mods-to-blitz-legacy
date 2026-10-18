#!/usr/bin/env python3
"""Prepare the Waffentrager E 110 model for the World of Tanks Blitz mod.

The source model (tank2/Waffentrager_E110.glb) is a flat Blender export:

  * every one of the 11 nodes carries a +90 deg rotation around X, while the
    mesh data itself is stored Z-up (so the rotation is correct but it has to be
    baked, not dropped),
  * there is no hull / turret / gun hierarchy - Hull, Turret_01, gun_01_Shape and
    the two chassis meshes are all siblings,
  * the road wheels are not separate objects, they are merged into
    exportChassL1Shape / exportChassR1Shape,
  * the tracks are split into four segments (segment1_L, segment1_R, segment2,
    segment2.001) instead of one mesh per side.

Blitz expects the layout used by the other mods in this repository:

    <tank name>
    |-- <tank>_hull_phys             collision box         (not drawn)
    |-- <tank>_module_tracks_40      suspension box        (not drawn)
    |-- hull_vis                     hull
    |   |-- wheelL1..8 / wheelR1..8  road wheels
    |   `-- chassis_L / chassis_R    suspension leftovers
    |-- trackL / trackR              animated tracks
    |-- <tank>_armor_hull_*          armour plates         (not drawn)
    `-- turret01                     rotating casemate
        |-- gun01                    gun
        |   `-- gun01_shoot_pos      muzzle marker         (not drawn)
        |-- <tank>_gun_mask_250      gun collision box     (not drawn)
        `-- <tank>_armor_turret_*    armour plates         (not drawn)

Nodes whose names contain `phys`, `armor_`, `mask_` or `shoot_pos` are logic
nodes: Blitz uses them for collision / armour / muzzle logic and does not draw
them.

The +90 deg X rotation is baked into a new vertex layout, the wheels are cut out
of the chassis meshes as connected components, the track segments are merged,
the turret and gun pivots are baked into their vertex data and the collision and
armour geometry is generated.

Usage:  python3 mods/germany_waffentrager_e110/tools/make_glb.py
"""

import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank2", "Waffentrager_E110.glb")
DST = os.path.join(ROOT, "mods", "germany_waffentrager_e110", "assets", "tanks",
                   "germany_waffentrager_e110.glb")

TANK_NAME = "germany_waffentrager_e110"

# Blitzträger auf E 110: hull 80/50/20 mm, casemate 20/10/8 mm.
HULL_PLATES = [("armor_hull_80", 80.0), ("armor_hull_50", 50.0), ("armor_hull_20", 20.0)]
TURRET_PLATES = [("armor_turret_20", 20.0), ("armor_turret_10", 10.0), ("armor_turret_8", 8.0)]

GL_TRIANGLES = 4
GL_UNSIGNED_SHORT = 5123
GL_UNSIGNED_INT = 5125
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
# accessor helpers
# --------------------------------------------------------------------------------------
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
FORMATS = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}


def read_accessor(gltf, binary, index):
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"]
    size = COMPONENTS[acc["type"]]
    fmt = FORMATS[acc["componentType"]]
    values = struct.unpack_from("<%d%s" % (count * size, fmt), binary, base)
    return [values[i * size:(i + 1) * size] for i in range(count)]


def append_buffer(gltf, binary, blob):
    """Append raw bytes to the binary chunk, 4 byte aligned, returns the byte offset."""
    while len(binary) % 4:
        binary.append(0)
    offset = len(binary)
    binary.extend(blob)
    return offset


def add_accessor(gltf, binary, values, comp_type, gltf_type, target):
    if comp_type == GL_FLOAT:
        blob = struct.pack("<%df" % len(values), *values)
    elif comp_type == GL_UNSIGNED_SHORT:
        blob = struct.pack("<%dH" % len(values), *values)
    elif comp_type == GL_UNSIGNED_INT:
        blob = struct.pack("<%dI" % len(values), *values)
    else:
        raise ValueError("unsupported component type %s" % comp_type)

    offset = append_buffer(gltf, binary, blob)
    view = len(gltf["bufferViews"])
    gltf["bufferViews"].append({
        "buffer": 0, "byteLength": len(blob), "byteOffset": offset, "target": target,
    })
    acc = {"bufferView": view, "componentType": comp_type, "type": gltf_type}
    size = COMPONENTS[gltf_type]
    acc["count"] = len(values) // size
    if gltf_type == "VEC3" and comp_type == GL_FLOAT:
        acc["min"] = [min(values[i::size]) for i in range(size)]
        acc["max"] = [max(values[i::size]) for i in range(size)]
    gltf["accessors"].append(acc)
    return len(gltf["accessors"]) - 1


# --------------------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------------------
# The source data is stored Z-up and the +90 deg X rotation of the nodes turns it
# into the Y-up / -Z forward space the engine uses.  This is that rotation, baked:
#     (x, y, z)  ->  (x, -z, y)
def to_world(point):
    return (point[0], -point[2], point[1])


def mesh_geometry(gltf, binary, mesh_index):
    """Return (positions, normals, uvs, triangles, material) of a single-primitive mesh."""
    positions, normals, uvs, triangles, material, _ = mesh_primitives(gltf, binary, mesh_index)[0]
    return positions, normals, uvs, triangles, material


def mesh_primitives(gltf, binary, mesh_index):
    """Return one (positions, normals, uvs, triangles, material) tuple per primitive."""
    out = []
    for prim in gltf["meshes"][mesh_index]["primitives"]:
        attrs = prim["attributes"]
        positions = read_accessor(gltf, binary, attrs["POSITION"])
        normals = read_accessor(gltf, binary, attrs["NORMAL"]) if "NORMAL" in attrs else []
        uvs = read_accessor(gltf, binary, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else []
        indices = read_accessor(gltf, binary, prim["indices"]) if "indices" in prim else []
        triangles = [tuple(indices[k][0] for k in range(i, i + 3))
                     for i in range(0, len(indices), 3)]
        out.append((positions, normals, uvs, triangles, prim.get("material"), set()))
    return out


def add_mesh(gltf, binary, name, positions, normals, uvs, triangles, material):
    """Create a new mesh from raw vertex arrays and a triangle list."""
    return add_mesh_primitives(gltf, binary, name,
                               [(positions, normals, uvs, triangles, material)])


def add_mesh_primitives(gltf, binary, name, primitives):
    """Create one mesh out of several primitives, each with its own material."""
    prims = []
    for positions, normals, uvs, triangles, material in primitives:
        prims.append(_make_primitive(gltf, binary, positions, normals, uvs,
                                     triangles, material))
    mesh_index = len(gltf["meshes"])
    gltf["meshes"].append({"name": name, "primitives": prims})
    return mesh_index


def _make_primitive(gltf, binary, positions, normals, uvs, triangles, material):
    flat_pos = [c for p in positions for c in p]
    pos_acc = add_accessor(gltf, binary, flat_pos, GL_FLOAT, "VEC3", GL_ARRAY_BUFFER)

    attrs = {"POSITION": pos_acc}
    if normals:
        flat_nrm = [c for n in normals for c in n]
        attrs["NORMAL"] = add_accessor(gltf, binary, flat_nrm, GL_FLOAT, "VEC3", GL_ARRAY_BUFFER)
    if uvs:
        flat_uv = [c for t in uvs for c in t]
        attrs["TEXCOORD_0"] = add_accessor(gltf, binary, flat_uv, GL_FLOAT, "VEC2", GL_ARRAY_BUFFER)

    flat_idx = [i for tri in triangles for i in tri]
    if max(flat_idx) < 0xFFFF:
        idx_acc = add_accessor(gltf, binary, flat_idx, GL_UNSIGNED_SHORT, "SCALAR",
                               GL_ELEMENT_ARRAY_BUFFER)
    else:
        idx_acc = add_accessor(gltf, binary, flat_idx, GL_UNSIGNED_INT, "SCALAR",
                               GL_ELEMENT_ARRAY_BUFFER)

    prim = {"attributes": attrs, "indices": idx_acc}
    if material is not None:
        prim["material"] = material
    return prim


def connected_components(triangles, vertex_count):
    parent = list(range(vertex_count))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for tri in triangles:
        a = find(tri[0])
        for other in tri[1:]:
            b = find(other)
            if a != b:
                parent[b] = a
    groups = {}
    for tri_index, tri in enumerate(triangles):
        groups.setdefault(find(tri[0]), []).append(tri_index)
    return list(groups.values())


def sub_mesh(positions, normals, uvs, triangles, tri_subset):
    """Extract `tri_subset` into new compact vertex arrays."""
    remap = {}
    new_pos, new_nrm, new_uv, new_tris = [], [], [], []
    for tri_index in tri_subset:
        tri = triangles[tri_index]
        out = []
        for vertex in tri:
            if vertex not in remap:
                remap[vertex] = len(new_pos)
                new_pos.append(positions[vertex])
                if normals:
                    new_nrm.append(normals[vertex])
                if uvs:
                    new_uv.append(uvs[vertex])
            out.append(remap[vertex])
        new_tris.append(tuple(out))
    return new_pos, new_nrm, new_uv, new_tris


def translate_mesh(positions, offset):
    return [tuple(p[k] + offset[k] for k in range(3)) for p in positions]


def bounds(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def add_box(gltf, binary, name, low, high, material=None):
    """Create an axis aligned box mesh and return its index."""
    x0, y0, z0 = low
    x1, y1, z1 = high
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [
        ((0, 3, 2, 1), (0.0, 0.0, -1.0)),
        ((4, 5, 6, 7), (0.0, 0.0, 1.0)),
        ((0, 1, 5, 4), (0.0, -1.0, 0.0)),
        ((3, 7, 6, 2), (0.0, 1.0, 0.0)),
        ((0, 4, 7, 3), (-1.0, 0.0, 0.0)),
        ((1, 2, 6, 5), (1.0, 0.0, 0.0)),
    ]
    positions, normals, triangles = [], [], []
    for quad, normal in faces:
        start = len(positions)
        for corner in quad:
            positions.append(corners[corner])
            normals.append(normal)
        triangles.append((start, start + 1, start + 2))
        triangles.append((start, start + 2, start + 3))
    return add_mesh(gltf, binary, name, positions, normals, [], triangles, material)


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
    source_binary = binary

    # -- 1. bake the +90 deg X rotation into the vertex data -------------------------
    for node in gltf["nodes"]:
        node.pop("rotation", None)

    source = {}
    for name in ("Hull", "Turret_01", "gun_01_Shape", "exportChassL1Shape", "exportChassR1Shape",
                 "segment1_L", "segment1_R", "segment2", "segment2.001"):
        index = find_mesh(gltf, name)
        primitives = []
        for positions, normals, uvs, triangles, material, _ in mesh_primitives(gltf, source_binary, index):
            positions = [to_world(p) for p in positions]
            normals = [to_world(n) for n in normals] if normals else []
            primitives.append((positions, normals, uvs, triangles, material))
        source[name] = primitives

    hull_pos = source["Hull"][0][0]
    turret_pos = [p for prim in source["Turret_01"] for p in prim[0]]
    gun_pos = source["gun_01_Shape"][0][0]
    track_l_pos = source["segment1_L"][0][0]
    track_r_pos = source["segment1_R"][0][0]

    hull_x, hull_y, hull_z = bounds(hull_pos)
    turret_x, turret_y, turret_z = bounds(turret_pos)
    gun_x, gun_y, gun_z = bounds(gun_pos)

    # -- 2. turret / gun pivots ------------------------------------------------------
    # The casemate sits at the back of the tank and rotates on a ring; the ring is
    # the footprint of the lowest part of the turret mesh.
    ring_points = [p for p in turret_pos if p[1] < turret_y[0] + 0.08]
    ring_x, _, ring_z = bounds(ring_points)
    turret_pivot = ((ring_x[0] + ring_x[1]) / 2.0, turret_y[0], (ring_z[0] + ring_z[1]) / 2.0)

    # The barrel is the narrow slice of the gun mesh in front of the casemate.
    barrel = [p for p in gun_pos if p[2] < turret_z[0] - 0.25]
    barrel_y = bounds(barrel)[1] if barrel else gun_y
    gun_axis_y = (barrel_y[0] + barrel_y[1]) / 2.0
    trunnion = (0.0, gun_axis_y, turret_z[0])
    muzzle = (0.0, gun_axis_y, gun_z[0])

    # -- 3. rebake the pivots --------------------------------------------------------
    turret_offset = tuple(-v for v in turret_pivot)
    gun_offset = tuple(trunnion[k] - turret_pivot[k] for k in range(3))

    # gun01 is a child of turret01 and sits at the trunnion, so its own vertices must be
    # relative to the trunnion (the node translation places them in the turret frame).
    gun_local = [tuple(p[k] - trunnion[k] for k in range(3)) for p in gun_pos]

    # Only the converted geometry should end up in the file: throw away the accessors
    # and meshes that came with the source asset before writing new ones.
    materials = gltf.get("materials", [])
    gltf["accessors"] = []
    gltf["bufferViews"] = []
    gltf["meshes"] = []
    gltf["buffers"] = [{"byteLength": 0}]
    binary = bytearray()

    # -- 4. split the chassis into road wheels + leftovers ---------------------------
    wheel_defs = {}
    chassis_leftovers = {}
    for side, chassis_name in (("L", "exportChassL1Shape"), ("R", "exportChassR1Shape")):
        positions, normals, uvs, triangles, material = source[chassis_name][0]
        groups = connected_components(triangles, len(positions))

        wheels = []
        for group in groups:
            verts = {v for tri_index in group for v in triangles[tri_index]}
            points = [positions[v] for v in verts]
            (_, _), (y0, y1), (z0, z1) = bounds(points)
            # a road wheel is a large, round, ground hugging disc
            if len(verts) >= 60 and abs((y1 - y0) - (z1 - z0)) < 0.12 and y0 < 0.15:
                wheels.append(((z0 + z1) / 2.0, group))
        wheels.sort(key=lambda item: item[0])

        wheel_tris = set()
        for _, group in wheels:
            wheel_tris.update(group)
        rest = [i for i in range(len(triangles)) if i not in wheel_tris]

        wheel_defs[side] = []
        for number, (_, group) in enumerate(wheels, start=1):
            wheel_defs[side].append(sub_mesh(positions, normals, uvs, triangles, group))
        chassis_leftovers[side] = sub_mesh(positions, normals, uvs, triangles, rest) + (material,)
        print("  chassis %-18s %2d wheels, %d leftover triangles"
              % (chassis_name, len(wheels), len(rest)))

    # -- 5. merge the track segments into one mesh per side --------------------------
    def merged_track(first, second):
        a = source[first][0]
        b = source[second][0]
        pos = a[0] + b[0]
        nrm = a[1] + b[1]
        uv = a[2] + b[2]
        offset = len(a[0])
        tris = list(a[3]) + [tuple(i + offset for i in tri) for tri in b[3]]
        return pos, nrm, uv, tris, a[4]

    track_l = merged_track("segment1_L", "segment2")
    track_r = merged_track("segment1_R", "segment2.001")

    # -- 6. build the meshes ---------------------------------------------------------
    hull_mesh = add_mesh(gltf, binary, "germany_waffentrager_e110_hull",
                         *source["Hull"][0])
    # Turret_01 ships two primitives: the casemate itself and the thin lamp/antenna
    # geometry that shares the mesh; both have to survive the conversion.
    turret_mesh = add_mesh_primitives(
        gltf, binary, "germany_waffentrager_e110_turret01_mesh",
        [(translate_mesh(prim[0], turret_offset), prim[1], prim[2], prim[3], prim[4])
         for prim in source["Turret_01"]])
    gun_mesh = add_mesh(gltf, binary, "germany_waffentrager_e110_gun01_mesh",
                        gun_local, *source["gun_01_Shape"][0][1:])
    track_l_mesh = add_mesh(gltf, binary, "germany_waffentrager_e110_trackL_mesh", *track_l)
    track_r_mesh = add_mesh(gltf, binary, "germany_waffentrager_e110_trackR_mesh", *track_r)

    wheel_meshes = {}
    for side in ("L", "R"):
        wheel_meshes[side] = []
        for number, (pos, nrm, uv, tris) in enumerate(wheel_defs[side], start=1):
            wheel_meshes[side].append(add_mesh(
                gltf, binary, "germany_waffentrager_e110_wheel%s%d_mesh" % (side, number),
                pos, nrm, uv, tris, chassis_leftovers[side][4]))

    chassis_meshes = {}
    for side in ("L", "R"):
        pos, nrm, uv, tris, material = chassis_leftovers[side]
        chassis_meshes[side] = add_mesh(gltf, binary,
                                        "germany_waffentrager_e110_chassis%s_mesh" % side,
                                        pos, nrm, uv, tris, material)

    # -- 7. logic and armour geometry (deliberately without a material) -------------
    half = 0.03
    shoot_pos_mesh = add_box(gltf, binary, "germany_waffentrager_e110_shoot_pos_mesh",
                             (-half, -half, -half), (half, half, half), None)
    hull_phys_mesh = add_box(gltf, binary, "germany_waffentrager_e110_hull_phys_mesh",
                             (hull_x[0] + 0.05, hull_y[0], hull_z[0] + 0.05),
                             (hull_x[1] - 0.05, hull_y[1] + 0.05, hull_z[1] - 0.05), None)
    tracks_phys_mesh = add_box(gltf, binary, "germany_waffentrager_e110_tracks_phys_mesh",
                               (bounds(track_l_pos)[0][0], 0.0, bounds(track_l_pos)[2][0]),
                               (bounds(track_r_pos)[0][1], 1.38, bounds(track_l_pos)[2][1]), None)

    gun_mask_mesh = add_box(gltf, binary, "germany_waffentrager_e110_gun_mask_mesh",
                            (gun_x[0] - turret_pivot[0], gun_axis_y - 0.35 - turret_pivot[1],
                             gun_z[0] - turret_pivot[2]),
                            (gun_x[1] - turret_pivot[0], gun_axis_y + 0.35 - turret_pivot[1],
                             trunnion[2] - turret_pivot[2]), None)

    plate_t = 0.04
    hull_front = add_box(gltf, binary, "germany_waffentrager_e110_hull_front",
                         (hull_x[0], hull_y[0], hull_z[0] - plate_t),
                         (hull_x[1], hull_y[1], hull_z[0]), None)
    hull_side_l = add_box(gltf, binary, "germany_waffentrager_e110_hull_side_l",
                          (hull_x[0] - plate_t, hull_y[0], hull_z[0]),
                          (hull_x[0], hull_y[1], hull_z[1]), None)
    hull_side_r = add_box(gltf, binary, "germany_waffentrager_e110_hull_side_r",
                          (hull_x[1], hull_y[0], hull_z[0]),
                          (hull_x[1] + plate_t, hull_y[1], hull_z[1]), None)

    def turret_box(name, low, high):
        return add_box(gltf, binary, name,
                       tuple(low[k] - turret_pivot[k] for k in range(3)),
                       tuple(high[k] - turret_pivot[k] for k in range(3)), None)

    turret_front = turret_box("germany_waffentrager_e110_turret_front",
                              (turret_x[0], turret_y[0], turret_z[0] - plate_t),
                              (turret_x[1], turret_y[1], turret_z[0]))
    turret_side_l = turret_box("germany_waffentrager_e110_turret_side_l",
                               (turret_x[0] - plate_t, turret_y[0], turret_z[0]),
                               (turret_x[0], turret_y[1], turret_z[1]))
    turret_side_r = turret_box("germany_waffentrager_e110_turret_side_r",
                               (turret_x[1], turret_y[0], turret_z[0]),
                               (turret_x[1] + plate_t, turret_y[1], turret_z[1]))

    # -- 8. rebuild the node tree ----------------------------------------------------
    nodes = []

    def add_node(node):
        nodes.append(node)
        return len(nodes) - 1

    root = add_node({"name": TANK_NAME, "children": []})
    hull_phys = add_node({"name": "%s_hull_phys" % TANK_NAME, "mesh": hull_phys_mesh,
                          "children": []})
    module_tracks = add_node({"name": "%s_module_tracks_40" % TANK_NAME,
                              "mesh": tracks_phys_mesh, "children": []})
    hull_vis = add_node({"name": "hull_vis", "mesh": hull_mesh, "children": []})
    track_l_node = add_node({"name": "trackL", "mesh": track_l_mesh, "children": []})
    track_r_node = add_node({"name": "trackR", "mesh": track_r_mesh, "children": []})
    turret = add_node({"name": "turret01", "mesh": turret_mesh,
                       "translation": list(turret_pivot), "children": []})
    gun = add_node({"name": "gun01", "mesh": gun_mesh,
                    "translation": list(gun_offset), "children": []})
    shoot_pos = add_node({"name": "gun01_shoot_pos", "mesh": shoot_pos_mesh,
                          "translation": [muzzle[0] - trunnion[0],
                                          muzzle[1] - trunnion[1],
                                          muzzle[2] - trunnion[2]], "children": []})
    gun_mask = add_node({"name": "%s_gun_mask_250" % TANK_NAME, "mesh": gun_mask_mesh,
                         "children": []})

    nodes[gun]["children"].append(shoot_pos)
    nodes[turret]["children"].extend([gun, gun_mask])

    for side in ("L", "R"):
        chassis = add_node({"name": "chassis%s" % side, "mesh": chassis_meshes[side],
                            "children": []})
        nodes[hull_vis]["children"].append(chassis)
        for number, mesh_index in enumerate(wheel_meshes[side], start=1):
            wheel = add_node({"name": "wheel%s%d" % (side, number), "mesh": mesh_index,
                              "children": []})
            nodes[hull_vis]["children"].append(wheel)

    for (suffix, _thickness), mesh_index in zip(TURRET_PLATES,
                                                (turret_front, turret_side_l, turret_side_r)):
        plate = add_node({"name": "%s_%s" % (TANK_NAME, suffix), "mesh": mesh_index,
                          "children": []})
        nodes[turret]["children"].append(plate)

    nodes[root]["children"].extend([hull_phys, module_tracks, hull_vis, track_l_node,
                                   track_r_node, turret])

    plate_nodes = []
    for (suffix, _thickness), mesh_index in zip(HULL_PLATES,
                                                (hull_front, hull_side_l, hull_side_r)):
        plate_nodes.append(add_node({"name": "%s_%s" % (TANK_NAME, suffix),
                                     "mesh": mesh_index, "children": []}))

    gltf["nodes"] = nodes
    gltf["scenes"] = [{"name": "Scene", "nodes": [root] + plate_nodes}]

    # -- 9. keep the buffer length in sync -------------------------------------------
    gltf["buffers"][0]["byteLength"] = len(binary)

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    save_glb(DST, gltf, binary)
    print("written %s (%.1f MB)" % (DST, os.path.getsize(DST) / 1024.0 / 1024.0))
    print("  turret pivot : %s" % (tuple(round(v, 3) for v in turret_pivot),))
    print("  gun trunnion : %s" % (tuple(round(v, 3) for v in trunnion),))
    print("  muzzle       : %s" % (tuple(round(v, 3) for v in muzzle),))
    print("  wheels       : L%d R%d" % (len(wheel_meshes["L"]), len(wheel_meshes["R"])))


if __name__ == "__main__":
    sys.exit(main())

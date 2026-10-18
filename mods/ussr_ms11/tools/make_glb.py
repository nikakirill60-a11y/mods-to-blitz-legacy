#!/usr/bin/env python3
"""Prepare the MS-11 (МС-11) model for the World of Tanks Blitz mod.

The source model (tank4/MS-11.glb) was exported from Blender (glTF I/O 5.1) and,
unlike the LV-1300, it keeps the original UV layout and the original materials
with their embedded textures.  Compared to what the engine needs it has these
problems:

  * every node carries a stray -90 deg yaw.  The vertex data itself is already
    Y-up with the gun pointing to -Z (the barrel spans z = -4.62 .. -1.40), the
    same as the 60TP source, so the rotation is simply dropped,
  * there is no hull / turret / gun hierarchy - the seven meshes are flat
    siblings, so the turret ring and the gun trunnion are baked into the
    vertex data and the nodes are placed at those pivots,
  * the running gear of each side is merged into one mesh (wheel_L / wheel_R):
    drive sprocket, 4 road wheels, 3 return rollers and the idler, each with a
    separate hub cap.  They are split by welding the positions, then every hub
    is attached to the wheel whose side-view footprint contains it,
  * the mantlet is part of the turret mesh (the mesh is called
    1_gun_01_mask_nc).  It stays visible artwork of turret01; only the
    generated invisible box is called *_gun_mask_*.

Texture orientation: the embedded MS-11_BC image is an exact vertical flip of
tank4/MS-11_BC.dx11_4.png (checked per pixel, same for the track texture), and
the original UVs address the embedded image.  make_textures.py therefore ships
the embedded images - the same rule that fixed the 60TP texture.

Output layout (same as the other mods in this repository):

    ussr_ms11
    |-- ussr_ms11_hull_phys               collision box        (not drawn)
    |-- ussr_ms11_module_tracks_40        suspension box       (not drawn)
    |-- hull_vis                          hull + rear "bucket"
    |   `-- wheelL1..9 / wheelR1..9       running gear, pivot = wheel centre
    |-- trackL / trackR                   tracks (original UVs)
    `-- turret01                          turret + mantlet, pivot = turret ring
        |-- gun01                         barrel, pivot = rear end of the barrel
        |   `-- gun01_shoot_pos           muzzle marker        (not drawn)
        |-- ussr_ms11_gun_mask_250        mantlet collision    (not drawn)
        `-- ussr_ms11_armor_turret_*      armour plates        (not drawn)
    ussr_ms11_armor_hull_*                armour plates        (not drawn)

Usage:  python3 mods/ussr_ms11/tools/make_glb.py
"""

import json
import math
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank4", "MS-11.glb")
DST = os.path.join(ROOT, "mods", "ussr_ms11", "assets", "tanks", "ussr_ms11.glb")

TANK_NAME = "ussr_ms11"

# Armour (mm) - read by the engine from the node name, so every plate node needs a
# unique name; left + right side plates share one node with two boxes.
#   hull: front 80-170 (wiki), sides 60-70, rear 50-60, rear bucket 200 (spaced)
#   turret: not published - estimated for a tier X medium
HULL_PLATES = [("armor_hull_120", 120.0), ("armor_hull_70", 70.0),
               ("armor_hull_60", 60.0), ("armor_hull_200", 200.0)]
TURRET_PLATES = [("armor_turret_200", 200.0), ("armor_turret_90", 90.0),
                 ("armor_turret_60", 60.0)]

WHEEL_MARGIN = 0.05        # metres, see find_wheels()

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
    magic, _version, length = struct.unpack("<III", data[:12])
    if magic != 0x46546C67:
        raise ValueError("not a glb file: %s" % path)
    offset, chunks = 12, []
    while offset < length:
        chunk_len, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        chunks.append((chunk_type, data[offset + 8:offset + 8 + chunk_len]))
        offset += 8 + chunk_len
    gltf = json.loads(chunks[0][1].decode("utf-8"))
    binary = chunks[1][1] if len(chunks) > 1 else b""
    return gltf, bytes(binary)


def save_glb(path, gltf, binary):
    json_chunk = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    bin_chunk = bytes(binary) + b"\x00" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk)
        fh.write(struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk)


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


def source_mesh(gltf, binary, node_name):
    """Return (positions, normals, triangles) of the single primitive of a node."""
    for node in gltf["nodes"]:
        if node.get("name") == node_name:
            if any(k in node for k in ("rotation", "scale", "matrix")) or \
                    any(abs(v) > 1e-9 for v in node.get("translation", [0, 0, 0])):
                raise ValueError("unexpected transform on %s" % node_name)
            prims = gltf["meshes"][node["mesh"]]["primitives"]
            if len(prims) != 1:
                raise ValueError("%s: expected 1 primitive, got %d" % (node_name, len(prims)))
            attrs = prims[0]["attributes"]
            positions = read_accessor(gltf, binary, attrs["POSITION"])
            normals = read_accessor(gltf, binary, attrs["NORMAL"])
            flat = [i[0] for i in read_accessor(gltf, binary, prims[0]["indices"])]
            triangles = [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]
            return positions, normals, triangles
    raise KeyError(node_name)


# --------------------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------------------
class Writer:
    def __init__(self):
        self.gltf = {"asset": {"version": "2.0", "generator": "mods-to-blitz-legacy make_glb.py"},
                     "scene": 0, "scenes": [], "nodes": [], "meshes": [], "accessors": [],
                     "bufferViews": [], "buffers": [{"byteLength": 0}]}
        self.binary = bytearray()

    def accessor(self, values, comp_type, gltf_type, target):
        fmt = {GL_FLOAT: "f", GL_UNSIGNED_SHORT: "H", GL_UNSIGNED_INT: "I"}[comp_type]
        blob = struct.pack("<%d%s" % (len(values), fmt), *values)
        while len(self.binary) % 4:
            self.binary.append(0)
        offset = len(self.binary)
        self.binary.extend(blob)
        self.gltf["bufferViews"].append({"buffer": 0, "byteOffset": offset,
                                         "byteLength": len(blob), "target": target})
        size = COMPONENTS[gltf_type]
        acc = {"bufferView": len(self.gltf["bufferViews"]) - 1, "componentType": comp_type,
               "type": gltf_type, "count": len(values) // size}
        if comp_type == GL_FLOAT:
            acc["min"] = [min(values[i::size]) for i in range(size)]
            acc["max"] = [max(values[i::size]) for i in range(size)]
        self.gltf["accessors"].append(acc)
        return len(self.gltf["accessors"]) - 1

    def mesh(self, name, positions, normals, uvs, triangles):
        attrs = {"POSITION": self.accessor([c for p in positions for c in p], GL_FLOAT, "VEC3",
                                           GL_ARRAY_BUFFER)}
        if normals:
            attrs["NORMAL"] = self.accessor([c for n in normals for c in n], GL_FLOAT, "VEC3",
                                            GL_ARRAY_BUFFER)
        if uvs:
            attrs["TEXCOORD_0"] = self.accessor([c for t in uvs for c in t], GL_FLOAT, "VEC2",
                                                GL_ARRAY_BUFFER)
        flat = [i for tri in triangles for i in tri]
        comp = GL_UNSIGNED_SHORT if max(flat) < 0xFFFF else GL_UNSIGNED_INT
        idx = self.accessor(flat, comp, "SCALAR", GL_ELEMENT_ARRAY_BUFFER)
        self.gltf["meshes"].append({"name": name,
                                    "primitives": [{"attributes": attrs, "indices": idx}]})
        return len(self.gltf["meshes"]) - 1

    def box(self, name, low, high):
        return self.boxes(name, [(low, high)])

    def boxes(self, name, boxes):
        positions, normals, triangles = [], [], []
        for low, high in boxes:
            self._box_geometry(low, high, positions, normals, triangles)
        return self.mesh(name, positions, normals, [], triangles)

    @staticmethod
    def _box_geometry(low, high, positions, normals, triangles):
        x0, y0, z0 = low
        x1, y1, z1 = high
        corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                   (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        faces = [((0, 3, 2, 1), (0.0, 0.0, -1.0)), ((4, 5, 6, 7), (0.0, 0.0, 1.0)),
                 ((0, 1, 5, 4), (0.0, -1.0, 0.0)), ((3, 7, 6, 2), (0.0, 1.0, 0.0)),
                 ((0, 4, 7, 3), (-1.0, 0.0, 0.0)), ((1, 2, 6, 5), (1.0, 0.0, 0.0))]
        for quad, normal in faces:
            start = len(positions)
            for corner in quad:
                positions.append(corners[corner])
                normals.append(normal)
            triangles += [(start, start + 1, start + 2), (start, start + 2, start + 3)]

    def node(self, name, mesh=None, translation=None):
        node = {"name": name, "children": []}
        if mesh is not None:
            node["mesh"] = mesh
        if translation is not None:
            node["translation"] = [float(v) for v in translation]
        self.gltf["nodes"].append(node)
        return len(self.gltf["nodes"]) - 1

    def save(self, path):
        self.gltf["buffers"][0]["byteLength"] = len(self.binary)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_glb(path, self.gltf, self.binary)


# --------------------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------------------
def bounds(points):
    return tuple((min(p[k] for p in points), max(p[k] for p in points)) for k in range(3))


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def welded_pieces(positions, triangles, tol=1e-4):
    """Group triangles into pieces that are connected once equal positions are welded."""
    key_of = {}
    weld = []
    for p in positions:
        key = (round(p[0] / tol), round(p[1] / tol), round(p[2] / tol))
        weld.append(key_of.setdefault(key, len(key_of)))
    parent = list(range(len(key_of)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for tri in triangles:
        a = find(weld[tri[0]])
        for v in tri[1:]:
            b = find(weld[v])
            if a != b:
                parent[b] = a
    pieces = {}
    for t, tri in enumerate(triangles):
        pieces.setdefault(find(weld[tri[0]]), []).append(t)
    return list(pieces.values())


def unshare(positions, normals, triangles, tri_subset, offset=(0.0, 0.0, 0.0)):
    """Give every triangle its own three vertices (needed for per-face UVs)."""
    pos, nrm, tris = [], [], []
    for t in tri_subset:
        start = len(pos)
        for v in triangles[t]:
            pos.append(sub(positions[v], offset))
            nrm.append(normals[v])
        tris.append((start, start + 1, start + 2))
    return pos, nrm, tris


# --------------------------------------------------------------------------------------
# MS-11 specific helpers
# --------------------------------------------------------------------------------------
def source_part(gltf, binary, node_name):
    """Return (positions, normals, uvs, triangles, material) of a node's primitive.

    The node rotation is the stray -90 deg yaw described above and is ignored on
    purpose; any other transform would be unexpected.
    """
    for node in gltf["nodes"]:
        if node.get("name") != node_name:
            continue
        rot = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
        if abs(rot[1] + math.sqrt(0.5)) > 1e-3 or abs(rot[3] - math.sqrt(0.5)) > 1e-3 or \
                "scale" in node or "matrix" in node or \
                any(abs(v) > 1e-9 for v in node.get("translation", [0, 0, 0])):
            raise ValueError("unexpected transform on %s" % node_name)
        prims = gltf["meshes"][node["mesh"]]["primitives"]
        if len(prims) != 1:
            raise ValueError("%s: expected 1 primitive, got %d" % (node_name, len(prims)))
        attrs = prims[0]["attributes"]
        positions = read_accessor(gltf, binary, attrs["POSITION"])
        normals = read_accessor(gltf, binary, attrs["NORMAL"])
        uvs = read_accessor(gltf, binary, attrs["TEXCOORD_0"])
        flat = [i[0] for i in read_accessor(gltf, binary, prims[0]["indices"])]
        triangles = [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]
        return positions, normals, uvs, triangles, prims[0]["material"]
    raise KeyError(node_name)


def subset(part, tri_subset, offset=(0.0, 0.0, 0.0)):
    """Copy the given triangles (with their vertices) and move them by -offset."""
    positions, normals, uvs, triangles, _ = part
    remap, pos, nrm, uv, tris = {}, [], [], [], []
    for t in tri_subset:
        new = []
        for v in triangles[t]:
            if v not in remap:
                remap[v] = len(pos)
                pos.append(sub(positions[v], offset))
                nrm.append(normals[v])
                uv.append(uvs[v])
            new.append(remap[v])
        tris.append(tuple(new))
    return pos, nrm, uv, tris


def copy_materials(w, gltf, binary):
    """Carry the original materials and their embedded images over unchanged."""
    images = []
    for image in gltf.get("images", []):
        view = gltf["bufferViews"][image["bufferView"]]
        blob = binary[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
        while len(w.binary) % 4:
            w.binary.append(0)
        w.gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(w.binary), "byteLength": len(blob)})
        w.binary.extend(blob)
        images.append({"name": image["name"], "mimeType": image["mimeType"],
                       "bufferView": len(w.gltf["bufferViews"]) - 1})
    w.gltf["images"] = images
    w.gltf["samplers"] = gltf.get("samplers", [])
    w.gltf["textures"] = gltf.get("textures", [])
    w.gltf["materials"] = gltf.get("materials", [])


def add_mesh(w, name, part_geometry, material):
    index = w.mesh(name, *part_geometry)
    w.gltf["meshes"][index]["primitives"][0]["material"] = material
    return index


def find_wheels(positions, triangles):
    """Split a merged running-gear mesh; returns [(centre, [tri indices])] front to back.

    The biggest welded pieces are the wheels themselves (sprocket, road wheels,
    return rollers, idler).  The hub caps are separate small pieces lying inside
    the side-view footprint of their wheel.  The return rollers are ~0.35 m clear
    of the road wheels, so a 5 cm margin never confuses the two.
    """
    pieces = []
    for tris in welded_pieces(positions, triangles):
        pts = [positions[v] for t in tris for v in triangles[t]]
        b = bounds(pts)
        pieces.append({"tris": tris, "b": b})
    pieces.sort(key=lambda p: (-(p["b"][1][1] - p["b"][1][0]) * (p["b"][2][1] - p["b"][2][0]),
                               p["b"][2][0]))
    wheels = []
    for piece in pieces:
        b = piece["b"]
        cy = (b[1][0] + b[1][1]) / 2.0
        cz = (b[2][0] + b[2][1]) / 2.0
        owner = None
        for wheel in wheels:
            wb = wheel["b"]
            if wb[1][0] - WHEEL_MARGIN <= cy <= wb[1][1] + WHEEL_MARGIN and \
                    wb[2][0] - WHEEL_MARGIN <= cz <= wb[2][1] + WHEEL_MARGIN:
                owner = wheel
                break
        if owner is None:
            centre = ((b[0][0] + b[0][1]) / 2.0, cy, cz)
            wheels.append({"tris": list(piece["tris"]), "b": b, "centre": centre})
        else:
            owner["tris"] += piece["tris"]
    out = [(wheel["centre"], sorted(wheel["tris"])) for wheel in wheels]
    out.sort(key=lambda w: w[0][2])
    return out


# --------------------------------------------------------------------------------------
# main conversion
# --------------------------------------------------------------------------------------
def main():
    gltf, binary = load_glb(SRC)
    hull = source_part(gltf, binary, "1_hull_batch_1")
    turret = source_part(gltf, binary, "1_turret_01_batch_1")
    gun = source_part(gltf, binary, "1_gun_02_batch_1")
    track = {"L": source_part(gltf, binary, "1_chassis_track_L_batch_1"),
             "R": source_part(gltf, binary, "1_chassis_track_R_batch_1")}
    wheel_src = {"L": source_part(gltf, binary, "wheel_L"),
                 "R": source_part(gltf, binary, "wheel_R")}

    hull_b = bounds(hull[0])
    turret_b = bounds(turret[0])
    gun_b = bounds(gun[0])

    # -- pivots -----------------------------------------------------------------------
    # Turret ring: the turret base sits on the hull roof at its lowest point; the ring
    # is the centre of the footprint of the bottom 7 cm of the turret.
    roof = turret_b[1][0]
    base = [p for p in turret[0] if p[1] < roof + 0.07]
    base_b = bounds(base)
    turret_pivot = ((base_b[0][0] + base_b[0][1]) / 2.0, roof, (base_b[2][0] + base_b[2][1]) / 2.0)

    # Gun: a plain barrel whose rear end sits flush in the mantlet hole.  Elevating it
    # around that rear end keeps it seated in the (turret-fixed) mantlet.
    gun_axis_x = (gun_b[0][0] + gun_b[0][1]) / 2.0
    gun_axis_y = (gun_b[1][0] + gun_b[1][1]) / 2.0
    trunnion = (gun_axis_x, gun_axis_y, gun_b[2][1])
    muzzle = (gun_axis_x, gun_axis_y, gun_b[2][0])

    # Mantlet: the part of the turret mesh in front of the turret face.
    turret_face_z = base_b[2][0]
    mantlet = [p for p in turret[0] if p[2] < turret_face_z - 0.01]
    mantlet_b = bounds(mantlet)

    # Turret body height without the antenna masts: the tallest wide piece.
    body_top = roof
    for tris in welded_pieces(turret[0], turret[3]):
        b = bounds([turret[0][v] for t in tris for v in turret[3][t]])
        if b[0][1] - b[0][0] > 0.4 and b[2][1] - b[2][0] > 0.4:
            body_top = max(body_top, b[1][1])

    w = Writer()
    copy_materials(w, gltf, binary)

    # -- visible meshes (original UVs and materials) ------------------------------------
    hull_mesh = add_mesh(w, "%s_hull" % TANK_NAME, subset(hull, range(len(hull[3]))), hull[4])
    turret_mesh = add_mesh(w, "%s_turret01_mesh" % TANK_NAME,
                           subset(turret, range(len(turret[3])), turret_pivot), turret[4])
    gun_mesh = add_mesh(w, "%s_gun01_mesh" % TANK_NAME,
                        subset(gun, range(len(gun[3])), trunnion), gun[4])
    track_meshes = {s: add_mesh(w, "%s_track%s_mesh" % (TANK_NAME, s),
                                subset(track[s], range(len(track[s][3]))), track[s][4])
                    for s in ("L", "R")}

    wheel_nodes = {}
    for side in ("L", "R"):
        part = wheel_src[side]
        wheel_nodes[side] = []
        for number, (centre, wtris) in enumerate(find_wheels(part[0], part[3]), start=1):
            mesh = add_mesh(w, "%s_wheel%s%d_mesh" % (TANK_NAME, side, number),
                            subset(part, wtris, centre), part[4])
            wheel_nodes[side].append((centre, mesh, len(wtris)))

    # -- logic geometry (no material, no UVs) -----------------------------------------
    half = 0.03
    shoot_pos_mesh = w.box("%s_shoot_pos_mesh" % TANK_NAME, (-half,) * 3, (half,) * 3)
    hx, hy, hz = hull_b
    # the hull body ends where the rear bucket starts (behind the tracks)
    track_b = {s: bounds(track[s][0]) for s in ("L", "R")}
    body_rear = max(track_b["L"][2][1], track_b["R"][2][1])
    bucket = bounds([p for p in hull[0] if p[2] > body_rear + 0.2])
    hull_phys_mesh = w.box("%s_hull_phys_mesh" % TANK_NAME,
                           (hx[0] + 0.25, hy[0], hz[0] + 0.25),
                           (hx[1] - 0.25, roof, body_rear))
    tracks_phys_mesh = w.box("%s_tracks_phys_mesh" % TANK_NAME,
                             (track_b["L"][0][0], 0.0, min(track_b["L"][2][0], track_b["R"][2][0])),
                             (track_b["R"][0][1], max(track_b["L"][1][1], track_b["R"][1][1]),
                              max(track_b["L"][2][1], track_b["R"][2][1])))
    gun_mask_mesh = w.box("%s_gun_mask_mesh" % TANK_NAME,
                          sub((mantlet_b[0][0], mantlet_b[1][0], mantlet_b[2][0]), turret_pivot),
                          sub((mantlet_b[0][1], mantlet_b[1][1], turret_face_z), turret_pivot))

    t = 0.04
    hull_plates = [
        w.box("%s_hull_front" % TANK_NAME, (hx[0], hy[0], hz[0] - t), (hx[1], roof, hz[0])),
        w.boxes("%s_hull_sides" % TANK_NAME,
                [((hx[0] - t, hy[0], hz[0]), (hx[0], roof, body_rear)),
                 ((hx[1], hy[0], hz[0]), (hx[1] + t, roof, body_rear))]),
        w.box("%s_hull_rear" % TANK_NAME, (hx[0], hy[0], body_rear), (hx[1], roof, body_rear + t)),
        w.box("%s_hull_bucket" % TANK_NAME, (bucket[0][0], bucket[1][0], bucket[2][1] - t),
              (bucket[0][1], bucket[1][1], bucket[2][1])),
    ]
    tx = turret_b[0]
    ty = (roof, body_top)
    tz = (turret_face_z, turret_b[2][1])

    def local(low, high):
        return (sub(low, turret_pivot), sub(high, turret_pivot))

    turret_plates = [
        w.boxes("%s_turret_front" % TANK_NAME, [local((tx[0], ty[0], tz[0] - t), (tx[1], ty[1], tz[0]))]),
        w.boxes("%s_turret_sides" % TANK_NAME,
                [local((tx[0] - t, ty[0], tz[0]), (tx[0], ty[1], tz[1])),
                 local((tx[1], ty[0], tz[0]), (tx[1] + t, ty[1], tz[1]))]),
        w.boxes("%s_turret_rear" % TANK_NAME, [local((tx[0], ty[0], tz[1]), (tx[1], ty[1], tz[1] + t))]),
    ]

    # -- node tree ------------------------------------------------------------------------
    nodes = w.gltf["nodes"]
    root = w.node(TANK_NAME)
    hull_phys = w.node("%s_hull_phys" % TANK_NAME, hull_phys_mesh)
    module_tracks = w.node("%s_module_tracks_40" % TANK_NAME, tracks_phys_mesh)
    hull_vis = w.node("hull_vis", hull_mesh)
    track_l = w.node("trackL", track_meshes["L"])
    track_r = w.node("trackR", track_meshes["R"])
    turret_node = w.node("turret01", turret_mesh, turret_pivot)
    gun_node = w.node("gun01", gun_mesh, sub(trunnion, turret_pivot))
    shoot_pos = w.node("gun01_shoot_pos", shoot_pos_mesh, sub(muzzle, trunnion))
    gun_mask = w.node("%s_gun_mask_250" % TANK_NAME, gun_mask_mesh)

    nodes[gun_node]["children"].append(shoot_pos)
    nodes[turret_node]["children"] += [gun_node, gun_mask]
    for (suffix, _), mesh in zip(TURRET_PLATES, turret_plates):
        nodes[turret_node]["children"].append(w.node("%s_%s" % (TANK_NAME, suffix), mesh))
    for side in ("L", "R"):
        for number, (centre, mesh, _) in enumerate(wheel_nodes[side], start=1):
            nodes[hull_vis]["children"].append(w.node("wheel%s%d" % (side, number), mesh, centre))
    nodes[root]["children"] += [hull_phys, module_tracks, hull_vis, track_l, track_r, turret_node]
    plate_nodes = [w.node("%s_%s" % (TANK_NAME, suffix), mesh)
                   for (suffix, _), mesh in zip(HULL_PLATES, hull_plates)]
    w.gltf["scenes"] = [{"name": "Scene", "nodes": [root] + plate_nodes}]

    w.save(DST)
    print("written %s (%.2f MB)" % (DST, os.path.getsize(DST) / 1048576.0))
    print("  turret pivot : %s" % (tuple(round(v, 3) for v in turret_pivot),))
    print("  gun trunnion : %s" % (tuple(round(v, 3) for v in trunnion),))
    print("  muzzle       : %s" % (tuple(round(v, 3) for v in muzzle),))
    print("  mantlet      : z %.3f .. %.3f, turret face z %.3f, turret body top y %.3f"
          % (mantlet_b[2][0], mantlet_b[2][1], turret_face_z, body_top))
    print("  rear bucket  : z %.3f .. %.3f (hull body ends at z %.3f)" % (bucket[2][0], bucket[2][1], body_rear))
    for side in ("L", "R"):
        print("  wheels %s     : %d  (triangles %s)" % (side, len(wheel_nodes[side]),
              " ".join("%d" % n for _, _, n in wheel_nodes[side])))
        for number, (centre, _, _) in enumerate(wheel_nodes[side], start=1):
            print("      wheel%s%-2d centre %s" % (side, number, tuple(round(v, 3) for v in centre)))


if __name__ == "__main__":
    sys.exit(main())

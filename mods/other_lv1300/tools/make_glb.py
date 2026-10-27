#!/usr/bin/env python3
"""Prepare the LV-1300 Uranus (ЛВ-1300 Уран) model for the World of Tanks Blitz mod.

The source model (tank3/lv1300.glb) was pulled out of the game client with a
Blender plugin and re-exported.  Compared to what the engine needs it has three
problems:

  * it has NO texture coordinates and no materials at all - only POSITION and
    NORMAL.  The original UV layout of Oth46_LV1300.dx11.png cannot be recovered
    from the file, so this script generates new UVs (see "UV layout" below) and
    make_textures.py paints a matching texture,
  * there is no hull / turret / gun hierarchy - the seven meshes are flat
    siblings,
  * the road wheels of each side are merged into a single mesh (wheel_L /
    wheel_R) and the triangles are not connected to each other (every hard edge
    splits the vertices), so the wheels have to be found by welding positions
    first and then grouping the welded pieces by their footprint.

Output layout (same as the other mods in this repository, wheel nodes are placed
at the wheel centre like in the original _279 / Maus models):

    other_lv1300
    |-- other_lv1300_hull_phys            collision box        (not drawn)
    |-- other_lv1300_module_tracks_40     suspension box       (not drawn)
    |-- hull_vis                          hull
    |   `-- wheelL1..N / wheelR1..N       road wheels, pivot = wheel centre
    |-- trackL / trackR                   tracks (U across, V along the belt)
    `-- turret01                          turret, pivot = turret ring centre
        |-- gun01                         gun, pivot = gun breech
        |   `-- gun01_shoot_pos           muzzle marker        (not drawn)
        |-- other_lv1300_gun_mask_250     gun collision box    (not drawn)
        `-- other_lv1300_armor_turret_*   armour plates        (not drawn)
    other_lv1300_armor_hull_*             armour plates        (not drawn)

UV layout of the generated body texture (other_lv1300.png, 2048 x 2048):

    +-----------+-----------+   u 0.0 - 0.5 : hull       (blue armour)
    |           |  turret   |   u 0.5 - 1.0, v 0.0 - 0.5 : turret (blue armour)
    |   hull    |           |   u 0.5 - 1.0, v 0.5 - 0.75: gun    (gun metal)
    |           +-----------+   u 0.5 - 1.0, v 0.75- 1.0 : wheels (dark steel)
    |           |   gun     |
    |           +-----------+
    |           |  wheels   |
    +-----------+-----------+

Every triangle is projected along the dominant axis of its face normal (box
projection) into the rectangle of its part, at a fixed metres-per-pixel scale,
so no texture is stretched or repeated across a seam.  The tracks get their own
UVs for the scrolling track texture: U runs across the belt (0..1), V runs
along the belt with one texture repeat per track link.

Usage:  python3 mods/other_lv1300/tools/make_glb.py
"""

import json
import math
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank3", "lv1300.glb")
DST = os.path.join(ROOT, "mods", "other_lv1300", "assets", "tanks", "other_lv1300.glb")

TANK_NAME = "other_lv1300"

# ЛВ-1300 Уран: "almost unpenetrable" turret front, very weak sides (see README).
# The thickness is read from the node name, so every plate node needs a unique name:
# the left and right side plates share one node (a mesh with two boxes) instead of
# two nodes called the same (Godot would rename the second one on import).
HULL_PLATES = [("armor_hull_200", 200.0), ("armor_hull_60", 60.0)]
TURRET_PLATES = [("armor_turret_400", 400.0), ("armor_turret_90", 90.0)]

WHEEL_MARGIN = 0.12        # metres, see find_wheels()
TRACK_LINK = 0.18          # metres of belt per repeat of the track texture

# UV rectangles (u0, v0, u1, v1) of every part inside the body texture.
REGION_HULL = (0.0, 0.0, 0.5, 1.0)
REGION_TURRET = (0.5, 0.0, 1.0, 0.5)
REGION_GUN = (0.5, 0.5, 1.0, 0.75)
REGION_WHEEL = (0.5, 0.75, 1.0, 1.0)
UV_MARGIN = 4.0 / 2048.0   # keep away from the region borders (mip-map bleeding)

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


def box_uvs(positions, triangles, region, origin, scale):
    """Box-project each triangle along the dominant axis of its face normal.

    `origin` is the (x, y, z) that lands on the region's top-left corner and
    `scale` is UV units per metre.  X-facing triangles use (z, y), Y-facing ones
    (x, z) and Z-facing ones (x, y); y always grows downwards in the image.
    """
    u0, v0, u1, v1 = region
    uvs = [None] * len(positions)
    for tri in triangles:
        a, b, c = (positions[v] for v in tri)
        n = cross(sub(b, a), sub(c, a))
        axis = max(range(3), key=lambda k: abs(n[k]))
        for v in tri:
            p = positions[v]
            if axis == 0:
                s, t = p[2] - origin[2], origin[1] - p[1]
            elif axis == 1:
                s, t = p[0] - origin[0], p[2] - origin[2]
            else:
                s, t = p[0] - origin[0], origin[1] - p[1]
            u = min(max(u0 + UV_MARGIN + s * scale, u0 + UV_MARGIN), u1 - UV_MARGIN)
            w = min(max(v0 + UV_MARGIN + t * scale, v0 + UV_MARGIN), v1 - UV_MARGIN)
            uvs[v] = (u, w)
    return uvs


def fit_scale(region, extent_s, extent_t):
    """UV units per metre so that a part of the given extent fits its region."""
    width = region[2] - region[0] - 2 * UV_MARGIN
    height = region[3] - region[1] - 2 * UV_MARGIN
    return min(width / extent_s, height / extent_t)


def body_part_uvs(positions, triangles, region):
    """Box UVs for a whole part, with one scale that fits every projection."""
    (x0, x1), (y0, y1), (z0, z1) = bounds(positions)
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    # largest s/t extents over the three projections: (z,y), (x,z), (x,y)
    extent_s = max(dz, dx)
    extent_t = max(dy, dz)
    scale = fit_scale(region, extent_s, extent_t)
    return box_uvs(positions, triangles, region, (x0, y1, z0), scale), scale


def track_uvs(positions, triangles):
    """U across the belt, V = distance along the belt / TRACK_LINK."""
    (x0, x1), _, _ = bounds(positions)
    # belt outline in the side view (z, y): convex hull of the vertices
    pts = sorted(set((round(p[2], 5), round(p[1], 5)) for p in positions))

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    loop = lower[:-1] + upper[:-1]
    cumulative = [0.0]
    for i in range(len(loop)):
        a, b = loop[i], loop[(i + 1) % len(loop)]
        cumulative.append(cumulative[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    perimeter = cumulative[-1]
    links = max(1, round(perimeter / TRACK_LINK))

    def along(z, y):
        best, best_d = 0.0, None
        for i in range(len(loop)):
            a, b = loop[i], loop[(i + 1) % len(loop)]
            ab = (b[0] - a[0], b[1] - a[1])
            length2 = ab[0] ** 2 + ab[1] ** 2 or 1e-12
            k = max(0.0, min(1.0, ((z - a[0]) * ab[0] + (y - a[1]) * ab[1]) / length2))
            q = (a[0] + ab[0] * k, a[1] + ab[1] * k)
            d = (q[0] - z) ** 2 + (q[1] - y) ** 2
            if best_d is None or d < best_d:
                best_d = d
                best = cumulative[i] + k * math.sqrt(length2)
        return best / perimeter * links          # integer number of repeats per loop

    uvs = [None] * len(positions)
    for tri in triangles:
        vs = [along(positions[v][2], positions[v][1]) for v in tri]
        # a triangle that straddles the start of the loop: unwrap it
        if max(vs) - min(vs) > links / 2.0:
            vs = [v + links if v < links / 2.0 else v for v in vs]
        for v, value in zip(tri, vs):
            u = (positions[v][0] - x0) / (x1 - x0)
            uvs[v] = (u, value)
    return uvs, perimeter, links


# --------------------------------------------------------------------------------------
# main conversion
# --------------------------------------------------------------------------------------
def find_wheels(positions, triangles):
    """Split a merged wheel mesh into wheels; returns [(centre, [tri indices])]."""
    pieces = []
    for tris in welded_pieces(positions, triangles):
        pts = [positions[v] for t in tris for v in triangles[t]]
        (_, _), (y0, y1), (z0, z1) = bounds(pts)
        pieces.append({"tris": tris, "y": (y0, y1), "z": (z0, z1)})
    # the biggest pieces are the wheel discs; everything else (hubs, bolts, the
    # other half of the tyre) belongs to the disc whose side-view footprint
    # contains its centre
    pieces.sort(key=lambda p: -((p["y"][1] - p["y"][0]) * (p["z"][1] - p["z"][0])))
    wheels = []
    for piece in pieces:
        cy = (piece["y"][0] + piece["y"][1]) / 2.0
        cz = (piece["z"][0] + piece["z"][1]) / 2.0
        owner, owner_d = None, None
        for wheel in wheels:
            # the drive sprocket teeth stick out ~5 cm past the sprocket disc, the return
            # rollers sit ~10 cm clear of the road wheels: a 12 cm margin separates them
            if wheel["y"][0] - WHEEL_MARGIN <= cy <= wheel["y"][1] + WHEEL_MARGIN and \
                    wheel["z"][0] - WHEEL_MARGIN <= cz <= wheel["z"][1] + WHEEL_MARGIN:
                d = (cy - wheel["centre"][1]) ** 2 + (cz - wheel["centre"][2]) ** 2
                if owner is None or d < owner_d:
                    owner, owner_d = wheel, d
        if owner is None:
            pts = [positions[v] for t in piece["tris"] for v in triangles[t]]
            (x0, x1), _, _ = bounds(pts)
            # the pivot is the centre of the disc itself, not of everything attached to it
            centre = ((x0 + x1) / 2.0, cy, cz)
            wheels.append({"tris": list(piece["tris"]), "y": piece["y"], "z": piece["z"],
                           "centre": centre})
        else:
            owner["tris"] += piece["tris"]
    out = [(wheel["centre"], wheel["tris"]) for wheel in wheels]
    out.sort(key=lambda w: w[0][2])
    return out


def main():
    gltf, binary = load_glb(SRC)
    hull = source_mesh(gltf, binary, "1_hull_batch_1")
    turret = source_mesh(gltf, binary, "1_turret_01_batch_1")
    gun = source_mesh(gltf, binary, "1_gun_02_batch_1")
    track = {"L": source_mesh(gltf, binary, "1_chassis_track_L_batch_1"),
             "R": source_mesh(gltf, binary, "1_chassis_track_R_batch_1")}
    wheel_src = {"L": source_mesh(gltf, binary, "wheel_L"),
                 "R": source_mesh(gltf, binary, "wheel_R")}

    hull_b = bounds(hull[0])
    turret_b = bounds(turret[0])
    gun_b = bounds(gun[0])

    # -- pivots -----------------------------------------------------------------------
    # The turret ring is the circular base of the turret mesh: the pieces whose lowest
    # point sits on the hull roof.  Their footprint is a 2.54 m circle.
    # (A thin antenna mast of the turret mesh reaches down to y = 0.61 inside the hull,
    # so the lowest turret vertex is NOT the ring.)
    wide = []
    for tris in welded_pieces(turret[0], turret[2]):
        b = bounds([turret[0][v] for t in tris for v in turret[2][t]])
        if b[0][1] - b[0][0] > 2.0 and b[2][1] - b[2][0] > 2.0:
            wide.append(b)
    if not wide:
        raise RuntimeError("turret ring not found")
    ring_y = min(b[1][0] for b in wide)
    base_pieces = [b for b in wide if b[1][0] < ring_y + 0.01]
    roof = ring_y
    ring_x = (min(b[0][0] for b in base_pieces) + max(b[0][1] for b in base_pieces)) / 2.0
    ring_z = (min(b[2][0] for b in base_pieces) + max(b[2][1] for b in base_pieces)) / 2.0
    turret_pivot = (ring_x, ring_y, ring_z)

    # The gun is a plain barrel: elevate it around its rear end on the barrel axis.
    barrel = [p for p in gun[0] if p[2] < gun_b[2][1] - 0.5 and p[2] > gun_b[2][0] + 0.6]
    gun_axis_y = (min(p[1] for p in barrel) + max(p[1] for p in barrel)) / 2.0
    gun_axis_x = (min(p[0] for p in barrel) + max(p[0] for p in barrel)) / 2.0
    trunnion = (gun_axis_x, gun_axis_y, gun_b[2][1])
    muzzle = (gun_axis_x, gun_axis_y, gun_b[2][0])

    w = Writer()

    # -- visible meshes -----------------------------------------------------------------
    pos, nrm, tris = unshare(*hull, range(len(hull[2])))
    uv, hull_scale = body_part_uvs(pos, tris, REGION_HULL)
    hull_mesh = w.mesh("%s_hull" % TANK_NAME, pos, nrm, uv, tris)

    pos, nrm, tris = unshare(*turret, range(len(turret[2])))
    uv, turret_scale = body_part_uvs(pos, tris, REGION_TURRET)
    pos = [sub(p, turret_pivot) for p in pos]
    turret_mesh = w.mesh("%s_turret01_mesh" % TANK_NAME, pos, nrm, uv, tris)

    pos, nrm, tris = unshare(*gun, range(len(gun[2])))
    uv, gun_scale = body_part_uvs(pos, tris, REGION_GUN)
    pos = [sub(p, trunnion) for p in pos]
    gun_mesh = w.mesh("%s_gun01_mesh" % TANK_NAME, pos, nrm, uv, tris)

    track_meshes = {}
    track_info = {}
    for side in ("L", "R"):
        pos, nrm, tris = unshare(*track[side], range(len(track[side][2])))
        uv, perimeter, links = track_uvs(pos, tris)
        track_meshes[side] = w.mesh("%s_track%s_mesh" % (TANK_NAME, side), pos, nrm, uv, tris)
        track_info[side] = (perimeter, links)

    wheel_nodes = {}
    for side in ("L", "R"):
        positions, normals, triangles = wheel_src[side]
        wheels = find_wheels(positions, triangles)
        wheel_nodes[side] = []
        for number, (centre, wtris) in enumerate(wheels, start=1):
            pos, nrm, tris = unshare(positions, normals, triangles, wtris, offset=centre)
            # every wheel uses the same patch of dark steel, projected around its centre
            (x0, x1), (y0, y1), (z0, z1) = bounds(pos)
            scale = fit_scale(REGION_WHEEL, max(z1 - z0, x1 - x0), max(y1 - y0, z1 - z0))
            uv = box_uvs(pos, tris, REGION_WHEEL, (x0, y1, z0), scale)
            mesh = w.mesh("%s_wheel%s%d_mesh" % (TANK_NAME, side, number), pos, nrm, uv, tris)
            wheel_nodes[side].append((centre, mesh, len(tris)))

    # -- logic geometry (no material, no UVs) -----------------------------------------
    half = 0.03
    shoot_pos_mesh = w.box("%s_shoot_pos_mesh" % TANK_NAME, (-half,) * 3, (half,) * 3)
    hull_phys_mesh = w.box("%s_hull_phys_mesh" % TANK_NAME,
                           (hull_b[0][0] + 0.25, hull_b[1][0], hull_b[2][0] + 0.25),
                           (hull_b[0][1] - 0.25, hull_b[1][1], hull_b[2][1] - 0.1))
    track_b = {s: bounds(track[s][0]) for s in ("L", "R")}
    tracks_phys_mesh = w.box("%s_tracks_phys_mesh" % TANK_NAME,
                             (track_b["L"][0][0], 0.0, min(track_b["L"][2][0], track_b["R"][2][0])),
                             (track_b["R"][0][1], max(track_b["L"][1][1], track_b["R"][1][1]),
                              max(track_b["L"][2][1], track_b["R"][2][1])))
    gun_mask_mesh = w.box("%s_gun_mask_mesh" % TANK_NAME,
                          (gun_b[0][0] - turret_pivot[0], gun_axis_y - 0.3 - turret_pivot[1],
                           gun_b[2][0] - turret_pivot[2]),
                          (gun_b[0][1] - turret_pivot[0], gun_axis_y + 0.3 - turret_pivot[1],
                           turret_b[2][0] - turret_pivot[2]))

    t = 0.04
    hx, hy, hz = hull_b
    hull_plates = [
        w.box("%s_hull_front" % TANK_NAME, (hx[0], hy[0], hz[0] - t), (hx[1], hy[1], hz[0])),
        w.boxes("%s_hull_sides" % TANK_NAME,
                [((hx[0] - t, hy[0], hz[0]), (hx[0], hy[1], hz[1])),
                 ((hx[1], hy[0], hz[0]), (hx[1] + t, hy[1], hz[1]))]),
    ]
    # the turret plates cover the turret body only, not the rear rack
    tx = turret_b[0]
    ty = (roof, turret_b[1][1])
    tz = (turret_b[2][0], min(turret_b[2][1], turret_pivot[2] + 1.9))

    def local(low, high):
        return (sub(low, turret_pivot), sub(high, turret_pivot))

    turret_plates = [
        w.boxes("%s_turret_front" % TANK_NAME,
                [local((tx[0], ty[0], tz[0] - t), (tx[1], ty[1], tz[0]))]),
        w.boxes("%s_turret_sides" % TANK_NAME,
                [local((tx[0] - t, ty[0], tz[0]), (tx[0], ty[1], tz[1])),
                 local((tx[1], ty[0], tz[0]), (tx[1] + t, ty[1], tz[1]))]),
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
    print("  texel scale  : hull %.1f, turret %.1f, gun %.1f px/m (2048 px texture)"
          % (hull_scale * 2048, turret_scale * 2048, gun_scale * 2048))
    for side in ("L", "R"):
        print("  track%s       : belt %.2f m, %d links" % ((side,) + track_info[side]))
        print("  wheels %s     : %d  %s" % (side, len(wheel_nodes[side]),
              " ".join("%d" % n for _, _, n in wheel_nodes[side])))
        for number, (centre, _, _) in enumerate(wheel_nodes[side], start=1):
            print("      wheel%s%-2d centre %s" % (side, number, tuple(round(v, 3) for v in centre)))


if __name__ == "__main__":
    sys.exit(main())

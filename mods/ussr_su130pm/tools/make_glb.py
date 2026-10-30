"""Prepare the SU-130PM (СУ-130ПМ) model for the World of Tanks Blitz mod.

The source model (tank5/CU-130PM.glb) is a Blender glTF export (glTF I/O 5.1)
that keeps the original UV layout and two materials:

    material 0  SU_130PM_mtr        hull, turret, gun, running gear
    material 1  SU_130PM_track_mtr  both tracks

Compared to what the engine needs it has these problems:

  * the whole vehicle is turned 180 degrees around the vertical axis - the
    barrel points to +Z, while every other tank in this repository faces -Z
    (60TP, MS-11, LV-1300, the four reference mods).  The rotation is baked into
    the vertex data, so the UVs stay exactly as the author laid them out,
  * there is no hull / turret / gun hierarchy: the seven meshes are flat
    siblings, so the turret ring and the gun trunnion are baked into the vertex
    data; the nodes are placed at those pivots instead,
  * the running gear of each side is merged into one mesh (wheel_L / wheel_R):
    front idler, six road wheels, three return rollers and the rear sprocket,
    each with a separate hub cap.  They are split by welding the positions and
    attaching every hub to the wheel whose side-view footprint contains it -
    11 wheels per side, pivot in the middle of each wheel so it can spin,
  * the game does not read the glTF materials; it takes the textures from the
    .tres (`tex_main`, `tex_tracks`), so the materials only keep the split
    between body and tracks.

The UV layout is the original one, so the original 2048x2048 atlas
tank5/R159_SU_130PM.dx11.dds can be used directly - see make_textures.py, which
only has to flip it vertically (Direct3D textures are stored bottom-up, glTF
UVs are not).  The tracks tile their strip about ten times around the belt, so
the separate track texture painted by make_textures.py holds eight links per
repeat.

Output layout (logic boxes are not drawn):

    ussr_su130pm
    |-- ussr_su130pm_hull_phys            collision box
    |-- ussr_su130pm_module_tracks_40     suspension box
    |-- hull_vis                          hull
    |   `-- wheelL1..11 / wheelR1..11     running gear
    |-- trackL / trackR                   tracks (original UVs)
    `-- turret01                          open-top fighting compartment
        |-- gun01                         130 mm M-65S, pivot at the breech
        |   `-- gun01_shoot_pos           muzzle marker
        |-- ussr_su130pm_gun_mask_20      gun collision box
        `-- ussr_su130pm_armor_turret_*   armour plates
    ussr_su130pm_armor_hull_*             armour plates

Pivots are measured from the GLB itself: the turret pivot is the centre of the
two thin floor plates of the turret mesh, the gun trunnion sits on the barrel
axis at the centre of the breech block.  As a cross-check, the same points
correspond to HP_turretJoint / HP_gunJoint of the decompiled WoT PC model:
  https://github.com/izeberg/wot-src/blob/master/sources/res/vehicles/russian/R159_SU_130PM/normal/lod0/Hull.visual_processed
  https://github.com/izeberg/wot-src/blob/master/sources/res/vehicles/russian/R159_SU_130PM/normal/lod0/Turret_01.visual_processed

Usage:  python3 mods/ussr_su130pm/tools/make_glb.py
"""


import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "tank5", "CU-130PM.glb")
DST = os.path.join(ROOT, "mods", "ussr_su130pm", "assets", "tanks", "ussr_su130pm.glb")

TANK_NAME = "ussr_su130pm"

# Armour (mm), read by the engine from the node name.  SU-130PM is a thin-skinned
# tier VIII TD: hull 30/25/20, open turret 15/6/6 (Blitz Hangar, WoT Blitz Wiki).
# Left and right side plates share one node so Godot does not rename duplicates.
HULL_PLATES = [("armor_hull_30", 30.0), ("armor_hull_25", 25.0)]
TURRET_PLATES = [("armor_turret_15", 15.0), ("armor_turret_6", 6.0)]

WHEEL_MARGIN = 0.12        # metres, see find_wheels()

# Source mesh name -> material in the output (0 = body, 1 = tracks).
MATERIAL_BODY = 0
MATERIAL_TRACK = 1
MATERIALS = [{"name": "ussr_su130pm_body_mtr",
              "doubleSided": True,
              "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                                       "metallicFactor": 0.0, "roughnessFactor": 0.5}},
             {"name": "ussr_su130pm_track_mtr",
              "doubleSided": True,
              "alphaMode": "MASK",
              "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                                       "metallicFactor": 0.0, "roughnessFactor": 0.5}}]

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
    """Return (positions, normals, uvs, triangles) of the single primitive of a node."""
    for node in gltf["nodes"]:
        if node.get("name") == node_name:
            if any(k in node for k in ("rotation", "scale", "matrix")) or \
                    any(abs(v) > 1e-9 for v in node.get("translation", [0, 0, 0])):
                raise ValueError("unexpected transform on %s" % node_name)
            prims = gltf["meshes"][node["mesh"]]["primitives"]
            if len(prims) != 1:
                raise ValueError("%s: expected 1 primitive, got %d" % (node_name, len(prims)))
            attrs = prims[0]["attributes"]
            for attribute in ("POSITION", "NORMAL", "TEXCOORD_0"):
                if attribute not in attrs:
                    raise ValueError("%s: missing %s" % (node_name, attribute))
            positions = read_accessor(gltf, binary, attrs["POSITION"])
            normals = read_accessor(gltf, binary, attrs["NORMAL"])
            uvs = read_accessor(gltf, binary, attrs["TEXCOORD_0"])
            flat = [i[0] for i in read_accessor(gltf, binary, prims[0]["indices"])]
            triangles = [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]
            return positions, normals, uvs, triangles
    raise KeyError(node_name)


# --------------------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------------------
class Writer:
    def __init__(self):
        self.gltf = {"asset": {"version": "2.0", "generator": "mods-to-blitz-legacy make_glb.py"},
                     "scene": 0, "scenes": [], "nodes": [], "meshes": [], "accessors": [],
                     "bufferViews": [], "buffers": [{"byteLength": 0}],
                     "materials": [dict(material) for material in MATERIALS]}
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

    def mesh(self, name, positions, normals, uvs, triangles, material=None):
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
        primitive = {"attributes": attrs, "indices": idx}
        if material is not None:
            primitive["material"] = material
        self.gltf["meshes"].append({"name": name, "primitives": [primitive]})
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


def flipped(mesh):
    """Bake the -180 deg yaw: barrel from +Z to -Z, left side from +X to -X.

    A yaw keeps the winding of the triangles, so the faces stay outward and the
    UVs are untouched.
    """
    positions, normals, uvs, triangles = mesh
    positions = [(-p[0], p[1], -p[2]) for p in positions]
    normals = [(-n[0], n[1], -n[2]) for n in normals]
    return positions, normals, uvs, triangles


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


def submesh(positions, normals, uvs, triangles, tri_subset, offset=(0.0, 0.0, 0.0)):
    """Copy the given triangles, dropping unused vertices and moving them by `offset`."""
    remap = {}
    pos, nrm, uv, tris = [], [], [], []
    for t in tri_subset:
        triangle = []
        for v in triangles[t]:
            if v not in remap:
                remap[v] = len(pos)
                pos.append(sub(positions[v], offset))
                nrm.append(normals[v])
                uv.append(uvs[v])
            triangle.append(remap[v])
        tris.append(tuple(triangle))
    return pos, nrm, uv, tris


# --------------------------------------------------------------------------------------
# main conversion
# --------------------------------------------------------------------------------------
def find_wheels(positions, triangles):
    """Split a merged wheel mesh into wheels; returns [(centre, [tri indices])].

    Some SU-130PM wheel discs are exported as two thin half-discs plus separate
    hub caps.  The pivot is therefore computed from *all* the triangles assigned
    to a wheel, not from the first piece: otherwise it would sit on one face of
    the wheel instead of on its axle.
    """
    pieces = []
    for tris in welded_pieces(positions, triangles):
        pts = [positions[v] for t in tris for v in triangles[t]]
        (_, _), (y0, y1), (z0, z1) = bounds(pts)
        pieces.append({"tris": tris, "y": (y0, y1), "z": (z0, z1)})
    # The biggest pieces are the wheel discs; hubs, bolts and the other half of
    # the tyre belong to the disc whose side-view footprint contains their centre.
    pieces.sort(key=lambda p: -((p["y"][1] - p["y"][0]) * (p["z"][1] - p["z"][0])))
    wheels = []
    for piece in pieces:
        cy = (piece["y"][0] + piece["y"][1]) / 2.0
        cz = (piece["z"][0] + piece["z"][1]) / 2.0
        owner, owner_d = None, None
        for wheel in wheels:
            if wheel["y"][0] - WHEEL_MARGIN <= cy <= wheel["y"][1] + WHEEL_MARGIN and \
                    wheel["z"][0] - WHEEL_MARGIN <= cz <= wheel["z"][1] + WHEEL_MARGIN:
                d = (cy - wheel["centre"][1]) ** 2 + (cz - wheel["centre"][2]) ** 2
                if owner is None or d < owner_d:
                    owner, owner_d = wheel, d
        if owner is None:
            wheels.append({"tris": list(piece["tris"]), "y": piece["y"], "z": piece["z"],
                           "centre": (0.0, cy, cz)})
        else:
            owner["tris"] += piece["tris"]
    out = []
    for wheel in wheels:
        pts = [positions[v] for t in wheel["tris"] for v in triangles[t]]
        centre = tuple((lo + hi) / 2.0 for lo, hi in bounds(pts))
        out.append((centre, wheel["tris"]))
    out.sort(key=lambda w: w[0][2])
    return out


def main():
    gltf, binary = load_glb(SRC)
    hull = flipped(source_mesh(gltf, binary, "1_hull_batch_1"))
    turret = flipped(source_mesh(gltf, binary, "1_turret_01_batch_1"))
    gun = flipped(source_mesh(gltf, binary, "1_gun_02_batch_1"))
    track = {"L": flipped(source_mesh(gltf, binary, "1_chassis_track_L_batch_1")),
             "R": flipped(source_mesh(gltf, binary, "1_chassis_track_R_batch_1"))}
    wheel_src = {"L": flipped(source_mesh(gltf, binary, "wheel_L")),
                 "R": flipped(source_mesh(gltf, binary, "wheel_R"))}

    hull_b = bounds(hull[0])
    turret_b = bounds(turret[0])
    gun_b = bounds(gun[0])

    # -- pivots -----------------------------------------------------------------------
    # The turret ring is the pair of thin floor plates of the turret mesh.  They
    # are ~1.0 x 1.0 m, only 8 cm thick and lie a few centimetres above the
    # lowest vertex of the mesh (the hand rails reach a little lower).  Their
    # centre is the pivot.
    base = []
    for tris in welded_pieces(turret[0], turret[3]):
        b = bounds([turret[0][v] for t in tris for v in turret[3][t]])
        if (b[1][0] < turret_b[1][0] + 0.06 and b[1][1] - b[1][0] < 0.12 and
                b[0][1] - b[0][0] > 0.8 and b[2][1] - b[2][0] > 0.8):
            base.append(b)
    if not base:
        raise RuntimeError("turret floor plates not found")
    roof = min(b[1][0] for b in base)
    turret_pivot = ((min(b[0][0] for b in base) + max(b[0][1] for b in base)) / 2.0,
                    roof,
                    (min(b[2][0] for b in base) + max(b[2][1] for b in base)) / 2.0)

    # Barrel axis: the long tube in front of z = -1.0 m.  Its centre line is the
    # horizontal axis of the gun; the trunnion sits at the centre of the breech
    # block (the welded piece around z = 0 that is as tall as the mantlet).
    barrel = [p for p in gun[0] if p[2] < -1.0]
    if not barrel:
        raise RuntimeError("barrel not found")
    gun_axis_y = (min(p[1] for p in barrel) + max(p[1] for p in barrel)) / 2.0
    gun_axis_x = (min(p[0] for p in barrel) + max(p[0] for p in barrel)) / 2.0
    breech = []
    for tris in welded_pieces(gun[0], gun[3]):
        b = bounds([gun[0][v] for t in tris for v in gun[3][t]])
        if (b[2][0] > -0.2 and b[2][1] < 0.8 and
                b[1][1] - b[1][0] > 0.5 and b[0][1] - b[0][0] < 0.6):
            breech.append((len(tris), b))
    trunnion_z = ((lambda b: (b[2][0] + b[2][1]) / 2.0)(max(breech, key=lambda x: x[0])[1])
                  if breech else 0.30)
    trunnion = (gun_axis_x, gun_axis_y, trunnion_z)
    muzzle = (gun_axis_x, gun_axis_y, gun_b[2][0])

    w = Writer()

    # -- visible meshes -----------------------------------------------------------------
    pos, nrm, uv, tris = submesh(*hull, range(len(hull[3])))
    hull_mesh = w.mesh("%s_hull" % TANK_NAME, pos, nrm, uv, tris, MATERIAL_BODY)

    pos, nrm, uv, tris = submesh(*turret, range(len(turret[3])), offset=turret_pivot)
    turret_mesh = w.mesh("%s_turret01_mesh" % TANK_NAME, pos, nrm, uv, tris, MATERIAL_BODY)

    pos, nrm, uv, tris = submesh(*gun, range(len(gun[3])), offset=trunnion)
    gun_mesh = w.mesh("%s_gun01_mesh" % TANK_NAME, pos, nrm, uv, tris, MATERIAL_BODY)

    track_meshes = {}
    for side in ("L", "R"):
        pos, nrm, uv, tris = submesh(*track[side], range(len(track[side][3])))
        track_meshes[side] = w.mesh("%s_track%s_mesh" % (TANK_NAME, side),
                                    pos, nrm, uv, tris, MATERIAL_TRACK)

    wheel_nodes = {}
    for side in ("L", "R"):
        positions, normals, uvs, triangles = wheel_src[side]
        wheels = find_wheels(positions, triangles)
        wheel_nodes[side] = []
        for number, (centre, wtris) in enumerate(wheels, start=1):
            pos, nrm, uv, tris = submesh(positions, normals, uvs, triangles, wtris, offset=centre)
            mesh = w.mesh("%s_wheel%s%d_mesh" % (TANK_NAME, side, number),
                          pos, nrm, uv, tris, MATERIAL_BODY)
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
    gun_mask = w.node("%s_gun_mask_20" % TANK_NAME, gun_mask_mesh)

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
    print("  turret pivot : %s (floor plates)" % (tuple(round(v, 3) for v in turret_pivot),))
    print("  gun trunnion : %s (breech block)" % (tuple(round(v, 3) for v in trunnion),))
    print("  muzzle       : %s" % (tuple(round(v, 3) for v in muzzle),))
    for side in ("L", "R"):
        print("  wheels %s     : %d  %s" % (side, len(wheel_nodes[side]),
              " ".join("%d" % n for _, _, n in wheel_nodes[side])))
        for number, (centre, _, _) in enumerate(wheel_nodes[side], start=1):
            print("      wheel%s%-2d centre %s" % (side, number, tuple(round(v, 3) for v in centre)))


if __name__ == "__main__":
    sys.exit(main())

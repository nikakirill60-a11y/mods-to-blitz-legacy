"""Generate a tiny synthetic WoT Blitz model in the *modern* layout:

    fixtures/demo_tank.sc2.dvpl   SFV2 v43, version tags, descriptor, one KA v2 body
    fixtures/demo_tank.scg.dvpl   SCPG v1 with KA v1 PolygonGroups
    fixtures/images/demo_tank.dx11.dds(.dvpl)   64x64 texture (uncompressed RGBA)
    fixtures/images/demo_track.dx11.dds.dvpl    track texture with alpha holes

It exercises everything the viewer must handle: hierarchy with a rotated
turret (quaternion), LOD 0/1 batches, a "-1 = every LOD" helper, an HP_ hard
point, a shadow-volume batch, an alphatest material with properties stored as
bytes, parent material inheritance, uint16 + uint32 indices and DVPL LZ4.
No game assets are used, so the files can live in the repository.

    python tools/scgviewer/tests/make_fixture.py
"""
from __future__ import annotations

import math
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")

# ---------------------------------------------------------------- KA writers
T_BOOL, T_INT32, T_FLOAT, T_STRING, T_BYTES, T_UINT32, T_KA = 1, 2, 3, 4, 6, 7, 8
T_INT64, T_UINT64, T_V3, T_V4, T_M4, T_AABB, T_ARRAY = 9, 10, 12, 13, 16, 19, 27


class V:
    """Typed value wrapper."""

    def __init__(self, t, v):
        self.t, self.v = t, v


def i32(v): return V(T_INT32, v)
def u32(v): return V(T_UINT32, v)
def u64(v): return V(T_UINT64, v)
def f32(v): return V(T_FLOAT, v)
def v3(*a): return V(T_V3, a)
def v4(*a): return V(T_V4, a)
def raw(b): return V(T_BYTES, b)


def auto(v):
    if isinstance(v, V):
        return v
    if isinstance(v, bool):
        return V(T_BOOL, v)
    if isinstance(v, int):
        return V(T_INT32, v)
    if isinstance(v, float):
        return V(T_FLOAT, v)
    if isinstance(v, str):
        return V(T_STRING, v)
    if isinstance(v, (bytes, bytearray)):
        return V(T_BYTES, bytes(v))
    if isinstance(v, dict):
        return V(T_KA, v)
    if isinstance(v, list):
        return V(T_ARRAY, v)
    raise TypeError(v)


def enc_value(val: V, table) -> bytes:
    t, v = val.t, val.v
    head = bytes([t])
    if t == T_BOOL:
        return head + bytes([1 if v else 0])
    if t == T_INT32:
        return head + struct.pack("<i", v)
    if t == T_UINT32:
        return head + struct.pack("<I", v)
    if t == T_FLOAT:
        return head + struct.pack("<f", v)
    if t == T_UINT64:
        return head + struct.pack("<Q", v)
    if t == T_STRING:
        if table is not None:
            return head + struct.pack("<I", table.id(v))
        b = v.encode()
        return head + struct.pack("<I", len(b)) + b
    if t == T_BYTES:
        return head + struct.pack("<I", len(v)) + v
    if t in (T_V3, T_V4):
        return head + struct.pack(f"<{len(v)}f", *v)
    if t == T_KA:
        body = enc_ka(v, table, child=table is not None)
        return head + struct.pack("<I", len(body)) + body
    if t == T_ARRAY:
        return head + struct.pack("<I", len(v)) + b"".join(enc_value(auto(x), table) for x in v)
    raise TypeError(t)


class Table:
    def __init__(self):
        self.ids: dict[str, int] = {}

    def id(self, s):
        if s not in self.ids:
            self.ids[s] = len(self.ids) * 7 + 3   # ids are not indices in real files either
        return self.ids[s]


def enc_ka(d: dict, table=None, child=False) -> bytes:
    if table is None:  # v1
        out = b"KA" + struct.pack("<HI", 1, len(d))
        for k, v in d.items():
            out += enc_value(V(T_STRING, k), None) + enc_value(auto(v), None)
        return out
    if not d and child:
        return b"KA" + struct.pack("<H", 0xFF02)
    pairs = b"".join(struct.pack("<I", table.id(k)) + enc_value(auto(v), table) for k, v in d.items())
    return b"KA" + struct.pack("<HI", 0x0102, len(d)) + pairs


def enc_ka2_root(d: dict) -> bytes:
    table = Table()
    # encode children first so the table is complete
    pairs = b"".join(struct.pack("<I", table.id(k)) + enc_value(auto(v), table) for k, v in d.items())
    names = list(table.ids)
    out = b"KA" + struct.pack("<HI", 2, len(names))
    for s in names:
        b = s.encode()
        out += struct.pack("<H", len(b)) + b
    out += b"".join(struct.pack("<I", table.ids[s]) for s in names)
    out += struct.pack("<I", len(d)) + pairs
    return out


# ---------------------------------------------------------------- DVPL (LZ4 literals-only block)
def lz4_literals(data: bytes) -> bytes:
    n = len(data)
    if n < 15:
        return bytes([n << 4]) + data
    out = bytearray([0xF0])
    rest = n - 15
    while rest >= 255:
        out.append(255)
        rest -= 255
    out.append(rest)
    return bytes(out) + data


def dvpl(data: bytes, compress=True) -> bytes:
    payload = lz4_literals(data) if compress else data
    return payload + struct.pack("<IIII", len(data), len(payload), zlib.crc32(payload) & 0xFFFFFFFF,
                                 2 if compress else 0) + b"DVPL"


# ---------------------------------------------------------------- geometry
FMT_FULL = 1 | 2 | 8 | 128 | 256     # pos normal uv0 tangent binormal = 56 bytes (game standard)
FMT_SHADOW = 1 | 2                   # pos normal = 24 bytes


def box(cx, cy, cz, sx, sy, sz, u0=0.0, u1=1.0):
    """Axis aligned box, 24 verts, CCW outward faces."""
    verts, idx = [], []
    faces = [((1, 0, 0), (0, 1, 0), (0, 0, 1)), ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),
             ((0, 1, 0), (-1, 0, 0), (0, 0, 1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
             ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (1, 0, 0), (0, -1, 0))]
    for n, t, b in faces:
        base = len(verts)
        c = [cx + n[0] * sx / 2, cy + n[1] * sy / 2, cz + n[2] * sz / 2]
        ht = [t[i] * (sx, sy, sz)[i] / 2 for i in range(3)]
        hb = [b[i] * (sx, sy, sz)[i] / 2 for i in range(3)]
        for (a, bb), uv in zip(((-1, -1), (1, -1), (1, 1), (-1, 1)), ((0, 1), (1, 1), (1, 0), (0, 0))):
            p = [c[i] + a * ht[i] + bb * hb[i] for i in range(3)]
            verts.append((p, n, (u0 + uv[0] * (u1 - u0), uv[1]), t, b))
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return verts, idx


def cylinder(cy0, cy1, r, cx=0.0, cz=0.0, seg=16):
    """Cylinder along +Y (a gun barrel)."""
    verts, idx = [], []
    for i in range(seg + 1):
        a = 2 * math.pi * i / seg
        n = (math.cos(a), 0.0, math.sin(a))
        for y, v in ((cy0, 1.0), (cy1, 0.0)):
            verts.append(((cx + r * n[0], y, cz + r * n[2]), n, (i / seg, v), (0, 1, 0), (0, 0, 1)))
    for i in range(seg):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        idx += [a, c, b, b, c, d]
    return verts, idx


def merge(*parts):
    verts, idx = [], []
    for v, i in parts:
        base = len(verts)
        verts += v
        idx += [x + base for x in i]
    return verts, idx


def polygon_group(pid, verts, idx, fmt=FMT_FULL, wide=False):
    buf = bytearray()
    for p, n, uv, t, b in verts:
        buf += struct.pack("<3f", *p)
        if fmt & 2:
            buf += struct.pack("<3f", *n)
        if fmt & 8:
            buf += struct.pack("<2f", *uv)
        if fmt & 128:
            buf += struct.pack("<3f", *t)
        if fmt & 256:
            buf += struct.pack("<3f", *b)
    ib = struct.pack(f"<{len(idx)}{'I' if wide else 'H'}", *idx)
    return {
        "##name": "PolygonGroup", "#id": raw(struct.pack("<Q", pid)),
        "cubeTextureCoordCount": 0, "indexCount": len(idx), "indexFormat": 1 if wide else 0,
        "indices": raw(ib), "packing": 0, "primitiveCount": len(idx) // 3, "rhi_primitiveType": 1,
        "textureCoordCount": 1 if fmt & 8 else 0, "vertexCount": len(verts), "vertexFormat": fmt,
        "vertices": raw(bytes(buf)),
    }


# ---------------------------------------------------------------- scene
def quat_z(deg):
    a = math.radians(deg) / 2
    return (0.0, 0.0, math.sin(a), math.cos(a))


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def rotate(q, v):
    x, y, z, w = q
    # DAVA row-vector convention: v' = v * M(q), M from Quaternion::GetMatrix
    m = [[1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
         [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
         [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]]
    return tuple(sum(v[i] * m[i][j] for i in range(3)) for j in range(3))


def entity(name, eid, t=(0, 0, 0), q=(0, 0, 0, 1), parent_world=None, batches=None, children=(), lod=True):
    pw_t, pw_q = parent_world or ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
    # world = local * parent (row vectors)
    wt = tuple(a + b for a, b in zip(rotate(pw_q, t), pw_t))
    wq = quat_mul(pw_q, q)  # q applied first, then parent rotation
    comps = [{
        "comp.typename": "TransformComponent",
        "tc.localRotation": v4(*q), "tc.localScale": v3(1, 1, 1), "tc.localTranslation": v3(*t),
        "tc.worldRotation": v4(*wq), "tc.worldScale": v3(1, 1, 1), "tc.worldTranslation": v3(*wt),
    }]
    if batches:
        ro = {"##name": "Mesh", "ro.batchCount": i32(len(batches)), "ro.flags": u32(1), "ro.debugflags": u32(0)}
        bd = {}
        for i, (ds, mat, lod_i) in enumerate(batches):
            bd[f"{i:04d}"] = {"##name": "RenderBatch", "rb.classname": "RenderBatch",
                              "rb.datasource": u64(ds), "rb.nmatname": u64(mat), "rb.sortingKey": u32(248)}
            ro[f"rb{i}.lodIndex"] = i32(lod_i)
            ro[f"rb{i}.switchIndex"] = i32(-1)
        ro["ro.batches"] = bd
        comps.append({"comp.typename": "RenderComponent", "rc.renderObj": ro})
        if lod:
            comps.append({"comp.typename": "LodComponent", "lc.loddist": {"distance0": f32(0.0), "distance1": f32(60.0)}})
    cd = {"count": i32(len(comps))}
    for i, c in enumerate(comps):
        cd[f"{i:04d}"] = c
    e = {"##name": "Entity", "components": cd, "flags": u32(0), "id": u32(eid), "name": name}
    kids = [c((wt, wq)) for c in children]
    if kids:
        e["#hierarchy"] = kids
    return e


def material(mid, name, parent=None, fx=None, textures=None, props=None, config=False):
    m = {"##name": "NMaterial", "#id": raw(struct.pack("<Q", mid)), "materialName": name,
         "qualityGroup": "Tank"}
    if parent is not None:
        m["parentMaterialKey"] = u64(parent)
    body = {}
    if fx:
        body["fxName"] = fx
    if textures:
        body["textures"] = textures
    if props:
        body["properties"] = props
    if config:  # modern files keep textures inside configArchive_N
        m["configCount"] = i32(1)
        m["configArchive_0"] = dict(configName="Default", **body)
    else:
        m.update(body)
    return m


def float_prop(v):
    # property blob: u8 type, u32 count, float[count]  (as stored by the game)
    return raw(bytes([0]) + struct.pack("<If", 1, v))


def dds_rgba(w, h, pixel):
    head = bytearray(128)
    head[0:4] = b"DDS "
    struct.pack_into("<IIIIIII", head, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000, h, w, w * 4, 0, 1)
    struct.pack_into("<II", head, 76, 32, 0x41)            # pixel format size, RGB|ALPHAPIXELS
    struct.pack_into("<IIIIII", head, 84, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    struct.pack_into("<I", head, 108, 0x1000)
    px = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixel(x, y)
            px += bytes([b, g, r, a])
    return bytes(head) + bytes(px)


def build():
    os.makedirs(os.path.join(OUT, "images"), exist_ok=True)
    # --- geometry (ids look like real ones: big numbers)
    hull0 = merge(box(0, 0, 0.75, 3.0, 6.0, 1.0), box(0, 2.6, 0.5, 2.6, 1.4, 0.6, 0.0, 0.5))
    hull1 = box(0, 0, 0.75, 3.0, 6.0, 1.0)
    turret = box(0, 0, 0.35, 2.0, 2.4, 0.7, 0.5, 1.0)
    gun = cylinder(0.0, 3.5, 0.09)
    track = merge(box(-1.65, 0, 0.45, 0.4, 6.2, 0.9), box(1.65, 0, 0.45, 0.4, 6.2, 0.9))
    shadow = box(0, 0, 0.75, 3.0, 6.0, 1.0)
    hp = box(0, 0, 0, 0.05, 0.05, 0.05)
    groups = [
        polygon_group(123456789001, *hull0),
        polygon_group(123456789002, *hull1),
        polygon_group(123456789003, *turret),
        polygon_group(123456789004, *gun, wide=True),
        polygon_group(123456789005, *track),
        polygon_group(123456789006, *shadow, fmt=FMT_SHADOW),
        polygon_group(123456789007, *hp),
    ]
    scg = b"SCPG" + struct.pack("<III", 1, len(groups), len(groups)) + b"".join(enc_ka(g) for g in groups)

    # --- materials: instances -> parents (textures live in the parent)
    mats = [
        material(10, "demo_tank_mtr", fx="~res:/Materials/NormalizedBlinnPhongAllQualities.Opaque.material",
                 textures={"albedo": "images/demo_tank.tex"}, config=True),
        material(11, "demo_track_mtr", fx="~res:/Materials/NormalizedBlinnPhongAllQualities.Alphatest.material",
                 textures={"albedo": "images/demo_track.tex"}, props={"alphatestThreshold": float_prop(0.3)}),
        material(12, "Shadow_Material", fx="~res:/Materials/ShadowVolume.material"),
        material(100, "Instance-0", parent=10), material(101, "Instance-1", parent=11),
        material(102, "Instance-2", parent=12),
    ]
    turret_q = quat_z(30.0)
    root = entity("demo_tank", 1, children=[
        lambda pw: entity("HP_gunFire_01", 2, t=(0, 3.6, 2.0), parent_world=pw, batches=[(123456789007, 100, -1)], lod=False),
        lambda pw: entity("hull", 3, parent_world=pw,
                          batches=[(123456789001, 100, 0), (123456789006, 102, 0), (123456789002, 100, 1)]),
        lambda pw: entity("chassis_track", 4, parent_world=pw, batches=[(123456789005, 101, 0), (123456789005, 101, 1)]),
        lambda pw: entity("turret_01", 5, t=(0, -0.4, 1.25), q=turret_q, parent_world=pw,
                          batches=[(123456789003, 100, 0), (123456789003, 100, 1)], children=[
            lambda pw2: entity("gun_01", 6, t=(0, 1.0, 0.35), parent_world=pw2,
                               batches=[(123456789004, 100, 0), (123456789004, 100, 1)]),
        ]),
    ])
    body = {"#dataNodes": mats, "#hierarchy": [root], "#sceneComponents": {"count": i32(0)}}
    tags = enc_ka({})
    desc = struct.pack("<II", 4, 1)          # size=4, fileType=1
    sc2 = b"SFV2" + struct.pack("<II", 43, 1) + tags + desc + enc_ka2_root(body)

    def tank_px(x, y):
        c = (90, 110, 70) if ((x // 8) + (y // 8)) % 2 else (130, 150, 95)
        if 4 <= x < 12 and 4 <= y < 12:        # top-left marker: red (orientation check)
            c = (220, 40, 40)
        return (*c, 140)                       # alpha = specular mask, not opacity

    def track_px(x, y):
        hole = (y % 8) in (3, 4) and 6 <= x < 26
        return (60, 55, 50, 0 if hole else 255)

    files = {
        "demo_tank.sc2.dvpl": dvpl(sc2),
        "demo_tank.scg.dvpl": dvpl(scg),
        "images/demo_tank.dx11.dds": dds_rgba(64, 64, tank_px),
        "images/demo_track.dx11.dds.dvpl": dvpl(dds_rgba(32, 32, track_px)),
    }
    for name, data in files.items():
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(data)
        print(f"{name:34s} {len(data):7d} bytes")


if __name__ == "__main__":
    build()

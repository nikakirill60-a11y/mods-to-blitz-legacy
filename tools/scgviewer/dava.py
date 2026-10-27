"""Reader for WoT Blitz / DAVA engine model files: .sc2, .scg and .dvpl.

Stdlib only (numpy is NOT required).  Used by ``scgtool.py`` (CLI) and as the
reference implementation for the JavaScript parser in ``scgviewer.html``.

Formats (all little-endian):

* DVPL  - any game file + 20-byte footer
          ``u32 unpacked, u32 packed, u32 crc32, u32 type, "DVPL"``;
          type 0 = stored, 1/2 = LZ4 block.
* KA    - DAVA KeyedArchive, ``"KA" u16 version``:
          0x0001 plain (key = typed string), 0x0002 with its own fast-name
          table, 0x0102 child that uses the parent's table, 0xFF02 empty.
* SCG   - ``"SCPG" u32 version u32 count u32 count2`` + ``count`` KAs, each one
          a ``PolygonGroup``.
* SC2   - ``"SFV2" u32 version u32 nodeCount``, version tags KA (v>=14),
          descriptor ``u32 size + size bytes`` (v>=10), then either
          - one KA v2 with ``#dataNodes`` / ``#hierarchy`` / ``#sceneComponents``
            (modern Blitz files, SC2 v30+), or
          - legacy layout: ``u32 dataNodeCount`` + data node KAs, then
            ``nodeCount`` entity KAs, children follow their parent
            (``#childrenCount``).

References: DAVA engine sources (KeyedArchive.cpp, VariantType.cpp,
SceneFileV2.cpp), Pyogenics/WOTBSCPGFormat wiki, BlitzKit (scpg.ts/sc2.ts).
"""

from __future__ import annotations

import math
import os
import re
import struct
import zlib
from array import array
from typing import Any

# ---------------------------------------------------------------------------
# errors / helpers
# ---------------------------------------------------------------------------


class DavaError(RuntimeError):
    pass


class Bytes(bytes):
    """Marker type for KA byte arrays (prints short in dumps)."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{len(self)} bytes>"


# ---------------------------------------------------------------------------
# DVPL
# ---------------------------------------------------------------------------


def lz4_block_decompress(src: bytes, out_size: int) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0 or off > len(out):
            raise DavaError(f"LZ4: bad match offset {off}")
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if ml <= off:
            out += out[start:start + ml]
        else:  # overlapping copy
            for k in range(ml):
                out.append(out[start + k])
    if len(out) != out_size:
        raise DavaError(f"LZ4: expected {out_size} bytes, got {len(out)}")
    return bytes(out)


def is_dvpl(data: bytes) -> bool:
    return len(data) >= 20 and data[-4:] == b"DVPL"


def decode_dvpl(data: bytes, check_crc: bool = True) -> bytes:
    if not is_dvpl(data):
        return data
    usize, psize, crc, kind = struct.unpack_from("<IIII", data, len(data) - 20)
    payload = data[:-20]
    if psize != len(payload):
        raise DavaError(f"DVPL: packed size {psize} != {len(payload)}")
    if check_crc and (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
        raise DavaError("DVPL: CRC32 mismatch")
    kind &= 0xFF
    if kind == 0:
        return payload
    if kind in (1, 2):
        try:
            import lz4.block  # type: ignore

            return lz4.block.decompress(payload, uncompressed_size=usize)
        except ImportError:
            return lz4_block_decompress(payload, usize)
    raise DavaError(f"DVPL: unknown compression type {kind}")


def read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return decode_dvpl(f.read())


# ---------------------------------------------------------------------------
# KeyedArchive
# ---------------------------------------------------------------------------

T_NONE, T_BOOL, T_INT32, T_FLOAT, T_STRING, T_WSTRING, T_BYTES, T_UINT32 = range(8)
T_KA, T_INT64, T_UINT64, T_V2, T_V3, T_V4, T_M2, T_M3, T_M4, T_COLOR = range(8, 18)
T_FASTNAME, T_AABB, T_FILEPATH, T_F64, T_INT8, T_UINT8, T_INT16, T_UINT16 = range(18, 26)
T_ARRAY, T_TRANSFORM = 27, 29

TYPE_NAMES = {
    0: "none", 1: "bool", 2: "int32", 3: "float", 4: "string", 5: "wstring",
    6: "bytes", 7: "uint32", 8: "archive", 9: "int64", 10: "uint64",
    11: "vec2", 12: "vec3", 13: "vec4", 14: "mat2", 15: "mat3", 16: "mat4",
    17: "color", 18: "fastname", 19: "aabbox3", 20: "filepath", 21: "float64",
    22: "int8", 23: "uint8", 24: "int16", 25: "uint16", 27: "array",
    29: "transform",
}


class Reader:
    __slots__ = ("d", "o", "end")

    def __init__(self, data: bytes, offset: int = 0, end: int | None = None):
        self.d = data
        self.o = offset
        self.end = len(data) if end is None else end

    def take(self, n: int) -> bytes:
        o = self.o
        if o + n > self.end:
            raise DavaError(f"unexpected end of data at 0x{o:x} (need {n})")
        self.o = o + n
        return self.d[o:o + n]

    def unpack(self, fmt: str):
        s = struct.calcsize(fmt)
        if self.o + s > self.end:
            raise DavaError(f"unexpected end of data at 0x{self.o:x} (need {s})")
        v = struct.unpack_from("<" + fmt, self.d, self.o)
        self.o += s
        return v

    def u8(self) -> int:
        return self.unpack("B")[0]

    def u16(self) -> int:
        return self.unpack("H")[0]

    def u32(self) -> int:
        return self.unpack("I")[0]

    def floats(self, n: int) -> list[float]:
        return list(self.unpack(f"{n}f"))


def _cstr(b: bytes) -> str:
    z = b.find(b"\0")
    if z >= 0:
        b = b[:z]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def _lookup(table: dict[int, str] | None, idx: int) -> str:
    if table is None:
        raise DavaError("fast-name reference without string table")
    if idx not in table:
        return f"<fastname #{idx}>"
    return table[idx]


def read_value(r: Reader, t: int, table: dict[int, str] | None) -> Any:
    if t == T_NONE:
        return None
    if t == T_BOOL:
        return r.u8() != 0
    if t == T_INT32:
        return r.unpack("i")[0]
    if t == T_UINT32:
        return r.u32()
    if t == T_FLOAT:
        return r.unpack("f")[0]
    if t in (T_STRING, T_FASTNAME, T_FILEPATH):
        if table is not None:
            return _lookup(table, r.u32())
        return _cstr(r.take(r.u32()))
    if t == T_WSTRING:
        if table is not None:
            return _lookup(table, r.u32())
        n = r.u32()
        # DAVA writes wchar_t; 2 bytes on Windows-built tools, 4 elsewhere.
        # Heuristic: the remaining archive must fit the string.
        raw = r.take(n * 2)
        return raw.decode("utf-16-le", errors="replace")
    if t == T_BYTES:
        return Bytes(r.take(r.u32()))
    if t == T_KA:
        size = r.u32()
        sub = Reader(r.d, r.o, r.o + size)
        r.o += size
        if size == 0:
            return {}
        return read_archive(sub, table)
    if t == T_INT64:
        return r.unpack("q")[0]
    if t == T_UINT64:
        return r.unpack("Q")[0]
    if t == T_V2:
        return r.floats(2)
    if t == T_V3:
        return r.floats(3)
    if t in (T_V4, T_COLOR):
        return r.floats(4)
    if t == T_M2:
        return r.floats(4)
    if t == T_M3:
        return r.floats(9)
    if t == T_M4:
        return r.floats(16)
    if t == T_AABB:
        f = r.floats(6)
        return {"min": f[:3], "max": f[3:]}
    if t == T_F64:
        return r.unpack("d")[0]
    if t == T_INT8:
        return r.unpack("b")[0]
    if t == T_UINT8:
        return r.u8()
    if t == T_INT16:
        return r.unpack("h")[0]
    if t == T_UINT16:
        return r.u16()
    if t == T_ARRAY:
        n = r.u32()
        return [read_value(r, r.u8(), table) for _ in range(n)]
    if t == T_TRANSFORM:
        f = r.floats(10)
        return {"position": f[0:3], "scale": f[3:6], "rotation": f[6:10]}
    raise DavaError(f"unknown KA value type {t} at 0x{r.o - 1:x}")


def read_archive(r: Reader, table: dict[int, str] | None = None) -> dict:
    start = r.o
    if r.take(2) != b"KA":
        raise DavaError(f"no KA signature at 0x{start:x}")
    ver = r.u16()
    out: dict[str, Any] = {}
    if ver == 0x0001:
        for _ in range(r.u32()):
            key = read_value(r, r.u8(), None)
            out[str(key)] = read_value(r, r.u8(), None)
        return out
    if ver == 0xFF02:
        return out
    if ver == 0x0002:
        n = r.u32()
        names = [_cstr(r.take(r.u16())) for _ in range(n)]
        ids = [r.u32() for _ in range(n)]
        table = dict(zip(ids, names))
    elif ver != 0x0102:
        raise DavaError(f"unsupported KA version 0x{ver:04x} at 0x{start:x}")
    if table is None:
        raise DavaError("KA 0x0102 without parent string table")
    for _ in range(r.u32()):
        key = _lookup(table, r.u32())
        out[key] = read_value(r, r.u8(), table)
    return out


def as_id(v: Any) -> int | None:
    """``#id`` / ``rb.datasource`` / ``rb.nmatname`` -> int."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v & 0xFFFFFFFFFFFFFFFF
    if isinstance(v, (bytes, bytearray)) and 0 < len(v) <= 8:
        return int.from_bytes(v, "little")
    return None


# ---------------------------------------------------------------------------
# Polygon groups (geometry)
# ---------------------------------------------------------------------------

# eVertexFormat bit -> (attribute name, float count). Order of bits == order
# of the attributes inside one interleaved vertex.
VERTEX_ATTRS = [
    (0, "position", 3), (1, "normal", 3), (2, "color", 1),
    (3, "uv0", 2), (4, "uv1", 2), (5, "uv2", 2), (6, "uv3", 2),
    (7, "tangent", 3), (8, "binormal", 3), (9, "hardJoint", 1),
    (10, "pivot4", 4), (11, "pivotOld", 3), (12, "flexibility", 1),
    (13, "angleSinCos", 2), (14, "jointIndex", 4), (15, "jointWeight", 4),
    (16, "cubeuv0", 3), (17, "cubeuv1", 3), (18, "cubeuv2", 3), (19, "cubeuv3", 3),
]
PRIM_NAMES = {1: "triangles", 2: "triangle strip", 10: "lines"}


def vertex_layout(fmt: int) -> tuple[list[tuple[str, int, int]], int]:
    """-> ([(name, float_offset, count)], stride_bytes)."""
    lay, off = [], 0
    for bit, name, cnt in VERTEX_ATTRS:
        if fmt & (1 << bit):
            lay.append((name, off, cnt))
            off += cnt
    return lay, off * 4


class Mesh:
    """Decoded polygon group: flat float lists + triangle index list."""

    def __init__(self, pg: dict):
        self.raw = pg
        self.id = as_id(pg.get("#id"))
        self.format = int(pg.get("vertexFormat", 0))
        self.vcount = int(pg.get("vertexCount", 0))
        self.prim = int(pg.get("rhi_primitiveType", pg.get("primitiveType", 1)) or 1)
        self.layout, self.stride = vertex_layout(self.format)
        vb = pg.get("vertices") or b""
        self.warning = ""
        if self.vcount and len(vb) != self.stride * self.vcount:
            real = len(vb) // self.vcount if self.vcount else 0
            self.warning = (f"vertex stride mismatch: format says {self.stride}, "
                            f"data says {real}")
            self.stride = real
        self.floats = array("f")
        usable = (len(vb) // 4) * 4
        self.floats.frombytes(vb[:usable])
        if struct.pack("=I", 1) != struct.pack("<I", 1):  # big-endian host
            self.floats.byteswap()
        ib = pg.get("indices") or b""
        icount = int(pg.get("indexCount", 0))
        ifmt = int(pg.get("indexFormat", 0))
        idx = array("H" if ifmt == 0 else "I")
        size = idx.itemsize
        idx.frombytes(ib[: icount * size])
        self.indices = list(idx)
        if self.prim == 2:  # strip -> list
            tri = []
            for k in range(len(self.indices) - 2):
                a, b, c = self.indices[k:k + 3]
                if a == b or b == c or a == c:
                    continue
                tri += (a, b, c) if k % 2 == 0 else (b, a, c)
            self.indices = tri
            self.prim = 1

    def attr(self, name: str) -> list[tuple[float, ...]] | None:
        for n, off, cnt in self.layout:
            if n == name:
                s = self.stride // 4
                f = self.floats
                return [tuple(f[i * s + off:i * s + off + cnt]) for i in range(self.vcount)]
        return None

    @property
    def triangles(self) -> int:
        return len(self.indices) // 3 if self.prim == 1 else 0

    def bbox(self) -> tuple[list[float], list[float]] | None:
        pos = self.attr("position")
        if not pos:
            return None
        mn = [min(p[i] for p in pos) for i in range(3)]
        mx = [max(p[i] for p in pos) for i in range(3)]
        return mn, mx

    def describe(self) -> str:
        names = "+".join(n for n, _, _ in self.layout)
        return (f"id={self.id} verts={self.vcount} tris={self.triangles} "
                f"stride={self.stride} [{names}] {PRIM_NAMES.get(self.prim, self.prim)}")


def read_scg(data: bytes) -> dict:
    r = Reader(data)
    if r.take(4) != b"SCPG":
        raise DavaError("not an SCG file (no SCPG signature)")
    version, count, count2 = r.unpack("III")
    groups = [read_archive(r) for _ in range(count)]
    return {"version": version, "count": count, "count2": count2,
            "polygonGroups": groups, "trailing": r.end - r.o}


# ---------------------------------------------------------------------------
# SC2
# ---------------------------------------------------------------------------


def _read_legacy_entity(r: Reader) -> dict:
    node = read_archive(r)
    kids = int(node.get("#childrenCount", 0) or 0)
    if kids:
        node["#hierarchy"] = [_read_legacy_entity(r) for _ in range(kids)]
    return node


def read_sc2(data: bytes) -> dict:
    r = Reader(data)
    if r.take(4) != b"SFV2":
        raise DavaError("not an SC2 file (no SFV2 signature)")
    version, node_count = r.unpack("II")
    tags = read_archive(r) if version >= 14 else {}
    desc_type = None
    if version >= 10:
        size = r.u32()
        blob = r.take(size)
        if size >= 4:
            desc_type = struct.unpack_from("<I", blob)[0]
    scene: dict[str, Any] = {
        "version": version, "nodeCount": node_count, "versionTags": tags,
        "fileType": desc_type,
    }
    # modern: a single KA v2 holding everything
    if r.o + 4 <= r.end and data[r.o:r.o + 2] == b"KA" and \
            struct.unpack_from("<H", data, r.o + 2)[0] == 2:
        body = read_archive(r)
        scene["layout"] = "modern"
        scene["dataNodes"] = list(body.get("#dataNodes") or [])
        scene["hierarchy"] = list(body.get("#hierarchy") or [])
        scene["sceneComponents"] = body.get("#sceneComponents") or {}
        scene["extra"] = {k: v for k, v in body.items()
                          if k not in ("#dataNodes", "#hierarchy", "#sceneComponents")}
    else:
        scene["layout"] = "legacy"
        data_nodes = []
        if version >= 2:
            n = r.u32()
            data_nodes = [read_archive(r) for _ in range(n)]
        scene["dataNodes"] = data_nodes
        scene["hierarchy"] = [_read_legacy_entity(r) for _ in range(node_count)]
    scene["trailing"] = r.end - r.o
    return scene


# ---------------------------------------------------------------------------
# Scene model (what a viewer/exporter needs)
# ---------------------------------------------------------------------------

IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def mat_mul(a: list[float], b: list[float]) -> list[float]:
    """Row-vector convention (DAVA): v' = v * A * B  ->  returns A*B."""
    out = [0.0] * 16
    for i in range(4):
        for j in range(4):
            out[i * 4 + j] = sum(a[i * 4 + k] * b[k * 4 + j] for k in range(4))
    return out


def trs_matrix(t, q, s) -> list[float]:
    """DAVA row-major matrix (translation in elements 12..14), q=(x,y,z,w)."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    # rows = rotated basis vectors (row-vector convention => transpose of
    # the usual column-vector rotation matrix)
    r = [
        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)],
    ]
    m = []
    for i in range(3):
        m += [r[i][0] * s[i], r[i][1] * s[i], r[i][2] * s[i], 0.0]
    m += [t[0], t[1], t[2], 1.0]
    return m


def transform_point(m: list[float], p) -> tuple[float, float, float]:
    x, y, z = p
    return (x * m[0] + y * m[4] + z * m[8] + m[12],
            x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


def components(entity: dict) -> list[dict]:
    comps = entity.get("components")
    if isinstance(comps, dict):
        cnt = comps.get("count")
        out = []
        if isinstance(cnt, int):
            for i in range(cnt):
                c = comps.get(f"{i:04d}")
                if isinstance(c, dict):
                    out.append(c)
        if not out:
            out = [v for v in comps.values() if isinstance(v, dict)]
        return out
    return []


def component(entity: dict, typename: str) -> dict | None:
    for c in components(entity):
        if c.get("comp.typename") == typename:
            return c
    return None


def local_matrix(entity: dict) -> list[float]:
    tc = component(entity, "TransformComponent")
    if tc:
        if "tc.localTranslation" in tc or "tc.localRotation" in tc:
            return trs_matrix(tc.get("tc.localTranslation") or (0, 0, 0),
                              tc.get("tc.localRotation") or (0, 0, 0, 1),
                              tc.get("tc.localScale") or (1, 1, 1))
        if isinstance(tc.get("tc.localMatrix"), list) and len(tc["tc.localMatrix"]) == 16:
            return list(tc["tc.localMatrix"])
    m = entity.get("localTransform") or entity.get("transform")
    if isinstance(m, list) and len(m) == 16:
        return list(m)
    return list(IDENTITY)


class Batch:
    def __init__(self, index: int, rb: dict, lod: int, switch: int):
        self.index = index
        self.raw = rb
        self.datasource = as_id(rb.get("rb.datasource"))
        self.material = as_id(rb.get("rb.nmatname"))
        self.lod = lod
        self.switch = switch


class Entity:
    def __init__(self, raw: dict, parent: "Entity | None"):
        self.raw = raw
        self.parent = parent
        self.name = str(raw.get("name") or raw.get("##name") or "?")
        self.local = local_matrix(raw)
        self.world = mat_mul(self.local, parent.world) if parent else self.local
        self.batches: list[Batch] = []
        rc = component(raw, "RenderComponent")
        ro = rc.get("rc.renderObj") if rc else None
        if isinstance(ro, dict):
            batches = ro.get("ro.batches")
            if isinstance(batches, dict):
                for key in sorted(batches):
                    rb = batches[key]
                    if not isinstance(rb, dict):
                        continue
                    try:
                        i = int(key)
                    except ValueError:
                        i = len(self.batches)
                    lod = ro.get(f"rb{i}.lodIndex", -1)
                    sw = ro.get(f"rb{i}.switchIndex", -1)
                    self.batches.append(Batch(i, rb, lod if isinstance(lod, int) else -1,
                                              sw if isinstance(sw, int) else -1))
        self.children = [Entity(c, self) for c in (raw.get("#hierarchy") or [])
                         if isinstance(c, dict)]

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


class Material:
    def __init__(self, node: dict):
        self.raw = node
        self.id = as_id(node.get("#id"))
        self.name = node.get("materialName") or node.get("name") or f"mat_{self.id}"
        self.parent = as_id(node.get("parentMaterialKey"))
        self.fx = node.get("fxName")
        tex = dict(node.get("textures") or {})
        cnt = node.get("configCount")
        if isinstance(cnt, int) and cnt > 0:
            cfgs = [node.get(f"configArchive_{i}") for i in range(cnt)]
            cfgs = [c for c in cfgs if isinstance(c, dict)]
            pick = next((c for c in cfgs if c.get("configName") == "Default"),
                        cfgs[0] if cfgs else None)
            if pick:
                tex.update(pick.get("textures") or {})
                self.fx = self.fx or pick.get("fxName")
        self.textures = {k: v for k, v in tex.items() if isinstance(v, str)}


class Scene:
    """SC2 (+ optional SCG) -> entities, materials, meshes."""

    def __init__(self, sc2: dict | None, scg: dict | None = None):
        self.sc2 = sc2
        self.meshes: dict[int, Mesh] = {}
        self.materials: dict[int, Material] = {}
        self.other_nodes: list[dict] = []
        if scg:
            for pg in scg["polygonGroups"]:
                m = Mesh(pg)
                if m.id is not None:
                    self.meshes[m.id] = m
        roots = []
        if sc2:
            for dn in sc2["dataNodes"]:
                kind = dn.get("##name")
                if kind == "PolygonGroup":
                    m = Mesh(dn)
                    if m.id is not None:
                        self.meshes[m.id] = m
                elif kind == "NMaterial" or "materialName" in dn:
                    mat = Material(dn)
                    if mat.id is not None:
                        self.materials[mat.id] = mat
                else:
                    self.other_nodes.append(dn)
            roots = [Entity(e, None) for e in sc2["hierarchy"] if isinstance(e, dict)]
        self.roots = roots

    def entities(self):
        for r in self.roots:
            yield from r.walk()

    def material_chain(self, mid: int | None) -> list[Material]:
        out, seen = [], set()
        while mid is not None and mid in self.materials and mid not in seen:
            seen.add(mid)
            m = self.materials[mid]
            out.append(m)
            mid = m.parent
        return out

    def textures_for(self, mid: int | None) -> dict[str, str]:
        tex: dict[str, str] = {}
        for m in reversed(self.material_chain(mid)):
            tex.update(m.textures)
        return tex

    def material_name(self, mid: int | None) -> str:
        chain = self.material_chain(mid)
        return chain[-1].name if chain else (f"mat_{mid}" if mid is not None else "none")

    def material_fx(self, mid: int | None) -> str:
        for m in self.material_chain(mid):
            if m.fx:
                return str(m.fx)
        return ""

    def is_service(self, e: "Entity | None", b: "Batch | None" = None) -> bool:
        """HP_ helpers, crash/dummy parts and shadow volumes (hidden by default)."""
        n = e.name if e else ""
        if re.match(r"(?i)HP_", n) or re.search(r"(?i)_crash|dummy", n):
            return True
        if b is not None:
            if re.search(r"(?i)ShadowVolume", self.material_fx(b.material)):
                return True
            if re.match(r"(?i)Shadow_Material", self.material_name(b.material)):
                return True
        return False

    def lods(self) -> list[int]:
        s = {b.lod for e in self.entities() for b in e.batches}
        return sorted(s)

    def draw_list(self, lod: int | None = 0, include_service: bool = True):
        """Yield (entity, batch, mesh) for the chosen LOD (None = all).

        Also follows the DAVA rule that ``-1`` means "every LOD"."""
        if not self.roots and self.meshes:
            for m in self.meshes.values():
                yield None, None, m
            return
        for e in self.entities():
            for b in e.batches:
                if lod is not None and b.lod not in (lod, -1):
                    continue
                m = self.meshes.get(b.datasource) if b.datasource is not None else None
                if m is None or (not include_service and self.is_service(e, b)):
                    continue
                yield e, b, m


def load_scene(path: str) -> Scene:
    """Open .sc2/.scg (optionally .dvpl); auto-loads the paired file."""
    base = path[:-5] if path.lower().endswith(".dvpl") else path
    low = base.lower()
    stem = base[:-4]

    def first(*cands):
        for c in cands:
            if os.path.isfile(c):
                return c
        return None

    sc2_path = path if low.endswith(".sc2") else first(stem + ".sc2", stem + ".sc2.dvpl")
    scg_path = path if low.endswith(".scg") else first(stem + ".scg", stem + ".scg.dvpl")
    sc2 = read_sc2(read_file(sc2_path)) if sc2_path else None
    scg = read_scg(read_file(scg_path)) if scg_path else None
    if not sc2 and not scg:
        raise DavaError(f"nothing to load for {path}")
    return Scene(sc2, scg)

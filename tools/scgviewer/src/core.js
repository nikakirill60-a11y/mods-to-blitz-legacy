/* ===== DAVA core: DVPL, KeyedArchive, SCG, SC2, scene model, textures =====
 * No DOM access in this block: it is also loaded by node for tests
 * (tools/scgviewer/test_core.js).  Port of tools/scgviewer/dava.py.
 */
"use strict";
const Dava = (() => {

class DavaError extends Error {}

// ---------------------------------------------------------------- DVPL / LZ4
function lz4Block(src, outSize) {
  const out = new Uint8Array(outSize);
  let i = 0, o = 0;
  const n = src.length;
  while (i < n) {
    const token = src[i++];
    let lit = token >> 4;
    if (lit === 15) { let b; do { b = src[i++]; lit += b; } while (b === 255); }
    if (o + lit > outSize) throw new DavaError("LZ4: output overflow");
    out.set(src.subarray(i, i + lit), o); i += lit; o += lit;
    if (i >= n) break;
    const off = src[i] | (src[i + 1] << 8); i += 2;
    if (off === 0 || off > o) throw new DavaError("LZ4: bad match offset " + off);
    let ml = token & 15;
    if (ml === 15) { let b; do { b = src[i++]; ml += b; } while (b === 255); }
    ml += 4;
    if (o + ml > outSize) throw new DavaError("LZ4: output overflow");
    let s = o - off;
    for (let k = 0; k < ml; k++) out[o++] = out[s++];
  }
  if (o !== outSize) throw new DavaError(`LZ4: expected ${outSize} bytes, got ${o}`);
  return out;
}

let CRC_TABLE = null;
function crc32(u8) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let c = 0xFFFFFFFF;
  for (let i = 0; i < u8.length; i++) c = CRC_TABLE[(c ^ u8[i]) & 255] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function isDVPL(u8) {
  const n = u8.length;
  return n >= 20 && u8[n - 4] === 0x44 && u8[n - 3] === 0x56 && u8[n - 2] === 0x50 && u8[n - 1] === 0x4C;
}

function decodeDVPL(u8, checkCrc = true) {
  if (!isDVPL(u8)) return u8;
  const dv = new DataView(u8.buffer, u8.byteOffset + u8.length - 20, 20);
  const usize = dv.getUint32(0, true), psize = dv.getUint32(4, true);
  const crc = dv.getUint32(8, true), kind = dv.getUint32(12, true) & 255;
  const payload = u8.subarray(0, u8.length - 20);
  if (psize !== payload.length) throw new DavaError(`DVPL: packed size ${psize} != ${payload.length}`);
  if (checkCrc && crc32(payload) !== crc) throw new DavaError("DVPL: CRC32 mismatch");
  if (kind === 0) return payload;
  if (kind === 1 || kind === 2) return lz4Block(payload, usize);
  throw new DavaError("DVPL: unknown compression type " + kind);
}

// ---------------------------------------------------------------- reader
const UTF8 = new TextDecoder("utf-8");
const UTF16 = new TextDecoder("utf-16le");

class Reader {
  constructor(u8, offset = 0, end = u8.length) {
    this.u8 = u8; this.o = offset; this.end = end;
    this.dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  }
  need(n) { if (this.o + n > this.end) throw new DavaError(`unexpected end of data at 0x${this.o.toString(16)} (need ${n})`); }
  take(n) { this.need(n); const s = this.u8.subarray(this.o, this.o + n); this.o += n; return s; }
  u8v() { this.need(1); return this.u8[this.o++]; }
  i8() { this.need(1); return this.dv.getInt8(this.o++); }
  u16() { this.need(2); const v = this.dv.getUint16(this.o, true); this.o += 2; return v; }
  i16() { this.need(2); const v = this.dv.getInt16(this.o, true); this.o += 2; return v; }
  u32() { this.need(4); const v = this.dv.getUint32(this.o, true); this.o += 4; return v; }
  i32() { this.need(4); const v = this.dv.getInt32(this.o, true); this.o += 4; return v; }
  i64() { this.need(8); const v = this.dv.getBigInt64(this.o, true); this.o += 8; return v; }
  u64() { this.need(8); const v = this.dv.getBigUint64(this.o, true); this.o += 8; return v; }
  f32() { this.need(4); const v = this.dv.getFloat32(this.o, true); this.o += 4; return v; }
  f64() { this.need(8); const v = this.dv.getFloat64(this.o, true); this.o += 8; return v; }
  floats(n) { const a = new Array(n); for (let i = 0; i < n; i++) a[i] = this.f32(); return a; }
  ascii(n) { return cstr(this.take(n)); }
}

function cstr(b) {
  let z = b.indexOf(0);
  if (z >= 0) b = b.subarray(0, z);
  return UTF8.decode(b);
}

// ---------------------------------------------------------------- KeyedArchive
const T = { NONE: 0, BOOL: 1, INT32: 2, FLOAT: 3, STRING: 4, WSTRING: 5, BYTES: 6, UINT32: 7,
  KA: 8, INT64: 9, UINT64: 10, V2: 11, V3: 12, V4: 13, M2: 14, M3: 15, M4: 16, COLOR: 17,
  FASTNAME: 18, AABB: 19, FILEPATH: 20, F64: 21, INT8: 22, UINT8: 23, INT16: 24, UINT16: 25,
  ARRAY: 27, TRANSFORM: 29 };

function lookup(table, idx) {
  if (!table) throw new DavaError("fast-name reference without string table");
  const s = table.get(idx);
  return s === undefined ? `<fastname #${idx}>` : s;
}

function readValue(r, t, table) {
  switch (t) {
    case T.NONE: return null;
    case T.BOOL: return r.u8v() !== 0;
    case T.INT32: return r.i32();
    case T.UINT32: return r.u32();
    case T.FLOAT: return r.f32();
    case T.STRING: case T.FASTNAME: case T.FILEPATH:
      if (table) return lookup(table, r.u32());
      return r.ascii(r.u32());
    case T.WSTRING:
      if (table) return lookup(table, r.u32());
      return UTF16.decode(r.take(r.u32() * 2));
    case T.BYTES: return r.take(r.u32());
    case T.KA: {
      const size = r.u32();
      const sub = new Reader(r.u8, r.o, r.o + size);
      r.need(size); r.o += size;
      return size ? readArchive(sub, table) : {};
    }
    case T.INT64: return r.i64();
    case T.UINT64: return r.u64();
    case T.V2: return r.floats(2);
    case T.V3: return r.floats(3);
    case T.V4: case T.COLOR: return r.floats(4);
    case T.M2: return r.floats(4);
    case T.M3: return r.floats(9);
    case T.M4: return r.floats(16);
    case T.AABB: { const f = r.floats(6); return { min: f.slice(0, 3), max: f.slice(3) }; }
    case T.F64: return r.f64();
    case T.INT8: return r.i8();
    case T.UINT8: return r.u8v();
    case T.INT16: return r.i16();
    case T.UINT16: return r.u16();
    case T.ARRAY: { const n = r.u32(); const a = new Array(n); for (let i = 0; i < n; i++) a[i] = readValue(r, r.u8v(), table); return a; }
    case T.TRANSFORM: { const f = r.floats(10); return { position: f.slice(0, 3), scale: f.slice(3, 6), rotation: f.slice(6) }; }
  }
  throw new DavaError(`unknown KA value type ${t} at 0x${(r.o - 1).toString(16)}`);
}

function readArchive(r, table = null) {
  const start = r.o;
  const sig = r.take(2);
  if (sig[0] !== 0x4B || sig[1] !== 0x41) throw new DavaError(`no KA signature at 0x${start.toString(16)}`);
  const ver = r.u16();
  const out = {};
  if (ver === 0x0001) {
    const n = r.u32();
    for (let i = 0; i < n; i++) {
      const key = readValue(r, r.u8v(), null);
      out[String(key)] = readValue(r, r.u8v(), null);
    }
    return out;
  }
  if (ver === 0xFF02) return out;
  if (ver === 0x0002) {
    const n = r.u32();
    const names = new Array(n);
    for (let i = 0; i < n; i++) names[i] = r.ascii(r.u16());
    table = new Map();
    for (let i = 0; i < n; i++) table.set(r.u32(), names[i]);
  } else if (ver !== 0x0102) {
    throw new DavaError(`unsupported KA version 0x${ver.toString(16)} at 0x${start.toString(16)}`);
  }
  if (!table) throw new DavaError("KA 0x0102 without parent string table");
  const n = r.u32();
  for (let i = 0; i < n; i++) {
    const key = lookup(table, r.u32());
    out[key] = readValue(r, r.u8v(), table);
  }
  return out;
}

/** #id / rb.datasource / rb.nmatname -> decimal string key (u64) or null */
function idKey(v) {
  if (typeof v === "bigint") return BigInt.asUintN(64, v).toString();
  if (typeof v === "number" && Number.isInteger(v)) return BigInt.asUintN(64, BigInt(v)).toString();
  if (v instanceof Uint8Array && v.length > 0 && v.length <= 8) {
    let x = 0n;
    for (let i = v.length - 1; i >= 0; i--) x = (x << 8n) | BigInt(v[i]);
    return x.toString();
  }
  return null;
}

// ---------------------------------------------------------------- geometry
const VERTEX_ATTRS = [
  [0, "position", 3], [1, "normal", 3], [2, "color", 1], [3, "uv0", 2], [4, "uv1", 2],
  [5, "uv2", 2], [6, "uv3", 2], [7, "tangent", 3], [8, "binormal", 3], [9, "hardJoint", 1],
  [10, "pivot4", 4], [11, "pivotOld", 3], [12, "flexibility", 1], [13, "angleSinCos", 2],
  [14, "jointIndex", 4], [15, "jointWeight", 4], [16, "cubeuv0", 3], [17, "cubeuv1", 3],
  [18, "cubeuv2", 3], [19, "cubeuv3", 3],
];
const PRIM_NAMES = { 1: "треугольники", 2: "полоса треугольников", 10: "линии" };

function vertexLayout(fmt) {
  const lay = {}; const names = []; let off = 0;
  for (const [bit, name, cnt] of VERTEX_ATTRS) {
    if (fmt & (1 << bit)) { lay[name] = { offset: off, count: cnt }; names.push(name); off += cnt; }
  }
  return { attrs: lay, names, stride: off * 4 };
}

class Mesh {
  constructor(pg, source) {
    this.raw = pg;
    this.source = source;              // "scg" | "sc2"
    this.id = idKey(pg["#id"]);
    this.format = Number(pg.vertexFormat || 0);
    this.vcount = Number(pg.vertexCount || 0);
    this.prim = Number(pg.rhi_primitiveType ?? pg.primitiveType ?? 1) || 1;
    const L = vertexLayout(this.format);
    this.layout = L.attrs; this.attrNames = L.names; this.stride = L.stride;
    this.warning = "";
    const vb = pg.vertices instanceof Uint8Array ? pg.vertices : new Uint8Array(0);
    if (this.vcount && vb.length !== this.stride * this.vcount) {
      const real = Math.floor(vb.length / this.vcount);
      this.warning = `размер вершины: по формату ${this.stride} байт, по данным ${real}`;
      this.stride = real;
      // keep only attributes that still fit
      for (const k of Object.keys(this.layout)) {
        const a = this.layout[k];
        if ((a.offset + a.count) * 4 > real) delete this.layout[k];
      }
    }
    const usable = vb.length - (vb.length % 4);
    this.floats = new Float32Array(vb.slice(0, usable).buffer);   // copy => aligned
    const icount = Number(pg.indexCount || 0);
    const ifmt = Number(pg.indexFormat || 0);
    const ib = pg.indices instanceof Uint8Array ? pg.indices : new Uint8Array(0);
    const isz = ifmt === 0 ? 2 : 4;
    const ibytes = ib.slice(0, Math.min(ib.length, icount * isz) - ((Math.min(ib.length, icount * isz)) % isz));
    let idx = ifmt === 0 ? new Uint16Array(ibytes.buffer) : new Uint32Array(ibytes.buffer);
    if (this.prim === 2) {             // strip -> list
      const tri = [];
      for (let k = 0; k + 2 < idx.length; k++) {
        const a = idx[k], b = idx[k + 1], c = idx[k + 2];
        if (a === b || b === c || a === c) continue;
        if (k % 2 === 0) tri.push(a, b, c); else tri.push(b, a, c);
      }
      idx = Uint32Array.from(tri);
      this.prim = 1;
    }
    this.indices = idx;
    this._bbox = null;
  }
  get triangles() { return this.prim === 1 ? Math.floor(this.indices.length / 3) : 0; }
  get lines() { return this.prim === 10 ? Math.floor(this.indices.length / 2) : 0; }
  attr(name) {
    const a = this.layout[name];
    if (!a) return null;
    const s = this.stride / 4, out = new Float32Array(this.vcount * a.count);
    for (let i = 0; i < this.vcount; i++)
      for (let c = 0; c < a.count; c++) out[i * a.count + c] = this.floats[i * s + a.offset + c];
    return out;
  }
  bbox() {
    if (this._bbox) return this._bbox;
    const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    const s = this.stride / 4;
    for (let i = 0; i < this.vcount; i++)
      for (let c = 0; c < 3; c++) {
        const v = this.floats[i * s + c];
        if (!Number.isFinite(v)) continue;
        if (v < mn[c]) mn[c] = v; if (v > mx[c]) mx[c] = v;
      }
    return (this._bbox = { min: mn, max: mx });
  }
}

function readSCG(u8) {
  const r = new Reader(u8);
  if (cstr(r.take(4)) !== "SCPG") throw new DavaError("это не SCG (нет сигнатуры SCPG)");
  const version = r.u32(), count = r.u32(), count2 = r.u32();
  const groups = [];
  for (let i = 0; i < count; i++) groups.push(readArchive(r));
  return { kind: "scg", version, count, count2, polygonGroups: groups, trailing: r.end - r.o };
}

// ---------------------------------------------------------------- SC2
function readLegacyEntity(r) {
  const node = readArchive(r);
  const kids = Number(node["#childrenCount"] || 0);
  if (kids) { node["#hierarchy"] = []; for (let i = 0; i < kids; i++) node["#hierarchy"].push(readLegacyEntity(r)); }
  return node;
}

function readSC2(u8) {
  const r = new Reader(u8);
  if (cstr(r.take(4)) !== "SFV2") throw new DavaError("это не SC2 (нет сигнатуры SFV2)");
  const version = r.u32(), nodeCount = r.u32();
  const tags = version >= 14 ? readArchive(r) : {};
  let fileType = null;
  if (version >= 10) {
    const size = r.u32(); const blob = r.take(size);
    if (size >= 4) fileType = new DataView(blob.buffer, blob.byteOffset, 4).getUint32(0, true);
  }
  const scene = { kind: "sc2", version, nodeCount, versionTags: tags, fileType };
  const modern = r.o + 4 <= r.end && r.u8[r.o] === 0x4B && r.u8[r.o + 1] === 0x41 &&
    r.dv.getUint16(r.o + 2, true) === 2;
  if (modern) {
    const body = readArchive(r);
    scene.layout = "modern";
    scene.dataNodes = body["#dataNodes"] || [];
    scene.hierarchy = body["#hierarchy"] || [];
    scene.sceneComponents = body["#sceneComponents"] || {};
  } else {
    scene.layout = "legacy";
    scene.dataNodes = [];
    if (version >= 2) { const n = r.u32(); for (let i = 0; i < n; i++) scene.dataNodes.push(readArchive(r)); }
    scene.hierarchy = [];
    for (let i = 0; i < nodeCount; i++) scene.hierarchy.push(readLegacyEntity(r));
  }
  scene.trailing = r.end - r.o;
  return scene;
}

// ---------------------------------------------------------------- math (DAVA row-major == GL column-major)
const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

/** DAVA row-vector product A*B (apply A first, then B). */
function matMul(a, b) {
  const o = new Array(16);
  for (let i = 0; i < 4; i++)
    for (let j = 0; j < 4; j++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[i * 4 + k] * b[k * 4 + j];
      o[i * 4 + j] = s;
    }
  return o;
}

function trsMatrix(t, q, s) {
  let [x, y, z, w] = q;
  const n = Math.hypot(x, y, z, w) || 1; x /= n; y /= n; z /= n; w /= n;
  const r = [
    [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
    [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
    [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)],
  ];
  const m = [];
  for (let i = 0; i < 3; i++) m.push(r[i][0] * s[i], r[i][1] * s[i], r[i][2] * s[i], 0);
  m.push(t[0], t[1], t[2], 1);
  return m;
}

function transformPoint(m, x, y, z) {
  return [x * m[0] + y * m[4] + z * m[8] + m[12], x * m[1] + y * m[5] + z * m[9] + m[13], x * m[2] + y * m[6] + z * m[10] + m[14]];
}

// ---------------------------------------------------------------- scene model
function components(entity) {
  const comps = entity.components;
  if (!comps || typeof comps !== "object") return [];
  const out = [];
  if (Number.isInteger(comps.count)) {
    for (let i = 0; i < comps.count; i++) {
      const c = comps[String(i).padStart(4, "0")];
      if (c && typeof c === "object" && !(c instanceof Uint8Array)) out.push(c);
    }
  }
  if (!out.length) for (const v of Object.values(comps)) if (v && typeof v === "object" && !Array.isArray(v) && !(v instanceof Uint8Array)) out.push(v);
  return out;
}
function component(entity, typename) {
  return components(entity).find(c => c["comp.typename"] === typename) || null;
}

function localMatrix(entity) {
  const tc = component(entity, "TransformComponent");
  if (tc) {
    if ("tc.localTranslation" in tc || "tc.localRotation" in tc)
      return trsMatrix(tc["tc.localTranslation"] || [0, 0, 0], tc["tc.localRotation"] || [0, 0, 0, 1], tc["tc.localScale"] || [1, 1, 1]);
    if (Array.isArray(tc["tc.localMatrix"]) && tc["tc.localMatrix"].length === 16) return tc["tc.localMatrix"].slice();
  }
  for (const k of ["localTransform", "transform"]) if (Array.isArray(entity[k]) && entity[k].length === 16) return entity[k].slice();
  return IDENTITY.slice();
}

let ENTITY_SEQ = 0;
class Entity {
  constructor(raw, parent) {
    this.uid = ++ENTITY_SEQ;
    this.raw = raw; this.parent = parent;
    this.name = String(raw.name ?? raw["##name"] ?? "?");
    this.local = localMatrix(raw);
    this.world = parent ? matMul(this.local, parent.world) : this.local;
    this.batches = [];
    this.visible = true;
    const rc = component(raw, "RenderComponent");
    const ro = rc && rc["rc.renderObj"];
    this.renderObject = ro && typeof ro === "object" ? ro : null;
    if (this.renderObject && ro["ro.batches"] && typeof ro["ro.batches"] === "object") {
      for (const key of Object.keys(ro["ro.batches"]).sort()) {
        const rb = ro["ro.batches"][key];
        if (!rb || typeof rb !== "object") continue;
        let i = parseInt(key, 10); if (!Number.isFinite(i)) i = this.batches.length;
        const lod = ro[`rb${i}.lodIndex`], sw = ro[`rb${i}.switchIndex`];
        this.batches.push({ index: i, raw: rb, entity: this,
          datasource: idKey(rb["rb.datasource"]), material: idKey(rb["rb.nmatname"]),
          lod: Number.isInteger(lod) ? lod : -1, switch: Number.isInteger(sw) ? sw : -1 });
      }
    }
    this.children = (raw["#hierarchy"] || []).filter(c => c && typeof c === "object").map(c => new Entity(c, this));
  }
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
  get path() { return this.parent ? this.parent.path + "/" + this.name : this.name; }
}

class Material {
  constructor(node) {
    this.raw = node;
    this.id = idKey(node["#id"]);
    this.name = node.materialName || node.name || `mat_${this.id}`;
    this.parent = idKey(node.parentMaterialKey);
    this.fx = node.fxName || null;
    const tex = Object.assign({}, node.textures || {});
    const cnt = node.configCount;
    if (Number.isInteger(cnt) && cnt > 0) {
      const cfgs = [];
      for (let i = 0; i < cnt; i++) { const c = node[`configArchive_${i}`]; if (c && typeof c === "object") cfgs.push(c); }
      const pick = cfgs.find(c => c.configName === "Default") || cfgs[0];
      if (pick) { Object.assign(tex, pick.textures || {}); this.fx = this.fx || pick.fxName || null; }
    }
    this.textures = {};
    for (const [k, v] of Object.entries(tex)) if (typeof v === "string") this.textures[k] = v;
  }
}

class Scene {
  constructor(sc2, scg) {
    this.sc2 = sc2 || null; this.scg = scg || null;
    this.meshes = new Map(); this.materials = new Map(); this.otherNodes = [];
    this.warnings = [];
    if (scg) for (const pg of scg.polygonGroups) { const m = new Mesh(pg, "scg"); if (m.id !== null) this.meshes.set(m.id, m); }
    this.roots = [];
    if (sc2) {
      for (const dn of sc2.dataNodes) {
        const kind = dn["##name"];
        if (kind === "PolygonGroup") { const m = new Mesh(dn, "sc2"); if (m.id !== null) this.meshes.set(m.id, m); }
        else if (kind === "NMaterial" || "materialName" in dn) { const mt = new Material(dn); if (mt.id !== null) this.materials.set(mt.id, mt); }
        else this.otherNodes.push(dn);
      }
      this.roots = sc2.hierarchy.filter(e => e && typeof e === "object").map(e => new Entity(e, null));
    }
    for (const m of this.meshes.values()) if (m.warning) this.warnings.push(`PolygonGroup ${m.id}: ${m.warning}`);
    let missing = 0;
    for (const e of this.entities()) for (const b of e.batches) if (b.datasource !== null && !this.meshes.has(b.datasource)) missing++;
    this.missingGeometry = missing;
  }
  *entities() { for (const r of this.roots) yield* r.walk(); }
  materialChain(mid) {
    const out = [], seen = new Set();
    while (mid !== null && mid !== undefined && this.materials.has(mid) && !seen.has(mid)) {
      seen.add(mid); const m = this.materials.get(mid); out.push(m); mid = m.parent;
    }
    return out;
  }
  texturesFor(mid) {
    const tex = {};
    for (const m of this.materialChain(mid).reverse()) Object.assign(tex, m.textures);
    return tex;
  }
  materialName(mid) {
    const ch = this.materialChain(mid);
    return ch.length ? ch[ch.length - 1].name : (mid !== null ? `mat_${mid}` : "нет");
  }
  materialFx(mid) {
    for (const m of this.materialChain(mid)) if (m.fx) return m.fx;
    return "";
  }
  lods() {
    const s = new Set();
    for (const e of this.entities()) for (const b of e.batches) s.add(b.lod);
    return [...s].sort((a, b) => a - b);
  }
  /** Helper geometry hidden by default: hard points, shadow volumes, destroyed variants. */
  isService(entity, batch) {
    const n = entity ? entity.name : "";
    if (/^HP_/i.test(n) || /_crash/i.test(n) || /dummy/i.test(n)) return true;
    if (batch) {
      const fx = this.materialFx(batch.material), nm = this.materialName(batch.material);
      if (/ShadowVolume/i.test(fx) || /^Shadow_Material/i.test(nm)) return true;
    }
    return false;
  }
  /** -> [{entity, batch, mesh}] for the chosen LOD (null = all LODs). */
  drawList(lod = 0, includeService = false) {
    const out = [];
    if (!this.roots.length) {
      for (const m of this.meshes.values()) out.push({ entity: null, batch: null, mesh: m });
      return out;
    }
    for (const e of this.entities()) {
      for (const b of e.batches) {
        if (lod !== null && b.lod !== lod && b.lod !== -1) continue;
        const m = b.datasource !== null ? this.meshes.get(b.datasource) : null;
        if (!m) continue;
        if (!includeService && this.isService(e, b)) continue;
        out.push({ entity: e, batch: b, mesh: m });
      }
    }
    return out;
  }
}

// ---------------------------------------------------------------- textures
function rgb565(c, out, o) {
  out[o] = ((c >> 11) & 31) * 255 / 31 | 0;
  out[o + 1] = ((c >> 5) & 63) * 255 / 63 | 0;
  out[o + 2] = (c & 31) * 255 / 31 | 0;
}

function decodeBC1Block(src, p, pal, forceFour) {
  const c0 = src[p] | (src[p + 1] << 8), c1 = src[p + 2] | (src[p + 3] << 8);
  rgb565(c0, pal, 0); rgb565(c1, pal, 4); pal[3] = pal[7] = 255;
  if (c0 > c1 || forceFour) {
    for (let k = 0; k < 3; k++) { pal[8 + k] = (2 * pal[k] + pal[4 + k]) / 3 | 0; pal[12 + k] = (pal[k] + 2 * pal[4 + k]) / 3 | 0; }
    pal[11] = pal[15] = 255;
  } else {
    for (let k = 0; k < 3; k++) { pal[8 + k] = (pal[k] + pal[4 + k]) >> 1; pal[12 + k] = 0; }
    pal[11] = 255; pal[15] = 0;
  }
}

function decodeAlphaBlock(src, p, out /* 16 values */) {
  const a0 = src[p], a1 = src[p + 1];
  const pal = [a0, a1];
  if (a0 > a1) for (let k = 1; k < 7; k++) pal.push(((7 - k) * a0 + k * a1) / 7 | 0);
  else { for (let k = 1; k < 5; k++) pal.push(((5 - k) * a0 + k * a1) / 5 | 0); pal.push(0, 255); }
  const lo = src[p + 2] | (src[p + 3] << 8) | (src[p + 4] << 16);
  const hi = src[p + 5] | (src[p + 6] << 8) | (src[p + 7] << 16);
  for (let k = 0; k < 8; k++) out[k] = pal[(lo >> (3 * k)) & 7];
  for (let k = 0; k < 8; k++) out[8 + k] = pal[(hi >> (3 * k)) & 7];
}

// ---- BC7 (BPTC).  Port of bcdec by Sergii Kudlai (MIT / Unlicense).
// Partition tables: one digit per texel = subset (0..2), +3 = anchor (fix-up) texel.
const BC7_P2 = "3011001100110014300100010001000431110111011101143001001100110114300000010001001430110111011111143001001101111114300000010011011430000000000100143011011111111114300000010111111430000000000101143001011111111114300000001111111430001111111111143000000000001114300010001110111431410001000000003000000040001110314100110001000030410001000000003000100041001110300000004000110031110011001100043041000100010000300010004000110031400110011001103041011001101100300101114110100030001111411100003141000110001110304110011001110031010101010101043000111100001114310110400101101030110011410011003041110000111100310101014010101031101001011010043101101010100104314100111100111030010011410010003041001001001100304110111101110031401001100101103011110011000014311001101001100430000140011000003100114001000000304001110010000030000040011100103000010041100100311011001001001430110110110010043140001110011100304110011100011031101100110010043110001100111004311111101000000430011000111001143000111100110014304100111111000030400010111011103100010001110114";
const BC7_P3 = "3014001102212225300400115211222130002001521122143225002200110114300000004122112530140011002200253025002211111114301100115211221430000000411122253000111141112225300011412222222530120042001200153112014201120115312204220122012530140112112212253014200152002220300400110112112531140011500122003000112241221125302500220022111431140111022202253004000152212221300000410122012530001100524022103125042200110000301200124122222531101251422101103000014012511221302211024102002531100410200222253011012201520014300020005211222430000002412212253225002200120014301400120022022531200420015001203000114122520000312012015042012031202012450101203011220011520014301111522200001431040101222222253000000051212124302214220022112530250011002200143220125102201224310122522222010430002121512121243104010101012225322501110222011430021412000211153000241221122115322204110111022530021112411200053110041001102225300000002142211531100410222222253022001100410025302211224122002530000000000024153005000100020004322212220222422531042222222222253114201152012220";
const BC7_W = [null, null, [0, 21, 43, 64], [0, 9, 18, 27, 37, 46, 55, 64], [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64]];
const BC7_CB = [4, 6, 5, 7, 5, 7, 7, 5], BC7_AB = [0, 0, 0, 0, 6, 8, 7, 5];
const BC7_PBITS = 0b11001011;

function decodeBC7Block(src, p, out, w, h, bx, by) {
  let pos = 0;
  const rb = n => { let v = 0; for (let i = 0; i < n; i++, pos++) v |= ((src[p + (pos >> 3)] >> (pos & 7)) & 1) << i; return v; };
  let mode = 0;
  while (mode < 8 && rb(1) === 0) mode++;
  const put = (x, y, r, g, b, a) => {
    const px = bx * 4 + x, py = by * 4 + y;
    if (px >= w || py >= h) return;
    const o = (py * w + px) * 4; out[o] = r; out[o + 1] = g; out[o + 2] = b; out[o + 3] = a;
  };
  if (mode >= 8) { for (let k = 0; k < 16; k++) put(k & 3, k >> 2, 0, 0, 0, 0); return; }
  let parts = 1, partition = 0, rotation = 0, isb = 0;
  if (mode === 0 || mode === 1 || mode === 2 || mode === 3 || mode === 7) { parts = (mode === 0 || mode === 2) ? 3 : 2; partition = rb(mode === 0 ? 4 : 6); }
  if (mode === 4 || mode === 5) { rotation = rb(2); if (mode === 4) isb = rb(1); }
  const ne = parts * 2, ep = [];
  for (let j = 0; j < ne; j++) ep.push([0, 0, 0, 0]);
  for (let c = 0; c < 3; c++) for (let j = 0; j < ne; j++) ep[j][c] = rb(BC7_CB[mode]);
  if (BC7_AB[mode]) for (let j = 0; j < ne; j++) ep[j][3] = rb(BC7_AB[mode]);
  const hasP = (BC7_PBITS >> mode) & 1;
  if (hasP) {
    for (const e of ep) for (let c = 0; c < 4; c++) e[c] <<= 1;
    if (mode === 1) { const a = rb(1), b = rb(1); for (let c = 0; c < 3; c++) { ep[0][c] |= a; ep[1][c] |= a; ep[2][c] |= b; ep[3][c] |= b; } }
    else for (const e of ep) { const b = rb(1); for (let c = 0; c < 4; c++) e[c] |= b; }
  }
  for (const e of ep) {
    let n = BC7_CB[mode] + hasP;
    for (let c = 0; c < 3; c++) { e[c] = (e[c] << (8 - n)) & 255; e[c] |= e[c] >> n; }
    if (BC7_AB[mode]) { n = BC7_AB[mode] + hasP; e[3] = (e[3] << (8 - n)) & 255; e[3] |= e[3] >> n; }
    else e[3] = 255;
  }
  const ib = (mode === 0 || mode === 1) ? 3 : (mode === 6 ? 4 : 2);
  const ib2 = mode === 4 ? 3 : (mode === 5 ? 2 : 0);
  const W = BC7_W[ib], W2 = ib2 ? BC7_W[ib2] : null;
  const tab = parts === 2 ? BC7_P2 : parts === 3 ? BC7_P3 : null;
  const subset = new Uint8Array(16), idx = new Uint8Array(16);
  for (let k = 0; k < 16; k++) {
    let d = tab ? tab.charCodeAt(partition * 16 + k) - 48 : (k ? 0 : 3);
    const anchor = d >= 3; if (anchor) d -= 3;
    subset[k] = d;
    idx[k] = rb(anchor ? ib - 1 : ib);
  }
  const lerp = (a, b, wt, i) => (a * (64 - wt[i]) + b * wt[i] + 32) >> 6;
  for (let k = 0; k < 16; k++) {
    const e0 = ep[subset[k] * 2], e1 = ep[subset[k] * 2 + 1], i = idx[k];
    let r, g, b, a;
    if (!ib2) { r = lerp(e0[0], e1[0], W, i); g = lerp(e0[1], e1[1], W, i); b = lerp(e0[2], e1[2], W, i); a = lerp(e0[3], e1[3], W, i); }
    else {
      const i2 = rb(k ? ib2 : ib2 - 1);
      if (!isb) { r = lerp(e0[0], e1[0], W, i); g = lerp(e0[1], e1[1], W, i); b = lerp(e0[2], e1[2], W, i); a = lerp(e0[3], e1[3], W2, i2); }
      else { r = lerp(e0[0], e1[0], W2, i2); g = lerp(e0[1], e1[1], W2, i2); b = lerp(e0[2], e1[2], W2, i2); a = lerp(e0[3], e1[3], W, i); }
    }
    if (rotation === 1) [a, r] = [r, a]; else if (rotation === 2) [a, g] = [g, a]; else if (rotation === 3) [a, b] = [b, a];
    put(k & 3, k >> 2, r, g, b, a);
  }
}

function decodeBC7(src, w, h) {
  const out = new Uint8Array(w * h * 4);
  const bw = Math.max(1, (w + 3) >> 2), bh = Math.max(1, (h + 3) >> 2);
  for (let by = 0, p = 0; by < bh; by++) for (let bx = 0; bx < bw; bx++, p += 16) {
    if (p + 16 > src.length) return out;
    decodeBC7Block(src, p, out, w, h, bx, by);
  }
  return out;
}

/** BCn -> RGBA8.  kind: 1..5, 7 */
function decodeBC(src, w, h, kind) {
  if (kind === 7) return decodeBC7(src, w, h);
  const out = new Uint8Array(w * h * 4);
  const bw = Math.max(1, (w + 3) >> 2), bh = Math.max(1, (h + 3) >> 2);
  const blockSize = kind === 1 || kind === 4 ? 8 : 16;
  const pal = new Uint8Array(16), A = new Array(16), B = new Array(16);
  let p = 0;
  for (let by = 0; by < bh; by++) for (let bx = 0; bx < bw; bx++, p += blockSize) {
    if (p + blockSize > src.length) return out;
    let colorAt = -1;
    if (kind === 1) colorAt = p;
    else if (kind === 2) { for (let k = 0; k < 16; k++) { const v = (src[p + (k >> 1)] >> ((k & 1) * 4)) & 15; A[k] = v * 17; } colorAt = p + 8; }
    else if (kind === 3) { decodeAlphaBlock(src, p, A); colorAt = p + 8; }
    else if (kind === 4) decodeAlphaBlock(src, p, A);
    else if (kind === 5) { decodeAlphaBlock(src, p, A); decodeAlphaBlock(src, p + 8, B); }
    let idx = 0;
    if (colorAt >= 0) { decodeBC1Block(src, colorAt, pal, kind !== 1); idx = (src[colorAt + 4] | (src[colorAt + 5] << 8) | (src[colorAt + 6] << 16) | (src[colorAt + 7] << 24)) >>> 0; }
    for (let k = 0; k < 16; k++) {
      const x = bx * 4 + (k & 3), y = by * 4 + (k >> 2);
      if (x >= w || y >= h) continue;
      const o = (y * w + x) * 4;
      if (kind <= 3) {
        const ci = ((idx >>> (2 * k)) & 3) * 4;
        out[o] = pal[ci]; out[o + 1] = pal[ci + 1]; out[o + 2] = pal[ci + 2];
        out[o + 3] = kind === 1 ? pal[ci + 3] : A[k];
      } else if (kind === 4) { out[o] = out[o + 1] = out[o + 2] = A[k]; out[o + 3] = 255; }
      else {
        const nx = A[k] / 127.5 - 1, ny = B[k] / 127.5 - 1;
        const nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
        out[o] = A[k]; out[o + 1] = B[k]; out[o + 2] = (nz * 0.5 + 0.5) * 255 | 0; out[o + 3] = 255;
      }
    }
  }
  return out;
}

function decodeMasked(src, off, w, h, bpp, masks) {
  const out = new Uint8Array(w * h * 4);
  const bytes = bpp >> 3;
  const shifts = masks.map(m => { if (!m) return [0, 0]; let s = 0; while (!((m >>> s) & 1)) s++; let n = 0; while ((m >>> (s + n)) & 1) n++; return [s, n]; });
  for (let i = 0; i < w * h; i++) {
    const p = off + i * bytes;
    if (p + bytes > src.length) break;
    let v = 0;
    for (let b = 0; b < bytes; b++) v |= src[p + b] << (8 * b);
    v >>>= 0;
    for (let c = 0; c < 4; c++) {
      const m = masks[c];
      if (!m) { out[i * 4 + c] = c === 3 ? 255 : 0; continue; }
      const [s, n] = shifts[c];
      out[i * 4 + c] = Math.round((((v & m) >>> 0) >>> s) * 255 / ((1 << n) - 1));
    }
  }
  return out;
}

const DXGI_BC = { 70: 1, 71: 1, 72: 1, 73: 2, 74: 2, 75: 2, 76: 3, 77: 3, 78: 3, 79: 4, 80: 4, 81: 4, 82: 5, 83: 5, 84: 5, 97: 7, 98: 7, 99: 7 };

function decodeDDS(u8) {
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  if (cstr(u8.subarray(0, 4)) !== "DDS ") throw new DavaError("не DDS");
  const h = dv.getUint32(12, true), w = dv.getUint32(16, true);
  const pfFlags = dv.getUint32(80, true);
  const fourcc = String.fromCharCode(u8[84], u8[85], u8[86], u8[87]);
  const bpp = dv.getUint32(88, true);
  let off = 128;
  if (pfFlags & 4) {
    if (fourcc === "DX10") {
      const dxgi = dv.getUint32(128, true); off = 148;
      if (DXGI_BC[dxgi]) return { width: w, height: h, data: decodeBC(u8.subarray(off), w, h, DXGI_BC[dxgi]), format: "DX10 BC" + DXGI_BC[dxgi] };
      if (dxgi === 28 || dxgi === 29) return { width: w, height: h, data: u8.slice(off, off + w * h * 4), format: "RGBA8" };
      if (dxgi === 87 || dxgi === 88 || dxgi === 91) return { width: w, height: h, data: decodeMasked(u8, off, w, h, 32, [0xFF0000, 0xFF00, 0xFF, dxgi === 88 ? 0 : 0xFF000000]), format: "BGRA8" };
      throw new DavaError("DDS DX10: формат " + dxgi + " не поддерживается");
    }
    const kind = { DXT1: 1, DXT2: 2, DXT3: 2, DXT4: 3, DXT5: 3, ATI1: 4, BC4U: 4, ATI2: 5, BC5U: 5 }[fourcc];
    if (!kind) throw new DavaError("DDS: FourCC " + fourcc + " не поддерживается");
    let data = decodeBC(u8.subarray(off), w, h, kind);
    let format = fourcc;
    if (kind === 3 && (pfFlags & 0x80000000)) format = "DXT5 (2 канала)";
    return { width: w, height: h, data, format };
  }
  const masks = [dv.getUint32(92, true), dv.getUint32(96, true), dv.getUint32(100, true), (pfFlags & 1) ? dv.getUint32(104, true) : 0];
  if (pfFlags & 0x20000 && !(pfFlags & 0x40)) { // luminance
    return { width: w, height: h, data: decodeMasked(u8, off, w, h, bpp, [masks[0], masks[0], masks[0], masks[3]]), format: "L" + bpp };
  }
  return { width: w, height: h, data: decodeMasked(u8, off, w, h, bpp, masks), format: "RGB" + bpp };
}

// ---- ETC1 / ETC2 (+EAC alpha)
const ETC_MOD = [[2, 8], [5, 17], [9, 29], [13, 42], [18, 60], [24, 80], [33, 106], [47, 183]];
const ETC_DIST = [3, 6, 11, 16, 23, 32, 41, 64];
const clamp255 = v => v < 0 ? 0 : v > 255 ? 255 : v;
const ext4 = v => (v << 4) | v, ext5 = v => (v << 3) | (v >> 2);

function etcBlock(b, out, w, h, bx, by, punch) {
  const hi = ((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]) >>> 0;
  const lo = ((b[4] << 24) | (b[5] << 16) | (b[6] << 8) | b[7]) >>> 0;
  const put = (x, y, r, g, bb, a = 255) => {
    const px = bx * 4 + x, py = by * 4 + y;
    if (px >= w || py >= h) return;
    const o = (py * w + px) * 4; out[o] = clamp255(r); out[o + 1] = clamp255(g); out[o + 2] = clamp255(bb); out[o + 3] = a;
  };
  const pixIdx = (x, y) => { const k = x * 4 + y; return { msb: (lo >>> (16 + k)) & 1, lsb: (lo >>> k) & 1 }; };
  const diff = (hi >>> 1) & 1;
  const flip = hi & 1;
  let base1, base2;
  if (!diff && !punch) {
    base1 = [ext4(hi >>> 28 & 15), ext4(hi >>> 20 & 15), ext4(hi >>> 12 & 15)];
    base2 = [ext4(hi >>> 24 & 15), ext4(hi >>> 16 & 15), ext4(hi >>> 8 & 15)];
  } else {
    const r = hi >>> 27 & 31, g = hi >>> 19 & 31, bl = hi >>> 11 & 31;
    const sx = v => (v & 4) ? v - 8 : v;
    const dr = sx(hi >>> 24 & 7), dg = sx(hi >>> 16 & 7), db = sx(hi >>> 8 & 7);
    const r2 = r + dr, g2 = g + dg, b2 = bl + db;
    if (r2 < 0 || r2 > 31) {        // T mode (ETC2)
      const R1 = ext4(((b[0] >> 1) & 12) | (b[0] & 3)), G1 = ext4(b[1] >> 4), B1 = ext4(b[1] & 15);
      const R2 = ext4(b[2] >> 4), G2 = ext4(b[2] & 15), B2 = ext4(b[3] >> 4);
      const d = ETC_DIST[((b[3] >> 1) & 6) | (b[3] & 1)];
      const pal = [[R1, G1, B1], [R2 + d, G2 + d, B2 + d], [R2, G2, B2], [R2 - d, G2 - d, B2 - d]];
      for (let x = 0; x < 4; x++) for (let y = 0; y < 4; y++) { const p = pixIdx(x, y); const i = p.msb * 2 + p.lsb; if (punch && i === 2) put(x, y, 0, 0, 0, 0); else put(x, y, ...pal[i]); }
      return;
    }
    if (g2 < 0 || g2 > 31) {        // H mode
      const R1 = ext4((b[0] >> 3) & 15), G1 = ext4(((b[0] & 7) << 1) | ((b[1] >> 4) & 1));
      const B1 = ext4((b[1] & 8) | ((b[1] & 3) << 1) | (b[2] >> 7));
      const R2 = ext4((b[2] >> 3) & 15), G2 = ext4(((b[2] & 7) << 1) | (b[3] >> 7)), B2 = ext4((b[3] >> 3) & 15);
      const v1 = (R1 << 16) | (G1 << 8) | B1, v2 = (R2 << 16) | (G2 << 8) | B2;
      const d = ETC_DIST[(b[3] & 4) | ((b[3] & 1) << 1) | (v1 >= v2 ? 1 : 0)];
      const pal = [[R1 + d, G1 + d, B1 + d], [R1 - d, G1 - d, B1 - d], [R2 + d, G2 + d, B2 + d], [R2 - d, G2 - d, B2 - d]];
      for (let x = 0; x < 4; x++) for (let y = 0; y < 4; y++) { const p = pixIdx(x, y); const i = p.msb * 2 + p.lsb; if (punch && i === 2) put(x, y, 0, 0, 0, 0); else put(x, y, ...pal[i]); }
      return;
    }
    if (b2 < 0 || b2 > 31) {        // planar mode
      const ro = (b[0] >> 1) & 63, go = ((b[0] & 1) << 6) | ((b[1] >> 1) & 63);
      const bo = ((b[1] & 1) << 5) | ((b[2] & 0x18)) | ((b[2] & 3) << 1) | (b[3] >> 7);
      const rh = ((b[3] >> 1) & 62) | (b[3] & 1), gh = b[4] >> 1, bh = ((b[4] & 1) << 5) | (b[5] >> 3);
      const rv = ((b[5] & 7) << 3) | (b[6] >> 5), gv = ((b[6] & 31) << 2) | (b[7] >> 6), bv = b[7] & 63;
      const e6 = v => (v << 2) | (v >> 4), e7 = v => (v << 1) | (v >> 6);
      const O = [e6(ro), e7(go), e6(bo)], H = [e6(rh), e7(gh), e6(bh)], V = [e6(rv), e7(gv), e6(bv)];
      for (let x = 0; x < 4; x++) for (let y = 0; y < 4; y++)
        put(x, y, ...[0, 1, 2].map(c => (x * (H[c] - O[c]) + y * (V[c] - O[c]) + 4 * O[c] + 2) >> 2));
      return;
    }
    base1 = [ext5(r), ext5(g), ext5(bl)];
    base2 = [ext5(r2), ext5(g2), ext5(b2)];
  }
  const t1 = hi >>> 5 & 7, t2 = hi >>> 2 & 7;
  for (let x = 0; x < 4; x++) for (let y = 0; y < 4; y++) {
    const second = flip ? y >= 2 : x >= 2;
    const base = second ? base2 : base1, tab = ETC_MOD[second ? t2 : t1];
    const p = pixIdx(x, y);
    if (punch && !diff && p.msb === 1 && p.lsb === 0) { put(x, y, 0, 0, 0, 0); continue; }
    let m = p.lsb ? tab[1] : tab[0];
    if (punch && !diff && p.lsb === 0) m = 0;
    if (p.msb) m = -m;
    put(x, y, base[0] + m, base[1] + m, base[2] + m);
  }
}

const EAC_MOD = [[-3, -6, -9, -15, 2, 5, 8, 14], [-3, -7, -10, -13, 2, 6, 9, 12], [-2, -5, -8, -13, 1, 4, 7, 12],
  [-2, -4, -6, -13, 1, 3, 5, 12], [-3, -6, -8, -12, 2, 5, 7, 11], [-3, -7, -9, -11, 2, 6, 8, 10],
  [-4, -7, -8, -11, 3, 6, 7, 10], [-3, -5, -8, -11, 2, 4, 7, 10], [-2, -6, -8, -10, 1, 5, 7, 9],
  [-2, -5, -8, -10, 1, 4, 7, 9], [-2, -4, -8, -10, 1, 3, 7, 9], [-2, -5, -7, -10, 1, 4, 6, 9],
  [-3, -4, -7, -10, 2, 3, 6, 9], [-1, -2, -3, -10, 0, 1, 2, 9], [-4, -6, -8, -9, 3, 5, 7, 8], [-3, -5, -7, -9, 2, 4, 6, 8]];

function eacAlpha(b, out, w, h, bx, by) {
  const base = b[0], mul = b[1] >> 4, tab = EAC_MOD[b[1] & 15];
  let bits = 0n;
  for (let k = 2; k < 8; k++) bits = (bits << 8n) | BigInt(b[k]);
  for (let x = 0; x < 4; x++) for (let y = 0; y < 4; y++) {
    const k = x * 4 + y;
    const i = Number((bits >> BigInt(45 - 3 * k)) & 7n);
    const px = bx * 4 + x, py = by * 4 + y;
    if (px >= w || py >= h) continue;
    out[(py * w + px) * 4 + 3] = clamp255(base + tab[i] * mul);
  }
}

function decodeETC(src, w, h, mode /* "etc1"|"etc2"|"etc2a"|"etc2a1" */) {
  const out = new Uint8Array(w * h * 4);
  const bw = (w + 3) >> 2, bh = (h + 3) >> 2, bs = mode === "etc2a" ? 16 : 8;
  let p = 0;
  for (let by = 0; by < bh; by++) for (let bx = 0; bx < bw; bx++, p += bs) {
    if (p + bs > src.length) return out;
    if (mode === "etc2a") { etcBlock(src.subarray(p + 8, p + 16), out, w, h, bx, by, false); eacAlpha(src.subarray(p, p + 8), out, w, h, bx, by); }
    else etcBlock(src.subarray(p, p + 8), out, w, h, bx, by, mode === "etc2a1");
  }
  return out;
}

function decodePVR(u8) {
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  if (dv.getUint32(0, true) !== 0x03525650) throw new DavaError("не PVR3");
  const pfLo = dv.getUint32(8, true), pfHi = dv.getUint32(12, true);
  const h = dv.getUint32(24, true), w = dv.getUint32(28, true);
  const meta = dv.getUint32(48, true);
  const off = 52 + meta;
  const src = u8.subarray(off);
  let data, format;
  if (pfHi === 0) {
    const bc = { 7: 1, 8: 2, 9: 2, 10: 3, 11: 3, 12: 4, 13: 5, 15: 7 }[pfLo];
    if (bc) { data = decodeBC(src, w, h, bc); format = "BC" + bc; }
    else if (pfLo === 6) { data = decodeETC(src, w, h, "etc1"); format = "ETC1"; }
    else if (pfLo === 22) { data = decodeETC(src, w, h, "etc2"); format = "ETC2"; }
    else if (pfLo === 23) { data = decodeETC(src, w, h, "etc2a"); format = "ETC2+EAC"; }
    else if (pfLo === 24) { data = decodeETC(src, w, h, "etc2a1"); format = "ETC2 A1"; }
    else throw new DavaError("PVR: сжатый формат " + pfLo + " не поддерживается (PVRTC/ASTC)");
  } else {
    const names = [], bits = [];
    for (let i = 0; i < 4; i++) {
      const ch = (pfLo >>> (8 * i)) & 255, bt = (pfHi >>> (8 * i)) & 255;
      if (ch && bt) { names.push(String.fromCharCode(ch)); bits.push(bt); }
    }
    const total = bits.reduce((a, b) => a + b, 0);
    const bytes = total >> 3;
    data = new Uint8Array(w * h * 4);
    const all8 = bits.every(b => b === 8);
    for (let i = 0; i < w * h; i++) {
      const p = i * bytes;
      if (p + bytes > src.length) break;
      const o = i * 4;
      data[o + 3] = 255;
      if (all8) {
        names.forEach((n, c) => { const t = "rgba".indexOf(n); if (t >= 0) data[o + t] = src[p + c]; else if (n === "l") data[o] = data[o + 1] = data[o + 2] = src[p + c]; });
      } else {
        let v = 0; for (let b = 0; b < bytes; b++) v |= src[p + b] << (8 * b); v >>>= 0;
        let shift = total;
        names.forEach((n, c) => {
          shift -= bits[c];
          const x = Math.round(((v >>> shift) & ((1 << bits[c]) - 1)) * 255 / ((1 << bits[c]) - 1));
          const t = "rgba".indexOf(n); if (t >= 0) data[o + t] = x; else if (n === "l") data[o] = data[o + 1] = data[o + 2] = x;
        });
      }
    }
    format = names.join("") + bits.join("");
  }
  // PVR images in Blitz are stored bottom-up relative to DDS
  const row = w * 4, tmp = new Uint8Array(row);
  for (let y = 0; y < h >> 1; y++) {
    const a = y * row, b = (h - 1 - y) * row;
    tmp.set(data.subarray(a, a + row)); data.copyWithin(a, b, b + row); data.set(tmp, b);
  }
  return { width: w, height: h, data, format: "PVR " + format };
}

/** Decode a game texture from bytes (DVPL is unwrapped automatically).
 *  Returns {width,height,data(RGBA8),format} or {browserImage:true} for png/jpg/webp. */
function decodeTexture(u8) {
  u8 = decodeDVPL(u8);
  if (u8[0] === 0x44 && u8[1] === 0x44 && u8[2] === 0x53 && u8[3] === 0x20) return decodeDDS(u8);
  if (u8[0] === 0x50 && u8[1] === 0x56 && u8[2] === 0x52 && u8[3] === 3) return decodePVR(u8);
  if ((u8[0] === 0x89 && u8[1] === 0x50) || (u8[0] === 0xFF && u8[1] === 0xD8) || (u8[0] === 0x52 && u8[1] === 0x49))
    return { browserImage: true, bytes: u8 };
  throw new DavaError("неизвестный формат текстуры");
}

// ---------------------------------------------------------------- file index / texture lookup
const IMG_EXT = ["dds", "pvr", "png", "webp", "jpg", "jpeg", "ktx"];
const GPU_TAGS = ["dx11", "dx9", "mali", "adreno", "tegra", "pvr", "png"];
const EXT_SCORE = { dds: 0, pvr: 2, png: 1, webp: 1, jpg: 1, jpeg: 1, ktx: 3 };

function normPath(p) {
  p = String(p).replace(/\\/g, "/").replace(/^~res:\/*/i, "").replace(/^\.\//, "");
  const out = [];
  for (const part of p.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") { if (out.length && out[out.length - 1] !== "..") out.pop(); else out.push(".."); }
    else out.push(part);
  }
  return out.join("/");
}
function dirOf(p) { const i = p.lastIndexOf("/"); return i >= 0 ? p.slice(0, i) : ""; }
function baseOf(p) { const i = p.lastIndexOf("/"); return i >= 0 ? p.slice(i + 1) : p; }

/** Index of user-supplied files: path -> handle (File, Uint8Array, url...). */
class FileIndex {
  constructor() { this.files = new Map(); this.texStems = new Map(); }
  add(path, handle) {
    path = normPath(path);
    this.files.set(path.toLowerCase(), { path, handle });
    let low = path.toLowerCase();
    if (low.endsWith(".dvpl")) low = low.slice(0, -5);
    const m = /\.([a-z0-9]+)$/.exec(low);
    if (!m || !IMG_EXT.includes(m[1])) return;
    let stem = low.slice(0, -m[0].length);
    let score = EXT_SCORE[m[1]] * 10;
    const reg = (s, sc) => {
      const list = this.texStems.get(s) || [];
      list.push({ path, handle, score: sc });
      list.sort((a, b) => a.score - b.score);
      this.texStems.set(s, list);
    };
    reg(stem, score + 1);
    const g = /\.([a-z0-9]+)$/.exec(stem);
    if (g && GPU_TAGS.includes(g[1])) reg(stem.slice(0, -g[0].length), score + (g[1] === "dx11" ? 0 : 2));
  }
  get(path) { return this.files.get(normPath(path).toLowerCase()) || null; }
  list() { return [...this.files.values()]; }
  models() { return this.list().filter(f => /\.(sc2|scg)(\.dvpl)?$/i.test(f.path)); }
  /** paired file for a model: same folder+stem, else same name anywhere */
  pair(path, ext) {
    const p = normPath(path).toLowerCase().replace(/\.dvpl$/, "");
    const stem = p.replace(/\.(sc2|scg)$/, "");
    for (const c of [stem + "." + ext, stem + "." + ext + ".dvpl"]) { const f = this.files.get(c); if (f) return f; }
    const b = baseOf(stem);
    for (const f of this.files.values()) {
      const q = f.path.toLowerCase().replace(/\.dvpl$/, "");
      if (baseOf(q) === b + "." + ext) return f;
    }
    return null;
  }
  /** texture path from a material (relative to the model folder or ~res:/) */
  findTexture(texPath, modelPath) {
    let t = String(texPath).replace(/\\/g, "/");
    const isRes = /^~res:/i.test(t);
    t = normPath(t).toLowerCase().replace(/\.tex$/, "");
    const tries = [];
    if (!isRes && modelPath) tries.push(normPath(dirOf(normPath(modelPath)) + "/" + t).toLowerCase());
    tries.push(t);
    for (const s of tries) { const l = this.texStems.get(s); if (l) return l[0]; }
    // suffix match: longest common tail wins
    const want = (tries[0] || t).split("/");
    let best = null, bestScore = -1;
    for (const [stem, list] of this.texStems) {
      const parts = stem.split("/");
      if (parts[parts.length - 1] !== want[want.length - 1]) continue;
      let k = 1;
      while (k < parts.length && k < want.length && parts[parts.length - 1 - k] === want[want.length - 1 - k]) k++;
      const sc = k * 100 - list[0].score;
      if (sc > bestScore) { bestScore = sc; best = list[0]; }
    }
    return best;
  }
}

// ---------------------------------------------------------------- JSON dump helper
function toJSONSafe(v, depth = 0) {
  if (v === null || v === undefined) return v ?? null;
  if (typeof v === "bigint") return v.toString();
  if (v instanceof Uint8Array) return `<${v.length} байт>`;
  if (Array.isArray(v)) return v.map(x => toJSONSafe(x, depth + 1));
  if (typeof v === "object") { const o = {}; for (const [k, x] of Object.entries(v)) o[k] = toJSONSafe(x, depth + 1); return o; }
  if (typeof v === "number" && !Number.isFinite(v)) return String(v);
  return v;
}

/** Load a model from raw bytes. */
function openModel(sc2Bytes, scgBytes) {
  const sc2 = sc2Bytes ? readSC2(decodeDVPL(sc2Bytes)) : null;
  const scg = scgBytes ? readSCG(decodeDVPL(scgBytes)) : null;
  return new Scene(sc2, scg);
}

return { DavaError, lz4Block, crc32, isDVPL, decodeDVPL, Reader, readArchive, readValue, idKey,
  vertexLayout, Mesh, readSCG, readSC2, matMul, trsMatrix, transformPoint, IDENTITY, components,
  component, Entity, Material, Scene, decodeBC, decodeDDS, decodePVR, decodeETC, decodeTexture,
  normPath, dirOf, baseOf, FileIndex, toJSONSafe, openModel, PRIM_NAMES, T };
})();
if (typeof module !== "undefined") module.exports = Dava;

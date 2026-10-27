// Node tests for the viewer core (src/core.js).  Run:  node tools/scgviewer/tests/test_core.js [extra.sc2 ...]
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const D = require("../src/core.js");

const FIX = path.join(__dirname, "fixtures");
const rd = p => new Uint8Array(fs.readFileSync(p));
let ok = 0;
const test = (name, fn) => { fn(); ok++; console.log("ok -", name); };

test("DVPL LZ4 + CRC", () => {
  const raw = rd(path.join(FIX, "demo_tank.scg.dvpl"));
  assert(D.isDVPL(raw));
  const dec = D.decodeDVPL(raw);
  assert.strictEqual(String.fromCharCode(...dec.subarray(0, 4)), "SCPG");
  const bad = raw.slice(); bad[10] ^= 255;
  assert.throws(() => D.decodeDVPL(bad), /CRC/);
});

const scene = D.openModel(rd(path.join(FIX, "demo_tank.sc2.dvpl")), rd(path.join(FIX, "demo_tank.scg.dvpl")));

test("SC2 modern layout (KA v2 body)", () => {
  assert.strictEqual(scene.sc2.version, 43);
  assert.strictEqual(scene.sc2.layout, "modern");
  assert.strictEqual(scene.sc2.trailing, 0);
  assert.strictEqual(scene.meshes.size, 7);
  assert.strictEqual(scene.materials.size, 6);
  assert.deepStrictEqual(scene.lods(), [-1, 0, 1]);
  assert.strictEqual(scene.missingGeometry, 0);
});

test("hierarchy transforms match stored world TRS", () => {
  for (const e of scene.entities()) {
    const tc = D.component(e.raw, "TransformComponent");
    const w = D.trsMatrix(tc["tc.worldTranslation"], tc["tc.worldRotation"], tc["tc.worldScale"]);
    const err = Math.max(...w.map((v, i) => Math.abs(v - e.world[i])));
    assert(err < 1e-5, `${e.name}: ${err}`);
  }
  const gun = [...scene.entities()].find(e => e.name === "gun_01");
  assert(Math.abs(gun.world[12] + 0.5) < 1e-5 && Math.abs(gun.world[13] - 0.466) < 1e-3);
});

test("draw list: LOD filter, -1 = all LODs, service objects hidden", () => {
  const names = l => l.map(it => it.entity.name + ":" + it.mesh.id);
  const lod0 = scene.drawList(0);
  assert.deepStrictEqual(names(lod0), ["hull:123456789001", "chassis_track:123456789005", "turret_01:123456789003", "gun_01:123456789004"]);
  const lod0all = scene.drawList(0, true);
  assert.strictEqual(lod0all.length, 6);   // + HP_ helper, + shadow volume
  assert.strictEqual(scene.drawList(1).length, 4);
  assert.strictEqual(scene.drawList(null, true).length, 10);
});

test("vertex formats + uint16/uint32 indices", () => {
  const hull = scene.meshes.get("123456789001"), gun = scene.meshes.get("123456789004"), sh = scene.meshes.get("123456789006");
  assert.strictEqual(hull.stride, 56); assert.deepStrictEqual(hull.attrNames, ["position", "normal", "uv0", "tangent", "binormal"]);
  assert(hull.indices instanceof Uint16Array); assert(gun.indices instanceof Uint32Array);
  assert.strictEqual(sh.stride, 24); assert(!sh.layout.uv0);
  const b = hull.bbox();
  assert.deepStrictEqual(b.min.map(v => +v.toFixed(3)), [-1.5, -3, 0.2]);
  assert.deepStrictEqual(b.max.map(v => +v.toFixed(3)), [1.5, 3.3, 1.25]);
});

test("materials: inheritance, configArchive, alphatest property blob", () => {
  const items = scene.drawList(0);
  const hull = items[0], track = items[1];
  assert.strictEqual(scene.materialName(hull.batch.material), "demo_tank_mtr");
  assert.deepStrictEqual(scene.texturesFor(hull.batch.material), { albedo: "images/demo_tank.tex" });
  assert(/Alphatest/.test(scene.materialFx(track.batch.material)));
  const blob = scene.materials.get("11").raw.properties.alphatestThreshold;
  assert(Math.abs(new DataView(blob.buffer, blob.byteOffset).getFloat32(5, true) - 0.3) < 1e-6);
});

test("texture lookup + DDS/DVPL decode", () => {
  const idx = new D.FileIndex();
  for (const f of ["demo_tank.sc2.dvpl", "demo_tank.scg.dvpl", "images/demo_tank.dx11.dds", "images/demo_track.dx11.dds.dvpl"]) idx.add("Tanks/Demo/" + f, f);
  assert.strictEqual(idx.pair("Tanks/Demo/demo_tank.sc2.dvpl", "scg").path, "Tanks/Demo/demo_tank.scg.dvpl");
  assert.strictEqual(idx.findTexture("images/demo_tank.tex", "Tanks/Demo/demo_tank.sc2.dvpl").path, "Tanks/Demo/images/demo_tank.dx11.dds");
  assert.strictEqual(idx.findTexture("~res:/3d/Tanks/Demo/images/demo_track.tex", null).path, "Tanks/Demo/images/demo_track.dx11.dds.dvpl");
  const img = D.decodeTexture(rd(path.join(FIX, "images/demo_tank.dx11.dds")));
  assert.strictEqual(img.width, 64);
  assert.deepStrictEqual([...img.data.subarray((5 * 64 + 5) * 4, (5 * 64 + 5) * 4 + 4)], [220, 40, 40, 140]); // red marker, top-left
  const tr = D.decodeTexture(rd(path.join(FIX, "images/demo_track.dx11.dds.dvpl")));
  assert.strictEqual(tr.data[(3 * 32 + 10) * 4 + 3], 0);
});

test("BC1/BC3 decoder on a hand-made block", () => {
  // BC1: c0 = pure red (0xF800) > c1 = pure blue (0x001F); all indices 0 -> red
  const bc1 = new Uint8Array([0x00, 0xF8, 0x1F, 0x00, 0, 0, 0, 0]);
  const o = D.decodeBC(bc1, 4, 4, 1);
  assert.deepStrictEqual([...o.subarray(0, 4)], [255, 0, 0, 255]);
  // index 1 everywhere -> blue
  const o2 = D.decodeBC(new Uint8Array([0x00, 0xF8, 0x1F, 0x00, 0x55, 0x55, 0x55, 0x55]), 4, 4, 1);
  assert.deepStrictEqual([...o2.subarray(60, 64)], [0, 0, 255, 255]);
  // BC3 alpha: a0=255 a1=0, all indices 1 -> alpha 0
  const bc3 = new Uint8Array([255, 0, 0x49, 0x92, 0x24, 0x49, 0x92, 0x24, 0x00, 0xF8, 0x1F, 0x00, 0, 0, 0, 0]);
  const o3 = D.decodeBC(bc3, 4, 4, 3);
  assert.strictEqual(o3[3], 0); assert.strictEqual(o3[0], 255);
});

test("BC7 (DX10 DDS) matches Pillow bit-exactly", () => {
  const img = D.decodeTexture(rd(path.join(FIX, "images/bc7_test.dds")));
  assert.strictEqual(img.format, "DX10 BC7");
  let h = 2166136261;
  for (const b of img.data) h = Math.imul(h ^ b, 16777619) >>> 0;
  assert.strictEqual(h, 0x985775c3);   // FNV-1a of Pillow's decode of the same file
});

// optional: real game files passed on the command line
for (const p of process.argv.slice(2)) {
  test("real file " + path.basename(p), () => {
    const isScg = /\.scg(\.dvpl)?$/i.test(p);
    const s = isScg ? D.openModel(null, rd(p)) : D.openModel(rd(p), (() => { const q = p.replace(/\.sc2/i, ".scg"); return fs.existsSync(q) ? rd(q) : null; })());
    const src = s.sc2 || s.scg;
    assert.strictEqual(src.trailing, 0, "trailing bytes");
    const dl = s.drawList(0);
    console.log(`   v${src.version} ${src.layout || "scg"}: ${s.meshes.size} groups, ${s.materials.size} materials, LOD0 ${dl.length} batches, ${dl.reduce((a, it) => a + it.mesh.triangles, 0)} tris, missing ${s.missingGeometry}`);
  });
}
console.log(`\n${ok} tests passed`);

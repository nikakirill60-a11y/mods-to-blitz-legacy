/* ===== Viewer UI + WebGL2 renderer (browser only) ===== */
"use strict";
(() => {
const $ = id => document.getElementById(id);
const canvas = $("gl");
const gl = canvas.getContext("webgl2", { antialias: true, preserveDrawingBuffer: true });

const S = {
  index: new Dava.FileIndex(),
  scene: null, modelPath: "", sc2Path: "", scgPath: "",
  lod: 0, textures: true, wire: false, service: false, grid: true, cull: false,
  selected: null,                    // Entity
  gpuMeshes: new Map(),              // Mesh -> gpu
  texCache: new Map(),               // file path -> {tex, img, error, format}
  matInfo: new Map(),                // material id -> {texPath, file, alphaTest, threshold}
  items: [],                         // current draw list
  cam: { target: [0, 0, 1], dist: 10, yaw: -2.2, pitch: 0.35 },
  sceneRadius: 5,
};

// ------------------------------------------------------------------ helpers
function toast(msg, err = false, ms = 4500) {
  const t = $("toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.style.display = "block";
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.style.display = "none"), ms);
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
async function readHandle(h) {
  if (h instanceof Uint8Array) return h;
  if (typeof h === "string") { const r = await fetch(h); if (!r.ok) throw new Error(h + ": HTTP " + r.status); return new Uint8Array(await r.arrayBuffer()); }
  return new Uint8Array(await h.arrayBuffer());
}
function download(name, data, mime) {
  const blob = data instanceof Blob ? data : new Blob([data], { type: mime || "application/octet-stream" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
const fmtN = n => n.toLocaleString("ru-RU");
const fmt3 = v => v.map(x => (+x).toFixed(3)).join(", ");
function stemName(p) { return Dava.baseOf(p).replace(/\.dvpl$/i, "").replace(/\.(sc2|scg)$/i, ""); }

// ------------------------------------------------------------------ math (column-major, GL)
const M4 = {
  persp(fovy, aspect, n, f) { const t = 1 / Math.tan(fovy / 2); return [t / aspect, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) / (n - f), -1, 0, 0, 2 * f * n / (n - f), 0]; },
  lookAt(e, c, up) {
    let z = norm(sub(e, c)), x = norm(cross(up, z)), y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, e), -dot(y, e), -dot(z, e), 1];
  },
  mul(a, b) { const o = new Array(16); for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s; } return o; },
};
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function scale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function norm(a) { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }

// ------------------------------------------------------------------ GL setup
const VS = `#version 300 es
layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNrm; layout(location=2) in vec2 aUv;
uniform mat4 uModel, uVP;
out vec3 vN; out vec2 vUv; out vec3 vW;
void main(){ vec4 w = uModel * vec4(aPos,1.0); vW = w.xyz; vN = mat3(uModel) * aNrm; vUv = aUv; gl_Position = uVP * w; }`;
const FS = `#version 300 es
precision highp float;
in vec3 vN; in vec2 vUv; in vec3 vW;
uniform sampler2D uTex; uniform int uHasTex, uHasNrm, uAlphaTest; uniform float uThreshold;
uniform vec3 uColor, uEye, uLight; uniform float uHi;
out vec4 o;
void main(){
  vec4 c = uHasTex == 1 ? texture(uTex, vUv) : vec4(uColor, 0.25);
  if (uAlphaTest == 1 && c.a < uThreshold) discard;
  vec3 V = normalize(uEye - vW);
  vec3 n = (uHasNrm == 1 && dot(vN, vN) > 1e-8) ? normalize(vN) : normalize(cross(dFdx(vW), dFdy(vW)));
  if (dot(n, V) < 0.0) n = -n;
  float sky = 0.5 + 0.5 * n.z;
  float dif = max(dot(n, uLight), 0.0);
  float fill = max(dot(n, normalize(vec3(-uLight.xy, 0.4))), 0.0);
  vec3 H = normalize(uLight + V);
  float spec = pow(max(dot(n, H), 0.0), 40.0) * (uAlphaTest == 1 ? 0.15 : c.a) * 0.6;
  vec3 col = c.rgb * (0.30 + 0.25 * sky + 0.60 * dif + 0.15 * fill) + vec3(spec);
  float rim = pow(1.0 - max(dot(n, V), 0.0), 2.0);
  col = mix(col, vec3(1.0, 0.62, 0.15), uHi * (0.25 + 0.5 * rim));
  o = vec4(col, 1.0);
}`;
const VS_L = `#version 300 es
layout(location=0) in vec3 aPos; uniform mat4 uModel, uVP; void main(){ gl_Position = uVP * uModel * vec4(aPos,1.0); }`;
const FS_L = `#version 300 es
precision highp float; uniform vec4 uColor; out vec4 o; void main(){ o = uColor; }`;

function compile(vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const u = {}; const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name; u[name] = gl.getUniformLocation(p, name); }
  return { p, u };
}

let PROG, LPROG, gridGpu, axesGpu, pickFbo = null;
function initGL() {
  if (!gl) { toast("Браузер не поддерживает WebGL2 — откройте в свежем Chrome/Edge/Firefox", true, 60000); return false; }
  PROG = compile(VS, FS); LPROG = compile(VS_L, FS_L);
  gl.clearColor(0.16, 0.18, 0.21, 1);
  gl.enable(gl.DEPTH_TEST);
  return true;
}

function makeLines(points) {
  const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(points), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 12, 0);
  gl.bindVertexArray(null);
  return { vao, buf: b, count: points.length / 3 };
}
function freeLines(g) { if (g) { gl.deleteBuffer(g.buf); gl.deleteVertexArray(g.vao); } }

function buildGrid(radius) {
  freeLines(gridGpu); freeLines(axesGpu);
  const step = radius > 40 ? 10 : radius > 8 ? 1 : radius > 1 ? 0.25 : 0.05;
  const n = Math.ceil(radius * 1.6 / step), L = n * step, pts = [];
  for (let i = -n; i <= n; i++) { pts.push(i * step, -L, 0, i * step, L, 0, -L, i * step, 0, L, i * step, 0); }
  gridGpu = makeLines(pts);
  const a = radius * 0.3;
  axesGpu = makeLines([0, 0, 0, a, 0, 0, 0, 0, 0, 0, a, 0, 0, 0, 0, 0, 0, a]);
}

function gpuMesh(mesh) {
  let g = S.gpuMeshes.get(mesh);
  if (g) return g;
  g = { vao: gl.createVertexArray(), bufs: [] };
  gl.bindVertexArray(g.vao);
  const vb = gl.createBuffer(); g.bufs.push(vb);
  gl.bindBuffer(gl.ARRAY_BUFFER, vb); gl.bufferData(gl.ARRAY_BUFFER, mesh.floats, gl.STATIC_DRAW);
  const st = mesh.stride;
  const L = mesh.layout;
  const bind = (loc, a, n) => { if (a && st) { gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, n, gl.FLOAT, false, st, a.offset * 4); } else { gl.disableVertexAttribArray(loc); } };
  bind(0, L.position || { offset: 0 }, 3);
  bind(1, L.normal, 3); if (!L.normal) gl.vertexAttrib3f(1, 0, 0, 0);
  bind(2, L.uv0, 2); if (!L.uv0) gl.vertexAttrib2f(2, 0, 0);
  const ib = gl.createBuffer(); g.bufs.push(ib);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
  g.itype = mesh.indices instanceof Uint16Array ? gl.UNSIGNED_SHORT : gl.UNSIGNED_INT;
  g.count = mesh.indices.length;
  g.mode = mesh.prim === 10 ? gl.LINES : gl.TRIANGLES;
  g.hasNrm = !!L.normal; g.hasUv = !!L.uv0;
  gl.bindVertexArray(null);
  S.gpuMeshes.set(mesh, g);
  return g;
}
function gpuWire(mesh, g) {
  if (g.wire) return g.wire;
  const idx = mesh.indices, n = Math.floor(idx.length / 3), lines = new Uint32Array(n * 6);
  for (let t = 0; t < n; t++) { const a = idx[3 * t], b = idx[3 * t + 1], c = idx[3 * t + 2]; lines.set([a, b, b, c, c, a], t * 6); }
  const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, g.bufs[0]);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, mesh.stride, (mesh.layout.position || { offset: 0 }).offset * 4);
  const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, lines, gl.STATIC_DRAW);
  gl.bindVertexArray(null);
  g.bufs.push(ib);
  return (g.wire = { vao, count: lines.length, extra: vao });
}
function freeGpu() {
  for (const g of S.gpuMeshes.values()) { for (const b of g.bufs) gl.deleteBuffer(b); gl.deleteVertexArray(g.vao); if (g.wire) gl.deleteVertexArray(g.wire.vao); }
  S.gpuMeshes.clear();
  for (const t of S.texCache.values()) if (t.tex) gl.deleteTexture(t.tex);
  S.texCache.clear();
}

// ------------------------------------------------------------------ materials / textures
function propFloat(scene, mid, key) {
  for (const m of scene.materialChain(mid)) {
    const srcs = [m.raw.properties];
    const cnt = m.raw.configCount;
    for (let i = 0; i < (cnt || 0); i++) { const c = m.raw[`configArchive_${i}`]; if (c) srcs.push(c.properties); }
    for (const props of srcs) {
      const v = props && props[key];
      if (typeof v === "number") return v;
      if (Array.isArray(v)) return v[0];
      if (v instanceof Uint8Array && v.length >= 9) return new DataView(v.buffer, v.byteOffset, v.length).getFloat32(5, true);
    }
  }
  return null;
}

function materialInfo(mid) {
  if (S.matInfo.has(mid)) return S.matInfo.get(mid);
  const sc = S.scene;
  const tex = sc.texturesFor(mid);
  const texPath = tex.baseColorMap || tex.albedo || tex.diffuse || tex.texture0 || null;
  const fx = sc.materialFx(mid);
  const thr = propFloat(sc, mid, "alphatestThreshold");
  const alphaTest = /alphatest/i.test(fx) || thr !== null;
  const file = texPath ? S.index.findTexture(texPath, S.sc2Path || S.scgPath) : null;
  const info = { mid, name: sc.materialName(mid), fx, textures: tex, texPath, file, alphaTest, threshold: thr ?? 0.3 };
  S.matInfo.set(mid, info);
  return info;
}

async function decodeImage(bytes) {
  const res = Dava.decodeTexture(bytes);
  if (!res.browserImage) return res;
  const bmp = await createImageBitmap(new Blob([res.bytes]));
  const c = document.createElement("canvas"); c.width = bmp.width; c.height = bmp.height;
  const ctx = c.getContext("2d"); ctx.drawImage(bmp, 0, 0);
  const d = ctx.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer), format: "PNG/JPG/WebP" };
}

function loadTexture(file) {
  if (S.texCache.has(file.path)) return S.texCache.get(file.path);
  const entry = { tex: null, img: null, error: null, format: "", pending: true };
  S.texCache.set(file.path, entry);
  (async () => {
    try {
      const img = await decodeImage(await readHandle(file.handle));
      entry.img = img; entry.format = img.format;
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, img.width, img.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, img.data);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      const aniso = gl.getExtension("EXT_texture_filter_anisotropic");
      if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, 8);
      entry.tex = t;
    } catch (e) { entry.error = e.message || String(e); console.warn(file.path, e); }
    entry.pending = false;
    renderMaterials(); requestDraw();
  })();
  return entry;
}

// ------------------------------------------------------------------ model loading
async function addFiles(list) {
  let added = [];
  for (const { path, handle } of list) { S.index.add(path, handle); added.push(Dava.normPath(path)); }
  // new textures may resolve old misses
  S.matInfo.clear();
  refreshModelSelect();
  const models = added.filter(p => /\.(sc2|scg)(\.dvpl)?$/i.test(p));
  const pick = models.find(p => /\.sc2(\.dvpl)?$/i.test(p)) || models[0];
  if (pick) await openModel(pick);
  else if (S.scene) { renderMaterials(); requestDraw(); toast(`Добавлено файлов: ${added.length}`); }
  else toast("Среди файлов нет .sc2 / .scg", true);
}

function refreshModelSelect() {
  const sel = $("modelSelect"), cur = S.modelPath;
  const seen = new Set(); const opts = [];
  for (const f of S.index.models().sort((a, b) => a.path.localeCompare(b.path))) {
    const key = f.path.toLowerCase().replace(/\.dvpl$/, "").replace(/\.(sc2|scg)$/, "");
    const isSc2 = /\.sc2(\.dvpl)?$/i.test(f.path);
    if (seen.has(key) && !isSc2) continue;
    if (seen.has(key)) { const i = opts.findIndex(o => o.key === key); if (i >= 0) opts.splice(i, 1); }
    seen.add(key); opts.push({ key, path: f.path });
  }
  sel.innerHTML = `<option value="">— модель (${opts.length}) —</option>` + opts.map(o => `<option value="${esc(o.path)}"${o.path === cur ? " selected" : ""}>${esc(o.path)}</option>`).join("");
}

async function openModel(path) {
  const f = S.index.get(path);
  if (!f) return;
  const isSc2 = /\.sc2(\.dvpl)?$/i.test(f.path);
  const sc2f = isSc2 ? f : S.index.pair(f.path, "sc2");
  const scgf = isSc2 ? S.index.pair(f.path, "scg") : f;
  try {
    const [a, b] = await Promise.all([sc2f ? readHandle(sc2f.handle) : null, scgf ? readHandle(scgf.handle) : null]);
    const scene = Dava.openModel(a, b);
    freeGpu(); S.matInfo.clear();
    S.scene = scene; S.modelPath = f.path; S.sc2Path = sc2f ? sc2f.path : ""; S.scgPath = scgf ? scgf.path : "";
    S.selected = null;
    const lods = scene.lods().filter(l => l >= 0);
    S.lod = lods.length ? lods[0] : 0;
    $("lodSelect").innerHTML = (lods.length ? lods : [0]).map(l => `<option value="${l}">${l}</option>`).join("") + `<option value="all">все</option>`;
    $("lodSelect").value = String(S.lod);
    $("drop").classList.add("loaded");
    refreshModelSelect();
    rebuildItems(); fitView(); renderTree(); renderProps(); renderMaterials();
    const msgs = [];
    if (scene.missingGeometry && !scgf) msgs.push(`Геометрия не найдена: добавьте «${stemName(f.path)}.scg» (или .scg.dvpl) из той же папки.`);
    else if (scene.missingGeometry) msgs.push(`Не найдено ${scene.missingGeometry} полигон-групп: .scg не от этой модели?`);
    if (scene.warnings.length) msgs.push(...scene.warnings.slice(0, 3));
    if (msgs.length) toast(msgs.join("\n"), true, 9000);
    document.title = stemName(f.path) + " — SCG/SC2 Viewer";
  } catch (e) {
    console.error(e);
    toast("Не удалось открыть " + f.path + ":\n" + (e.message || e), true, 10000);
  }
}

function entityVisible(e) { for (let x = e; x; x = x.parent) if (!x.visible) return false; return true; }

function rebuildItems() {
  const sc = S.scene;
  if (!sc) { S.items = []; return; }
  const lod = S.lod === "all" ? null : S.lod;
  S.items = sc.drawList(lod, S.service).filter(it => !it.entity || entityVisible(it.entity));
  for (const it of S.items) it.mat = it.batch ? materialInfo(it.batch.material) : null;
  S.items.sort((a, b) => (a.mat && a.mat.alphaTest ? 1 : 0) - (b.mat && b.mat.alphaTest ? 1 : 0));
  updateStatus();
  requestDraw();
}

function sceneBounds(items) {
  const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  for (const it of items) {
    const b = it.mesh.bbox(); if (!Number.isFinite(b.min[0])) continue;
    const W = it.entity ? it.entity.world : Dava.IDENTITY;
    for (let i = 0; i < 8; i++) {
      const p = Dava.transformPoint(W, i & 1 ? b.max[0] : b.min[0], i & 2 ? b.max[1] : b.min[1], i & 4 ? b.max[2] : b.min[2]);
      for (let c = 0; c < 3; c++) { mn[c] = Math.min(mn[c], p[c]); mx[c] = Math.max(mx[c], p[c]); }
    }
  }
  if (!Number.isFinite(mn[0])) return { min: [-1, -1, 0], max: [1, 1, 1] };
  return { min: mn, max: mx };
}

function fitView(items) {
  const b = sceneBounds(items || S.items);
  const c = scale(add(b.min, b.max), 0.5);
  const r = Math.max(0.05, Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]) / 2);
  S.cam.target = c; S.cam.dist = r * 2.6;
  if (!items) { S.sceneRadius = r; buildGrid(r); }
  requestDraw();
}

function setView(v) {
  const C = S.cam;
  if (v === "front") { C.yaw = Math.PI / 2; C.pitch = 0.08; }
  else if (v === "side") { C.yaw = 0; C.pitch = 0.08; }
  else if (v === "top") { C.yaw = -Math.PI / 2; C.pitch = 1.55; }
  else if (v === "iso") { C.yaw = Math.PI / 4; C.pitch = 0.4; }
  else return fitView();
  requestDraw();
}

// ------------------------------------------------------------------ rendering
let drawQueued = false;
function requestDraw() { if (!drawQueued) { drawQueued = true; requestAnimationFrame(draw); } }

function cameraMatrices() {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth * dpr)), h = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const C = S.cam;
  const eye = add(C.target, scale([Math.cos(C.pitch) * Math.cos(C.yaw), Math.cos(C.pitch) * Math.sin(C.yaw), Math.sin(C.pitch)], C.dist));
  const near = Math.max(0.001, C.dist * 0.01), far = C.dist * 50 + S.sceneRadius * 10;
  const P = M4.persp(45 * Math.PI / 180, w / h, near, far);
  const V = M4.lookAt(eye, C.target, [0, 0, 1]);
  return { VP: M4.mul(P, V), eye, w, h };
}

function draw() {
  drawQueued = false;
  if (!gl) return;
  const { VP, eye, w, h } = cameraMatrices();
  gl.viewport(0, 0, w, h);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  const C = S.cam;
  const light = norm([Math.cos(C.yaw + 0.6) * 0.8, Math.sin(C.yaw + 0.6) * 0.8, 0.9]);

  if (S.grid && gridGpu) {
    gl.useProgram(LPROG.p);
    gl.uniformMatrix4fv(LPROG.u.uVP, false, VP); gl.uniformMatrix4fv(LPROG.u.uModel, false, Dava.IDENTITY);
    gl.uniform4f(LPROG.u.uColor, 0.32, 0.35, 0.40, 1);
    gl.bindVertexArray(gridGpu.vao); gl.drawArrays(gl.LINES, 0, gridGpu.count);
    gl.bindVertexArray(axesGpu.vao);
    [[0.9, 0.3, 0.3], [0.3, 0.85, 0.3], [0.35, 0.5, 1]].forEach((c, i) => { gl.uniform4f(LPROG.u.uColor, c[0], c[1], c[2], 1); gl.drawArrays(gl.LINES, i * 2, 2); });
  }

  gl.useProgram(PROG.p);
  const U = PROG.u;
  gl.uniformMatrix4fv(U.uVP, false, VP);
  gl.uniform3fv(U.uEye, eye); gl.uniform3fv(U.uLight, light);
  gl.uniform1i(U.uTex, 0);
  if (S.cull) gl.enable(gl.CULL_FACE); else gl.disable(gl.CULL_FACE);
  gl.cullFace(gl.BACK); gl.frontFace(gl.CCW);  // verified on game files: winding agrees with normals (CCW)
  gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);
  for (const it of S.items) {
    const g = gpuMesh(it.mesh);
    gl.uniformMatrix4fv(U.uModel, false, it.entity ? it.entity.world : Dava.IDENTITY);
    let hasTex = 0, alphaTest = 0, thr = 0.3;
    if (it.mat) {
      alphaTest = it.mat.alphaTest ? 1 : 0; thr = it.mat.threshold;
      if (S.textures && it.mat.file && g.hasUv) {
        const t = loadTexture(it.mat.file);
        if (t.tex) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, t.tex); hasTex = 1; }
      }
    }
    if (!hasTex) alphaTest = 0;
    gl.uniform1i(U.uHasTex, hasTex); gl.uniform1i(U.uAlphaTest, alphaTest); gl.uniform1f(U.uThreshold, thr);
    gl.uniform1i(U.uHasNrm, g.hasNrm ? 1 : 0);
    gl.uniform3fv(U.uColor, it.mat ? [0.62, 0.64, 0.6] : [0.7, 0.7, 0.72]);
    gl.uniform1f(U.uHi, S.selected && it.entity && isUnder(it.entity, S.selected) ? 1 : 0);
    gl.bindVertexArray(g.vao);
    gl.drawElements(g.mode, g.count, g.itype, 0);
  }
  gl.disable(gl.POLYGON_OFFSET_FILL);
  if (S.wire) {
    gl.useProgram(LPROG.p);
    gl.uniformMatrix4fv(LPROG.u.uVP, false, VP);
    gl.uniform4f(LPROG.u.uColor, 0.05, 0.05, 0.06, 0.9);
    for (const it of S.items) {
      if (it.mesh.prim !== 1) continue;
      const wv = gpuWire(it.mesh, gpuMesh(it.mesh));
      gl.uniformMatrix4fv(LPROG.u.uModel, false, it.entity ? it.entity.world : Dava.IDENTITY);
      gl.bindVertexArray(wv.vao); gl.drawElements(gl.LINES, wv.count, gl.UNSIGNED_INT, 0);
    }
  }
  gl.bindVertexArray(null);
}
function isUnder(e, anc) { for (let x = e; x; x = x.parent) if (x === anc) return true; return false; }

// ------------------------------------------------------------------ picking
function pick(cx, cy) {
  if (!S.items.length) return null;
  const { VP, w, h } = cameraMatrices();
  if (!pickFbo || pickFbo.w !== w || pickFbo.h !== h) {
    if (pickFbo) { gl.deleteFramebuffer(pickFbo.fb); gl.deleteTexture(pickFbo.tex); gl.deleteRenderbuffer(pickFbo.rb); }
    const fb = gl.createFramebuffer(), tex = gl.createTexture(), rb = gl.createRenderbuffer();
    gl.bindTexture(gl.TEXTURE_2D, tex); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.bindRenderbuffer(gl.RENDERBUFFER, rb); gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, w, h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, rb);
    pickFbo = { fb, tex, rb, w, h };
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, pickFbo.fb);
  gl.viewport(0, 0, w, h); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.useProgram(LPROG.p); gl.uniformMatrix4fv(LPROG.u.uVP, false, VP);
  if (S.cull) gl.enable(gl.CULL_FACE); else gl.disable(gl.CULL_FACE);
  S.items.forEach((it, i) => {
    const id = i + 1, g = gpuMesh(it.mesh);
    gl.uniform4f(LPROG.u.uColor, (id & 255) / 255, ((id >> 8) & 255) / 255, ((id >> 16) & 255) / 255, 1);
    gl.uniformMatrix4fv(LPROG.u.uModel, false, it.entity ? it.entity.world : Dava.IDENTITY);
    gl.bindVertexArray(g.vao); gl.drawElements(g.mode, g.count, g.itype, 0);
  });
  const dpr = window.devicePixelRatio || 1, px = new Uint8Array(4);
  gl.readPixels(Math.round(cx * dpr), Math.round(h - cy * dpr), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.bindVertexArray(null);
  gl.clearColor(0.16, 0.18, 0.21, 1);
  requestDraw();
  const id = px[0] | (px[1] << 8) | (px[2] << 16);
  return id ? S.items[id - 1] : null;
}

// ------------------------------------------------------------------ UI: tree / props / materials
function entityCounts(e) {
  let tris = 0;
  for (const b of e.batches) {
    if (S.lod !== "all" && b.lod !== S.lod && b.lod !== -1) continue;
    const m = S.scene.meshes.get(b.datasource); if (m) tris += m.triangles;
  }
  return tris;
}

function renderTree() {
  const sc = S.scene, el = $("tree");
  if (!sc) { el.innerHTML = '<div class="hint">Файлы не загружены</div>'; return; }
  if (!sc.roots.length) {
    el.innerHTML = `<div class="hint">Только геометрия (.scg без .sc2): ${sc.meshes.size} полигон-групп</div>`;
    $("treeStats").textContent = ""; return;
  }
  const filter = $("treeFilter").value.trim().toLowerCase();
  const html = [];
  const rec = e => {
    const tris = entityCounts(e);
    const svc = sc.isService(e, null) || (e.batches.length && e.batches.every(b => sc.isService(e, b)));
    const kids = e.children.length;
    html.push(`<li data-uid="${e.uid}"><div class="row${S.selected === e ? " sel" : ""}" data-uid="${e.uid}">` +
      `<span class="tw">${kids ? "▾" : ""}</span><input type="checkbox" data-uid="${e.uid}"${e.visible ? " checked" : ""}>` +
      `<span class="nm${svc ? " svc" : ""}">${esc(e.name)}</span>` + (tris ? ` <span class="badge">${fmtN(tris)}△</span>` : "") + `</div>`);
    if (kids) { html.push("<ul>"); e.children.forEach(rec); html.push("</ul>"); }
    html.push("</li>");
  };
  html.push("<ul>"); sc.roots.forEach(rec); html.push("</ul>");
  el.innerHTML = html.join("");
  S.uidMap = new Map([...sc.entities()].map(e => [String(e.uid), e]));
  if (filter) {
    for (const li of el.querySelectorAll("li")) {
      const e = S.uidMap.get(li.dataset.uid);
      const match = [...e.walk()].some(x => x.name.toLowerCase().includes(filter));
      li.classList.toggle("hidden-row", !match);
    }
  }
  $("treeStats").textContent = `(${[...sc.entities()].length} узлов)`;
}

function texStatusHtml(mi) {
  if (!mi || !mi.texPath) return '<span class="hint">без текстуры</span>';
  if (!mi.file) return `<span class="bad" title="положите файл рядом или откройте папку целиком">✗ не найдена</span>`;
  const t = S.texCache.get(mi.file.path);
  if (t && t.error) return `<span class="bad">✗ ${esc(t.error)}</span>`;
  return `<span class="ok">✓ ${esc(mi.file.path)}</span>${t && t.format ? ` <span class="hint">${esc(t.format)}${t.img ? ` ${t.img.width}×${t.img.height}` : ""}</span>` : ""}`;
}

function shortJSON(v) {
  return JSON.stringify(Dava.toJSONSafe(v), (k, x) => Array.isArray(x) && x.length > 24 && typeof x[0] !== "object" ? `[${x.length} значений]` : x, 1);
}

function renderProps() {
  const el = $("props"), e = S.selected, sc = S.scene;
  if (!e || !sc) { el.innerHTML = '<div class="hint">Щёлкните по детали в окне или в дереве</div>'; return; }
  const W = e.world;
  const rows = [
    ["Имя", esc(e.name)], ["Путь", esc(e.path)],
    ["Позиция (мир)", fmt3([W[12], W[13], W[14]])],
    ["Позиция (лок.)", fmt3([e.local[12], e.local[13], e.local[14]])],
    ["Компоненты", esc(Dava.components(e.raw).map(c => c["comp.typename"]).join(", "))],
  ];
  let h = "<table>" + rows.map(([a, b]) => `<tr><th>${a}</th><td>${b}</td></tr>`).join("") + "</table>";
  if (e.batches.length) {
    h += `<table><tr><th>LOD</th><th>Меш</th><th>Материал / текстура</th></tr>`;
    for (const b of e.batches) {
      const m = sc.meshes.get(b.datasource), mi = materialInfo(b.material);
      const mesh = m ? `${fmtN(m.vcount)} верш., ${fmtN(m.triangles || m.lines)} ${m.prim === 10 ? "лин." : "△"}<br><span class="hint">${m.attrNames.join(" ")} (${m.stride} Б)</span>` : `<span class="bad">нет данных ${esc(b.datasource)}</span>`;
      h += `<tr><td>${b.lod}${b.switch >= 0 ? "/s" + b.switch : ""}</td><td>${mesh}</td><td>${esc(mi.name)}${mi.alphaTest ? " <span class=hint>(alphatest)</span>" : ""}<br>${texStatusHtml(mi)}</td></tr>`;
    }
    h += "</table>";
  }
  const raw = Object.assign({}, e.raw); delete raw["#hierarchy"];
  h += `<details><summary>Сырые данные (KeyedArchive)</summary><pre>${esc(shortJSON(raw))}</pre></details>`;
  el.innerHTML = h;
}

function renderMaterials() {
  const el = $("materials"), sc = S.scene;
  if (!sc) { el.innerHTML = ""; return; }
  const used = new Map();
  for (const it of S.items) if (it.mat) { const k = it.mat.name; if (!used.has(k)) used.set(k, it.mat); }
  let found = 0, total = 0;
  const rows = [];
  for (const mi of used.values()) {
    if (mi.texPath) { total++; if (mi.file) found++; }
    const other = Object.entries(mi.textures).filter(([k]) => !["albedo", "baseColorMap"].includes(k)).map(([k, v]) => `${k}: ${v}`).join("\n");
    rows.push(`<tr><td title="${esc(mi.fx)}">${esc(mi.name)}</td><td><span title="${esc(other)}">${esc(mi.texPath || "—")}</span><br>${texStatusHtml(mi)}</td></tr>`);
  }
  el.innerHTML = rows.length ? `<table>${rows.join("")}</table>` + (found < total ? `<div class="hint">Не хватает текстур? Откройте папку модели целиком (с подпапкой images).</div>` : "") : '<div class="hint">нет</div>';
  $("texStats").textContent = total ? `(${found}/${total})` : "";
}

function updateStatus() {
  const sc = S.scene;
  if (!sc) { $("status").textContent = ""; return; }
  let tris = 0, verts = 0;
  for (const it of S.items) { tris += it.mesh.triangles; verts += it.mesh.vcount; }
  const parts = [];
  if (sc.sc2) parts.push(`SC2 v${sc.sc2.version} (${sc.sc2.layout === "modern" ? "новый формат" : "старый формат"})`);
  if (sc.scg) parts.push(`SCG v${sc.scg.version}`);
  parts.push(`полигон-групп ${sc.meshes.size}`, `материалов ${sc.materials.size}`);
  $("status").textContent = `${S.modelPath}\n${parts.join(" · ")}\nна экране: ${S.items.length} батчей, ${fmtN(tris)} треуг., ${fmtN(verts)} вершин`;
}

function select(e, focus = false) {
  S.selected = e;
  renderProps();
  for (const r of $("tree").querySelectorAll(".row.sel")) r.classList.remove("sel");
  if (e) {
    const row = $("tree").querySelector(`.row[data-uid="${e.uid}"]`);
    if (row) { row.classList.add("sel"); row.scrollIntoView({ block: "nearest" }); }
    if (focus) { const its = S.items.filter(it => it.entity && isUnder(it.entity, e)); if (its.length) fitView(its); }
  }
  requestDraw();
}

// ------------------------------------------------------------------ export
function exportItems() { return S.items.filter(it => it.mesh.prim === 1); }

async function imagePNG(img, opaque) {
  const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
  const d = new ImageData(new Uint8ClampedArray(img.data), img.width, img.height);
  if (opaque) for (let i = 3; i < d.data.length; i += 4) d.data[i] = 255;
  c.getContext("2d").putImageData(d, 0, 0);
  const blob = await new Promise(r => c.toBlob(r, "image/png"));
  return new Uint8Array(await blob.arrayBuffer());
}

async function waitTextures(items) {
  const ps = [];
  for (const it of items) if (it.mat && it.mat.file) loadTexture(it.mat.file);
  for (let k = 0; k < 600; k++) { if (![...S.texCache.values()].some(t => t.pending)) break; await new Promise(r => setTimeout(r, 50)); }
}

function sanitize(arr) { for (let i = 0; i < arr.length; i++) if (!Number.isFinite(arr[i])) arr[i] = 0; return arr; }

async function exportGLB(opts = {}) {
  const sc = S.scene; if (!sc) return toast("Сначала откройте модель", true);
  const items = exportItems(); if (!items.length) return toast("Нечего экспортировать", true);
  toast("Готовлю GLB…");
  if (S.textures) await waitTextures(items);
  const bin = []; let binLen = 0;
  const views = [], accessors = [], meshes = [], materials = [], textures = [], images = [], nodes = [];
  const pushView = (u8, target) => {
    const pad = (4 - (binLen % 4)) % 4; if (pad) { bin.push(new Uint8Array(pad)); binLen += pad; }
    views.push(Object.assign({ buffer: 0, byteOffset: binLen, byteLength: u8.byteLength }, target ? { target } : {}));
    bin.push(new Uint8Array(u8.buffer, u8.byteOffset, u8.byteLength)); binLen += u8.byteLength;
    return views.length - 1;
  };
  const pushAcc = (arr, type, comp, target, minmax) => {
    const a = { bufferView: pushView(arr, target), componentType: comp, count: arr.length / ({ VEC3: 3, VEC2: 2, SCALAR: 1 }[type]), type };
    if (minmax) Object.assign(a, minmax);
    accessors.push(a); return accessors.length - 1;
  };
  const matIdx = new Map(), imgIdx = new Map();
  async function material(mi) {
    const key = mi ? mi.name : "__none";
    if (matIdx.has(key)) return matIdx.get(key);
    const m = { name: key, doubleSided: true, pbrMetallicRoughness: { metallicFactor: 0, roughnessFactor: 0.8 } };
    if (mi && S.textures && mi.file) {
      const t = S.texCache.get(mi.file.path);
      if (t && t.img) {
        const ik = mi.file.path + (mi.alphaTest ? "#a" : "#o");
        if (!imgIdx.has(ik)) {
          const png = await imagePNG(t.img, !mi.alphaTest);
          images.push({ name: Dava.baseOf(mi.file.path).replace(/\..*$/, ""), mimeType: "image/png", bufferView: pushView(png) });
          textures.push({ source: images.length - 1, sampler: 0 });
          imgIdx.set(ik, textures.length - 1);
        }
        m.pbrMetallicRoughness.baseColorTexture = { index: imgIdx.get(ik) };
        if (mi.alphaTest) { m.alphaMode = "MASK"; m.alphaCutoff = mi.threshold; }
      }
    }
    materials.push(m); matIdx.set(key, materials.length - 1);
    return materials.length - 1;
  }
  const primCache = new Map();
  async function primitive(it) {
    const ck = it.mesh.id + "|" + (it.mat ? it.mat.name : "");
    if (primCache.has(ck)) return primCache.get(ck);
    const m = it.mesh, attrs = {};
    const pos = sanitize(m.attr("position"));
    const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < pos.length; i++) { const c = i % 3; mn[c] = Math.min(mn[c], pos[i]); mx[c] = Math.max(mx[c], pos[i]); }
    attrs.POSITION = pushAcc(pos, "VEC3", 5126, 34962, { min: mn, max: mx });
    const nrm = m.attr("normal");
    if (nrm) {
      sanitize(nrm);
      for (let i = 0; i < nrm.length; i += 3) { const l = Math.hypot(nrm[i], nrm[i + 1], nrm[i + 2]); if (l > 1e-6) { nrm[i] /= l; nrm[i + 1] /= l; nrm[i + 2] /= l; } else { nrm[i] = 0; nrm[i + 1] = 0; nrm[i + 2] = 1; } }
      attrs.NORMAL = pushAcc(nrm, "VEC3", 5126, 34962);
    }
    const uv = m.attr("uv0"); if (uv) attrs.TEXCOORD_0 = pushAcc(sanitize(uv), "VEC2", 5126, 34962);
    // DAVA winding is counter-clockwise like glTF (checked against vertex normals)
    const idx = Uint32Array.from(m.indices);
    const p = { attributes: attrs, indices: pushAcc(idx, "SCALAR", 5125, 34963), material: await material(it.mat), mode: 4 };
    primCache.set(ck, p);
    return p;
  }
  // hierarchy: one glTF node per DAVA entity that (indirectly) has exported geometry
  const byEntity = new Map(), loose = [];
  for (const it of items) { if (it.entity) { if (!byEntity.has(it.entity)) byEntity.set(it.entity, []); byEntity.get(it.entity).push(it); } else loose.push(it); }
  const needed = new Set();
  for (const e of byEntity.keys()) for (let x = e; x; x = x.parent) needed.add(x);
  async function buildNode(e) {
    const node = { name: e.name, matrix: e.local.map(Number) };
    if (node.matrix.every((v, i) => Math.abs(v - Dava.IDENTITY[i]) < 1e-9)) delete node.matrix;
    nodes.push(node); const ni = nodes.length - 1;
    const its = byEntity.get(e);
    if (its) { const prims = []; for (const it of its) prims.push(await primitive(it)); meshes.push({ name: e.name, primitives: prims }); node.mesh = meshes.length - 1; }
    const kids = [];
    for (const c of e.children) if (needed.has(c)) kids.push(await buildNode(c));
    if (kids.length) node.children = kids;
    return ni;
  }
  const rootKids = [];
  for (const r of sc.roots) if (needed.has(r)) rootKids.push(await buildNode(r));
  for (const it of loose) { meshes.push({ name: "PolygonGroup_" + it.mesh.id, primitives: [await primitive(it)] }); nodes.push({ name: "PolygonGroup_" + it.mesh.id, mesh: meshes.length - 1 }); rootKids.push(nodes.length - 1); }
  nodes.push({ name: stemName(S.modelPath) || "model", rotation: [-Math.SQRT1_2, 0, 0, Math.SQRT1_2], children: rootKids });
  const gltf = {
    asset: { version: "2.0", generator: "scgviewer (mods-to-blitz-legacy)" },
    scene: 0, scenes: [{ name: stemName(S.modelPath), nodes: [nodes.length - 1] }],
    nodes, meshes, materials, accessors, bufferViews: views, buffers: [{ byteLength: 0 }],
  };
  if (textures.length) { gltf.textures = textures; gltf.images = images; gltf.samplers = [{ magFilter: 9729, minFilter: 9987, wrapS: 10497, wrapT: 10497 }]; }
  const pad = (4 - (binLen % 4)) % 4; if (pad) { bin.push(new Uint8Array(pad)); binLen += pad; }
  gltf.buffers[0].byteLength = binLen;
  let json = new TextEncoder().encode(JSON.stringify(gltf));
  const jpad = (4 - (json.length % 4)) % 4;
  if (jpad) { const j2 = new Uint8Array(json.length + jpad).fill(0x20); j2.set(json); json = j2; }
  const total = 12 + 8 + json.length + 8 + binLen;
  const out = new Uint8Array(total), dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546C67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, json.length, true); dv.setUint32(16, 0x4E4F534A, true); out.set(json, 20);
  let o = 20 + json.length;
  dv.setUint32(o, binLen, true); dv.setUint32(o + 4, 0x004E4942, true); o += 8;
  for (const b of bin) { out.set(b, o); o += b.length; }
  if (opts.bytes) return out;
  download(stemName(S.modelPath) + (S.lod === "all" ? "" : "_lod" + S.lod) + ".glb", out, "model/gltf-binary");
  toast(`GLB: ${meshes.length} мешей, ${images.length} текстур`);
}

function exportOBJ() {
  const sc = S.scene; if (!sc) return toast("Сначала откройте модель", true);
  const items = exportItems(); if (!items.length) return toast("Нечего экспортировать", true);
  const name = stemName(S.modelPath);
  const obj = [`# ${S.modelPath} — exported by scgviewer (Y-up)`, `mtllib ${name}.mtl`], mtl = [], mats = new Set();
  let vBase = 1;
  items.forEach((it, k) => {
    const W = it.entity ? it.entity.world : Dava.IDENTITY, m = it.mesh;
    const pos = m.attr("position"), nrm = m.attr("normal"), uv = m.attr("uv0");
    obj.push(`o ${(it.entity ? it.entity.name : "pg") + "_" + k}`);
    for (let i = 0; i < m.vcount; i++) {
      const p = Dava.transformPoint(W, pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]);
      obj.push(`v ${p[0].toFixed(5)} ${p[2].toFixed(5)} ${(-p[1]).toFixed(5)}`);
    }
    if (uv) for (let i = 0; i < m.vcount; i++) obj.push(`vt ${(uv[2 * i] || 0).toFixed(5)} ${(1 - (uv[2 * i + 1] || 0)).toFixed(5)}`);
    if (nrm) for (let i = 0; i < m.vcount; i++) {
      const x = nrm[3 * i] || 0, y = nrm[3 * i + 1] || 0, z = nrm[3 * i + 2] || 0;
      const n = [x * W[0] + y * W[4] + z * W[8], x * W[1] + y * W[5] + z * W[9], x * W[2] + y * W[6] + z * W[10]];
      obj.push(`vn ${n[0].toFixed(4)} ${n[2].toFixed(4)} ${(-n[1]).toFixed(4)}`);
    }
    const mn = (it.mat ? it.mat.name : "default").replace(/\s+/g, "_");
    if (!mats.has(mn)) {
      mats.add(mn); mtl.push(`newmtl ${mn}`, "Kd 0.8 0.8 0.8");
      if (it.mat && it.mat.file) mtl.push(`map_Kd ${Dava.baseOf(it.mat.file.path).replace(/\.(dds|pvr|png|webp|jpg)(\.dvpl)?$/i, ".png").replace(/\.dx11/i, "")}`);
      mtl.push("");
    }
    obj.push(`usemtl ${mn}`);
    const f = i => { const a = vBase + i; return uv && nrm ? `${a}/${a}/${a}` : uv ? `${a}/${a}` : nrm ? `${a}//${a}` : `${a}`; };
    for (let t = 0; t + 2 < m.indices.length; t += 3) obj.push(`f ${f(m.indices[t])} ${f(m.indices[t + 1])} ${f(m.indices[t + 2])}`);
    vBase += m.vcount;
  });
  download(name + ".obj", obj.join("\n") + "\n", "text/plain");
  setTimeout(() => download(name + ".mtl", mtl.join("\n"), "text/plain"), 300);
}

function exportJSON() {
  const sc = S.scene; if (!sc) return toast("Сначала откройте модель", true);
  const src = sc.sc2 || sc.scg;
  download(stemName(S.modelPath) + (sc.sc2 ? ".sc2" : ".scg") + ".json", JSON.stringify(Dava.toJSONSafe(src), null, 1), "application/json");
}

// ------------------------------------------------------------------ input
function collectDrop(dt) {
  const out = [];
  const walk = (entry, prefix) => new Promise(resolve => {
    if (entry.isFile) entry.file(f => { out.push({ path: prefix + f.name, handle: f }); resolve(); }, () => resolve());
    else if (entry.isDirectory) {
      const rd = entry.createReader(), all = [];
      const next = () => rd.readEntries(async ents => {
        if (!ents.length) { for (const e of all) await walk(e, prefix + entry.name + "/"); resolve(); }
        else { all.push(...ents); next(); }
      }, () => resolve());
      next();
    } else resolve();
  });
  const entries = [...dt.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return Promise.resolve([...dt.files].map(f => ({ path: f.name, handle: f })));
  return Promise.all(entries.map(e => walk(e, ""))).then(() => out);
}

function bindUI() {
  $("btnFiles").onclick = () => $("inFiles").click();
  $("btnFolder").onclick = () => $("inFolder").click();
  $("inFiles").onchange = e => { addFiles([...e.target.files].map(f => ({ path: f.name, handle: f }))); e.target.value = ""; };
  $("inFolder").onchange = e => { addFiles([...e.target.files].map(f => ({ path: f.webkitRelativePath || f.name, handle: f }))); e.target.value = ""; };
  $("modelSelect").onchange = e => { if (e.target.value) openModel(e.target.value); };
  $("lodSelect").onchange = e => { S.lod = e.target.value === "all" ? "all" : +e.target.value; rebuildItems(); renderTree(); renderMaterials(); renderProps(); };
  const opt = (id, key, after) => { $(id).onchange = e => { S[key] = e.target.checked; if (after) after(); requestDraw(); }; };
  opt("optTextures", "textures"); opt("optWire", "wire"); opt("optGrid", "grid"); opt("optCull", "cull");
  opt("optService", "service", () => { rebuildItems(); renderMaterials(); });
  $("btnGLB").onclick = () => exportGLB().catch(e => toast("Ошибка экспорта: " + e.message, true));
  $("btnOBJ").onclick = exportOBJ; $("btnJSON").onclick = exportJSON;
  $("btnShot").onclick = () => { draw(); canvas.toBlob(b => download(stemName(S.modelPath || "scene") + ".png", b)); };
  $("btnHelp").onclick = () => ($("help").hidden = false);
  $("helpClose").onclick = () => ($("help").hidden = true);
  $("help").onclick = e => { if (e.target.id === "help") $("help").hidden = true; };
  for (const b of document.querySelectorAll("#views button")) b.onclick = () => setView(b.dataset.view);
  $("btnAllOn").onclick = () => { if (!S.scene) return; for (const e of S.scene.entities()) e.visible = true; renderTree(); rebuildItems(); };
  $("btnAllOff").onclick = () => { if (!S.scene) return; for (const e of S.scene.entities()) e.visible = false; renderTree(); rebuildItems(); };
  $("treeFilter").oninput = () => renderTree();
  $("tree").addEventListener("click", e => {
    const uid = e.target.dataset.uid; if (!uid || !S.uidMap) return;
    const ent = S.uidMap.get(uid);
    if (e.target.type === "checkbox") { ent.visible = e.target.checked; rebuildItems(); renderMaterials(); return; }
    if (e.target.classList.contains("tw")) { const ul = e.target.closest("li").querySelector("ul"); if (ul) { ul.hidden = !ul.hidden; e.target.textContent = ul.hidden ? "▸" : "▾"; } return; }
    select(ent);
  });
  $("tree").addEventListener("dblclick", e => { const row = e.target.closest(".row"); if (row && S.uidMap) select(S.uidMap.get(row.dataset.uid), true); });

  // drag & drop anywhere
  const drop = $("drop");
  window.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  window.addEventListener("dragleave", e => { if (e.relatedTarget === null) drop.classList.remove("over"); });
  window.addEventListener("drop", async e => { e.preventDefault(); drop.classList.remove("over"); addFiles(await collectDrop(e.dataTransfer)); });

  // camera
  let drag = null;
  canvas.addEventListener("contextmenu", e => e.preventDefault());
  canvas.addEventListener("pointerdown", e => { canvas.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, pan: e.button === 2 || e.button === 1 || e.shiftKey }; });
  canvas.addEventListener("pointermove", e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
    const C = S.cam;
    if (drag.pan) {
      const f = C.dist * 0.0018;
      const right = [-Math.sin(C.yaw), Math.cos(C.yaw), 0];
      const up = cross(norm([Math.cos(C.pitch) * Math.cos(C.yaw), Math.cos(C.pitch) * Math.sin(C.yaw), Math.sin(C.pitch)]), right);
      C.target = add(C.target, add(scale(right, -dx * f), scale(up, -dy * f)));
    } else { C.yaw -= dx * 0.008; C.pitch = Math.max(-1.55, Math.min(1.55, C.pitch + dy * 0.008)); }
    requestDraw();
  });
  canvas.addEventListener("pointerup", e => {
    if (drag && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 4 && e.button === 0) {
      const r = canvas.getBoundingClientRect(), it = pick(e.clientX - r.left, e.clientY - r.top);
      select(it && it.entity ? it.entity : null);
    }
    drag = null;
  });
  canvas.addEventListener("dblclick", e => {
    const r = canvas.getBoundingClientRect(), it = pick(e.clientX - r.left, e.clientY - r.top);
    if (it && it.entity) select(it.entity, true); else fitView();
  });
  canvas.addEventListener("wheel", e => { e.preventDefault(); S.cam.dist *= Math.exp(e.deltaY * 0.0012); requestDraw(); }, { passive: false });
  window.addEventListener("resize", requestDraw);
  window.addEventListener("keydown", e => {
    if (e.target.tagName === "INPUT" && e.target.type !== "checkbox") return;
    if (e.key === "f" || e.key === "F" || e.key === "а" || e.key === "А") fitView();
    else if (e.key === "w" || e.key === "ц") { $("optWire").click(); }
    else if (e.key === "t" || e.key === "е") { $("optTextures").click(); }
    else if (e.key === "Escape") { select(null); $("help").hidden = true; }
  });
}

// ------------------------------------------------------------------ start
if (initGL()) { buildGrid(5); requestDraw(); }
bindUI();
window.SCGViewer = { S, openModel, addFiles, exportGLB, setView, select };
// ?load=a.sc2,b.scg,images/x.dds  (relative URLs; handy for a local web server)
const params = new URLSearchParams(location.search);
const q = params.get("load");
if (q) addFiles(q.split(",").filter(Boolean).map(u => ({ path: decodeURIComponent(u.split("?")[0]), handle: u })));
// ?list=/api/list  -> file list from the desktop launcher (SCGViewer.pyw)
const L = params.get("list");
if (L) {
  fetch(L).then(r => r.json()).then(j => {
    if (!j.files || !j.files.length) return;
    const enc = p => "/f/" + p.split("/").map(encodeURIComponent).join("/");
    if (j.root) document.title = j.root + " — SCG/SC2 Viewer";
    return addFiles(j.files.map(p => ({ path: p, handle: enc(p) })));
  }).catch(e => toast("Список файлов: " + (e.message || e), true));
}
// desktop launcher keeps running while the window is open
if (params.get("app")) {
  const ping = () => fetch("/api/ping", { cache: "no-store" }).catch(() => {});
  ping(); setInterval(ping, 5000);
  window.addEventListener("pagehide", () => { try { navigator.sendBeacon("/api/bye", "1"); } catch (e) { /* ignore */ } });
}
})();

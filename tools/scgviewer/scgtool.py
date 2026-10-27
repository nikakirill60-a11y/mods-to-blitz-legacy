#!/usr/bin/env python3
"""scgtool - console companion of scgviewer.html (WoT Blitz .sc2 / .scg / .dvpl).

Only the Python standard library is needed.

    python scgtool.py                         open the 3D viewer (scgviewer.html) in a browser
    python scgtool.py info  Tank.sc2[.dvpl]   versions, counts, LODs, triangles, textures
    python scgtool.py tree  Tank.sc2          node hierarchy with batches / LODs / materials
    python scgtool.py mats  Tank.sc2          materials, shaders and textures (with parents)
    python scgtool.py obj   Tank.sc2 [-o out.obj] [--lod 0|all] [--service]
                                              export to OBJ + MTL (Y-up, opens in Blender)
    python scgtool.py json  Tank.sc2 [-o out.json]   raw archive dump (binary blobs shortened)
    python scgtool.py unpack FILE_OR_DIR [-o OUT]    decode every *.dvpl (keeps the folder tree)

The paired file is found automatically: Tank.sc2 <-> Tank.scg (plain or .dvpl).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dava  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass

TEX_EXT = (".dx11.dds", ".dds", ".png", ".webp", ".dx11.pvr", ".pvr", ".jpg", ".tga")


# --------------------------------------------------------------------------- helpers

def model_paths(path: str) -> tuple[str | None, str | None]:
    base = path[:-5] if path.lower().endswith(".dvpl") else path
    stem, low = base[:-4], base.lower()

    def first(*c):
        return next((x for x in c if os.path.isfile(x)), None)

    sc2 = path if low.endswith(".sc2") else first(stem + ".sc2", stem + ".sc2.dvpl")
    scg = path if low.endswith(".scg") else first(stem + ".scg", stem + ".scg.dvpl")
    return sc2, scg


def find_texture(tex: str, model_path: str) -> str | None:
    """'.tex' reference -> real image file next to the model (by stem)."""
    t = tex.replace("\\", "/")
    for pre in ("~res:/", "~doc:/"):
        if t.startswith(pre):
            t = t[len(pre):]
    if t.lower().endswith(".tex"):
        t = t[:-4]
    root = os.path.dirname(os.path.abspath(model_path))
    cands = [os.path.normpath(os.path.join(root, t))]
    # absolute ~res:/3d/... paths: try the file name inside the model's own images folder
    cands += [os.path.join(root, "images", os.path.basename(t)), os.path.join(root, os.path.basename(t))]
    for stem in cands:
        for ext in TEX_EXT:
            for suf in ("", ".dvpl"):
                p = stem + ext + suf
                if os.path.isfile(p):
                    return p
    return None


def tri_count(scene: dava.Scene, lod, service: bool) -> tuple[int, int]:
    n = t = 0
    for _e, _b, m in scene.draw_list(lod, include_service=service):
        n += 1
        t += m.triangles
    return n, t


def fmt_vec(v, nd=3):
    return "(" + ", ".join(f"{x:.{nd}f}" for x in v) + ")"


def open_scene(path: str) -> tuple[dava.Scene, str | None, str | None]:
    sc2, scg = model_paths(path)
    if not sc2 and not scg:
        raise SystemExit(f"не найден .sc2/.scg: {path}")
    return dava.load_scene(path), sc2, scg


# --------------------------------------------------------------------------- commands

def cmd_info(a):
    s, sc2p, scgp = open_scene(a.file)
    print(f"Модель : {a.file}")
    if s.sc2:
        print(f"SC2    : {sc2p}  v{s.sc2['version']} ({'новый' if s.sc2.get('layout') == 'modern' else 'старый'} формат)"
              f", лишних байт в конце: {s.sc2.get('trailing', 0)}")
    if scgp:
        print(f"SCG    : {scgp}")
    elif s.sc2 and s.sc2.get("layout") == "modern":
        print("SCG    : НЕ НАЙДЕН - геометрия нового формата лежит в парном .scg")
    ents = list(s.entities())
    print(f"Узлов  : {len(ents)}   полигон-групп: {len(s.meshes)}   материалов: {len(s.materials)}")
    used = {b.datasource for e in ents for b in e.batches}
    missing = sum(1 for u in used if u is not None and u not in s.meshes)
    if missing:
        print(f"ВНИМАНИЕ: {missing} батчей ссылаются на отсутствующую геометрию")
    print("LOD    : " + ", ".join(str(x) for x in s.lods()) + "   (-1 = виден во всех LOD)")
    for lod in [x for x in s.lods() if x >= 0] or [None]:
        n, t = tri_count(s, lod, False)
        ns, ts = tri_count(s, lod, True)
        print(f"  LOD {lod}: {n} батчей, {t} треугольников  (со служебными: {ns} / {ts})")
    texs = {}
    for e, b, _m in s.draw_list(None):
        for k, v in s.textures_for(b.material if b else None).items():
            texs.setdefault(v, set()).add(k)
    if texs:
        print("Текстуры:")
        for t, kinds in sorted(texs.items()):
            f = find_texture(t, sc2p or scgp)
            print(f"  {'+' if f else '-'} {t}  [{', '.join(sorted(kinds))}]" + (f"  -> {os.path.relpath(f)}" if f else "  (файл не найден)"))
    bmin, bmax = [math.inf] * 3, [-math.inf] * 3
    for e, _b, m in s.draw_list(0, include_service=False):
        pos = m.attr("position") or []
        w = e.world if e else dava.IDENTITY
        for p in pos:
            q = dava.transform_point(w, p)
            for i in range(3):
                bmin[i] = min(bmin[i], q[i]); bmax[i] = max(bmax[i], q[i])
    if bmin[0] < math.inf:
        size = [bmax[i] - bmin[i] for i in range(3)]
        print(f"Габариты LOD0 (м): ширина X {size[0]:.2f}, длина Y {size[1]:.2f}, высота Z {size[2]:.2f}")


def cmd_tree(a):
    s, _p, _q = open_scene(a.file)

    def show(e, depth):
        flag = "  [служебный]" if s.is_service(e) else ""
        t = e.world[12:15]
        print(f"{'  ' * depth}{e.name}  @{fmt_vec(t, 2)}{flag}")
        for b in e.batches:
            m = s.meshes.get(b.datasource)
            geo = f"{m.vcount} верш / {m.triangles} тр" if m else "НЕТ ГЕОМЕТРИИ"
            svc = " [служ.]" if s.is_service(e, b) else ""
            print(f"{'  ' * depth}  - lod {b.lod:>2}  {geo:<22} {s.material_name(b.material)}{svc}")
        for c in e.children:
            show(c, depth + 1)

    for r in s.roots:
        show(r, 0)
    if not s.roots:
        for m in s.meshes.values():
            print(m.describe())


def cmd_mats(a):
    s, sc2p, scgp = open_scene(a.file)
    used = {b.material for e in s.entities() for b in e.batches}
    for mid, m in s.materials.items():
        mark = "*" if mid in used else " "
        par = s.materials.get(m.parent)
        print(f"{mark} {m.name}  (id {mid})" + (f"  <- {par.name}" if par else ""))
        fx = s.material_fx(mid)
        if fx:
            print(f"     fx: {fx}")
        for k, v in s.textures_for(mid).items():
            f = find_texture(v, sc2p or scgp)
            print(f"     {k:<12} {v}" + (f"  -> {os.path.basename(f)}" if f else ""))
        props = m.raw.get("properties")
        if isinstance(props, dict) and props:
            print("     props: " + ", ".join(sorted(props)))
    print("\n* = используется в модели")


def cmd_obj(a):
    s, sc2p, scgp = open_scene(a.file)
    lod = None if str(a.lod).lower() == "all" else int(a.lod)
    out = a.output or os.path.splitext(os.path.basename(a.file.replace(".dvpl", "")))[0] + ".obj"
    mtl_path = os.path.splitext(out)[0] + ".mtl"
    out_dir = os.path.dirname(os.path.abspath(out))
    lines = [f"# exported by scgtool from {os.path.basename(a.file)}", f"mtllib {os.path.basename(mtl_path)}"]
    mats: dict[str, dict] = {}
    vbase = tbase = nbase = 1
    nobj = ntri = 0
    for e, b, m in s.draw_list(lod, include_service=a.service):
        if m.prim != 1 or not m.indices:
            continue
        pos = m.attr("position")
        if not pos:
            continue
        nrm, uv = m.attr("normal"), m.attr("uv0")
        w = e.world if e else dava.IDENTITY
        mid = b.material if b else None
        mname, tex = s.material_name(mid), s.textures_for(mid)
        if mname in mats and mats[mname] != tex:  # same parent, overridden textures
            mname = f"{mname}_{mid}"
        mats.setdefault(mname, tex)
        nobj += 1
        lines.append(f"o {(e.name if e else 'mesh')}_{nobj}" + (f"_lod{b.lod}" if b and b.lod >= 0 else ""))
        for p in pos:  # DAVA Z-up -> OBJ Y-up: (x, z, -y)
            x, y, z = dava.transform_point(w, p)
            lines.append(f"v {x:.6f} {z:.6f} {-y:.6f}")
        if uv:
            lines += [f"vt {t[0]:.6f} {1 - t[1]:.6f}" for t in uv]
        if nrm:
            for n in nrm:
                x = n[0] * w[0] + n[1] * w[4] + n[2] * w[8]
                y = n[0] * w[1] + n[1] * w[5] + n[2] * w[9]
                z = n[0] * w[2] + n[1] * w[6] + n[2] * w[10]
                ln = math.sqrt(x * x + y * y + z * z) or 1.0
                lines.append(f"vn {x / ln:.4f} {z / ln:.4f} {-y / ln:.4f}")
        lines.append(f"usemtl {mname}")
        idx = m.indices
        for k in range(0, len(idx) - 2, 3):
            f = []
            for i in idx[k:k + 3]:
                v = str(vbase + i)
                vt = str(tbase + i) if uv else ""
                vn = str(nbase + i) if nrm else ""
                f.append(v + ("/" + vt if uv or nrm else "") + ("/" + vn if nrm else ""))
            lines.append("f " + " ".join(f))
            ntri += 1
        vbase += len(pos)
        tbase += len(uv) if uv else 0
        nbase += len(nrm) if nrm else 0
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    ml = []
    for name, tex in mats.items():
        ml += [f"newmtl {name}", "Kd 1 1 1", "Ka 0 0 0", "Ks 0 0 0"]
        alb = tex.get("albedo") or tex.get("diffuse")
        f = find_texture(alb, sc2p or scgp) if alb else None
        if f:
            ml.append("map_Kd " + os.path.relpath(f, out_dir).replace("\\", "/"))
        nm = tex.get("normalmap")
        f = find_texture(nm, sc2p or scgp) if nm else None
        if f:
            ml.append("map_Bump " + os.path.relpath(f, out_dir).replace("\\", "/"))
        ml.append("")
    with open(mtl_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(ml))
    print(f"OK: {out} ({nobj} объектов, {ntri} треугольников, LOD {a.lod}) + {os.path.basename(mtl_path)}")
    if any(not find_texture(t.get('albedo', ''), sc2p or scgp) for t in mats.values() if t.get("albedo")):
        print("  часть текстур не найдена рядом с моделью (папка images/)")


def json_safe(v, full=False):
    if isinstance(v, (bytes, bytearray)):
        if full or len(v) <= 32:
            return "hex:" + bytes(v).hex()
        return f"<{len(v)} bytes: {bytes(v[:16]).hex()}...>"
    if isinstance(v, dict):
        return {str(k): json_safe(x, full) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x, full) for x in v]
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    return v


def cmd_json(a):
    sc2p, scgp = model_paths(a.file)
    data = {}
    if sc2p:
        data["sc2"] = dava.read_sc2(dava.read_file(sc2p))
    if scgp and (a.scg or not sc2p):
        data["scg"] = dava.read_scg(dava.read_file(scgp))
    txt = json.dumps(json_safe(data, a.full), ensure_ascii=False, indent=1)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(txt)
        print(f"OK: {a.output} ({len(txt) // 1024} КБ)")
    else:
        print(txt)


def cmd_unpack(a):
    src = a.path
    files = []
    if os.path.isdir(src):
        for root, _d, names in os.walk(src):
            files += [os.path.join(root, n) for n in names if n.lower().endswith(".dvpl")]
        base = src
    else:
        files, base = [src], os.path.dirname(src)
    if not files:
        print("нет файлов .dvpl")
        return
    ok = bad = 0
    for f in sorted(files):
        rel = os.path.relpath(f, base)[:-5]
        dst = os.path.join(a.output, rel) if a.output else f[:-5]
        try:
            raw = open(f, "rb").read()
            data = dava.decode_dvpl(raw) if dava.is_dvpl(raw) else raw
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(data)
            ok += 1
            if len(files) <= 20:
                print(f"  {rel}  {len(raw)} -> {len(data)} байт")
        except Exception as ex:  # keep going on broken files
            bad += 1
            print(f"  ОШИБКА {rel}: {ex}")
    print(f"распаковано: {ok}, ошибок: {bad}")


# --------------------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(prog="scgtool", description="WoT Blitz .sc2/.scg/.dvpl tool",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd")
    for name, fn, hlp in (("info", cmd_info, "сводка по модели"), ("tree", cmd_tree, "дерево узлов"),
                          ("mats", cmd_mats, "материалы и текстуры")):
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("file")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("obj", help="экспорт в OBJ+MTL")
    sp.add_argument("file"); sp.add_argument("-o", "--output")
    sp.add_argument("--lod", default="0", help="номер LOD или all (по умолчанию 0)")
    sp.add_argument("--service", action="store_true", help="включить HP_-точки, тени, crash-модели")
    sp.set_defaults(fn=cmd_obj)
    sp = sub.add_parser("json", help="сырой дамп структуры")
    sp.add_argument("file"); sp.add_argument("-o", "--output")
    sp.add_argument("--scg", action="store_true", help="добавить и .scg (большой)")
    sp.add_argument("--full", action="store_true", help="не сокращать бинарные блоки")
    sp.set_defaults(fn=cmd_json)
    sp = sub.add_parser("unpack", help="распаковать .dvpl")
    sp.add_argument("path"); sp.add_argument("-o", "--output")
    sp.set_defaults(fn=cmd_unpack)

    args = list(sys.argv[1:] if argv is None else argv)
    # drag & drop of a model onto the .bat -> "info"
    if len(args) == 1 and os.path.isfile(args[0]):
        args = ["info"] + args
    a = p.parse_args(args)
    if not a.cmd:
        viewer = os.path.join(HERE, "scgviewer.html")
        print(__doc__)
        if os.path.isfile(viewer):
            print("Открываю 3D-просмотрщик:", viewer)
            webbrowser.open("file:///" + viewer.replace("\\", "/").lstrip("/"))
        return 0
    try:
        a.fn(a)
    except dava.DavaError as ex:
        print("ОШИБКА формата:", ex)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

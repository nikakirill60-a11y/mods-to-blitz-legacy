#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resviewer.py - просмотр файлов игры БЕЗ распаковки.

Открывает файл любого размера (читается прямо с диска, ничего не
распаковывается) и показывает, что внутри:

  * resources.assets.resS / *.resS (Unity) - какие картинки, звуки, модели
    лежат по каким адресам (если рядом лежит resources.assets и установлен
    UnityPy), иначе - найденные внутри картинки/звуки по сигнатурам;
  * resources.assets / *.assets / globalgamemanagers (Unity) - список объектов
    и их поля (нужен UnityPy), имена классов скриптов игры (MonoScript);
  * .pck / .exe / .apk-pck (Godot) - список файлов внутри, текст скриптов
    .gd, имена из скомпилированных скриптов .gdc и строки из ресурсов .res;
  * любой другой файл - поиск слов, текстовые строки, hex.

Кнопка "Барабан" ищет сразу все слова, связанные с барабаном / магазином:
burst, intrashot, cycle_info, fire_mode, clip, magazine, drum, volley ...

Запуск:
    python resviewer.py                      окно программы
    python resviewer.py ФАЙЛ                 окно, сразу с файлом
    python resviewer.py ФАЙЛ --drum          поиск слов барабана в консоли
    python resviewer.py ФАЙЛ --find intrashot [--find clip]
    python resviewer.py ФАЙЛ --list          что внутри (файлы / объекты)
    python resviewer.py ФАЙЛ --show 12       показать элемент №12 из --list
    python resviewer.py ФАЙЛ --show burst_info.gd   ... или по части имени
    python resviewer.py ФАЙЛ --strings [--min 6] [--filter burst]
    python resviewer.py ФАЙЛ --hex 0x1F400

Нужен только Python 3.8+. Дополнительно (по желанию):
    pip install UnityPy      объекты Unity и их поля, картинки из .resS
    pip install zstandard    сжатые скрипты Godot 4 (.gdc)
"""

import base64
import bisect
import hashlib
import io
import json
import mmap
import os
import queue
import re
import struct
import sys
import threading
import zlib

VERSION = "1.0"
_APP = None  # окно (для проверки)

DRUM_WORDS = ["burst", "intrashot", "cycle_info", "fire_mode", "volley", "clip",
              "magazine", "drum", "shots", "shell_count", "autoload", "reload"]

MAX_HITS = 3000          # предел результатов поиска на одно слово
MAX_STRINGS = 50000      # предел списка строк
MAX_PREVIEW = 8 << 20    # больше 8 МБ в предпросмотр не читаем


# =====================================================================================
# файл
# =====================================================================================
class Source:
    """Файл, отображённый в память: читается по кусочкам, без загрузки целиком."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.name = os.path.basename(path)
        self.size = os.path.getsize(path)
        self._fh = open(path, "rb")
        if self.size:
            self.data = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        else:
            self.data = b""

    def read(self, offset, length):
        offset = max(0, int(offset))
        return bytes(self.data[offset:offset + max(0, int(length))])

    def u32(self, offset, fmt="<I"):
        return struct.unpack_from(fmt, self.data, offset)[0]

    def close(self):
        try:
            if self.size:
                self.data.close()
        finally:
            self._fh.close()


def human(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return ("%d %s" % (n, unit)) if unit == "Б" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def printable(raw, encoding="utf-8"):
    """Байты -> читаемая строка: непечатные символы заменяются на точку."""
    text = raw.decode(encoding, "replace")
    return "".join(ch if (ch.isprintable() and ch != "\ufffd") else "·" for ch in text)


# =====================================================================================
# поиск, строки, hex
# =====================================================================================
CHUNK = 64 << 20  # файл читается кусками по 64 МБ


def _search_patterns(word):
    """[(как записано, байты для поиска, без учёта регистра?, кодировка)] для одного слова."""
    raw = word.encode("utf-8")
    pats = [("текст", raw.lower(), True, "utf-8")]
    if all(b < 128 for b in raw):
        pats.append(("UTF-16", b"".join(bytes([b, 0]) for b in raw.lower()), True, "utf-16-le"))
    # имена внутри скомпилированных скриптов Godot (.gdc без сжатия): XOR 0xb6
    pats.append(("скрипт Godot 3 (.gdc)", bytes(b ^ 0xB6 for b in raw), False, "xor8"))
    x32 = b"".join(bytes(c ^ 0xB6 for c in struct.pack("<I", ord(ch))) for ch in word)
    pats.append(("скрипт Godot 4 (.gdc)", x32, False, "xor32"))
    return pats


def _context(src, offset, length, enc, around=40):
    if enc == "utf-16-le":
        start = max(0, offset - around * 2)
        start -= (offset - start) % 2
        return printable(src.read(start, length + around * 4), "utf-16-le")
    if enc == "xor8":
        raw = bytes(b ^ 0xB6 for b in src.read(offset, length))
        return printable(raw) + "   (расшифровано)"
    if enc == "xor32":
        raw = bytes(b ^ 0xB6 for b in src.read(offset, length))
        return printable(raw, "utf-32-le") + "   (расшифровано)"
    return printable(src.read(max(0, offset - around), length + around * 2))


def search_words(src, words, limit=MAX_HITS, stop=None):
    """Поиск сразу нескольких слов за ОДИН проход по файлу.

    Возвращает [(смещение, где найдено, слово, контекст)] по возрастанию смещения.
    Без учёта регистра: кусок файла переводится в нижний регистр, и по нему идёт
    одно регулярное выражение со всеми словами (это в десятки раз быстрее, чем
    искать каждое слово отдельно с re.IGNORECASE).
    """
    words = [w for w in dict.fromkeys(w.strip() for w in words) if w]
    if not words or not src.size:
        return []
    ci, cs = {}, {}
    for w in words:
        for kind, pat, nocase, enc in _search_patterns(w):
            (ci if nocase else cs).setdefault(pat, (kind, w, enc))
    rx_ci = re.compile(b"|".join(re.escape(p) for p in sorted(ci, key=len, reverse=True))) if ci else None
    rx_cs = re.compile(b"|".join(re.escape(p) for p in sorted(cs, key=len, reverse=True))) if cs else None
    overlap = max(len(p) for p in list(ci) + list(cs))
    counts = dict.fromkeys(words, 0)
    hits = []
    for start in range(0, src.size, CHUNK):
        if stop and stop.is_set():
            break
        chunk = src.data[start:start + CHUNK + overlap]
        for rx, table, data in ((rx_ci, ci, None), (rx_cs, cs, chunk)):
            if rx is None:
                continue
            if data is None:
                data = chunk.lower()
            for m in rx.finditer(data):
                if m.start() >= CHUNK:
                    break  # совпадение начнётся в следующем куске
                kind, w, enc = table[m.group()]
                if counts[w] >= limit:
                    continue
                counts[w] += 1
                off = start + m.start()
                hits.append((off, kind, w, _context(src, off, len(m.group()), enc)))
        if all(c >= limit for c in counts.values()):
            break
    hits.sort(key=lambda h: h[0])
    return hits


def search(src, word, limit=MAX_HITS, stop=None):
    return search_words(src, [word], limit, stop)


STRING_RE = {}


def _string_re(minlen):
    if minlen not in STRING_RE:
        STRING_RE[minlen] = re.compile(rb"(?:[\x20-\x7e]|[\xd0\xd1][\x80-\xbf]){%d,}" % minlen)
    return STRING_RE[minlen]


def strings(src, minlen=6, filt=None, limit=MAX_STRINGS, stop=None):
    """Текстовые строки (латиница и кириллица UTF-8): [(смещение, строка)]."""
    pat = _string_re(minlen)
    out = []
    if filt:
        # быстрый путь: ищем само слово, потом расширяем до границ строки
        needle = filt.lower().encode("utf-8")
        seen = set()
        for start in range(0, src.size, CHUNK):
            if stop and stop.is_set():
                break
            low = src.data[start:start + CHUNK + len(needle)].lower()
            i = low.find(needle)
            while i != -1 and i < CHUNK:
                off = start + i
                lo = max(0, off - 4096)
                window = src.read(lo, (off - lo) + len(needle) + 4096)
                for m in pat.finditer(window):
                    if m.start() + lo <= off < m.end() + lo:
                        if m.start() + lo not in seen:
                            seen.add(m.start() + lo)
                            out.append((m.start() + lo, m.group().decode("utf-8", "replace")))
                        break
                if len(out) >= limit:
                    return out
                i = low.find(needle, i + 1)
        out.sort()
        return out
    for start in range(0, src.size, CHUNK):
        if stop and stop.is_set():
            break
        end = min(src.size, start + CHUNK)
        for m in pat.finditer(src.data, start, end):
            out.append((m.start(), m.group().decode("utf-8", "replace")))
            if len(out) >= limit:
                return out
    return out


def strings_of(raw, minlen=4, limit=4000):
    pat = re.compile(rb"(?:[\x20-\x7e]|[\xd0\xd1][\x80-\xbf]){%d,}" % minlen)
    out = []
    for m in pat.finditer(raw):
        out.append(m.group().decode("utf-8", "replace"))
        if len(out) >= limit:
            break
    return out


def hexdump(raw, base=0):
    lines = []
    for i in range(0, len(raw), 16):
        chunk = raw[i:i + 16]
        hx = " ".join("%02X" % b for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("%010X  %-47s  %s" % (base + i, hx, asc))
    return "\n".join(lines)


def parse_offset(text):
    text = text.strip().lower().replace(" ", "")
    if not text:
        return 0
    if text.startswith("0x"):
        return int(text, 16)
    if re.fullmatch(r"[0-9]+", text):
        return int(text)
    return int(text, 16)


# =====================================================================================
# сигнатуры вложенных файлов (для .resS без описания и для любых файлов)
# =====================================================================================
SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "PNG картинка"),
    (b"OggS\x00\x02", "OGG звук"),
    (b"FSB5", "FSB5 звук (FMOD)"),
    (b"RIFF", "RIFF (WAV/WebP)"),
    (b"DDS |", "DDS текстура"),
    (b"\xabKTX 11\xbb", "KTX текстура"),
    (b"\xabKTX 20\xbb", "KTX2 текстура"),
    (b"BKHD", "Wwise звуковой банк"),
    (b"GDPC", "Godot пакет .pck"),
    (b"GDSC", "Godot скрипт .gdc"),
    (b"RSRC", "Godot ресурс .res"),
    (b"RSCC", "Godot сжатый ресурс"),
    (b"GST2", "Godot текстура .ctex"),
    (b"UnityFS\x00", "Unity бандл"),
    (b"PK\x03\x04", "ZIP"),
    (b"\xff\xd8\xff", "JPEG картинка"),
]


def scan_signatures(src, limit=5000, stop=None):
    pat = re.compile(b"|".join(re.escape(s) for s, _ in SIGNATURES))
    names = {s: n for s, n in SIGNATURES}
    out = []
    for m in pat.finditer(src.data):
        sig = m.group()
        off = m.start()
        name = names[sig]
        if sig == b"\xff\xd8\xff" and src.read(off + 3, 1) not in (b"\xe0", b"\xe1", b"\xdb", b"\xee"):
            continue
        if sig == b"RIFF":
            kind = src.read(off + 8, 4)
            if kind not in (b"WAVE", b"WEBP"):
                continue
            name = "WAV звук" if kind == b"WAVE" else "WebP картинка"
        size = embedded_size(src, off, sig)
        out.append({"label": name, "type": name, "offset": off, "size": size, "kind": "sig"})
        if len(out) >= limit or (stop and stop.is_set()):
            break
    return out


def embedded_size(src, off, sig):
    """Размер вложенного файла, если его можно узнать из заголовка."""
    try:
        if sig.startswith(b"\x89PNG"):
            p = off + 8
            while p + 12 <= src.size:
                ln = struct.unpack(">I", src.read(p, 4))[0]
                typ = src.read(p + 4, 4)
                p += 12 + ln
                if typ == b"IEND":
                    return p - off
                if ln > src.size:
                    break
        if sig == b"RIFF":
            return struct.unpack("<I", src.read(off + 4, 4))[0] + 8
        if sig == b"FSB5":
            h = struct.unpack("<7I", src.read(off + 4, 28))
            return 60 + h[2] + h[3] + h[4]
    except Exception:
        pass
    return None


# =====================================================================================
# Godot .pck
# =====================================================================================
def find_pck(src):
    """Смещение начала пакета Godot внутри файла (.pck, .exe со встроенным пакетом)."""
    if not src.size:
        return None
    if src.read(0, 4) == b"GDPC":
        return 0
    if src.size >= 16 and src.read(src.size - 4, 4) == b"GDPC":
        ds = struct.unpack("<Q", src.read(src.size - 12, 8))[0]
        start = src.size - 12 - ds
        if 0 <= start < src.size and src.read(start, 4) == b"GDPC":
            return start
    # встроенный пакет без хвоста: перебираем вхождения "GDPC"
    n = 0
    for m in re.finditer(re.escape(b"GDPC"), src.data):
        n += 1
        if n > 200:
            break
        try:
            info = parse_pck(src, m.start())
            if info["entries"] or info.get("error"):
                return m.start()
        except Exception:
            continue
    return None


def parse_pck(src, start):
    """Заголовок и список файлов пакета (форматы Godot 3, 4.0-4.3 и 4.4+)."""
    p = start + 4
    ver, major, minor, patch = struct.unpack_from("<4I", src.data, p)
    p += 16
    if ver < 1 or ver > 9 or major < 1 or major > 9:
        raise ValueError("не похоже на пакет Godot")
    info = {"start": start, "version": ver, "godot": "%d.%d.%d" % (major, minor, patch),
            "flags": 0, "entries": [], "error": None}
    flags = file_base = 0
    if ver >= 2:
        flags, file_base = struct.unpack_from("<IQ", src.data, p)
        p += 12
    info["flags"] = flags
    if ver >= 3:
        dir_offset = struct.unpack_from("<Q", src.data, p)[0]
        p = start + dir_offset
    else:
        p += 64  # 16 зарезервированных слов
    if flags & 1:
        info["error"] = ("Список файлов пакета ЗАШИФРОВАН ключом игры. Без ключа шифрования "
                         "посмотреть его нельзя - но поиск слов по всему файлу всё равно работает.")
        return info
    if flags & 4:
        info["note"] = "Пакет-«каталог» (sparse bundle): сами файлы лежат рядом, отдельно."
    count = struct.unpack_from("<I", src.data, p)[0]
    p += 4
    if count > 2000000:
        raise ValueError("слишком много файлов - не пакет")
    raw = []
    for _ in range(count):
        plen = struct.unpack_from("<I", src.data, p)[0]
        if plen > 4096:
            raise ValueError("битый список файлов")
        path = src.read(p + 4, plen).split(b"\x00")[0].decode("utf-8", "replace")
        p += 4 + plen
        ofs, size = struct.unpack_from("<QQ", src.data, p)
        md5 = src.read(p + 16, 16)
        p += 32
        fflags = 0
        if ver >= 2:
            fflags = struct.unpack_from("<I", src.data, p)[0]
            p += 4
        raw.append((path, ofs, size, md5, fflags))

    if ver == 1:
        bases = [0, start]
    elif ver == 2:
        bases = [file_base + start, file_base] if flags & 2 else [file_base, file_base + start]
    else:
        bases = [file_base + start, file_base]
    base = _pick_base(src, raw, bases)
    for path, ofs, size, md5, fflags in raw:
        info["entries"].append({
            "label": path[6:] if path.startswith("res://") else path,
            "type": "файл Godot", "offset": base + ofs, "size": size,
            "encrypted": bool(fflags & 1), "removed": bool(fflags & 2), "kind": "pck"})
    return info


def _pick_base(src, raw, bases):
    """Проверяем md5 нескольких маленьких файлов, чтобы точно выбрать начало данных."""
    seen = []
    for b in bases:
        if b in seen:
            continue
        seen.append(b)
        checked = ok = 0
        for path, ofs, size, md5, fflags in raw:
            if fflags & 3 or size == 0 or size > (1 << 20):
                continue
            checked += 1
            if hashlib.md5(src.read(b + ofs, size)).digest() == md5:
                ok += 1
            if checked >= 3:
                break
        if checked and ok == checked:
            return b
    for b in seen:
        if all(b + ofs + size <= src.size for _, ofs, size, _, _ in raw):
            return b
    return seen[0]


# -- содержимое файлов из пакета --------------------------------------------------------
def _zstd_decompress(data, size):
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(data, max_output_size=size)
    except ImportError:
        pass
    try:
        from compression import zstd  # Python 3.14+
        return zstd.decompress(data)
    except ImportError:
        raise RuntimeError("Скрипт сжат zstd. Установите модуль:  pip install zstandard")


def _decode_variant(b, p, godot4):
    """Простые значения Variant (числа, строки). Возвращает (значение, новая позиция)."""
    header = struct.unpack_from("<I", b, p)[0]
    typ, flag64 = header & 0xFF, bool(header & (1 << 16))
    p += 4
    if typ == 0:
        return None, p
    if typ == 1:
        return bool(struct.unpack_from("<I", b, p)[0]), p + 4
    if typ == 2:
        return (struct.unpack_from("<q", b, p)[0], p + 8) if flag64 else (struct.unpack_from("<i", b, p)[0], p + 4)
    if typ == 3:
        return (struct.unpack_from("<d", b, p)[0], p + 8) if flag64 else (struct.unpack_from("<f", b, p)[0], p + 4)
    if typ == 4 or (godot4 and typ == 21):
        ln = struct.unpack_from("<I", b, p)[0]
        s = bytes(b[p + 4:p + 4 + ln]).decode("utf-8", "replace")
        return s, p + 4 + ln + ((4 - ln % 4) % 4)
    raise ValueError("сложное значение")


def decode_gdc(data):
    """Скомпилированный скрипт Godot (.gdc): имена (переменные, поля, функции) и константы."""
    version = struct.unpack_from("<I", data, 4)[0]
    lines = []
    if version >= 100:  # Godot 4.3+
        dsize = struct.unpack_from("<I", data, 8)[0]
        body = data[12:] if dsize == 0 else _zstd_decompress(data[12:], dsize)
        n_id, n_const = struct.unpack_from("<II", body, 0)
        p = 16
        idents = []
        for _ in range(n_id):
            ln = struct.unpack_from("<I", body, p)[0]
            raw = bytes(c ^ 0xB6 for c in body[p + 4:p + 4 + ln * 4])
            idents.append(raw.decode("utf-32-le", "replace"))
            p += 4 + ln * 4
        godot4, head = True, "Скомпилированный скрипт Godot 4 (формат %d)" % version
    else:  # Godot 3
        body = data
        n_id, n_const = struct.unpack_from("<II", body, 8)
        p = 24
        idents = []
        for _ in range(n_id):
            ln = struct.unpack_from("<I", body, p)[0]
            raw = bytes(c ^ 0xB6 for c in body[p + 4:p + 4 + ln])
            idents.append(raw.split(b"\x00")[0].decode("utf-8", "replace"))
            p += 4 + ln
        godot4, head = False, "Скомпилированный скрипт Godot 3 (формат %d)" % version
    consts = []
    for _ in range(n_const):
        try:
            v, p = _decode_variant(body, p, godot4)
            consts.append(v)
        except Exception:
            consts.append("…")
            break
    lines.append(head)
    lines.append("")
    lines.append("ИМЕНА в скрипте (%d) - переменные, поля, функции, классы, в порядке появления:" % len(idents))
    lines.append("")
    for i in range(0, len(idents), 6):
        lines.append("    " + ",  ".join(idents[i:i + 6]))
    lines.append("")
    lines.append("КОНСТАНТЫ (%d) - числа и строки из кода:" % len(consts))
    lines.append("")
    for v in consts:
        lines.append("    " + repr(v))
    lines.append("")
    lines.append("Подсказка: поля, которые видно в .tres (reload, intrashot, ...), идут в списке имён;")
    lines.append("рядом с ними - имена остальных полей того же ресурса.")
    return "\n".join(lines)


def decompress_rscc(data):
    """Сжатый ресурс Godot (RSCC): возвращает распакованные байты."""
    mode, block, total = struct.unpack_from("<III", data, 4)
    count = total // block + 1
    sizes = struct.unpack_from("<%dI" % count, data, 16)
    p = 16 + 4 * count
    out = bytearray()
    for i, cs in enumerate(sizes):
        chunk = data[p:p + cs]
        p += cs
        want = min(block, total - len(out))
        if mode == 1:      # deflate
            out += zlib.decompress(chunk)
        elif mode == 3:    # gzip
            out += zlib.decompress(chunk, 31)
        elif mode == 2:    # zstd
            out += _zstd_decompress(chunk, want)
        else:
            raise RuntimeError("сжатие FastLZ не поддерживается")
    return bytes(out)


def looks_like_text(raw):
    if not raw:
        return True
    sample = raw[:65536]
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError as e:
        if e.start < len(sample) - 4:
            return False
        text = sample[:e.start].decode("utf-8", "replace")
    bad = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t")
    return bad <= len(text) // 100


def describe_bytes(raw, name=""):
    """Текстовое описание содержимого (для предпросмотра)."""
    head = raw[:4]
    try:
        if head == b"GDSC":
            return decode_gdc(raw)
        if head == b"RSCC":
            raw = decompress_rscc(raw)
            head = raw[:4]
            name += "  (распакован из RSCC)"
        if head == b"RSRC":
            ss = strings_of(raw, 3)
            return ("Двоичный ресурс Godot %s.\nСтроки внутри (имена свойств, классов, пути):\n\n" % name
                    + "\n".join("    " + s for s in ss))
    except Exception as e:
        return "Не удалось разобрать (%s).\n\n%s" % (e, hexdump(raw[:2048]))
    if looks_like_text(raw):
        return raw.decode("utf-8", "replace")
    ss = strings_of(raw, 4, 300)
    return ("Двоичные данные. Первые байты:\n\n" + hexdump(raw[:1024])
            + "\n\nСтроки внутри:\n\n" + "\n".join("    " + s for s in ss))


# =====================================================================================
# Unity
# =====================================================================================
def unity_kind(src):
    head = src.read(0, 48)
    if head[:8] == b"UnityFS\x00":
        return "Unity бандл (UnityFS)"
    if len(head) >= 32:
        ms, fs, ver, do = struct.unpack(">4I", head[:16])
        if 9 <= ver <= 60:
            if fs == src.size:
                return "Unity файл объектов (SerializedFile v%d)" % ver
            if ver >= 22 and struct.unpack(">Q", head[24:32])[0] == src.size:
                return "Unity файл объектов (SerializedFile v%d)" % ver
    return None


def unitypy():
    try:
        import UnityPy  # noqa
        return UnityPy
    except Exception:
        return None


def _obj_name(obj):
    for getter in ("peek_name",):
        if hasattr(obj, getter):
            try:
                return getattr(obj, getter)() or ""
            except Exception:
                pass
    try:
        data = obj.read()
        return getattr(data, "m_Name", "") or getattr(data, "name", "") or ""
    except Exception:
        return ""


def _obj_dict(obj):
    for getter in ("parse_as_dict", "read_typetree"):
        if hasattr(obj, getter):
            try:
                return getattr(obj, getter)()
            except Exception:
                continue
    return None


def _stream_ref(d):
    """(файл, смещение, размер) внешних данных объекта (.resS / .resource)."""
    if not isinstance(d, dict):
        return None
    sd = d.get("m_StreamData")
    if isinstance(sd, dict) and sd.get("path"):
        return sd.get("path"), int(sd.get("offset", 0)), int(sd.get("size", 0))
    rs = d.get("m_Resource")
    if isinstance(rs, dict) and rs.get("m_Source"):
        return rs.get("m_Source"), int(rs.get("m_Offset", 0)), int(rs.get("m_Size", 0))
    return None


class UnityView:
    """Объекты Unity из файла (.assets) или ссылки на открытый .resS."""

    def __init__(self, assets_path, ress_name=None, stop=None):
        UnityPy = unitypy()
        self.env = UnityPy.load(assets_path)
        self.objects = {}
        self.rows = []
        for obj in self.env.objects:
            if stop and stop.is_set():
                break
            t = obj.type.name
            d = None
            ref = None
            if t in ("Texture2D", "AudioClip", "Mesh", "VideoClip", "Font", "Cubemap", "Texture3D", "Texture2DArray"):
                d = _obj_dict(obj)
                ref = _stream_ref(d)
            if ress_name:
                if not ref or os.path.basename(str(ref[0]).replace("\\", "/")).lower() != ress_name.lower():
                    continue
            name = _obj_name(obj)
            if t == "MonoScript" and d is None:
                d = _obj_dict(obj)
                if isinstance(d, dict):
                    ns = d.get("m_Namespace") or ""
                    name = (ns + "." if ns else "") + (d.get("m_ClassName") or name)
            key = len(self.rows)
            self.objects[key] = obj
            row = {"label": name or "(без имени)", "type": t, "key": key, "kind": "unity",
                   "path_id": obj.path_id}
            if ress_name and ref:
                row["offset"], row["size"] = ref[1], ref[2]
            else:
                row["offset"] = getattr(obj, "byte_start", None)
                row["size"] = getattr(obj, "byte_size", None)
                if ref:
                    row["stream"] = "%s @ %d (%s)" % (os.path.basename(str(ref[0])), ref[1], human(ref[2]))
            self.rows.append(row)
        if ress_name:
            self.rows.sort(key=lambda r: r["offset"])

    def describe(self, row):
        obj = self.objects[row["key"]]
        lines = ["%s  «%s»   (path_id %s)" % (row["type"], row["label"], row["path_id"])]
        if row.get("stream"):
            lines.append("данные во внешнем файле: " + row["stream"])
        d = _obj_dict(obj)
        if isinstance(d, dict):
            for big in ("image data", "m_Data", "m_IndexBuffer", "m_VertexData", "m_Script"):
                if big in d and isinstance(d[big], (bytes, bytearray, list, str)) and len(d[big]) > 512:
                    d[big] = "<%d байт>" % len(d[big])
            lines.append("")
            lines.append(json.dumps(d, ensure_ascii=False, indent=2, default=_json_default)[:400000])
        else:
            raw = obj.get_raw_data() if hasattr(obj, "get_raw_data") else b""
            lines.append("")
            lines.append("Поля этого объекта без скриптов игры не расшифровать (обычно это MonoBehaviour -")
            lines.append("настройки из кода игры). Сырые байты и строки внутри:")
            lines.append("")
            lines.append(hexdump(raw[:2048]))
            ss = strings_of(raw, 3, 500)
            if ss:
                lines.append("")
                lines.append("Строки: " + " | ".join(ss))
        return "\n".join(lines)

    def image_png(self, row):
        obj = self.objects[row["key"]]
        if row["type"] not in ("Texture2D", "Sprite"):
            return None
        img = obj.read().image
        bio = io.BytesIO()
        img.save(bio, "PNG")
        return bio.getvalue()


def _json_default(o):
    if isinstance(o, (bytes, bytearray)):
        return "<%d байт>" % len(o)
    return str(o)


# =====================================================================================
# общий разбор файла
# =====================================================================================
class Analysis:
    """Что это за файл и что у него внутри (строки таблицы «Внутри файла»)."""

    def __init__(self, src, stop=None):
        self.src = src
        self.kind = "файл"
        self.notes = []
        self.rows = []
        self.unity = None
        self.pck = None
        name_low = src.name.lower()

        start = find_pck(src) if src.size else None
        if start is not None:
            self.pck = parse_pck(src, start)
            self.kind = "Godot пакет (формат %d, Godot %s)%s" % (
                self.pck["version"], self.pck["godot"],
                "" if start == 0 else ", встроен в файл с адреса %d" % start)
            if self.pck.get("error"):
                self.notes.append(self.pck["error"])
            if self.pck.get("note"):
                self.notes.append(self.pck["note"])
            self.rows = self.pck["entries"]
            enc = sum(1 for e in self.rows if e["encrypted"])
            if enc:
                self.notes.append("%d файлов внутри зашифрованы - их содержимое не прочитать." % enc)
            self.notes.append("Скрипты барабана ищите в списке: assets/data/tank_info/burst_info.gd, "
                              "cycle_info.gd, module_gun.gd (или .gdc).")
            return

        ukind = unity_kind(src)
        if name_low.endswith(".ress") or name_low.endswith(".resource"):
            self.kind = "Unity внешние данные (%s): картинки/звук/модели без описаний" % src.name[src.name.rfind("."):]
            assets = src.path[:src.path.rfind(".")]
            if unitypy() and os.path.exists(assets):
                try:
                    self.unity = UnityView(assets, src.name, stop)
                    self.rows = self.unity.rows
                    self.notes.append("Описание объектов взято из %s (UnityPy)." % os.path.basename(assets))
                except Exception as e:
                    self.notes.append("Не удалось прочитать %s: %s" % (os.path.basename(assets), e))
            elif not os.path.exists(assets):
                self.notes.append("Рядом нет файла %s - без него не узнать, что где лежит. "
                                  "Показаны вложенные файлы по сигнатурам." % os.path.basename(assets))
            else:
                self.notes.append("Для списка объектов установите UnityPy:  pip install UnityPy")
            self.notes.append("В .resS только «сырые» данные (пиксели, звук, вершины). Текста и "
                              "настроек (барабан и т.п.) там обычно нет - ищите в .assets / скриптах.")
            if not self.rows:
                self.rows = scan_signatures(src, stop=stop)
                if not self.rows and not self.unity:
                    self.notes.append("Вложенных файлов с заголовками не найдено: здесь сырые пиксели/звук без "
                                      "заголовков. Что где лежит - знает только %s." % os.path.basename(assets))
            return

        if ukind:
            self.kind = ukind
            if unitypy():
                try:
                    self.unity = UnityView(src.path, None, stop)
                    self.rows = self.unity.rows
                    scripts = sum(1 for r in self.rows if r["type"] == "MonoScript")
                    if scripts:
                        self.notes.append("MonoScript - это имена классов кода игры (%d шт.). "
                                          "Ищите среди них что-то вроде Gun, Drum, Clip, Magazine." % scripts)
                except Exception as e:
                    self.notes.append("UnityPy не смог прочитать файл: %s" % e)
            else:
                self.notes.append("Для списка объектов установите UnityPy:  pip install UnityPy. "
                                  "Поиск слов и строки работают и без него.")
            return

        if src.read(0, 4) == b"PK\x03\x04":
            self.kind = "ZIP / APK архив (откройте .pck из папки assets внутри)"
        self.rows = scan_signatures(src, stop=stop)
        if self.rows:
            self.notes.append("Найдены вложенные файлы по сигнатурам (%d)." % len(self.rows))

    def regions(self):
        if getattr(self, "_regions", None) is None:
            self._regions = sorted((r["offset"], r["offset"] + r["size"], r["label"])
                                   for r in self.rows if r.get("offset") is not None and r.get("size"))
            self._starts = [r[0] for r in self._regions]
        return self._regions

    def owner(self, offset):
        """В каком файле/объекте лежит этот адрес (для результатов поиска)."""
        regs = self.regions()
        i = bisect.bisect_right(self._starts, offset) - 1
        if i >= 0 and regs[i][0] <= offset < regs[i][1]:
            return regs[i][2]
        return ""

    def describe(self, row):
        if row["kind"] == "unity":
            return self.unity.describe(row)
        if row["kind"] == "pck":
            if row["encrypted"]:
                return "Файл зашифрован ключом игры - содержимое прочитать нельзя."
            raw = self.src.read(row["offset"], min(row["size"], MAX_PREVIEW))
            text = describe_bytes(raw, row["label"])
            if row["size"] > MAX_PREVIEW:
                text = "(показаны первые %s из %s)\n\n" % (human(MAX_PREVIEW), human(row["size"])) + text
            return text
        size = row.get("size") or 1024
        raw = self.src.read(row["offset"], min(size, 4096))
        return "%s, адрес %d (0x%X), размер %s\n\n%s" % (
            row["label"], row["offset"], row["offset"], human(row["size"]) if row.get("size") else "неизвестен",
            hexdump(raw[:1024], row["offset"]))

    def image_png(self, row):
        """PNG-байты картинки для просмотра, если элемент - картинка."""
        if row["kind"] == "unity":
            return self.unity.image_png(row)
        if row.get("label", "").startswith("PNG") and row.get("size"):
            return self.src.read(row["offset"], row["size"])
        if row["kind"] == "pck" and row["label"].lower().endswith(".png") and not row["encrypted"]:
            return self.src.read(row["offset"], row["size"])
        return None

    def raw_bytes(self, row):
        if row["kind"] == "unity":
            obj = self.unity.objects[row["key"]]
            if self.src.name.lower().endswith((".ress", ".resource")):
                return self.src.read(row["offset"], row["size"])
            return obj.get_raw_data() if hasattr(obj, "get_raw_data") else b""
        return self.src.read(row["offset"], row.get("size") or 4096)


HINT_PREFIXES = ("Подсказка:", "рядом с ними", "Скомпилированный скрипт", "Двоичный ресурс")


def search_packed(analysis, words, stop=None, limit=400):
    """Поиск внутри СЖАТЫХ скриптов (.gdc) и ресурсов (RSCC) пакета Godot.

    Обычный поиск их не видит: текст там сжат zstd/deflate.  Файлы распаковываются
    только в памяти; результат - (адрес файла, где, слово, строка с совпадением).
    """
    if not analysis or not analysis.pck:
        return []
    src = analysis.src
    hits = []
    low = [w.lower() for w in words if w]
    for row in analysis.rows:
        if stop and stop.is_set():
            break
        if row["encrypted"] or not row["size"] or row["size"] > MAX_PREVIEW:
            continue
        head = src.read(row["offset"], 12)
        if head[:4] == b"GDSC":
            version, packed = struct.unpack("<II", head[4:12])
            if version < 100 or packed == 0:
                continue  # не сжат - его находит обычный поиск
        elif head[:4] != b"RSCC":
            continue
        try:
            text = describe_bytes(src.read(row["offset"], row["size"]), row["label"])
        except Exception:
            continue
        for line in text.splitlines():
            if line.startswith(HINT_PREFIXES):
                continue
            l = line.lower()
            for w, wl in zip(words, low):
                if wl in l:
                    hits.append((row["offset"], "внутри сжатого файла", w, line.strip()[:200]))
                    if len(hits) >= limit * max(1, len(words)):
                        return hits
    return hits


def search_all(src, analysis, words, stop=None, limit=MAX_HITS):
    hits = search_words(src, words, limit=limit, stop=stop)
    hits += search_packed(analysis, words, stop)
    hits.sort(key=lambda h: h[0])
    return hits


def drum_report(src, analysis=None, stop=None):
    """Поиск всех слов барабана; возвращает список (смещение, где, слово, контекст)."""
    return search_all(src, analysis, DRUM_WORDS, stop, limit=400)


# =====================================================================================
# консольный режим
# =====================================================================================
def run_cli(args):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    src = Source(args.file)
    print("Файл: %s (%s)" % (src.path, human(src.size)))
    need_analysis = args.list or args.show or args.info or args.find or args.drum
    an = Analysis(src) if need_analysis else None
    if an:
        print("Тип: " + an.kind)
        for n in an.notes:
            print("  * " + n)
        print()

    if args.list:
        for i, r in enumerate(an.rows):
            off = "" if r.get("offset") is None else "@%d" % r["offset"]
            size = "" if not r.get("size") else human(r["size"])
            extra = ("  [зашифрован]" if r.get("encrypted") else "") + (("  -> " + r["stream"]) if r.get("stream") else "")
            print("%5d  %-22s %-60s %12s %10s%s" % (i, r["type"][:22], r["label"][:60], off, size, extra))
        print("\nВсего: %d" % len(an.rows))

    if args.show:
        rows = an.rows
        sel = None
        if re.fullmatch(r"\d+", args.show) and int(args.show) < len(rows):
            sel = rows[int(args.show)]
        else:
            for r in rows:
                if args.show.lower() in r["label"].lower():
                    sel = r
                    break
        if sel is None:
            print("Не найдено: %s" % args.show)
        else:
            print("=== %s ===" % sel["label"])
            print(an.describe(sel))

    words = list(args.find or [])
    if args.drum:
        words += DRUM_WORDS
    all_hits = search_all(src, an, words, limit=args.limit) if words else []
    for w in dict.fromkeys(words):
        hits = [h for h in all_hits if h[2] == w]
        print("--- «%s»: %d совпадений%s" % (w, len(hits), " (показаны первые %d)" % args.limit if len(hits) >= args.limit else ""))
        for off, kind, _, ctx in hits:
            own = an.owner(off) if an else ""
            print("  @%-12d %-22s %s%s" % (off, kind, ctx, ("   [в: %s]" % own) if own else ""))

    if args.strings:
        for off, s in strings(src, args.min, args.filter):
            print("@%-12d %s" % (off, s))

    if args.hex is not None:
        off = parse_offset(args.hex)
        print(hexdump(src.read(off, args.length), off))
    src.close()
    return 0


# =====================================================================================
# окно (tkinter)
# =====================================================================================
def run_gui(initial=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class App:
        def __init__(self, root):
            self.root = root
            self.src = None
            self.an = None
            self.jobs = queue.Queue()
            self.stop = threading.Event()
            self.hex_offset = 0
            root.title("resviewer %s - просмотр файлов игры без распаковки" % VERSION)
            root.geometry("1200x760")
            mono = ("Consolas", 10) if sys.platform.startswith("win") else ("Courier", 10)
            self.mono = mono

            top = ttk.Frame(root, padding=6)
            top.pack(fill="x")
            ttk.Button(top, text="Открыть файл…", command=self.ask_open).pack(side="left")
            ttk.Button(top, text="Барабан: искать слова", command=self.drum).pack(side="left", padx=6)
            self.path_var = tk.StringVar(value="Файл не открыт. Нажмите «Открыть файл…» "
                                               "(resources.assets.resS, resources.assets, .pck, .exe …)")
            ttk.Label(top, textvariable=self.path_var).pack(side="left", padx=8)

            self.info = tk.Text(root, height=4, wrap="word", relief="flat", background=root.cget("background"))
            self.info.pack(fill="x", padx=8)
            self.info.configure(state="disabled")

            nb = ttk.Notebook(root)
            nb.pack(fill="both", expand=True, padx=6, pady=4)
            self.nb = nb
            self._build_inside(nb)
            self._build_search(nb)
            self._build_strings(nb)
            self._build_hex(nb)

            self.status = tk.StringVar(value="")
            ttk.Label(root, textvariable=self.status, anchor="w", padding=(8, 2)).pack(fill="x")
            root.after(100, self._poll)

        # ---------- вкладки ----------
        def _tree(self, parent, cols, widths):
            frame = ttk.Frame(parent)
            tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
            for c, w in zip(cols, widths):
                tree.heading(c, text=c)
                tree.column(c, width=w, stretch=(w > 300), anchor="w")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            return frame, tree

        def _text(self, parent):
            frame = ttk.Frame(parent)
            txt = tk.Text(frame, wrap="none", font=self.mono, undo=False)
            ys = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            xs = ttk.Scrollbar(frame, orient="horizontal", command=txt.xview)
            txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            ys.pack(side="right", fill="y")
            xs.pack(side="bottom", fill="x")
            txt.pack(side="left", fill="both", expand=True)
            return frame, txt

        def _build_inside(self, nb):
            tab = ttk.Frame(nb)
            nb.add(tab, text="Внутри файла")
            bar = ttk.Frame(tab)
            bar.pack(fill="x", pady=3)
            ttk.Label(bar, text="Фильтр:").pack(side="left")
            self.inside_filter = tk.StringVar()
            e = ttk.Entry(bar, textvariable=self.inside_filter, width=30)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda ev: self.fill_inside())
            ttk.Button(bar, text="Применить", command=self.fill_inside).pack(side="left")
            ttk.Button(bar, text="Показать картинку", command=self.show_image).pack(side="left", padx=12)
            ttk.Button(bar, text="Сохранить выбранное…", command=self.save_selected).pack(side="left")
            pane = ttk.PanedWindow(tab, orient="horizontal")
            pane.pack(fill="both", expand=True)
            f1, self.inside = self._tree(pane, ("№", "имя / путь", "тип", "адрес", "размер"), (50, 420, 150, 100, 90))
            f2, self.preview = self._text(pane)
            pane.add(f1, weight=1)
            pane.add(f2, weight=1)
            self.inside.bind("<<TreeviewSelect>>", self.on_inside_select)

        def _build_search(self, nb):
            tab = ttk.Frame(nb)
            nb.add(tab, text="Поиск")
            bar = ttk.Frame(tab)
            bar.pack(fill="x", pady=3)
            ttk.Label(bar, text="Слово:").pack(side="left")
            self.word = tk.StringVar(value="intrashot")
            e = ttk.Entry(bar, textvariable=self.word, width=30)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda ev: self.find())
            ttk.Button(bar, text="Найти", command=self.find).pack(side="left")
            ttk.Button(bar, text="Барабан (все слова)", command=self.drum).pack(side="left", padx=6)
            ttk.Label(bar, text="двойной щелчок - открыть место в hex / файл внутри").pack(side="left", padx=10)
            f, self.hits = self._tree(tab, ("адрес", "слово", "как записано", "в файле/объекте", "текст вокруг"),
                                      (110, 90, 150, 220, 600))
            f.pack(fill="both", expand=True)
            self.hits.bind("<Double-1>", self.on_hit_open)

        def _build_strings(self, nb):
            tab = ttk.Frame(nb)
            nb.add(tab, text="Строки")
            bar = ttk.Frame(tab)
            bar.pack(fill="x", pady=3)
            ttk.Label(bar, text="мин. длина:").pack(side="left")
            self.minlen = tk.IntVar(value=6)
            ttk.Spinbox(bar, from_=3, to=64, textvariable=self.minlen, width=4).pack(side="left", padx=4)
            ttk.Label(bar, text="содержит:").pack(side="left")
            self.sfilter = tk.StringVar()
            e = ttk.Entry(bar, textvariable=self.sfilter, width=30)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda ev: self.list_strings())
            ttk.Button(bar, text="Показать строки", command=self.list_strings).pack(side="left")
            f, self.strs = self._tree(tab, ("адрес", "строка"), (110, 900))
            f.pack(fill="both", expand=True)
            self.strs.bind("<Double-1>", lambda ev: self._goto_selected_offset(self.strs))

        def _build_hex(self, nb):
            tab = ttk.Frame(nb)
            nb.add(tab, text="Hex")
            bar = ttk.Frame(tab)
            bar.pack(fill="x", pady=3)
            ttk.Label(bar, text="адрес (0x… или число):").pack(side="left")
            self.hex_entry = tk.StringVar(value="0")
            e = ttk.Entry(bar, textvariable=self.hex_entry, width=16)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda ev: self.goto_hex(parse_offset(self.hex_entry.get())))
            ttk.Button(bar, text="Перейти", command=lambda: self.goto_hex(parse_offset(self.hex_entry.get()))).pack(side="left")
            ttk.Button(bar, text="◀ назад", command=lambda: self.goto_hex(self.hex_offset - 4096)).pack(side="left", padx=6)
            ttk.Button(bar, text="вперёд ▶", command=lambda: self.goto_hex(self.hex_offset + 4096)).pack(side="left")
            f, self.hex = self._text(tab)
            f.pack(fill="both", expand=True)

        # ---------- фоновая работа ----------
        def run_bg(self, title, func, done):
            self.status.set(title + " …")
            self.root.configure(cursor="watch")

            def worker():
                try:
                    res = func()
                    self.jobs.put((done, res, None))
                except Exception as e:  # noqa
                    self.jobs.put((done, None, e))
            threading.Thread(target=worker, daemon=True).start()

        def _poll(self):
            try:
                while True:
                    done, res, err = self.jobs.get_nowait()
                    self.root.configure(cursor="")
                    if err is not None:
                        self.status.set("Ошибка: %s" % err)
                        messagebox.showerror("Ошибка", str(err))
                    else:
                        done(res)
            except queue.Empty:
                pass
            self.root.after(100, self._poll)

        def _set_text(self, widget, text):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)

        # ---------- действия ----------
        def ask_open(self):
            path = filedialog.askopenfilename(title="Открыть файл игры", filetypes=[
                ("Все файлы", "*.*"), ("Unity", "*.resS *.assets *.resource"), ("Godot", "*.pck *.exe")])
            if path:
                self.open(path)

        def open(self, path):
            if self.src:
                self.src.close()
            self.src = Source(path)
            self.path_var.set("%s   (%s)" % (self.src.path, human(self.src.size)))
            for t in (self.inside, self.hits, self.strs):
                t.delete(*t.get_children())
            self._set_text(self.preview, "")
            self.goto_hex(0)
            src = self.src
            self.run_bg("Разбираю файл", lambda: Analysis(src), self._opened)

        def _opened(self, an):
            self.an = an
            text = "Тип: %s\n%s" % (an.kind, "\n".join("• " + n for n in an.notes))
            self.info.configure(state="normal")
            self.info.delete("1.0", "end")
            self.info.insert("1.0", text)
            self.info.configure(state="disabled")
            self.fill_inside()
            self.status.set("Готово: элементов внутри - %d" % len(an.rows))

        def fill_inside(self):
            if not self.an:
                return
            self.inside.delete(*self.inside.get_children())
            flt = self.inside_filter.get().lower().strip()
            shown = 0
            for i, r in enumerate(self.an.rows):
                if flt and flt not in r["label"].lower() and flt not in r["type"].lower():
                    continue
                off = "" if r.get("offset") is None else str(r["offset"])
                size = human(r["size"]) if r.get("size") else ""
                typ = r["type"] + ("  🔒" if r.get("encrypted") else "")
                self.inside.insert("", "end", iid=str(i), values=(i, r["label"], typ, off, size))
                shown += 1
            self.status.set("Показано %d из %d" % (shown, len(self.an.rows)))

        def _selected_row(self):
            sel = self.inside.selection()
            if not sel or not self.an:
                return None
            return self.an.rows[int(sel[0])]

        def on_inside_select(self, ev=None):
            row = self._selected_row()
            if row is None:
                return
            an = self.an
            self.run_bg("Читаю «%s»" % row["label"], lambda: an.describe(row),
                        lambda text: (self._set_text(self.preview, text), self.status.set(row["label"])))

        def show_image(self):
            row = self._selected_row()
            if row is None:
                return
            an = self.an

            def shown(png):
                if not png:
                    messagebox.showinfo("Картинка", "Этот элемент - не картинка (или её формат не читается).")
                    return
                win = tk.Toplevel(self.root)
                win.title(row["label"])
                img = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
                k = max(1, (max(img.width(), img.height()) + 899) // 900)
                if k > 1:
                    img = img.subsample(k, k)
                lbl = ttk.Label(win, image=img)
                lbl.image = img
                lbl.pack()
            self.run_bg("Готовлю картинку", lambda: an.image_png(row), shown)

        def save_selected(self):
            row = self._selected_row()
            if row is None:
                return
            name = os.path.basename(row["label"].replace("\\", "/")) or "data.bin"
            path = filedialog.asksaveasfilename(initialfile=name)
            if path:
                with open(path, "wb") as fh:
                    fh.write(self.an.raw_bytes(row))
                self.status.set("Сохранено: %s" % path)

        def _fill_hits(self, hits):
            self.hits.delete(*self.hits.get_children())
            for i, (off, kind, word, ctx) in enumerate(hits):
                own = self.an.owner(off) if self.an else ""
                self.hits.insert("", "end", iid=str(i), values=(off, word, kind, own, ctx))
            self.nb.select(1)
            self.status.set("Найдено совпадений: %d" % len(hits))

        def find(self):
            if not self.src:
                return
            w = self.word.get().strip()
            src = self.src
            an = self.an
            self.run_bg("Ищу «%s»" % w, lambda: search_all(src, an, [w]), self._fill_hits)

        def drum(self):
            if not self.src:
                messagebox.showinfo("Барабан", "Сначала откройте файл.")
                return
            src, an = self.src, self.an
            self.run_bg("Ищу слова барабана (%s)" % ", ".join(DRUM_WORDS), lambda: drum_report(src, an), self._fill_hits)

        def on_hit_open(self, ev=None):
            sel = self.hits.selection()
            if not sel:
                return
            off = int(self.hits.item(sel[0], "values")[0])
            own = self.an.owner(off) if self.an else ""
            if own:
                for i, r in enumerate(self.an.rows):
                    if r["label"] == own:
                        self.inside_filter.set("")
                        self.fill_inside()
                        self.inside.selection_set(str(i))
                        self.inside.see(str(i))
                        self.nb.select(0)
                        return
            self.goto_hex(off - 256)
            self.nb.select(3)

        def _goto_selected_offset(self, tree):
            sel = tree.selection()
            if sel:
                self.goto_hex(int(tree.item(sel[0], "values")[0]) - 256)
                self.nb.select(3)

        def list_strings(self):
            if not self.src:
                return
            src, mn, flt = self.src, max(3, int(self.minlen.get() or 6)), self.sfilter.get()

            def done(items):
                self.strs.delete(*self.strs.get_children())
                for i, (off, s) in enumerate(items):
                    self.strs.insert("", "end", iid=str(i), values=(off, s))
                self.status.set("Строк: %d%s" % (len(items), " (предел)" if len(items) >= MAX_STRINGS else ""))
            self.run_bg("Собираю строки", lambda: strings(src, mn, flt), done)

        def goto_hex(self, off):
            if not self.src:
                return
            off = max(0, min(int(off), max(0, self.src.size - 1)))
            off -= off % 16
            self.hex_offset = off
            self.hex_entry.set("0x%X" % off)
            self._set_text(self.hex, hexdump(self.src.read(off, 4096), off))

    global _APP
    root = tk.Tk()
    app = _APP = App(root)
    if initial:
        root.after(200, lambda: app.open(initial))
    root.mainloop()


# =====================================================================================
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Просмотр файлов игры без распаковки (resS, assets, pck, exe).")
    ap.add_argument("file", nargs="?", help="файл для просмотра")
    ap.add_argument("--info", action="store_true", help="что это за файл")
    ap.add_argument("--list", action="store_true", help="список файлов / объектов внутри")
    ap.add_argument("--show", metavar="N|ИМЯ", help="показать элемент из --list")
    ap.add_argument("--find", action="append", metavar="СЛОВО", help="найти слово (можно несколько раз)")
    ap.add_argument("--drum", action="store_true", help="найти все слова барабана")
    ap.add_argument("--strings", action="store_true", help="вывести текстовые строки")
    ap.add_argument("--min", type=int, default=6, help="мин. длина строки (по умолчанию 6)")
    ap.add_argument("--filter", help="только строки, содержащие это")
    ap.add_argument("--hex", metavar="АДРЕС", help="hex с адреса (0x… или число)")
    ap.add_argument("--length", type=int, default=512, help="сколько байт для --hex")
    ap.add_argument("--limit", type=int, default=200, help="предел совпадений на слово в консоли")
    args = ap.parse_args(argv)
    console = any([args.info, args.list, args.show, args.find, args.drum, args.strings, args.hex is not None])
    if console:
        if not args.file:
            ap.error("укажите файл")
        return run_cli(args)
    try:
        run_gui(args.file)
    except ImportError:
        print("Окно недоступно (нет модуля tkinter). Используйте консольный режим, например:\n"
              "  python resviewer.py ФАЙЛ --drum\n  python resviewer.py ФАЙЛ --list")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

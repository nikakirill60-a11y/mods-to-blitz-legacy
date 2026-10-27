#!/usr/bin/env python3
"""SCG/SC2 Viewer - desktop launcher for scgviewer.html (WoT Blitz .sc2 / .scg / .dvpl models).

    SCGViewer.pyw                      empty window, drag files/folders into it (or press "Открыть")
    SCGViewer.pyw Tank.sc2.dvpl        open this model (textures from images/ are found automatically)
    SCGViewer.pyw Data/3d/Tanks/USSR   open a folder: every model in it appears in the model list

Windows: double-click the file, drop a model/folder onto it, or "Open with" -> SCGViewer.bat.
Only the Python standard library is used.  A tiny local web server (127.0.0.1 only) serves the
viewer and the chosen folder; the window is Edge/Chrome in "app" mode (no tabs, no address bar).
The program exits by itself when the window is closed.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
VIEWER = os.path.join(HERE, "scgviewer.html")
EXTS = (".sc2", ".scg", ".dvpl", ".dds", ".pvr", ".png", ".webp", ".jpg", ".jpeg")
MAX_FILES = 50000
IDLE_EXIT = 180          # seconds without a ping -> exit (background windows ping rarely)


def collect(target: str | None) -> tuple[str | None, list[str], str | None]:
    """-> (root dir, relative file list, model to open first)."""
    if not target:
        return None, [], None
    target = os.path.abspath(target)
    if os.path.isfile(target):
        root, first = os.path.dirname(target), os.path.basename(target)
    elif os.path.isdir(target):
        root, first = target, None
    else:
        raise SystemExit(f"Не найдено: {target}")
    files: list[str] = []
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for n in names:
            if n.lower().endswith(EXTS):
                files.append(os.path.relpath(os.path.join(dirpath, n), root).replace(os.sep, "/"))
                if len(files) >= MAX_FILES:
                    break
        if len(files) >= MAX_FILES:
            break
    files.sort()
    if first and first in files:          # the chosen model goes first -> opened automatically
        files.remove(first)
        files.insert(0, first)
    return root, files, first


class State:
    root: str | None = None
    files: list[str] = []
    first: str | None = None
    last_ping = time.time()
    seen_page = False
    lock = threading.Lock()


def make_handler(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/octet-stream"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(u.path)
            if path in ("/", "/index.html"):
                with open(VIEWER, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if path == "/api/list":
                state.seen_page = True
                state.last_ping = time.time()
                body = {"root": state.root, "files": state.files, "open": state.first}
                return self._send(200, json.dumps(body, ensure_ascii=False).encode(), "application/json")
            if path == "/api/ping":
                state.seen_page = True
                state.last_ping = time.time()
                return self._send(200, b"ok", "text/plain")
            if path.startswith("/f/") and state.root:
                full = os.path.realpath(os.path.join(state.root, path[3:]))
                root = os.path.realpath(state.root)
                try:
                    inside = os.path.commonpath([full, root]) == root
                except ValueError:              # different drives on Windows
                    inside = False
                if not inside or not os.path.isfile(full):
                    return self._send(404, b"not found", "text/plain")
                with open(full, "rb") as fh:
                    data = fh.read()
                return self._send(200, data, mimetypes.guess_type(full)[0] or "application/octet-stream")
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            if path == "/api/bye":           # window closed
                state.last_ping = 0
            self._send(200, b"ok", "text/plain")

    return H


def browser_candidates() -> list[str]:
    c: list[str] = []
    if sys.platform.startswith("win"):
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            c += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                  os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                  os.path.join(base, "Chromium", "Application", "chrome.exe"),
                  os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                  os.path.join(base, "Yandex", "YandexBrowser", "Application", "browser.exe")]
    elif sys.platform == "darwin":
        c += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for name in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            c.append(p)
    return [p for p in c if os.path.isfile(p)]


def open_window(url: str) -> bool:
    """Open url in its own app window; True if an app-mode browser was found."""
    profile = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SCGViewer", "profile")
    for exe in browser_candidates():
        try:
            os.makedirs(profile, exist_ok=True)
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1400,900",
                              "--no-first-run", "--no-default-browser-check"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    webbrowser.open(url)
    return False


def message(text: str):
    """Show an error even when started with pythonw (no console)."""
    try:
        import tkinter
        from tkinter import messagebox
        r = tkinter.Tk(); r.withdraw()
        messagebox.showerror("SCG/SC2 Viewer", text)
        r.destroy()
    except Exception:
        print(text, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    no_window = "--no-window" in args
    args = [a for a in args if a != "--no-window"]
    port = 0
    if "--port" in args:
        i = args.index("--port"); port = int(args[i + 1]); del args[i:i + 2]
    if not os.path.isfile(VIEWER):
        message(f"Не найден {VIEWER}.\nДержите SCGViewer.pyw в одной папке с scgviewer.html.")
        return 1
    state = State()
    try:
        state.root, state.files, state.first = collect(args[0] if args else None)
    except SystemExit as e:
        message(str(e))
        return 1
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    url = f"http://127.0.0.1:{srv.server_address[1]}/?list=/api/list&app=1"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("SCG/SC2 Viewer:", url)
    if state.root:
        print(f"папка: {state.root}  файлов: {len(state.files)}")
    if no_window:
        print("(--no-window) Ctrl+C для выхода")
    else:
        open_window(url)
    started = time.time()
    try:
        while True:
            time.sleep(1)
            if no_window:
                continue
            idle = time.time() - state.last_ping
            if state.seen_page and (state.last_ping == 0 or idle > IDLE_EXIT):
                time.sleep(2)                       # page reload sends bye + ping right after
                if state.last_ping == 0 or time.time() - state.last_ping > IDLE_EXIT:
                    break
            if not state.seen_page and time.time() - started > 120:
                message("Окно просмотрщика не открылось.\nОткройте вручную в браузере:\n" + url)
                started = time.time() + 10 ** 9
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

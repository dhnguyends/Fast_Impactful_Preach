"""FastImpact Preach for Windows: starts the local web app and opens it in the browser.

A small window shows that the app is running (Open in browser / Data folder / Quit). Everything the
user creates lives in %LOCALAPPDATA%\\FastImpactPreach (projects, API keys in .env, fonts, logs, the
speech model download cache), never in the read-only program folder.
"""
import ctypes
import json
import logging
import logging.handlers
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "FastImpact Preach"
FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))  # static/, assets/, fip/, app/
EXE_DIR = Path(sys.executable).parent if FROZEN else BUNDLE                 # ffmpeg\, model\ (installed next to the exe)
HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FastImpactPreach"
DATA = HOME / "data"
LOGS = HOME / "logs"
PREFERRED_PORTS = [8000] + list(range(8765, 8775))   # 8000 first: the redirect URI registered for YouTube sign-in
if not FROZEN:                                       # running from the source tree (python win11\src\launcher.py)
    sys.path.insert(0, str(BUNDLE))

# ---------- language (Windows display language) ----------
try:
    FR = (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x0C
except Exception:  # noqa: BLE001
    FR = False
TXT = {
    "starting": ("Démarrage…", "Starting…"),
    "running": ("FastImpact Preach est lancé", "FastImpact Preach is running"),
    "hint": ("L'application s'ouvre dans votre navigateur. Gardez cette fenêtre ouverte pendant que vous l'utilisez.",
             "The app opens in your web browser. Keep this window open while you use it."),
    "open": ("Ouvrir dans le navigateur", "Open in browser"),
    "data": ("Dossier des données", "Data folder"),
    "quit": ("Quitter", "Quit"),
    "busy": ("Un traitement ou une publication est en cours. Quitter l'arrête.\n\nQuitter quand même ?",
             "Processing or publishing is in progress. Quitting stops it.\n\nQuit anyway?"),
    "failed": ("FastImpact Preach n'a pas pu démarrer :\n\n{e}\n\nJournal : {log}",
               "FastImpact Preach could not start:\n\n{e}\n\nLog: {log}"),
}


def tr(key: str, **kw) -> str:
    return TXT[key][0 if FR else 1].format(**kw)


# ---------- environment (before the app is imported) ----------
def prepare_environment() -> None:
    for d in (DATA, LOGS, HOME / "models", DATA / "fonts"):
        d.mkdir(parents=True, exist_ok=True)
    os.environ["FIP_DATA_DIR"] = str(DATA)
    os.environ["FIP_ENV_FILE"] = str(HOME / ".env")
    os.environ["FIP_FONTS_DIR"] = str(DATA / "fonts")          # writable: the app may copy fonts there
    os.environ.setdefault("HF_HOME", str(HOME / "models"))     # speech model download cache
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    for f in (BUNDLE / "assets" / "fonts").glob("*.tt[fc]"):   # caption fonts shipped with the app
        dst = DATA / "fonts" / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
    ff = EXE_DIR / "ffmpeg"
    if (ff / "ffmpeg.exe").exists():
        os.environ["FFMPEG_BIN"] = str(ff / "ffmpeg.exe")
        os.environ["FFPROBE_BIN"] = str(ff / "ffprobe.exe")
    model = EXE_DIR / "model" / "faster-whisper-large-v3-turbo"
    if (model / "model.bin").exists() and not os.environ.get("WHISPER_MODEL"):
        os.environ["WHISPER_MODEL"] = str(model)                # installed with the app: no first-use download
    # NVIDIA GPU: CUDA 12 / cuDNN 9 libraries installed on this PC are used when present (else CPU).
    for var in ("CUDA_PATH", "CUDNN_PATH"):
        p = os.environ.get(var)
        if p and (Path(p) / "bin").exists():
            try:
                os.add_dll_directory(str(Path(p) / "bin"))
            except OSError:
                pass


def setup_logging() -> Path:
    log_file = LOGS / "fastimpact.log"
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    if FROZEN:  # windowed exe: no console — print() / progress bars must not crash, they go to the log
        stream = open(LOGS / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    return log_file


# ---------- port / single instance ----------
def _ours(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return "max_clips" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port() -> tuple[int, bool]:
    """(port, already running). Another FastImpact Preach already open → reuse it."""
    for p in PREFERRED_PORTS:
        if _free(p):
            return p, False
        if _ours(p):
            return p, True
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1], False


# ---------- server ----------
class Server:
    def __init__(self, port: int):
        import uvicorn
        from app.main import app
        self.url = f"http://localhost:{port}"   # Google sign-in redirect URIs are registered with localhost
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                                    access_log=False))
        self.thread = threading.Thread(target=self.server.run, name="web", daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(240):                       # first start can be slow (antivirus scans the files)
            if self.server.started:
                return
            if not self.thread.is_alive():
                raise RuntimeError("the web server stopped while starting (see the log)")
            time.sleep(0.25)
        raise RuntimeError("the web server did not start in time")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def busy() -> bool:
    try:
        from app import jobs
        return jobs._jobs.unfinished_tasks > 0
    except Exception:  # noqa: BLE001
        return False


# ---------- window ----------
def window(srv: Server) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    ico = BUNDLE / "assets" / "app.ico"
    if ico.exists():
        try:
            root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
    frm = ttk.Frame(root, padding=18)
    frm.grid()
    ttk.Label(frm, text=tr("running"), font=("Segoe UI", 13, "bold")).grid(column=0, row=0, columnspan=3, sticky="w")
    link = ttk.Label(frm, text=srv.url, foreground="#5b2bd6", cursor="hand2", font=("Segoe UI", 10, "underline"))
    link.grid(column=0, row=1, columnspan=3, sticky="w", pady=(4, 2))
    link.bind("<Button-1>", lambda e: webbrowser.open(srv.url))
    ttk.Label(frm, text=tr("hint"), wraplength=380, foreground="#55596a").grid(column=0, row=2, columnspan=3,
                                                                                sticky="w", pady=(2, 12))

    def quit_app():
        if busy() and not messagebox.askyesno(APP_NAME, tr("busy"), icon="warning"):
            return
        root.destroy()

    ttk.Button(frm, text=tr("open"), command=lambda: webbrowser.open(srv.url)).grid(column=0, row=3, sticky="w")
    ttk.Button(frm, text=tr("data"), command=lambda: subprocess.Popen(["explorer", str(DATA)])).grid(
        column=1, row=3, padx=8)
    ttk.Button(frm, text=tr("quit"), command=quit_app).grid(column=2, row=3, sticky="e")
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()


def main() -> int:
    prepare_environment()
    log_file = setup_logging()
    log = logging.getLogger("launcher")
    port, running = pick_port()
    if os.environ.get("FIP_SMOKE_TEST") == "1" and running:   # build check: never reuse another instance
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port, running = s.getsockname()[1], False
    if running:                                     # second launch: just show the open app
        webbrowser.open(f"http://localhost:{port}")
        return 0
    try:
        srv = Server(port)
        srv.start()
    except Exception as e:  # noqa: BLE001
        log.exception("Start failed")
        try:
            import tkinter as tk
            from tkinter import messagebox
            tk.Tk().withdraw()
            messagebox.showerror(APP_NAME, tr("failed", e=e, log=log_file))
        except Exception:  # noqa: BLE001
            pass
        return 1
    log.info("%s running at %s (data: %s)", APP_NAME, srv.url, DATA)
    if os.environ.get("FIP_SMOKE_TEST") == "1":     # build check: the packaged app starts and answers, then exits
        ok = _ours(port)
        log.info("Smoke test: %s", "OK" if ok else "FAILED")
        srv.stop()
        return 0 if ok else 2
    webbrowser.open(srv.url)
    try:
        window(srv)
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

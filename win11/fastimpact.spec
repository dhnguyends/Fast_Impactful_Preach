# PyInstaller spec: FastImpact Preach for Windows (one folder, windowed launcher).
# Built by win11\build.ps1 — run that script rather than PyInstaller directly.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent          # project root (SPECPATH = the win11 folder)
WIN = Path(SPECPATH)

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "assets" / "fonts"), "assets/fonts"),
    (str(ROOT / "assets" / "brand"), "assets/brand"),
    (str(WIN / "build" / "app.ico"), "assets"),
]
datas += collect_data_files("faster_whisper")       # Silero VAD model
datas += collect_data_files("cv2")                  # Haar cascades (face tracking)
datas += collect_data_files("certifi")
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")

hidden = (collect_submodules("uvicorn") + collect_submodules("app") + collect_submodules("fip")
          + ["multipart", "python_multipart", "dotenv", "tkinter", "tkinter.ttk", "tkinter.messagebox"])

a = Analysis(
    [str(WIN / "src" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["torch", "whisper", "pytest", "matplotlib", "IPython", "notebook", "pandas", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FastImpactPreach",
    icon=str(WIN / "build" / "app.ico"),
    console=False,
    version=str(WIN / "build" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FastImpactPreach")

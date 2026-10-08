"""Build helpers: app icon (.ico), Windows version resource, and a copy of the speech model.
Usage: python make_assets.py <build dir> <version> [--model]"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_REPO = "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"


def icon(build: Path) -> None:
    from PIL import Image
    src = ROOT / "docs" / "FastImpactPreach_logo_120.png"
    with Image.open(src) as im:
        im = im.convert("RGBA")
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        big = sq.resize((256, 256), Image.LANCZOS)
        big.save(build / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def version_info(build: Path, version: str) -> None:
    nums = (version.split(".") + ["0", "0", "0", "0"])[:4]
    tup = ", ".join(str(int(n)) for n in nums)
    (build / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[StringFileInfo([StringTable('040C04B0', [
    StringStruct('CompanyName', 'Eglise Evangélique Internationale de Genève (EEIG GVA)'),
    StringStruct('FileDescription', 'FastImpact Preach'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('InternalName', 'FastImpactPreach'),
    StringStruct('OriginalFilename', 'FastImpactPreach.exe'),
    StringStruct('ProductName', 'FastImpact Preach'),
    StringStruct('ProductVersion', '{version}')])]),
  VarFileInfo([VarStruct('Translation', [0x040C, 1200])])])
""", encoding="utf-8")


def model(build: Path) -> bool:
    """Copy large-v3-turbo from the Hugging Face cache (follows the cache's symlinks)."""
    import os
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub" / MODEL_REPO / "snapshots"
    snaps = sorted(hub.glob("*/model.bin"), key=lambda p: p.stat().st_mtime) if hub.exists() else []
    if not snaps:
        print(f"  speech model not found in {hub}")
        return False
    src = snaps[-1].parent
    dst = build / "model" / "faster-whisper-large-v3-turbo"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        target = dst / f.name
        if not target.exists() or target.stat().st_size != f.stat().st_size:
            shutil.copyfile(f, target)                # copyfile follows symlinks → real data
    print(f"  speech model: {sum(p.stat().st_size for p in dst.iterdir()) / 1e9:.2f} GB")
    return True


if __name__ == "__main__":
    build, version = Path(sys.argv[1]), sys.argv[2]
    build.mkdir(parents=True, exist_ok=True)
    icon(build)
    version_info(build, version)
    if "--model" in sys.argv:
        sys.exit(0 if model(build) else 3)

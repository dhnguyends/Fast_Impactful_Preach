"""Environment-driven settings (paths, binaries, models, API credentials)."""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("FIP_ENV_FILE", ROOT / ".env"))  # on a server: inside the persistent data volume
load_dotenv(ENV_FILE)

# Server mode (public deployment): no "file on this PC" projects, secure cookies, login required
# when FIP_APP_PASSWORD is set (see app/auth.py).
SERVER_MODE = os.getenv("FIP_SERVER_MODE", "0").lower() in ("1", "true", "yes")

DATA_DIR = Path(os.getenv("FIP_DATA_DIR", ROOT / "data"))
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / "cache"
STATIC_DIR = ROOT / "static"
FONTS_DIR = Path(os.getenv("FIP_FONTS_DIR", ROOT / "assets" / "fonts"))
WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
SYSTEM_FONT_DIRS = [WIN_FONTS, Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                    Path.home() / ".local/share/fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]


def _find_font(*names: str) -> str:
    for d in SYSTEM_FONT_DIRS:
        if not d.exists():
            continue
        for n in names:
            hit = next(iter(d.rglob(n)), None) if d != WIN_FONTS else (d / n if (d / n).exists() else None)
            if hit:
                return str(hit)
    return ""


# Colour emoji font: Segoe UI Emoji on Windows, Noto Color Emoji on Linux, Apple Color Emoji on macOS.
EMOJI_FONT = os.getenv("EMOJI_FONT") or _find_font("seguiemj.ttf", "NotoColorEmoji.ttf", "Apple Color Emoji.ttc")

FFMPEG = os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.getenv("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto | cuda | cpu

OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30

# Only an explicit API key is used. Inherited ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL (set for
# other tools) are deliberately ignored so sermons are never sent to an unexpected endpoint.
CLAUDE_BASE_URL = os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com")


def claude_model() -> str:
    return os.getenv("CLAUDE_MODEL", "claude-opus-5-5")


def claude_effort() -> str:
    return os.getenv("CLAUDE_EFFORT", "high")


def ensure_dirs() -> None:
    for d in (PROJECTS_DIR, CACHE_DIR, FONTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def reload_env() -> None:
    load_dotenv(ENV_FILE, override=True)


def api_key() -> str:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip()


def workspace_id() -> str:
    """Needed only for API keys that are not scoped to a workspace."""
    return (os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip()


def api_key_configured() -> bool:
    return bool(api_key())


def mask(secret: str) -> str:
    """Show only the first and last 4 characters."""
    if not secret:
        return ""
    if len(secret) <= 10:
        return secret[:2] + "…"
    return f"{secret[:4]}…{secret[-4:]}"


def save_env(values: dict) -> None:
    """Update (or add) keys in .env, keeping every other line untouched."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    todo = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in todo:
            lines[i] = f"{key}={todo.pop(key)}"
    lines += [f"{k}={v}" for k, v in todo.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in values.items():
        os.environ[k] = v


BRAND_DEFAULT_LOGO = ROOT / "assets" / "brand" / "logo.png"   # shipped with the app (EEIG)


def brand_logo() -> Path | None:
    """End-card logo: the one uploaded in the app (data/brand/logo.png), else the shipped default."""
    for p in (DATA_DIR / "brand" / "logo.png", BRAND_DEFAULT_LOGO):
        if p.is_file():
            return p
    return None

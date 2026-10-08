"""Thin, cancellable wrappers around ffmpeg / ffprobe."""
import json
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable

from . import settings
from .errors import Cancelled, ConfigError, MediaError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def check_binaries() -> None:
    for b in (settings.FFMPEG, settings.FFPROBE):
        if not (Path(b).is_file() or shutil.which(b)):
            raise ConfigError(f"'{b}' was not found. Install FFmpeg and add it to PATH (or set FFMPEG_BIN).")


def run(args: list, cwd: Path | None = None, timeout: float | None = None,
        cancelled: Callable[[], bool] | None = None, error_cls=MediaError) -> str:
    """Run a command; kill it if `cancelled()` turns true. Returns stdout."""
    proc = subprocess.Popen([str(a) for a in args], cwd=str(cwd) if cwd else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=NO_WINDOW)
    start = time.monotonic()
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if (cancelled and cancelled()) or (timeout and time.monotonic() - start > timeout):
                proc.kill()
                proc.communicate()
                if cancelled and cancelled():
                    raise Cancelled("Cancelled.")
                raise error_cls(f"{Path(str(args[0])).name} timed out.")
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip()[-1500:]
        raise error_cls(f"{Path(str(args[0])).name} failed: {tail}")
    return out.decode("utf-8", "replace")


def probe(path: Path, need_audio: bool = True) -> dict:
    if not Path(path).is_file():
        raise MediaError(f"File not found: {path}")
    data = json.loads(run([settings.FFPROBE, "-v", "error", "-show_entries",
                           "format=duration:stream=codec_type,width,height,r_frame_rate",
                           "-of", "json", path]))
    info = {"duration": float(data.get("format", {}).get("duration") or 0),
            "width": 0, "height": 0, "fps": 30.0, "has_video": False, "has_audio": False}
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info["has_video"]:
            info.update(has_video=True, width=s.get("width", 0), height=s.get("height", 0))
            num, _, den = (s.get("r_frame_rate") or "30/1").partition("/")
            try:
                info["fps"] = float(num) / float(den or 1) or 30.0
            except (ValueError, ZeroDivisionError):
                pass
        elif s.get("codec_type") == "audio":
            info["has_audio"] = True
    if info["duration"] <= 0:
        raise MediaError("Could not read the duration of this media file.")
    if need_audio and not info["has_audio"]:
        raise MediaError("This file has no audio track, so there is nothing to transcribe.")
    return info


def extract_audio(src: Path, dst: Path, cancelled=None) -> Path:
    """16 kHz mono PCM — the format Whisper works on."""
    run([settings.FFMPEG, "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", dst], cancelled=cancelled)
    return dst


def thumbnail(src: Path, dst: Path, at: float, width: int = 480) -> Path:
    run([settings.FFMPEG, "-y", "-v", "error", "-ss", f"{max(0.0, at):.2f}", "-i", src,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", dst])
    return dst


@lru_cache(maxsize=1)
def video_encoder() -> tuple:
    """NVENC when the GPU supports it, otherwise libx264."""
    try:
        run([settings.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
        return ("-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0",
                "-profile:v", "high", "-pix_fmt", "yuv420p")
    except Exception:
        return ("-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p")

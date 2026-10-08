"""Web projects: JSON storage + one background worker (GPU work is serialised) that drives
fip.pipeline and mirrors its progress into project.json for the UI."""
import json
import logging
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path

from fip import ffmpeg, pipeline, settings, subtitles, transcribe
from fip.errors import Cancelled, FipError
from fip.models import MAX_CLIPS, Options, parse_time, passages_of
from fip.pipeline import NeedsRange

log = logging.getLogger(__name__)

STEPS = [("upload", "Uploading"), ("create", "Create project"), ("process", "Process video"),
         ("find", "Find best parts"), ("edit", "Edit clips"), ("finalize", "Finalize")]

_lock = threading.RLock()
_jobs: "queue.Queue[tuple]" = queue.Queue()
_cancel: set = set()


# ---------- storage ----------

def pdir(pid: str) -> Path:
    return settings.PROJECTS_DIR / pid


def exists(pid: str) -> bool:
    return (pdir(pid) / "project.json").exists()


def load(pid: str) -> dict:
    with _lock:
        return json.loads((pdir(pid) / "project.json").read_text(encoding="utf-8"))


def save(p: dict) -> dict:
    with _lock:
        path = pdir(p["id"]) / "project.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return p


def update(pid: str, **fields) -> dict:
    with _lock:
        p = load(pid)
        p.update(fields)
        return save(p)


def options(p: dict) -> Options:
    return Options(**(p.get("options") or {}))


def set_step(pid: str, key: str, state: str, progress: float | None = None, message: str = ""):
    with _lock:
        p = load(pid)
        st = p["steps"][key]
        st["state"] = state
        if progress is not None:
            st["progress"] = round(progress, 4)
        st["message"] = message
        p["step"] = key
        save(p)


def set_clip(pid: str, index: int, **fields):
    with _lock:
        p = load(pid)
        for c in p["clips"]:
            if c["index"] == index:
                c.update(fields)
        save(p)


def reset_steps(p: dict, keys=("find", "edit", "finalize")):
    for k in keys:
        p["steps"][k].update(state="pending", progress=0, message="")


def list_projects() -> list:
    out = []
    if not settings.PROJECTS_DIR.exists():
        return out
    for d in settings.PROJECTS_DIR.iterdir():
        f = d / "project.json"
        if f.exists():
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                out.append({k: p.get(k) for k in ("id", "title", "status", "created", "media", "thumb", "step")}
                           | {"clips": len(p.get("clips", []))})
            except (OSError, json.JSONDecodeError):
                log.warning("Unreadable project file %s", f)
    return sorted(out, key=lambda x: x["created"] or 0, reverse=True)


def create_project(title: str, source_kind: str, opts: dict) -> dict:
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    pdir(pid).mkdir(parents=True)
    p = {"id": pid, "title": title, "created": time.time(), "status": "uploading",
         "source_kind": source_kind, "source": None, "source_url": None,
         "options": Options(**(opts or {})).model_dump(),
         "step": "upload", "steps": {k: {"label": lbl, "state": "pending", "progress": 0, "message": ""}
                                     for k, lbl in STEPS},
         "media": None, "thumb": None, "analysis": None, "clips": [], "error": None}
    return save(p)


def delete_project(pid: str):
    _cancel.add(pid)
    shutil.rmtree(pdir(pid), ignore_errors=True)


# ---------- queue ----------

def enqueue(pid: str):
    _cancel.discard(pid)
    update(pid, status="queued", error=None)
    _jobs.put(("pipeline", pid))


def enqueue_render(pid: str, index: int):
    set_clip(pid, index, status="pending", error=None)
    _jobs.put(("render", pid, index))


def _set_publish(pid: str, index: int, net: str, **fields):
    with _lock:
        p = load(pid)
        for c in p["clips"]:
            if c["index"] == index:
                c.setdefault("publish", {}).setdefault(net, {}).update(fields, at=time.time())
        save(p)


def enqueue_publish(pid: str, index: int, net: str, privacy: str, batch: int):
    _set_publish(pid, index, net, status="queued", error=None, url=None, progress=0, batch=batch,
                 note="", state=None)
    _jobs.put(("publish", pid, index, net, privacy))


_limits: dict = {}        # network → (message, until): a quota / upload limit was hit, skip further uploads
LIMIT_PAUSE = 30 * 60


def publish_one(pid: str, index: int, net: str, privacy: str):
    """Upload one rendered short to a social network (runs in the worker, one at a time).
    Status: queued → uploading (progress 0..1) → processing → done (state "online") | error."""
    from . import social
    p = load(pid)
    clip = next((c for c in p["clips"] if c["index"] == index), None)
    if clip is None:
        return
    hit = _limits.get(net)
    if hit and time.time() < hit[1]:   # same limit as the previous short: do not call the API again
        _set_publish(pid, index, net, status="error", error=hit[0])
        return
    _set_publish(pid, index, net, status="uploading", progress=0)
    last = {"phase": "uploading", "frac": 0.0}

    def progress(phase: str, frac: float):
        if phase != last["phase"] or frac - last["frac"] >= 0.04 or frac >= 1:  # throttle disk writes
            last.update(phase=phase, frac=frac)
            _set_publish(pid, index, net, status=phase, progress=round(frac, 3))
    try:
        res = social.publish(pdir(pid) / "clips" / (clip.get("video") or ""), clip, net, privacy, progress=progress)
        _set_publish(pid, index, net, status="done", progress=1, url=res["url"], state=res["state"],
                     privacy=res.get("privacy"), note=res.get("note", ""), account=res.get("account", ""))
        log.info("Published %s clip %d to %s: %s (%s)", pid, index + 1, net, res["url"], res["state"])
        _limits.pop(net, None)
    except social.LimitError as e:
        _limits[net] = (str(e), time.time() + LIMIT_PAUSE)
        log.warning("%s limit reached (%s): the rest of the batch is skipped", net, e.reason)
        _set_publish(pid, index, net, status="error", error=str(e)[:400])
    except FipError as e:
        _set_publish(pid, index, net, status="error", error=str(e)[:400])
    except Exception as e:  # network errors etc.: never kill the worker
        log.exception("Publishing crashed")
        _set_publish(pid, index, net, status="error", error=f"Unexpected error: {str(e)[:300]}")


def cancel(pid: str):
    _cancel.add(pid)
    update(pid, status="cancelled")


def _worker():
    while True:
        job = _jobs.get()
        try:
            pid = job[1]
            if exists(pid) and pid not in _cancel:
                if job[0] == "pipeline":
                    run(pid)
                elif job[0] == "publish":
                    publish_one(pid, *job[2:])
                else:
                    render_one(pid, job[2])
        except Exception:
            log.exception("Job %s crashed", job)
        finally:
            _jobs.task_done()


def start_worker():
    settings.ensure_dirs()
    subtitles.ensure_fonts()
    for p in list_projects():  # projects interrupted by a restart are resumed
        if p["status"] in ("queued", "running"):
            enqueue(p["id"])
    threading.Thread(target=_worker, daemon=True, name="pipeline").start()


# ---------- the pipeline, step by step ----------

def run(pid: str):
    p = update(pid, status="running")
    d = pdir(pid)
    opts = options(p)
    cancelled = lambda: pid in _cancel  # noqa: E731
    step = "upload"
    try:
        if p["source_kind"] == "url" and not p.get("source"):
            set_step(pid, step, "active", 0, "Downloading…")
            p = update(pid, source=str(_download(pid, p["source_url"], d)))
        set_step(pid, "upload", "done", 1)
        src = Path(p["source"])

        step = "create"
        set_step(pid, step, "active", 0.3)
        info = ffmpeg.probe(src)
        thumb = None
        if info["has_video"]:
            thumb = ffmpeg.thumbnail(src, d / "thumb.jpg", at=min(30.0, info["duration"] / 4)).name
        update(pid, media=info, thumb=thumb)
        set_step(pid, step, "done", 1)

        step = "process"
        tr = pipeline.get_transcript(src, info, d, opts, progress=lambda f, m: set_step(pid, "process", "active", f, m),
                                     cancelled=cancelled)
        set_step(pid, step, "done", 1, f"{len(tr['words'])} words · language: {tr['language']}")

        step = "find"
        try:
            sel = pipeline.select(tr, info, d, opts, title=p.get("title", ""),
                                  progress=lambda f, m: set_step(pid, "find", "active", f, m))
        except NeedsRange:
            set_step(pid, step, "waiting", 0, "Waiting for you: choose the part of the video to turn into a short")
            update(pid, status="awaiting_input")
            return
        n = len(sel.clips)
        set_step(pid, step, "done", 1, f"{n} clip{'s' if n > 1 else ''} " + ("ready" if sel.fixed else "proposed"))
        if cancelled():
            raise Cancelled("Cancelled.")

        step = "edit"
        sel = pipeline.refine(sel, tr, d, opts, progress=lambda f, m: set_step(pid, "edit", "active", f, m))
        previous = {c["index"]: c for c in load(pid).get("clips", [])}
        for c in sel.clips:  # keep already-rendered clips whose boundaries did not change
            old = previous.get(c["index"])
            same = old and old.get("status") == "done" and passages_of(old) == passages_of(c)
            if same:
                c.update({k: old[k] for k in ("status", "video", "thumb", "srt", "start", "end", "duration",
                                              "text", "version", "slide_spans", "slides", "segments", "broll", "broll_used") if k in old})
            else:
                c["status"] = "pending"
        update(pid, clips=sel.clips, analysis=sel.meta)
        set_step(pid, step, "done", 1)

        step = "finalize"
        clips = pipeline.render_all(src, info, tr, sel.clips, d, opts,
                                    progress=lambda f, m: set_step(pid, "finalize", "active", f, m),
                                    on_clip=lambda i, f: set_clip(pid, i, **f, **({"version": int(time.time())}
                                                                                  if f.get("status") == "done" else {})),
                                    cancelled=cancelled)
        pipeline.export_json(clips, sel.meta, d / "clips.json")
        failed = sum(c.get("status") == "error" for c in clips)
        set_step(pid, step, "done", 1, "All clips ready" if not failed else f"{failed} clip(s) failed")
        update(pid, status="done", step="done")
    except Cancelled:
        update(pid, status="cancelled")
    except FipError as e:
        set_step(pid, step, "error", None, str(e)[:500])
        update(pid, status="error", error=f"{dict(STEPS)[step]}: {str(e)[:800]}")
    except Exception as e:  # a bug, not an expected failure: keep the traceback in the log
        log.exception("Pipeline crashed for %s", pid)
        set_step(pid, step, "error", None, f"Unexpected error: {e}")
        update(pid, status="error", error=f"{dict(STEPS)[step]}: unexpected error ({e}). See the server log.")


def render_one(pid: str, index: int):
    """Re-render one clip (after Adjust)."""
    from fip.render import render_clip
    p = load(pid)
    clip = next((c for c in p["clips"] if c["index"] == index), None)
    if clip is None:
        return
    set_clip(pid, index, status="rendering", error=None)
    try:
        tr = transcribe.load(pdir(pid) / "transcript.json")
        opts = options(p)
        if opts.broll and clip.get("broll"):
            used = {b.get("ref") for c in p["clips"] if c["index"] != index for b in c.get("broll") or []}
            clip["broll"] = pipeline.with_broll(clip, tr, used)
            set_clip(pid, index, broll=clip["broll"])
        out = render_clip(Path(p["source"]), p["media"], tr, clip, pdir(pid) / "clips", options(p),
                          cancelled=lambda: pid in _cancel)
        set_clip(pid, index, status="done", version=int(time.time()), **out)
        p = load(pid)
        pipeline.export_json(p["clips"], p.get("analysis") or {}, pdir(pid) / "clips.json")
    except FipError as e:
        set_clip(pid, index, status="error", error=str(e)[-600:])


# ---------- user actions ----------

def set_range(pid: str, mode: str, start=None, end=None) -> dict:
    """Answer to the 'which part?' prompt (1 clip on a long video)."""
    p = load(pid)
    if mode == "manual":
        s, e = parse_time(start), parse_time(end)
        if s is None or e is None or e <= s:
            raise ValueError("Please enter a valid start and end time (e.g. 12:30 and 13:45).")
        if p.get("media") and e > p["media"]["duration"] + 1:
            raise ValueError("The end time is after the end of the video.")
    p["options"] = options(p).model_copy(update={"num_clips": 1, "single_mode": mode,
                                                 "single_start": start or "", "single_end": end or ""}).model_dump()
    _clear_selection(pid, p)
    enqueue(pid)
    return load(pid)


def prepare_shorts(pid: str, num_clips: int) -> dict:
    """A fresh set of N shorts from the existing transcript (no new upload / transcription)."""
    p = load(pid)
    n = max(1, min(MAX_CLIPS, int(num_clips)))
    opts = options(p).model_copy(update={"num_clips": n})
    if n == 1 and opts.single_mode == "ask":
        opts.single_mode = "auto"  # asked from the results page: let Claude pick the moment
    p["options"] = opts.model_dump()
    _clear_selection(pid, p, delete_clips=True)
    enqueue(pid)
    return load(pid)


def _clear_selection(pid: str, p: dict, delete_clips: bool = False):
    for f in ("analysis.json", "refine.json", "clips.json"):
        (pdir(pid) / f).unlink(missing_ok=True)
    if delete_clips and (pdir(pid) / "clips").exists():
        for f in (pdir(pid) / "clips").glob("clip_*"):
            f.unlink(missing_ok=True)
    reset_steps(p)
    p.update(clips=[], analysis=None, error=None)
    save(p)


def _download(pid: str, url: str, d: Path) -> Path:
    try:
        import yt_dlp
    except ImportError as e:
        raise FipError("yt-dlp is not installed (pip install yt-dlp).") from e

    def hook(s):
        if pid in _cancel:
            raise Cancelled("Cancelled.")
        if s.get("status") == "downloading":
            total = s.get("total_bytes") or s.get("total_bytes_estimate") or 0
            frac = (s.get("downloaded_bytes", 0) / total) if total else 0
            set_step(pid, "upload", "active", frac, f"Downloading… {int(frac * 100)}%")

    ydl_opts = {"outtmpl": str(d / "source.%(ext)s"), "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
                "merge_output_format": "mp4", "noplaylist": True, "quiet": True, "no_warnings": True,
                "progress_hooks": [hook]}
    if Path(settings.FFMPEG).is_absolute():
        ydl_opts["ffmpeg_location"] = str(Path(settings.FFMPEG).parent)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=True)
    except Cancelled:
        raise
    except Exception as e:
        msg = str(e)
        if "not a bot" in msg or "Sign in to confirm" in msg:  # YouTube blocks data-centre addresses
            raise FipError("YouTube refused to send this video to the server (\"confirm you're not a bot\": YouTube "
                           "blocks downloads from data centres). Please upload the video file instead — for your "
                           "own channel: YouTube Studio → Content → ⋮ next to the video → Download.") from e
        raise FipError(f"Download failed: {msg[:300]}") from e
    if meta.get("title"):
        update(pid, title=meta["title"])
    files = sorted(d.glob("source.*"), key=lambda f: f.stat().st_size, reverse=True)
    if not files:
        raise FipError("Download failed: no file was saved.")
    return files[0]

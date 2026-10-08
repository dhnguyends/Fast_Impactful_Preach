"""Step orchestration shared by the CLI and the web app.

Every step caches its result in a work directory, so a failed or cancelled run resumes where it
stopped:   transcript.json → analysis.json → refine.json → clips/clip_NN.mp4 → clips.json
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import broll, ffmpeg, segment, settings, transcribe
from .errors import Cancelled, ConfigError, FipError, LLMError
from .models import SHORT_VIDEO_MAX, Options, passages_of, spec_json
from .render import render_clip

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]
_noop: Progress = lambda f, m: None  # noqa: E731


class NeedsRange(FipError):
    """1 clip requested on a long video in 'ask' mode: the user must choose the part."""


@dataclass
class Selection:
    clips: list            # clip dicts (sentence ids + Claude metadata), index = rank
    meta: dict             # sermon_title, sermon_summary, main_scriptures
    fixed: bool            # boundaries decided by the user / the video, not by Claude


def get_transcript(src: Path, info: dict, work: Path, opts: Options, engine: str = "faster",
                   model: str | None = None, progress: Progress = _noop, cancelled=None) -> dict:
    path = work / "transcript.json"
    if path.exists():
        return transcribe.load(path)
    wav = work / "audio.wav"
    progress(0.0, "Extracting audio…")
    ffmpeg.extract_audio(src, wav, cancelled=cancelled)
    progress(0.01, "Loading speech model…")
    try:
        tr = transcribe.transcribe(wav, info["duration"], opts.language, engine=engine, model=model,
                                   progress=progress, cancelled=cancelled)
    finally:
        wav.unlink(missing_ok=True)
    transcribe.save(tr, path)
    return tr


def fixed_range(tr: dict, info: dict, opts: Options) -> tuple[int, int] | None:
    """Boundaries imposed by the 1-clip mode, or None when Claude should choose.
    Raises NeedsRange when the user has to pick the part of a long video."""
    if opts.num_clips != 1:
        return None
    sents = tr["sentences"]
    if info["duration"] <= SHORT_VIDEO_MAX:
        return 0, len(sents) - 1                       # already a short: use it whole
    if opts.single_mode == "manual":
        return segment.range_to_sentences(sents, opts.single_start, opts.single_end)
    if opts.single_mode == "ask":
        raise NeedsRange("Choose the part of the video to turn into a short.")
    return None                                         # auto: Claude picks the best moment


def select(tr: dict, info: dict, work: Path, opts: Options, title: str = "",
           progress: Progress = _noop) -> Selection:
    """Claude selection (+ optional boundary refinement), cached in analysis.json / refine.json."""
    sents = tr["sentences"]
    fixed = fixed_range(tr, info, opts)
    an_path = work / "analysis.json"
    if an_path.exists():
        analysis = json.loads(an_path.read_text(encoding="utf-8"))
    else:
        if fixed and not settings.api_key_configured():
            analysis = segment.plain_analysis(title or "Clip", *fixed)
        elif fixed:
            progress(0.3, "Claude is analysing your clip…")
            analysis = segment.describe_range(sents, tr["language"], *fixed, opts)
        else:
            progress(0.15, f"Claude is reading the sermon ({len(sents)} sentences)…")
            analysis = segment.select_clips(sents, tr["language"], opts)
        an_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")

    meta = {k: analysis.get(k) for k in ("sermon_title", "sermon_summary", "main_scriptures")}
    if fixed:
        c = dict(analysis["clips"][0], start_sentence=fixed[0], end_sentence=fixed[1], index=0,
                 passages=[{"start_sentence": fixed[0], "end_sentence": fixed[1], "role": "body"}])
        c.setdefault("score", c.get("virality_score"))
        c["approx_duration"] = round(sents[fixed[1]]["e"] - sents[fixed[0]]["s"], 1)
        return Selection([c], meta, True)
    clips = segment.sanitize_clips(analysis["clips"], sents, opts, limit=opts.num_clips)
    return Selection(clips, meta, False)


def refine(sel: Selection, tr: dict, work: Path, opts: Options, progress: Progress = _noop) -> Selection:
    """Second Claude pass on cut points. Best effort: a failure keeps the original boundaries."""
    if sel.fixed or not opts.refine:
        return sel
    path = work / "refine.json"
    if not path.exists():
        if not settings.api_key_configured():
            return sel
        progress(0.2, "Checking that every clip starts and ends cleanly…")
        try:
            fixes = segment.refine_boundaries(tr["sentences"], sel.clips, opts)
        except (LLMError, ConfigError) as e:
            log.warning("Boundary refinement skipped: %s", e)
            return sel
        path.write_text(json.dumps(fixes, ensure_ascii=False, indent=1), encoding="utf-8")
    fixes = json.loads(path.read_text(encoding="utf-8"))
    clips = [dict(c) for c in sel.clips]
    for r in fixes:
        i = r.get("index")
        if not (isinstance(i, int) and 0 <= i < len(clips)):
            continue
        c, new = clips[i], r.get("passages")
        if not new and "start_sentence" in r:      # refine.json written before montage mode
            new = [{"start_sentence": r["start_sentence"], "end_sentence": r["end_sentence"]}]
        old = c.get("passages") or [{"start_sentence": a, "end_sentence": b, "role": "body"}
                                    for a, b in passages_of(c)]
        if not isinstance(new, list) or len(new) != len(old):
            continue                               # Claude changed the structure: keep the original
        try:
            c["passages"] = [{"start_sentence": int(n["start_sentence"]), "end_sentence": int(n["end_sentence"]),
                              "role": o.get("role", "body")} for n, o in zip(new, old)]
        except (KeyError, TypeError, ValueError):
            continue
        c["refine_note"] = r.get("reason", "")
    clips = segment.sanitize_clips(clips, tr["sentences"], opts, keep_index=True)
    return Selection(clips, sel.meta, False)


def render_all(src: Path, info: dict, tr: dict, clips: list, work: Path, opts: Options,
               progress: Progress = _noop, on_clip: Callable[[int, dict], None] | None = None,
               cancelled=None) -> list:
    """Render every clip not already rendered. on_clip(index, fields) reports each status change."""
    out_dir = work / "clips"
    todo = [c for c in clips if c.get("status") != "done"]
    used = {b.get("ref") for c in clips if c.get("status") == "done" for b in c.get("broll") or []}
    for n, c in enumerate(todo):
        progress(n / max(1, len(todo)), f"Rendering short #{c['index'] + 1} ({n + 1} of {len(todo)})…")
        if on_clip:
            on_clip(c["index"], {"status": "rendering", "error": None})
        try:
            if opts.broll and c.get("broll"):   # library first, then Pixabay; never two shorts on one shot
                progress(n / max(1, len(todo)), f"Finding B-roll for short #{c['index'] + 1}…")
                c["broll"] = with_broll(c, tr, used)
            c.update(render_clip(src, info, tr, c, out_dir, opts, cancelled=cancelled), status="done")
        except FipError as e:
            c.update(status="error", error=str(e)[-600:])
            if on_clip:
                on_clip(c["index"], {"status": "error", "error": c["error"]})
            if isinstance(e, Cancelled):
                raise
            log.error("Clip %d failed: %s", c["index"] + 1, e)
            continue
        if on_clip:
            on_clip(c["index"], {k: c[k] for k in ("status", "start", "end", "duration", "video",
                                                   "thumb", "srt", "text", "slide_spans", "segments",
                                                   "broll", "broll_used") if k in c})
    progress(1.0, "All clips ready")
    return clips


def with_broll(clip: dict, tr: dict, used: set) -> list:
    """clip['broll'] with every cue resolved (file, source, credit) — unresolvable cues stay listed
    (without a file) so they can be reviewed in Adjust."""
    resolved = {(b.get("sentence"), b.get("word"), b.get("query")): b for b in broll.resolve(clip, tr, used)}
    return [resolved.get((b.get("sentence"), b.get("word"), b.get("query")), b) for b in clip.get("broll") or []]


def export_json(clips: list, meta: dict, path: Path) -> Path:
    """clips.json in the documented format (see fip.models.spec_json)."""
    data = {**meta, "clips": [spec_json(c) for c in clips]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

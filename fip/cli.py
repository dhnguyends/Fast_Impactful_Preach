"""Command-line interface.

  python -m fip run sermon.mp4                       # everything: transcribe → Claude → render 10 shorts
  python -m fip run sermon.mp4 --clips 5 --min 30 --max 60 --highlight active --crop track
  python -m fip run sermon.mp4 --clips 1 --start 12:30 --end 13:45
  python -m fip transcribe sermon.mp4                # transcript.json only
  python -m fip find sermon.mp4                      # + clips.json (Claude), no rendering
  python -m fip render sermon.mp4 --only 2,5         # (re)render from the cached selection

Outputs go to --out (default: ./shorts/<video name>/): transcript.json, analysis.json,
clips.json (start_time, end_time, title, hook, virality_score, summary, reasoning, …) and
clips/clip_NN.mp4 (+ .srt, .jpg).
"""
import argparse
import json
import logging
import sys
import threading
from pathlib import Path

from . import __version__, ffmpeg, pipeline, settings
from .errors import Cancelled, ConfigError, FipError
from .models import MAX_CLIPS, Options, fmt_time

log = logging.getLogger("fip")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fip", description="Turn a long sermon video into vertical shorts with Claude.")
    p.add_argument("--version", action="version", version=f"fip {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("video", type=Path, help="input video or audio file")
    common.add_argument("-o", "--out", type=Path, help="output/work directory (default ./shorts/<name>)")
    common.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    g = common.add_argument_group("transcription")
    g.add_argument("--lang", default="auto", help="spoken language code (fr, en, …) or auto")
    g.add_argument("--engine", choices=["faster", "openai"], default="faster",
                   help="Whisper implementation (default: faster-whisper)")
    g.add_argument("--whisper-model", default=None, help=f"Whisper model (default {settings.WHISPER_MODEL})")

    sel = argparse.ArgumentParser(add_help=False)
    g = sel.add_argument_group("clip selection (Claude)")
    g.add_argument("-n", "--clips", type=int, default=10, help=f"number of shorts, 1–{MAX_CLIPS} (default 10)")
    g.add_argument("--min", dest="min_duration", type=float, default=30, help="min clip length, s (default 30)")
    g.add_argument("--max", dest="max_duration", type=float, default=60, help="max clip length, s (default 60)")
    g.add_argument("--guidance", default="", help="extra instruction for Claude")
    g.add_argument("--no-refine", action="store_true", help="skip the 2nd pass on cut points")
    g.add_argument("--no-montage", action="store_true", help="one sermon passage per short (no montage)")
    g.add_argument("--montage-max", type=float, default=90, help="max length of a montage short, s (default 90)")
    g.add_argument("--model", help="Claude model id (default claude-opus-5-5)")
    g.add_argument("--start", help="1 clip: start time (e.g. 12:30)")
    g.add_argument("--end", help="1 clip: end time (e.g. 13:45)")
    g.add_argument("--auto", action="store_true", help="1 clip on a long video: let Claude pick the moment")
    g.add_argument("--fresh", action="store_true", help="ignore the cached selection and ask Claude again")

    ren = argparse.ArgumentParser(add_help=False)
    g = ren.add_argument_group("rendering")
    g.add_argument("--crop", choices=["track", "center"], default="track", help="speaker tracking or centre crop")
    g.add_argument("--slides", choices=["fit", "crop"], default="fit", help="full-screen slides: show whole frame")
    g.add_argument("--captions", choices=["eeig", "impact"], default="eeig",
                   help="caption style: eeig (sentence case, blue box) or impact (UPPERCASE, bouncy)")
    g.add_argument("--highlight", choices=["active", "keyword"], default="active",
                   help="impact captions: highlight the spoken word, or one keyword per chunk")
    g.add_argument("--no-emojis", action="store_true")
    g.add_argument("--no-zoom", action="store_true", help="disable punch-in zooms")
    g.add_argument("--hook-title", choices=["off", "hook", "full"], default="hook",
                   help="on-screen hook title: off, the first seconds (default), or the whole short")
    g.add_argument("--title-seconds", type=float, default=15, help="how long the hook title stays (default 15 s)")
    g.add_argument("--no-outro", action="store_true", help="no end card")
    g.add_argument("--brand", default=None, help="end card name")
    g.add_argument("--tagline", default=None, help="end card tagline")
    g.add_argument("--watermark", default="", help="small text top-right")
    g.add_argument("--only", help="render only these clip numbers, e.g. 1,3,5")

    sub.add_parser("transcribe", parents=[common], help="extract audio + word-level transcript")
    sub.add_parser("find", parents=[common, sel], help="transcribe + Claude selection → clips.json")
    sub.add_parser("render", parents=[common, sel, ren], help="render shorts from the cached selection")
    sub.add_parser("run", parents=[common, sel, ren], help="full pipeline")
    return p


def _options(a) -> Options:
    d = {"language": a.lang}
    if hasattr(a, "clips"):
        mode = "manual" if (a.start or a.end) else "auto" if a.auto else "ask"
        d.update(num_clips=a.clips, min_duration=a.min_duration, max_duration=a.max_duration,
                 guidance=a.guidance, refine=not a.no_refine, single_mode=mode,
                 montage=not a.no_montage, montage_max=a.montage_max,
                 single_start=a.start or "", single_end=a.end or "")
        if not 1 <= a.clips <= MAX_CLIPS:
            raise ConfigError(f"--clips must be between 1 and {MAX_CLIPS}.")
    if hasattr(a, "crop"):
        d.update(crop=a.crop, slides=a.slides, caption_style=a.captions, highlight=a.highlight,
                 emojis=a.captions == "impact" and not a.no_emojis,
                 punch_zoom=not a.no_zoom, title_mode=a.hook_title, title_seconds=a.title_seconds,
                 outro=not a.no_outro, watermark=a.watermark)
        if a.brand is not None:
            d["brand"] = a.brand
        if a.tagline is not None:
            d["tagline"] = a.tagline
    return Options(**d)


class _Bar:
    """One-line progress on stderr."""

    def __init__(self, label: str):
        self.label = label

    def __call__(self, frac: float, msg: str):
        sys.stderr.write(f"\r  {self.label:<10} {int(frac * 100):3d}%  {msg[:70]:<70}")
        sys.stderr.flush()

    def done(self, msg: str = ""):
        sys.stderr.write(f"\r  {self.label:<10} done  {msg[:70]:<70}\n")


def _ask_range(info: dict, opts: Options) -> Options:
    if not sys.stdin.isatty():
        raise ConfigError("1 clip on a long video: pass --start/--end, or --auto to let Claude choose.")
    print(f"\nThe video is {fmt_time(info['duration'])} long. Which part should become the short?")
    s = input("  start (e.g. 12:30, empty = let Claude choose): ").strip()
    if not s:
        return opts.model_copy(update={"single_mode": "auto"})
    e = input("  end   (e.g. 13:45): ").strip()
    return opts.model_copy(update={"single_mode": "manual", "single_start": s, "single_end": e})


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    a = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cancel = threading.Event()
    try:
        ffmpeg.check_binaries()
        settings.ensure_dirs()
        if getattr(a, "model", None):
            import os
            os.environ["CLAUDE_MODEL"] = a.model
        opts = _options(a)
        src = a.video.resolve()
        work = (a.out or Path("shorts") / src.stem).resolve()
        work.mkdir(parents=True, exist_ok=True)
        info = ffmpeg.probe(src)
        print(f"fip {__version__} · {src.name} · {fmt_time(info['duration'])} · "
              f"{info['width']}x{info['height']} → {work}", flush=True)

        bar = _Bar("transcribe")
        tr = pipeline.get_transcript(src, info, work, opts, engine=a.engine, model=a.whisper_model,
                                     progress=bar, cancelled=cancel.is_set)
        bar.done(f"{len(tr['words'])} words, {len(tr['sentences'])} sentences, language {tr['language']}")
        if a.cmd == "transcribe":
            return 0

        if getattr(a, "fresh", False):
            for f in ("analysis.json", "refine.json"):
                (work / f).unlink(missing_ok=True)
        bar = _Bar("claude")
        try:
            sel = pipeline.select(tr, info, work, opts, title=src.stem, progress=bar)
        except pipeline.NeedsRange:
            opts = _ask_range(info, opts)
            sel = pipeline.select(tr, info, work, opts, title=src.stem, progress=bar)
        sel = pipeline.refine(sel, tr, work, opts, progress=bar)
        bar.done(f"{len(sel.clips)} clips selected")
        pipeline.export_json(sel.clips, sel.meta, work / "clips.json")

        if a.cmd != "find":
            clips = sel.clips
            if a.only:
                wanted = {int(x) - 1 for x in a.only.split(",") if x.strip().isdigit()}
                clips = [c for c in clips if c["index"] in wanted]
            bar = _Bar("render")
            pipeline.render_all(src, info, tr, clips, work, opts, progress=bar, cancelled=cancel.is_set)
            bar.done(f"{sum(c.get('status') == 'done' for c in clips)}/{len(clips)} shorts")
            pipeline.export_json(sel.clips, sel.meta, work / "clips.json")

        print()
        for c in sel.clips:
            s = c.get("start", sel_start(tr, c))
            e = c.get("end", sel_end(tr, c))
            status = "" if c.get("status") in (None, "done") else f"  [{c['status']}: {c.get('error', '')[:60]}]"
            n_pass = len(c.get("passages") or [1])
            status = (f"  (montage of {n_pass} passages)" if n_pass > 1 else "") + status
            print(f"  {c['index'] + 1:>2}. [{c.get('score') or '–':>3}] {fmt_time(s)}–{fmt_time(e)}  "
                  f"{c.get('title', '')}{status}")
        print(f"\n  clips.json → {work / 'clips.json'}")
        failed = [c for c in sel.clips if c.get("status") == "error"]
        return 1 if failed else 0
    except KeyboardInterrupt:
        cancel.set()
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Cancelled:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except FipError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return e.exit_code


def sel_start(tr, c):
    return tr["sentences"][c["start_sentence"]]["s"]


def sel_end(tr, c):
    return tr["sentences"][c["end_sentence"]]["e"]


if __name__ == "__main__":
    sys.exit(main())

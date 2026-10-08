"""16:9 → 9:16 reframing plan.

* Speaker tracking ("track"): faces are detected 2×/s; the crop follows the speaker with
  "camera cut" moves (only when they leave the centre band) and continuity, so it never jumps to an
  audience member or to the preacher's image on a projection screen.
* Centre crop ("center"): a fixed centred window.
* Slides: full-screen slides (mostly white frames) cannot be cropped without cutting the text, so
  those spans are rendered "fit": whole frame, centred over a blurred, zoomed copy of itself.
* Punch zoom: alternates 1.0 / 1.14 on sentence boundaries for a bouncy rhythm.

The crop window only ever MOVES at runtime (sendcmd x/y); its size never changes. Changing the
crop size mid-stream makes FFmpeg 7.1 end the video branch early, and the render then hangs forever
waiting for frames. The zoom is instead a second, fixed-size branch switched on during zoom spans.
"""
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import cv2
import numpy as np

from . import settings
from .ffmpeg import NO_WINDOW

SAMPLE_FPS = 4               # frames analysed per second (slide check on every one: cheap)
FACE_EVERY = 2               # face detection on every 2nd sample only (expensive) → 2 faces/s
SAMPLE_W = 480
# Slide detection = share of near-white pixels, with hysteresis so fades in/out stay "slide":
SLIDE_ENTER = 0.35           # a slide starts at ≥ 35 % near-white (speaker shots measured ≤ 12 %)
SLIDE_STAY = 0.20            # and lasts while ≥ 20 %
SLIDE_MIN_SECONDS = 1.0
SLIDE_PAD = 0.75             # margin before/after each span (slides fade in semi-transparent)
SLIDE_EDGE_SNAP = 1.0        # a span this close to the clip start/end is extended to it

_cascades = None


ZOOM = 1.14


@dataclass
class FramePlan:
    crops: list = field(default_factory=list)       # [(t, x, y, w, h)] relative to clip start; w/h constant
    fit_spans: list = field(default_factory=list)   # [(t0, t1)] where the whole frame is shown
    zoom_spans: list = field(default_factory=list)  # [(t0, t1)] where the punch-in zoom is shown


def _detectors():
    global _cascades
    if _cascades is None:
        base = Path(cv2.data.haarcascades)
        _cascades = [cv2.CascadeClassifier(str(base / "haarcascade_frontalface_default.xml")),
                     cv2.CascadeClassifier(str(base / "haarcascade_profileface.xml"))]
    return _cascades


def analyse(src: Path, start: float, end: float, src_w: int, src_h: int, need_faces: bool = True):
    """Sample frames → (face track [(t, cx|None, cy, size)], white fractions [(t, 0..1)])."""
    sh = int(round(SAMPLE_W * src_h / src_w / 2) * 2)
    cmd = [settings.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", str(src), "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_W}:{sh}",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    frame_bytes = SAMPLE_W * sh
    scale = src_w / SAMPLE_W
    samples, slides, i = [], [], 0
    front, profile = _detectors()
    mn = max(18, sh // 14)
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            gray = np.frombuffer(buf, np.uint8).reshape(sh, SAMPLE_W)
            t = i / SAMPLE_FPS
            slides.append((t, float((gray > 200).mean())))
            i += 1
            if (i - 1) % FACE_EVERY:
                continue
            faces = []
            if need_faces:
                eq = cv2.equalizeHist(gray)
                faces = list(front.detectMultiScale(eq, 1.15, 5, minSize=(mn, mn)))
                if not faces:
                    faces = list(profile.detectMultiScale(eq, 1.15, 4, minSize=(mn, mn)))
                if not faces:
                    flipped = profile.detectMultiScale(cv2.flip(eq, 1), 1.15, 4, minSize=(mn, mn))
                    faces = [(SAMPLE_W - x - w, y, w, h) for (x, y, w, h) in flipped]
            samples.append((t, [((x + w / 2) * scale, (y + h / 2) * scale, w * scale) for (x, y, w, h) in faces]))
    finally:
        proc.stdout.close()
        proc.wait()
    return _follow_speaker(samples, src_w), slides


def _follow_speaker(samples: list, src_w: int) -> list:
    """One face per sample, preferring continuity with the speaker over the largest face."""
    all_faces = [f for _, fs in samples for f in fs]
    if not all_faces:
        return [(t, None, None, None) for t, _ in samples]
    bins: dict = {}
    for cx, _, sz in all_faces:  # the horizontal spot where faces are found most often
        b = int(cx // (src_w / 12))
        bins[b] = bins.get(b, 0) + sz
    best = max(bins, key=bins.get)
    prev = median(cx for cx, _, _ in all_faces if int(cx // (src_w / 12)) == best)
    out, misses = [], 0
    for t, fs in samples:
        if not fs:
            out.append((t, None, None, None))
            continue
        near = min(fs, key=lambda f: abs(f[0] - prev))
        if abs(near[0] - prev) <= src_w * 0.12 or misses >= 4:
            prev, misses = near[0], 0
            out.append((t, near[0], near[1], near[2]))
        else:  # far-away face: ignore unless the speaker really moved (persistent > 1 s)
            misses += 1
            out.append((t, None, None, None))
    return out


def slide_spans(slides: list, duration: float) -> list:
    """White-fraction samples [(t, frac)] → spans [(t0, t1)] to show the whole frame.

    Hysteresis: a span opens at ≥ SLIDE_ENTER and continues while ≥ SLIDE_STAY, also backwards
    (a fade-in belongs to the slide). Spans shorter than SLIDE_MIN_SECONDS are dropped, the rest
    get SLIDE_PAD of margin, are snapped to the clip edges within SLIDE_EDGE_SNAP, and merged."""
    step = 1 / SAMPLE_FPS
    fr = [f for _, f in slides]
    ts = [t for t, _ in slides]
    spans, k = [], 0
    while k < len(fr):
        if fr[k] < SLIDE_ENTER:
            k += 1
            continue
        a = k
        while a > 0 and fr[a - 1] >= SLIDE_STAY:      # include the fade-in
            a -= 1
        b = k
        while b + 1 < len(fr) and fr[b + 1] >= SLIDE_STAY:  # and the slide + fade-out
            b += 1
        spans.append([ts[a], ts[b] + step])
        k = b + 1
    out = []
    for a, b in spans:
        if b - a < SLIDE_MIN_SECONDS:
            continue
        a, b = max(0.0, a - SLIDE_PAD), min(duration, b + SLIDE_PAD)
        if a <= SLIDE_EDGE_SNAP:
            a = 0.0
        if duration - b <= SLIDE_EDGE_SNAP:
            b = duration
        if out and a - out[-1][1] <= 0.7:            # merge spans separated by a short gap
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in out]


def pick_cut_times(sentence_bounds: list, duration: float, min_gap=3.5, max_gap=7.0) -> list:
    """Zoom-change instants on sentence boundaries, roughly every 3.5–7 s."""
    cuts, last = [], 0.0
    for b in sorted(sentence_bounds):
        if b - last >= min_gap and duration - b > 2.0:
            cuts.append(b)
            last = b
    filled, prev = [], 0.0
    for c in cuts + [duration]:
        while c - prev > max_gap * 1.6:   # long stretches without a sentence end
            prev += max_gap
            if duration - prev > 2.0:
                filled.append(prev)
        if c < duration:
            filled.append(c)
        prev = c
    return filled


def plan(track: list, duration: float, src_w: int, src_h: int, cut_times: list, mode: str = "track",
         zoom_levels=(1.0, 1.14)) -> list:
    """Crop commands [(t, x, y, w, h)] relative to the clip start."""
    if src_w * 16 >= src_h * 9:          # landscape / square: full-height 9:16 window
        base_h, base_w = src_h, int(src_h * 9 / 16)
    else:                                # already narrow: full-width window
        base_w, base_h = src_w, min(src_h, int(src_w * 16 / 9))

    known = [p for p in track if p[1] is not None] if mode == "track" else []
    default_cx = median(p[1] for p in known) if known else src_w / 2
    default_cy = median(p[2] for p in known) if known else src_h * 0.38

    def face_at(t0, t1):
        pts = [p for p in known if t0 - 0.2 <= p[0] <= t1 + 0.2]
        return (median(p[1] for p in pts), median(p[2] for p in pts)) if pts else None

    def window(cx, cy, zoom):
        w = int(base_w / zoom) // 2 * 2
        h = int(base_h / zoom) // 2 * 2
        x = int(min(max(cx - w / 2, 0), src_w - w))
        y = int(min(max(cy - h * 0.32, 0), src_h - h))  # face in the upper third, room for captions
        return x, y, w, h

    bounds = [0.0] + [c for c in cut_times if 0.5 < c < duration - 0.5] + [duration]
    cmds, cx, cy = [], default_cx, default_cy
    for k in range(len(bounds) - 1):
        t0, t1 = bounds[k], bounds[k + 1]
        zoom = zoom_levels[k % len(zoom_levels)]
        f = face_at(t0, min(t1, t0 + 1.5))
        if f:
            cx, cy = f
        x, y, w, h = window(cx, cy, zoom)
        cmds.append((t0, x, y, w, h))
        t = t0 + 1.0
        while t < t1 - 0.8:  # re-cut only if the speaker leaves the frame's central band
            f = face_at(t, t + 0.7)
            if f and abs(f[0] - (x + w / 2)) > w * 0.28:
                cx, cy = f
                x, y, w, h = window(cx, cy, zoom)
                cmds.append((t, x, y, w, h))
                t += 1.5
            else:
                t += 0.5
    return cmds


def zoom_spans(cut_times: list, duration: float) -> list:
    """Every other shot (between consecutive cut times) is zoomed in."""
    bounds = [0.0] + [c for c in cut_times if 0.5 < c < duration - 0.5] + [duration]
    return [(bounds[k], bounds[k + 1]) for k in range(1, len(bounds) - 1, 2)]


def zoom_window(w: int, h: int, zoom: float = ZOOM) -> tuple:
    """Sub-window (zw, zh, x, y) of the base crop for the punch-in: centred, slightly high (face)."""
    zw, zh = int(w / zoom) // 2 * 2, int(h / zoom) // 2 * 2
    return zw, zh, (w - zw) // 2, (h - zh) // 3


def build_plan(src: Path, start: float, end: float, info: dict, sentence_ends: list, opts,
               slides_mode: str | None = None, joins: list = ()) -> FramePlan:
    """slides_mode overrides opts.slides for one clip: "crop" never switches layout, "whole" shows the
    whole frame for the entire clip, "fit" detects slides; None or "auto" = the project setting.
    joins: montage splice times (clip-relative): always a new shot, re-centred on the speaker."""
    duration = end - start
    w, h = info["width"], info["height"]
    track, slides = analyse(src, start, end, w, h, need_faces=opts.crop == "track")
    cuts = pick_cut_times(sentence_ends, duration) if opts.punch_zoom else []
    if joins:  # a splice is a cut; drop zoom cuts too close to one (no 1-second shots)
        cuts = sorted([c for c in cuts if all(abs(c - j) > 1.5 for j in joins)] + list(joins))
    crops = plan(track, duration, w, h, cuts, mode=opts.crop, zoom_levels=(1.0,))  # constant size
    mode = slides_mode if slides_mode in ("fit", "crop", "whole") else opts.slides
    fits = slide_spans(slides, duration) if mode == "fit" else [(0.0, duration)] if mode == "whole" else []
    return FramePlan(crops=crops, fit_spans=fits, zoom_spans=zoom_spans(cuts, duration) if opts.punch_zoom else [])


def sendcmd_script(cmds: list, target: str = "crop@track") -> str:
    """Position-only commands for a *named* crop instance (never w/h: see the module docstring)."""
    return "\n".join(f"{t:.3f} {target} x {x}, {target} y {y};" for (t, x, y, _w, _h) in cmds) + "\n"

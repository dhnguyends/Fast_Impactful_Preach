"""Render one vertical short in a single FFmpeg pass:
reframe (tracked crop + slide 'fit' layer) → ASS subtitles → emoji pops → loudness → end card."""
import hashlib
import logging
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import reframe, settings, subtitles
from .errors import RenderError
from .ffmpeg import run, thumbnail, video_encoder
from .models import DEFAULT_BRAND, DEFAULT_TAGLINE, Options, passages_of

log = logging.getLogger(__name__)
OUTRO_SECONDS = 1.6
SPLICE_FADE = 0.03   # s of audio fade on each side of a montage join (no click)
BROLL_NOT_BEFORE = 2.0   # s: the short opens on the preacher (the hook)
BROLL_TAIL = 1.2         # s: and ends on him
BROLL_GAP = 3.0          # s of preacher between two cutaways


# ---------- clip boundaries ----------

def clip_bounds(words: list, sentences: list, a: int, b: int, src_duration: float) -> tuple[float, float]:
    """Sentence ids → padded (start, end) seconds that never swallow neighbouring words."""
    w0, w1 = sentences[a]["w0"], sentences[b]["w1"]
    s = words[w0]["s"] - 0.18
    if w0 > 0:
        s = max(s, words[w0 - 1]["e"] + 0.02)
    e = words[w1]["e"] + 0.45
    if w1 + 1 < len(words):
        e = min(e, words[w1 + 1]["s"] - 0.03)
    return max(0.0, s), min(src_duration, max(e, words[w1]["e"]))


def clip_words(words: list, start: float, end: float) -> list:
    return [{"w": w["w"], "s": round(w["s"] - start, 3), "e": round(w["e"] - start, 3)}
            for w in words if w["s"] >= start - 0.01 and w["e"] <= end + 0.01]


# ---------- end card ----------

def _wrap(d: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list:
    lines, cur = [], ""
    for w in text.split():
        test = f"{cur} {w}".strip()
        if cur and d.textlength(test, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = test
    return lines + [cur] if cur else lines


def outro_card(brand: str, tagline: str) -> Path:
    """Branded end card: church logo on a white rounded tile, name and tagline below (wrapped).
    Cached per text + logo file."""
    logo = settings.brand_logo()
    logo_sig = hashlib.md5(logo.read_bytes()).hexdigest()[:8] if logo else "none"
    key = hashlib.md5(f"{brand}|{tagline}|{logo_sig}|v5".encode()).hexdigest()[:10]
    out = settings.CACHE_DIR / f"outro_{key}.png"
    W, H = settings.OUT_W, settings.OUT_H
    if out.exists():
        try:  # never trust a cached file blindly: a wrong size breaks the final concat
            with Image.open(out) as cached:
                if cached.size == (W, H):
                    return out
        except OSError:
            pass
        log.warning("Cached end card %s is invalid; regenerating it", out.name)
    im = Image.new("RGB", (W, H), (14, 10, 30))
    d = ImageDraw.Draw(im)
    for k in range(-H, W + H, 120):  # subtle diagonal lattice
        d.line([(k, 0), (k + H, H)], fill=(24, 19, 46), width=2)
        d.line([(k, H), (k + H, 0)], fill=(24, 19, 46), width=2)
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W / 2 - 330, H / 2 - 330, W / 2 + 330, H / 2 + 330], fill=(70, 40, 140))
    im = Image.blend(im, glow.filter(ImageFilter.GaussianBlur(160)), 0.35)
    d = ImageDraw.Draw(im)
    fonts = settings.FONTS_DIR
    bold = next((str(fonts / f) for f in subtitles.FONT_FILES if (fonts / f).exists()),
                str(settings.WIN_FONTS / "arialbd.ttf"))
    f_brand, f_tag = ImageFont.truetype(bold, 76), ImageFont.truetype(bold, 40)
    cx = W // 2
    if logo:  # church logo on a white rounded tile (logos are usually drawn on white)
        tile, pad = 380, 34
        top = H // 2 - 80 - tile
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([cx - tile // 2, top + 10, cx + tile // 2, top + tile + 10], 44,
                                                 fill=(0, 0, 0, 120))
        im = Image.alpha_composite(im.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(14))).convert("RGB")
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([cx - tile // 2, top, cx + tile // 2, top + tile], 44, fill=(255, 255, 255))
        with Image.open(logo) as lg:
            lg = lg.convert("RGBA")
            scale = (tile - 2 * pad) / max(lg.size)  # fit the tile (small logos are upscaled smoothly)
            lg = lg.resize((round(lg.width * scale), round(lg.height * scale)), Image.LANCZOS)
            im.paste(lg, (cx - lg.width // 2, top + (tile - lg.height) // 2), lg)
        d = ImageDraw.Draw(im)
    y = H // 2 + 20
    for line in _wrap(d, brand, f_brand, W - 160):
        d.text((cx, y), line, font=f_brand, fill=(255, 255, 255), anchor="mm")
        y += 92
    y -= 7
    for line in _wrap(d, tagline, f_tag, W - 160):
        d.text((cx, y), line, font=f_tag, fill=(176, 160, 230), anchor="mm")
        y += 52
    im.save(out)
    return out


# ---------- montage ----------

def montage_source(src: Path, info: dict, transcript: dict, passages: list, out: Path,
                   cancelled=None) -> tuple[dict, dict, list, list]:
    """Join the passages (play order) into one intermediate video, hard video cuts and a 30 ms audio
    fade at each join. Returns (info, transcript, joins, spans) for that video: words and sentences
    re-timed onto the montage timeline, joins = splice times, spans = source (start, end) per passage.
    Rendering the short from this file reuses the whole single-passage pipeline unchanged."""
    words_all, sentences = transcript["words"], transcript["sentences"]
    FPS = settings.OUT_FPS
    spans, durs = [], []
    for a, b in passages:
        s, e = clip_bounds(words_all, sentences, a, b, info["duration"])
        spans.append((s, e))
        durs.append(max(1, round((e - s) * FPS)) / FPS)   # whole frames: audio and video stay aligned

    vwords, vsents, joins, off = [], [], [], 0.0
    for (a, b), (s, _e), d in zip(passages, spans, durs):
        for sid in range(a, b + 1):
            sen = sentences[sid]
            w0 = len(vwords)
            vwords += [dict(w, s=round(w["s"] - s + off, 3), e=round(min(w["e"] - s, d) + off, 3))
                       for w in words_all[sen["w0"]:sen["w1"] + 1]]
            if len(vwords) > w0:
                vsents.append(dict(sen, id=len(vsents), w0=w0, w1=len(vwords) - 1,
                                   s=vwords[w0]["s"], e=vwords[-1]["e"]))
        off += d
        joins.append(round(off, 3))
    joins.pop()

    args = [settings.FFMPEG, "-y", "-v", "error"]
    for (s, _e), d in zip(spans, durs):
        args += ["-ss", f"{s:.3f}", "-t", f"{d:.3f}", "-i", str(src)]
    graph, ins = [], ""
    for k, d in enumerate(durs):
        if info["has_video"]:
            graph.append(f"[{k}:v]fps={FPS},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[v{k}]")
            ins += f"[v{k}]"
        graph.append(f"[{k}:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                     f"afade=t=in:d={SPLICE_FADE},afade=t=out:st={max(0.0, d - SPLICE_FADE):.3f}:d={SPLICE_FADE},"
                     f"apad,atrim=0:{d:.3f}[a{k}]")
        ins += f"[a{k}]"
    v = 1 if info["has_video"] else 0
    graph.append(f"{ins}concat=n={len(durs)}:v={v}:a=1" + ("[vout][aout]" if v else "[aout]"))
    args += ["-filter_complex", ";".join(graph)]
    if v:
        args += ["-map", "[vout]", "-c:v", "libx264", "-preset", "superfast", "-crf", "14", "-pix_fmt", "yuv420p"]
    args += ["-map", "[aout]", "-c:a", "pcm_s16le", str(out)]
    run(args, cancelled=cancelled, error_cls=RenderError, timeout=300 + off * 10)

    minfo = dict(info, duration=round(off, 3), fps=FPS)
    return minfo, {"words": vwords, "sentences": vsents, "language": transcript.get("language")}, joins, spans


def montage_cues(cues: list, spans: list, offsets: list) -> list:
    """B-roll anchors are source times: move each onto the montage timeline (drop cut ones)."""
    out = []
    for c in cues:
        at = c.get("at")
        for (s, e), off in zip(spans, offsets):
            if at is not None and s <= at < e:
                out.append(dict(c, at=round(at - s + off, 3)))
                break
    return out


# ---------- B-roll ----------

def place_broll(cues: list, start: float, dur: float, fits: list) -> list:
    """Clip-relative (t0, seconds, cue) for the resolved cues that fit: not over the opening hook or
    the ending, not over a slide shown whole, with some preacher between two cutaways."""
    out, last_end = [], -99.0
    for c in sorted((c for c in cues if c.get("file") and c.get("at") is not None), key=lambda c: c["at"]):
        t0, d = c["at"] - start, float(c.get("seconds") or 3)
        if t0 < BROLL_NOT_BEFORE or t0 + d > dur - BROLL_TAIL or t0 < last_end + BROLL_GAP:
            continue
        if any(t0 < f1 and t0 + d > f0 for f0, f1 in fits):
            continue
        if not Path(c["file"]).exists():
            continue
        out.append((round(t0, 3), round(d, 3), c))
        last_end = t0 + d
    return out


def broll_track(placed: list, dur: float, out: Path, cancelled=None) -> Path:
    """ONE continuous video (black between cutaways) the length of the clip: a single input that
    starts at 0 never stalls FFmpeg 7's scheduler, unlike several late-starting inputs."""
    W, H, FPS = settings.OUT_W, settings.OUT_H, settings.OUT_FPS
    args, graph, seq, t = [settings.FFMPEG, "-y", "-v", "error"], [], "", 0.0
    for k, (t0, d, c) in enumerate(placed):
        if t0 - t > 0.01:
            graph.append(f"color=c=black:s={W}x{H}:r={FPS}:d={t0 - t:.3f},setsar=1[g{k}]")
            seq += f"[g{k}]"
        args += ["-t", f"{d + 1:.3f}", "-i", c["file"]]
        graph.append(f"[{k}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                     f"crop={W}:{H},setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration={d:.3f},"
                     f"trim=duration={d:.3f},setpts=PTS-STARTPTS[b{k}]")
        seq += f"[b{k}]"
        t = t0 + d
    graph.append(f"color=c=black:s={W}x{H}:r={FPS}:d={max(0.1, dur - t):.3f},setsar=1[gend]")
    seq += "[gend]"
    n = seq.count("[")
    graph.append(f"{seq}concat=n={n}:v=1:a=0,format=yuv420p[out]")
    args += ["-filter_complex", ";".join(graph), "-map", "[out]", "-c:v", "libx264", "-preset", "ultrafast",
             "-crf", "16", str(out)]
    run(args, cancelled=cancelled, error_cls=RenderError, timeout=120 + dur * 4)
    return out


# ---------- render ----------

def render_clip(src: Path, info: dict, transcript: dict, clip: dict, out_dir: Path, opts: Options,
                cancelled=None, joins: list = ()) -> dict:
    """Render clip_NN.mp4 (+ .jpg thumbnail, .srt, .ass) into out_dir. Returns file/timing info.
    A montage clip (several passages) is first joined into an intermediate video."""
    passages = passages_of(clip)
    if len(passages) > 1:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / f"clip_{clip['index'] + 1:02d}_montage.mkv"
        try:
            minfo, mtr, mjoins, spans = montage_source(src, info, transcript, passages, tmp, cancelled)
            single = {k: v for k, v in clip.items() if k != "passages"}
            single.update(start_sentence=0, end_sentence=len(mtr["sentences"]) - 1,
                          broll=montage_cues(clip.get("broll") or [], spans, [0.0] + mjoins))
            res = render_clip(tmp, minfo, mtr, single, out_dir, opts, cancelled=cancelled, joins=mjoins)
        finally:
            tmp.unlink(missing_ok=True)
        res.update(start=round(min(s for s, _ in spans), 3), end=round(max(e for _, e in spans), 3),
                   segments=[[round(s, 3), round(e, 3)] for s, e in spans])
        return res
    words_all, sentences = transcript["words"], transcript["sentences"]
    a, b = clip["start_sentence"], clip["end_sentence"]
    if not (0 <= a <= b < len(sentences)):
        raise RenderError(f"Clip {clip.get('index')} has invalid sentence range {a}–{b}.")
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip_bounds(words_all, sentences, a, b, info["duration"])
    dur = end - start
    words = clip_words(words_all, start, end)
    name = f"clip_{clip['index'] + 1:02d}"
    W, H, FPS = settings.OUT_W, settings.OUT_H, settings.OUT_FPS

    # 1) subtitles
    ass, emoji_events = subtitles.build_ass(
        words, dur, keywords=clip.get("highlight_words", []), emoji_cues=clip.get("emoji_cues", []),
        highlight=opts.highlight, emojis=opts.emojis, style=opts.caption_style,
        # hook_title "" (cleared in Adjust) means no title for this short; absent (old projects) → the title
        title=((clip["hook_title"] if "hook_title" in clip else clip.get("title")) or None)
        if opts.title_mode != "off" else None,
        title_until=dur if opts.title_mode == "full" else min(dur, opts.title_seconds),
        watermark=opts.watermark)
    (out_dir / f"{name}.ass").write_text(ass, encoding="utf-8")
    if not (out_dir / "fonts").exists():
        shutil.copytree(subtitles.ensure_fonts(), out_dir / "fonts")

    # 2) picture: tracked/centre crop, plus a 'fit' layer over slides
    graph, fits = [], []
    landscape = info["has_video"] and info["width"] > info["height"] * 9 / 16 + 4
    if not info["has_video"]:
        graph.append(f"color=c=0x0e0a1e:s={W}x{H}:r={FPS}:d={dur:.3f}[pic]")
    elif landscape:
        sentence_ends = [sentences[k]["e"] - start for k in range(a, b)]
        fp = reframe.build_plan(src, start, end, info, sentence_ends, opts, slides_mode=clip.get("slides"),
                                joins=[j - start for j in joins if start < j < end])
        fits = fp.fit_spans
        (out_dir / f"{name}_crop.txt").write_text(reframe.sendcmd_script(fp.crops), encoding="utf-8")
        _, x, y, w, h = fp.crops[0]
        crop = f"sendcmd=f={name}_crop.txt,crop@track={w}:{h}:{x}:{y}"
        if fp.zoom_spans:  # punch zoom = a fixed-size zoomed copy shown during zoom spans
            zw, zh, zx, zy = reframe.zoom_window(w, h)
            zen = "+".join(f"between(t,{t0:.3f},{t1:.3f})" for t0, t1 in fp.zoom_spans)
            crop += (f",split=2[cu][cz];[cu]scale={W}:{H}:flags=lanczos,setsar=1[cun];"
                     f"[cz]crop={zw}:{zh}:{zx}:{zy},scale={W}:{H}:flags=lanczos,setsar=1[czm];"
                     f"[cun][czm]overlay=0:0:enable='{zen}'")
        else:
            crop += f",scale={W}:{H}:flags=lanczos,setsar=1"
        if fits:
            enable = "+".join(f"between(t,{t0:.3f},{t1:.3f})" for t0, t1 in fits)
            graph += [f"[0:v]fps={FPS},split=3[v1][v2][v3]",
                      f"[v1]{crop}[cropped]",
                      # blurred backdrop built at 1/4 size (cheap), then upscaled
                      f"[v2]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                      f"boxblur=10:2,eq=brightness=-0.12,scale={W}:{H}:flags=bilinear,setsar=1[bg]",
                      f"[v3]scale={W}:-2:flags=lanczos,setsar=1[fg]",
                      "[bg][fg]overlay=0:(H-h)/2-140[fit]",
                      f"[cropped][fit]overlay=0:0:enable='{enable}'[pic]"]
        else:
            graph.append(f"[0:v]fps={FPS},{crop}[pic]")
    else:  # already vertical
        graph.append(f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                     f"crop={W}:{H},setsar=1[pic]")
    pic = "pic"
    placed = place_broll(clip.get("broll") or [], start, dur, fits) if opts.broll else []
    track = None
    if placed:  # cutaways over the picture, under the captions (input index: after src and emojis)
        track = broll_track(placed, dur, out_dir / f"{name}_broll.mkv", cancelled)
        bi = 2 if emoji_events else 1
        ben = "+".join(f"between(t,{t0:.3f},{t0 + d:.3f})" for t0, d, _ in placed)
        graph += [f"[{bi}:v]fps={FPS},setsar=1[brt]",
                  f"[pic][brt]overlay=0:0:enable='{ben}':eof_action=pass[picb]"]
        pic = "picb"
    graph.append(f"[{pic}]ass={name}.ass:fontsdir=fonts[base]")

    # 3) emoji pops, audio, end card
    args = [settings.FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    last = "base"
    n_inputs = 1
    if emoji_events:
        # ONE continuous emoji track from 0 s to the end of the clip (transparent between pops).
        # Several late-starting, early-ending image inputs shifted with setpts deadlock FFmpeg 7's
        # threaded scheduler (seen on Debian's 7.1: render stuck forever at 0 % CPU).
        track = subtitles.emoji_track(emoji_events, dur, out_dir / f"{name}_emoji.ffconcat")
        args += ["-f", "concat", "-safe", "0", "-i", track.name]
        graph += [f"[1:v]fps={FPS},format=rgba[em]",
                  f"[{last}][em]overlay=x=(W-w)/2:y={emoji_events[0]['y']}-h/2:eof_action=pass:format=auto[emo]"]
        last, n_inputs = "emo", 2
    if track is not None:
        args += ["-i", track.name]
        n_inputs += 1
    graph.append(f"[0:a]afade=t=in:d=0.06,afade=t=out:st={max(0.0, dur - 0.3):.3f}:d=0.3,"
                 "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo[am]")
    if opts.outro:
        card = outro_card(opts.brand or DEFAULT_BRAND, opts.tagline or DEFAULT_TAGLINE)
        args += ["-loop", "1", "-framerate", str(FPS), "-t", str(OUTRO_SECONDS), "-i", str(card)]
        graph += [f"[{n_inputs}:v]format=yuv420p,setsar=1,fade=t=in:d=0.25[ov]",
                  f"[{last}]format=yuv420p[vm]",
                  f"anullsrc=r=48000:cl=stereo,atrim=0:{OUTRO_SECONDS}[as]",
                  "[vm][am][ov][as]concat=n=2:v=1:a=1[vout][aout]"]
    else:
        graph += [f"[{last}]format=yuv420p[vout]", "[am]anull[aout]"]

    mp4 = out_dir / f"{name}.mp4"
    args += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
             "-c:a", "aac", "-b:a", "160k", *video_encoder(), "-r", str(FPS),
             "-movflags", "+faststart", mp4.name]
    # Watchdog: even a slow CPU-only server renders faster than 1/15 real time; past that, the
    # render is stuck — fail this clip (the queue moves on) instead of blocking forever.
    try:
        run(args, cwd=out_dir, cancelled=cancelled, error_cls=RenderError, timeout=300 + dur * 15)
    except RenderError as e:
        if "timed out" in str(e):
            raise RenderError(f"Rendering of {mp4.name} stopped responding and was aborted.") from e
        raise
    finally:
        if track is not None:
            track.unlink(missing_ok=True)

    thumb = thumbnail(mp4, out_dir / f"{name}.jpg", at=min(1.0, dur / 3), width=360)
    (out_dir / f"{name}.srt").write_text(subtitles.srt(words), encoding="utf-8")
    log.info("Rendered %s (%.1fs, %d slide spans, %d emojis)", mp4.name, dur, len(fits), len(emoji_events))
    return {"start": round(start, 3), "end": round(end, 3), "duration": round(dur, 2),
            "segments": [[round(start, 3), round(end, 3)]],
            "video": mp4.name, "thumb": thumb.name, "srt": f"{name}.srt",
            "text": " ".join(w["w"] for w in words), "slide_spans": [list(s) for s in fits],
            "broll_used": [{"t": t0, "seconds": d, "source": c.get("source"), "credit": c.get("credit", ""),
                            "url": c.get("url", "")} for t0, d, c in placed]}

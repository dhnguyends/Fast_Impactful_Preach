"""Animated ASS subtitles. Two styles:

style="eeig" (default) — the church's own short-video look:
* normal sentence case, up to 2 lines, Montserrat Bold, white with a soft dark outline;
* the word being spoken sits in a light-blue rounded box (drawn as an ASS vector shape: words are
  laid out here with the font's real metrics, so each box matches its word);
* no emojis unless asked.

style="impact" — the bouncy Vizard-like template:
* 2–3 word UPPERCASE chunks, heavy Montserrat, white with a thick black outline.
* Each chunk bounces in (70 % → 110 % → 100 % in 170 ms).
* highlight="active":  the word being spoken turns green and grows slightly (karaoke style);
                        Claude's keywords stay yellow the rest of the time.
  highlight="keyword": one highlighted word per chunk (keyword yellow, otherwise green).
* Emojis (chosen by Claude for words of the clip) pop in above the caption: rendered as PNG
  sequences because libass cannot draw colour emoji.
"""
import re
import shutil
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import settings
from .errors import RenderError
from .transcribe import join_words

YELLOW = "&H0000E5FF&"   # ASS colours are &HBBGGRR&
GREEN = "&H0030E82B&"
WHITE = "&H00FFFFFF&"

CAPTION_Y = 0.70         # caption centre, fraction of frame height (as in the template)
EMOJI_GAP = 165          # px above the caption centre
FONT_SIZE = 100
MAX_CHUNK_WORDS = 3
MAX_CHUNK_CHARS = 18
POP = r"\fscx70\fscy70\t(0,90,\fscx110\fscy110)\t(90,170,\fscx100\fscy100)"
STOPWORDS = set("""le la les un une des de du d l à au aux et ou mais donc or ni car que qui quoi
dont où ce cet cette ces se sa son ses ma mon mes ta ton tes leur leurs nous vous ils elles il elle
je tu on y en ne pas plus est sont a ai as ont être avoir c qu s n j m t pour par sur dans avec
the a an and or but so of to in on at for by with is are was were be been it its this that these
those he she we you they i not do does did as from""".split())

FONT_FILES = ("Montserrat-Black.ttf", "Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf")


def ensure_fonts() -> Path:
    """Copy the caption font next to the app so libass finds it reliably."""
    settings.FONTS_DIR.mkdir(parents=True, exist_ok=True)
    for name in FONT_FILES:
        dst = settings.FONTS_DIR / name
        if not dst.exists():
            found = settings._find_font(name)
            if found:
                shutil.copy(found, dst)
    return settings.FONTS_DIR


def font_style() -> tuple[str, int]:
    """Heaviest available Montserrat weight → (family name, ASS bold flag)."""
    d = settings.FONTS_DIR
    if (d / "Montserrat-Black.ttf").exists():
        return "Montserrat Black", 0
    if (d / "Montserrat-ExtraBold.ttf").exists():
        return "Montserrat ExtraBold", 0
    if (d / "Montserrat-Bold.ttf").exists():
        return "Montserrat", -1
    return "Arial Black", 0


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w']", "", s).split("'")[-1]


def chunk_words(words: list) -> list:
    """Split words into short caption chunks, breaking on punctuation and pauses."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text_len = sum(len(x["w"]) + 1 for x in cur)
        if (nxt is None or len(cur) >= MAX_CHUNK_WORDS
                or text_len + len(nxt["w"]) > MAX_CHUNK_CHARS
                or re.search(r"[.!?…,;:]$", w["w"])
                or nxt["s"] - w["e"] > 0.35):
            chunks.append(cur)
            cur = []
    return chunks


def ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _header(title_style: bool) -> str:
    W, H = settings.OUT_W, settings.OUT_H
    family, bold = font_style()
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{family},{FONT_SIZE},{WHITE},{WHITE},&H00000000&,&H90000000&,{bold},0,0,0,100,100,1,0,1,8,4,5,90,90,0,1
Style: Title,{family},64,{WHITE},{WHITE},&H00000000&,&H00000000&,{bold},0,0,0,100,100,0,0,3,18,0,8,80,80,170,1
Style: Mark,{family},34,{WHITE},{WHITE},&H00000000&,&H00000000&,{bold},0,0,0,100,100,0,0,1,3,0,9,70,70,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list, duration: float, keywords=(), emoji_cues=(), highlight: str = "active",
              emojis: bool = True, title: str | None = None, watermark: str = "",
              style: str = "eeig", title_until: float | None = None) -> tuple[str, list]:
    """words: [{'w','s','e'}] relative to the clip start; the title shows from 0 to title_until.
    Returns (ass_text, emoji_events [{'emoji','s','e','y'}])."""
    title_end = min(duration, title_until if title_until else 3.2)
    if style == "eeig":
        return _build_eeig(words, duration, emoji_cues if emojis else (), title, watermark, title_end)
    W, H = settings.OUT_W, settings.OUT_H
    y = int(H * CAPTION_Y)
    pos = r"\an5\pos(" + f"{W // 2},{y}" + ")"
    kw = {norm(k) for k in keywords if k}
    cue_map = {}
    for c in emoji_cues if emojis else ():
        for part in re.split(r"\s+", c.get("word", "")):
            if norm(part):
                cue_map.setdefault(norm(part), c.get("emoji", ""))

    lines = []
    if title:
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(title_end)},Title,,0,0,0,,"
                     r"{\an8\3c&H00E5FF&\4c&H00E5FF&\fad(0,250)\fscx60\fscy60\t(0,140,\fscx106\fscy106)"
                     r"\t(140,240,\fscx100\fscy100)\c&H000000&}" + _esc(title.upper()))
    if watermark:
        lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Mark,,0,0,0,,"
                     r"{\alpha&H30&}" + _esc(watermark))

    chunks = chunk_words(words)
    emoji_events, alt, last_emoji_end = [], 0, -9.0
    for i, ch in enumerate(chunks):
        start = ch[0]["s"]
        nxt_start = chunks[i + 1][0]["s"] if i + 1 < len(chunks) else duration
        end = max(min(nxt_start, ch[-1]["e"] + 0.6), start + 0.25)

        if highlight == "active":
            for j, w in enumerate(ch):
                s = start if j == 0 else w["s"]
                e = ch[j + 1]["s"] if j + 1 < len(ch) else end
                if e - s < 0.02:
                    continue
                parts = []
                for k, x in enumerate(ch):
                    t = _esc(x["w"].upper())
                    if k == j:  # the spoken word; the first one rides the chunk's pop instead of growing
                        grow = "" if j == 0 else r"\fscx114\fscy114"
                        reset = "" if j == 0 else r"\fscx100\fscy100"
                        parts.append(r"{\c" + GREEN + grow + "}" + t + r"{\c" + WHITE + reset + "}")
                    elif norm(x["w"]) in kw:
                        parts.append(r"{\c" + YELLOW + "}" + t + r"{\c" + WHITE + "}")
                    else:
                        parts.append(t)
                tags = "{" + pos + (POP if j == 0 else "") + "}"
                lines.append(f"Dialogue: 1,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{tags}{join_words(parts)}")
        else:
            idx = next((j for j, w in enumerate(ch) if norm(w["w"]) in kw), None)
            color = YELLOW
            if idx is None:
                cands = [(len(norm(w["w"])), j) for j, w in enumerate(ch)
                         if norm(w["w"]) not in STOPWORDS and len(norm(w["w"])) > 2]
                idx = max(cands)[1] if cands else None
                color = GREEN if alt % 2 == 0 else YELLOW
                alt += 1
            parts = [(r"{\c" + color + "}" + _esc(w["w"].upper()) + r"{\c" + WHITE + "}") if j == idx
                     else _esc(w["w"].upper()) for j, w in enumerate(ch)]
            lines.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,"
                         "{" + pos + POP + "}" + join_words(parts))

        for w in ch:
            emo = cue_map.get(norm(w["w"]))
            if emo and w["s"] - last_emoji_end > 0.3:
                e_end = min(max(end, w["s"] + 1.2), w["s"] + 2.0, duration)
                emoji_events.append({"emoji": emo, "s": w["s"], "e": e_end, "y": y - EMOJI_GAP})
                last_emoji_end = e_end
                cue_map.pop(norm(w["w"]), None)  # each cue pops once
                break
    return _header(bool(title)) + "\n".join(lines) + "\n", emoji_events


# ---------- "eeig" style: sentence case + light-blue box on the spoken word ----------

EEIG_FONT_FILE = "Montserrat-Bold.ttf"
EEIG_SIZE = 66                    # ASS font size (libass units)
EEIG_MAX_LINE = 900               # px available per line (1080 minus margins)
EEIG_LINE_GAP = 1.42              # line pitch, in ems
EEIG_BOX = "&H00E8B44E&"          # light blue (#4EB4E8), ASS &HBBGGRR&
EEIG_PAD_X, EEIG_PAD_Y, EEIG_RADIUS = 12, 7, 12
_eeig_cache: dict = {}


def win_ratio(path: Path) -> float:
    """(usWinAscent + usWinDescent) / unitsPerEm: libass sizes fonts with this ratio, so
    em = ASS font size / ratio. Read straight from the TrueType tables."""
    import struct
    data = Path(path).read_bytes()
    tables = {}
    for i in range(struct.unpack(">H", data[4:6])[0]):
        tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        tables[tag.decode("latin1")] = off
    upem = struct.unpack(">H", data[tables["head"] + 18: tables["head"] + 20])[0]
    win_asc, win_desc = struct.unpack(">HH", data[tables["OS/2"] + 74: tables["OS/2"] + 78])
    return (win_asc + win_desc) / upem


def _eeig_font():
    """(PIL font at the em size libass will use, em px, family name)."""
    if "f" not in _eeig_cache:
        path = ensure_fonts() / EEIG_FONT_FILE
        if not path.exists():
            raise RenderError(f"Caption font {EEIG_FONT_FILE} is missing from {settings.FONTS_DIR}.")
        em = EEIG_SIZE / win_ratio(path)
        _eeig_cache["f"] = (ImageFont.truetype(str(path), size=em), em, "Montserrat")
    return _eeig_cache["f"]


def eeig_pages(words: list, font, max_line: int = EEIG_MAX_LINE) -> list:
    """Group words into pages of ≤ 2 lines that fit max_line px. A page also ends at a sentence
    end or a pause > 0.6 s (once it holds a few words), like a human editor's caption cards.
    Returns [[line_words, ...], ...]."""
    space = font.getlength(" ")
    pages, lines, line, width = [], [], [], 0.0
    for i, w in enumerate(words):
        wl = font.getlength(w["w"])
        if line and width + space + wl > max_line:
            lines.append(line)
            line, width = [], 0.0
            if len(lines) == 2:
                pages.append(lines)
                lines = []
        line.append(w)
        width = wl if len(line) == 1 else width + space + wl
        nxt = words[i + 1] if i + 1 < len(words) else None
        count = sum(len(x) for x in lines) + len(line)
        if nxt is None or (count >= 3 and (re.search(r"[.!?…]$", w["w"]) or nxt["s"] - w["e"] > 0.6)):
            lines.append(line)
            pages.append(lines)
            lines, line, width = [], [], 0.0
    return pages


def _rounded_rect(w: float, h: float, r: float) -> str:
    r = min(r, h / 2, w / 2)
    k = r * 0.45  # Bézier handle
    f = lambda v: f"{v:.1f}"  # noqa: E731
    return (f"m {f(r)} 0 l {f(w - r)} 0 b {f(w - k)} 0 {f(w)} {f(k)} {f(w)} {f(r)} "
            f"l {f(w)} {f(h - r)} b {f(w)} {f(h - k)} {f(w - k)} {f(h)} {f(w - r)} {f(h)} "
            f"l {f(r)} {f(h)} b {f(k)} {f(h)} 0 {f(h - k)} 0 {f(h - r)} "
            f"l 0 {f(r)} b 0 {f(k)} {f(k)} 0 {f(r)} 0")


TITLE_SIZE = 72                   # hook title (EEIG style): white, bold, top of the frame, ≤ 2 lines
TITLE_MARGIN_V = 190              # px from the top (about 10 % of the height, like the church's shorts)
TITLE_MAX_LINE = 900              # px per line (1080 minus side margins)
_BREAK_AFTER = (":", "?", "!", ",", ";", "…", ".")


def title_lines(title: str, font, max_line: int = TITLE_MAX_LINE) -> list:
    """Split the hook title into ≤ 2 balanced lines. Prefers breaking right after punctuation
    ("Justifié avant d'être sanctifié :" / "la grâce avant la loi"), never before it."""
    words = title.split()
    if len(words) < 2 or font.getlength(title) <= max_line:
        return [title]
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if b[0] in ":?!;,.…»":
            continue                                    # never start a line with punctuation
        wa, wb = font.getlength(a), font.getlength(b)
        cost = abs(wa - wb) + (0 if a.endswith(_BREAK_AFTER) else 0.35 * max_line)
        cost += 2 * max_line if max(wa, wb) > max_line else 0
        if best is None or cost < best[0]:
            best = (cost, [a, b])
    return best[1] if best else [title]


def _build_eeig(words: list, duration: float, emoji_cues, title, watermark,
                title_end: float = 3.2) -> tuple[str, list]:
    W, H = settings.OUT_W, settings.OUT_H
    font, em, family = _eeig_font()
    space = font.getlength(" ")
    pitch = em * EEIG_LINE_GAP
    yc = H * CAPTION_Y
    head = _header(bool(title)).replace(
        "Style: Mark,",
        f"Style: CapE,{family},{EEIG_SIZE},{WHITE},{WHITE},&H40101010&,&H80000000&,-1,0,0,0,100,100,0,0,1,3,2,4,0,0,0,1\n"
        f"Style: TitleE,{family},{TITLE_SIZE},{WHITE},{WHITE},&H50000000&,&H70000000&,-1,0,0,0,100,100,0,0,1,2.5,3,8,110,110,{TITLE_MARGIN_V},1\n"
        "Style: Mark,")
    lines_out = []
    if title:  # hook title: sentence case as written, on ≤ 2 balanced lines broken after punctuation
        tfont = font.font_variant(size=em * TITLE_SIZE / EEIG_SIZE)
        lines_out.append(f"Dialogue: 3,{ass_time(0)},{ass_time(title_end)},TitleE,,0,0,0,,"
                         r"{\fad(250,350)}" + r"\N".join(_esc(l) for l in title_lines(title, tfont)))
    if watermark:
        lines_out.append(f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Mark,,0,0,0,,"
                         r"{\alpha&H30&}" + _esc(watermark))

    cue_map = {}
    for c in emoji_cues:
        for part in re.split(r"\s+", c.get("word", "")):
            if norm(part):
                cue_map.setdefault(norm(part), c.get("emoji", ""))
    emoji_events, last_emoji_end = [], -9.0

    pages = eeig_pages(words, font)
    for pi, page in enumerate(pages):
        p_words = [w for ln in page for w in ln]
        start = p_words[0]["s"]
        nxt_start = pages[pi + 1][0][0]["s"] if pi + 1 < len(pages) else duration
        end = max(min(nxt_start, p_words[-1]["e"] + 0.6), start + 0.3)
        # box interval of each word (by position in the page): from its start to the next word's start
        box = {id(w): (start if k == 0 else w["s"], p_words[k + 1]["s"] if k + 1 < len(p_words) else end)
               for k, w in enumerate(p_words)}
        n = len(page)
        for li, ln in enumerate(page):
            y = yc + (li - (n - 1) / 2) * pitch
            widths = [font.getlength(w["w"]) for w in ln]
            x = (W - (sum(widths) + space * (len(ln) - 1))) / 2
            for wi, (w, wl) in enumerate(zip(ln, widths)):
                # the word itself, for the whole page
                lines_out.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},CapE,,0,0,0,,"
                                 "{" + rf"\an4\pos({x:.1f},{y:.1f})\fad(70,0)" + "}" + _esc(w["w"]))
                # the light-blue box while this word is spoken
                b0, b1 = box[id(w)]
                if b1 - b0 >= 0.02:
                    bw, bh = wl + 2 * EEIG_PAD_X, em * 1.08 + 2 * EEIG_PAD_Y
                    lines_out.append(
                        f"Dialogue: 1,{ass_time(b0)},{ass_time(b1)},CapE,,0,0,0,,"
                        "{" + rf"\an7\pos({x - EEIG_PAD_X:.1f},{y - bh / 2:.1f})\p1\bord0\shad0\1c{EEIG_BOX}\alpha&H08&"
                        + "}" + _rounded_rect(bw, bh, EEIG_RADIUS) + r"{\p0}")
                x += wl + space
        for w in p_words:  # optional emoji above the caption block
            emo = cue_map.get(norm(w["w"]))
            if emo and w["s"] - last_emoji_end > 0.3:
                e_end = min(max(end, w["s"] + 1.2), w["s"] + 2.0, duration)
                emoji_events.append({"emoji": emo, "s": w["s"], "e": e_end,
                                     "y": int(yc - (n / 2) * pitch - 90)})
                last_emoji_end = e_end
                cue_map.pop(norm(w["w"]), None)
                break
    return head + "\n".join(lines_out) + "\n", emoji_events


def srt(words: list) -> str:
    def ts(t):
        ms = int(round(max(t, 0) * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{ts(ch[0]['s'])} --> {ts(ch[-1]['e'])}\n{join_words(ch)}\n"
                     for i, ch in enumerate(chunk_words(words), 1))


# ---------- emoji pop sequences ----------

EMOJI_SIZE = 150


def _emoji_image(emoji: str, size: int) -> Image.Image:
    if not settings.EMOJI_FONT:
        raise RenderError("No colour emoji font found (install Noto Color Emoji or set EMOJI_FONT).")
    font = ImageFont.truetype(settings.EMOJI_FONT, 109)  # 109 px: the bitmap size of Noto/Segoe colour glyphs
    im = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((110, 110), emoji, font=font, embedded_color=True, anchor="mm")
    im = im.crop(im.getbbox() or (0, 0, 220, 220))
    im.thumbnail((size, size), Image.LANCZOS)
    return im


def emoji_track(events: list, duration: float, path: Path, fps: int = settings.OUT_FPS) -> Path:
    """Write an ffconcat playlist making all emoji pops ONE continuous image track:
    transparent frame until the first pop, the pop frames, transparent again, … until `duration`."""
    seqs = [(ev, emoji_sequence(ev["emoji"], max(8, int((ev["e"] - ev["s"]) * fps)))) for ev in events]
    first = Image.open(seqs[0][1] / "0000.png")
    blank = settings.CACHE_DIR / "emoji" / f"blank_{first.width}x{first.height}.png"
    if not blank.exists():
        Image.new("RGBA", first.size, (0, 0, 0, 0)).save(blank)

    def entry(f: Path, d: float) -> list:
        return [f"file '{f.resolve().as_posix()}'", f"duration {d:.4f}"]

    lines, t, step = ["ffconcat version 1.0"], 0.0, 1 / fps
    for ev, seq in sorted(seqs, key=lambda x: x[0]["s"]):
        if ev["s"] - t > step / 2:
            lines += entry(blank, ev["s"] - t)
            t = ev["s"]
        for f in sorted(seq.glob("*.png")):
            lines += entry(f, step)
            t += step
    lines += entry(blank, max(step, duration - t + 1.0))
    lines.append(f"file '{blank.resolve().as_posix()}'")  # the concat demuxer ignores the last duration
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def emoji_sequence(emoji: str, frames: int, fps: int = settings.OUT_FPS) -> Path:
    """PNG sequence of the emoji popping in (scale overshoot), holding, then fading (cached)."""
    key = "_".join(f"{ord(c):x}" for c in emoji) + f"_{frames}"
    out_dir = settings.CACHE_DIR / "emoji" / key
    if (out_dir / f"{frames - 1:04d}.png").exists():
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _emoji_image(emoji, EMOJI_SIZE)
    canvas = int(EMOJI_SIZE * 1.3)
    for i in range(frames):
        t = i / fps
        s = 0.2 + 1.02 * (t / 0.12) if t < 0.12 else 1.22 - 0.22 * ((t - 0.12) / 0.12) if t < 0.24 else 1.0
        fade = min(1.0, (frames - 1 - i) / 4)
        frame = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        w, h = max(2, int(base.width * s)), max(2, int(base.height * s))
        em = base.resize((w, h), Image.LANCZOS)
        if fade < 1.0:
            em.putalpha(em.getchannel("A").point(lambda a: int(a * fade)))
        frame.alpha_composite(em, ((canvas - w) // 2, (canvas - h) // 2))
        frame.save(out_dir / f"{i:04d}.png")
    return out_dir

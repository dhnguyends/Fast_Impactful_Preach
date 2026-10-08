"""Unit tests for the pure logic (no GPU, no network, no FFmpeg)."""
import re

import pytest

from fip import reframe, segment, subtitles, transcribe
from fip.errors import ConfigError
from fip.models import Options, fmt_time, parse_time, spec_json
from fip.render import clip_bounds


def W(text, s, e, p=1.0):
    return {"w": text, "s": s, "e": e, "p": p}


def words_from(text: str, start=0.0, step=0.4):
    out, t = [], start
    for tok in text.split():
        out.append(W(tok, round(t, 2), round(t + step - 0.05, 2)))
        t += step
    return out


# ---------- models ----------

@pytest.mark.parametrize("v,expected", [("75", 75), ("1:15", 75), ("01:02:03", 3723), ("12,5", 12.5),
                                        ("", None), ("abc", None), ("-3", None), ("1:2:3:4", None)])
def test_parse_time(v, expected):
    assert parse_time(v) == expected


def test_fmt_time():
    assert fmt_time(75) == "1:15" and fmt_time(3723) == "1:02:03"


def test_options_clamp_and_defaults():
    o = Options(num_clips=25)
    assert o.num_clips == 10
    assert Options(num_clips=0).num_clips == 1
    assert (o.min_duration, o.max_duration) == (30, 60)
    assert Options(min_duration=50, max_duration=40).max_duration > 50
    assert Options(unknown_key=1).num_clips == 10  # old project files stay loadable


def test_options_reject_bad_enum():
    with pytest.raises(Exception):
        Options(highlight="rainbow")


# ---------- transcript post-processing ----------

def test_merge_fragments_french():
    ws = [W("Dans", 0, .2), W("l", .2, .3), W("'Ancien", .3, .6), W("Qu", .7, .8), W("'est", .8, .9),
          W("-ce", .9, 1.0), W("pas", 1.0, 1.2), W("»", 1.2, 1.25)]
    out = [w["w"] for w in transcribe.merge_fragments(ws)]
    assert out == ["Dans", "l'Ancien", "Qu'est-ce", "pas»"]


def test_build_sentences_punctuation_and_pause():
    ws = words_from("Dieu est amour. Il nous aime") + [W("vraiment", 10.0, 10.4)]
    sents = transcribe.build_sentences(ws)
    assert [s["text"] for s in sents] == ["Dieu est amour.", "Il nous aime", "vraiment"]
    assert sents[0]["w0"] == 0 and sents[0]["w1"] == 2 and sents[1]["w0"] == 3


def test_build_sentences_ignores_abbreviation():
    sents = transcribe.build_sentences(words_from("Comme dit cf. Jean trois seize."))
    assert len(sents) == 1


def test_join_words_french_punctuation():
    assert transcribe.join_words(["Bonjour", ",", "le", "monde", "?"]) == "Bonjour, le monde?"


# ---------- subtitles ----------

def test_chunks_are_short():
    chunks = subtitles.chunk_words(words_from("la colère contre son frère est déjà un crime devant Dieu."))
    assert all(len(c) <= subtitles.MAX_CHUNK_WORDS for c in chunks)
    assert sum(len(c) for c in chunks) == 11  # every word kept, none duplicated


def test_ass_active_word_highlight():
    ws = words_from("tu ne tueras pas")
    ass, emojis = subtitles.build_ass(ws, 2.0, keywords=["tueras"], emoji_cues=[{"word": "tueras", "emoji": "⚔️"}],
                                      style="impact")
    events = [l for l in ass.splitlines() if l.startswith("Dialogue: 1")]
    assert len(events) == 4                                     # one event per spoken word
    assert all(e.count(subtitles.GREEN) == 1 for e in events)   # exactly one active (green) word
    assert r"\t(0,90" in events[0] and r"\t(0,90" not in events[1]  # bounce only when the chunk appears
    assert "TU" in events[0] and subtitles.YELLOW in events[0]  # keyword yellow when not spoken
    assert emojis and emojis[0]["emoji"] == "⚔️"
    starts = [re.search(r"Dialogue: 1,([^,]+),([^,]+)", e).groups() for e in events]
    assert all(starts[i][1] == starts[i + 1][0] for i in range(len(starts) - 1))  # contiguous, no flicker


def test_ass_keyword_mode_and_no_emojis():
    ass, emojis = subtitles.build_ass(words_from("la grâce de Dieu"), 2.0, keywords=["grâce"],
                                      emoji_cues=[{"word": "Dieu", "emoji": "✝️"}], highlight="keyword", emojis=False,
                                      style="impact")
    assert len([l for l in ass.splitlines() if l.startswith("Dialogue: 1")]) == 2
    assert emojis == []


def test_srt():
    out = subtitles.srt(words_from("Dieu est amour."))
    assert out.startswith("1\n00:00:00,000 --> ") and "Dieu est amour." in out


# ---------- selection maths ----------

def _sentences(n=40, length=8.0):
    return [{"id": i, "w0": i, "w1": i, "s": i * length, "e": i * length + length - 0.5, "text": f"S{i}"}
            for i in range(n)]


def test_sanitize_enforces_bounds_and_overlaps():
    sents = _sentences()
    raw = [{"start_sentence": 0, "end_sentence": 20, "score": 90},   # 167 s → trimmed
           {"start_sentence": 10, "end_sentence": 10, "score": 95},  # 7.5 s → extended
           {"start_sentence": 2, "end_sentence": 5, "score": 50},    # overlaps a better clip → dropped
           {"start_sentence": 30, "end_sentence": 34, "score": 80}]
    out = segment.sanitize_clips(raw, sents, Options(min_duration=30, max_duration=60), limit=10)
    assert [c["score"] for c in out] == [95, 90, 80]
    assert [c["index"] for c in out] == [0, 1, 2]
    for c in out:
        assert 30 * 0.85 <= c["approx_duration"] <= 60 * 1.15
    spans = sorted((c["start_sentence"], c["end_sentence"]) for c in out)
    assert all(spans[i][1] < spans[i + 1][0] for i in range(len(spans) - 1))


def test_sanitize_limit():
    sents = _sentences()
    raw = [{"start_sentence": i * 6, "end_sentence": i * 6 + 4, "score": 50 + i} for i in range(6)]
    out = segment.sanitize_clips(raw, sents, Options(), limit=3)
    assert [c["score"] for c in out] == [55, 54, 53]


def test_range_to_sentences():
    sents = _sentences()
    assert segment.range_to_sentences(sents, "0:20", "0:45") == (2, 5)
    with pytest.raises(ConfigError):
        segment.range_to_sentences(sents, "1:00", "0:30")


def test_normalise_maps_fields():
    out = segment._normalise({"clips": [{"start_sentence": 1, "end_sentence": 3, "virality_score": 88,
                                         "reasoning": "r", "category": "weird"}]})
    c = out["clips"][0]
    assert c["score"] == 88 and c["why_meaningful"] == "r" and c["category"] == "application"


def test_clip_bounds_padding_never_overlaps_neighbours():
    ws = words_from("a b c d e f", step=1.0)
    sents = [{"id": 0, "w0": 0, "w1": 1}, {"id": 1, "w0": 2, "w1": 3}, {"id": 2, "w0": 4, "w1": 5}]
    s, e = clip_bounds(ws, sents, 1, 1, 10.0)
    assert ws[1]["e"] < s <= ws[2]["s"] and ws[3]["e"] <= e < ws[4]["s"]


def test_spec_json_shape():
    j = spec_json({"index": 0, "start": 1.5, "end": 40.0, "title": "T", "hook": "H", "score": 90,
                   "core_message": "S", "why_meaningful": "R"})
    assert j["start_time"] == 1.5 and j["end_time"] == 40.0 and j["virality_score"] == 90
    assert {"title", "hook", "summary", "reasoning"} <= j.keys()


# ---------- reframing ----------

def _whites(pattern: dict, seconds: float):
    """White-fraction samples: {(t0, t1): fraction}, 0.05 (speaker shot) elsewhere."""
    step = 1 / reframe.SAMPLE_FPS
    out = []
    for i in range(int(seconds * reframe.SAMPLE_FPS)):
        t = i * step
        out.append((t, next((f for (a, b), f in pattern.items() if a <= t < b), 0.05)))
    return out


def test_slide_span_includes_fade_and_padding():
    # speaker 0-10 s, fade-in 10-11 s (25 % white), slide 11-16 s (60 %), fade-out 16-16.5 s, speaker after
    s = _whites({(10, 11): 0.25, (11, 16): 0.60, (16, 16.5): 0.25}, 30)
    (a, b), = reframe.slide_spans(s, 30.0)
    assert a == pytest.approx(10 - reframe.SLIDE_PAD)          # the fade-in belongs to the slide, + margin
    assert b == pytest.approx(16.5 + reframe.SLIDE_PAD)


def test_slide_span_snaps_to_clip_edges():
    s = _whites({(1.5, 8): 0.6, (25, 29): 0.6}, 30)            # slide starts 1.5 s in / ends 1 s before the end
    pad = reframe.SLIDE_PAD
    assert reframe.slide_spans(s, 30.0) == [(0.0, pytest.approx(8 + pad)), (pytest.approx(25 - pad), 30.0)]


def test_slide_ignores_short_flashes_and_speaker_shots():
    s = _whites({(5, 5.5): 0.6, (12, 20): 0.12}, 30)           # 0.5 s flash, and a bright speaker shot
    assert reframe.slide_spans(s, 30.0) == []


def test_slide_spans_merge_close_slides():
    s = _whites({(5, 9): 0.6, (10, 14): 0.6}, 30)              # two slides 1 s apart → one span
    assert len(reframe.slide_spans(s, 30.0)) == 1


def test_plan_center_and_track():
    track = [(t / 2, 1500.0, 400.0, 120.0) for t in range(40)]
    center = reframe.plan(track, 20.0, 1920, 1080, [], mode="center", zoom_levels=(1.0,))
    assert center[0][1] == (1920 - center[0][3]) // 2 or abs(center[0][1] - (1920 - 607) / 2) < 2
    tracked = reframe.plan(track, 20.0, 1920, 1080, [8.0], mode="track", zoom_levels=(1.0,))
    x, w = tracked[0][1], tracked[0][3]
    assert x <= 1500 <= x + w                                     # speaker inside the window
    assert len({(c[3], c[4]) for c in tracked}) == 1              # the window size NEVER changes
    assert all(0 <= c[1] <= 1920 - c[3] and 0 <= c[2] <= 1080 - c[4] for c in tracked)


def test_zoom_spans_alternate_shots():
    assert reframe.zoom_spans([5.0, 10.0, 15.0], 20.0) == [(5.0, 10.0), (15.0, 20.0)]
    zw, zh, zx, zy = reframe.zoom_window(606, 1080)
    assert (zw, zh) == (530, 946) and zx + zw <= 606 and zy + zh <= 1080


def test_follow_speaker_ignores_far_face():
    samples = [(i / 2, [(1500.0, 400.0, 100.0)]) for i in range(10)]
    samples[5] = (2.5, [(300.0, 400.0, 160.0)])                   # someone else, larger, once
    out = reframe._follow_speaker(samples, 1920)
    assert out[5][1] is None and out[6][1] == 1500.0


def test_sendcmd_targets_named_crop():
    script = reframe.sendcmd_script([(0.0, 10, 0, 606, 1080)])
    assert "crop@track x 10" in script and " w " not in script and " h " not in script  # moves only


def test_emoji_track_is_one_continuous_playlist(tmp_path):
    events = [{"emoji": "✝️", "s": 2.0, "e": 3.2, "y": 1179}, {"emoji": "🔥", "s": 10.0, "e": 11.2, "y": 1179}]
    path = subtitles.emoji_track(events, 15.0, tmp_path / "t.ffconcat")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ffconcat version 1.0"
    durations = [float(l.split()[1]) for l in lines if l.startswith("duration")]
    assert durations[0] == pytest.approx(2.0)            # blank until the first pop
    assert sum(durations) >= 15.0                         # covers the whole clip: no early end-of-stream
    assert lines[-1].startswith("file '") and "blank_" in lines[-1]


def test_outro_card_regenerates_corrupted_cache(monkeypatch, tmp_path):
    from PIL import Image
    from fip import render, settings
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    card = render.outro_card("Église", "Tagline")
    Image.new("RGB", (1920, 1080)).save(card)          # simulate a damaged cache file
    assert Image.open(render.outro_card("Église", "Tagline")).size == (settings.OUT_W, settings.OUT_H)


def test_slide_span_does_not_swallow_a_real_opening_shot():
    s = _whites({(2.25, 10): 0.6}, 30)                         # speaker for 2 s, then a slide (short #1)
    (a, _b), = reframe.slide_spans(s, 30.0)
    assert a > 1.0                                              # the opening speaker shot stays cropped


# ---------- EEIG caption style & end card ----------

def test_win_ratio_montserrat():
    from fip import settings
    r = subtitles.win_ratio(subtitles.ensure_fonts() / "Montserrat-Bold.ttf")
    assert r == pytest.approx(1.379, abs=0.001)            # calibrated against libass: widths match within 1 %


def test_eeig_pages_fit_and_keep_every_word():
    font, em, _ = subtitles._eeig_font()
    ws = words_from("Imaginez un papa qui sort son enfant d'une maison en feu. Il lui sauve la vie. "
                    "Il ne va pas commencer par lui donner des règles de vie mais d'abord il va le sauver.")
    pages = subtitles.eeig_pages(ws, font)
    assert [w["w"] for pg in pages for ln in pg for w in ln] == [w["w"] for w in ws]   # all words, in order
    for pg in pages:
        assert 1 <= len(pg) <= 2
        for ln in pg:
            width = sum(font.getlength(w["w"]) for w in ln) + font.getlength(" ") * (len(ln) - 1)
            assert width <= subtitles.EEIG_MAX_LINE
    assert pages[0][-1][-1]["w"] == "feu."                  # a card ends at the end of a sentence


def test_eeig_ass_boxes_follow_the_spoken_word():
    ws = words_from("La grâce avant la loi, toujours.")
    ass, emojis = subtitles.build_ass(ws, 4.0, emoji_cues=[{"word": "grâce", "emoji": "🙏"}], style="eeig", emojis=False)
    text = [l for l in ass.splitlines() if l.startswith("Dialogue: 2")]
    boxes = [l for l in ass.splitlines() if l.startswith("Dialogue: 1")]
    assert len(text) == len(ws) and len(boxes) == len(ws)  # each word placed once, one box per word
    assert all(r"\p1" in b and subtitles.EEIG_BOX in b for b in boxes)
    times = [re.search(r"Dialogue: 1,([^,]+),([^,]+)", b).groups() for b in boxes]
    assert all(times[i][1] == times[i + 1][0] for i in range(len(times) - 1))   # the box moves without gaps
    assert "GRÂCE" not in ass and "grâce" in ass           # sentence case, not uppercase
    assert emojis == []                                     # no emojis unless asked


def test_end_card_uses_the_church_logo(monkeypatch, tmp_path):
    from PIL import Image
    from fip import render, settings
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    logo = tmp_path / "logo.png"
    Image.new("RGB", (176, 176), (30, 120, 200)).save(logo)  # a blue square as the "logo"
    monkeypatch.setattr(settings, "brand_logo", lambda: logo)
    card = Image.open(render.outro_card("Église", "Tagline")).convert("RGB")
    assert card.size == (settings.OUT_W, settings.OUT_H)
    cx, top = settings.OUT_W // 2, settings.OUT_H // 2 - 80 - 380
    assert card.getpixel((cx - 175, top + 190)) == (255, 255, 255)   # white tile around the logo
    r, g, b = card.getpixel((cx, top + 190))
    assert b > 150 and r < 80                                          # the logo in the middle of the tile
    monkeypatch.setattr(settings, "brand_logo", lambda: None)          # no logo → different card, no crash
    assert render.outro_card("Église", "Tagline") != card


# ---------- hook title ----------

def test_hook_title_timing_modes():
    ws = words_from("La grâce avant la loi.")
    ass, _ = subtitles.build_ass(ws, 40.0, title="Justifié avant d'être sanctifié", title_until=15)
    line = next(l for l in ass.splitlines() if ",TitleE," in l)
    assert line.startswith("Dialogue: 3,0:00:00.00,0:00:15.00,") and "Justifié avant" in line   # as written, not uppercased
    ass, _ = subtitles.build_ass(ws, 40.0, title="T", title_until=40.0)
    assert ",0:00:40.00,TitleE," in ass                                          # whole short
    ass, _ = subtitles.build_ass(ws, 8.0, title="T", title_until=15)
    assert ",0:00:08.00,TitleE," in ass                                          # never past the clip
    assert ",TitleE," not in subtitles.build_ass(ws, 8.0, title=None)[0]          # off


def test_options_title_mode_and_old_projects():
    assert Options().title_mode == "hook" and Options().title_seconds == 15
    assert Options(show_title=True).title_mode == "hook"   # old projects still load (unknown key ignored)


def test_normalise_fills_hook_and_publish_titles():
    out = segment._normalise({"clips": [{"start_sentence": 1, "end_sentence": 3, "title": "La grâce  d'abord",
                                         "virality_score": 80}]})
    c = out["clips"][0]
    assert c["hook_title"] == "La grâce d'abord" and c["publish_title"] == "La grâce d'abord"
    assert "hook_title" in segment.CLIP_SCHEMA["required"] and "publish_title" in segment.CLIP_SCHEMA["required"]


def test_title_lines_break_after_punctuation():
    font = subtitles._eeig_font()[0].font_variant(size=100)
    assert subtitles.title_lines("Justifié avant d'être sanctifié : la grâce avant la loi", font) == \
        ["Justifié avant d'être sanctifié :", "la grâce avant la loi"]
    assert subtitles.title_lines("Femme, soumets-toi ? Mauvaise lecture !", font)[0] == "Femme, soumets-toi ?"
    assert subtitles.title_lines("La dîme", font) == ["La dîme"]

"""Montage mode: several sermon passages joined in one short."""
import json
import shutil
import subprocess

import pytest

from fip import pipeline, reframe, render, segment, settings
from fip.models import Options, passages_of, spec_json


def _sentences(n=40, length=8.0):
    return [{"id": i, "w0": i, "w1": i, "s": i * length, "e": i * length + length - 0.5, "text": f"S{i}"}
            for i in range(n)]


def P(a, b, role="body"):
    return {"start_sentence": a, "end_sentence": b, "role": role}


def test_passages_of_old_and_new_clips():
    assert passages_of({"start_sentence": 3, "end_sentence": 7}) == [(3, 7)]
    assert passages_of({"start_sentence": 3, "end_sentence": 30, "passages": [P(30, 30, "hook"), P(3, 5)]}) == \
        [(30, 30), (3, 5)]


def test_clean_passages_order_hook_overlap_merge():
    cp = segment.clean_passages
    # body passages are put in sermon order; a later hook may play first
    assert cp([P(30, 31, "hook"), P(12, 14), P(3, 5)], 40) == [[30, 31, "hook"], [3, 5, "body"], [12, 14, "body"]]
    # a "hook" that comes first in the sermon anyway is just sermon order
    assert cp([P(2, 2, "hook"), P(10, 12)], 40) == [[2, 2, "hook"], [10, 12, "body"]]
    # a body passage listed first out of order is NOT allowed to jump ahead
    assert cp([P(30, 31), P(3, 5)], 40) == [[3, 5, "body"], [30, 31, "body"]]
    # contiguous / overlapping passages merge; a hook inside a body passage is dropped
    assert cp([P(4, 4, "hook"), P(3, 5), P(6, 8)], 40) == [[3, 8, "body"]]
    # at most 4 passages; ids clamped
    assert len(cp([P(i * 5, i * 5) for i in range(7)], 40)) == 4
    assert cp([P(38, 99)], 40) == [[38, 39, "body"]]
    # montage off: keep the longest passage only
    assert cp([P(1, 1), P(10, 14)], 40, montage=False) == [[10, 14, "body"]]


def test_sanitize_montage_duration_and_overlaps():
    sents = _sentences()
    opts = Options(min_duration=30, max_duration=60, montage_max=90)
    raw = [{"score": 95, "passages": [P(30, 30, "hook"), P(2, 5), P(12, 15)]},  # 7.5 + 31.5 + 31.5 = 70.5 s
           {"score": 90, "start_sentence": 4, "end_sentence": 8},               # overlaps the montage → dropped
           {"score": 80, "passages": [P(20, 29), P(33, 39)]}]                   # 135 s → trimmed under 90*1.15
    out = segment.sanitize_clips(raw, sents, opts, limit=10)
    assert [c["score"] for c in out] == [95, 80]
    m = out[0]
    assert m["passages"][0] == P(30, 30, "hook") and len(m["passages"]) == 3
    assert (m["start_sentence"], m["end_sentence"]) == (2, 30) and m["approx_duration"] == 70.5
    assert out[1]["approx_duration"] <= 90 * 1.15
    assert all(p["start_sentence"] <= p["end_sentence"] for p in out[1]["passages"])


def test_sanitize_single_passage_unchanged_limits():
    out = segment.sanitize_clips([{"score": 1, "start_sentence": 0, "end_sentence": 20}], _sentences(),
                                 Options(min_duration=30, max_duration=60))
    assert out[0]["approx_duration"] <= 60 * 1.15 and out[0]["passages"] == [P(0, out[0]["end_sentence"])]


def test_normalise_passages_and_old_format():
    out = segment._normalise({"clips": [
        {"title": "M", "passages": [P(9, 9, "hook"), {"start_sentence": 1, "end_sentence": 3, "role": "x"}]},
        {"title": "Old", "start_sentence": 4, "end_sentence": 6},
        {"title": "Broken"}]})
    assert [c["title"] for c in out["clips"]] == ["M", "Old"]
    assert out["clips"][0]["passages"] == [P(9, 9, "hook"), P(1, 3)]
    assert out["clips"][1]["passages"] == [P(4, 6)]
    assert "passages" in segment.CLIP_SCHEMA["required"] and "start_sentence" not in segment.CLIP_SCHEMA["required"]


def test_refine_keeps_roles_and_reads_old_cache(tmp_path):
    sents = _sentences()
    clips = segment.sanitize_clips([{"score": 9, "passages": [P(30, 30, "hook"), P(2, 5)]},
                                    {"score": 8, "start_sentence": 10, "end_sentence": 14}], sents, Options())
    (tmp_path / "refine.json").write_text(json.dumps([
        {"index": 0, "passages": [{"start_sentence": 30, "end_sentence": 31}, {"start_sentence": 3, "end_sentence": 6}],
         "reason": "r"},
        {"index": 1, "start_sentence": 11, "end_sentence": 14, "reason": "old format"}]))
    sel = pipeline.refine(pipeline.Selection(clips, {}, False), {"sentences": sents}, tmp_path, Options())
    assert sel.clips[0]["passages"] == [P(30, 31, "hook"), P(3, 6)]
    assert passages_of(sel.clips[1]) == [(11, 14)]


def test_joins_are_cuts_and_reframe_there(monkeypatch):
    monkeypatch.setattr(reframe, "analyse", lambda *a, **k: ([(0.0, 400, 300), (10.2, 1500, 300)], []))
    info = {"width": 1920, "height": 1080}
    fp = reframe.build_plan(None, 0, 20, info, [3.6, 7.5, 9.9, 14.0], Options(punch_zoom=False), joins=[10.0])
    assert [c[0] for c in fp.crops][:2] == [0.0, 10.0] and fp.crops[1][1] > fp.crops[0][1]  # new framing at the join
    assert fp.zoom_spans == []
    fp = reframe.build_plan(None, 0, 20, info, [3.6, 7.5, 9.9, 14.0], Options(), joins=[10.0])
    assert 9.9 not in [c[0] for c in fp.crops]          # no 0.1 s shot next to the splice


def test_spec_json_segments():
    assert spec_json({"start": 1, "end": 5})["segments"] == [[1, 5]]
    assert spec_json({"segments": [[50, 60], [1, 5]]})["segments"] == [[50, 60], [1, 5]]


# ---------- real FFmpeg render ----------

@pytest.fixture()
def sermon(tmp_path):
    if not shutil.which(settings.FFMPEG):
        pytest.skip("FFmpeg not available")
    src = tmp_path / "sermon.mp4"
    subprocess.run([settings.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=40",
                    "-f", "lavfi", "-i", "sine=f=440:d=40", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    words, sents = [], []
    for i in range(10):            # 10 sentences of 4 words, 4 s each
        w0 = len(words)
        for k in range(4):
            t = i * 4 + k * 0.9 + 0.2
            words.append({"w": f"mot{i}{k}" + ("." if k == 3 else ""), "s": round(t, 2), "e": round(t + 0.7, 2)})
        sents.append({"id": i, "w0": w0, "w1": len(words) - 1, "s": words[w0]["s"], "e": words[-1]["e"],
                      "text": " ".join(w["w"] for w in words[w0:])})
    info = {"duration": 40.0, "width": 640, "height": 360, "fps": 30, "has_video": True, "has_audio": True}
    return src, info, {"words": words, "sentences": sents, "language": "fr"}


def _probe_duration(path):
    out = subprocess.run([settings.FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out)


def test_render_montage_end_to_end(sermon, tmp_path):
    src, info, tr = sermon
    clip = {"index": 0, "title": "Montage", "hook_title": "Une question ?", "start_sentence": 1, "end_sentence": 8,
            "passages": [P(8, 8, "hook"), P(1, 2), P(5, 5)]}
    opts = Options(crop="center", outro=False, slides="crop")
    out = render.render_clip(src, info, tr, clip, tmp_path / "clips", opts)
    segs = out["segments"]
    assert len(segs) == 3 and segs[0][0] > segs[1][0]               # the hook (from 32 s) plays first
    assert out["start"] == segs[1][0] and out["end"] == segs[0][1]
    want = sum(e - s for s, e in segs)
    assert abs(out["duration"] - want) < 0.6
    assert abs(_probe_duration(tmp_path / "clips" / out["video"]) - out["duration"]) < 0.3
    assert out["text"].split()[0] == "mot80"                         # captions follow the play order
    assert not list((tmp_path / "clips").glob("*_montage.mkv"))      # intermediate file removed
    ass = (tmp_path / "clips" / "clip_01.ass").read_text(encoding="utf-8")
    assert "Une question ?" in ass

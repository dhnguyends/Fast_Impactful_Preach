"""B-roll: anchors, church library, Pixabay (faked), placement and a real render."""
import importlib
import json
import shutil
import subprocess

import httpx
import pytest

from fip import render, settings
from fip.models import Options


@pytest.fixture()
def br(monkeypatch, tmp_path):
    monkeypatch.setenv("FIP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIP_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    importlib.reload(settings)
    from fip import broll
    importlib.reload(broll)
    yield broll
    importlib.reload(settings)


def _make_video(path, seconds=6, size="1920x1080", color=None):
    src = f"color=c={color}:s={size}:r=30:d={seconds}" if color else f"testsrc2=s={size}:r=30:d={seconds}"
    subprocess.run([settings.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", src, "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


TR = {"words": [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in
                enumerate("Le semeur sort pour semer. La moisson, est grande ! Prions.".split())],
      "sentences": [{"id": 0, "w0": 0, "w1": 4, "s": 0.0, "e": 2.4}, {"id": 1, "w0": 5, "w1": 9, "s": 2.5, "e": 4.9},
                    {"id": 2, "w0": 10, "w1": 10, "s": 5.0, "e": 5.4}]}


def test_anchor_time(br):
    assert br.anchor_time(TR, 1, "moisson") == 3.0          # "moisson," matches without the comma
    assert br.anchor_time(TR, 0, "semer") == 2.0             # "semer." too
    assert br.anchor_time(TR, 1, "absent") == 2.5           # not found → the sentence start
    assert br.anchor_time(TR, 9, "x") is None


def test_library_add_match_update_remove(br, tmp_path):
    if not shutil.which(settings.FFMPEG):
        pytest.skip("FFmpeg not available")
    item = br.library_add(_make_video(tmp_path / "up.mp4"), "Champ de blé", ["moisson", "blé", "harvest", " "])
    assert item["keywords"] == ["blé", "harvest", "moisson"] and item["duration"] == pytest.approx(6, abs=0.2)
    assert br.library_file(item["id"]).exists() and br.library_file(item["id"], thumb=True).exists()
    assert br.library_match({"query": "wheat field harvest", "keywords": ["moisson"]})["id"] == item["id"]
    assert br.library_match({"query": "Moïsson"})["id"] == item["id"]        # accents / case ignored
    assert br.library_match({"query": "ocean waves"}) is None
    assert br.library_match({"query": "harvest"}, used={item["id"]}) is None
    br.library_update(item["id"], keywords=["mer"])
    assert br.library_match({"query": "harvest"}) is None
    br.library_remove(item["id"])
    assert br.library() == []


def _pixabay(hits, seen):
    def handler(req: httpx.Request):
        seen.append(req)
        if "pixabay.com/api/videos" in str(req.url):
            return httpx.Response(200, json={"total": len(hits), "hits": hits})
        return httpx.Response(200, content=b"\x00" * 64)   # the video download
    return handler


def _hit(i, dur=10, large_size=30_000_000):
    return {"id": i, "pageURL": f"https://pixabay.com/videos/id-{i}/", "duration": dur, "user": "Ann",
            "videos": {"large": {"url": f"https://cdn/{i}_large.mp4", "width": 3840, "height": 2160,
                                 "size": large_size, "thumbnail": f"https://cdn/{i}_large.jpg"},
                       "medium": {"url": f"https://cdn/{i}_medium.mp4", "width": 1920, "height": 1080,
                                  "size": 9_000_000, "thumbnail": f"https://cdn/{i}_medium.jpg"}}}


def test_pixabay_search_pick_cache_and_resolve(br, monkeypatch):
    seen = []
    real = httpx.Client
    monkeypatch.setattr(br.httpx, "Client", lambda *a, **k: real(transport=httpx.MockTransport(
        _pixabay([_hit(1, dur=2), _hit(2), _hit(3, large_size=200_000_000)], seen))))
    with pytest.raises(br.BrollError):
        br.stock_search("wheat", 3)                                   # no key yet
    monkeypatch.setenv("PIXABAY_API_KEY", "123-abcdef0123456789abcd")
    res = br.stock_search("wheat field", 3)
    assert [r["id"] for r in res] == [2, 3]                           # 2 s video too short for a 3 s cue
    assert res[0]["file"]["quality"] == "large" and res[1]["file"]["quality"] == "medium"  # 200 MB 4K skipped
    q = seen[0].url.params
    assert q["key"] == "123-abcdef0123456789abcd" and q["safesearch"] == "true" and q["q"] == "wheat field"
    calls = len([r for r in seen if "api/videos" in str(r.url)])
    assert calls == 2 and seen[1].url.params["q"] == "wheat"         # no tag matched: the subject alone too
    br.stock_search("wheat field", 3)
    assert len([r for r in seen if "api/videos" in str(r.url)]) == calls  # cached 24 h

    clip = {"broll": [{"sentence": 1, "word": "moisson", "query": "wheat field", "keywords": [], "seconds": 3},
                      {"sentence": 0, "word": "semeur", "query": "wheat field", "keywords": [], "seconds": 9}]}
    used = set()
    cues = br.resolve(clip, TR, used)
    assert [c["ref"] for c in cues] == ["pb:2", "pb:3"]               # never the same shot twice
    assert cues[0]["at"] == 3.0 and cues[0]["source"] == "pixabay" and cues[0]["credit"] == "Ann / Pixabay"
    assert cues[1]["seconds"] == br.MAX_SECONDS
    assert all(json.dumps(c) for c in cues) and used == {"pb:2", "pb:3"}
    # a cue resolved earlier is kept as it is (no new search, no new download)
    n = len(seen)
    assert br.resolve({"broll": cues}, TR)[0]["file"] == cues[0]["file"] and len(seen) == n


def test_resolve_never_raises(br, monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "123-abcdef0123456789abcd")
    real = httpx.Client
    monkeypatch.setattr(br.httpx, "Client", lambda *a, **k: real(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, text="API rate limit exceeded"))))
    assert br.resolve({"broll": [{"sentence": 1, "word": "moisson", "query": "x", "seconds": 3}]}, TR) == []


def test_place_broll_rules(tmp_path):
    f = tmp_path / "b.mp4"
    f.write_bytes(b"x")
    cue = lambda at, d=3: {"at": at, "seconds": d, "file": str(f)}  # noqa: E731
    placed = render.place_broll([cue(100.5), cue(104), cue(110), cue(117), cue(125), cue(130)], 100, 32,
                                fits=[(16.0, 20.0)])
    # 0.5 s: over the hook → no; 4 s ok; 10 s ok (3 s gap); 17 s: over a slide → no; 25 s ok; 30 s: too close to the end
    assert [p[0] for p in placed] == [4.0, 10.0, 25.0]
    assert render.place_broll([{"at": 105, "seconds": 3}], 100, 30, []) == []   # not resolved → skipped


def test_montage_cues_follow_the_play_order():
    cues = [{"at": 1352.0}, {"at": 1320.0}, {"at": 900.0}]
    out = render.montage_cues(cues, [(1346.5, 1357.7), (1318.2, 1343.4)], [0.0, 11.2])
    assert [c["at"] for c in out] == [5.5, 13.0]                      # 900 s is not in the montage


def test_render_with_broll_end_to_end(tmp_path):
    if not shutil.which(settings.FFMPEG):
        pytest.skip("FFmpeg not available")
    src = tmp_path / "sermon.mp4"
    subprocess.run([settings.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=20",
                    "-f", "lavfi", "-i", "sine=f=300:d=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    red = _make_video(tmp_path / "red.mp4", seconds=2, size="1280x720", color="red")   # shorter than the cue
    words = [{"w": f"m{i}.", "s": i * 1.0 + 0.1, "e": i * 1.0 + 0.8} for i in range(20)]
    tr = {"words": words, "sentences": [{"id": i, "w0": i, "w1": i, "s": w["s"], "e": w["e"], "text": w["w"]}
                                        for i, w in enumerate(words)]}
    info = {"duration": 20.0, "width": 640, "height": 360, "fps": 30, "has_video": True, "has_audio": True}
    clip = {"index": 0, "title": "T", "start_sentence": 2, "end_sentence": 17,
            "broll": [{"at": 8.1, "seconds": 3, "file": str(red), "source": "library", "credit": "Rouge"}]}
    out = render.render_clip(src, info, tr, clip, tmp_path / "clips", Options(crop="center", outro=False,
                                                                               slides="crop", punch_zoom=False))
    assert out["broll_used"] == [{"t": pytest.approx(out["broll_used"][0]["t"]), "seconds": 3.0, "source": "library",
                                  "credit": "Rouge", "url": ""}]
    t0 = out["broll_used"][0]["t"]
    mp4 = tmp_path / "clips" / out["video"]

    def pixel(t):
        raw = subprocess.run([settings.FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(mp4), "-frames:v", "1",
                              "-vf", "crop=8:8:536:300,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return tuple(raw[:3])
    r, g, b = pixel(t0 + 1.0)
    assert r > 180 and g < 80 and b < 80                             # the cutaway (red) is on screen
    r, g, b = pixel(t0 + 2.6)
    assert r > 180 and g < 80                                        # 2 s clip held on its last frame
    r, g, b = pixel(t0 + 4.0)
    assert abs(r - g) < 20                                           # back to the preacher (gray)
    assert not list((tmp_path / "clips").glob("*_broll.mkv"))

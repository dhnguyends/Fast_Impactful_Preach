"""Social publishing: state, OAuth flow and the YouTube upload against a fake Google API."""
import importlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


@pytest.fixture()
def social(monkeypatch, tmp_path):
    monkeypatch.setenv("FIP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIP_ENV_FILE", str(tmp_path / ".env"))
    for app_ in ("YOUTUBE", "META", "TIKTOK"):  # never pick up the developer's real keys
        for part in ("ID", "SECRET"):
            monkeypatch.delenv(f"FIP_{app_}_CLIENT_{part}", raising=False)
    from fip import settings
    importlib.reload(settings)
    from app import social as s
    importlib.reload(s)
    yield s
    importlib.reload(settings)


def fake_google(requests_seen, privacy_after="unlisted"):
    processing_calls = []

    def handler(req: httpx.Request):
        requests_seen.append(req)
        u = str(req.url)
        if u.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
        if "youtube/v3/channels" in u:
            return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "EEIG Genève"}}]})
        if "upload/youtube/v3/videos" in u:
            return httpx.Response(200, headers={"Location": "https://upload.example/session1"})
        if u == "https://upload.example/session1":  # chunked: 308 until the last chunk arrives
            first, rest = req.headers["Content-Range"].split(" ")[1].split("/")
            end, total = int(first.split("-")[1]), int(rest)
            if end + 1 < total:
                return httpx.Response(308, headers={"Range": f"bytes=0-{end}"})
            return httpx.Response(200, json={"id": "abc123", "status": {"privacyStatus": "unlisted"}})
        if "youtube/v3/videos" in u and "part=status" in u:  # processing check
            processing_calls.append(1)
            state = "uploaded" if len(processing_calls) < 2 else "processed"
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": state, "privacyStatus": privacy_after}}]})
        return httpx.Response(404, json={"error": {"message": "unexpected " + u}})
    return handler


def patch_httpx(monkeypatch, s, handler):
    real = httpx.Client
    monkeypatch.setattr(s.httpx, "Client", lambda *a, **k: real(transport=httpx.MockTransport(handler)))


def test_status_and_select(social):
    st = social.status()
    assert st["selected"] is None and set(st["networks"]) == {"youtube", "instagram", "facebook", "tiktok"}
    assert not st["networks"]["youtube"]["configured"]
    assert social.select("youtube")["selected"] == "youtube"


def test_meta_app_shared_by_instagram_and_facebook(social):
    social.save_app("instagram", "1234567890", "secretsecret")
    st = social.status()["networks"]
    assert st["instagram"]["configured"] and st["facebook"]["configured"] and not st["youtube"]["configured"]


def test_youtube_connect_and_upload(social, monkeypatch, tmp_path):
    social.save_app("youtube", "client-id-123.apps.googleusercontent.com", "GOCSPX-secret")
    url = social.auth_url("youtube", "https://shorts.example/api/social/youtube/callback")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["client-id-123.apps.googleusercontent.com"] and q["access_type"] == ["offline"]
    assert "youtube.upload" in q["scope"][0]

    seen = []
    patch_httpx(monkeypatch, social, fake_google(seen))
    with pytest.raises(social.PublishError):
        social.finish_oauth("youtube", "CODE", "forged-state")          # CSRF protection
    st = social.finish_oauth("youtube", "CODE", q["state"][0])
    yt = st["networks"]["youtube"]
    assert yt["connected"] and yt["account"] == "EEIG Genève" and st["selected"] == "youtube"

    video = tmp_path / "clip_01.mp4"
    video.write_bytes(b"\x00" * 2500)
    monkeypatch.setattr(social, "CHUNK", 1024)                         # force 3 chunks
    monkeypatch.setattr(social.time, "sleep", lambda s: None)
    clip = {"title": "Dieu veut ton cœur", "post_caption": "Une parole forte.", "scripture_refs": ["2 Co 9:7"],
            "hashtags": ["Bible", "#Jésus"]}
    steps = []
    res = social.publish(video, clip, "youtube", "unlisted", progress=lambda ph, f: steps.append((ph, round(f, 2))))
    assert res == {"url": "https://youtube.com/shorts/abc123", "privacy": "unlisted", "state": "online", "note": "",
                   "account": "EEIG Genève"}
    assert steps[0] == ("uploading", 0.0) and ("uploading", 0.82) in steps and steps[-1] == ("processing", 1.0)
    init = next(r for r in seen if "upload/youtube/v3/videos" in str(r.url))
    meta = json.loads(init.content)
    assert meta["status"]["privacyStatus"] == "unlisted"
    assert meta["snippet"]["title"].endswith("#Shorts")
    assert "2 Co 9:7" in meta["snippet"]["description"] and "#Bible #Jésus" in meta["snippet"]["description"]
    puts = [r for r in seen if str(r.url) == "https://upload.example/session1"]
    assert len(puts) == 3 and sum(len(r.content) for r in puts) == 2500 and init.headers["Authorization"] == "Bearer AT"
    assert puts[-1].headers["Content-Range"] == "bytes 2048-2499/2500"


def test_youtube_reports_forced_private(social, monkeypatch, tmp_path):
    social.save_app("youtube", "client-id-123.apps.googleusercontent.com", "GOCSPX-secret")
    q = parse_qs(urlparse(social.auth_url("youtube", "https://x/cb")).query)
    seen = []
    patch_httpx(monkeypatch, social, fake_google(seen, privacy_after="private"))
    monkeypatch.setattr(social.time, "sleep", lambda s: None)
    social.finish_oauth("youtube", "CODE", q["state"][0])
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 100)
    res = social.publish(video, {"title": "T"}, "youtube", "public")
    assert res["state"] == "online" and res["privacy"] == "private" and "Private" in res["note"]


def test_publish_requires_connection(social, tmp_path):
    with pytest.raises(social.PublishError):
        social.publish(tmp_path / "missing.mp4", {}, "youtube")


def fake_google_two_channels(seen):
    """Each sign-in returns a new token; the channel depends on the token (personal, then EEIG GVA)."""
    issued = []
    channels = {"AT1": ("UCme", "Nguyen Thanh"), "AT2": ("UCeeig", "EEIG GVA"), "AT3": ("UCme", "Nguyen Thanh")}

    def handler(req: httpx.Request):
        seen.append(req)
        u = str(req.url)
        if u.startswith("https://oauth2.googleapis.com/token"):
            issued.append(1)
            return httpx.Response(200, json={"access_token": f"AT{len(issued)}", "refresh_token": "RT", "expires_in": 3600})
        if "youtube/v3/channels" in u:
            cid, name = channels[req.headers["Authorization"].split()[1]]
            return httpx.Response(200, json={"items": [{"id": cid, "snippet": {"title": name}}]})
        if "upload/youtube/v3/videos" in u:
            return httpx.Response(200, headers={"Location": "https://upload.example/s"})
        if u == "https://upload.example/s":
            return httpx.Response(200, json={"id": "v1", "status": {"privacyStatus": "public"}})
        if "youtube/v3/videos" in u:
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "processed", "privacyStatus": "public"}}]})
        return httpx.Response(404, json={"error": {"message": u}})
    return handler


def _connect(social, n=1):
    for _ in range(n):
        q = parse_qs(urlparse(social.auth_url("youtube", "https://x/cb")).query)
        assert "select_account" in q["prompt"][0]          # Google shows the channel chooser every time
        social.finish_oauth("youtube", "CODE", q["state"][0])


def test_several_channels_default_eeig_gva(social, monkeypatch, tmp_path):
    social.save_app("youtube", "client-id-123.apps.googleusercontent.com", "GOCSPX-secret")
    seen = []
    patch_httpx(monkeypatch, social, fake_google_two_channels(seen))
    monkeypatch.setattr(social.time, "sleep", lambda s: None)
    _connect(social)                                        # personal channel first → active (only one)
    assert social.status()["networks"]["youtube"]["account"] == "Nguyen Thanh"
    _connect(social)                                        # EEIG GVA added → becomes active (the default)
    yt = social.status()["networks"]["youtube"]
    assert yt["account"] == "EEIG GVA" and [a["name"] for a in yt["accounts"]] == ["Nguyen Thanh", "EEIG GVA"]
    _connect(social)                                        # re-adding personal does not steal the default
    assert social.status()["networks"]["youtube"]["account"] == "EEIG GVA"
    assert len(social.status()["networks"]["youtube"]["accounts"]) == 2

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 10)
    res = social.publish(video, {"title": "T"}, "youtube")
    assert res["account"] == "EEIG GVA"
    assert next(r for r in seen if "upload/youtube" in str(r.url)).headers["Authorization"] == "Bearer AT2"

    social.set_active("youtube", "UCme")                    # switch to the personal channel
    assert social.publish(video, {"title": "T"}, "youtube")["account"] == "Nguyen Thanh"
    with pytest.raises(social.PublishError):
        social.set_active("youtube", "UCunknown")

    social.disconnect("youtube", "UCeeig")                  # remove one channel only
    yt = social.status()["networks"]["youtube"]
    assert yt["connected"] and [a["name"] for a in yt["accounts"]] == ["Nguyen Thanh"]


def test_old_single_account_state_is_migrated(social):
    (social._dir() / "state.json").write_text(json.dumps({
        "selected": "youtube", "tokens": {"youtube": {"access_token": "x", "account": "EEIG GVA", "channel_id": "UCeeig"}}}))
    yt = social.status()["networks"]["youtube"]
    assert yt["connected"] and yt["account"] == "EEIG GVA" and yt["accounts"][0]["id"] == "UCeeig"


def test_caption_uses_publish_title_and_keeps_shorts_tag(social):
    clip = {"title": "La dîme", "publish_title": "La dîme : obligation ou joie ? 🙏 #foi #bible",
            "post_caption": "Une parole.", "hashtags": ["foi"]}
    title, body = social.caption(clip, "youtube")
    assert title == "La dîme : obligation ou joie ? 🙏 #foi #bible #Shorts"
    assert social.caption(clip, "instagram")[0] == "La dîme : obligation ou joie ? 🙏 #foi #bible"
    assert social.caption({"title": "Old project"}, "youtube")[0] == "Old project #Shorts"


def test_youtube_upload_limit_gives_clear_message(social, monkeypatch, tmp_path):
    social.save_app("youtube", "client-id-123.apps.googleusercontent.com", "GOCSPX-secret")
    q = parse_qs(urlparse(social.auth_url("youtube", "https://x/cb")).query)
    seen = []
    base = fake_google(seen)

    def handler(req):
        if "upload/youtube/v3/videos" in str(req.url):
            seen.append(req)
            return httpx.Response(400, json={"error": {"code": 400, "message":
                "The user has exceeded the number of videos they may upload.",
                "errors": [{"reason": "uploadLimitExceeded", "domain": "youtube.video"}]}})
        return base(req)
    patch_httpx(monkeypatch, social, handler)
    social.finish_oauth("youtube", "CODE", q["state"][0])
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 10)
    with pytest.raises(social.LimitError) as e:
        social.publish(video, {"title": "T"}, "youtube")
    assert e.value.reason == "uploadLimitExceeded" and "24 hours" in str(e.value) and "youtube.com/verify" in str(e.value)


def test_batch_stops_after_a_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("FIP_DATA_DIR", str(tmp_path))
    from fip import settings
    importlib.reload(settings)
    from app import jobs, social as s
    importlib.reload(jobs)
    p = jobs.create_project("T", "upload", {})
    jobs.update(p["id"], clips=[{"index": i, "video": f"clip_0{i + 1}.mp4", "title": "T"} for i in range(3)])
    calls = []

    def fake_publish(video, clip, net, privacy, progress=None):
        calls.append(clip["index"])
        raise s.LimitError("YouTube's daily upload limit for this channel is reached.", reason="uploadLimitExceeded")
    monkeypatch.setattr(s, "publish", fake_publish)
    for i in range(3):
        jobs.publish_one(p["id"], i, "youtube", "public")
    clips = jobs.load(p["id"])["clips"]
    assert calls == [0]                                              # YouTube was asked only once
    assert all(c["publish"]["youtube"]["status"] == "error" and "daily upload limit" in c["publish"]["youtube"]["error"]
               for c in clips)
    importlib.reload(settings)

"""Login and server-mode behaviour of the web app (no GPU / network needed)."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FIP_APP_PASSWORD", "test-pass-123")
    monkeypatch.setenv("FIP_SERVER_MODE", "1")
    monkeypatch.setenv("FIP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIP_ENV_FILE", str(tmp_path / ".env"))
    from fip import settings
    importlib.reload(settings)
    from app import auth, jobs, main
    for m in (auth, jobs, main):
        importlib.reload(m)
    monkeypatch.setattr(jobs, "start_worker", lambda: None)
    monkeypatch.setattr(auth.time, "sleep", lambda s: None)
    with TestClient(main.app, base_url="https://testserver") as c:
        yield c
    importlib.reload(settings)


def test_pages_and_api_require_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/login.html"
    assert client.get("/api/projects").status_code == 401
    assert client.get("/media/x/clips/clip_01.mp4").status_code == 401
    assert client.get("/login.html").status_code == 200


def test_wrong_then_right_password(client):
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    r = client.post("/api/login", json={"password": "test-pass-123"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert client.get("/api/projects").status_code == 200
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/projects").status_code == 401


def test_rate_limit(client):
    for _ in range(8):
        client.post("/api/login", json={"password": "bad"})
    assert client.post("/api/login", json={"password": "test-pass-123"}).status_code == 429


def test_forged_cookie_rejected(client):
    client.cookies.set("fip_session", "9999999999.deadbeef")
    assert client.get("/api/projects").status_code == 401


def test_server_mode_blocks_local_files(client):
    client.post("/api/login", json={"password": "test-pass-123"})
    r = client.post("/api/projects", json={"kind": "local", "path": "C:/Windows/win.ini"})
    assert r.status_code == 403
    h = client.get("/api/health").json()
    assert h["server_mode"] and h["auth"]
    assert client.get("/docs").status_code in (401, 404)


def test_public_pages_need_no_login(client):
    for path, must in (("/about", "Team sign-in"), ("/privacy", "youtube.upload"), ("/terms", "YouTube Terms of Service")):
        r = client.get(path)
        assert r.status_code == 200 and must in r.text
    priv = client.get("/privacy").text
    assert "myaccount.google.com/permissions" in priv and "Limited Use" in priv   # Google's required disclosures
    assert client.get("/api/projects").status_code == 401                        # everything else still locked


def test_logo_upload_and_reset(client):
    import io
    from PIL import Image
    client.post("/api/login", json={"password": "test-pass-123"})
    assert client.put("/api/brand/logo", content=b"not an image").status_code == 400
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "red").save(buf, format="PNG")
    r = client.put("/api/brand/logo", content=buf.getvalue())
    assert r.status_code == 200 and r.json()["size"] == [200, 200]
    assert client.get("/api/brand/logo").status_code == 200
    assert client.delete("/api/brand/logo").json()["ok"]


def test_adjust_accepts_hook_title(client):
    from pydantic import ValidationError
    from app.main import Adjust
    assert Adjust(start_sentence=1, end_sentence=4).hook_title is None          # untouched → keep Claude's
    assert Adjust(start_sentence=1, end_sentence=4, hook_title="").hook_title == ""
    with pytest.raises(ValidationError):
        Adjust(start_sentence=1, end_sentence=4, hook_title="x" * 81)


def test_pixabay_key_and_broll_library(client, monkeypatch, tmp_path):
    import shutil
    import subprocess
    from fip import settings
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    client.post("/api/login", json={"password": "test-pass-123"})
    assert client.post("/api/settings", json={"pixabay_key": "not a key"}).status_code == 400
    r = client.post("/api/settings", json={"pixabay_key": "1234567-0123456789abcdef0123"})
    assert r.status_code == 200 and r.json()["pixabay_masked"] == "1234…0123"
    assert "0123456789abcdef" not in r.text                         # never sent back in full
    lib = client.get("/api/broll/library").json()
    assert lib == {"items": [], "stock": True}
    assert client.put("/api/broll/library?filename=x.exe", content=b"x").status_code == 400
    if not shutil.which(settings.FFMPEG):
        return
    vid = tmp_path / "c.mp4"
    subprocess.run([settings.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(vid)], check=True)
    r = client.put("/api/broll/library?filename=Mer.mp4&name=Mer&keywords=mer,%20sea", content=vid.read_bytes())
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["keywords"] == ["mer", "sea"] and client.get(item["thumb"]).status_code == 200
    assert client.patch(f"/api/broll/library/{item['id']}", json={"keywords": ["ocean"]}).json()["keywords"] == ["ocean"]
    assert client.get("/api/broll/library/../../etc/video").status_code == 404
    assert client.delete(f"/api/broll/library/{item['id']}").json()["ok"]
    assert client.get("/api/broll/library").json()["items"] == []


def test_youtube_bot_check_gives_a_clear_message(client, monkeypatch, tmp_path):
    import sys
    import types
    from app import jobs
    from fip.errors import FipError

    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download):
            raise Exception("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser")
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    with pytest.raises(FipError) as e:
        jobs._download("p1", "https://youtu.be/x", tmp_path)
    assert "upload the video file instead" in str(e.value) and "YouTube Studio" in str(e.value)


def test_public_pages_in_english_and_french(client):
    en = client.get("/privacy")
    assert en.status_code == 200 and "Privacy policy" in en.text and 'lang="en"' in en.text
    assert '<a href="?lang=fr"' in en.text and "{{" not in en.text
    fr = client.get("/privacy?lang=fr")
    assert "Politique de confidentialité" in fr.text and 'lang="fr"' in fr.text and "{{" not in fr.text
    assert fr.cookies.get("fip_lang") == "fr"
    assert "Accueil" in client.get("/about").text                     # the choice is remembered (cookie)
    client.cookies.clear()
    assert "Conditions d'utilisation" in client.get("/terms", headers={"Accept-Language": "fr-CH,fr;q=0.9"}).text
    assert "Terms of use" in client.get("/terms", headers={"Accept-Language": "en-GB"}).text
    assert client.get("/i18n.js").status_code == 200                 # the login page needs it before sign-in

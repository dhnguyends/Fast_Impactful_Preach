"""FastAPI server: REST API + static single-page app (Vizard-style UI). Bound to 127.0.0.1."""
import logging
import os
import re
import time
from typing import Literal
from urllib.parse import quote

import httpx
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from fip import __version__, broll, settings, transcribe
from fip.errors import FipError
from fip.models import MAX_CLIPS, MAX_PASSAGES, SHORT_VIDEO_MAX, Options

from . import auth, jobs, public_pages, social

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac",
             ".flac", ".ogg", ".wma", ".wmv", ".mpg", ".mpeg", ".ts"}


@asynccontextmanager
async def lifespan(_app):
    jobs.start_worker()
    yield


app = FastAPI(title="FastImpact Preach", version=__version__, lifespan=lifespan,
              docs_url=None if settings.SERVER_MODE else "/docs", redoc_url=None, openapi_url=None)
app.middleware("http")(auth.middleware)


class Login(BaseModel):
    password: str


@app.post("/api/login")
def login(body: Login, request: Request):
    if not auth.enabled():
        return {"ok": True}
    ok = auth.check_password(request, body.password)
    if ok is None:
        raise HTTPException(429, "Too many attempts. Try again in 10 minutes.")
    if not ok:
        raise HTTPException(401, "Wrong password.")
    resp = JSONResponse({"ok": True})
    auth.set_cookie(resp, request)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


class NewProject(BaseModel):
    kind: str                 # upload | url | local
    title: str | None = None
    url: str | None = None
    path: str | None = None
    options: dict = {}


class KeySettings(BaseModel):
    api_key: str | None = None        # None = keep, "" = remove
    workspace_id: str | None = None
    pixabay_key: str | None = None    # B-roll stock videos


class RangeChoice(BaseModel):
    mode: str                         # auto | manual
    start: str | None = None
    end: str | None = None


class Passage(BaseModel):
    start_sentence: int
    end_sentence: int
    role: Literal["hook", "body"] = "body"


class BrollCue(BaseModel):
    sentence: int
    word: str = Field("", max_length=40)
    query: str = Field("", max_length=100)
    keywords: list[str] = []
    seconds: float = Field(3, ge=1.5, le=4.5)
    source: str = Field("auto", pattern=r"^(auto|pixabay|library:[0-9a-f]{10})$")
    variant: int = Field(0, ge=0, le=50)


class Adjust(BaseModel):
    start_sentence: int
    end_sentence: int
    passages: list[Passage] | None = Field(None, max_length=MAX_PASSAGES)  # montage, in play order
    slides: Literal["auto", "crop", "whole"] = "auto"   # per-short override of the slide layout
    hook_title: str | None = Field(None, max_length=80)  # on-screen hook title ("" = none for this short)
    broll: list[BrollCue] | None = Field(None, max_length=broll.MAX_CUES)  # None = keep


class Prepare(BaseModel):
    num_clips: int


def _get(pid: str) -> dict:
    if not re.fullmatch(r"[\w\-]+", pid) or not jobs.exists(pid):
        raise HTTPException(404, "Project not found")
    return jobs.load(pid)


def _idle(p: dict):
    if p["status"] in ("running", "queued"):
        raise HTTPException(409, "Please wait until processing finishes.")


def _validated(opts: dict) -> dict:
    try:
        return Options(**(opts or {})).model_dump()
    except ValidationError as e:
        raise HTTPException(400, f"Invalid option: {e.errors()[0]['loc'][0]} — {e.errors()[0]['msg']}")


# ---------- settings ----------

# ---------- public pages (Google OAuth verification: home page, privacy policy, terms) ----------

def _public(render, request: Request, lang: str | None) -> HTMLResponse:
    chosen = public_pages.pick_lang(lang, request.cookies.get(public_pages.LANG_COOKIE),
                                    request.headers.get("accept-language"))
    resp = HTMLResponse(render(chosen))
    if lang in public_pages.LANGS:   # an explicit choice is remembered (same cookie as the app's switch)
        resp.set_cookie(public_pages.LANG_COOKIE, lang, max_age=365 * 86400, samesite="lax")
    return resp


@app.get("/about", response_class=HTMLResponse)
def page_about(request: Request, lang: str | None = None):
    return _public(public_pages.about, request, lang)


@app.get("/privacy", response_class=HTMLResponse)
def page_privacy(request: Request, lang: str | None = None):
    return _public(public_pages.privacy, request, lang)


@app.get("/terms", response_class=HTMLResponse)
def page_terms(request: Request, lang: str | None = None):
    return _public(public_pages.terms, request, lang)


@app.get("/api/health")
def health():
    settings.reload_env()
    return {"version": __version__, "api_key": settings.api_key_configured(), "model": settings.claude_model(),
            "max_clips": MAX_CLIPS, "short_video_max": SHORT_VIDEO_MAX, "whisper": settings.WHISPER_MODEL,
            "defaults": Options().model_dump(), "server_mode": settings.SERVER_MODE, "auth": auth.enabled()}


def _key_status() -> dict:
    return {"configured": settings.api_key_configured(), "key_masked": settings.mask(settings.api_key()),
            "workspace_masked": settings.mask(settings.workspace_id()), "model": settings.claude_model(),
            "pixabay_masked": settings.mask(broll.stock_key())}


# ---------- end-card logo ----------

@app.get("/api/brand/logo")
def brand_logo_get():
    logo = settings.brand_logo()
    if not logo:
        raise HTTPException(404, "No logo")
    return FileResponse(logo, headers={"Cache-Control": "no-store"})


@app.put("/api/brand/logo")
async def brand_logo_put(request: Request):
    """Upload a new end-card logo (raw image body: PNG / JPG / WEBP, ≤ 5 MB). Stored as PNG."""
    import io
    from PIL import Image, UnidentifiedImageError
    data = await request.body()
    if not data or len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Please choose an image of at most 5 MB.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if min(im.size) < 64:
                raise HTTPException(400, "This image is too small (at least 64×64 pixels).")
            im = im.convert("RGBA")
            dst = settings.DATA_DIR / "brand" / "logo.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst)
    except UnidentifiedImageError:
        raise HTTPException(400, "This file is not an image (use PNG or JPG).")
    return {"ok": True, "size": list(im.size)}


@app.delete("/api/brand/logo")
def brand_logo_reset():
    (settings.DATA_DIR / "brand" / "logo.png").unlink(missing_ok=True)
    return {"ok": True, "default": bool(settings.brand_logo())}


@app.get("/api/settings")
def get_settings():
    settings.reload_env()
    return _key_status()


@app.post("/api/settings")
def set_settings(body: KeySettings):
    values = {}
    if body.api_key is not None:
        key = body.api_key.strip()
        if key and not re.fullmatch(r"sk-ant-[\w\-]{20,}", key):
            raise HTTPException(400, "That doesn't look like a Claude API key (it starts with sk-ant-).")
        values["ANTHROPIC_API_KEY"] = key
    if body.workspace_id is not None:
        ws = body.workspace_id.strip()
        if ws and not re.fullmatch(r"[\w\-]{4,}", ws):
            raise HTTPException(400, "Invalid workspace ID.")
        values["ANTHROPIC_WORKSPACE_ID"] = ws
    if body.pixabay_key is not None:
        pk = body.pixabay_key.strip()
        if pk and not re.fullmatch(r"\d+-[0-9a-fA-F]{16,}", pk):
            raise HTTPException(400, "That doesn't look like a Pixabay API key (digits, a dash, then letters/digits).")
        values["PIXABAY_API_KEY"] = pk
    if values:
        settings.save_env(values)
    return _key_status()


# ---------- B-roll library (the church's own cutaway clips) ----------

def _lib_item(i: dict) -> dict:
    return {**i, "thumb": f"/api/broll/library/{i['id']}/thumb", "video": f"/api/broll/library/{i['id']}/video"}


class LibraryEdit(BaseModel):
    name: str | None = Field(None, max_length=80)
    keywords: list[str] | None = None


@app.get("/api/broll/library")
def broll_library():
    return {"items": [_lib_item(i) for i in broll.library()], "stock": bool(broll.stock_key())}


@app.put("/api/broll/library")
async def broll_library_add(request: Request, filename: str = "clip.mp4", name: str = "", keywords: str = ""):
    """Raw streamed upload of one clip (≤ 300 MB) with its name and comma-separated keywords."""
    ext = Path(filename).suffix.lower()
    if ext not in broll.LIBRARY_EXT:
        raise HTTPException(400, f"Unsupported video type: {ext or filename}")
    tmp = settings.CACHE_DIR / f"broll_upload_{os.getpid()}_{id(request)}{ext}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 300 * 1024 * 1024:
                    raise HTTPException(413, "B-roll clips must be at most 300 MB.")
                f.write(chunk)
        item = broll.library_add(tmp, name or Path(filename).stem, keywords.split(","))
    except (broll.BrollError, FipError) as e:
        raise HTTPException(400, str(e))
    finally:
        tmp.unlink(missing_ok=True)
    return _lib_item(item)


@app.patch("/api/broll/library/{iid}")
def broll_library_edit(iid: str, body: LibraryEdit):
    try:
        return _lib_item(broll.library_update(iid, body.name, body.keywords))
    except broll.BrollError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/broll/library/{iid}")
def broll_library_delete(iid: str):
    broll.library_remove(iid)
    return {"ok": True}


@app.get("/api/broll/library/{iid}/{what}")
def broll_library_file(iid: str, what: Literal["thumb", "video"]):
    f = broll.library_file(iid, thumb=what == "thumb") if re.fullmatch(r"[0-9a-f]{10}", iid) else None
    if not f:
        raise HTTPException(404, "Not found")
    return FileResponse(f)


# ---------- projects ----------

@app.get("/api/projects")
def projects():
    return jobs.list_projects()


@app.post("/api/projects")
def new_project(body: NewProject):
    opts = _validated(body.options)
    if body.kind == "url":
        url = (body.url or "").strip()
        if not re.match(r"^https?://", url):
            raise HTTPException(400, "Please paste a valid http(s) link.")
        p = jobs.create_project(body.title or url, "url", opts)
        jobs.update(p["id"], source_url=url)
        jobs.enqueue(p["id"])
    elif body.kind == "local":
        if settings.SERVER_MODE:
            raise HTTPException(403, "Reading files from the server is disabled. Upload the video instead.")
        path = Path((body.path or "").strip().strip('"'))
        if not path.is_file():
            raise HTTPException(400, f"File not found: {path}")
        if path.suffix.lower() not in MEDIA_EXT:
            raise HTTPException(400, f"Unsupported file type: {path.suffix}")
        p = jobs.create_project(body.title or path.stem, "local", opts)
        jobs.update(p["id"], source=str(path))
        jobs.enqueue(p["id"])
    elif body.kind == "upload":
        p = jobs.create_project(body.title or "Untitled sermon", "upload", opts)
    else:
        raise HTTPException(400, "Unknown project kind")
    return jobs.load(p["id"])


@app.put("/api/projects/{pid}/upload")
async def upload(pid: str, request: Request, filename: str = "video.mp4"):
    """Raw streamed upload (no multipart buffering — fine for multi-GB sermons)."""
    _get(pid)
    ext = Path(filename).suffix.lower()
    if ext not in MEDIA_EXT:
        raise HTTPException(400, f"Unsupported file type: {ext}")
    dst = jobs.pdir(pid) / f"source{ext}"
    try:
        with open(dst, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
    except OSError as e:
        raise HTTPException(507, f"Could not save the upload: {e}")
    jobs.update(pid, source=str(dst))
    jobs.enqueue(pid)
    return jobs.load(pid)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _get(pid)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _get(pid)
    jobs.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/cancel")
def cancel(pid: str):
    _get(pid)
    jobs.cancel(pid)
    return jobs.load(pid)


@app.post("/api/projects/{pid}/retry")
def retry(pid: str):
    _idle(_get(pid))
    jobs.enqueue(pid)
    return jobs.load(pid)


@app.post("/api/projects/{pid}/prepare")
def prepare(pid: str, body: Prepare):
    _idle(_get(pid))
    if not (jobs.pdir(pid) / "transcript.json").exists():
        raise HTTPException(409, "The video is not transcribed yet.")
    if not 1 <= body.num_clips <= MAX_CLIPS:
        raise HTTPException(400, f"Number of shorts must be between 1 and {MAX_CLIPS}.")
    return jobs.prepare_shorts(pid, body.num_clips)


@app.post("/api/projects/{pid}/range")
def choose_range(pid: str, body: RangeChoice):
    _idle(_get(pid))
    if body.mode not in ("auto", "manual"):
        raise HTTPException(400, "Unknown mode")
    try:
        return jobs.set_range(pid, body.mode, body.start, body.end)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects/{pid}/sentences")
def sentences(pid: str, lo: int = 0, hi: int = 10**9):
    _get(pid)
    try:
        tr = transcribe.load(jobs.pdir(pid) / "transcript.json")
    except FipError:
        raise HTTPException(404, "No transcript yet")
    return [{k: s[k] for k in ("id", "s", "e", "text")} for s in tr["sentences"][max(0, lo):hi + 1]]


@app.post("/api/projects/{pid}/clips/{index}/adjust")
def adjust(pid: str, index: int, body: Adjust):
    p = _get(pid)
    _idle(p)
    ps = body.passages or [Passage(start_sentence=body.start_sentence, end_sentence=body.end_sentence)]
    if any(x.end_sentence < x.start_sentence or x.start_sentence < 0 for x in ps):
        raise HTTPException(400, "End must come after start.")
    if any(not (x.end_sentence < y.start_sentence or x.start_sentence > y.end_sentence)
           for i, x in enumerate(ps) for y in ps[i + 1:]):
        raise HTTPException(400, "Two passages overlap.")
    if not any(c["index"] == index for c in p["clips"]):
        raise HTTPException(404, "Clip not found")
    fields = {"start_sentence": min(x.start_sentence for x in ps), "end_sentence": max(x.end_sentence for x in ps),
              "passages": [x.model_dump() | ({"role": "body"} if len(ps) == 1 else {}) for x in ps],
              "slides": body.slides}
    if body.hook_title is not None:
        fields["hook_title"] = " ".join(body.hook_title.split())
    if body.broll is not None:  # keep what was already found for unchanged cues, re-resolve the others
        old = next(c for c in p["clips"] if c["index"] == index).get("broll") or []
        sig = lambda b: (b.get("sentence"), b.get("word"), b.get("query"), b.get("source", "auto"),  # noqa: E731
                         b.get("variant", 0))
        known = {sig(b): b for b in old}
        fields["broll"] = [dict(known.get(sig(b.model_dump()), {}), **b.model_dump())
                           if sig(b.model_dump()) in known else b.model_dump() for b in body.broll]
    jobs.set_clip(pid, index, **fields)
    jobs.enqueue_render(pid, index)  # same queue as the pipeline: never two GPU renders at once
    return jobs.load(pid)


# ---------- social publishing ----------

Network = Literal["youtube", "instagram", "facebook", "tiktok"]


class SocialSelect(BaseModel):
    network: Network | None


class SocialApp(BaseModel):
    client_id: str
    client_secret: str


class PublishBody(BaseModel):
    network: Network
    clips: list[int]
    privacy: Literal["public", "unlisted", "private"] = "public"


def _redirect_uri(request: Request, net: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/api/social/{net}/callback"


@app.get("/api/social")
def social_status(request: Request):
    s = social.status()
    for n, v in s["networks"].items():
        v["redirect_uri"] = _redirect_uri(request, n)
    return s


@app.post("/api/social/select")
def social_select(body: SocialSelect):
    return social.select(body.network)


@app.post("/api/social/{net}/app")
def social_app(net: Network, body: SocialApp):
    if not re.fullmatch(r"[\w.\-]{6,200}", body.client_id.strip()) or not re.fullmatch(r"[\w.\-]{6,200}", body.client_secret.strip()):
        raise HTTPException(400, "Please paste the app's client ID and client secret exactly as shown.")
    return social.save_app(net, body.client_id, body.client_secret)


@app.get("/api/social/{net}/connect")
def social_connect(net: Network, request: Request):
    from fastapi.responses import RedirectResponse
    try:
        return RedirectResponse(social.auth_url(net, _redirect_uri(request, net)))
    except social.PublishError as e:
        return RedirectResponse(f"/#/social?error={quote(str(e))}")


@app.get("/api/social/{net}/callback")
def social_callback(net: Network, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    from fastapi.responses import RedirectResponse
    if error or not code:
        return RedirectResponse(f"/#/social?error={quote(error_description or error or 'Connection cancelled.')}")
    try:
        social.finish_oauth(net, code, state)
    except social.PublishError as e:
        return RedirectResponse(f"/#/social?error={quote(str(e))}")
    except httpx.HTTPError:
        return RedirectResponse(f"/#/social?error={quote('Could not reach the network. Try again.')}")
    return RedirectResponse(f"/#/social?connected={net}")


class SocialAccount(BaseModel):
    id: str | None = None


@app.post("/api/social/{net}/account")
def social_account(net: Network, body: SocialAccount):
    """Choose which connected channel / page / account receives the uploads."""
    try:
        return social.set_active(net, body.id or "")
    except social.PublishError as e:
        raise HTTPException(404, str(e))


@app.post("/api/social/{net}/disconnect")
def social_disconnect(net: Network, body: SocialAccount | None = None):
    """Disconnect one account (body.id) or every account of the network."""
    return social.disconnect(net, body.id if body else None)


@app.post("/api/projects/{pid}/publish")
def publish(pid: str, body: PublishBody):
    p = _get(pid)
    st = social.status()["networks"][body.network]
    if not st["connected"]:
        raise HTTPException(409, f"Connect {st['label']} first.")
    ready = {c["index"] for c in p["clips"] if c.get("status") == "done"}
    chosen = [i for i in dict.fromkeys(body.clips) if i in ready]
    if not chosen:
        raise HTTPException(400, "Select at least one finished short.")
    batch = int(time.time() * 1000)  # groups this "Go Ahead" for the progress panel
    for i in chosen:
        jobs.enqueue_publish(pid, i, body.network, body.privacy, batch)
    return {"batch": batch, "network": body.network, "clips": chosen}


@app.get("/api/projects/{pid}/source")
def source(pid: str):
    """Stream the original video (range requests supported) for the start/end picker."""
    f = Path(_get(pid).get("source") or "")
    if settings.SERVER_MODE and jobs.pdir(pid).resolve() not in f.resolve().parents:
        raise HTTPException(404, "Source not available")
    if not f.is_file():
        raise HTTPException(404, "Source not available")
    return FileResponse(f)


@app.get("/media/{pid}/{name:path}")
def media(pid: str, name: str, download: int = 0):
    p = _get(pid)
    base = jobs.pdir(pid).resolve()
    f = (base / name).resolve()
    if name == "clips.json" and not f.exists() and p.get("clips"):  # projects made before v2
        from fip.pipeline import export_json
        export_json(p["clips"], p.get("analysis") or {}, f)
    if base not in f.parents or not f.is_file():
        raise HTTPException(404)
    if download:
        stem = re.sub(r"[^\w\- ]", "", p.get("title") or "clip")[:40].strip() or "clip"
        return FileResponse(f, filename=f"{stem} - {f.name}")
    return FileResponse(f)


class _Static(StaticFiles):
    """The page itself is always re-checked (a deploy shows up at once); app.js/css are versioned (?v=)."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path in ("", ".", "index.html", "login.html"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", _Static(directory=settings.STATIC_DIR, html=True), name="static")

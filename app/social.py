"""Social publishing: OAuth connection + upload of rendered shorts.

Networks: YouTube (Google OAuth, YouTube Data API v3), Instagram & Facebook (one Meta app,
Graph API Reels), TikTok (Login Kit + Content Posting API). Each needs a developer app created by
the user; its client id/secret are stored in the server .env, OAuth tokens in DATA_DIR/social.
"""
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from fip import settings
from fip.errors import FipError

log = logging.getLogger(__name__)

NETWORKS = ("youtube", "instagram", "facebook", "tiktok")
LABELS = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook", "tiktok": "TikTok"}
GRAPH = "https://graph.facebook.com/v21.0"

# Which developer app each network uses (Instagram and Facebook share one Meta app).
APP = {"youtube": "YOUTUBE", "instagram": "META", "facebook": "META", "tiktok": "TIKTOK"}
OAUTH = {
    "youtube": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        "extra": {"access_type": "offline", "prompt": "select_account consent", "include_granted_scopes": "true"},
    },
    "instagram": {
        "auth": "https://www.facebook.com/v21.0/dialog/oauth",
        "token": f"{GRAPH}/oauth/access_token",
        "scope": "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement,business_management",
        "extra": {},
    },
    "facebook": {
        "auth": "https://www.facebook.com/v21.0/dialog/oauth",
        "token": f"{GRAPH}/oauth/access_token",
        "scope": "pages_show_list,pages_read_engagement,pages_manage_posts,publish_video",
        "extra": {},
    },
    "tiktok": {
        "auth": "https://www.tiktok.com/v2/auth/authorize/",
        "token": "https://open.tiktokapis.com/v2/oauth/token/",
        "scope": "user.info.basic,video.publish",
        "extra": {},
    },
}
SETUP = {
    "youtube": "Google Cloud Console → new project → enable “YouTube Data API v3” → OAuth consent screen "
               "(External, add yourself as test user) → Credentials → OAuth client ID → Web application → "
               "add the redirect URI below.",
    "instagram": "developers.facebook.com → My Apps → Create app (Business) → add “Instagram” and "
                 "“Facebook Login for Business” → Valid OAuth Redirect URIs: the URI below. The Instagram "
                 "account must be a Professional account linked to a Facebook Page.",
    "facebook": "developers.facebook.com → My Apps → Create app (Business) → add “Facebook Login for "
                "Business” → Valid OAuth Redirect URIs: the URI below. You need admin rights on the Page.",
    "tiktok": "developers.tiktok.com → Manage apps → Create app → add “Login Kit” and “Content Posting "
              "API” → Redirect URI: the URI below. Until TikTok audits the app, posts are private (only you).",
}


# YouTube / Google API limits: retrying the next short of the batch cannot work, so the batch stops.
LIMITS = {
    "uploadLimitExceeded": "YouTube's daily upload limit for this channel is reached. Wait about 24 hours, then publish "
                           "again. To raise the limit, verify the channel by phone at youtube.com/verify.",
    "quotaExceeded": "The app's YouTube API quota is used up for today (it resets at midnight Pacific time, 09:00 in "
                     "Geneva). Publish again after that.",
    "dailyLimitExceeded": "The app's YouTube API quota is used up for today (it resets at midnight Pacific time, 09:00 in "
                          "Geneva). Publish again after that.",
    "rateLimitExceeded": "YouTube is receiving too many requests from the app. Wait a few minutes, then publish again.",
}


class PublishError(FipError):
    pass


class LimitError(PublishError):
    """A quota / upload limit of the network: the following uploads would fail the same way."""
    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


# ---------- storage ----------

def _dir() -> Path:
    d = settings.DATA_DIR / "social"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Preferred account per network when several are connected (YouTube: the church channel).
DEFAULT_ACCOUNT = {"youtube": os.getenv("FIP_DEFAULT_YOUTUBE_ACCOUNT", "EEIG GVA")}


def _load() -> dict:
    f = _dir() / "state.json"
    try:
        st = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    st.setdefault("selected", None)
    st.setdefault("pending", {})
    st.setdefault("accounts", {})   # {net: {account_id: token + account info}}
    st.setdefault("active", {})     # {net: account_id chosen by the user}
    for net, tok in (st.pop("tokens", None) or {}).items():  # migrate the single-account format
        if tok:
            st["accounts"].setdefault(net, {})[_acc_id(net, tok)] = tok
    return st


def _acc_id(net: str, tok: dict) -> str:
    return str(tok.get("channel_id") or tok.get("page_id") or tok.get("ig_user_id") or tok.get("open_id") or "default")


def _norm(name: str) -> str:
    return "".join(name.lower().split()).lstrip("@")


def _active_id(st: dict, net: str) -> str | None:
    accs = st["accounts"].get(net) or {}
    if not accs:
        return None
    chosen = st["active"].get(net)
    if chosen in accs:
        return chosen
    default = _norm(DEFAULT_ACCOUNT.get(net, ""))
    for aid, tok in accs.items():
        if default and _norm(tok.get("account", "")) == default:
            return aid
    return next(iter(accs))


def _save(state: dict) -> None:
    f = _dir() / "state.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(f)


def client(net: str) -> tuple[str, str]:
    settings.reload_env()
    app = APP[net]
    return (os.getenv(f"FIP_{app}_CLIENT_ID", "").strip(), os.getenv(f"FIP_{app}_CLIENT_SECRET", "").strip())


def configured(net: str) -> bool:
    return all(client(net))


def status() -> dict:
    st = _load()
    out = {"selected": st.get("selected"), "networks": {}}
    for n in NETWORKS:
        accs = st["accounts"].get(n) or {}
        active = _active_id(st, n)
        out["networks"][n] = {
            "label": LABELS[n], "configured": configured(n), "connected": bool(accs),
            "account": accs[active].get("account", "") if active else "",
            "accounts": [{"id": aid, "name": tok.get("account", aid), "active": aid == active} for aid, tok in accs.items()],
            "default_account": DEFAULT_ACCOUNT.get(n, ""),
            "setup": SETUP[n], "client_id": settings.mask(client(n)[0]), "app": APP[n]}
    return out


def set_active(net: str, account_id: str) -> dict:
    st = _load()
    if account_id not in (st["accounts"].get(net) or {}):
        raise PublishError("This account is not connected.")
    st["active"][net] = account_id
    _save(st)
    return status()


def select(net: str | None) -> dict:
    st = _load()
    st["selected"] = net
    _save(st)
    return status()


def save_app(net: str, client_id: str, client_secret: str) -> dict:
    app = APP[net]
    settings.save_env({f"FIP_{app}_CLIENT_ID": client_id.strip(), f"FIP_{app}_CLIENT_SECRET": client_secret.strip()})
    return status()


def disconnect(net: str, account_id: str | None = None) -> dict:
    """Forget one connected account, or all accounts of the network."""
    st = _load()
    if account_id:
        (st["accounts"].get(net) or {}).pop(account_id, None)
        if st["active"].get(net) == account_id:
            st["active"].pop(net)
    else:
        st["accounts"].pop(net, None)
        st["active"].pop(net, None)
    _save(st)
    return status()


# ---------- OAuth ----------

def auth_url(net: str, redirect_uri: str) -> str:
    cid, _ = client(net)
    if not cid:
        raise PublishError(f"{LABELS[net]} is not set up yet: add the developer app keys first.")
    st = _load()
    state = secrets.token_urlsafe(24)
    st.setdefault("pending", {})[state] = {"net": net, "at": time.time(), "redirect": redirect_uri}
    st["pending"] = {k: v for k, v in st["pending"].items() if time.time() - v["at"] < 900}
    _save(st)
    o = OAUTH[net]
    params = {"redirect_uri": redirect_uri, "response_type": "code", "scope": o["scope"], "state": state, **o["extra"]}
    params["client_key" if net == "tiktok" else "client_id"] = cid
    return f"{o['auth']}?{urlencode(params)}"


def finish_oauth(net: str, code: str, state: str) -> dict:
    st = _load()
    pend = st.get("pending", {}).pop(state, None)
    if not pend or pend["net"] != net or time.time() - pend["at"] > 900:
        raise PublishError("The sign-in link expired or is invalid. Please try connecting again.")
    _save(st)  # consume the one-time state
    cid, secret = client(net)
    data = {"code": code, "redirect_uri": pend["redirect"], "grant_type": "authorization_code",
            "client_secret": secret}
    data["client_key" if net == "tiktok" else "client_id"] = cid
    with httpx.Client(timeout=30) as h:
        if net in ("instagram", "facebook"):
            r = h.get(OAUTH[net]["token"], params=data)
        else:
            r = h.post(OAUTH[net]["token"], data=data)
        tok = _json(r, "sign-in")
        if net in ("instagram", "facebook"):  # exchange for a long-lived (60-day) user token
            r = h.get(f"{GRAPH}/oauth/access_token", params={
                "grant_type": "fb_exchange_token", "client_id": cid, "client_secret": secret,
                "fb_exchange_token": tok["access_token"]})
            tok = _json(r, "sign-in")
        tok["obtained_at"] = time.time()
        tok.update(_account(h, net, tok))
    st = _load()  # re-read: another tab may have changed it during the sign-in
    aid = _acc_id(net, tok)
    st["accounts"].setdefault(net, {})[aid] = tok
    # The new account becomes active unless the preferred (default) account is already chosen.
    default = _norm(DEFAULT_ACCOUNT.get(net, ""))
    current = _active_id(st, net)
    cur_tok = (st["accounts"][net] or {}).get(current) or {}
    if not (default and current != aid and _norm(cur_tok.get("account", "")) == default):
        st["active"][net] = aid
    st["selected"] = net
    _save(st)
    return status()


def _json(r: httpx.Response, what: str) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        err = data.get("error") if isinstance(data, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else None) or data.get("error_description") \
            or (err if isinstance(err, str) else None) or r.text[:200]
        reasons = [e.get("reason") for e in (err.get("errors") or []) if isinstance(e, dict)]             if isinstance(err, dict) else []
        hit = next((x for x in reasons if x in LIMITS), None)
        if hit:
            raise LimitError(LIMITS[hit], reason=hit)
        raise PublishError(f"{what.capitalize()} failed ({r.status_code}): {msg}")
    return data


def _account(h: httpx.Client, net: str, tok: dict) -> dict:
    """Name of the connected channel / page / account (+ ids needed for publishing)."""
    at = tok["access_token"]
    if net == "youtube":
        d = _json(h.get("https://www.googleapis.com/youtube/v3/channels",
                        params={"part": "snippet", "mine": "true"}, headers={"Authorization": f"Bearer {at}"}), "channel lookup")
        items = d.get("items") or []
        if not items:
            raise PublishError("This Google account has no YouTube channel.")
        return {"account": items[0]["snippet"]["title"], "channel_id": items[0]["id"]}
    if net in ("facebook", "instagram"):
        pages = _json(h.get(f"{GRAPH}/me/accounts", params={
            "access_token": at, "fields": "id,name,access_token,instagram_business_account{id,username}"}), "page lookup")
        pages = pages.get("data") or []
        if not pages:
            raise PublishError("No Facebook Page found for this account (you need admin rights on a Page).")
        if net == "facebook":
            p = pages[0]
            return {"account": p["name"], "page_id": p["id"], "page_token": p["access_token"]}
        for p in pages:
            ig = p.get("instagram_business_account")
            if ig:
                return {"account": "@" + ig.get("username", ""), "ig_user_id": ig["id"]}
        raise PublishError("No Instagram Professional account is linked to your Facebook Pages.")
    d = _json(h.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "open_id,display_name"},
                    headers={"Authorization": f"Bearer {at}"}), "account lookup")
    user = (d.get("data") or {}).get("user", {})
    return {"account": user.get("display_name", "TikTok"), "open_id": user.get("open_id") or tok.get("open_id")}


def _access_token(net: str) -> dict:
    """Token of the ACTIVE account, refreshed when needed (YouTube / TikTok tokens are short-lived)."""
    st = _load()
    aid = _active_id(st, net)
    tok = (st["accounts"].get(net) or {}).get(aid) if aid else None
    if not tok:
        raise PublishError(f"Not connected to {LABELS[net]}.")
    age = time.time() - tok.get("obtained_at", 0)
    if net in ("youtube", "tiktok") and age > int(tok.get("expires_in", 3600)) - 120:
        if not tok.get("refresh_token"):
            raise PublishError(f"The {LABELS[net]} session expired. Please connect again.")
        cid, secret = client(net)
        data = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_secret": secret}
        data["client_key" if net == "tiktok" else "client_id"] = cid
        new = _json(httpx.post(OAUTH[net]["token"], data=data, timeout=30), "token refresh")
        tok.update({k: v for k, v in new.items() if v}, obtained_at=time.time())
        st["accounts"][net][aid] = tok
        _save(st)
    return tok


# ---------- publishing ----------

def caption(clip: dict, net: str) -> tuple[str, str]:
    """(title, description) from Claude's metadata."""
    tags = " ".join(h if h.startswith("#") else "#" + h for h in clip.get("hashtags") or [])
    refs = " · ".join(clip.get("scripture_refs") or [])
    title = " ".join((clip.get("publish_title") or clip.get("title") or "Short").split())
    if net == "youtube" and "#shorts" not in title.lower():
        title = title[:92].rstrip() + " #Shorts"
    title = title[:100]
    body = "\n\n".join(x for x in (clip.get("post_caption", "").strip(), refs, tags) if x)
    return title, body


CHUNK = 8 * 1024 * 1024  # YouTube resumable chunks must be multiples of 256 KiB


def publish(video: Path, clip: dict, net: str, privacy: str = "public", progress=None) -> dict:
    """Upload one short and wait until the network confirms it is online.

    progress(phase, fraction) is called with phase "uploading" (0..1) then "processing".
    Returns {"url", "privacy", "state": "online" | "processing", "note"}."""
    if not video.is_file():
        raise PublishError("The rendered video file is missing. Re-render the short first.")
    progress = progress or (lambda phase, frac: None)
    tok = _access_token(net)
    title, body = caption(clip, net)
    res = {"youtube": _youtube, "facebook": _facebook, "instagram": _instagram, "tiktok": _tiktok}[net](
        video, tok, title, body, privacy, progress)
    res["account"] = tok.get("account", "")
    return res


def _youtube(video: Path, tok: dict, title: str, body: str, privacy: str, progress) -> dict:
    size = video.stat().st_size
    wanted = privacy if privacy in ("public", "unlisted", "private") else "public"
    meta = {"snippet": {"title": title, "description": body[:4900], "categoryId": "29"},  # Nonprofits & Activism
            "status": {"privacyStatus": wanted, "selfDeclaredMadeForKids": False}}
    auth = {"Authorization": f"Bearer {tok['access_token']}"}
    with httpx.Client(timeout=httpx.Timeout(60, write=600)) as h:
        r = h.post("https://www.googleapis.com/upload/youtube/v3/videos",
                   params={"uploadType": "resumable", "part": "snippet,status"},
                   headers={**auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                   json=meta)
        if r.status_code >= 400:
            _json(r, "YouTube upload")
        session = r.headers["Location"]
        progress("uploading", 0.0)
        offset, video_json = 0, None
        with open(video, "rb") as f:
            while video_json is None:  # chunked resumable upload → real progress
                f.seek(offset)
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                r = h.put(session, content=chunk, headers={
                    **auth, "Content-Type": "video/mp4", "Content-Range": f"bytes {offset}-{end}/{size}"})
                if r.status_code == 308:  # chunk stored, more to send
                    rng = r.headers.get("Range")
                    offset = int(rng.rsplit("-", 1)[1]) + 1 if rng else 0
                    progress("uploading", offset / size)
                elif r.status_code in (200, 201):
                    video_json = r.json()
                else:
                    _json(r, "YouTube upload")
        vid = video_json["id"]
        progress("processing", 1.0)
        # Confirm it is online: YouTube must finish processing before the Short can be watched.
        state, priv = "processing", video_json.get("status", {}).get("privacyStatus", wanted)
        for _ in range(36):  # up to ~6 minutes
            d = _json(h.get("https://www.googleapis.com/youtube/v3/videos",
                            params={"part": "status", "id": vid}, headers=auth), "YouTube status")
            items = d.get("items") or []
            if items:
                st = items[0].get("status", {})
                priv = st.get("privacyStatus", priv)
                us = st.get("uploadStatus")
                if us == "processed":
                    state = "online"
                    break
                if us in ("failed", "rejected", "deleted"):
                    why = st.get("failureReason") or st.get("rejectionReason") or us
                    raise PublishError(f"YouTube {us} the video ({why}).")
            time.sleep(10)
    note = ""
    if priv == "private" and wanted != "private":
        note = "YouTube kept it Private (API project not audited yet): make it Public in YouTube Studio."
    return {"url": f"https://youtube.com/shorts/{vid}", "privacy": priv, "state": state, "note": note}


def _facebook(video: Path, tok: dict, title: str, body: str, privacy: str, progress) -> dict:
    page, ptok = tok["page_id"], tok["page_token"]
    with httpx.Client(timeout=httpx.Timeout(60, write=600)) as h:
        start = _json(h.post(f"{GRAPH}/{page}/video_reels", data={"upload_phase": "start", "access_token": ptok}),
                      "Facebook upload")
        progress("uploading", 0.05)
        size = video.stat().st_size
        with open(video, "rb") as f:
            _json(h.post(start["upload_url"], content=f.read(), headers={
                "Authorization": f"OAuth {ptok}", "offset": "0", "file_size": str(size)}), "Facebook upload")
        progress("processing", 1.0)
        _json(h.post(f"{GRAPH}/{page}/video_reels", data={
            "upload_phase": "finish", "video_id": start["video_id"], "video_state": "PUBLISHED",
            "description": f"{title}\n\n{body}"[:2200], "access_token": ptok}), "Facebook publish")
    return {"url": f"https://www.facebook.com/reel/{start['video_id']}", "privacy": "public", "state": "online", "note": ""}


def _instagram(video: Path, tok: dict, title: str, body: str, privacy: str, progress) -> dict:
    ig, at = tok["ig_user_id"], tok["access_token"]
    with httpx.Client(timeout=httpx.Timeout(60, write=600)) as h:
        c = _json(h.post(f"{GRAPH}/{ig}/media", data={
            "media_type": "REELS", "upload_type": "resumable", "caption": f"{title}\n\n{body}"[:2200],
            "share_to_feed": "true", "access_token": at}), "Instagram upload")
        progress("uploading", 0.05)
        size = video.stat().st_size
        with open(video, "rb") as f:
            _json(h.post(f"https://rupload.facebook.com/ig-api-upload/v21.0/{c['id']}", content=f.read(), headers={
                "Authorization": f"OAuth {at}", "offset": "0", "file_size": str(size)}), "Instagram upload")
        progress("processing", 1.0)
        for _ in range(60):  # Instagram processes the video before it can be published
            s = _json(h.get(f"{GRAPH}/{c['id']}", params={"fields": "status_code", "access_token": at}), "Instagram status")
            if s.get("status_code") == "FINISHED":
                break
            if s.get("status_code") == "ERROR":
                raise PublishError("Instagram could not process the video.")
            time.sleep(5)
        m = _json(h.post(f"{GRAPH}/{ig}/media_publish", data={"creation_id": c["id"], "access_token": at}),
                  "Instagram publish")
        link = _json(h.get(f"{GRAPH}/{m['id']}", params={"fields": "permalink", "access_token": at}), "Instagram link")
    return {"url": link.get("permalink") or "https://www.instagram.com/", "privacy": "public", "state": "online", "note": ""}


def _tiktok(video: Path, tok: dict, title: str, body: str, privacy: str, progress) -> dict:
    size = video.stat().st_size
    auth = {"Authorization": f"Bearer {tok['access_token']}", "Content-Type": "application/json; charset=UTF-8"}
    with httpx.Client(timeout=httpx.Timeout(60, write=600)) as h:
        info = _json(h.post("https://open.tiktokapis.com/v2/post/publish/creator_info/query/", headers=auth),
                     "TikTok account check").get("data", {})
        levels = info.get("privacy_level_options") or ["SELF_ONLY"]
        level = "PUBLIC_TO_EVERYONE" if privacy == "public" and "PUBLIC_TO_EVERYONE" in levels else levels[-1]
        init = _json(h.post("https://open.tiktokapis.com/v2/post/publish/video/init/", headers=auth, json={
            "post_info": {"title": f"{title}\n\n{body}"[:2100], "privacy_level": level},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1},
        }), "TikTok upload")["data"]
        progress("uploading", 0.05)
        with open(video, "rb") as f:
            r = h.put(init["upload_url"], content=f.read(), headers={
                "Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"})
            if r.status_code >= 400:
                raise PublishError(f"TikTok upload failed ({r.status_code}).")
        progress("processing", 1.0)
        state = "processing"
        for _ in range(36):  # confirm: PUBLISH_COMPLETE
            d = _json(h.post("https://open.tiktokapis.com/v2/post/publish/status/fetch/", headers=auth,
                             json={"publish_id": init["publish_id"]}), "TikTok status").get("data", {})
            if d.get("status") == "PUBLISH_COMPLETE":
                state = "online"
                break
            if d.get("status") == "FAILED":
                raise PublishError(f"TikTok could not publish the video ({d.get('fail_reason', 'unknown')}).")
            time.sleep(5)
    public = level == "PUBLIC_TO_EVERYONE"
    return {"url": "https://www.tiktok.com/", "privacy": "public" if public else "private", "state": state,
            "note": "" if public else "Private until TikTok audits your developer app."}

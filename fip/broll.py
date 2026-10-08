"""B-roll: short cutaway shots over the preacher (a wheat field on "moisson", an open Bible…).

Claude proposes up to 3 cues per clip, each anchored on a word the preacher says:
    {"sentence": 412, "word": "moisson", "query": "wheat field sunset", "keywords": ["moisson", "blé"],
     "seconds": 3}
`resolve()` turns a cue into a playable file: first a matching clip of the church's own library
(keywords), otherwise a video from Pixabay (free Content License, free API key in .env; search
answers are cached 24 h as Pixabay asks). Network or search failures never fail a render: the cue
is simply dropped.
"""
import hashlib
import json
import logging
import os
import re
import shutil
import time
import unicodedata
import uuid
from pathlib import Path

import httpx

from . import settings

log = logging.getLogger(__name__)

PIXABAY_VIDEOS = "https://pixabay.com/api/videos/"
SEARCH_TTL = 24 * 3600           # Pixabay: "requests must be cached for 24 hours"
MAX_DOWNLOAD = 80 * 1024 * 1024  # skip a 4K file larger than this (use the 1080p one)
MAX_CUES = 3
MIN_SECONDS, MAX_SECONDS = 1.5, 4.5
LIBRARY_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


class BrollError(Exception):
    pass


# ---------- helpers ----------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s).lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def _tokens(*texts) -> set:
    return {t[:-1] if t.endswith("s") and len(t) > 3 else t   # coins ~ coin, moissons ~ moisson
            for x in texts for t in _norm(x).split() if len(t) > 2}


def _root() -> Path:
    return settings.DATA_DIR / "broll"


def _cache() -> Path:
    d = _root() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stock_key() -> str:
    settings.reload_env()
    return (os.getenv("PIXABAY_API_KEY") or "").strip()


def anchor_time(transcript: dict, sentence: int, word: str) -> float | None:
    """Absolute source time of `word` in sentence `sentence` (first match; sentence start otherwise)."""
    sents, words = transcript["sentences"], transcript["words"]
    if not (0 <= sentence < len(sents)):
        return None
    s = sents[sentence]
    target = _norm(word)
    for w in words[s["w0"]:s["w1"] + 1]:
        if target and _norm(w["w"]) == target:
            return w["s"]
    for w in words[s["w0"]:s["w1"] + 1]:          # "moisson," / "l'Éternel" → contains
        if target and target in _norm(w["w"]):
            return w["s"]
    return s["s"]


# ---------- church library ----------

def _lib_dir() -> Path:
    d = _root() / "library"
    d.mkdir(parents=True, exist_ok=True)
    return d


def library() -> list:
    f = _lib_dir() / "library.json"
    try:
        items = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [i for i in items if (_lib_dir() / i["file"]).exists()]


def _save_library(items: list):
    (_lib_dir() / "library.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def library_add(src: Path, name: str, keywords: list) -> dict:
    """Register an uploaded clip (moved into the library) with its keywords and a thumbnail."""
    from .ffmpeg import probe, thumbnail
    info = probe(src, need_audio=False)  # cutaways are usually silent
    if not info.get("has_video"):
        raise BrollError("This file has no video.")
    iid = uuid.uuid4().hex[:10]
    dst = _lib_dir() / f"{iid}{src.suffix.lower()}"
    shutil.move(str(src), dst)
    try:
        thumbnail(dst, _lib_dir() / f"{iid}.jpg", at=min(1.0, info["duration"] / 2), width=240)
    except Exception as e:  # noqa: BLE001 — a missing thumbnail is cosmetic
        log.warning("B-roll thumbnail failed: %s", e)
    kws = sorted({k.strip() for k in keywords if k.strip()})[:30]
    item = {"id": iid, "file": dst.name, "name": name.strip()[:80] or iid, "keywords": kws,
            "duration": round(info["duration"], 2), "width": info["width"], "height": info["height"],
            "added": int(time.time())}
    items = library() + [item]
    _save_library(items)
    return item


def library_update(iid: str, name: str | None = None, keywords: list | None = None) -> dict:
    items = library()
    for i in items:
        if i["id"] == iid:
            if name is not None:
                i["name"] = name.strip()[:80] or i["name"]
            if keywords is not None:
                i["keywords"] = sorted({k.strip() for k in keywords if k.strip()})[:30]
            _save_library(items)
            return i
    raise BrollError("B-roll clip not found.")


def library_remove(iid: str):
    items = library()
    keep = [i for i in items if i["id"] != iid]
    for i in items:
        if i["id"] == iid:
            (_lib_dir() / i["file"]).unlink(missing_ok=True)
            (_lib_dir() / f"{iid}.jpg").unlink(missing_ok=True)
    _save_library(keep)


def library_file(iid: str, thumb: bool = False) -> Path | None:
    for i in library():
        if i["id"] == iid:
            p = _lib_dir() / (f"{iid}.jpg" if thumb else i["file"])
            return p if p.exists() else None
    return None


def library_match(cue: dict, used: set = frozenset()) -> dict | None:
    """Best library clip for a cue: most shared keywords (at least one)."""
    want = _tokens(cue.get("query", ""), cue.get("word", ""), *cue.get("keywords", []))
    best, score = None, 0
    for i in library():
        if i["id"] in used:
            continue
        s = len(want & _tokens(i["name"], *i["keywords"]))
        if s > score:
            best, score = i, s
    return best


# ---------- Pixabay ----------

def _pick_file(hit: dict) -> dict | None:
    """The stream to crop to 9:16: 4K when available and not huge (a 1080p frame cropped to 9:16 is
    only 608 px wide), else 1080p. Never below 720 px tall."""
    vids = hit.get("videos") or {}
    for size in ("large", "medium"):
        f = vids.get(size) or {}
        if f.get("url") and (f.get("height") or 0) >= 720 and 0 < (f.get("size") or 0) <= MAX_DOWNLOAD:
            return dict(f, quality=size)
    f = vids.get("medium") or {}
    return dict(f, quality="medium") if f.get("url") and (f.get("height") or 0) >= 720 else None


def _cached_search(params: dict) -> dict:
    key = hashlib.md5(json.dumps({k: v for k, v in params.items() if k != "key"}, sort_keys=True)
                      .encode()).hexdigest()[:16]
    f = _cache() / f"search_{key}.json"
    if f.exists() and time.time() - f.stat().st_mtime < SEARCH_TTL:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        with httpx.Client(timeout=20) as c:
            r = c.get(PIXABAY_VIDEOS, params=params)
    except httpx.HTTPError as e:
        raise BrollError(f"Pixabay is unreachable ({e.__class__.__name__}).") from e
    if r.status_code in (400, 401, 403) and "key" in r.text.lower():
        raise BrollError("The Pixabay API key was rejected.")
    if r.status_code == 429:
        raise BrollError("Pixabay rate limit reached; try again in a minute.")
    if r.status_code != 200:
        raise BrollError(f"Pixabay error {r.status_code}.")
    data = r.json()
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


PIXABAY_LANGS = {"cs", "da", "de", "en", "es", "fr", "id", "it", "hu", "nl", "no", "pl", "pt", "ro", "sk", "fi",
                 "sv", "tr", "vi", "th", "bg", "ru", "el", "ja", "ko", "zh"}


def stock_search(query: str, need: float, key: str | None = None, per_page: int = 15, lang: str = "en") -> list:
    """Candidate videos [{id, file, image, user, url, duration}], most popular first."""
    key = key or stock_key()
    if not key:
        raise BrollError("No Pixabay API key.")
    def search(q):
        return _cached_search({"key": key, "q": q[:100], "video_type": "film", "safesearch": "true",
                               "per_page": per_page, "min_height": 720,
                               "lang": lang if lang in PIXABAY_LANGS else "en"}).get("hits", [])

    want = _tokens(query)
    hits = search(query)
    best = max((len(want & _tokens(h.get("tags", ""))) for h in hits), default=0)
    head = next((w for w in query.split() if len(w) > 2), "")
    if len(want) > 1 and best < len(want) and head and head.lower() != query.lower():
        seen = {h["id"] for h in hits}   # Pixabay matches ANY word: also search the subject alone
        hits += [h for h in search(head) if h["id"] not in seen]
    out = []
    for h in hits:
        f = _pick_file(h)
        if f and (h.get("duration") or 0) >= need:
            out.append({"id": h["id"], "file": f, "image": f.get("thumbnail", ""), "url": h.get("pageURL", ""),
                        "user": h.get("user", ""), "duration": h.get("duration"),
                        # the subject (first word) counts more: coins in hand → coins, not sand in a hand
                        "match": len(want & _tokens(h.get("tags", ""))) + 0.5 * bool(_tokens(head) & _tokens(h.get("tags", "")))})
    # Pixabay ranks by popularity: put the videos whose tags carry most query words first
    # ("coins in hand" → coins, not a popular "sand in hand"); stable sort keeps popularity next.
    out.sort(key=lambda c: -c["match"])
    return out


def _download(url: str, dst: Path):
    tmp = dst.with_suffix(".part")
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as c, c.stream("GET", url) as r:
            if r.status_code != 200:
                raise BrollError(f"Download failed ({r.status_code}).")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
        tmp.replace(dst)
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise BrollError(f"Download failed ({e.__class__.__name__}).") from e


# ---------- resolve ----------

def resolve(clip: dict, transcript: dict, used: set | None = None) -> list:
    """Fill each cue of clip['broll'] with a file (library first, then Pixabay) and its absolute
    anchor time. Returns the playable cues; cues already resolved (file present) are kept as they are.
    Cue fields set: at, file, source ('library'|'pixabay'), credit, url, thumb, ref."""
    used = set() if used is None else used
    out = []
    for cue in (clip.get("broll") or [])[:MAX_CUES]:
        if not isinstance(cue, dict) or cue.get("off"):
            continue
        cue = dict(cue)
        cue["seconds"] = min(MAX_SECONDS, max(MIN_SECONDS, float(cue.get("seconds") or 3)))
        if cue.get("at") is None:
            cue["at"] = anchor_time(transcript, int(cue.get("sentence", -1)), cue.get("word", ""))
        if cue["at"] is None:
            continue
        if cue.get("file") and Path(cue["file"]).exists():
            used.add(cue.get("ref"))
            out.append(cue)
            continue
        try:
            got = _resolve_one(cue, used, transcript.get("language") or "en")
        except BrollError as e:
            log.warning("B-roll '%s' skipped: %s", cue.get("query"), e)
            got = None
        if got:
            cue.update(got)
            used.add(cue["ref"])
            out.append(cue)
    return out


def _resolve_one(cue: dict, used: set, lang: str = "en") -> dict | None:
    src = cue.get("source") or "auto"
    if src.startswith("library:"):                      # chosen by the user in Adjust
        iid = src.split(":", 1)[1]
        f = library_file(iid)
        item = next((i for i in library() if i["id"] == iid), None)
        if f and item:
            return {"file": str(f), "source": "library", "ref": f"lib:{iid}", "credit": item["name"],
                    "url": "", "thumb": f"/api/broll/library/{iid}/thumb"}
        return None
    if src == "auto":
        item = library_match(cue, {u[4:] for u in used if u and u.startswith("lib:")})
        if item:
            return {"file": str(_lib_dir() / item["file"]), "source": "library", "ref": f"lib:{item['id']}",
                    "credit": item["name"], "url": "", "thumb": f"/api/broll/library/{item['id']}/thumb"}
    query, qlang = (cue.get("query") or "").strip(), "en"
    if not query:                       # added in Adjust without a search: the spoken word itself
        query, qlang = (cue.get("word") or "").strip(), lang
    if not query or not stock_key():
        return None
    cands = [c for c in stock_search(query, cue["seconds"], lang=qlang) if f"pb:{c['id']}" not in used]
    if not cands:
        return None
    pick = cands[int(cue.get("variant") or 0) % len(cands)]
    f = pick["file"]
    dst = _cache() / f"pixabay_{pick['id']}_{f['quality']}.mp4"
    if not dst.exists():
        _download(f["url"], dst)
    return {"file": str(dst), "source": "pixabay", "ref": f"pb:{pick['id']}",
            "credit": f"{pick['user']} / Pixabay" if pick["user"] else "Pixabay", "url": pick["url"],
            "thumb": pick["image"]}

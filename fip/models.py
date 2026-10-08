"""Validated options and the public clip JSON format.

Transcript JSON (transcript.json), kept as plain dicts for speed and compatibility:
    {"language": "fr",
     "words":     [{"w": "Dieu", "s": 12.34, "e": 12.61, "p": 0.98}, ...],
     "sentences": [{"id": 0, "w0": 0, "w1": 17, "s": 1.2, "e": 6.9, "text": "..."}, ...]}
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_CLIPS = 10
SHORT_VIDEO_MAX = 180.0  # seconds: with 1 clip, a video this short is used whole
MAX_PASSAGES = 4         # montage: at most this many sermon passages joined in one short
CATEGORIES = ("doctrine", "application", "repentance", "discipleship", "gospel")
DEFAULT_BRAND = "Eglise Evangélique Internationale de Genève"
DEFAULT_TAGLINE = "Partagez la Bonne Nouvelle"


class Options(BaseModel):
    """Every knob of the pipeline. Unknown keys are ignored (old project files stay loadable)."""

    model_config = {"extra": "ignore"}

    # --- clip selection ---
    num_clips: int = Field(10, ge=1, le=MAX_CLIPS)
    min_duration: float = Field(30, ge=5, le=300)
    max_duration: float = Field(60, ge=10, le=600)
    language: str = "auto"                     # Whisper language code or "auto"
    refine: bool = True                        # 2nd Claude pass on cut points
    guidance: str = ""                         # free text for Claude
    montage: bool = True                       # a short may join 2-4 passages of the sermon
    montage_max: float = Field(90, ge=30, le=180)  # max length of a montage short (seconds)
    # 1-clip mode on a long video: ask the user, let Claude pick, or use a manual range
    single_mode: Literal["ask", "auto", "manual"] = "ask"
    single_start: str = ""
    single_end: str = ""

    # --- picture ---
    crop: Literal["track", "center"] = "track"  # speaker tracking or plain centre crop
    slides: Literal["fit", "crop"] = "fit"      # no face on screen (slides): show whole frame
    punch_zoom: bool = True                    # alternate 1.0 / 1.14 zoom on sentence boundaries
    broll: bool = True                         # cutaway shots (church library, then Pixabay)

    # --- subtitles ---
    caption_style: Literal["eeig", "impact"] = "eeig"  # church style (sentence case, blue box) or Vizard-like
    highlight: Literal["active", "keyword"] = "active"  # impact style: spoken word, or a keyword per chunk
    emojis: bool = False                       # off in the church style; the impact style shows them
    title_mode: Literal["off", "hook", "full"] = "hook"  # hook title at the top: first seconds / whole short
    title_seconds: float = Field(15, ge=2, le=120)           # how long it stays in "hook" mode

    # --- branding ---
    outro: bool = True
    brand: str = DEFAULT_BRAND
    tagline: str = DEFAULT_TAGLINE
    watermark: str = ""

    @field_validator("num_clips", mode="before")
    @classmethod
    def _clamp_clips(cls, v):
        try:
            return max(1, min(MAX_CLIPS, int(v)))
        except (TypeError, ValueError):
            return 10

    @model_validator(mode="after")
    def _check_range(self):
        if self.max_duration <= self.min_duration:
            self.max_duration = self.min_duration + 15
        return self


def passages_of(clip: dict) -> list:
    """[(start_sentence, end_sentence), ...] in play order. A clip without 'passages' (single
    passage, or a project made before montage mode) is its start_sentence..end_sentence."""
    ps = clip.get("passages") or []
    out = [(int(p["start_sentence"]), int(p["end_sentence"])) for p in ps
           if isinstance(p, dict) and "start_sentence" in p and "end_sentence" in p]
    return out or [(int(clip["start_sentence"]), int(clip["end_sentence"]))]


def parse_time(v) -> float | None:
    """'75', '1:15', '01:02:03', '12,5' → seconds (None if invalid)."""
    v = str(v if v is not None else "").strip().replace(",", ".")
    if not v:
        return None
    try:
        parts = [float(x) for x in v.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(x < 0 for x in parts):
        return None
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return sec


def fmt_time(t: float) -> str:
    t = max(0, int(round(t)))
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def spec_json(clip: dict) -> dict:
    """The public, documented clip format (clips.json / CLI output)."""
    return {
        "index": clip.get("index"),
        "start_time": clip.get("start", clip.get("start_time")),
        "end_time": clip.get("end", clip.get("end_time")),
        "title": clip.get("title", ""),
        "hook_title": clip.get("hook_title", clip.get("title", "")),
        "publish_title": clip.get("publish_title") or clip.get("title", ""),
        "hook": clip.get("hook", ""),
        "virality_score": clip.get("score"),
        "summary": clip.get("core_message", ""),
        "reasoning": clip.get("why_meaningful", ""),
        "category": clip.get("category"),
        "scripture_refs": clip.get("scripture_refs", []),
        "post_caption": clip.get("post_caption", ""),
        "hashtags": clip.get("hashtags", []),
        "segments": clip.get("segments") or [[clip.get("start"), clip.get("end")]],
        "broll": clip.get("broll_used") or [],
        "video": clip.get("video"),
    }

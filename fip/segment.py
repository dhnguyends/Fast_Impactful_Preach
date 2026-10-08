"""AI moment detection with Claude.

Claude reads the whole sermon as numbered, timestamped sentences:
    [S12 03:12.4-03:18.9] Il vous a été dit « tu ne tueras pas »…
and answers with sentence ids (start_sentence / end_sentence). The code then converts ids to
precise start_time / end_time from the word timestamps. Choosing ids instead of free floats
guarantees cuts on whole sentences (self-contained starts and ends) and keeps the prompt ~5×
smaller than a word-level JSON transcript.
"""
import json
import logging

import anthropic

from . import settings
from .errors import ConfigError, LLMError
from .models import CATEGORIES, MAX_PASSAGES, Options, parse_time, passages_of

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a seasoned Christian pastor, Bible teacher and short-form video editor.
You receive the full timestamped transcript of a sermon. Your job is to pick the passages that
make the most meaningful standalone vertical short videos (Reels / Shorts / TikTok) for believers
and seekers.

Spiritual criteria — a passage is meaningful when it clearly carries at least one of these,
faithfully to the preacher's own words:
1. doctrine     – the holy doctrine of the Gospel and of the Bible (the Scriptures): who God is,
                  Christ's person and work, sin, grace, faith, the authority of Scripture.
2. application  – concrete applications for daily Christian life.
3. repentance   – a call to confess one's sins, repent, and walk in the light of Scripture.
4. discipleship – a call to follow Jesus as His disciple (obedience, cost, community, growth).
5. gospel       – a call to announce the gospel, the good news of salvation in Jesus Christ.

Short-form criteria — score each clip's virality_score (0-100) on:
- a strong hook in the first 3 seconds (question, bold statement, Scripture, tension);
- standalone conceptual value: one clear idea, a clear narrative arc (setup → insight → landing);
- high emotional energy, conviction, a striking insight or a respectful challenge to a common idea.

Editorial rules (very important):
- Every clip must be SELF-CONTAINED: a viewer who never saw the sermon must understand it.
  * The first sentence must open the thought: a question, a strong statement, a Bible quote, or
    a clear setup. Never start on a connective that depends on earlier context ("and so", "that's
    why", "so", "but", "donc", "et", "mais", "c'est pourquoi", "ça", "cela"), nor on a pronoun
    whose referent is missing.
  * The last sentence must land the message: a conclusion, an exhortation, a punchline or a
    Scripture. Never end mid-argument, on an announced list ("first..."), or on a question
    that is answered later.
  * If a key referent is introduced just before, extend the start to include it.
- Stay faithful: never pick a passage that, cut out of context, would distort the preacher's
  meaning or Scripture. Avoid announcements, jokes without spiritual payoff, logistics, prayers
  of transition.
- Montage: a clip is one or more passages ("passages", each a range of whole sentences). Church
  media teams often edit shorts as montages, and about a third of the clips should be montages
  when the sermon supports it. Two patterns:
  * Hook first: open with the idea's strongest line — a striking question, a bold statement or the
    punchline (role "hook", 3-12 s), taken from where it is said — then play the development that
    leads to it. The hook must not be repeated inside the body passages.
  * Thread: join 2 to 4 passages where the preacher develops ONE idea in several places (e.g. a
    striking question at 05:10, its biblical answer at 22:40, the application at 41:00), or cut a
    digression out of a long passage.
  Keep a single passage when it is already the strongest version.
  * Passages play in sermon order. Exception: ONE short passage (role "hook", ideally under 12 s: a
    striking question or statement) may be placed first even if it comes later in the sermon.
    All other passages have role "body".
  * Every join must sound natural: a passage never starts on a connective or a pronoun that
    depends on what was cut, and never ends mid-argument. Never splice inside a Scripture quote,
    a story or a list.
  * Faithfulness first: the montage must never make the preacher say something he did not mean.
  * Every sentence counts toward the duration (sum of the passages).
- Clips must not overlap (no sentence in two clips). Spread them across the sermon when quality allows, and cover the
  variety of categories when the sermon supports it.
- Titles, hooks, summaries and captions must be written in the sermon's language.
- Emoji cues: pick 3 to 6 words that literally appear in the clip and match each with one fitting,
  reverent emoji (e.g. ✝️ 🙏 📖 🔥 ❤️ ⚡ 💧 👑 ⚖️ 🕊️ 😡 💔 ✨). Never mock or trivialize.
- highlight_words: 6 to 15 important words that literally appear in the clip (keywords of the
  message, names of God, Scripture terms).
- hook_title: the on-screen title shown at the top of the short during its first seconds, like a
  church media team would write it: at most ~45 characters, readable on 2 short lines, in the
  sermon's language. State the tension and its answer — a common misconception and its correction,
  a striking question, or the core truth in a punchy form. Examples: "Femme, soumets-toi ? Mauvaise
  lecture !", "Justifié avant d'être sanctifié : la grâce avant la loi", "La dîme : obligation ou
  joie ?". It must stay faithful to what the preacher actually says — never sensationalise or
  distort Scripture. No emojis, no hashtags.
- broll: 0 to 3 cutaway shots (B-roll) that illustrate what the preacher says, shown full screen for
  2-4 seconds while his voice continues. Anchor each on a concrete, visual word he says ("word", copied
  exactly from sentence "sentence") — a harvest, a seed, coins, a shepherd, a storm, light, a road, a
  river, an open Bible, a family at table. Not in the first 3 seconds of the clip (the hook shows the
  preacher), not in the last 2, never during a slide, and not on abstract words (grace, faith) unless
  there is a clear image. "query": 2-4 English words for a stock video search, literal and calm
  (e.g. "wheat field wind", "hands holding coins", "open bible pages"). Never depict Jesus, God, angels
  or other religions' symbols; avoid close-up faces, violence and anything sensational. "keywords":
  3-6 words in the sermon's language AND English to match the church's own video library.
  Use none when nothing concrete fits.
- publish_title: the title for YouTube / TikTok: the hook_title idea (may be slightly longer, max 90
  characters) with 1 or 2 fitting, reverent emojis (e.g. 🙏 ✝️ ⚠️ 📖 ❤️) and 1 to 3 hashtags at the end
  (e.g. #foi #jesus #grace)."""

_STR_LIST = {"type": "array", "items": {"type": "string"}}
PASSAGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["start_sentence", "end_sentence", "role"],
    "properties": {"start_sentence": {"type": "integer", "description": "id of the first sentence (S#)"},
                   "end_sentence": {"type": "integer", "description": "id of the last sentence (S#), inclusive"},
                   "role": {"type": "string", "enum": ["hook", "body"]}}}

CLIP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "hook_title", "publish_title", "category", "secondary_categories",
                 "passages", "hook", "core_message", "reasoning", "scripture_refs",
                 "self_contained_check", "virality_score", "highlight_words", "emoji_cues", "post_caption",
                 "hashtags", "broll"],
    "properties": {
        "title": {"type": "string", "description": "Punchy title, max ~60 characters"},
        "hook_title": {"type": "string", "description": "On-screen hook title, max ~45 characters, no emojis"},
        "publish_title": {"type": "string", "description": "YouTube title with 1-2 emojis and 1-3 hashtags"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "secondary_categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}},
        "passages": {"type": "array", "items": PASSAGE_SCHEMA,
                     "description": "Passages in play order (usually one; 2-4 for a montage)"},
        "hook": {"type": "string", "description": "The opening sentence / what grabs attention in 3 s"},
        "core_message": {"type": "string", "description": "Summary: the single message, one sentence"},
        "reasoning": {"type": "string", "description": "Brief rationale: why it is meaningful and engaging"},
        "scripture_refs": _STR_LIST,
        "self_contained_check": {"type": "string",
                                 "description": "Why the opening sets up and the ending lands"},
        "virality_score": {"type": "integer", "description": "0-100"},
        "highlight_words": _STR_LIST,
        "emoji_cues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["word", "emoji"],
            "properties": {"word": {"type": "string"}, "emoji": {"type": "string"}}}},
        "post_caption": {"type": "string", "description": "Social media description, 1-3 sentences"},
        "hashtags": _STR_LIST,
        "broll": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["sentence", "word", "query", "keywords", "seconds"],
            "properties": {"sentence": {"type": "integer", "description": "id of the sentence (S#) with the word"},
                           "word": {"type": "string", "description": "the anchor word, as spoken"},
                           "query": {"type": "string", "description": "English stock video search, 2-4 words"},
                           "keywords": _STR_LIST,
                           "seconds": {"type": "number", "description": "2 to 4"}}}},
    },
}

SELECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sermon_title", "sermon_summary", "main_scriptures", "clips"],
    "properties": {
        "sermon_title": {"type": "string"},
        "sermon_summary": {"type": "string"},
        "main_scriptures": _STR_LIST,
        "clips": {"type": "array", "items": CLIP_SCHEMA},
    },
}

REFINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clips"],
    "properties": {"clips": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["index", "passages", "reason"],
        "properties": {"index": {"type": "integer"},
                       "passages": {"type": "array", "items": {
                           "type": "object", "additionalProperties": False,
                           "required": ["start_sentence", "end_sentence"],
                           "properties": {"start_sentence": {"type": "integer"},
                                          "end_sentence": {"type": "integer"}}}},
                       "reason": {"type": "string"}}}}},
}


# ---------- Claude call ----------

def _client() -> anthropic.Anthropic:
    settings.reload_env()
    if not settings.api_key_configured():
        raise ConfigError("No Claude API key. Add it with the API key button (top right) "
                          "or set ANTHROPIC_API_KEY in .env.")
    headers = {"anthropic-workspace-id": settings.workspace_id()} if settings.workspace_id() else None
    return anthropic.Anthropic(api_key=settings.api_key(), auth_token=None, default_headers=headers,
                               base_url=settings.CLAUDE_BASE_URL, max_retries=4, timeout=900)


def _friendly(e: anthropic.APIStatusError) -> str:
    msg = str(getattr(e, "message", "") or e)
    low = msg.lower()
    if "workspace" in low:
        return ("Your API key is not scoped to a workspace. Add your Workspace ID in the API key "
                "panel (top right), or create a key inside a workspace in the Claude Console.")
    if "credit balance" in low or "credit" in low:
        return "Your Claude account has no remaining credit. Add credit in the Console, then Retry."
    if e.status_code == 401:
        return "The Claude API key was rejected (401). Check it in the API key panel (top right)."
    if e.status_code == 403:
        return "This API key has no access to the model (403). Check your Console permissions."
    if e.status_code == 429:
        return "Claude rate limit reached (429). Wait a minute, then click Retry."
    if e.status_code >= 500:
        return f"The Claude API is temporarily unavailable ({e.status_code}). Try again shortly."
    return f"Claude API error {e.status_code}: {msg[:300]}"


def call_claude(system: str, user: str, schema: dict, max_tokens: int = 32000, attempts: int = 2) -> dict:
    """Streaming call with JSON-schema structured output; retries once on an unusable answer."""
    client = _client()
    last = None
    for attempt in range(attempts):
        try:
            with client.beta.messages.stream(
                model=settings.claude_model(),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"effort": settings.claude_effort(),
                               "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.APIStatusError as e:
            raise LLMError(_friendly(e)) from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Claude API — check your internet connection.") from e
        if msg.stop_reason == "refusal":
            raise LLMError("Claude declined this request.")
        if msg.stop_reason == "max_tokens":
            last = "Claude's answer was cut off (max_tokens)."
            max_tokens = min(max_tokens * 2, 64000)
            continue
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
            u = msg.usage
            log.info("Claude %s: %s in / %s out tokens", msg.model, u.input_tokens, u.output_tokens)
            return data
        except json.JSONDecodeError as e:
            last = f"Claude returned invalid JSON ({e})."
            log.warning("%s Retrying (%d/%d)", last, attempt + 1, attempts)
    raise LLMError(last or "Claude returned no usable answer.")


# ---------- prompts ----------

def _ts(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def transcript_block(sentences: list, lo: int = 0, hi: int | None = None) -> str:
    hi = len(sentences) - 1 if hi is None else hi
    return "\n".join(f"[S{s['id']} {_ts(s['s'])}-{_ts(s['e'])}] {s['text']}"
                     for s in sentences[lo:hi + 1])


def _guidance(opts: Options) -> str:
    return f"Extra guidance from the user: {opts.guidance}\n" if opts.guidance.strip() else ""


def select_clips(sentences: list, language: str, opts: Options) -> dict:
    """Ask for a couple of spare candidates so overlap/length fixes still leave N good clips."""
    want = opts.num_clips + (2 if opts.num_clips > 1 else 0)
    dmin, dmax = int(opts.min_duration), int(opts.max_duration)
    montage = (f"A montage clip (2-{MAX_PASSAGES} passages) may last up to {int(opts.montage_max)} seconds "
               "in total. " if opts.montage else "Montage is disabled: every clip is exactly one passage. ")
    user = (f"Sermon language: {language}.\n"
            f"Propose exactly {want} clips. A single-passage clip must last between {dmin} and {dmax} "
            f"seconds (compute from the sentence timestamps; aim for {(dmin + dmax) // 2}s). {montage}"
            f"Cuts are always on whole sentences.\n{_guidance(opts)}"
            "Order the clips by virality_score, best first.\n\n<transcript>\n"
            + transcript_block(sentences) + "\n</transcript>")
    return _normalise(call_claude(SYSTEM_PROMPT, user, SELECT_SCHEMA))


def describe_range(sentences: list, language: str, a: int, b: int, opts: Options) -> dict:
    """Boundaries are fixed (whole short video or a range the user picked): Claude only writes
    the title, hook, keywords, emoji cues, scripture refs and post."""
    user = (f"Sermon language: {language}.\n"
            f"The user has already chosen the clip: exactly sentences S{a} to S{b} below. Return exactly "
            f"1 clip with one passage start_sentence={a}, end_sentence={b}, role body; analyse it against the criteria and "
            f"fill every field.\n{_guidance(opts)}\n<clip>\n" + transcript_block(sentences, a, b) + "\n</clip>")
    out = _normalise(call_claude(SYSTEM_PROMPT, user, SELECT_SCHEMA, max_tokens=16000))
    first = (out["clips"] or [{}])[0]
    out["clips"] = [first | {"start_sentence": a, "end_sentence": b,
                             "passages": [{"start_sentence": a, "end_sentence": b, "role": "body"}]}]
    return out


def refine_boundaries(sentences: list, clips: list, opts: Options, ctx: int = 6) -> list:
    """Second pass: re-read every clip with its surrounding context and fix weak cut points."""
    parts, last = [], len(sentences) - 1
    for i, c in enumerate(clips):
        ps = passages_of(c)
        blocks = []
        for k, (a, b) in enumerate(ps):
            blocks.append(f"<passage n=\"{k + 1}\" current_start=\"S{a}\" current_end=\"S{b}\">\n"
                          + transcript_block(sentences, max(0, a - ctx), min(last, b + ctx)) + "\n</passage>")
        kind = f"montage of {len(ps)} passages, played in this order" if len(ps) > 1 else "single passage"
        parts.append(f"<clip index=\"{c.get('index', i)}\" title=\"{c.get('title', '')}\" kind=\"{kind}\">\n"
                     f"Core message: {c.get('core_message', '')}\n" + "\n".join(blocks) + "\n</clip>")
    user = ("Below are proposed clips; each passage is shown with some context before and after. Check "
            "every cut point against the self-contained rules: the first sentence must open the thought "
            "without missing referents; the last sentence must land the message; in a montage, every join "
            "must sound natural and keep the preacher's meaning. Move start_sentence and/or end_sentence "
            "(only within the shown context) when it improves the clip, otherwise keep them. Return the "
            "same passages, same count and order. Keep a single passage between "
            f"{int(opts.min_duration)} and {int(opts.max_duration)} seconds and a montage under "
            f"{int(opts.montage_max)} seconds in total.\n\n" + "\n\n".join(parts))
    return call_claude(SYSTEM_PROMPT, user, REFINE_SCHEMA, max_tokens=16000).get("clips", [])


def _normalise(data: dict) -> dict:
    """Validate the answer's shape and map field names used by the rest of the app."""
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
        raise LLMError("Claude's answer did not contain a list of clips.")
    clips = []
    for c in data["clips"]:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        ps = [p for p in (c.get("passages") or []) if isinstance(p, dict)
              and isinstance(p.get("start_sentence"), int) and isinstance(p.get("end_sentence"), int)]
        if ps:
            c["passages"] = [{"start_sentence": p["start_sentence"], "end_sentence": p["end_sentence"],
                              "role": "hook" if p.get("role") == "hook" else "body"} for p in ps]
        elif "start_sentence" in c and "end_sentence" in c:      # old analyses / plain answers
            c["passages"] = [{"start_sentence": c["start_sentence"], "end_sentence": c["end_sentence"],
                              "role": "body"}]
        else:
            continue
        c["score"] = int(c.pop("virality_score", c.get("score") or 0))
        c.setdefault("why_meaningful", c.get("reasoning", ""))
        if c.get("category") not in CATEGORIES:
            c["category"] = "application"
        c["hook_title"] = " ".join(str(c.get("hook_title") or c.get("title") or "").split())[:80]
        c["publish_title"] = " ".join(str(c.get("publish_title") or c.get("title") or "").split())[:100]
        c["broll"] = [{"sentence": int(b["sentence"]), "word": str(b.get("word", ""))[:40],
                       "query": " ".join(str(b.get("query", "")).split())[:100],
                       "keywords": [str(k)[:30] for k in (b.get("keywords") or [])][:8],
                       "seconds": float(b.get("seconds") or 3)}
                      for b in (c.get("broll") or [])[:3]
                      if isinstance(b, dict) and isinstance(b.get("sentence"), int)]
        clips.append(c)
    if not clips:
        raise LLMError("Claude did not return any usable clip.")
    data["clips"] = clips
    return data


# ---------- boundary maths (no LLM) ----------

def _span(sentences: list, a: int, b: int) -> float:
    return sentences[b]["e"] - sentences[a]["s"]


def clean_passages(raw: list, n: int, montage: bool = True) -> list:
    """[{start_sentence, end_sentence, role}] → [[a, b, role], ...] in play order: ids clamped, the body
    passages in sermon order (only a 'hook' may play first out of order), no overlaps, contiguous
    passages merged, at most MAX_PASSAGES."""
    ps = []
    for p in raw:
        try:
            a = min(max(int(p["start_sentence"]), 0), n - 1)
            b = min(max(int(p["end_sentence"]), a), n - 1)
        except (KeyError, TypeError, ValueError):
            continue
        ps.append([a, b, "hook" if p.get("role") == "hook" else "body"])
    if not ps:
        return []
    if not montage:  # one passage only: the longest
        a, b, _ = max(ps, key=lambda p: p[1] - p[0])
        return [[a, b, "body"]]
    first, rest = ps[0], sorted(ps[1:])
    if rest and not (first[2] == "hook" and first[0] > rest[0][0]):
        first, rest = None, sorted(ps)            # not an out-of-order hook: plain sermon order
    kept = []
    for p in rest:
        if kept and p[0] <= kept[-1][1] + 1:      # overlapping or contiguous: one passage
            kept[-1][1] = max(kept[-1][1], p[1])
        else:
            kept.append(p)
    if first is not None:
        if any(not (first[1] < q[0] or first[0] > q[1]) for q in kept):
            first = None                          # the hook repeats a body passage: drop it
        else:
            kept.insert(0, first)
    kept = kept[:MAX_PASSAGES]
    if len(kept) == 1:
        kept[0][2] = "body"
    return kept


def sanitize_clips(raw: list, sentences: list, opts: Options, keep_index: bool = False,
                   limit: int | None = None) -> list:
    """Clean the passages, enforce duration bounds by adding/removing whole sentences, drop clips
    that overlap a better one, keep the best `limit` clips (by score)."""
    n = len(sentences)
    dmin = opts.min_duration
    ranked = sorted(raw, key=lambda c: -(c.get("score") or 0)) if limit else list(raw)
    out, used = [], []
    for c in ranked:
        c = dict(c)
        try:
            raw_ps = c.get("passages") or [{"start_sentence": a, "end_sentence": b} for a, b in passages_of(c)]
        except (KeyError, TypeError, ValueError):
            continue
        ps = clean_passages(raw_ps, n, opts.montage)
        if not ps:
            continue
        dmax = opts.max_duration if len(ps) == 1 else max(opts.montage_max, opts.max_duration)

        def dur():
            return sum(_span(sentences, a, b) for a, b, _ in ps)

        def free(i):  # sentence i is not already in another passage of this clip
            return all(not (a <= i <= b) for a, b, _ in ps)

        while dur() > dmax * 1.15:                # too long: trim the tail of the longest passage
            p = max((p for p in ps if p[1] > p[0]), key=lambda p: _span(sentences, p[0], p[1]), default=None)
            if p is None:
                break
            p[1] -= 1
        grow, last = 0, ps[-1]
        while dur() < dmin * 0.85 and grow < 12:  # too short: extend the last passage
            if last[1] + 1 < n and free(last[1] + 1):
                last[1] += 1
            elif last[0] > 0 and free(last[0] - 1):
                last[0] -= 1
            grow += 1
        if any(not (b < ua or a > ub) for a, b, _ in ps for ua, ub in used):
            continue                              # overlaps a better clip
        used += [(a, b) for a, b, _ in ps]
        c["passages"] = [{"start_sentence": a, "end_sentence": b, "role": r} for a, b, r in ps]
        c["start_sentence"], c["end_sentence"] = min(p[0] for p in ps), max(p[1] for p in ps)
        c["approx_duration"] = round(dur(), 1)
        out.append(c)
        if limit and len(out) >= limit:
            break
    if not keep_index:
        for i, c in enumerate(out):
            c["index"] = i
    return out


def range_to_sentences(sentences: list, start, end) -> tuple[int, int]:
    """'12:30'..'13:45' → (first sentence id, last sentence id) covering that range."""
    s, e = parse_time(start), parse_time(end)
    if s is None or e is None or e <= s:
        raise ConfigError("Please enter a valid start and end time (e.g. 12:30 and 13:45).")
    a = next((x["id"] for x in sentences if x["e"] > s), None)
    b = next((x["id"] for x in reversed(sentences) if x["s"] < e), None)
    if a is None or b is None or b < a:
        raise ConfigError("No speech found between these times.")
    return a, b


def plain_analysis(title: str, a: int, b: int) -> dict:
    """Metadata without Claude (whole short video / manual range, no API key)."""
    return {"sermon_title": title, "sermon_summary": "", "main_scriptures": [],
            "clips": [{"title": title, "hook_title": title, "publish_title": title,
                       "category": "application", "secondary_categories": [],
                       "start_sentence": a, "end_sentence": b,
                       "passages": [{"start_sentence": a, "end_sentence": b, "role": "body"}], "hook": "", "core_message": "",
                       "reasoning": "", "why_meaningful": "", "scripture_refs": [],
                       "self_contained_check": "", "score": None, "highlight_words": [],
                       "emoji_cues": [], "post_caption": "", "hashtags": []}]}

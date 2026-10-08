"""Speech-to-text with word-level timestamps, then sentence segmentation.

Engines (same OpenAI Whisper model weights):
  * "faster" (default) – faster-whisper / CTranslate2: ~4× faster, lower memory, int8 on GPU.
  * "openai"           – the reference `openai-whisper` package (needs PyTorch installed).
"""
import json
import logging
import re
from pathlib import Path
from typing import Callable

from . import settings
from .errors import Cancelled, TranscriptionError
from .models import fmt_time

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]

# Biases Whisper toward correct spelling of common sermon vocabulary.
PROMPTS = {
    "fr": "Prédication chrétienne. Jésus-Christ, l'Évangile, les Écritures, la Bible, "
          "le Saint-Esprit, la repentance, le péché, la grâce, le salut, les disciples.",
    "en": "Christian sermon. Jesus Christ, the Gospel, the Scriptures, the Bible, "
          "the Holy Spirit, repentance, sin, grace, salvation, disciples.",
}

_models: dict = {}


def transcribe(audio: Path, duration: float, language: str = "auto", engine: str = "faster",
               model: str | None = None, progress: Progress | None = None,
               cancelled: Callable[[], bool] | None = None) -> dict:
    """Returns {"language", "words", "sentences"} (see fip.models for the format)."""
    progress = progress or (lambda f, m: None)
    cancelled = cancelled or (lambda: False)
    lang = None if not language or language == "auto" else language
    name = model or settings.WHISPER_MODEL
    if engine == "openai":
        raw, detected = _openai(audio, name, lang, duration, progress, cancelled)
    elif engine == "faster":
        raw, detected = _faster(audio, name, lang, duration, progress, cancelled)
    else:
        raise TranscriptionError(f"Unknown transcription engine '{engine}' (use 'faster' or 'openai').")
    words = merge_fragments(raw)
    if not words:
        raise TranscriptionError("No speech was detected in this file.")
    return {"language": detected, "words": words, "sentences": build_sentences(words)}


def _faster(audio, name, lang, duration, progress, cancelled):
    model = _faster_model(name)
    try:
        segments, info = model.transcribe(
            str(audio), language=lang, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}, beam_size=5,
            initial_prompt=PROMPTS.get(lang or ""), condition_on_previous_text=False)
        words = []
        for seg in segments:  # lazy generator: decoding happens while iterating
            if cancelled():
                raise Cancelled("Cancelled.")
            words += [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3),
                       "p": round(w.probability, 3)} for w in (seg.words or []) if w.word.strip()]
            if duration:
                progress(min(seg.end / duration, 1.0), f"Transcribing… {fmt_time(seg.end)} / {fmt_time(duration)}")
        return words, info.language
    except Cancelled:
        raise
    except Exception as e:
        raise TranscriptionError(f"Transcription failed: {e}") from e


def _faster_model(name: str):
    key = ("faster", name)
    if key in _models:
        return _models[key]
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise TranscriptionError("faster-whisper is not installed (pip install faster-whisper).") from e
    attempts = []
    if settings.WHISPER_DEVICE in ("auto", "cuda"):
        # Pascal GPUs (GTX 10xx) have no fast fp16: int8 is the quick path there.
        attempts += [("cuda", "int8_float16"), ("cuda", "int8"), ("cuda", "float32")]
    attempts.append(("cpu", "int8"))
    last = None
    for device, ctype in attempts:
        try:
            _models[key] = WhisperModel(name, device=device, compute_type=ctype)
            log.info("Whisper %s loaded on %s (%s)", name, device, ctype)
            return _models[key]
        except Exception as e:  # unsupported compute type / no CUDA → try the next one
            last = e
    raise TranscriptionError(f"Could not load Whisper model '{name}': {last}")


def _openai(audio, name, lang, duration, progress, cancelled):
    try:
        import torch
        import whisper
    except ImportError as e:
        raise TranscriptionError("The 'openai' engine needs: pip install openai-whisper torch") from e
    key = ("openai", name)
    if key not in _models:
        device = "cuda" if torch.cuda.is_available() and settings.WHISPER_DEVICE != "cpu" else "cpu"
        _models[key] = whisper.load_model(name, device=device)
    progress(0.05, "Transcribing (reference Whisper — no live progress)…")
    if cancelled():
        raise Cancelled("Cancelled.")
    try:
        result = _models[key].transcribe(str(audio), language=lang, word_timestamps=True,
                                         initial_prompt=PROMPTS.get(lang or ""),
                                         condition_on_previous_text=False, verbose=None)
    except Exception as e:
        raise TranscriptionError(f"Transcription failed: {e}") from e
    words = [{"w": w["word"].strip(), "s": round(w["start"], 3), "e": round(w["end"], 3),
              "p": round(w.get("probability", 1.0), 3)}
             for seg in result.get("segments", []) for w in seg.get("words", []) if w["word"].strip()]
    return words, result.get("language", lang or "")


# ---------- text post-processing ----------

def merge_fragments(words: list) -> list:
    """Glue Whisper sub-word pieces back on: "l" + "'Ancien" → "l'Ancien", "est" + "-ce" → "est-ce"."""
    out = []
    for w in words:
        t = w["w"]
        glue = (re.match(r"^['’]\w", t) or re.match(r"^-\w", t)        # elisions, "est-ce"
                or re.fullmatch(r"[»”\")\].,;:!?…]+", t))                 # stray closing punctuation
        if out and (glue or out[-1]["w"].endswith(("'", "’", "«", "“"))):
            prev = out[-1]
            prev["w"] += t
            prev["e"] = w["e"]
            prev["p"] = min(prev.get("p", 1), w.get("p", 1))
        else:
            out.append(dict(w))
    return out


_END = re.compile(r"[.!?…]+[»\"')\]]*$")
_SOFT = re.compile(r"[,;:]$")
_ABBREV = re.compile(r"(M|Mme|Dr|St|v|ch|cf|av|J\.-C)\.")


def build_sentences(words: list, max_words: int = 45) -> list:
    """Group words into sentences (punctuation, long pauses), indexing word ranges."""
    sentences, start = [], 0
    for i, w in enumerate(words):
        n = i - start + 1
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["s"] - w["e"]) if nxt else 99
        if (nxt is None
                or (_END.search(w["w"]) and not _ABBREV.fullmatch(w["w"]))
                or gap > 1.6
                or (n >= max_words and (_SOFT.search(w["w"]) or gap > 0.4))
                or n >= max_words * 2):
            seg = words[start:i + 1]
            sentences.append({"id": len(sentences), "w0": start, "w1": i,
                              "s": seg[0]["s"], "e": seg[-1]["e"], "text": join_words(seg)})
            start = i + 1
    return sentences


def join_words(ws: list) -> str:
    out = ""
    for w in ws:
        t = w["w"] if isinstance(w, dict) else w
        if out and not re.match(r"^[,.;:!?…)\]»]", t) and not out.endswith(("'", "’", "(", "«", "-")):
            out += " "
        out += t
    return out


def save(tr: dict, path: Path) -> None:
    path.write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")


def load(path: Path) -> dict:
    try:
        tr = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise TranscriptionError(f"Cannot read transcript {path}: {e}") from e
    if not tr.get("words") or not tr.get("sentences"):
        raise TranscriptionError(f"Transcript {path} is empty or invalid.")
    return tr

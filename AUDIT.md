# FastImpact Preach — audit of v1 and v2 rebuild

v1 is archived in `archive/v1/`. v2 splits the code into a reusable core package (`fip/`), a CLI
(`python -m fip`) and a thin web layer (`app/`). The web UI, REST API and existing projects are unchanged.

## 1. Audit findings (v1) and what v2 does about them

| # | Area | v1 finding | Severity | v2 |
|---|------|-----------|----------|----|
| 1 | Architecture | Pipeline logic, project storage, job queue and HTTP concerns mixed in `app/pipeline.py`; nothing usable without the web server | High | Core `fip/` package (no web deps) + `fip/cli.py`; `app/jobs.py` only stores projects and calls `fip.pipeline` |
| 2 | Concurrency | "Adjust" re-renders ran on an ad-hoc thread **in parallel** with the pipeline worker (two GPU encodes, racing writes to `project.json`) | High | All GPU work goes through one queue (`("pipeline", id)` / `("render", id, n)` jobs) |
| 3 | Picture | Full-screen slides were centre-cropped → slide text cut off (shorts #6, #9) | High | Slide detection (≥ 35 % near-white pixels for ≥ 1 s, calibrated on the real sermon) → whole frame over a blurred backdrop |
| 4 | Subtitles | Only one static highlighted word per chunk; spec asks for the *currently spoken* word | Medium | `highlight="active"`: spoken word green + grows; Claude keywords yellow; v1 style kept as `keyword` |
| 5 | Selection | Overlap/length fixes could leave **fewer than N** clips | Medium | Claude proposes N+2 candidates; best N kept after fixes |
| 6 | LLM robustness | Invalid JSON / truncated answer = hard failure; field names not validated | Medium | Structured output + shape validation, one retry, `max_tokens` doubling, typed `LLMError` with user-facing messages (workspace, credit, 401/403/429/5xx) |
| 7 | Errors | `RuntimeError`/`print`/`traceback` everywhere; failures in a clip aborted the whole run | Medium | `FipError` hierarchy with exit codes, `logging`, per-clip failure isolation |
| 8 | Cancellation | Cancel only checked between steps; FFmpeg kept running | Low | Cancellable subprocess runner (kills FFmpeg), checked during transcription |
| 9 | Validation | Options were a free dict; bad values could crash mid-pipeline | Medium | `fip.models.Options` (pydantic): ranges, enums, clamping, unknown keys ignored (old projects load) |
| 10 | Performance | Face detection 3×/s; slide layer would have tripled full-res filtering | Low | 2×/s sampling; blurred backdrop built at ¼ size (≈ 41 s per 45 s short incl. analysis, NVENC) |
| 11 | API | Deprecated `@app.on_event`; `retry?reanalyze` dead path | Low | FastAPI lifespan; dead code removed |
| 12 | Output | No machine-readable result | Medium | `clips.json` in the spec format (+ download button) |
| 13 | Tests | None | Medium | 29 pytest unit tests (`tests/`), no GPU/network needed |
| 14 | Security | Server bound to 127.0.0.1 ✓; key stored in `.env` in plain text; inherited proxy tokens ignored ✓ | Note | Unchanged. Keep `.env` out of version control (`.gitignore`) |

## 2. Spec compliance

| Requirement | Status | Notes |
|---|---|---|
| FFmpeg audio extraction | ✅ | 16 kHz mono PCM |
| Whisper, word-level JSON transcript | ✅ | `transcript.json`; engine `faster` (faster-whisper, same OpenAI Whisper weights, ~4× faster) or `openai` (reference package). The `openai` engine needs `pip install openai-whisper torch` and was not tested here |
| Full transcript → LLM, 30–60 s standalone clips, hook / emotion / narrative arc | ✅ | Spec criteria merged with the 5 spiritual criteria; default length 30–60 s |
| Structured JSON `start_time, end_time, title, virality_score, summary` (+ hook, reasoning) | ✅ | `clips.json` |
| "Claude 3.5 Sonnet / GPT-4o" | ⚠️ changed | Claude 3.5 Sonnet is retired; uses `claude-opus-5-5` (override with `--model` / `CLAUDE_MODEL`). Claude only (no GPT-4o) |
| MoviePy/FFmpeg slicing | ✅ FFmpeg | One FFmpeg pass per short; MoviePy not used (much slower, re-encodes per effect) |
| Speaker tracking or centre crop → 1080×1920 | ✅ | `--crop track|center` |
| ASS subtitles: current word bold/green + contextual emojis, burned with FFmpeg | ✅ | Emojis chosen by Claude, rendered as colour PNG pops (libass cannot draw colour emoji) |
| Modular, error handling, CLI arguments | ✅ | `python -m fip {transcribe,find,render,run} --help` |

### Deliberate deviation: sentence ids instead of raw timestamps
The spec's prompt sends the transcript JSON and asks for float `start_time`/`end_time`. v2 sends
numbered, timestamped **sentences** and Claude answers with sentence ids; the code converts them to
`start_time`/`end_time` from word timestamps (with padding that never cuts a neighbouring word).
Why: (1) every cut lands on a whole sentence — the "self-contained start and end" requirement;
(2) the prompt is ~5× smaller than word-level JSON (cheaper, faster); (3) no hallucinated or
mid-word timestamps. The public output is still the spec's float format.

# FastImpact Preach (v2)

Turn a long sermon video (~50 min) into **10 impactful vertical shorts**, chosen by Claude for
their spiritual weight and rendered in a bouncy "impact" subtitle style.

```
[Long video] ➔ [FFmpeg audio] ➔ [Whisper word-level transcript] ➔ [Claude clip selection + cut-point check]
             ➔ [FFmpeg: 9:16 reframing + animated ASS subtitles + emoji + end card] ➔ shorts + clips.json
```

See [AUDIT.md](AUDIT.md) for the audit of v1 and how v2 meets the specification.

## Two ways to use it

### Web app (Vizard-style UI)
1. Double-click `run.bat` (first launch creates `.venv` and installs `requirements.txt`), then open http://127.0.0.1:8000
2. **API key** button (top right): paste your Claude key (+ Workspace ID if the key is not scoped to a
   workspace). Stored in `.env` on this computer, shown masked afterwards.
3. Upload / paste a link / pick a file on this PC → follow the steps → play, download, copy post, **Adjust**
   cut points, **Prepare shorts** (1–10) again from the same transcript, download **JSON**.

### Command line
```bat
.venv\Scripts\python -m fip run sermon.mp4                          :: 10 shorts → .\shorts\sermon\
.venv\Scripts\python -m fip run sermon.mp4 -n 5 --min 30 --max 60 --highlight active --crop track
.venv\Scripts\python -m fip run sermon.mp4 -n 1 --start 12:30 --end 13:45
.venv\Scripts\python -m fip find sermon.mp4                         :: Claude selection → clips.json only
.venv\Scripts\python -m fip render sermon.mp4 --only 2,5             :: re-render from the cached selection
.venv\Scripts\python -m fip run --help                              :: all options
```
Every step is cached in the output folder (`transcript.json`, `analysis.json`, `refine.json`), so a rerun
resumes where it stopped; `--fresh` asks Claude again. Exit codes: 0 ok, 1 some clips failed, 2 config,
3 media, 4 transcription, 5 Claude, 6 render, 130 cancelled.

## Deploy on a server (Infomaniak Cloud VPS or any Debian/Ubuntu VPS)
Docker image (CPU: Whisper int8 + libx264) behind Caddy (automatic HTTPS), password login.
1. DNS: point an `A` record (e.g. `shorts.your-church.ch`) to the server IP.
2. Infomaniak Manager → your VPS → Firewall: open TCP **80** and **443**.
3. From this PC:
   ```powershell
   .\deploy\deploy.ps1 -Server <IP> -User debian -Key C:\path\to\private-key -Domain shorts.your-church.ch
   ```
   First run installs Docker, builds the image (~10 min) and generates a random team password:
   `ssh debian@<IP> "grep FIP_APP_PASSWORD ~/fastimpact/.env.server"`. Re-run the same command to update.
4. Open `https://<domain>`, sign in, then set the Claude API key with the **API key** button.

Server mode disables "Use a file on this PC", hides the API docs and requires the password for every page,
API call and media file. Data (projects, Whisper model, Claude key) lives in the `fip-data` Docker volume.

## Output: `clips.json`
```json
{ "sermon_title": "…", "sermon_summary": "…", "main_scriptures": ["Matthieu 23:23"],
  "clips": [ { "index": 0, "start_time": 2410.0, "end_time": 2601.4, "segments": [[2580.1, 2601.4], [2410.0, 2440.2]], "title": "…", "hook_title": "…", "publish_title": "…", "hook": "…",
               "virality_score": 93, "summary": "…", "reasoning": "…", "category": "application",
               "scripture_refs": ["2 Corinthiens 9:7"], "post_caption": "…", "hashtags": ["#Bible"],
               "video": "clip_01.mp4" } ] }
```

## Options (web *Clip settings* = CLI flags)
| Option | Values (default) |
|---|---|
| Number of clips | 1–10 (**10**). With 1: a video < 3 min is used whole; for a longer one: ask / Claude picks / `--start --end` |
| Clip length | **30–60 s** |
| Caption style | **eeig** (sentence case, light-blue box on the spoken word, no emojis) · impact (UPPERCASE, bouncy, green word, emojis) — CLI `--captions` |
| Framing | **track** (follow the speaker) · center |
| Slides | **fit** (whole slide over blurred backdrop) · crop |
| Montage | **on**, max **90 s** — Claude may join 2–4 passages of the sermon in one short (a hook line first, then the development in sermon order; or one idea developed in several places). Passages are editable or removable in Adjust — CLI `--no-montage`, `--montage-max` |
| B-roll | **on** — up to 3 cutaway shots per short (2–4 s) on concrete words Claude picks ("menthe" → mint leaves). Your church's **B-roll library** (Clip settings → B-roll library, with keywords) is used first, then **Pixabay** (free key: pixabay.com/api/docs, saved with the API key button as `PIXABAY_API_KEY`). Review, change the search, pick a library clip, take another result, add or remove shots in Adjust. Credits are listed on each card and in `clips.json` |
| Hook title | **first 15 s** · whole short · off — Claude writes a short hook (e.g. "La dîme : obligation ou joie ?"), editable per short in Adjust — CLI `--hook-title hook\|full\|off`, `--title-seconds` |
| Punch zooms, emojis, end card, watermark, brand/tagline | on, off, on, —, Eglise Evangélique Internationale de Genève |
| End card logo | EEIG logo (`assets/brand/logo.png`) on a white tile; change it in Clip settings → End card logo |
| Transcription engine | **faster** (faster-whisper) · openai (reference `openai-whisper`, CLI `--engine`) |
| `.env` | `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID`, `CLAUDE_MODEL` (claude-opus-5-5), `CLAUDE_EFFORT` (high), `WHISPER_MODEL` (large-v3-turbo), `WHISPER_DEVICE` |

## Layout
```
fip/                 core library (no web dependencies)
  models.py          Options (validated), time helpers, public clip JSON
  ffmpeg.py          cancellable ffmpeg/ffprobe runner, probe, audio, encoder (NVENC → x264)
  transcribe.py      Whisper engines → words → sentences
  segment.py         Claude prompts/schemas, selection, cut-point refinement, boundary maths
  reframe.py         face tracking, slide detection, crop plan
  subtitles.py       ASS 'impact' template (active word / keyword), emoji pops, SRT
  render.py          single-pass FFmpeg render + end card
  pipeline.py        cached steps shared by CLI and web
  cli.py             python -m fip …
app/                 FastAPI server: main.py (routes), jobs.py (projects + single GPU queue)
static/              web UI
tests/               pytest unit tests:  .venv\Scripts\python -m pytest tests
archive/v1/          previous version
```

Requirements: Python 3.11, FFmpeg on PATH, NVIDIA GPU recommended (CPU works, slower).
The first transcription downloads the Whisper model (~1.6 GB).

# AI Recorder for Meetings

A local, portable meeting recorder: live Italian subtitles on an external display,
deferred high-quality transcription, and final documents generated locally and
shared manually by a person (human in the loop). No subscription is required, and
the core workflow works without internet access.

## Features

- Live subtitles through Vosk streaming speech recognition.
- Accurate, delayed transcription through Whisper / faster-whisper.
- Optional OpenAI-compatible LLM for structured minutes; disabled by default.
- PDF, DOCX, Markdown, and Mermaid diagrams, each generated only when requested.
- A browser-based display page designed for a second screen or TV.
- Saved meetings remain available after a reboot, so transcription and document
  generation can be done independently at any later time.

## Installation

From the project directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`av` is pinned to 18.1.1 because 19.x removed an argument still used by
`faster-whisper` 1.2.1. `mermaid.min.js` is bundled in `static/vendor/`, so the
live map works even without internet.

### Download the speech models

The speech models are intentionally not stored in GitHub: the full Italian
Vosk model is about 1.2 GB and the Whisper model is several GB. After creating
`.venv`, run:

```bash
.venv/bin/python scripts/download_models.py
```

This downloads the official `vosk-model-it-0.22` model from
<https://alphacephei.com/vosk/models>, installs it in
`models/vosk-model-it-0.22`, and preloads faster-whisper `medium` into the
local Hugging Face cache. The command is safe to run again: completed models
are reused, and an interrupted Vosk archive is resumed when possible.

For a lighter live model (about 48 MB, lower accuracy), use:

```bash
.venv/bin/python scripts/download_models.py --vosk-model small
```

This also writes `RIUNIONI_VOSK_MODEL` to the ignored local `.env`, so the
server automatically uses the selected model without editing tracked files.
The script must be run before starting the server. The server does not download
models automatically, so startup remains predictable and can work offline once
the models are installed. The Vosk model is loaded before capture starts, so
loading time does not end up in the transcript.

## Startup

```bash
.venv/bin/python server.py
```

Open <http://localhost:8000/display> in a browser, move the window to the
external display, and switch to fullscreen (`F11`).

- **Top bar**: a `Light` / `Dark` theme toggle (remembered by the browser), then
  `Start` and `Stop and save`. Stopping only ends the recording and saves
  `audio.wav`, `riunione.json`, and `trascrizione.txt` — nothing else.
- **Three dropdown menus**, placed below the top bar so each workflow stays
  separate:
  - `Base meetings` — every saved meeting (audio + live Vosk transcript);
  - `Accurate` — meetings with Whisper transcription, with a time estimate;
  - `Documents` — meetings with generated files (PDF/DOCX/Markdown/diagrams).

  A meeting can appear in more than one menu: one with an accurate transcript
  and documents appears in all three. Selecting an item only changes the
  selection — no panel opens, so you can keep switching meetings. `Fast transcript`
  then shows the matching viewer (the live transcript by default, documents when
  the item was chosen from `Documents` or the transcript is empty).
- **Action bar**: above the dropdown menus, with the selected meeting, a single
  `Fast transcript` button, `Generate all documents`, and
  `Generate accurate transcription` (which starts a job, it does not open a panel).
- **Viewers**: the transcript and document panels are full-screen overlays above
  the bars, with a `Close` button (or `Esc`) that is always clickable. The
  document viewer has separate buttons — `Generate Markdown`, `Generate PDF`,
  `Generate DOCX`, `Generate diagrams` — so only the requested file type is
  produced when clicked.

### Transcription and documents are separate actions

- `Generate accurate transcription` runs Whisper on the saved audio and rewrites only
  `trascrizione.txt` / `trascrizione.json`. It also keeps the original live
  transcript as `trascrizione-live.txt`. It **never** generates or deletes
  documents, and it works on past meetings even if documents already exist.
- `Generate Markdown` / `Generate PDF` / `Generate DOCX` / `Generate diagrams`
  produce only that file type from the current transcript and JSON. Nothing is
  generated automatically: use the buttons in the document viewer, or
  `Generate all documents` in the action bar to create every type at once.
  Both can be run at any time, before or after an accurate transcription.

Both actions work after a restart, with no meeting in progress, and offline
(except for the first `npx`/Chromium download for diagrams). If the machine is
shut down while a job is running, the job status is lost: simply run the action
again — already saved files remain intact.

The time estimate shown before an accurate transcription is computed as
`audio_duration × accurate_speed_factor + accurate_overhead_seconds`, for
example `about 1 min 31 s`. Both parameters are in `config.yaml` under
`finalization` (defaults `1.3` and `10`). Since it is an average rate, treat it
as an indication: system load and model size can make it vary.

Meeting IDs use a readable timestamp, `YYYYMMDD-HH:MM:SS` (for example
`20261121-19:00:27`). Older folders were renamed to the same format.

## API

| Method | Endpoint | Action |
|---|---|---|
| GET | `/health` | quick check that the server responds |
| GET | `/api/status` | current status + background jobs |
| GET | `/api/meetings` | saved meetings, durations, estimates, documents |
| GET | `/api/jobs` | status of background jobs |
| GET | `/api/transcript?meeting_id=…` | text of the saved transcript |
| GET | `/api/document?meeting_id=…&file=…` | open/download a generated document |
| GET | `/api/devices` | list microphones |
| POST | `/api/start` | start capture + live STT + optional LLM |
| POST | `/api/stop` | save audio + JSON + transcript (no documents) |
| POST | `/api/finalize` | generate all document types for a meeting |
| POST | `/api/generate-document` | generate one document type (`kind`: `md`, `pdf`, `docx`, `diagrams`) |
| POST | `/api/transcribe-accurate` | Whisper transcription only |
| POST | `/api/finalize-accurate` | backwards-compatible alias of the above |

These POST endpoints accept an optional JSON body:

```bash
curl -X POST -H 'Content-Type: application/json' \
  -d '{"meeting_id":"20261121-17:27:52","kind":"pdf"}' \
  http://localhost:8000/api/generate-document

curl -X POST -H 'Content-Type: application/json' \
  -d '{"meeting_id":"20261121-17:27:52"}' \
  http://localhost:8000/api/finalize

curl -X POST -H 'Content-Type: application/json' \
  -d '{"meeting_id":"20261121-17:27:52","model":"medium"}' \
  http://localhost:8000/api/transcribe-accurate
```

To list microphones from a terminal: `.venv/bin/python list_mics.py`.

## Configuration

Everything is configured in `config.yaml`. Quick overrides via environment:

```bash
export RIUNIONI_LLM_BASE_URL="http://localhost:8080/v1"
export RIUNIONI_LLM_API_KEY="your-api-key"
export RIUNIONI_LLM_MODELS="model-1,model-2"   # retry in order
export RIUNIONI_AUDIO_DEVICE="hw:2,0"          # example USB mic
.venv/bin/python server.py
```

Alternatively, copy `.env.example` to `.env` and fill it in: variables are read
automatically at startup (precedence: environment > `.env` > YAML).

If `base_url` or `models` are empty, the LLM stays disabled: **subtitles keep
working**, and the generated documents contain the transcript with a placeholder
summary.

## Output files

`output/<YYYYMMDD-HH:MM:SS>` contains, right after `Stop and save`:

- `audio.wav` — raw recording, always saved;
- `riunione.json` — structured minutes (regenerable without re-running the AI);
- `trascrizione.txt` — live transcript.

After `Generate accurate transcription`:

- `trascrizione.json` — Whisper segments with timestamps;
- `trascrizione.txt` — replaced by the accurate version;
- `trascrizione-live.txt` — copy of the original live transcript.

After `Generate documents`:

- `documenti/verbale.pdf`, `verbale.docx`, `verbale.md`, `mappa-mentale.*`, `flusso.*`.

Saved meetings are discovered automatically from folders containing `audio.wav`,
so there is no need to keep the live session open to work on them later.

For accurate transcription from the command line:

```bash
.venv/bin/python stt_final.py --meeting output/20261121-16:30:00 --model medium
```

This rewrites the transcript only. Add `--finalize` to also generate documents.

Mermaid diagrams are rendered to SVG/PNG via `npx` (internet needed the first
time); without `npx`, the `.mmd` files remain and can be opened in any Mermaid
editor. A person collects the finished files and sends them manually, for
example via WhatsApp: no sending automation or public page.

## Tests and benchmarks

```bash
.venv/bin/python tests/benchmark_stt.py tests/test_5min_16k.wav
```

The sample is synthetic Italian generated with `espeak-ng`: it compares models
on the same audio and does not replace a test with real microphone audio.

Live subtitles use **Vosk streaming** because short Whisper chunks have high and
irregular latency, which makes the subtitle lag grow over time. Vosk emits
partial results quickly and confirmed sentences shortly after each sentence
ends. The final document always uses the Whisper re-transcription, not the live
subtitles, regardless of the live model.

Model weights are downloaded on first use and then cached locally. Generation
speed depends on the machine, the model size, and the system load; the estimate
in `/api/meetings` is based on the configured factors in `config.yaml`.

The lag displayed in `/display` uses a monotonic clock, so it does not jump if
the machine sleeps or the system clock is corrected.

## Privacy

Recording laws vary by jurisdiction. Always announce at the start of a meeting
that it is being recorded, and obtain participants' consent before sharing the
minutes. Because sharing is manual, final control stays with the person sending
the files.

## Task status

- [x] Model benchmarks and tests
- [x] Audio capture to raw WAV
- [x] Live streaming STT via Vosk + WebSocket
- [x] FastAPI server + `/display` page
- [x] Deferred generation: transcription and documents are independent actions
- [x] Optional LLM loop with retry and graceful degradation
- [x] Display with map, decisions, and actions
- [x] PDF/DOCX/Markdown/Mermaid document generation
- [x] Accurate re-transcription without touching existing documents
- [x] Clickable document viewer + re-transcription time estimate
- [ ] USB microphone setup and on-site test with a real microphone
- [ ] End-to-end test with a real meeting

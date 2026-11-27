"""FastAPI server: capture, live subtitles and /display page for the TV.

Startup:
    .venv/bin/python server.py
Then open http://localhost:8000/display on the extended display (HDMI).
"""

from __future__ import annotations

import asyncio
import copy
import json
import queue
import re
import threading
import time
import wave
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import uvicorn
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from capture import AudioCapture, list_input_devices
from config import load_config
from llm_loop import LLMLoop
from state import MeetingState
from stt_live import LiveTranscriber

cfg = load_config()
ROOT = Path(cfg["_root"])

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    (ROOT / cfg["paths"]["output_dir"]).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Meeting recorder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
clients: set[WebSocket] = set()
_main_loop: asyncio.AbstractEventLoop | None = None

state: MeetingState | None = None
capture: AudioCapture | None = None
transcriber: LiveTranscriber | None = None
llm: LLMLoop | None = None
_accurate_thread: threading.Thread | None = None

# Finalization jobs survive the live state: after a PC restart
# you can generate a meeting saved the day before.
jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_VALID_MEETING_ID = re.compile(
    r"^(?:[0-9]{8}-[0-9]{6}|[0-9]{8}-[0-9]{2}:[0-9]{2}:[0-9]{2})$"
)


def _broadcast(payload: dict[str, Any]) -> None:
    if _main_loop is None:
        return
    asyncio.run_coroutine_threadsafe(_send_all(payload), _main_loop)


async def _send_all(payload: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for client in list(clients):
        try:
            await client.send_json(payload)
        except Exception:
            dead.append(client)
    for client in dead:
        clients.discard(client)


def _push_state() -> None:
    if state is not None:
        _broadcast({"type": "state", "data": state.snapshot()})


def _on_segment(segment: dict[str, Any]) -> None:
    _broadcast({"type": "segment", "data": segment})


def _on_llm_result(result: dict[str, Any]) -> None:
    _broadcast({"type": "result", "data": result})


def _on_partial(text: str) -> None:
    _broadcast({"type": "partial", "text": text})


def _send_jobs() -> None:
    _broadcast({"type": "jobs", "data": _list_jobs()})


def _list_jobs() -> list[dict[str, Any]]:
    with _jobs_lock:
        return sorted(
            (dict(job) for job in jobs.values()),
            key=lambda item: item.get("started_at", 0),
            reverse=True,
        )


def _job_start(kind: str, meeting_id: str) -> str:
    key = f"{kind}:{meeting_id}"
    with _jobs_lock:
        jobs[key] = {
            "kind": kind,
            "meeting_id": meeting_id,
            "status": "in progress",
            "started_at": time.time(),
            "finished_at": None,
            "log": [],
            "documenti": [],
        }
    _send_jobs()
    return key


def _job_log(key: str, message: str) -> None:
    with _jobs_lock:
        job = jobs.get(key)
        if job is None:
            return
        job["log"] = (job["log"] + [f"{time.strftime('%H:%M:%S')}  {message}"])[-30:]
    _send_jobs()


def _job_finish(key: str, status: str, **extra: Any) -> None:
    with _jobs_lock:
        job = jobs.get(key)
        if job is None:
            return
        job["status"] = status
        job["finished_at"] = time.time()
        job.update(extra)
    _send_jobs()


def _wav_duration_seconds(path: Path) -> int:
    """Reads only the WAV header: fast even with hours-long recordings."""
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            return int(round(handle.getnframes() / rate)) if rate else 0
    except (OSError, wave.Error):
        return 0


def _accurate_estimate_seconds(duration: int) -> int:
    """Estimate based on configured factors: audio duration plus model load."""
    fin = cfg.get("finalization", {})
    factor = float(fin.get("accurate_speed_factor", 1.3))
    overhead = float(fin.get("accurate_overhead_seconds", 10))
    return max(1, int(duration * factor + overhead + 0.5))


def format_estimate(seconds: int) -> str:
    seconds = max(1, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if not minutes:
        return f"about {secs} s"
    if not secs:
        return f"about {minutes} min"
    return f"about {minutes} min {secs} s"


def _meeting_dirs() -> list[Path]:
    base = ROOT / cfg["paths"]["output_dir"]
    if not base.exists():
        return []
    return sorted(
        (path for path in base.iterdir() if path.is_dir() and (path / "audio.wav").exists()),
        key=lambda path: path.name,
        reverse=True,
    )


def _resolve_meeting_dir(meeting_id: str | None, allow_live: bool = True) -> tuple[str, Path]:
    if not meeting_id and allow_live and state is not None:
        meeting_id = state.meeting_id
    if not meeting_id:
        saved = _meeting_dirs()
        if not saved:
            raise FileNotFoundError("no saved meeting")
        meeting_id = saved[0].name
    if not _VALID_MEETING_ID.match(meeting_id):
        raise ValueError(f"invalid meeting id: {meeting_id}")
    meeting_dir = ROOT / cfg["paths"]["output_dir"] / meeting_id
    if not (meeting_dir / "audio.wav").exists():
        raise FileNotFoundError(f"raw audio missing for {meeting_id}")
    return meeting_id, meeting_dir


def _meeting_busy(meeting_id: str) -> bool:
    with _jobs_lock:
        return any(
            item["meeting_id"] == meeting_id and item["status"] == "in progress"
            for item in jobs.values()
        )


def _sync_live_status(meeting_id: str, status: str, message: str) -> None:
    """Updates the live status only if the meeting being processed is the current one."""
    if state is not None and state.meeting_id == meeting_id:
        state.set_status(status)
        state.add_log(message)
        _push_state()


def _build_result() -> dict[str, Any]:
    assert state is not None
    result = dict(state.result)
    result.setdefault("titolo", f"Meeting minutes {state.meeting_id}")
    result.setdefault("data", datetime.now().strftime("%Y-%m-%d"))
    result.setdefault("partecipanti", [])
    result.setdefault("riassunto", "(summary unavailable: LLM not configured or unreachable)")
    result.setdefault("decisioni", [])
    result.setdefault("azioni", [])
    result.setdefault("argomenti", [])
    result.setdefault("mindmap_mermaid", "")
    result.setdefault("flusso_mermaid", "")
    return result


@app.get("/display", response_class=HTMLResponse)
async def display() -> HTMLResponse:
    html = (ROOT / "static" / "display.html").read_text(encoding="utf-8")
    # No cache: page fixes must appear right after a restart.
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    if state is None:
        return {"status": "idle", "meeting_id": None, "jobs": _list_jobs()}
    snapshot = state.snapshot()
    snapshot["jobs"] = _list_jobs()
    return snapshot


@app.get("/api/meetings")
async def api_meetings() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for meeting_dir in _meeting_dirs():
        documents: list[str] = []
        docs_dir = meeting_dir / "documenti"
        if docs_dir.exists():
            documents = sorted(
                str(path.relative_to(meeting_dir))
                for path in docs_dir.rglob("*")
                if path.is_file()
            )
        transcript_path = meeting_dir / "trascrizione.txt"
        transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
        duration = _wav_duration_seconds(meeting_dir / "audio.wav")
        size = sum(path.stat().st_size for path in meeting_dir.rglob("*") if path.is_file())
        items.append(
            {
                "meeting_id": meeting_dir.name,
                "has_audio": (meeting_dir / "audio.wav").exists(),
                "has_result": (meeting_dir / "riunione.json").exists(),
                "has_transcript": transcript_path.exists(),
                "has_accurate": (meeting_dir / "trascrizione.json").exists(),
                "documents": documents,
                "transcript_chars": len(transcript.strip()),
                "duration_seconds": duration,
                "accurate_estimate_seconds": _accurate_estimate_seconds(duration),
                "size_mb": round(size / 1_000_000, 1),
            }
        )
    return {"meetings": items}


@app.get("/api/jobs")
async def api_jobs() -> dict[str, Any]:
    return {"jobs": _list_jobs()}


@app.get("/api/transcript")
async def api_transcript(meeting_id: str | None = None) -> JSONResponse:
    """Returns the saved transcript of a meeting, live or accurate."""
    try:
        meeting_id, meeting_dir = _resolve_meeting_dir(meeting_id)
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    path = meeting_dir / "trascrizione.txt"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return JSONResponse(
        {
            "meeting_id": meeting_id,
            "accurata": (meeting_dir / "trascrizione.json").exists(),
            "trascrizione": text,
        }
    )


@app.get("/api/document")
async def api_document(meeting_id: str | None = None, file: str | None = None):
    """Serves an already generated document (PDF, DOCX, MD, JSON, SVG...)."""
    try:
        meeting_id, meeting_dir = _resolve_meeting_dir(meeting_id)
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if not file:
        return JSONResponse({"error": "file not specified"}, status_code=400)

    # Accepts both the path relative to the meeting ("documenti/verbale.pdf")
    # and just the file name ("verbale.pdf"), but always inside the folder.
    candidate = meeting_dir / file
    if not candidate.exists():
        candidate = meeting_dir / "documenti" / file
    try:
        resolved = candidate.resolve()
        resolved.relative_to(meeting_dir.resolve())
    except (ValueError, OSError):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not resolved.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    # PDF/images/text open directly in a new tab; the others
    # (DOCX...) download with the correct name instead of "document".
    viewable = {".pdf", ".md", ".txt", ".json", ".svg", ".png", ".jpg", ".jpeg", ".html"}
    disposition = "inline" if resolved.suffix.lower() in viewable else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{resolved.name}"'}
    return FileResponse(str(resolved), headers=headers)


@app.get("/api/devices")
async def api_devices() -> dict[str, Any]:
    return {"devices": list_input_devices(), "configured": cfg["audio"].get("device")}


@app.post("/api/start")
async def api_start() -> JSONResponse:
    global state, capture, transcriber, llm
    if state is not None and state.status == "live":
        return JSONResponse({"error": "meeting already in progress"}, status_code=409)

    while not audio_queue.empty():
        audio_queue.get_nowait()

    meeting_id = datetime.now().strftime("%Y%m%d-%H:%M:%S")
    state = MeetingState(
        meeting_id=meeting_id,
        output_dir=str(ROOT / cfg["paths"]["output_dir"] / meeting_id),
    )
    state.set_status("starting")
    state.add_log(f"Meeting {meeting_id} created")

    engine = cfg["stt_live"].get("engine", "faster_whisper")
    if engine == "vosk":
        from stt_live import VoskTranscriber

        transcriber = VoskTranscriber(cfg, state, audio_queue, _on_segment, _on_partial)
    else:
        transcriber = LiveTranscriber(cfg, state, audio_queue, _on_segment, _on_partial)
    try:
        transcriber.warmup()
    except Exception as exc:
        state.set_status("error-stt")
        state.add_log(f"Unable to load the STT model: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=500)

    capture = AudioCapture(cfg, state, audio_queue)
    try:
        audio_path = capture.start()
        state.add_log(f"Raw audio: {audio_path}")
    except Exception as exc:
        state.set_status("error-audio")
        state.add_log(f"Unable to start the microphone: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=500)

    transcriber.start()
    llm = LLMLoop(cfg, state, _on_llm_result)
    llm.start()
    state.set_status("live")

    _push_state()
    return JSONResponse({"meeting_id": meeting_id, "status": "live"})


@app.post("/api/stop")
async def api_stop() -> JSONResponse:
    global state, transcriber, llm, capture
    if state is None:
        return JSONResponse({"error": "no meeting started"}, status_code=400)

    if llm is not None and llm.enabled:
        llm.refresh_now()
    if transcriber is not None:
        transcriber.stop()
    if llm is not None:
        llm.stop()
    if capture is not None:
        capture.stop()

    meeting_dir = ROOT / cfg["paths"]["output_dir"] / state.meeting_id
    meeting_dir.mkdir(parents=True, exist_ok=True)

    result = _build_result()
    (meeting_dir / "riunione.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (meeting_dir / "trascrizione.txt").write_text(
        state.transcript_text() + "\n", encoding="utf-8"
    )

    state.set_result(result)
    state.set_status("saved")
    state.add_log(
        "Audio and transcript saved. Documents are generated later "
        "with the 'Generate documents' button (even after a PC restart)."
    )
    _push_state()

    return JSONResponse(
        {
            "meeting_id": state.meeting_id,
            "output_dir": str(meeting_dir),
            "documenti": [],
            "segmenti_live": len(state.segments),
            "nota": "Recording closed: documents not generated yet",
        }
    )


def _ensure_result(meeting_dir: Path, meeting_id: str) -> None:
    """Recreates riunione.json if missing (e.g. meeting interrupted before stop)."""
    if (meeting_dir / "riunione.json").exists():
        return
    transcript_path = meeting_dir / "trascrizione.txt"
    transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    result = {
        "titolo": f"Meeting minutes {meeting_id}",
        "data": datetime.now().strftime("%Y-%m-%d"),
        "partecipanti": [],
        "riassunto": "(summary unavailable: LLM not configured or unreachable)",
        "decisioni": [],
        "azioni": [],
        "argomenti": [],
        "mindmap_mermaid": "",
        "flusso_mermaid": "",
    }
    (meeting_dir / "riunione.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not transcript:
        (meeting_dir / "trascrizione.txt").write_text("", encoding="utf-8")


def _finalize_worker(meeting_id: str, key: str, render_diagrams: bool) -> None:
    try:
        from finalize import finalize

        meeting_dir = ROOT / cfg["paths"]["output_dir"] / meeting_id
        _job_log(key, "Document generation started")
        _sync_live_status(meeting_id, "finalizing", "Document generation started")
        _ensure_result(meeting_dir, meeting_id)
        manifest = finalize(meeting_dir, cfg, render_diagrams=render_diagrams)
        _job_log(key, "Documents ready: " + ", ".join(manifest["documenti"]))
        _job_finish(key, "completed", documenti=manifest["documenti"])
        _sync_live_status(meeting_id, "finalized", f"Documents ready in {meeting_dir}")
    except Exception as exc:
        _job_log(key, f"Error: {exc}")
        _job_finish(key, "error", error=str(exc))
        _sync_live_status(meeting_id, "error-finalization", f"Finalization failed: {exc}")


def _document_worker(meeting_id: str, key: str, kind: str, render_diagrams: bool) -> None:
    """Generates only the requested document type, never the others."""
    try:
        from finalize import generate_document

        meeting_dir = ROOT / cfg["paths"]["output_dir"] / meeting_id
        _job_log(key, f"Generating {kind} document")
        _sync_live_status(meeting_id, "finalizing", f"Generating {kind} document")
        _ensure_result(meeting_dir, meeting_id)
        manifest = generate_document(meeting_dir, cfg, kind, render_diagrams=render_diagrams)
        _job_log(key, "Generated: " + ", ".join(manifest["documenti"]))
        _job_finish(key, "completed", documenti=manifest["documenti"], kind=kind)
        _sync_live_status(meeting_id, "finalized", f"{kind} document ready")
    except Exception as exc:
        _job_log(key, f"Error: {exc}")
        _job_finish(key, "error", error=str(exc))
        _sync_live_status(meeting_id, "error-finalization", f"Document generation failed: {exc}")


def _accurate_worker(meeting_id: str, key: str, model: str | None) -> None:
    """Transcription only: documents are generated separately by /api/finalize."""
    try:
        from stt_final import transcribe_final

        meeting_dir = ROOT / cfg["paths"]["output_dir"] / meeting_id
        run_cfg = copy.deepcopy(cfg)
        if model:
            run_cfg["stt_final"]["model"] = model
        estimate = _accurate_estimate_seconds(_wav_duration_seconds(meeting_dir / "audio.wav"))
        estimate_text = format_estimate(estimate)
        _job_log(key, f"Accurate transcription started · {estimate_text}")
        _sync_live_status(
            meeting_id, "accurate-transcription", f"Accurate transcription started · {estimate_text}"
        )
        # Keep the live transcript before Whisper overwrites trascrizione.txt.
        live = meeting_dir / "trascrizione.txt"
        backup = meeting_dir / "trascrizione-live.txt"
        if live.exists() and not backup.exists():
            backup.write_bytes(live.read_bytes())
            _job_log(key, "Live transcript kept as trascrizione-live.txt")
        payload = transcribe_final(meeting_dir, run_cfg)
        count = len(payload["segments"])
        _job_log(key, f"Transcribed {count} segments from {payload['duration']:.0f}s of audio")
        transcript = (meeting_dir / "trascrizione.txt").read_text(encoding="utf-8")
        try:
            from llm_loop import refresh_result_from_transcript

            result_path = meeting_dir / "riunione.json"
            previous = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            refreshed = refresh_result_from_transcript(run_cfg, transcript, previous)
            if refreshed != previous:
                result_path.write_text(
                    json.dumps(refreshed, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                _job_log(key, "Meeting summary and diagrams refreshed from accurate transcript")
        except Exception as exc:
            _job_log(key, f"Structured refresh skipped: {exc}")
        _job_log(key, "Documents not touched: use 'Generate documents' when ready")
        _job_finish(key, "completed", segmenti=count)
        _sync_live_status(
            meeting_id, "transcribed-accurate", f"Accurate transcription ready: {count} segments"
        )
    except Exception as exc:
        _job_log(key, f"Error: {exc}")
        _job_finish(key, "error", error=str(exc))
        _sync_live_status(meeting_id, "error-transcription", f"Transcription failed: {exc}")


def _payload_meeting_id(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    value = payload.get("meeting_id")
    return str(value) if value else None


def _bool_option(payload: dict[str, Any] | None, name: str, default: bool) -> bool:
    value = payload.get(name) if payload else None
    if value is None:
        fin = cfg.get("finalization", {})
        return bool(fin.get(name, default))
    return str(value).lower() in ("1", "true", "yes", "on")


@app.post("/api/finalize")
async def api_finalize(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
    """Generates documents from a saved meeting, even without live state."""
    try:
        meeting_id, _meeting_dir = _resolve_meeting_dir(_payload_meeting_id(payload))
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if _meeting_busy(meeting_id):
        return JSONResponse({"error": "job already in progress for this meeting"}, status_code=409)

    render = _bool_option(payload, "render_diagrams", True)
    key = _job_start("finalize", meeting_id)
    threading.Thread(
        target=_finalize_worker, args=(meeting_id, key, render), daemon=True
    ).start()
    return JSONResponse(
        {"status": "generation started", "meeting_id": meeting_id, "job": key}
    )


@app.post("/api/generate-document")
async def api_generate_document(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
    """Generates one document type on request; nothing is generated automatically."""
    try:
        meeting_id, _meeting_dir = _resolve_meeting_dir(_payload_meeting_id(payload))
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    kind = str((payload or {}).get("kind", "")).strip().lower()
    from finalize import DOCUMENT_KINDS
    if kind not in DOCUMENT_KINDS:
        return JSONResponse({"error": f"unknown document kind: {kind}"}, status_code=400)
    if _meeting_busy(meeting_id):
        return JSONResponse({"error": "job already in progress for this meeting"}, status_code=409)
    render = _bool_option(payload, "render_diagrams", False)
    key = _job_start("finalize", meeting_id)
    threading.Thread(
        target=_document_worker, args=(meeting_id, key, kind, render), daemon=True
    ).start()
    return JSONResponse(
        {"status": "generation started", "meeting_id": meeting_id, "kind": kind, "job": key}
    )


@app.post("/api/transcribe-accurate")
@app.post("/api/finalize-accurate")  # backwards-compatible alias: transcription only
async def api_finalize_accurate(
    payload: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """Whisper transcription only; documents are generated by /api/finalize."""
    global _accurate_thread
    try:
        meeting_id, _meeting_dir = _resolve_meeting_dir(_payload_meeting_id(payload))
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if _meeting_busy(meeting_id):
        return JSONResponse({"error": "job already in progress for this meeting"}, status_code=409)

    model = None
    if payload and payload.get("model"):
        model = str(payload["model"])
    elif cfg.get("finalization", {}).get("accurate_model"):
        model = str(cfg["finalization"]["accurate_model"])
    estimate = _accurate_estimate_seconds(_wav_duration_seconds(_meeting_dir / "audio.wav"))
    key = _job_start("accurate", meeting_id)
    _accurate_thread = threading.Thread(
        target=_accurate_worker, args=(meeting_id, key, model), daemon=True
    )
    _accurate_thread.start()
    return JSONResponse(
        {
            "status": "transcription started",
            "meeting_id": meeting_id,
            "job": key,
            "accurate_estimate_seconds": estimate,
            "estimate": format_estimate(estimate),
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    try:
        if state is not None:
            await websocket.send_json({"type": "state", "data": state.snapshot()})
        await websocket.send_json({"type": "jobs", "data": _list_jobs()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)


def main() -> None:
    uvicorn.run(app, host=cfg["display"]["host"], port=int(cfg["display"]["port"]))


if __name__ == "__main__":
    main()

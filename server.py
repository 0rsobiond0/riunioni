"""Server FastAPI: cattura, sottotitoli live e pagina /display per la TV.

Avvio:
    .venv/bin/python server.py
Poi aprire http://localhost:8000/display sul display esteso (HDMI).
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


app = FastAPI(title="Registratore riunioni", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
clients: set[WebSocket] = set()
_main_loop: asyncio.AbstractEventLoop | None = None

state: MeetingState | None = None
capture: AudioCapture | None = None
transcriber: LiveTranscriber | None = None
llm: LLMLoop | None = None
_accurate_thread: threading.Thread | None = None

# I lavori di finalizzazione sopravvivono allo stato live: dopo un riavvio
# del PC si puo' generare una riunione salvata il giorno prima.
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
            "status": "in corso",
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
    """Legge solo l'header WAV: veloce anche con registrazioni di ore."""
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            return int(round(handle.getnframes() / rate)) if rate else 0
    except (OSError, wave.Error):
        return 0


def _accurate_estimate_seconds(duration: int) -> int:
    """Stima tarata su questa CPU: ~1,3x il tempo reale piu' il carico del modello."""
    fin = cfg.get("finalization", {})
    factor = float(fin.get("accurate_speed_factor", 1.3))
    overhead = float(fin.get("accurate_overhead_seconds", 10))
    return max(1, int(duration * factor + overhead + 0.5))


def format_stima(seconds: int) -> str:
    seconds = max(1, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if not minutes:
        return f"circa {secs} s"
    if not secs:
        return f"circa {minutes} min"
    return f"circa {minutes} min {secs} s"


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
            raise FileNotFoundError("nessuna riunione salvata")
        meeting_id = saved[0].name
    if not _VALID_MEETING_ID.match(meeting_id):
        raise ValueError(f"id riunione non valido: {meeting_id}")
    meeting_dir = ROOT / cfg["paths"]["output_dir"] / meeting_id
    if not (meeting_dir / "audio.wav").exists():
        raise FileNotFoundError(f"audio grezzo mancante per {meeting_id}")
    return meeting_id, meeting_dir


def _meeting_busy(meeting_id: str) -> bool:
    with _jobs_lock:
        return any(
            item["meeting_id"] == meeting_id and item["status"] == "in corso"
            for item in jobs.values()
        )


def _sync_live_status(meeting_id: str, status: str, message: str) -> None:
    """Aggiorna lo stato live solo se la riunione lavorata e' quella corrente."""
    if state is not None and state.meeting_id == meeting_id:
        state.set_status(status)
        state.add_log(message)
        _push_state()


def _build_result() -> dict[str, Any]:
    assert state is not None
    result = dict(state.result)
    result.setdefault("titolo", f"Verbale riunione {state.meeting_id}")
    result.setdefault("data", datetime.now().strftime("%Y-%m-%d"))
    result.setdefault("partecipanti", [])
    result.setdefault("riassunto", "(riassunto non disponibile: LLM non configurato o non raggiungibile)")
    result.setdefault("decisioni", [])
    result.setdefault("azioni", [])
    result.setdefault("argomenti", [])
    result.setdefault("mindmap_mermaid", "")
    result.setdefault("flusso_mermaid", "")
    return result


@app.get("/display", response_class=HTMLResponse)
async def display() -> HTMLResponse:
    html = (ROOT / "static" / "display.html").read_text(encoding="utf-8")
    # Niente cache: le correzioni alla pagina devono apparire subito dopo un riavvio.
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
    """Restituisce la trascrizione salvata di una riunione, live o accurata."""
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
    """Serve un documento gia' generato (PDF, DOCX, MD, JSON, SVG...)."""
    try:
        meeting_id, meeting_dir = _resolve_meeting_dir(meeting_id)
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if not file:
        return JSONResponse({"error": "file non specificato"}, status_code=400)

    # Accetta sia il percorso relativo alla riunione ("documenti/verbale.pdf")
    # sia il solo nome file ("verbale.pdf"), ma sempre dentro la cartella.
    candidate = meeting_dir / file
    if not candidate.exists():
        candidate = meeting_dir / "documenti" / file
    try:
        resolved = candidate.resolve()
        resolved.relative_to(meeting_dir.resolve())
    except (ValueError, OSError):
        return JSONResponse({"error": "percorso non valido"}, status_code=400)
    if not resolved.is_file():
        return JSONResponse({"error": "file non trovato"}, status_code=404)
    # PDF/immagini/testo si aprono direttamente nella nuova scheda; gli altri
    # (DOCX...) scaricano con il nome corretto invece di "document".
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
        return JSONResponse({"error": "riunione gia' in corso"}, status_code=409)

    while not audio_queue.empty():
        audio_queue.get_nowait()

    meeting_id = datetime.now().strftime("%Y%m%d-%H:%M:%S")
    state = MeetingState(
        meeting_id=meeting_id,
        output_dir=str(ROOT / cfg["paths"]["output_dir"] / meeting_id),
    )
    state.set_status("avvio")
    state.add_log(f"Riunione {meeting_id} creata")

    engine = cfg["stt_live"].get("engine", "faster_whisper")
    if engine == "vosk":
        from stt_live import VoskTranscriber

        transcriber = VoskTranscriber(cfg, state, audio_queue, _on_segment, _on_partial)
    else:
        transcriber = LiveTranscriber(cfg, state, audio_queue, _on_segment, _on_partial)
    try:
        transcriber.warmup()
    except Exception as exc:
        state.set_status("errore-stt")
        state.add_log(f"Impossibile caricare il modello STT: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=500)

    capture = AudioCapture(cfg, state, audio_queue)
    try:
        audio_path = capture.start()
        state.add_log(f"Audio grezzo: {audio_path}")
    except Exception as exc:
        state.set_status("errore-audio")
        state.add_log(f"Impossibile avviare il microfono: {exc}")
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
        return JSONResponse({"error": "nessuna riunione avviata"}, status_code=400)

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
    state.set_status("salvato")
    state.add_log(
        "Audio e trascrizione salvati. I documenti si generano dopo "
        "con il pulsante 'Genera documenti' (anche a PC riavviato)."
    )
    _push_state()

    return JSONResponse(
        {
            "meeting_id": state.meeting_id,
            "output_dir": str(meeting_dir),
            "documenti": [],
            "segmenti_live": len(state.segments),
            "nota": "Registrazione chiusa: documenti non ancora generati",
        }
    )


def _ensure_result(meeting_dir: Path, meeting_id: str) -> None:
    """Ricrea riunione.json se manca (es. riunione interrotta prima dello stop)."""
    if (meeting_dir / "riunione.json").exists():
        return
    transcript_path = meeting_dir / "trascrizione.txt"
    transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    result = {
        "titolo": f"Verbale riunione {meeting_id}",
        "data": datetime.now().strftime("%Y-%m-%d"),
        "partecipanti": [],
        "riassunto": "(riassunto non disponibile: LLM non configurato o non raggiungibile)",
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
        _job_log(key, "Generazione documenti avviata")
        _sync_live_status(meeting_id, "finalizzazione", "Generazione documenti avviata")
        _ensure_result(meeting_dir, meeting_id)
        manifest = finalize(meeting_dir, cfg, render_diagrams=render_diagrams)
        _job_log(key, "Documenti pronti: " + ", ".join(manifest["documenti"]))
        _job_finish(key, "completato", documenti=manifest["documenti"])
        _sync_live_status(meeting_id, "finalizzato", f"Documenti pronti in {meeting_dir}")
    except Exception as exc:
        _job_log(key, f"Errore: {exc}")
        _job_finish(key, "errore", error=str(exc))
        _sync_live_status(meeting_id, "errore-finalizzazione", f"Finalizzazione fallita: {exc}")


def _accurate_worker(
    meeting_id: str, key: str, model: str | None, render_diagrams: bool
) -> None:
    try:
        from finalize import finalize
        from stt_final import transcribe_final

        meeting_dir = ROOT / cfg["paths"]["output_dir"] / meeting_id
        run_cfg = copy.deepcopy(cfg)
        if model:
            run_cfg["stt_final"]["model"] = model
        estimate = _accurate_estimate_seconds(_wav_duration_seconds(meeting_dir / "audio.wav"))
        stima = format_stima(estimate)
        _job_log(key, f"Ritrascrizione accurata avviata · {stima}")
        _sync_live_status(
            meeting_id, "ritrascrizione-accurata", f"Ritrascrizione accurata avviata · {stima}"
        )
        payload = transcribe_final(meeting_dir, run_cfg)
        count = len(payload["segments"])
        _job_log(key, f"Ritrascritti {count} segmenti da {payload['duration']:.0f}s di audio")
        _ensure_result(meeting_dir, meeting_id)
        manifest = finalize(meeting_dir, run_cfg, render_diagrams=render_diagrams)
        _job_log(key, "Documenti pronti: " + ", ".join(manifest["documenti"]))
        _job_finish(key, "completato", documenti=manifest["documenti"], segmenti=count)
        _sync_live_status(
            meeting_id, "finalizzato-accurato", f"Ritrascrizione accurata completata: {count} segmenti"
        )
    except Exception as exc:
        _job_log(key, f"Errore: {exc}")
        _job_finish(key, "errore", error=str(exc))
        _sync_live_status(meeting_id, "errore-ritrascrizione", f"Ritrascrizione fallita: {exc}")


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
    """Genera i documenti da una riunione salvata, anche senza stato live."""
    try:
        meeting_id, _meeting_dir = _resolve_meeting_dir(_payload_meeting_id(payload))
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if _meeting_busy(meeting_id):
        return JSONResponse({"error": "lavoro gia' in corso per questa riunione"}, status_code=409)

    render = _bool_option(payload, "render_diagrams", False)
    key = _job_start("finalize", meeting_id)
    threading.Thread(
        target=_finalize_worker, args=(meeting_id, key, render), daemon=True
    ).start()
    return JSONResponse(
        {"status": "generazione avviata", "meeting_id": meeting_id, "job": key}
    )


@app.post("/api/finalize-accurate")
async def api_finalize_accurate(
    payload: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """Ritrascrizione Whisper + documenti finali di una riunione salvata."""
    global _accurate_thread
    try:
        meeting_id, _meeting_dir = _resolve_meeting_dir(_payload_meeting_id(payload))
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if _meeting_busy(meeting_id):
        return JSONResponse({"error": "lavoro gia' in corso per questa riunione"}, status_code=409)

    model = None
    if payload and payload.get("model"):
        model = str(payload["model"])
    elif cfg.get("finalization", {}).get("accurate_model"):
        model = str(cfg["finalization"]["accurate_model"])
    render = _bool_option(payload, "accurate_render_diagrams", True)

    estimate = _accurate_estimate_seconds(_wav_duration_seconds(_meeting_dir / "audio.wav"))
    key = _job_start("accurate", meeting_id)
    _accurate_thread = threading.Thread(
        target=_accurate_worker, args=(meeting_id, key, model, render), daemon=True
    )
    _accurate_thread.start()
    return JSONResponse(
        {
            "status": "ritrascrizione avviata",
            "meeting_id": meeting_id,
            "job": key,
            "accurate_estimate_seconds": estimate,
            "stima": format_stima(estimate),
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

"""Ritrascrizione di qualita' sull'audio grezzo, a riunione conclusa.

Padre del documento finale: il testo prodotto qui sovrascrive quello live.
Il modello grande puo' impiegare diversi minuti su questa CPU: e' un lavoro
offline, non fa parte del percorso critico della riunione.
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path
from typing import Any

import numpy as np

from config import load_config
from state import format_timestamp


def read_wav_16k(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"ampiezza campione non supportata: {width} byte")
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(audio), rate


def transcribe_final(meeting_dir: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    meeting_dir = Path(meeting_dir)
    audio_path = meeting_dir / "audio.wav"
    if not audio_path.exists():
        raise FileNotFoundError(f"manca {audio_path}")

    stt = cfg["stt_final"]
    audio, rate = read_wav_16k(audio_path)
    if rate != int(cfg["audio"]["sample_rate"]):
        raise ValueError(f"sample rate {rate} != {cfg['audio']['sample_rate']}")

    model = WhisperModel(
        stt["model"],
        device="cpu",
        compute_type=stt["compute_type"],
        cpu_threads=int(stt["cpu_threads"]),
    )
    glossary = cfg["llm"].get("glossary", "").strip().replace("\n", " ")
    segments, info = model.transcribe(
        audio,
        language=stt.get("language", "it"),
        beam_size=int(stt["beam_size"]),
        vad_filter=True,
        initial_prompt=glossary or None,
    )

    items = [
        {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
        for seg in segments
        if seg.text.strip()
    ]
    payload = {
        "model": stt["model"],
        "language": info.language,
        "duration": round(info.duration, 2),
        "segments": items,
    }
    (meeting_dir / "trascrizione.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [f"[{format_timestamp(item['start'])}] {item['text']}" for item in items]
    (meeting_dir / "trascrizione.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Ritrascrizione accurata + finalizzazione")
    parser.add_argument("--meeting", required=True)
    parser.add_argument("--model", default=None, help="sovrascrive stt_final.model")
    parser.add_argument("--no-finalize", action="store_true")
    parser.add_argument("--no-diagrams", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.model:
        cfg["stt_final"]["model"] = args.model

    payload = transcribe_final(Path(args.meeting), cfg)
    print(f"Trascritti {len(payload['segments'])} segmenti da {payload['duration']}s di audio")

    if not args.no_finalize:
        from finalize import finalize

        manifest = finalize(Path(args.meeting), cfg, not args.no_diagrams)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Stato condiviso della riunione, thread-safe.

I worker (cattura, STT, LLM) girano su thread diversi; il server FastAPI
legge snapshot per WebSocket e API. Tutta la mutazione passa da qui.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


def format_timestamp(seconds: float) -> str:
    """MM:SS senza decimali e senza minuti da 60 (00:60 -> 01:00)."""
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class MeetingState:
    meeting_id: str
    started_at: float = field(default_factory=time.time)
    created_monotonic: float = field(default_factory=time.monotonic)
    audio_started_at: float = 0.0
    audio_started_monotonic: float = 0.0
    segments: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "idle"
    llm_status: str = "non configurato"
    log: list[str] = field(default_factory=list)
    output_dir: str = ""
    last_lag: float = 0.0
    dropped: int = 0
    partial: str = ""
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def add_segment(self, seconds: float, text: str) -> bool:
        text = " ".join(text.split())
        if not text:
            return False
        with self._lock:
            if self.segments and self.segments[-1]["text"] == text:
                return False
            self.segments.append({"t": round(seconds, 1), "text": text})
            return True

    def transcript_text(self) -> str:
        with self._lock:
            return "\n".join(
                f"[{format_timestamp(seg['t'])}] {seg['text']}"
                for seg in self.segments
            )

    def plain_text(self) -> str:
        with self._lock:
            return " ".join(seg["text"] for seg in self.segments)

    def mark_audio_start(self) -> None:
        """Il tempo dell'audio parte quando parte la cattura, non prima del warmup."""
        with self._lock:
            self.audio_started_at = time.time()
            # Monotonico: se il PC va in sospensione o l'orologio si sposta,
            # il ritardo percepito resta quello dell'audio, non dell'orologio.
            self.audio_started_monotonic = time.monotonic()

    def audio_elapsed(self) -> float:
        with self._lock:
            start = self.audio_started_monotonic or self.created_monotonic
        return max(0.0, time.monotonic() - start)

    def elapsed_seconds(self) -> int:
        """Secondi interi di registrazione, stabili anche se l'orologio salta."""
        return int(self.audio_elapsed())

    def set_lag(self, lag: float) -> None:
        with self._lock:
            self.last_lag = round(max(0.0, lag), 2)

    def set_partial(self, text: str) -> None:
        with self._lock:
            self.partial = text

    def note_drop(self) -> None:
        with self._lock:
            self.dropped += 1

    def set_result(self, result: dict[str, Any]) -> None:
        with self._lock:
            self.result = result

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status

    def set_llm_status(self, status: str) -> None:
        with self._lock:
            self.llm_status = status
            self.add_log(f"LLM: {status}")

    def add_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.log.append(f"{stamp}  {message}")
            self.log = self.log[-60:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "meeting_id": self.meeting_id,
                "status": self.status,
                "llm_status": self.llm_status,
                "started_at": self.started_at,
                "elapsed_seconds": self.elapsed_seconds(),
                "segments": list(self.segments),
                "result": dict(self.result),
                "log": list(self.log),
                "output_dir": self.output_dir,
                "last_lag": self.last_lag,
                "dropped": self.dropped,
                "partial": self.partial,
            }

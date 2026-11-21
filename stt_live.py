"""Live transcription with faster-whisper using sequential chunks.

Each block of a few seconds is transcribed once (with a small
overlap to avoid cutting words) and the text is emitted immediately. Compared
to a sliding window re-transcribed multiple times, this keeps the load predictable
and the delay from growing over time.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from state import MeetingState


class LiveTranscriber:
    def __init__(
        self,
        cfg: dict[str, Any],
        state: MeetingState,
        audio_queue: queue.Queue[np.ndarray],
        on_segment: Callable[[dict[str, Any]], None],
        on_partial: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.queue = audio_queue
        self.on_segment = on_segment
        self.on_partial = on_partial
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.model: Any = None
        self._last_text = ""
        self.segment_count = 0

    def warmup(self) -> None:
        """Loads the model before opening the microphone: no initial queue."""
        from faster_whisper import WhisperModel

        stt = self.cfg["stt_live"]
        t0 = time.perf_counter()
        self.model = WhisperModel(
            stt["model"],
            device="cpu",
            compute_type=stt["compute_type"],
            cpu_threads=int(stt["cpu_threads"]),
        )
        self.state.add_log(
            f"STT ready: {stt['model']} in {time.perf_counter() - t0:.1f}s"
        )

    def start(self) -> None:
        if self.model is None:
            self.warmup()
        self.state.mark_audio_start()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="stt-live")
        self.thread.start()

    @staticmethod
    def _strip_overlap(previous: str, current: str) -> str:
        """Removes repeated text between two consecutive chunks."""
        if not previous:
            return current
        if current in previous:
            return ""
        limit = min(len(previous), len(current))
        for size in range(limit, 0, -1):
            if previous[-size:] == current[:size]:
                return current[size:].lstrip()
        return current

    def _run(self) -> None:
        assert self.model is not None
        stt = self.cfg["stt_live"]
        sr = int(self.cfg["audio"]["sample_rate"])
        chunk = max(1, int(float(stt["chunk_seconds"]) * sr))
        overlap = max(0, int(float(stt["overlap_seconds"]) * sr))
        if overlap >= chunk:
            overlap = chunk // 4
        advance = chunk - overlap

        glossary = self.cfg["llm"].get("glossary", "").strip().replace("\n", " ")
        buffer = np.empty(0, dtype=np.float32)
        timeline = 0  # absolute sample index of the start of the buffer

        while not self.stop_event.is_set():
            try:
                data = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            buffer = np.concatenate((buffer, data)) if buffer.size else data.copy()

            while len(buffer) >= chunk and not self.stop_event.is_set():
                audio = buffer[:chunk]
                abs_start_s = timeline / sr
                abs_end_s = (timeline + chunk) / sr

                try:
                    segments, _info = self.model.transcribe(
                        audio,
                        language=stt.get("language", "it"),
                        beam_size=int(stt["beam_size"]),
                        vad_filter=bool(stt.get("vad_filter", True)),
                        condition_on_previous_text=False,
                        initial_prompt=glossary or None,
                    )
                    texts = [" ".join(seg.text.split()) for seg in segments]
                    texts = [text for text in texts if text]
                except Exception as exc:
                    self.state.add_log(f"STT transcription error: {exc}")
                    texts = []

                combined = " ".join(texts).strip()
                emitted = self._strip_overlap(self._last_text, combined)
                if emitted:
                    self._last_text = (self._last_text + " " + emitted).strip()[-800:]
                    if self.state.add_segment(abs_start_s, emitted):
                        self.segment_count += 1
                        # Perceived lag: how much time really passed
                        # from the end of speech to when the text appears.
                        lag = max(0.0, self.state.audio_elapsed() - abs_end_s)
                        self.state.set_lag(lag)
                        if self.segment_count <= 5:
                            self.state.add_log(
                                f"Chunk latency {self.segment_count}: {lag:.2f}s"
                            )
                        self.on_segment({"t": round(abs_start_s, 1), "text": emitted})

                buffer = buffer[advance:]
                timeline += advance

        self.state.add_log(f"STT stopped: {self.segment_count} chunks emitted")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=10)
            self.thread = None


class VoskTranscriber:
    """Low-latency streaming STT: suitable for old and loaded CPUs.

    Partials arrive while speaking (~0.3 s), finals shortly after the end
    of the sentence. Quality is lower than Whisper, but live subtitles
    stay in sync; the final document uses the Whisper re-transcription.
    """

    def __init__(
        self,
        cfg: dict[str, Any],
        state: MeetingState,
        audio_queue: queue.Queue[np.ndarray],
        on_segment: Callable[[dict[str, Any]], None],
        on_partial: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.queue = audio_queue
        self.on_segment = on_segment
        self.on_partial = on_partial
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.model: Any = None
        self.recognizer: Any = None
        self.segment_count = 0

    def warmup(self) -> None:
        import json as _json
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        configured = self.cfg["stt_live"].get("vosk_model", "models/vosk-model-small-it-0.22")
        model_path = Path(configured)
        if not model_path.is_absolute():
            model_path = Path(self.cfg["_root"]) / model_path
        if not model_path.exists():
            raise FileNotFoundError(f"Vosk model missing: {model_path}")

        t0 = time.perf_counter()
        self.model = Model(str(model_path))
        self.recognizer = KaldiRecognizer(self.model, int(self.cfg["audio"]["sample_rate"]))
        self.recognizer.SetWords(True)
        self.state.add_log(
            f"Vosk ready: {model_path.name} in {time.perf_counter() - t0:.1f}s"
        )

    def start(self) -> None:
        if self.model is None:
            self.warmup()
        self.state.mark_audio_start()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="stt-vosk")
        self.thread.start()

    def _run(self) -> None:
        import json as _json

        assert self.recognizer is not None
        sr = int(self.cfg["audio"]["sample_rate"])
        piece = max(1, int(0.2 * sr))  # 200 ms: smooth partials without overworking the CPU
        total_samples = 0
        last_partial = ""

        while not self.stop_event.is_set():
            try:
                data = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue

            for offset in range(0, len(data), piece):
                part = data[offset : offset + piece]
                pcm = (np.clip(part, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                start_s = (total_samples + offset) / sr
                end_s = start_s + len(part) / sr

                try:
                    if self.recognizer.AcceptWaveform(pcm):
                        result = _json.loads(self.recognizer.Result())
                        text = " ".join(result.get("text", "").split())
                        if text:
                            lag = max(0.0, self.state.audio_elapsed() - end_s)
                            self.state.set_lag(lag)
                            self.state.set_partial("")
                            if self.on_partial is not None:
                                self.on_partial("")
                            if self.state.add_segment(start_s, text):
                                self.segment_count += 1
                                if self.segment_count <= 5:
                                    self.state.add_log(f"Sentence latency {self.segment_count}: {lag:.2f}s")
                                self.on_segment({"t": round(start_s, 1), "text": text})
                        last_partial = ""
                    else:
                        partial = " ".join(
                            _json.loads(self.recognizer.PartialResult())
                            .get("partial", "")
                            .split()
                        )
                        if partial != last_partial:
                            last_partial = partial
                            self.state.set_partial(partial)
                            if self.on_partial is not None:
                                self.on_partial(partial)
                except Exception as exc:
                    self.state.add_log(f"Vosk error: {exc}")

            total_samples += len(data)

        self.state.add_log(f"Vosk stopped: {self.segment_count} sentences")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=8)
            self.thread = None

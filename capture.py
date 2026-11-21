"""Audio capture from the microphone and saving of the raw WAV.

The raw WAV is always written: it's the backup plan that makes it possible
to transcribe everything at the end of the meeting with a more accurate model.
"""

from __future__ import annotations

import queue
import wave
from pathlib import Path
from typing import Any

import numpy as np

from state import MeetingState


def list_input_devices() -> list[str]:
    """Lists available input devices (useful for choosing the USB mic)."""
    try:
        import sounddevice as sd

        lines = []
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] > 0:
                lines.append(
                    f"{index}: {device['name']} "
                    f"(inputs={device['max_input_channels']}, "
                    f"sr={int(device['default_samplerate'])})"
                )
        return lines
    except Exception as exc:  # PortAudio unavailable
        return [f"Device enumeration error: {exc}"]


class AudioCapture:
    def __init__(
        self,
        cfg: dict[str, Any],
        state: MeetingState,
        audio_queue: queue.Queue[np.ndarray],
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.queue = audio_queue
        self.stream: Any = None
        self.wave_file: wave.Wave_write | None = None
        self.path: Path | None = None
        self.blocks = 0
        self.dropped = 0
        self._sample_rate = int(cfg["audio"]["sample_rate"])

    def start(self) -> Path:
        import sounddevice as sd

        device = self.cfg["audio"].get("device")
        if self.cfg["audio"].get("list_devices_on_start", True):
            for line in list_input_devices():
                self.state.add_log(f"Audio: {line}")

        out_dir = Path(self.cfg["_root"]) / self.cfg["paths"]["output_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / self.state.meeting_id / "audio.wav"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.wave_file = wave.open(str(self.path), "wb")
        self.wave_file.setnchannels(1)
        self.wave_file.setsampwidth(2)
        self.wave_file.setframerate(self._sample_rate)

        blocksize = max(
            1, int(self._sample_rate * float(self.cfg["audio"]["chunk_seconds"]))
        )
        self.state.add_log(
            f"Capture started: device={device or 'default'}, "
            f"sr={self._sample_rate}, block={blocksize}"
        )

        self.stream = sd.InputStream(
            device=device,
            channels=1,
            samplerate=self._sample_rate,
            dtype="float32",
            blocksize=blocksize,
            callback=self._callback,
        )
        self.stream.start()
        return self.path

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status:
            self.state.add_log(f"Audio warning: {status}")

        samples = np.ascontiguousarray(indata[:, 0], dtype=np.float32)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
        if self.wave_file is not None:
            try:
                self.wave_file.writeframes(pcm.tobytes())
            except Exception as exc:
                self.state.add_log(f"WAV write error: {exc}")

        try:
            self.queue.put_nowait(samples.copy())
            self.blocks += 1
        except queue.Full:
            self.dropped += 1
            self.state.note_drop()

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as exc:
                self.state.add_log(f"Stream close error: {exc}")
            self.stream = None

        if self.wave_file is not None:
            try:
                self.wave_file.close()
            except Exception as exc:
                self.state.add_log(f"WAV close error: {exc}")
            self.wave_file = None

        self.state.add_log(
            f"Capture finished: {self.blocks} blocks, {self.dropped} dropped"
        )

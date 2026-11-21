#!/usr/bin/env python3
"""Measures the speed factor of faster-whisper models on CPU.

Usage:
    python tests/benchmark_stt.py tests/test_5min_16k.wav

Prints for each model: loading time, transcription time,
audio duration and transcription_time / audio_duration ratio.
A ratio < 1 means the model can keep up with live transcription.
"""

import sys
import time

from faster_whisper import WhisperModel

AUDIO = sys.argv[1] if len(sys.argv) > 1 else "tests/test_5min_16k.wav"
CONFIGS = [
    ("tiny", 5),
    ("base", 5),
    ("base", 1),
    ("small", 5),
]


def main() -> None:
    print(f"Audio: {AUDIO}", flush=True)
    for name, beam in CONFIGS:
        t0 = time.perf_counter()
        model = WhisperModel(
            name, device="cpu", compute_type="int8", cpu_threads=4
        )
        load_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        segments, info = model.transcribe(
            AUDIO,
            language="it",
            beam_size=beam,
            vad_filter=True,
        )
        n_segments = sum(1 for _ in segments)
        transcribe_s = time.perf_counter() - t0

        ratio = transcribe_s / info.duration if info.duration else 0.0
        print(
            f"{name:6s} beam={beam} | load {load_s:5.1f}s | "
            f"transcription {transcribe_s:6.1f}s / {info.duration:6.1f}s audio "
            f"= {ratio:4.2f}x | {n_segments} segments "
            f"{'[LIVE OK]' if ratio < 1 else '[TOO SLOW FOR LIVE]'}",
            flush=True,
        )
        del model


if __name__ == "__main__":
    main()

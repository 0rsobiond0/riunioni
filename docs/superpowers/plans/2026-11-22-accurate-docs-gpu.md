# Accurate Documents and Optional GPU Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Regenerate meeting intelligence and documents from the best available transcript, while using CUDA for faster-whisper when available and CPU otherwise.

**Architecture:** Add a small STT runtime helper that detects CUDA through ctranslate2 and returns a safe device/compute-type pair. Use it for all faster-whisper construction. After accurate transcription, send the complete accurate transcript to the configured OpenAI-compatible LLM, persist the refreshed `riunione.json`, and only then allow document generation to consume it.

**Tech Stack:** Python, faster-whisper/ctranslate2, existing OpenAI-compatible LLM client, FastAPI, unittest.

**Spec:** User requested accurate transcript-driven documents and automatic GPU use with CPU fallback.

## Global Constraints

- Prefer `cuda` only when `ctranslate2.get_cuda_device_count() > 0`; otherwise use `cpu`.
- Use `float16` on CUDA and configured `int8` on CPU unless explicitly overridden.
- Never make GPU availability a startup requirement.
- Accurate transcription must refresh `riunione.json` before document generation.
- If LLM is disabled/unavailable, preserve the existing result and still generate documents from the accurate transcript.
- Do not include models, `.env`, or caches in Git.

## Review Focus

- CUDA unavailable: all STT paths remain usable on CPU.
- CUDA library detection failure: fallback to CPU with a clear reason.
- LLM disabled or unreachable after accurate transcription: transcript remains accurate and documents still generate.
- Empty accurate transcript: do not destroy a valid existing structured result.
- Re-running accurate transcription: preserve the live backup and refresh the structured result.

---

### Task 1: Optional GPU runtime selection

**Files:**
- Create: `stt_runtime.py`
- Modify: `stt_live.py`, `stt_final.py`, `scripts/download_models.py`, `tests/test_download_models.py`

**Interfaces:**
- `select_stt_runtime(config: dict) -> tuple[str, str]` returns `(device, compute_type)`.

- [ ] Add failing tests for CUDA-present and CUDA-unavailable selection.
- [ ] Implement safe ctranslate2 detection with CPU fallback.
- [ ] Replace hard-coded `device="cpu"` in faster-whisper paths with the helper.
- [ ] Run focused tests, full unittest suite, and compile checks.

### Task 2: Accurate transcript refreshes structured meeting result

**Files:**
- Modify: `llm_loop.py`, `server.py`, `stt_final.py`
- Test: `tests/test_accurate_pipeline.py`

**Interfaces:**
- Add a reusable function that accepts a complete transcript and prior result, calls the configured OpenAI-compatible LLM, normalizes the JSON, and returns the refreshed result.

- [ ] Add failing tests for successful refresh and disabled-LLM preservation.
- [ ] Implement the refresh using the existing schema/prompt conventions.
- [ ] Call it after `transcribe_final` and persist the refreshed `riunione.json` before marking the accurate job complete.
- [ ] Keep document generation independent and safe when refresh fails.

### Task 3: Verification and documentation

**Files:**
- Modify: `README.md`, `config.yaml` if needed

- [ ] Document automatic GPU/CPU selection and the accurate-transcript document flow.
- [ ] Run full tests, compile checks, and `git diff --check`.
- [ ] Verify no model/cache/secret files are staged.

# Model Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Provide a documented, explicit setup command that downloads the required Italian Vosk and faster-whisper models without putting model artifacts in Git.

**Architecture:** Add one Python setup script under `scripts/` that downloads/extracts the Vosk archive and preloads faster-whisper `medium` into the standard Hugging Face cache. Update the README with the command, sources, sizes, and offline/startup behavior. The server remains free of implicit network downloads.

**Tech Stack:** Python standard library, `urllib`/`zipfile`, existing `faster-whisper`, Markdown.

**Spec:** User-approved option 2: explicit model-download script plus README documentation.

## Global Constraints

- Vosk model destination: `models/vosk-model-it-0.22`.
- Vosk source: official `https://alphacephei.com/vosk/models/vosk-model-it-0.22.zip`.
- Whisper model: `medium`, loaded through the project virtualenv and cached by faster-whisper/Hugging Face.
- Models remain untracked; `.gitignore` already excludes `models/`.
- The server must not download models implicitly during startup.
- Re-running setup must reuse completed downloads and tolerate interrupted downloads where the underlying cache supports it.

## Review Focus

- Existing complete Vosk directory: script must skip download and extraction.
- Partial Vosk archive: script must resume or clearly fail without destroying it.
- Invalid/corrupt ZIP: script must not leave a falsely installed model directory.
- Missing virtualenv/dependency: script must report an actionable error.
- Existing Whisper cache: script must reuse it rather than force a new model download.

---

### Task 1: Add model setup script

**Files:**
- Create: `scripts/download_models.py`
- Test: command-line help and compile check

**Interfaces:**
- CLI: `python scripts/download_models.py [--vosk-model {full,small}] [--whisper-model MODEL]`.
- Default: full Vosk Italian model and Whisper `medium`.

- [ ] Add argument parsing, repository-root discovery, and clear progress/error messages.
- [ ] Download the official Vosk ZIP to a temporary/cache path with resume support where possible.
- [ ] Validate the ZIP before extraction; extract to a temporary directory and atomically rename to `models/vosk-model-it-0.22`.
- [ ] Load `WhisperModel` with the selected model on CPU/int8 to ensure the model is cached.
- [ ] Make reruns idempotent and return nonzero on failed setup.
- [ ] Verify with `python -m py_compile scripts/download_models.py` and `python scripts/download_models.py --help`.

### Task 2: Document model setup

**Files:**
- Modify: `README.md` installation/model sections

**Interfaces:**
- Document the exact command using `.venv/bin/python scripts/download_models.py`.
- Explain Vosk full/small choices, Whisper cache behavior, approximate disk use, official source, and that startup stays offline/no implicit downloads.

- [ ] Replace the manual-only Vosk instructions with the explicit setup command while retaining the small-model alternative.
- [ ] Add troubleshooting guidance for interrupted downloads and rerunning the command.
- [ ] Verify Markdown content and run the project Python compilation/import checks.

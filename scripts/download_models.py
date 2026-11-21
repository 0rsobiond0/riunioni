#!/usr/bin/env python3
"""Download the speech models required by the meeting recorder."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VOSK_URLS = {
    "full": "https://alphacephei.com/vosk/models/vosk-model-it-0.22.zip",
    "small": "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip",
}


def vosk_model_ready(path: Path) -> bool:
    """Return whether *path* looks like an extracted Vosk model."""
    return path.is_dir() and (path / "conf").is_dir() and (
        (path / "am").is_dir() or (path / "graph").is_dir()
    )


def extract_vosk_model(archive: Path, destination: Path) -> None:
    """Safely extract a Vosk archive and install it atomically."""
    with tempfile.TemporaryDirectory(prefix="vosk-extract-") as temporary:
        temporary_path = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                member_path = (temporary_path / member.filename).resolve()
                if temporary_path not in member_path.parents:
                    raise RuntimeError("downloaded archive contains an unsafe path")
            bundle.extractall(temporary_path)

        candidates = [path for path in temporary_path.iterdir() if path.is_dir()]
        if len(candidates) != 1 or not vosk_model_ready(candidates[0]):
            raise RuntimeError("downloaded archive does not contain a valid Vosk model")

        destination.parent.mkdir(parents=True, exist_ok=True)
        installed = destination.with_name(destination.name + ".installing")
        if installed.exists():
            shutil.rmtree(installed)
        shutil.move(str(candidates[0]), installed)
        if destination.exists():
            shutil.rmtree(destination)
        installed.replace(destination)


def download_file(url: str, target: Path) -> None:
    """Download *url* to *target*, resuming an existing partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url)
    existing_size = target.stat().st_size if target.exists() else 0
    if existing_size:
        request.add_header("Range", f"bytes={existing_size}-")

    with urllib.request.urlopen(request) as response:
        if existing_size and response.status != 206:
            existing_size = 0
        mode = "ab" if existing_size else "wb"
        with target.open(mode) as output:
            shutil.copyfileobj(response, output)


def configure_vosk_model(root: Path, model_name: str) -> None:
    """Persist the selected Vosk model in the ignored local .env file."""
    env_path = root / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    setting = f"RIUNIONI_VOSK_MODEL=models/{model_name}"
    replaced = False
    updated: list[str] = []
    for line in lines:
        if line.startswith("RIUNIONI_VOSK_MODEL="):
            updated.append(setting)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(setting)
    env_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def install_vosk(root: Path, variant: str) -> None:
    model_name = "vosk-model-it-0.22" if variant == "full" else "vosk-model-small-it-0.22"
    destination = root / "models" / model_name
    if not vosk_model_ready(destination):
        archive = root / ".cache" / f"{model_name}.zip"
        print(f"Scarico Vosk {variant}: {VOSK_URLS[variant]}")
        download_file(VOSK_URLS[variant], archive)
        print(f"Estraggo Vosk in {destination}")
        extract_vosk_model(archive, destination)
    else:
        print(f"Vosk già presente: {destination}")
    configure_vosk_model(root, model_name)


def install_whisper(model: str) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper non installato: esegui pip install -r requirements.txt") from exc

    print(f"Precarico faster-whisper {model} nella cache locale")
    WhisperModel(model, device="cpu", compute_type="int8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vosk-model", choices=sorted(VOSK_URLS), default="full")
    parser.add_argument("--whisper-model", default="medium")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    try:
        install_vosk(root, args.vosk_model)
        install_whisper(args.whisper_model)
    except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(f"Errore download modelli: {exc}", file=sys.stderr)
        return 1
    print("Modelli pronti.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

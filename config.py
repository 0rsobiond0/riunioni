"""Caricamento configurazione con override via variabili d'ambiente."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.yaml"

_ENV_MAP: dict[str, tuple[str, str]] = {
    "RIUNIONI_LLM_BASE_URL": ("llm", "base_url"),
    "RIUNIONI_LLM_API_KEY": ("llm", "api_key"),
    "RIUNIONI_LLM_MODELS": ("llm", "models"),
    "RIUNIONI_AUDIO_DEVICE": ("audio", "device"),
    "RIUNIONI_STT_LIVE_MODEL": ("stt_live", "model"),
    "RIUNIONI_STT_FINAL_MODEL": ("stt_final", "model"),
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Legge config.yaml e applica gli override RIUNIONI_* presenti nell'ambiente."""
    config_path = Path(path) if path else DEFAULT_CONFIG
    with config_path.open(encoding="utf-8") as handle:
        cfg: dict[str, Any] = yaml.safe_load(handle) or {}

    # Precedenza: variabile d'ambiente reale > file .env > config.yaml
    env_file = ROOT / ".env"
    file_values: dict[str, str | None] = {}
    if env_file.exists():
        try:
            from dotenv import dotenv_values

            file_values = dotenv_values(env_file)
        except ImportError:
            pass

    overrides: dict[str, Any] = {}
    for env_name, (section, key) in _ENV_MAP.items():
        value = os.environ.get(env_name) or file_values.get(env_name)
        if not value:
            continue
        if key == "models":
            value = [item.strip() for item in value.split(",") if item.strip()]
        overrides.setdefault(section, {})[key] = value

    cfg = _merge(cfg, overrides)
    cfg["_config_path"] = str(config_path)
    cfg["_root"] = str(ROOT)
    return cfg

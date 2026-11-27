"""Runtime selection for faster-whisper with optional CUDA support."""

from __future__ import annotations

from ctypes.util import find_library
from typing import Any


def _cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except (ImportError, RuntimeError, OSError):
        return 0


def _cuda_libraries_available() -> bool:
    """Check the CUDA runtime libraries required by ctranslate2."""
    return bool(find_library("cublas") or find_library("cublasLt"))


def select_stt_runtime(config: dict[str, Any]) -> tuple[str, str]:
    """Choose CUDA/float16 only when GPU and CUDA libraries are usable."""
    if _cuda_device_count() > 0 and _cuda_libraries_available():
        return "cuda", str(config.get("cuda_compute_type", "float16"))
    return "cpu", str(config.get("compute_type", "int8"))


def create_whisper_model(model_class: Any, model_name: str, config: dict[str, Any], **kwargs: Any) -> Any:
    """Create Whisper on the best runtime, retrying on CPU if CUDA cannot load."""
    device, compute_type = select_stt_runtime(config)
    options = {"device": device, "compute_type": compute_type, **kwargs}
    try:
        return model_class(model_name, **options)
    except (RuntimeError, OSError) as exc:
        if device != "cuda":
            raise
        message = str(exc).lower()
        cuda_error = any(token in message for token in ("cuda", "cublas", "cudnn", "cudart"))
        if not cuda_error:
            raise
        fallback = {"device": "cpu", "compute_type": str(config.get("compute_type", "int8")), **kwargs}
        return model_class(model_name, **fallback)

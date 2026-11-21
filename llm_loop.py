"""Periodic update of the meeting minutes via an OpenAI-compatible endpoint.

The LLM is optional: if it's not configured or doesn't respond, subtitles
keep working and the loop just records the error state.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from state import MeetingState

REQUIRED_KEYS: dict[str, Any] = {
    "titolo": "",
    "data": "",
    "partecipanti": [],
    "riassunto": "",
    "decisioni": [],
    "azioni": [],
    "argomenti": [],
    "mindmap_mermaid": "",
    "flusso_mermaid": "",
}

LIST_KEYS = {"partecipanti", "decisioni", "azioni", "argomenti"}


def extract_json(raw: str) -> dict[str, Any]:
    """Tolerates code fences and stray text around the JSON."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the response")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the response is not a JSON object")
    return data


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Ensures the expected schema even if the model omits some keys."""
    out: dict[str, Any] = {}
    for key, default in REQUIRED_KEYS.items():
        value = data.get(key, default)
        if key in LIST_KEYS and not isinstance(value, list):
            value = []
        if key not in LIST_KEYS and not isinstance(value, str):
            value = str(value)
        out[key] = value
    out["data"] = out["data"] or data.get("data", "")
    return out


class LLMLoop:
    def __init__(
        self,
        cfg: dict[str, Any],
        state: MeetingState,
        on_result: Callable[[dict[str, Any]], None],
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.on_result = on_result
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._processed_segments = 0
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        llm = self.cfg["llm"]
        return bool(llm.get("base_url")) and bool(llm.get("models"))

    def start(self) -> None:
        if not self.enabled:
            self.state.set_llm_status("disabled (missing base_url or models)")
            return
        from openai import OpenAI

        llm = self.cfg["llm"]
        self._client = OpenAI(
            base_url=llm["base_url"],
            api_key=llm.get("api_key") or "none",
            timeout=float(llm.get("timeout_seconds", 45)),
            max_retries=0,
        )
        self.state.set_llm_status(f"active: {', '.join(llm['models'])}")
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="llm")
        self.thread.start()

    def _prompt(self, new_text: str, previous: dict[str, Any]) -> str:
        llm = self.cfg["llm"]
        return (
            "You are an assistant that produces meeting minutes. "
            "Respond ONLY with a valid JSON object, no markdown or comments.\n"
            "Required schema:\n"
            + json.dumps(REQUIRED_KEYS, ensure_ascii=False)
            + "\n\nGlossary of names and terms (use it to correct the transcript):\n"
            + (llm.get("glossary") or "(empty)")
            + "\n\nPrevious summary (update it, don't start from scratch):\n"
            + json.dumps(previous, ensure_ascii=False)[:6000]
            + "\n\nNew transcript to integrate:\n"
            + new_text[:12000]
        )

    def _call_model(self, model: str, prompt: str) -> dict[str, Any]:
        assert self._client is not None
        messages = [
            {"role": "system", "content": "Respond exclusively with valid JSON."},
            {"role": "user", "content": prompt},
        ]
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception:
            # some compatible endpoints don't accept response_format
            response = self._client.chat.completions.create(model=model, messages=messages)
        content = response.choices[0].message.content or ""
        return normalize(extract_json(content))

    def _run(self) -> None:
        interval = max(5, int(self.cfg["llm"].get("interval_seconds", 25)))
        while not self.stop_event.wait(interval):
            snapshot = self.state.snapshot()
            segments = snapshot["segments"]
            if self._processed_segments >= len(segments):
                continue
            new_text = "\n".join(
                seg["text"] for seg in segments[self._processed_segments :]
            )
            if not new_text.strip():
                continue

            previous = snapshot["result"]
            last_error = ""
            for model in self.cfg["llm"]["models"]:
                try:
                    result = self._call_model(model, self._prompt(new_text, previous))
                    self._processed_segments = len(segments)
                    self.state.set_result(result)
                    self.state.set_llm_status(f"OK · {model}")
                    self.on_result(result)
                    last_error = ""
                    break
                except Exception as exc:
                    last_error = f"{model}: {type(exc).__name__}: {exc}"
                    self.state.add_log(f"LLM retry: {last_error}")

            if last_error:
                self.state.set_llm_status(f"error (subtitles active) · {last_error[:120]}")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=10)
            self.thread = None

    def refresh_now(self) -> None:
        """Forces an immediate update (useful before finalization)."""
        if not self.enabled or self.thread is None:
            return
        snapshot = self.state.snapshot()
        segments = snapshot["segments"]
        if self._processed_segments >= len(segments):
            return
        new_text = "\n".join(seg["text"] for seg in segments[self._processed_segments :])
        try:
            result = self._call_model(self.cfg["llm"]["models"][0], self._prompt(new_text, snapshot["result"]))
            self._processed_segments = len(segments)
            self.state.set_result(result)
            self.on_result(result)
        except Exception as exc:
            self.state.add_log(f"Final LLM refresh failed: {exc}")

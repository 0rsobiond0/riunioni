import unittest
from unittest.mock import patch

from llm_loop import refresh_result_from_transcript
from stt_runtime import create_whisper_model


class AccuratePipelineTests(unittest.TestCase):
    def test_refresh_returns_previous_result_when_llm_is_disabled(self):
        previous = {"titolo": "Existing", "riassunto": "Keep this"}
        self.assertEqual(
            refresh_result_from_transcript({}, "accurate text", previous),
            previous,
        )

    def test_refresh_uses_normalized_llm_result(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"riassunto":"accurate", "mindmap_mermaid":"graph TD; A-->B"}'
            ))]
        )
        client = Mock()
        client.chat.completions.create.return_value = response
        cfg = {"llm": {"base_url": "http://llm", "models": ["model"]}}
        with patch("openai.OpenAI", return_value=client):
            result = refresh_result_from_transcript(cfg, "accurate text", {})
        self.assertEqual(result["riassunto"], "accurate")
        self.assertEqual(result["mindmap_mermaid"], "graph TD; A-->B")

    def test_whisper_model_falls_back_to_cpu_when_cuda_library_is_missing(self):
        from unittest.mock import Mock

        whisper_model = Mock(side_effect=[RuntimeError("libcublas.so.12 is not found"), "cpu-model"])
        with patch("stt_runtime.select_stt_runtime", return_value=("cuda", "float16")):
            result = create_whisper_model(whisper_model, "medium", {"compute_type": "int8"})
        self.assertEqual(result, "cpu-model")
        self.assertEqual(whisper_model.call_count, 2)
        self.assertEqual(whisper_model.call_args.args, ("medium",))
        self.assertEqual(whisper_model.call_args.kwargs["device"], "cpu")

    def test_runtime_selection_uses_cuda_when_available(self):
        from stt_runtime import select_stt_runtime

        with patch("stt_runtime._cuda_device_count", return_value=1), patch(
            "stt_runtime._cuda_libraries_available", return_value=True
        ):
            self.assertEqual(select_stt_runtime({"compute_type": "int8"}), ("cuda", "float16"))

    def test_runtime_selection_uses_cpu_when_cuda_libraries_are_missing(self):
        from stt_runtime import select_stt_runtime

        with patch("stt_runtime._cuda_device_count", return_value=1), patch(
            "stt_runtime._cuda_libraries_available", return_value=False
        ):
            self.assertEqual(select_stt_runtime({"compute_type": "int8"}), ("cpu", "int8"))

    def test_runtime_selection_falls_back_to_cpu(self):
        from stt_runtime import select_stt_runtime

        with patch("stt_runtime._cuda_device_count", return_value=0):
            self.assertEqual(select_stt_runtime({"compute_type": "int8"}), ("cpu", "int8"))

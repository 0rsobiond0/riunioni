import tempfile
import unittest
import zipfile
from pathlib import Path

from config import load_config
from scripts.download_models import extract_vosk_model, vosk_model_ready


class DownloadModelsTests(unittest.TestCase):
    def test_extract_vosk_model_installs_nested_model_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "model.zip"
            destination = root / "models" / "vosk-model-it-0.22"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("vosk-model-it-0.22/conf/model.conf", "ok")
                bundle.writestr("vosk-model-it-0.22/graph/words.txt", "ok")

            extract_vosk_model(archive, destination)

            self.assertTrue((destination / "conf/model.conf").is_file())
            self.assertTrue(vosk_model_ready(destination))

    def test_vosk_model_ready_requires_model_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "model"
            destination.mkdir()
            self.assertFalse(vosk_model_ready(destination))

    def test_extract_failure_preserves_existing_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "invalid.zip"
            destination = root / "models" / "vosk-model-it-0.22"
            destination.mkdir(parents=True)
            (destination / "sentinel").write_text("keep", encoding="utf-8")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("not-a-model/file.txt", "bad")

            with self.assertRaises(RuntimeError):
                extract_vosk_model(archive, destination)

            self.assertEqual((destination / "sentinel").read_text(encoding="utf-8"), "keep")

    def test_vosk_environment_override_selects_small_model(self):
        import os

        previous = os.environ.get("RIUNIONI_VOSK_MODEL")
        os.environ["RIUNIONI_VOSK_MODEL"] = "models/vosk-model-small-it-0.22"
        try:
            config = load_config()
            self.assertEqual(config["stt_live"]["vosk_model"], "models/vosk-model-small-it-0.22")
        finally:
            if previous is None:
                os.environ.pop("RIUNIONI_VOSK_MODEL", None)
            else:
                os.environ["RIUNIONI_VOSK_MODEL"] = previous


if __name__ == "__main__":
    unittest.main()

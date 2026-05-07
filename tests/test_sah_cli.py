import importlib.machinery
import importlib.util
import unittest
from pathlib import Path


def _load_sah():
    path = Path(__file__).resolve().parents[1] / "sah" / "sah"
    loader = importlib.machinery.SourceFileLoader("sah_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SahCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sah = _load_sah()

    def test_opencode_modalities_for_qwen_multimodal_model(self):
        self.assertEqual(
            self.sah._opencode_input_modalities_for("unsloth/Qwen3.6-27B-NVFP4"),
            ["text", "image", "video", "pdf"],
        )

    def test_opencode_modalities_for_text_model(self):
        self.assertEqual(
            self.sah._opencode_input_modalities_for("meta-llama/Llama-3.3-70B"),
            ["text"],
        )


if __name__ == "__main__":
    unittest.main()

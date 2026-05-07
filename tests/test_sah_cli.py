import importlib.machinery
import importlib.util
import unittest
from unittest import mock
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

    def test_codex_modalities_for_qwen_multimodal_model(self):
        self.assertEqual(
            self.sah._codex_input_modalities_for("unsloth/Qwen3.6-27B-NVFP4"),
            ["text", "image"],
        )

    def test_split_codex_exec_prompt_keeps_options(self):
        exec_args, prompt = self.sah._split_codex_exec_prompt(
            [
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--image",
                "/tmp/test.png",
                "Read this image",
            ]
        )

        self.assertEqual(prompt, "Read this image")
        self.assertEqual(
            exec_args,
            [
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--image",
                "/tmp/test.png",
            ],
        )

    def test_codex_pdf_prompt_augmentation(self):
        with mock.patch.object(
            self.sah,
            "_pdf_attachments_for_text",
            return_value=["[Extracted PDF text]\nPDF SECRET CODE: ORBIT-531"],
        ):
            out = self.sah._augment_codex_pdf_text("Read /tmp/example.pdf")

        self.assertIn("Read the extracted PDF text below", out)
        self.assertNotIn("/tmp/example.pdf", out)
        self.assertIn("PDF SECRET CODE: ORBIT-531", out)


if __name__ == "__main__":
    unittest.main()

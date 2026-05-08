import importlib.machinery
import importlib.util
import io
import json
import tempfile
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

    def test_normalize_passthrough_strips_separator_only(self):
        self.assertEqual(self.sah._normalize_passthrough(["--", "--help"]), ["--help"])
        self.assertEqual(self.sah._normalize_passthrough(["run", "hello"]), ["run", "hello"])

    def test_claude_desktop_requires_explicit_lifecycle_action(self):
        class Args:
            install = False
            restore = False
            status = False
            no_restart = True

        with mock.patch.object(self.sah, "_cd_paths", return_value={}):
            with self.assertRaises(SystemExit) as ctx:
                self.sah.cmd_claude_desktop(Args())

        self.assertIn("--install", str(ctx.exception))

    def test_integrations_lists_lifecycle_commands(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", new=out):
            self.sah.cmd_integrations(None)

        text = out.getvalue()
        self.assertIn("Integration", text)
        self.assertIn("Mode", text)
        self.assertIn("sah qwen --install", text)
        self.assertIn("sah claude-desktop --restore", text)
        self.assertRegex(text, r"hermes\s+CLI\s+.*launch only\s+sah hermes\s+n/a")

    def test_hermes_persistent_install_is_explicitly_unsupported(self):
        class Args:
            install = True
            restore = False
            status = False
            passthrough = []

        with self.assertRaises(SystemExit) as ctx:
            self.sah.cmd_hermes(Args())

        self.assertIn("launch-only", str(ctx.exception))

    def test_toml_validation_rejects_invalid_config(self):
        with self.assertRaises(SystemExit) as ctx:
            self.sah._validate_toml_text('model = "ok"\n[broken\n', Path("/tmp/config.toml"))

        self.assertIn("invalid TOML", str(ctx.exception))

    def test_codex_profile_merge_preserves_existing_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = home / ".codex" / "config.toml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text('model = "existing"\n\n[profiles.work]\nmodel = "other"\n')

            with mock.patch.object(self.sah.Path, "home", return_value=home):
                with mock.patch.object(self.sah, "api_base", return_value="http://hub/v1"):
                    self.sah._ensure_codex_profile()

            text = cfg.read_text()
            parsed = self.sah.tomllib.loads(text)
            self.assertEqual(parsed["model"], "existing")
            self.assertEqual(parsed["profiles"]["work"]["model"], "other")
            self.assertEqual(parsed["profiles"]["sah"]["model_provider"], "sah")
            self.assertEqual(parsed["model_providers"]["sah"]["base_url"], "http://hub/v1/")

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

    def test_qwen_global_install_and_restore_existing_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            config = Path(tmp) / "config"
            settings = home / ".qwen" / "settings.json"
            settings.parent.mkdir(parents=True)
            original = {"$version": 3, "model": {"name": "qwen-default"}}
            settings.write_text(json.dumps(original) + "\n")

            old_config_dir = self.sah.CONFIG_DIR
            self.sah.CONFIG_DIR = config / "sah"
            try:
                with mock.patch.object(self.sah.Path, "home", return_value=home):
                    with mock.patch.object(self.sah, "api_base", return_value="http://hub/v1"):
                        with mock.patch("sys.stdout", new=io.StringIO()):
                            self.sah._install_qwen_global("Qwen/Qwen3.6-27B-FP8")
                            installed = json.loads(settings.read_text())
                            self.assertEqual(installed["security"]["auth"]["selectedType"], "openai")
                            self.assertEqual(installed["env"]["OPENAI_API_KEY"], "sah")
                            self.assertEqual(installed["model"]["name"], "Qwen/Qwen3.6-27B-FP8")
                            self.assertTrue((config / "sah" / "qwen-settings.backup.json").is_file())

                            self.sah._restore_qwen_global()
                        self.assertEqual(json.loads(settings.read_text()), original)
            finally:
                self.sah.CONFIG_DIR = old_config_dir

    def test_qwen_global_restore_removes_settings_created_by_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            config = Path(tmp) / "config"
            settings = home / ".qwen" / "settings.json"

            old_config_dir = self.sah.CONFIG_DIR
            self.sah.CONFIG_DIR = config / "sah"
            try:
                with mock.patch.object(self.sah.Path, "home", return_value=home):
                    with mock.patch.object(self.sah, "api_base", return_value="http://hub/v1"):
                        with mock.patch("sys.stdout", new=io.StringIO()):
                            self.sah._install_qwen_global("Qwen/Qwen3.6-27B-FP8")
                            self.assertTrue(settings.is_file())
                            self.sah._restore_qwen_global()
                        self.assertFalse(settings.exists())
            finally:
                self.sah.CONFIG_DIR = old_config_dir

    def test_generic_client_backup_restore_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config" / "sah"
            target = Path(tmp) / "client.json"
            target.write_text('{"before": true}\n')
            old_config_dir = self.sah.CONFIG_DIR
            self.sah.CONFIG_DIR = config
            try:
                self.sah._backup_client_file_once("client", target)
                target.write_text('{"after": true}\n')
                with mock.patch("sys.stdout", new=io.StringIO()):
                    self.sah._restore_client_file("client", target)
                self.assertEqual(target.read_text(), '{"before": true}\n')
                self.assertFalse((config / "client-settings.backup.json").exists())
            finally:
                self.sah.CONFIG_DIR = old_config_dir

    def test_openclaw_profile_paths_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(self.sah.Path, "home", return_value=home):
                config_path, legacy_path = self.sah._openclaw_config_paths(profile="sah")

        self.assertEqual(config_path, home / ".openclaw-sah" / "openclaw.json")
        self.assertEqual(legacy_path, home / ".openclaw-sah" / "clawdbot.json")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from daemon.services import docker_service


class DockerServiceRecipeParsingTests(unittest.TestCase):
    def test_atlas_serve_command_prefetches_positional_hf_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe_dir = Path(tmp)
            (recipe_dir / "docker-compose.yml").write_text(
                """
services:
  atlas:
    image: avarok/atlas-gb10:latest
    command:
      - serve
      - RedHatAI/Qwen3.6-35B-A3B-NVFP4
""".lstrip()
            )

            self.assertEqual(
                docker_service._parse_vllm_model_service(recipe_dir),
                ("atlas", "RedHatAI/Qwen3.6-35B-A3B-NVFP4"),
            )
            self.assertTrue(docker_service._service_needs_external_prefetch(recipe_dir, "atlas"))

    def test_non_atlas_serve_command_is_not_treated_as_hf_prefetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe_dir = Path(tmp)
            (recipe_dir / "docker-compose.yml").write_text(
                """
services:
  app:
    image: example/runtime:latest
    command:
      - serve
      - owner/repo
""".lstrip()
            )

            self.assertIsNone(docker_service._parse_vllm_model_service(recipe_dir))
            self.assertFalse(docker_service._service_needs_external_prefetch(recipe_dir, "app"))


if __name__ == "__main__":
    unittest.main()

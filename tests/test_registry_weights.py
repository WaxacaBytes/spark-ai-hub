import tempfile
import unittest
from pathlib import Path

from daemon.services.registry_service import _weights_from_compose


def weights(compose: str) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "docker-compose.yml").write_text(compose)
        return [(w.repo, w.revision) for w in _weights_from_compose(Path(d))]


class WeightsFromComposeTests(unittest.TestCase):
    def test_reads_every_download_spelling_in_order(self):
        compose = """
        RUN python3 -c "from huggingface_hub import snapshot_download as get; get('TencentARC/Pixal3D', revision='b0cb', allow_patterns=['ckpts/*_mv.*']); get('briaai/RMBG-2.0')"
        RUN python3 -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='Org/Single-File', filename='a.safetensors', revision='c0ffee')"
        RUN hf download nvidia/Qwen3.8-Flash-Next-FP4-Local --revision b9ce18a6 --local-dir /models
        RUN python3 -c "from huggingface_hub import snapshot_download; snapshot_download('TencentARC/Pixal3D', revision='other')"
        """
        self.assertEqual(weights(compose), [
            ("TencentARC/Pixal3D", "b0cb"),
            ("briaai/RMBG-2.0", ""),
            ("Org/Single-File", "c0ffee"),
            ("nvidia/Qwen3.8-Flash-Next-FP4-Local", "b9ce18a6"),
        ])

    def test_an_image_pulled_with_no_download_has_none(self):
        self.assertEqual(weights("services:\n  app:\n    image: ghcr.io/org/app:latest\n"), [])


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

import yaml

RECIPES = Path(__file__).resolve().parent.parent / "registry" / "recipes"

# Apps that resolve model names internally (inside their own code) rather than
# on the command line, so their weights cannot be baked in without a hand-written
# repo list. They download at first launch and are the only recipes allowed to
# keep a model cache volume.
RUNTIME_DOWNLOAD_APPS = {
    "acestep", "chatterbox-turbo", "firered-image-edit", "foundation-1",
    "hunyuan3d-spark", "onyx", "spatialedit",
}

HF_CACHE_HINTS = ("huggingface", "hf_home", "/workspace/cache", "/root/.cache/hf")


def _composes():
    for d in sorted(RECIPES.iterdir()):
        f = d / "docker-compose.yml"
        if f.is_file():
            yield d.name, yaml.safe_load(f.read_text()) or {}


class WeightsInImageTest(unittest.TestCase):
    """Model weights are part of the app artifact: they live in the image, so
    removing the image reclaims every byte the recipe introduced."""

    def test_no_recipe_mounts_a_model_cache_volume(self):
        offenders = []
        for slug, compose in _composes():
            if slug in RUNTIME_DOWNLOAD_APPS:
                continue
            named = set((compose.get("volumes") or {}).keys())
            for svc in (compose.get("services") or {}).values():
                for mount in (svc.get("volumes") or []):
                    if not isinstance(mount, str) or ":" not in mount:
                        continue
                    src, dst = mount.split(":")[0], mount.split(":")[1]
                    if src in named and any(h in dst.lower() for h in HF_CACHE_HINTS):
                        offenders.append(f"{slug}: {mount}")
        self.assertEqual(offenders, [], "model cache volumes must not be mounted")

    def test_weights_stage_uses_a_build_secret_for_the_token(self):
        """The HF token must never be baked into image history as an ARG."""
        offenders = []
        for slug, compose in _composes():
            for svc in (compose.get("services") or {}).values():
                build = svc.get("build")
                if not isinstance(build, dict):
                    continue
                inline = build.get("dockerfile_inline") or ""
                if "snapshot_download" not in inline:
                    continue
                if "--mount=type=secret,id=hf_token" not in inline:
                    offenders.append(f"{slug}: weights stage without build secret")
                if "ARG HF_TOKEN" in inline:
                    offenders.append(f"{slug}: HF_TOKEN passed as ARG")
        self.assertEqual(offenders, [])

    def test_recipes_that_build_declare_it_in_recipe_yaml(self):
        offenders = []
        for slug, compose in _composes():
            builds = any(
                isinstance(s, dict) and s.get("build")
                for s in (compose.get("services") or {}).values()
            )
            rf = RECIPES / slug / "recipe.yaml"
            if not builds or not rf.is_file():
                continue
            meta = yaml.safe_load(rf.read_text()) or {}
            if not ((meta.get("docker") or {}).get("build")):
                offenders.append(slug)
        self.assertEqual(offenders, [], "compose builds but recipe.yaml says docker.build: false")


if __name__ == "__main__":
    unittest.main()

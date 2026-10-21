import unittest
from pathlib import Path
from unittest import mock

import yaml

from daemon.config import settings
from daemon.models.recipe import Recipe
from daemon.services import image_service

GUIDES = settings.registry_path.parent / "prompt_guides"


class PromptGuideTests(unittest.TestCase):
    def test_every_recipe_that_names_a_guide_has_it(self):
        named = {}
        for path in settings.registry_path.glob("*/recipe.yaml"):
            guide = (yaml.safe_load(path.read_text()) or {}).get("prompt_guide")
            if guide:
                named[path.parent.name] = guide
        self.assertTrue(named)
        for slug, guide in named.items():
            self.assertTrue((GUIDES / f"{guide}.md").is_file(), f"{slug} names a missing guide {guide}")

    def test_a_model_with_a_guide_returns_it(self):
        recipes = {"h3": Recipe(name="H3", slug="h3", prompt_guide="minimax-h3")}
        with mock.patch.object(image_service, "get_recipes", lambda: recipes):
            self.assertIn("integrated_multimodal_description", image_service.prompt_guide("h3"))

    def test_a_model_without_one_says_to_follow_its_notes(self):
        recipes = {"wan": Recipe(name="Wan", slug="wan")}
        with mock.patch.object(image_service, "get_recipes", lambda: recipes):
            self.assertIn("no prompt guide", image_service.prompt_guide("wan"))

    def test_an_unknown_model_is_an_error(self):
        with mock.patch.object(image_service, "get_recipes", lambda: {}):
            with self.assertRaises(image_service.ImageError):
                image_service.prompt_guide("nope")

    def test_a_guided_image_model_refuses_plain_text(self):
        recipes = {"ideogram": Recipe(name="Ideogram", slug="ideogram", prompt_guide="ideogram-4")}
        with mock.patch.object(image_service, "get_recipes", lambda: recipes):
            with self.assertRaises(image_service.ImageError) as caught:
                image_service.check_prompt("ideogram", "A lighthouse at dusk")
            self.assertIn("get_prompt_guide", str(caught.exception))
            image_service.check_prompt("ideogram", '{"compositional_deconstruction":{"background":"sea","elements":[]}}')

    def test_an_unguided_model_takes_plain_text(self):
        recipes = {"flux": Recipe(name="FLUX", slug="flux")}
        with mock.patch.object(image_service, "get_recipes", lambda: recipes):
            image_service.check_prompt("flux", "A lighthouse at dusk")


if __name__ == "__main__":
    unittest.main()

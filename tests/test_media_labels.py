import unittest
from unittest import mock

from daemon.models.recipe import Recipe
from daemon.services import media_store

RECIPES = {
    "h3-fl2va": Recipe(name="MiniMax-H3 FP8", slug="h3-fl2va", quantization="FP8", variant="FL2VA"),
    "h3-ref2va": Recipe(name="MiniMax-H3 Ref2VA INT8 Turbo", slug="h3-ref2va", quantization="INT8",
                        variant="Ref2VA"),
    "charswap": Recipe(name="MiniMax-H3 Character Swap LoRA", slug="charswap"),
    "orbit": Recipe(name="MiniMax-H3 360 Orbit LoRA", slug="orbit", tags=["orbit-360"]),
    "ltx": Recipe(name="LTX-2.5 Distilled", slug="ltx", quantization="BF16"),
    "sol": Recipe(name="Sol-H3 FL2VA", slug="sol", quantization="H3 FP8, LTX BF16", variant="FL2VA"),
}


class LabelTests(unittest.TestCase):
    def fields(self, slug, lora=""):
        with mock.patch("daemon.services.registry_service.get_recipe", RECIPES.get):
            return media_store._model_fields(slug, lora)

    def test_the_variant_and_quantization_a_name_leaves_out_are_added(self):
        self.assertEqual(self.fields("h3-fl2va")["model"], "MiniMax-H3 FL2VA FP8")
        self.assertEqual(self.fields("ltx")["model"], "LTX-2.5 Distilled BF16")

    def test_a_quantization_per_part_is_bracketed_not_joined_like_a_lora(self):
        self.assertEqual(self.fields("sol")["model"], "Sol-H3 FL2VA (H3 FP8, LTX BF16)")

    def test_what_the_name_already_says_is_not_repeated(self):
        self.assertEqual(self.fields("h3-ref2va")["model"], "MiniMax-H3 Ref2VA INT8 Turbo")

    def test_a_lora_render_names_the_base_and_the_lora(self):
        self.assertEqual(self.fields("h3-ref2va", "charswap")["model"],
                         "MiniMax-H3 Ref2VA INT8 Turbo + Character Swap LoRA")

    def test_a_link_lora_render_names_the_saved_lora(self):
        with mock.patch("daemon.services.registry_service.get_recipe", RECIPES.get):
            fields = media_store._model_fields(
                "h3-ref2va", "link-abcdef012345",
                {"link-abcdef012345": "Retro sci-fi style"},
            )
        self.assertEqual(fields["model"], "MiniMax-H3 Ref2VA INT8 Turbo + LoRA: Retro sci-fi style")

    def test_an_orbit_lora_makes_the_file_an_orbit(self):
        self.assertTrue(self.fields("h3-fl2va", "orbit")["orbit"])
        self.assertFalse(self.fields("h3-fl2va")["orbit"])

    def test_a_removed_recipe_still_shows_its_slug(self):
        self.assertEqual(self.fields("gone", "charswap")["model"], "gone + MiniMax-H3 Character Swap LoRA")

    def test_a_removed_lora_leaves_the_label_for_the_details(self):
        fields = self.fields("h3-ref2va", "charswap+lora-gone")
        self.assertEqual(fields["model"], "MiniMax-H3 Ref2VA INT8 Turbo + Character Swap LoRA")
        self.assertEqual(fields["removed_loras"], ["lora-gone"])

    def test_a_deleted_link_lora_is_a_removed_lora(self):
        self.assertEqual(self.fields("h3-ref2va", "link-abcdef012345")["removed_loras"], ["link-abcdef012345"])


if __name__ == "__main__":
    unittest.main()

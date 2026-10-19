import asyncio
import unittest
from unittest import mock

from daemon.models.recipe import Recipe, RecipeLora, RecipeVideoDefaults
from daemon.services import lora_service, media_store


def _lora(slug, bases, tags=(), pipeline=None, **defaults):
    return Recipe(name=f"{slug} LoRA", slug=slug, tags=["lora", *tags],
                  lora=RecipeLora(bases=bases, file=f"/lora/{slug}.safetensors", pipeline=pipeline),
                  video_defaults=RecipeVideoDefaults(**defaults) if defaults else None)


LORAS = {
    "turbo": _lora("turbo", ["fl2va", "fl2va-int8"], tags=["turbo"], steps=9, flow_shift=6.0,
                   extra_params={"audio_flow_shift": 3.0}),
    "orbit": _lora("orbit", ["fl2va"], tags=["orbit-360"], steps=29, seconds=4, flow_shift=12.0,
                   extra_params={"target": {"aspect_ratio": "1:1"}}),
    "faceswap": _lora("faceswap", ["ref2va"]),
    "alpha": _lora("alpha", ["ltx"], pipeline="alpha-gen"),
    "hdr": _lora("hdr", ["ltx"], pipeline="hdr"),
}


def resolve(*slugs):
    with mock.patch.object(lora_service, "installed", mock.AsyncMock(return_value=LORAS)):
        return asyncio.run(lora_service.resolve(list(slugs)))


class StackTests(unittest.TestCase):
    def test_one_lora_is_itself(self):
        self.assertIs(resolve("orbit"), LORAS["orbit"])
        self.assertIsNone(resolve())

    def test_loras_listed_together_run_as_one_stack_on_the_bases_they_share(self):
        stack = resolve("turbo", "orbit")
        self.assertEqual(stack.slug, "turbo+orbit")
        self.assertEqual(stack.lora.bases, ["fl2va"])
        self.assertEqual(stack.tags, ["lora", "orbit-360", "turbo"])

    def test_loras_with_no_base_in_common_are_refused(self):
        with self.assertRaisesRegex(lora_service.LoraError, "no base model in common"):
            resolve("orbit", "faceswap")

    def test_two_loras_that_need_their_own_pipelines_are_refused(self):
        with self.assertRaisesRegex(lora_service.LoraError, "own pipeline"):
            resolve("alpha", "hdr")

    def test_an_unknown_lora_is_named(self):
        with self.assertRaisesRegex(lora_service.LoraError, "'nope' is not an installed LoRA"):
            resolve("orbit", "nope")

    def test_a_turbos_schedule_wins_whichever_order_they_are_listed_in(self):
        base = RecipeVideoDefaults(steps=50, seconds=5, flow_shift=12.0,
                                   extra_params={"task": "t2va", "audio_flow_shift": 3.0})
        for order in (("turbo", "orbit"), ("orbit", "turbo")):
            got = lora_service.merged_defaults(base, resolve(*order))
            self.assertEqual((got.steps, got.flow_shift, got.seconds), (9, 6.0, 4))
            self.assertEqual(got.extra_params["target"], {"aspect_ratio": "1:1"})
            self.assertEqual(got.extra_params["task"], "t2va")

    def test_my_files_names_every_lora_of_a_stack(self):
        recipes = {**LORAS, "fl2va": Recipe(name="MiniMax-H3 FL2VA FP8", slug="fl2va")}
        with mock.patch("daemon.services.registry_service.get_recipe", recipes.get):
            fields = media_store._model_fields("fl2va", "turbo+orbit")
        self.assertEqual(fields["model"], "MiniMax-H3 FL2VA FP8 + turbo LoRA + orbit LoRA")
        self.assertTrue(fields["orbit"])


if __name__ == "__main__":
    unittest.main()

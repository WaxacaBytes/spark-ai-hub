import unittest
from unittest import mock

from daemon.routers.openai_proxy import _normalize_pdf_file_parts


class OpenAIProxyTests(unittest.TestCase):
    def test_pdf_file_parts_become_text_parts(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read this"},
                        {
                            "type": "file",
                            "file": {
                                "filename": "sample.pdf",
                                "file_data": "data:application/pdf;base64,abc",
                            },
                        },
                    ],
                }
            ]
        }

        with mock.patch(
            "daemon.routers.openai_proxy._extract_pdf_text_from_data_url",
            return_value="PDF SECRET CODE: ORBIT-531",
        ):
            self.assertTrue(_normalize_pdf_file_parts(body))

        self.assertEqual(
            body["messages"][0]["content"][1],
            {
                "type": "text",
                "text": "[PDF attachment: sample.pdf]\nPDF SECRET CODE: ORBIT-531",
            },
        )

    def test_non_pdf_file_parts_are_left_alone(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "file",
                            "file": {
                                "filename": "sample.txt",
                                "file_data": "data:text/plain;base64,abc",
                            },
                        }
                    ],
                }
            ]
        }

        self.assertFalse(_normalize_pdf_file_parts(body))
        self.assertEqual(body["messages"][0]["content"][0]["type"], "file")


if __name__ == "__main__":
    unittest.main()


class RoutingTests(unittest.TestCase):
    """Several LLMs run at once; /v1 routes by model and defaults to the largest."""

    def setUp(self):
        import asyncio
        from daemon.models.recipe import Recipe, RecipeIntegration, RecipeUI
        from daemon.routers import openai_proxy

        def llm(slug, params_b):
            return Recipe(name=slug, slug=slug, category="llm", params_b=params_b,
                          tags=["tool-use"], integration=RecipeIntegration(),
                          ui=RecipeUI(type="api-only", port=8000, proxy=True))
        self.recipes = {"small": llm("small", 4), "big": llm("big", 122),
                        "img": Recipe(name="img", slug="img", category="image-gen",
                                      ui=RecipeUI(type="api-only", port=30000, proxy=True))}
        served = self.served = {"small": "org/Small-4B", "big": "org/Big-122B", "img": "img-model"}

        async def upstream(_session, recipe):
            return openai_proxy.Upstream(recipe.slug, f"http://front/run/{recipe.slug}/v1",
                                         served[recipe.slug],
                                         [{"id": served[recipe.slug], "params_b": recipe.params_b}])

        openai_proxy._UPSTREAM_CACHE.update({"list": [], "fetched_at": 0.0})
        self.addCleanup(openai_proxy._UPSTREAM_CACHE.update, {"list": [], "fetched_at": 0.0})
        for patch in (
            mock.patch.object(openai_proxy, "get_recipes", return_value=self.recipes),
            mock.patch.object(openai_proxy, "_running_slugs",
                              mock.AsyncMock(return_value={"small", "big", "img"})),
            mock.patch.object(openai_proxy, "_upstream", upstream),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.proxy = openai_proxy
        self.run = asyncio.run

    def test_only_llms_are_upstreams_largest_first(self):
        self.assertEqual([u.slug for u in self.run(self.proxy._upstreams())], ["big", "small"])

    def test_a_named_model_goes_to_its_server(self):
        up, model = self.run(self.proxy.route("org/Small-4B"))
        self.assertEqual((up.slug, model), ("small", "org/Small-4B"))

    def test_any_other_name_goes_to_the_largest(self):
        up, model = self.run(self.proxy.route("claude-sonnet-5"))
        self.assertEqual((up.slug, model), ("big", "org/Big-122B"))

    def test_a_recipe_slug_names_its_model(self):
        up, model = self.run(self.proxy.route("small"))
        self.assertEqual((up.slug, model), ("small", "org/Small-4B"))

    def test_two_builds_of_one_model_are_listed_by_slug(self):
        self.recipes["small-dflash"] = self.recipes["small"].model_copy(update={"slug": "small-dflash"})
        self.served["small-dflash"] = "org/Small-4B"
        with mock.patch.object(self.proxy, "_running_slugs",
                               mock.AsyncMock(return_value={"small", "small-dflash", "big"})):
            listed = [e["id"] for u in self.run(self.proxy._upstreams()) for e in u.entries]
            self.assertEqual(sorted(listed), ["org/Big-122B", "small", "small-dflash"])
            up, model = self.run(self.proxy.route("small-dflash"))
        self.assertEqual((up.slug, model), ("small-dflash", "org/Small-4B"))

    def test_nothing_running(self):
        with mock.patch.object(self.proxy, "_running_slugs", mock.AsyncMock(return_value=set())):
            self.assertIsNone(self.run(self.proxy.route("anything")))


class LaunchPlanTests(unittest.TestCase):
    """The swap prompt only appears when the next LLM does not fit."""

    def plan(self, target, free, running):
        import asyncio
        from types import SimpleNamespace
        from daemon.routers import containers
        gb = {"a": 30, "b": 60, "c": 90, "new": 70, "img": 40}
        recipes = {s: SimpleNamespace(is_llm=s != "img", memory_gb=g) for s, g in gb.items()}
        with mock.patch.object(containers, "get_recipe", side_effect=recipes.get), \
             mock.patch.object(containers, "get_recipes", return_value=recipes), \
             mock.patch.object(containers, "memory_plan",
                               mock.AsyncMock(return_value=(gb[target], free, running))):
            return asyncio.run(containers.launch_plan(target))["stop"]

    def test_fits_beside_what_is_running(self):
        self.assertEqual(self.plan("new", 80, ["a", "b"]), [])

    def test_stops_the_smallest_model_that_frees_enough(self):
        self.assertEqual(self.plan("new", 20, ["a", "b", "c", "img"]), ["b"])

    def test_stops_the_largest_until_it_fits(self):
        self.assertEqual(self.plan("new", -60, ["a", "b", "c"]), ["c", "b"])

    def test_never_stops_media_apps(self):
        self.assertEqual(self.plan("new", 0, ["img"]), [])

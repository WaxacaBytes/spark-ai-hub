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

    def test_a_starred_model_is_the_default_even_when_smaller(self):
        with mock.patch.object(self.proxy.favorites_service, "_stars", {"small": {"by": "admin", "at": ""}}):
            self.assertEqual([u.slug for u in self.run(self.proxy._upstreams())], ["small", "big"])
            up, model = self.run(self.proxy.route("claude-sonnet-5"))
        self.assertEqual((up.slug, model), ("small", "org/Small-4B"))

    def test_a_starred_model_that_is_not_running_changes_nothing(self):
        with mock.patch.object(self.proxy.favorites_service, "_stars", {"elsewhere": {"by": "admin", "at": ""}}):
            up, _ = self.run(self.proxy.route("claude-sonnet-5"))
        self.assertEqual(up.slug, "big")


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


class StreamErrorTests(unittest.TestCase):
    """A model that refuses a streamed request: the client gets the model's own
    status and message, not a 200 stream that ends without a finish_reason."""

    def setUp(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from daemon.routers import openai_proxy

        refuse = self.refuse = {"on": True}
        seen = self.seen = []

        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                sent = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.append(sent.get("max_tokens"))
                # A 1000-token context and a 400-token prompt, worded as vLLM words it.
                if sent.get("max_tokens") and 400 + sent["max_tokens"] > 1000:
                    body = json.dumps({"error": {"message": (
                        "This model's maximum context length is 1000 tokens. However, you requested "
                        f"{sent['max_tokens']} output tokens and your prompt contains at least 400 input "
                        "tokens, for a total of at least 1001 tokens."), "code": 400}}).encode()
                    self.send_response(400)
                    self.send_header("content-type", "application/json")
                elif refuse["on"]:
                    body = json.dumps({"error": {"message": "This model's maximum context length is 262144 tokens.",
                                                 "type": "BadRequestError", "code": 400}}).encode()
                    self.send_response(400)
                    self.send_header("content-type", "application/json")
                else:
                    body = (b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
                            b"data: [DONE]\n\n")
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        up = openai_proxy.Upstream("llm", f"http://127.0.0.1:{server.server_port}/v1", "m", [])
        for patch in (mock.patch.object(openai_proxy, "route", mock.AsyncMock(return_value=(up, "m"))),
                      mock.patch.object(openai_proxy, "record_call", mock.AsyncMock())):
            patch.start()
            self.addCleanup(patch.stop)
        app = FastAPI()
        app.include_router(openai_proxy.router)
        self.client = TestClient(app)

    def _chat(self, stream=True, **extra):
        return self.client.post("/v1/chat/completions", json={
            "model": "m", "stream": stream, "messages": [{"role": "user", "content": "hi"}], **extra})

    def test_both_engines_wordings_are_understood(self):
        from daemon.routers.openai_proxy import _fit_output
        import json
        body = json.dumps({"max_tokens": 5000000}).encode()
        tensorfold = (b'{"error": {"message": "This server\'s maximum context length is 262144 tokens, but the '
                      b'rendered prompt has 54 tokens and requests 5000000 reply tokens, which exceeds the '
                      b'context window."}}')
        self.assertEqual(json.loads(_fit_output(body, tensorfold))["max_tokens"], 262090)
        self.assertIsNone(_fit_output(body, b'{"error": {"message": "max_tokens is too large"}}'))

    def test_an_output_ceiling_one_over_the_context_is_lowered_to_fit(self):
        self.refuse["on"] = False
        for stream in (True, False):
            self.seen.clear()
            r = self._chat(stream=stream, max_tokens=601)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.seen, [601, 600])

    def test_a_refused_stream_comes_back_as_the_models_error(self):
        r = self._chat()
        self.assertEqual(r.status_code, 400)
        self.assertIn("maximum context length", r.json()["error"]["message"])

    def test_an_accepted_stream_still_streams(self):
        self.refuse["on"] = False
        r = self._chat()
        self.assertEqual((r.status_code, r.headers["content-type"].split(";")[0]), (200, "text/event-stream"))
        self.assertIn('"finish_reason":"stop"', r.text)


if __name__ == "__main__":
    unittest.main()

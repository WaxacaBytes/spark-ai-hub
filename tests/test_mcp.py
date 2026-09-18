import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from daemon.routers import mcp
from daemon.services import audio_service, image_service, video_service


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(mcp.router)
    return TestClient(app)


def _rpc(method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _sse_data(body: str) -> list[str]:
    """The data payload of each event in an SSE body."""
    return [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]


def _png(width=64, height=32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.client = _client()

    def test_initialize_echoes_a_supported_version(self):
        r = self.client.post("/mcp", json=_rpc("initialize", {"protocolVersion": "2025-06-18"}))
        result = r.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])

    def test_initialize_offers_latest_for_unknown_version(self):
        r = self.client.post("/mcp", json=_rpc("initialize", {"protocolVersion": "1999-01-01"}))
        self.assertEqual(r.json()["result"]["protocolVersion"], mcp.PROTOCOL_VERSIONS[0])

    def test_notification_is_acknowledged_without_body(self):
        r = self.client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.content, b"")

    def test_tools_list_names(self):
        r = self.client.post("/mcp", json=_rpc("tools/list"))
        names = {t["name"] for t in r.json()["result"]["tools"]}
        self.assertEqual(names, {"generate_image", "edit_image", "get_image",
                                 "list_image_models", "generate_video", "get_video",
                                 "list_video_models", "generate_music"})

    def test_get_has_no_stream(self):
        self.assertEqual(self.client.get("/mcp").status_code, 405)

    def test_unknown_method(self):
        r = self.client.post("/mcp", json=_rpc("resources/list"))
        self.assertEqual(r.json()["error"]["code"], -32601)

    def test_unknown_tool(self):
        r = self.client.post("/mcp", json=_rpc("tools/call", {"name": "rm_rf", "arguments": {}}))
        self.assertEqual(r.json()["error"]["code"], -32602)

    def test_parse_error(self):
        r = self.client.post("/mcp", content=b"{nope", headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], -32700)


class McpToolCallTests(unittest.TestCase):
    def setUp(self):
        self.client = _client()
        self.result = image_service.Result(
            image_id="a" * 32, model="qwen-image-2512", width=1328, height=1328,
            seed=7, preview_jpeg_b64="AAAA",
        )

    def test_generate_as_json(self):
        with mock.patch.object(image_service, "run", mock.AsyncMock(return_value=self.result)) as run:
            r = self.client.post(
                "/mcp", json=_rpc("tools/call", {"name": "generate_image",
                                                 "arguments": {"prompt": "a cat", "seed": "7"}}),
                headers={"accept": "application/json"},
            )
        result = r.json()["result"]
        self.assertFalse(result["isError"])
        self.assertIn(f"/images/{'a' * 32}.png", result["content"][0]["text"])
        self.assertEqual(result["content"][1], {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"})
        kind, params, images, model = run.call_args.args[:4]
        self.assertEqual((kind, params.prompt, params.seed, images, model),
                         ("generate", "a cat", 7, [], None))

    def test_generate_as_sse_ends_with_the_response(self):
        with mock.patch.object(image_service, "run", mock.AsyncMock(return_value=self.result)):
            r = self.client.post(
                "/mcp", json=_rpc("tools/call", {"name": "generate_image",
                                                 "arguments": {"prompt": "a cat"}}, msg_id=9),
                headers={"accept": "application/json, text/event-stream"},
            )
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
        final = json.loads(_sse_data(r.text)[-1])
        self.assertEqual(final["id"], 9)
        self.assertEqual(final["result"]["structuredContent"]["seed"], 7)

    def test_image_error_is_a_tool_error_not_a_protocol_error(self):
        err = image_service.ImageError("No image generate model is running")
        with mock.patch.object(image_service, "run", mock.AsyncMock(side_effect=err)):
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "generate_image", "arguments": {"prompt": "x"}}))
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("No image generate model", result["content"][0]["text"])

    def test_edit_requires_images(self):
        r = self.client.post("/mcp", json=_rpc("tools/call", {
            "name": "edit_image", "arguments": {"prompt": "make it blue"}}))
        self.assertTrue(r.json()["result"]["isError"])

    def test_image_file_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / f"{'b' * 32}.png").write_bytes(_png())
            with mock.patch.object(image_service, "IMAGE_DIR", Path(tmp)):
                self.assertEqual(self.client.get(f"/images/{'b' * 32}.png").status_code, 200)
                self.assertEqual(self.client.get(f"/images/{'c' * 32}.png").status_code, 404)
                self.assertEqual(self.client.get("/images/..%2Fspark-ai-hub.db").status_code, 404)


class ImageServiceTests(unittest.TestCase):
    def test_tagged_recipes_become_backends(self):
        def recipe(name, tags):
            return mock.Mock(name=name, tags=tags)
        recipes = {
            "sglang-flux2-klein-9b": recipe("klein", ["openai-images", "text-to-image", "image-edit"]),
            "sglang-z-image-turbo": recipe("zit", ["openai-images", "text-to-image"]),
            "sglang-some-llm": recipe("llm", ["llm", "sglang"]),
            "tagged-but-no-tool": recipe("x", ["openai-images"]),
            "qwen-image-2512": recipe("qwen", ["gradio", "text-to-image"]),
        }
        with mock.patch.object(image_service, "get_recipes", return_value=recipes):
            found = image_service.backends()
        self.assertEqual(set(found), {"sglang-flux2-klein-9b", "sglang-z-image-turbo"})
        self.assertEqual(found["sglang-flux2-klein-9b"].kinds, {"generate", "edit"})
        self.assertEqual(found["sglang-z-image-turbo"].kinds, {"generate"})

    def test_generation_body(self):
        body = image_service.generation_body(
            image_service.Params(prompt="p", aspect_ratio="16:9", steps=4), seed=11)
        self.assertEqual(body, {
            "prompt": "p", "n": 1, "size": "1344x768", "response_format": "b64_json",
            "output_format": "png", "seed": 11, "num_inference_steps": 4,
        })

    def test_documented_defaults_fill_unset_steps_and_guidance(self):
        from daemon.models.recipe import RecipeImageDefaults
        defaults = RecipeImageDefaults(steps=4, guidance_scale=1.0, notes="distilled", source="card")
        backend = image_service.Backend(slug="k", kinds=frozenset({"generate"}),
                                        summary="k", defaults=defaults)
        self.assertEqual(image_service.with_defaults(backend, image_service.Params(prompt="p")).steps, 4)
        # an explicit request wins
        self.assertEqual(image_service.with_defaults(backend, image_service.Params(prompt="p", steps=9)).steps, 9)
        body = image_service.generation_body(image_service.Params(prompt="p", steps=4), 1, defaults)
        self.assertEqual((body["num_inference_steps"], body["guidance_scale"]), (4, 1.0))
        self.assertNotIn("true_cfg_scale", body)
        self.assertEqual(image_service.recommended(backend),
                         {"steps": 4, "guidance_scale": 1.0, "notes": "distilled", "source": "card"})

    def test_no_defaults_leaves_steps_to_the_server(self):
        backend = image_service.Backend(slug="k", kinds=frozenset({"generate"}), summary="k")
        self.assertIsNone(image_service.with_defaults(backend, image_service.Params(prompt="p")).steps)
        self.assertIsNone(image_service.recommended(backend))

    def test_recipe_yaml_parses_image_defaults(self):
        from daemon.models.recipe import Recipe
        r = Recipe(name="x", slug="x", image_defaults={"steps": 50, "true_cfg_scale": 4.0})
        self.assertEqual((r.image_defaults.steps, r.image_defaults.true_cfg_scale), (50, 4.0))

    def test_edit_size_keeps_aspect_at_one_megapixel(self):
        self.assertEqual(image_service.edit_size(_png(1920, 1080)), (1360, 768))
        self.assertEqual(image_service.edit_size(_png(500, 500)), (1024, 1024))
        w, h = image_service.edit_size(_png(3000, 1000))
        self.assertEqual((w % 16, h % 16), (0, 0))

    def test_loopback_urls_are_refused(self):
        async def go():
            with self.assertRaises(image_service.ImageError):
                await image_service.load_input("http://127.0.0.1:9010/api/admin/users", None)
        asyncio.run(go())

    def test_local_paths_are_refused(self):
        async def go():
            with self.assertRaises(image_service.ImageError):
                await image_service.load_input("/etc/passwd", None)
        asyncio.run(go())

    def test_data_url_and_hub_url_inputs(self):
        png = _png()

        async def go(tmp):
            data = await image_service.load_input(
                "data:image/png;base64," + base64.b64encode(png).decode(), None)
            self.assertEqual(data, png)
            (Path(tmp) / f"{'d' * 32}.png").write_bytes(png)
            hub = await image_service.load_input(
                f"https://spark.example.ts.net/images/{'d' * 32}.png", None)
            self.assertEqual(hub, png)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(image_service, "IMAGE_DIR", Path(tmp)):
                asyncio.run(go(tmp))

    def test_store_writes_png_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(image_service, "IMAGE_DIR", Path(tmp)):
                result = image_service.store(_png(1024, 512), "m", 1)
                self.assertTrue((Path(tmp) / f"{result.image_id}.png").is_file())
        self.assertEqual((result.width, result.height), (1024, 512))
        with Image.open(io.BytesIO(base64.b64decode(result.preview_jpeg_b64))) as preview:
            self.assertEqual(preview.size, (512, 256))


class PresetAndCapabilityTests(unittest.TestCase):
    def test_preset_replaces_steps(self):
        from daemon.models.recipe import RecipeImageDefaults
        defaults = RecipeImageDefaults(preset="V4_DEFAULT_20")
        backend = image_service.Backend(slug="i", kinds=frozenset({"generate"}), summary="i",
                                        defaults=defaults)
        params = image_service.with_defaults(backend, image_service.Params(prompt="p", steps=30))
        self.assertIsNone(params.steps)
        body = image_service.generation_body(params, 1, defaults)
        self.assertEqual(body["preset"], "V4_DEFAULT_20")
        self.assertNotIn("num_inference_steps", body)
        self.assertEqual(image_service.recommended(backend), {"preset": "V4_DEFAULT_20"})

    def test_media_capabilities_from_tags(self):
        from daemon.models.recipe import Recipe
        self.assertEqual(Recipe(name="k", slug="k", tags=["openai-images", "image-edit", "multi-image", "text-to-image"])
                         .media_capabilities, ["image-generation", "image-editing", "multi-image-input"])
        self.assertEqual(Recipe(name="w", slug="w", tags=["image-to-video", "text-to-video"])
                         .media_capabilities, ["text-to-video", "image-to-video"])
        self.assertEqual(Recipe(name="l", slug="l", tags=["llm", "vision"]).media_capabilities, [])


class VideoToolTests(unittest.TestCase):
    def setUp(self):
        self.client = _client()

    def test_video_fields_use_defaults_and_flip_for_portrait(self):
        from daemon.models.recipe import RecipeVideoDefaults
        d = RecipeVideoDefaults(steps=50, guidance_scale=5.0, fps=24, seconds=5, size="1280x704")
        wide = video_service.video_fields(d, prompt="p", seconds=None, aspect_ratio="16:9", seed=1, steps=None)
        self.assertEqual(wide, {"prompt": "p", "seed": 1, "size": "1280x704", "seconds": 5, "fps": 24,
                                "guidance_scale": 5.0, "num_inference_steps": 50})
        tall = video_service.video_fields(d, prompt="p", seconds=3, aspect_ratio="9:16", seed=1, steps=20)
        self.assertEqual((tall["size"], tall["seconds"], tall["num_inference_steps"]), ("704x1280", 3, 20))

    def test_generate_video_returns_job_id(self):
        info = {"job_id": "j" * 32, "model": "wan", "status": "queued", "seconds": 5,
                "size": "1280x704", "seed": 3, "steps": 50}
        with mock.patch.object(video_service, "start", mock.AsyncMock(return_value=info)):
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "generate_video", "arguments": {"prompt": "waves"}}))
        result = r.json()["result"]
        self.assertFalse(result["isError"])
        self.assertIn("j" * 32, result["content"][0]["text"])

    def test_get_video_completed_has_url_and_poster(self):
        info = {"job_id": "j", "model": "wan", "status": "completed", "video_id": "a" * 32,
                "poster": "AAAA", "seconds": 5, "size": "1280x704", "seed": 3, "steps": 50,
                "inference_time_s": 301.2}
        with mock.patch.object(video_service, "check", mock.AsyncMock(return_value=info)) as check:
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "get_video", "arguments": {"job_id": "j"}}))
        result = r.json()["result"]
        self.assertIn(f"/videos/{'a' * 32}.mp4", result["content"][0]["text"])
        self.assertEqual(result["content"][1]["mimeType"], "image/jpeg")
        self.assertEqual(check.call_args.args[:2], ("j", 120))

    def test_get_video_in_progress_is_not_an_error(self):
        info = {"job_id": "j", "model": "wan", "status": "in_progress", "progress": 40}
        with mock.patch.object(video_service, "check", mock.AsyncMock(return_value=info)):
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "get_video", "arguments": {"job_id": "j", "wait_seconds": 0}}))
        result = r.json()["result"]
        self.assertFalse(result["isError"])
        self.assertIn("40%", result["content"][0]["text"])
        self.assertEqual(result["structuredContent"]["next_call"]["tool"], "get_video")

    def test_video_file_route_rejects_bad_names(self):
        self.assertEqual(self.client.get("/videos/../../etc.mp4").status_code, 404)
        self.assertEqual(self.client.get(f"/videos/{'b' * 32}.mp4").status_code, 404)


class UnifiedVideoEditAndMusicTests(unittest.TestCase):
    def setUp(self):
        self.client = _client()

    def test_chat_body_carries_text_and_every_input_image(self):
        from daemon.models.recipe import RecipeImageDefaults
        png = _png(1920, 1080)
        body = image_service.chat_body(image_service.Params(prompt="p", steps=50), 5,
                                       RecipeImageDefaults(steps=50, guidance_scale=4.0), [png, png])
        content = body["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "p"})
        self.assertEqual(len(content), 3)
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual((body["modalities"], body["width"], body["height"], body["cfg_scale"],
                          body["num_inference_steps"]), (["image"], 1360, 768, 4.0, 50))
        gen = image_service.chat_body(image_service.Params(prompt="p", aspect_ratio="16:9"), 5, None, [])
        self.assertEqual((gen["width"], gen["height"]), (1344, 768))
        self.assertNotIn("cfg_scale", gen)

    def test_chat_tag_makes_a_chat_backend(self):
        recipes = {"u": mock.Mock(name="u", tags=["openai-chat-images", "text-to-image", "image-edit"])}
        with mock.patch.object(image_service, "get_recipes", return_value=recipes):
            backend = image_service.backends()["u"]
        self.assertEqual((backend.protocol, backend.kinds), ("chat", {"generate", "edit"}))

    def test_frame_based_video_fields(self):
        from daemon.models.recipe import RecipeVideoDefaults
        d = RecipeVideoDefaults(steps=30, guidance_scale=5.0, fps=16, num_frames=81, size="832x480",
                                flow_shift=5.0, extra_params={"task": "x"})
        f = video_service.video_fields(d, prompt="p", seconds=None, aspect_ratio="16:9", seed=1, steps=None)
        self.assertEqual((f["num_frames"], f["size"], f["flow_shift"], f["extra_params"]),
                         (81, "832x480", 5.0, {"task": "x"}))
        self.assertNotIn("seconds", f)
        f3 = video_service.video_fields(d, prompt="p", seconds=3, aspect_ratio="16:9", seed=1, steps=None)
        self.assertEqual(f3["num_frames"], 45)   # 3 s x 16 fps = 48 -> 45, the nearest 4k+1

    def test_duration_in_extra_params_and_aspect_ratio_field(self):
        from daemon.models.recipe import RecipeVideoDefaults
        d = RecipeVideoDefaults(steps=50, fps=24, seconds=8, size="960x576", flow_shift=12.0,
                                extra_params={"task": "t2va", "duration": 8.0, "audio_flow_shift": 3.0},
                                send_aspect_ratio=True)
        f = video_service.video_fields(d, prompt="p", seconds=4, aspect_ratio="16:9", seed=1, steps=10)
        self.assertEqual(f["extra_params"], {"task": "t2va", "duration": 4.0, "audio_flow_shift": 3.0})
        self.assertEqual((f["aspect_ratio"], f["size"], f["flow_shift"], f["num_inference_steps"]),
                         ("16:9", "960x576", 12.0, 10))
        self.assertNotIn("seconds", f)
        self.assertEqual(d.extra_params["duration"], 8.0)   # the recipe's defaults are not mutated
        default_len = video_service.video_fields(d, prompt="p", seconds=None, aspect_ratio="16:9", seed=1, steps=None)
        self.assertEqual(default_len["extra_params"]["duration"], 8.0)

    def test_target_in_extra_params_takes_length_and_aspect_ratio(self):
        from daemon.models.recipe import RecipeVideoDefaults
        d = RecipeVideoDefaults(steps=50, seconds=5, flow_shift=12.0,
                                extra_params={"task": "t2va", "conditions": [], "audio_flow_shift": 3.0,
                                              "target": {"short_edge": 768, "aspect_ratio": "16:9",
                                                         "duration_seconds": 5.0}})
        f = video_service.video_fields(d, prompt="p", seconds=4, aspect_ratio="9:16", seed=1, steps=10)
        self.assertEqual(f["extra_params"]["target"],
                         {"short_edge": 768, "aspect_ratio": "9:16", "duration_seconds": 4.0})
        self.assertEqual((f["seconds"], f["flow_shift"], f["num_inference_steps"]), (4, 12.0, 10))
        for rejected in ("fps", "size", "num_frames", "guidance_scale", "aspect_ratio"):
            self.assertNotIn(rejected, f)
        self.assertEqual(d.extra_params["target"]["duration_seconds"], 5.0)   # defaults not mutated
        self.assertEqual(video_service.clip_seconds(f), 4)

    def test_image_job_survives_the_caller_giving_up(self):
        """A client's timeout must not cancel the render or lose the picture."""
        async def scenario():
            started = asyncio.Event()

            async def slow_run(kind, params, images, model, on_progress=None):
                started.set()
                await asyncio.sleep(0.3)
                return SimpleNamespace(path="/images/abc.png", model="m", width=8, height=8,
                                       seed=1, steps=None, preset=None, preview_jpeg_b64="x")

            with mock.patch.object(image_service, "run", slow_run):
                job_id = await image_service.start_job(
                    "edit", image_service.Params(prompt="p"), ["u"], "m")
                # The caller gives up while the render is still going.
                pending = await image_service.check_job(job_id, 0)
                self.assertEqual((pending["status"], pending["result"]), ("rendering", None))
                await started.wait()
                done = await image_service.check_job(job_id, 5)
                self.assertEqual((done["status"], done["result"].path), ("completed", "/images/abc.png"))
                # Collectable again afterwards.
                self.assertEqual((await image_service.check_job(job_id, 0))["status"], "completed")
        from types import SimpleNamespace
        asyncio.run(scenario())

    def test_get_image_reports_pending_then_the_image(self):
        from types import SimpleNamespace
        result = SimpleNamespace(path="/images/abc.png", model="m", width=8, height=8,
                                 seed=1, steps=None, preset=None, preview_jpeg_b64="x")
        pending = mcp._image_result(
            {"job_id": "j1", "status": "rendering", "model": "m", "kind": "edit",
             "result": None, "error": None, "elapsed": 90}, "http://h")
        self.assertIn("get_image with job_id j1", pending["content"][0]["text"])
        self.assertFalse(pending["isError"])
        self.assertEqual(pending["structuredContent"]["next_call"],
                         {"tool": "get_image", "arguments": {"job_id": "j1"}})
        done = mcp._image_result(
            {"job_id": "j1", "status": "completed", "model": "m", "kind": "edit",
             "result": result, "error": None, "elapsed": 120}, "http://h")
        self.assertEqual(done["structuredContent"]["url"], "http://h/images/abc.png")
        failed = mcp._image_result(
            {"job_id": "j1", "status": "failed", "model": "m", "kind": "edit",
             "result": None, "error": "boom", "elapsed": 3}, "http://h")
        self.assertEqual((failed["isError"], failed["content"][0]["text"]), (True, "boom"))

    def test_unknown_image_job_is_a_clear_error(self):
        with self.assertRaisesRegex(image_service.ImageError, "No image job"):
            asyncio.run(image_service.check_job("nope", 0))

    def test_hidream_backend_from_tag(self):
        from types import SimpleNamespace
        recipes = {"h": SimpleNamespace(name="HiDream", image_defaults=None,
                                        tags=["hidream-app", "text-to-image", "image-edit", "multi-image"])}
        with mock.patch.object(image_service, "get_recipes", return_value=recipes):
            backend = image_service.backends()["h"]
        self.assertEqual((backend.protocol, backend.kinds), ("hidream", {"generate", "edit"}))

    def test_hidream_body_picks_mode_by_reference_count(self):
        p = image_service.Params(prompt="a cat", aspect_ratio="16:9")
        t2i = image_service.hidream_body(p, 7, [])
        self.assertEqual((t2i["mode"], t2i["width"], t2i["height"], t2i["seed"]), ("t2i", 2688, 1536, 7))
        self.assertNotIn("refs_b64", t2i)
        edit = image_service.hidream_body(p, 7, [b"png1"])
        self.assertEqual((edit["mode"], edit["keep_original_aspect"], len(edit["refs_b64"])), ("edit", True, 1))
        subject = image_service.hidream_body(p, 7, [b"png1", b"png2"])
        self.assertEqual((subject["mode"], subject["keep_original_aspect"]), ("subject", False))

    def test_parse_sse_handles_split_and_huge_events(self):
        big = "A" * 200_000
        stream = (b'data: {"type": "progress", "step": 1}\n\n'
                  + ('data: {"type": "done", "image": "%s"}\n\n' % big).encode())
        events, rest = image_service.parse_sse(stream[:120])
        self.assertEqual(([e["type"] for e in events], rest != b""), (["progress"], True))
        events, rest = image_service.parse_sse(rest + stream[120:])
        self.assertEqual((events[0]["type"], len(events[0]["image"]), rest), ("done", 200_000, b""))

    def test_only_installed_models_are_listed_and_picked(self):
        from types import SimpleNamespace
        recipe = lambda name: SimpleNamespace(
            name=name, tags=["openai-images", "text-to-image"], image_defaults=None,
            requirements=SimpleNamespace(min_memory_gb=16))
        recipes = {"a-installed": recipe("A"), "b-catalog-only": recipe("B")}
        with mock.patch.object(image_service, "get_recipes", return_value=recipes), \
             mock.patch.object(image_service, "get_installed_slugs",
                               mock.AsyncMock(return_value={"a-installed"})), \
             mock.patch.object(image_service, "is_recipe_running", mock.AsyncMock(return_value=False)):
            listed = asyncio.run(image_service.list_models())
            self.assertEqual([m["model"] for m in listed], ["a-installed"])
            self.assertEqual(listed[0]["state"], "stopped")
            with self.assertRaisesRegex(image_service.ImageError, "not an installed model"):
                asyncio.run(image_service.pick_backend("generate", "b-catalog-only"))
            with self.assertRaisesRegex(image_service.ImageError, "Launch a-installed"):
                asyncio.run(image_service.pick_backend("generate", None))
        with mock.patch.object(image_service, "get_recipes", return_value=recipes), \
             mock.patch.object(image_service, "get_installed_slugs", mock.AsyncMock(return_value=set())):
            with self.assertRaisesRegex(image_service.ImageError, "is installed on the Spark"):
                asyncio.run(image_service.pick_backend("generate", None))

    def test_text_only_video_form_is_still_multipart(self):
        form = video_service.video_form()
        form.add_field("prompt", "waves")
        self.assertTrue(form.is_multipart)

    def test_clip_seconds_from_seconds_frames_or_duration(self):
        self.assertEqual(video_service.clip_seconds({"seconds": 5}), 5)
        self.assertEqual(video_service.clip_seconds({"num_frames": 45, "fps": 16}), 2.8)
        self.assertEqual(video_service.clip_seconds({"extra_params": {"duration": 4.0}}), 4.0)
        self.assertIsNone(video_service.clip_seconds({"prompt": "p"}))

    def test_video_inputs_refuse_loopback_and_local_paths(self):
        async def go():
            for ref in ("http://127.0.0.1:9010/x.mp4", "/etc/passwd"):
                with self.assertRaises(image_service.ImageError):
                    await video_service.load_video_input(ref, None)
        asyncio.run(go())

    def test_music_body(self):
        self.assertEqual(
            audio_service.music_body(lyrics="[Verse]\nla", style="lofi", seconds=30, seed=3),
            {"input": "[Verse]\nla", "instructions": "lofi", "seed": 3,
             "max_new_tokens": 750, "response_format": "wav"})

    def test_generate_music_tool(self):
        info = {"audio_id": "c" * 32, "model": "m", "seconds": 30.0, "seed": 3}
        with mock.patch.object(audio_service, "generate", mock.AsyncMock(return_value=info)):
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "generate_music", "arguments": {"lyrics": "[Verse]\nla", "style": "lofi"}}))
        result = r.json()["result"]
        self.assertFalse(result["isError"])
        self.assertIn(f"/audio/{'c' * 32}.wav", result["content"][0]["text"])

    def test_generate_music_needs_lyrics_and_style(self):
        r = self.client.post("/mcp", json=_rpc("tools/call", {
            "name": "generate_music", "arguments": {"lyrics": "x"}}))
        self.assertTrue(r.json()["result"]["isError"])

    def test_audio_route_rejects_bad_names(self):
        self.assertEqual(self.client.get("/audio/nope.wav").status_code, 404)

    def test_video_edit_and_music_capabilities(self):
        from daemon.models.recipe import Recipe
        self.assertEqual(Recipe(name="v", slug="v", tags=["video-edit", "text-to-video"]).media_capabilities,
                         ["text-to-video", "video-editing"])
        self.assertEqual(Recipe(name="m", slug="m", tags=["openai-music", "text-to-music"]).media_capabilities,
                         ["music-generation"])


class ProxiedApiRecipeTests(unittest.TestCase):
    def _recipe(self, **ui):
        from daemon.models.recipe import Recipe, RecipeUI
        return Recipe(name="x", slug="img", ui=RecipeUI(**ui))

    def test_proxied_api_recipe_links_to_its_api_path(self):
        r = self._recipe(type="api-only", port=30000, proxy=True, path="/v1/models")
        self.assertEqual(r.app_url, "/run/img/v1/models")

    def test_unproxied_api_recipe_keeps_host_port_fallback(self):
        self.assertEqual(self._recipe(type="api-only", port=9001, path="/v1/models").app_url, "")

    def test_proxy_routes_api_only_recipes(self):
        from daemon.services import proxy_service
        recipes = {
            "img": self._recipe(type="api-only", port=30000, proxy=True),
            "llm": self._recipe(type="api-only", port=9001),
        }
        with mock.patch("daemon.services.registry_service.get_recipes", return_value=recipes), \
             mock.patch.object(proxy_service, "_container_name", side_effect=lambda s: f"c-{s}"):
            self.assertEqual(proxy_service._proxied_recipes(), [("img", "c-img", 30000, True)])


if __name__ == "__main__":
    unittest.main()

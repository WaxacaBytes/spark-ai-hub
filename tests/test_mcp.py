import asyncio
import base64
import io
import ipaddress
import json
import socket
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from daemon import db as db_module
from daemon.routers import files, links, mcp, uploads
from daemon.services import (
    audio_service, decision_service, docker_service, image_service, link_service, media_store,
    upload_service, video_service, web_service,
)

# Stand-ins for AuthMiddleware's request.state.user. Rows 1-3 exist in the
# temporary DB below, because media.user_id references users(id).
OWNER = {"id": 1, "role": "user"}
OTHER = {"id": 2, "role": "user"}
ADMIN = {"id": 3, "role": "admin"}
_USERS = {"owner": OWNER, "other": OTHER, "admin": ADMIN, "anon": None}
_tmp_db = _db_patch = None


def setUpModule():
    global _tmp_db, _db_patch
    _tmp_db = tempfile.TemporaryDirectory()
    _db_patch = mock.patch.object(db_module, "DB_PATH", str(Path(_tmp_db.name) / "hub.db"))
    _db_patch.start()

    async def init():
        await db_module.init_db()
        db = await db_module.get_db()
        for user in (OWNER, OTHER, ADMIN):
            await db.execute("INSERT INTO users (id, email, password_hash, role, api_key, status) "
                             "VALUES (?, ?, 'x', ?, ?, 'active')",
                             (user["id"], f"u{user['id']}@x", user["role"], f"k{user['id']}"))
        await db.commit()
        await db.close()
    asyncio.run(init())


def tearDownModule():
    _db_patch.stop()
    _tmp_db.cleanup()


def _client() -> TestClient:
    """The MCP and upload routes, called as OWNER unless an x-test-user header
    names someone else ('other', 'admin' or 'anon')."""
    app = FastAPI()

    @app.middleware("http")
    async def as_user(request, call_next):
        request.state.user = _USERS[request.headers.get("x-test-user", "owner")]
        return await call_next(request)

    app.include_router(mcp.router)
    app.include_router(uploads.router)
    app.include_router(files.router)
    app.include_router(links.router)
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
                                 "list_video_models", "generate_music", "get_music",
                                 "create_upload", "create_download",
                                 "start_model", "stop_model", "web_search", "web_fetch",
                                 "decide"})

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

    def _generate(self, run, headers=None):
        """generate_image, then get_image on the job it returns."""
        backend = mock.AsyncMock(return_value=SimpleNamespace(slug="qwen-image-2512"))
        with mock.patch.object(image_service, "pick_backend", backend), \
                mock.patch.object(image_service, "run", run):
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "generate_image", "arguments": {"prompt": "a cat", "seed": "7"}}),
                headers=headers)
            sse = r.headers["content-type"].startswith("text/event-stream")
            started = (json.loads(_sse_data(r.text)[-1]) if sse else r.json())["result"]
            self.assertFalse(started["isError"])
            self.assertIn("Not done yet", started["content"][0]["text"])
            return self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "get_image", "arguments": started["structuredContent"]["next_call"]["arguments"]},
                msg_id=9), headers=headers)

    def test_generate_as_json(self):
        run = mock.AsyncMock(return_value=self.result)
        r = self._generate(run, {"accept": "application/json"})
        result = r.json()["result"]
        self.assertFalse(result["isError"])
        self.assertIn(f"/images/{'a' * 32}.png", result["content"][0]["text"])
        self.assertEqual(result["content"][1], {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"})
        kind, params, images, model = run.call_args.args[:4]
        self.assertEqual((kind, params.prompt, params.seed, images, model),
                         ("generate", "a cat", 7, [], "qwen-image-2512"))

    def test_generate_as_sse_ends_with_the_response(self):
        r = self._generate(mock.AsyncMock(return_value=self.result),
                           {"accept": "application/json, text/event-stream"})
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
        final = json.loads(_sse_data(r.text)[-1])
        self.assertEqual(final["id"], 9)
        self.assertEqual(final["result"]["structuredContent"]["seed"], 7)

    def test_image_error_is_a_tool_error_not_a_protocol_error(self):
        err = image_service.ImageError("No image generate model is running")
        with mock.patch.object(image_service, "pick_backend", mock.AsyncMock(side_effect=err)):
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "generate_image", "arguments": {"prompt": "x"}}))
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertIn("No image generate model", result["content"][0]["text"])

    def test_render_failure_comes_back_from_get_image(self):
        r = self._generate(mock.AsyncMock(side_effect=image_service.ImageError("boom")))
        result = r.json()["result"]
        self.assertEqual((result["isError"], result["content"][0]["text"]), (True, "boom"))

    def test_edit_requires_images(self):
        r = self.client.post("/mcp", json=_rpc("tools/call", {
            "name": "edit_image", "arguments": {"prompt": "make it blue"}}))
        self.assertTrue(r.json()["result"]["isError"])

    def test_image_file_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / f"{'b' * 32}.png").write_bytes(_png())
            asyncio.run(media_store.record(f"{'b' * 32}.png", OWNER["id"]))
            with mock.patch.object(image_service, "IMAGE_DIR", Path(tmp)):
                ok = self.client.get(f"/images/{'b' * 32}.png")
                self.assertEqual(ok.status_code, 200)
                self.assertIn("private", ok.headers["cache-control"])
                self.assertEqual(self.client.get(f"/images/{'c' * 32}.png").status_code, 404)
                self.assertEqual(self.client.get("/images/..%2Fspark-ai-hub.db").status_code, 404)

    def test_media_is_private_to_its_owner(self):
        name = f"{'e' * 32}.png"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / name).write_bytes(_png())
            (Path(tmp) / f"{'f' * 32}.png").write_bytes(_png())      # made before owners were kept
            asyncio.run(media_store.record(name, OWNER["id"]))
            with mock.patch.object(image_service, "IMAGE_DIR", Path(tmp)):
                get = lambda n, who: self.client.get(f"/images/{n}", headers={"x-test-user": who})
                # Someone else's file answers exactly like a missing one.
                self.assertEqual(get(name, "other").status_code, 404)
                self.assertEqual(get(name, "admin").status_code, 404)
                anon = get(name, "anon")
                self.assertEqual(anon.status_code, 401)
                self.assertIn("www-authenticate", anon.headers)
                page = self.client.get(f"/images/{name}", headers={"x-test-user": "anon",
                                                                    "accept": "text/html"})
                self.assertEqual(page.status_code, 401)
                self.assertIn("Sign in", page.text)
                # A file with no recorded owner is the admins'.
                self.assertEqual(get(f"{'f' * 32}.png", "owner").status_code, 404)
                self.assertEqual(get(f"{'f' * 32}.png", "admin").status_code, 200)

    def test_edit_refuses_someone_elses_image(self):
        name = f"{'9' * 32}.png"

        async def go(tmp):
            (Path(tmp) / name).write_bytes(_png())
            await media_store.record(name, OTHER["id"])
            with self.assertRaisesRegex(image_service.ImageError, "No Hub image"):
                await image_service.load_input(f"http://spark.local:9000/images/{name}", None, OWNER)
            self.assertEqual(await image_service.load_input(f"/images/{name}", None, OTHER), _png())

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(image_service, "IMAGE_DIR", Path(tmp)):
                asyncio.run(go(tmp))


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

    def test_hub_url_input_and_inline_data_refused(self):
        png = _png()

        async def go(tmp):
            # Inline base64 is refused and the agent is pointed at create_upload.
            with self.assertRaisesRegex(image_service.ImageError, "create_upload"):
                await image_service.load_input(
                    "data:image/png;base64," + base64.b64encode(png).decode(), None, OWNER)
            (Path(tmp) / f"{'d' * 32}.png").write_bytes(png)
            await media_store.record(f"{'d' * 32}.png", OWNER["id"])
            hub = await image_service.load_input(
                f"https://spark.example.ts.net/images/{'d' * 32}.png", None, OWNER)
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


class WebToolTests(unittest.TestCase):
    def test_results_are_numbered_with_url_snippet_and_infobox(self):
        text = web_service.format_results({
            "infoboxes": [{"infobox": "Rust", "content": "A language.",
                           "urls": [{"url": "https://en.wikipedia.org/wiki/Rust"}]}],
            "results": [{"title": "A", "url": "https://a.example", "content": "first",
                         "publishedDate": "2026-09-01T00:00:00"},
                        {"title": "B", "url": "https://b.example", "content": ""},
                        {"title": "C", "url": "https://c.example"}],
        }, 2)
        self.assertIn("Rust: A language. (https://en.wikipedia.org/wiki/Rust)", text)
        self.assertIn("1. A\n   https://a.example\n   first\n   Published: 2026-09-01", text)
        self.assertIn("2. B\n   https://b.example", text)
        self.assertNotIn("c.example", text)

    def test_no_results_names_the_engines_that_failed(self):
        text = web_service.format_results(
            {"results": [], "unresponsive_engines": [["brave", "too many requests"]]}, 8)
        self.assertIn("brave (too many requests)", text)

    def test_fetch_is_https_and_public_only(self):
        async def go():
            for url in ("http://example.com/", "file:///etc/passwd",
                        "https://127.0.0.1:9010/api/admin/users", "https://localhost/"):
                with self.assertRaises(web_service.WebError, msg=url):
                    await web_service.fetch(url)
        asyncio.run(go())

    def test_only_an_admin_reaches_the_lan_and_nobody_the_spark(self):
        admin, member = {"id": 1, "role": "admin"}, {"id": 2, "role": "user"}
        spark = [ipaddress.ip_network(n) for n in
                 ("127.0.0.0/8", "::1/128", "192.168.3.219/32", "172.21.0.0/16")]

        def resolve(ip):
            return lambda *a, **k: [(socket.AF_INET, 0, 0, "", (ip, 80))]

        with mock.patch.object(web_service, "_spark_networks", return_value=spark):
            for ip, as_admin, as_member in (
                    ("93.184.215.14", "public", "public"),   # the internet
                    ("192.168.3.40", "lan", None),           # a device at home
                    ("100.90.1.2", "lan", None),             # the tailnet
                    ("192.168.3.219", None, None),           # the Spark's own address
                    ("127.0.0.1", None, None),               # the daemon, the Spark's services
                    ("172.21.0.5", None, None)):             # a recipe's container
                with mock.patch.object(web_service.socket, "getaddrinfo", resolve(ip)):
                    self.assertEqual(web_service.reach("h", 80, admin), as_admin, ip)
                    self.assertEqual(web_service.reach("h", 80, member), as_member, ip)
                    self.assertEqual(web_service.reach("h", 80, None), as_member, ip)

    def test_plain_http_only_inside_the_local_network(self):
        async def go():
            with mock.patch.object(web_service, "reach", return_value="public"):
                with self.assertRaises(web_service.WebError):
                    await web_service.fetch("http://example.com/", {"role": "admin"})
        asyncio.run(go())

    def test_html_keeps_the_content_and_drops_the_furniture(self):
        text = web_service._html_to_markdown(
            "<html><head><title>T</title><script>evil()</script></head><body>"
            "<nav>Menu</nav><main><h2>Heading</h2><p>Body <a href='https://x.example'>link</a></p>"
            "</main><footer>Legal</footer></body></html>", "https://x.example/docs/")
        self.assertIn("## Heading", text)
        self.assertIn("[link](https://x.example)", text)
        self.assertIn("[next](https://x.example/docs/next)", web_service._html_to_markdown(
            "<body><p><a href='next'>next</a></p></body>", "https://x.example/docs/"))
        for gone in ("evil", "Menu", "Legal"):
            self.assertNotIn(gone, text)

    def test_empty_query_is_a_tool_error(self):
        r = _client().post("/mcp", json=_rpc("tools/call", {
            "name": "web_search", "arguments": {"query": " "}}))
        self.assertTrue(r.json()["result"]["isError"])


class ReadCappedTests(unittest.TestCase):
    def test_reads_every_chunk_not_just_the_first(self):
        chunks = [b"a" * 70_000, b"b" * 70_000, b"c" * 10]

        class Content:
            async def iter_chunked(self, _size):
                for chunk in chunks:
                    yield chunk

        response = mock.Mock(content=Content())
        self.assertEqual(len(asyncio.run(web_service.read_capped(response, 1_000_000))), 140_010)
        self.assertGreater(len(asyncio.run(web_service.read_capped(response, 100_000))), 100_000)


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
        self.assertEqual(check.call_args.args[:2], ("j", mcp.CHECK_WAIT))

    def test_get_video_in_progress_is_not_an_error(self):
        info = {"job_id": "j", "model": "wan", "status": "in_progress", "progress": 40}
        with mock.patch.object(video_service, "check", mock.AsyncMock(return_value=info)):
            r = self.client.post("/mcp", json=_rpc("tools/call", {
                "name": "get_video", "arguments": {"job_id": "j"}}))
        result = r.json()["result"]
        self.assertFalse(result["isError"])
        self.assertIn("40%", result["content"][0]["text"])
        self.assertEqual(result["structuredContent"]["next_call"]["tool"], "get_video")

    def test_video_file_route_rejects_bad_names(self):
        self.assertEqual(self.client.get("/videos/../../etc.mp4").status_code, 404)
        self.assertEqual(self.client.get(f"/videos/{'b' * 32}.mp4").status_code, 404)


class _FakeVideoServer:
    """Stands in for aiohttp.ClientSession: records the one POST /v1/videos."""
    sent: dict = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data=None, headers=None, json=None):
        _FakeVideoServer.sent = {"url": url, "data": data, "json": json}

        class _Resp:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def json(self):
                return {"id": "remote-1", "status": "queued"}
        return _Resp()


class VideoInputTests(unittest.TestCase):
    """Every input H3 takes reaches it, in the shape its server reads."""

    FL2VA = dict(seconds=5, flow_shift=12.0, extra_params={
        "task": "t2va", "conditions": [], "audio_flow_shift": 3.0,
        "target": {"short_edge": 768, "aspect_ratio": "16:9", "duration_seconds": 5.0}},
        aspect_ratios=["16:9", "9:16", "21:9", "4:3", "1:1", "3:4"], max_seconds=15)

    def _start(self, defaults, kinds, **kw):
        from daemon.models.recipe import RecipeVideoDefaults
        backend = video_service.Backend(slug="h3", kinds=frozenset(kinds), summary="H3",
                                        defaults=RecipeVideoDefaults(**defaults))
        png = io.BytesIO()
        Image.new("RGB", (8, 8)).save(png, format="PNG")
        with mock.patch.object(video_service, "pick_backend", mock.AsyncMock(return_value=backend)) as pick, \
                mock.patch.object(video_service, "load_input",
                                  mock.AsyncMock(side_effect=lambda ref, *a, **k: png.getvalue() if ref.endswith("png")
                                                 else b"RIFF....WAVE" if ref.endswith("wav") else _MP4)), \
                mock.patch.object(video_service.aiohttp, "ClientSession", _FakeVideoServer), \
                mock.patch.object(video_service.proxy_service, "internal_url", return_value="http://h3"):
            kw = {"prompt": "p", "image": None, "seconds": None, "aspect_ratio": None,
                  "seed": 1, "steps": None, "model": None, **kw}
            asyncio.run(video_service.start(**kw))
        return pick.call_args.args[0], _FakeVideoServer.sent

    def test_text_alone_stays_t2va(self):
        needs, sent = self._start(self.FL2VA, {"text", "image", "last_image"})
        self.assertEqual(needs, {"text"})
        extra = sent["json"]["extra_params"]
        self.assertEqual((extra["task"], extra["conditions"], extra["target"]["aspect_ratio"]),
                         ("t2va", [], "16:9"))

    def test_first_and_last_frame_turn_t2va_into_fl2va_keyframes(self):
        needs, sent = self._start(self.FL2VA, {"text", "image", "last_image"},
                                  image="https://x/a.png", last_image="https://x/b.png")
        self.assertEqual(needs, {"image", "last_image"})
        extra = sent["json"]["extra_params"]
        self.assertEqual(extra["task"], "fl2va")
        self.assertEqual([(c["role"], c["frame_index"]) for c in extra["conditions"]],
                         [("keyframe", 0), ("keyframe", -1)])
        self.assertEqual(extra["target"]["aspect_ratio"], "auto")   # follows the picture

    def test_last_frame_alone_is_a_last_keyframe(self):
        _, sent = self._start(self.FL2VA, {"text", "image", "last_image"},
                              last_image="https://x/b.png", aspect_ratio="1:1")
        extra = sent["json"]["extra_params"]
        self.assertEqual([c["frame_index"] for c in extra["conditions"]], [-1])
        self.assertEqual(extra["target"]["aspect_ratio"], "1:1")

    def test_ref2va_takes_clip_pictures_sound_and_a_last_frame(self):
        d = {**self.FL2VA, "extra_params": {**self.FL2VA["extra_params"], "task": "ref2va"}}
        needs, sent = self._start(d, {"image", "video", "images", "audio", "last_image"},
                                  video="https://x/v.mp4", images=["https://x/a.png", "https://x/b.png"],
                                  audio="https://x/s.wav", last_image="https://x/c.png")
        self.assertEqual(needs, {"image", "images", "video", "audio", "last_image"})
        extra = sent["json"]["extra_params"]
        self.assertEqual(extra["task"], "ref2va")
        self.assertEqual([(c["type"], c["role"], c.get("frame_index")) for c in extra["conditions"]],
                         [("video", "reference", None), ("image", "reference", None),
                          ("image", "reference", None), ("image", "keyframe", -1),
                          ("audio", "reference", None)])
        self.assertTrue(extra["conditions"][-1]["uri"].startswith("data:audio/wav;base64,"))

    def test_multipart_fl2va_sends_ordered_references_and_frame_indices(self):
        d = dict(fps=24, seconds=8, flow_shift=12.0, send_aspect_ratio=True,
                 extra_params={"task": "t2va", "duration": 8.0, "short_edge": 576})
        _, sent = self._start(d, {"text", "image", "last_image"},
                              image="https://x/a.png", last_image="https://x/b.png")
        parts = {f[0]["name"]: f for f in sent["data"]._fields}
        names = [f[0]["name"] for f in sent["data"]._fields]
        self.assertEqual(names.count("input_references"), 2)
        self.assertNotIn("aspect_ratio", names)
        extra = json.loads(parts["extra_params"][2])
        self.assertEqual((extra["task"], extra["frame_indices"]), ("fl2va", [0, -1]))

    def test_shape_and_length_are_checked_against_the_model(self):
        with self.assertRaisesRegex(image_service.ImageError, "renders 16:9, 9:16"):
            self._start(dict(seconds=5, size="1280x704"), {"text"}, aspect_ratio="1:1")
        with self.assertRaisesRegex(image_service.ImageError, "up to 10 s"):
            self._start(dict(seconds=5, size="1280x704"), {"text"}, seconds=12)
        self._start(self.FL2VA, {"text"}, seconds=15, aspect_ratio="21:9")


_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64


class _MediaDirsTest(unittest.TestCase):
    """Every media folder in a temp dir; no tests of its own."""

    def setUp(self):
        self.client = _client()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(image_service, "IMAGE_DIR", root / "images"),
            mock.patch.object(image_service, "UPLOAD_DIR", root / "images" / "uploads"),
            mock.patch.object(video_service, "VIDEO_DIR", root / "videos"),
            mock.patch.object(video_service, "UPLOAD_DIR", root / "videos" / "uploads"),
            mock.patch.object(audio_service, "AUDIO_DIR", root / "audio"),
            mock.patch.object(audio_service, "UPLOAD_DIR", root / "audio" / "uploads"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()


class UploadTests(_MediaDirsTest):
    def test_sound_upload_is_stored_as_wav(self):
        wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
        info = upload_service.save(wav)
        self.assertEqual(info["kind"], "audio")
        self.assertTrue(info["path"].startswith("/audio/") and info["path"].endswith(".wav"))
        self.assertIsNotNone(media_store.find(info["path"].rsplit("/", 1)[1]))
        self.assertTrue(upload_service.is_upload(media_store.find(info["path"].rsplit("/", 1)[1])))

    def test_image_upload_gives_a_private_hub_url_the_tools_accept(self):
        buf = io.BytesIO()
        Image.new("RGB", (40, 20), (1, 2, 3)).save(buf, format="JPEG")
        r = self.client.post("/api/uploads", content=buf.getvalue())
        self.assertEqual(r.status_code, 200, r.text)
        info = r.json()
        self.assertEqual((info["kind"], info["width"], info["height"]), ("image", 40, 20))
        self.assertRegex(info["url"], r"^http://testserver/images/[0-9a-f]{32}\.png$")
        path = urllib_path(info["url"])
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get(path).headers["cache-control"], "private, max-age=3600")
        self.assertEqual(self.client.get(path, headers={"x-test-user": "other"}).status_code, 404)
        stored = asyncio.run(image_service.load_input(info["url"], None, OWNER))
        self.assertEqual(Image.open(io.BytesIO(stored)).size, (40, 20))

    def test_video_upload(self):
        r = self.client.post("/api/uploads", content=_MP4)
        info = r.json()
        self.assertEqual(info["kind"], "video")
        self.assertEqual(self.client.get(urllib_path(info["url"])).content, _MP4)
        stored = asyncio.run(image_service.load_input(info["url"], None, OWNER, suffix=".mp4",
                                                      max_bytes=video_service.MAX_VIDEO_INPUT_BYTES))
        self.assertEqual(stored, _MP4)

    def test_heic_and_avif_photos_are_images_not_videos(self):
        """They share MP4's `ftyp` container; the brand says they are stills."""
        for fmt in ("HEIF", "AVIF"):
            buf = io.BytesIO()
            Image.new("RGB", (40, 20), (1, 2, 3)).save(buf, format=fmt)
            self.assertEqual(buf.getvalue()[4:8], b"ftyp")
            info = self.client.post("/api/uploads", content=buf.getvalue()).json()
            self.assertEqual((info["kind"], info["width"], info["height"]), ("image", 40, 20), fmt)
            self.assertRegex(info["url"], r"/images/[0-9a-f]{32}\.png$")

    def test_unreadable_upload_is_refused(self):
        self.assertEqual(self.client.post("/api/uploads", content=b"not an image").status_code, 400)
        self.assertEqual(self.client.post("/api/uploads", content=b"").status_code, 400)

    def test_purge_drops_uploads_after_a_week_and_results_after_a_month(self):
        import os
        import time as _time
        day = 24 * 3600

        def put(folder, name, age_days):
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / name
            path.write_bytes(b"x")
            then = _time.time() - age_days * day
            os.utime(path, (then, then))
            return path

        old_upload = put(image_service.UPLOAD_DIR, f"{'1' * 32}.png", 8)
        new_upload = put(image_service.UPLOAD_DIR, f"{'2' * 32}.png", 6)
        old_video_upload = put(video_service.UPLOAD_DIR, f"{'3' * 32}.mp4", 8)
        week_result = put(image_service.IMAGE_DIR, f"{'4' * 32}.png", 8)
        old_result = put(image_service.IMAGE_DIR, f"{'5' * 32}.png", 31)
        old_video = put(video_service.VIDEO_DIR, f"{'6' * 32}.mp4", 31)
        old_song = put(audio_service.AUDIO_DIR, f"{'7' * 32}.wav", 31)
        stranger = put(image_service.IMAGE_DIR, "notes.txt", 400)
        asyncio.run(media_store.record(old_result.name, OWNER["id"]))

        self.assertEqual(asyncio.run(media_store.purge_expired()), 5)
        gone = [old_upload, old_video_upload, old_result, old_video, old_song]
        self.assertEqual([p.exists() for p in gone], [False] * 5)
        self.assertTrue(all(p.exists() for p in (new_upload, week_result, stranger)))
        # Its owner row went with it.
        self.assertFalse(asyncio.run(media_store.can_read(old_result.name, OWNER)))


class MyFilesTests(_MediaDirsTest):
    def _upload(self, who="owner"):
        r = self.client.post("/api/uploads", content=_png(), headers={"x-test-user": who})
        return r.json()["url"].rsplit("/", 1)[1]

    def test_each_account_lists_only_its_own_files(self):
        mine, theirs = self._upload("owner"), self._upload("other")
        image_service.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        (image_service.IMAGE_DIR / f"{'8' * 32}.png").write_bytes(_png())   # ownerless, pre-owners
        names = lambda who: [(f["name"], f["upload"]) for f in
                             self.client.get("/api/media", headers={"x-test-user": who}).json()["files"]]
        self.assertEqual(names("owner"), [(mine, True)])
        self.assertEqual(names("other"), [(theirs, True)])
        self.assertEqual(names("admin"), [("8" * 32 + ".png", False)])   # plus the ownerless
        self.assertEqual(self.client.get("/api/media", headers={"x-test-user": "anon"}).status_code, 401)
        info = self.client.get("/api/media").json()
        self.assertEqual((info["results_days"], info["uploads_days"]), (30, 7))

    def test_delete_own_file_only(self):
        mine, theirs = self._upload("owner"), self._upload("other")
        self.assertEqual(self.client.delete(f"/api/media/{theirs}").status_code, 404)
        self.assertEqual(self.client.get(f"/images/{theirs}", headers={"x-test-user": "other"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/media/{mine}").status_code, 200)
        self.assertEqual(self.client.get(f"/images/{mine}").status_code, 404)
        self.assertEqual(self.client.get("/api/media").json()["files"], [])
        self.assertEqual(self.client.delete("/api/media/..%2F..%2Fspark-ai-hub.db").status_code, 404)

    def test_only_admins_see_and_delete_an_ownerless_file(self):
        image_service.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        old = image_service.IMAGE_DIR / f"{'8' * 32}.png"
        old.write_bytes(_png())
        self.assertEqual(self.client.delete(f"/api/media/{old.name}",
                                            headers={"x-test-user": "owner"}).status_code, 404)
        self.assertEqual(self.client.get(f"/images/{old.name}",
                                         headers={"x-test-user": "admin"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/media/{old.name}",
                                            headers={"x-test-user": "admin"}).status_code, 200)
        self.assertFalse(old.exists())

    def test_thumbnails_are_small_and_owner_only(self):
        mine = self._upload("owner")
        r = self.client.get(f"/api/media/{mine}/thumb")
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"))
        self.assertLessEqual(max(Image.open(io.BytesIO(r.content)).size), files.THUMB_EDGE)
        self.assertEqual(self.client.get(f"/api/media/{mine}/thumb",
                                         headers={"x-test-user": "other"}).status_code, 404)


class LinkTests(_MediaDirsTest):
    """create_upload / create_download: the link is the only credential."""

    def _tool(self, name, args=None, who="owner"):
        r = self.client.post("/mcp", json=_rpc("tools/call", {"name": name, "arguments": args or {}}),
                             headers={"x-test-user": who})
        return r.json()["result"]

    def _upload_link(self, who="owner"):
        result = self._tool("create_upload", who=who)
        self.assertFalse(result["isError"], result)
        return urllib_path(result["structuredContent"]["upload_url"])

    def _db(self, sql, *params):
        async def go():
            db = await db_module.get_db()
            await db.execute(sql, params)
            await db.commit()
            await db.close()
        asyncio.run(go())

    def test_upload_link_works_once_without_a_key_and_owns_the_file(self):
        link = self._upload_link()
        r = self.client.post(link, content=_png(), headers={"x-test-user": "anon"})
        self.assertEqual(r.status_code, 200, r.text)
        name = urllib_path(r.json()["url"]).rsplit("/", 1)[1]
        self.assertTrue(asyncio.run(media_store.can_read(name, OWNER)))
        self.assertFalse(asyncio.run(media_store.can_read(name, OTHER)))
        again = self.client.post(link, content=_png(), headers={"x-test-user": "anon"})
        self.assertEqual(again.status_code, 404)
        self.assertIn("create_upload", again.json()["detail"])

    def test_refused_upload_leaves_the_link_usable(self):
        link = self._upload_link()
        self.assertEqual(self.client.post(link, content=b"nope", headers={"x-test-user": "anon"}).status_code, 400)
        self.assertEqual(self.client.post(link, content=_png(), headers={"x-test-user": "anon"}).status_code, 200)

    def test_expired_or_bogus_upload_link(self):
        link = self._upload_link()
        self._db("UPDATE media_links SET expires_at = 0")
        self.assertEqual(self.client.post(link, content=_png()).status_code, 404)
        self.assertEqual(self.client.post(link_service.UPLOAD_PREFIX + "x" * 43, content=_png()).status_code, 404)

    def test_suspended_account_link_stops_working(self):
        link = self._upload_link("other")
        self._db("UPDATE users SET status = 'rejected' WHERE id = ?", OTHER["id"])
        try:
            self.assertEqual(self.client.post(link, content=_png()).status_code, 404)
        finally:
            self._db("UPDATE users SET status = 'active' WHERE id = ?", OTHER["id"])
        # ...and was handed back, so it works again once the account does.
        self.assertEqual(self.client.post(link, content=_png()).status_code, 200)

    def test_download_link_for_own_files_only(self):
        mine = urllib_path(self.client.post(self._upload_link(), content=_png()).json()["url"])
        result = self._tool("create_download", {"url": "http://spark.local:9000" + mine})
        self.assertFalse(result["isError"], result)
        link = urllib_path(result["structuredContent"]["download_url"])
        for _ in range(2):              # retries are fine until it expires
            r = self.client.get(link, headers={"x-test-user": "anon"})
            self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"))
            self.assertEqual(r.content, (image_service.UPLOAD_DIR / mine.rsplit("/", 1)[1]).read_bytes())
            self.assertIn("no-store", r.headers["cache-control"])
        refused = self._tool("create_download", {"url": mine}, who="other")
        self.assertTrue(refused["isError"])
        self._db("UPDATE media_links SET expires_at = 0")
        self.assertEqual(self.client.get(link).status_code, 404)

    def test_download_link_dies_with_the_file(self):
        mine = urllib_path(self.client.post(self._upload_link(), content=_png()).json()["url"])
        link = urllib_path(self._tool("create_download", {"url": mine})["structuredContent"]["download_url"])
        self.client.delete(f"/api/media/{mine.rsplit('/', 1)[1]}")
        self.assertEqual(self.client.get(link).status_code, 404)

    def test_links_need_an_account(self):
        self.assertTrue(self._tool("create_upload", who="anon")["isError"])
        self.assertTrue(self._tool("create_download", {"url": "/images/" + "a" * 32 + ".png"})["isError"])


def urllib_path(url: str) -> str:
    import urllib.parse
    return urllib.parse.urlsplit(url).path


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

            async def slow_run():
                started.set()
                await asyncio.sleep(0.3)
                return SimpleNamespace(path="/images/abc.png", model="m", width=8, height=8,
                                       seed=1, steps=None, preset=None, preview_jpeg_b64="x")

            job_id = image_service.start_job("edit", slow_run, "m", OWNER)
            # The caller gives up while the render is still going.
            pending = await image_service.check_job(job_id, 0, OWNER)
            self.assertEqual((pending["status"], pending["result"]), ("rendering", None))
            await started.wait()
            done = await image_service.check_job(job_id, 5, OWNER)
            self.assertEqual((done["status"], done["result"].path), ("completed", "/images/abc.png"))
            # Collectable again afterwards -- by the account that started it only.
            self.assertEqual((await image_service.check_job(job_id, 0, OWNER))["status"], "completed")
            with self.assertRaisesRegex(image_service.ImageError, "No job"):
                await image_service.check_job(job_id, 0, OTHER)
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
        with self.assertRaisesRegex(image_service.ImageError, "No job"):
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
            with self.assertRaisesRegex(image_service.ImageError, "Start a-installed with start_model"):
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
            for ref in ("http://127.0.0.1:9010/x.mp4", "/etc/passwd", "data:video/mp4;base64,AAAA",
                        f"/images/{'a' * 32}.png"):     # an image URL is not a video input
                with self.assertRaises(image_service.ImageError):
                    await image_service.load_input(ref, None, OWNER, suffix=".mp4",
                                                   max_bytes=video_service.MAX_VIDEO_INPUT_BYTES)
        asyncio.run(go())

    def test_music_body(self):
        self.assertEqual(
            audio_service.music_body(lyrics="[Verse]\nla", style="lofi", seconds=30, seed=3),
            {"input": "[Verse]\nla", "instructions": "lofi", "seed": 3,
             "max_new_tokens": 750, "response_format": "wav"})

    def test_every_render_returns_a_job_then_get_collects_it(self):
        """One shape for all media: start returns a job_id at once, get_* collects it."""
        from types import SimpleNamespace
        song = {"audio_id": "c" * 32, "model": "m", "seconds": 30.0, "seed": 3}
        picture = SimpleNamespace(path="/images/abc.png", model="i", width=8, height=8,
                                  seed=1, steps=None, preset=None, preview_jpeg_b64="x")
        with mock.patch.object(audio_service, "pick_backend",
                               mock.AsyncMock(return_value=SimpleNamespace(slug="m"))), \
                mock.patch.object(audio_service, "generate", mock.AsyncMock(return_value=song)), \
                mock.patch.object(image_service, "pick_backend",
                                  mock.AsyncMock(return_value=SimpleNamespace(slug="i"))), \
                mock.patch.object(image_service, "run", mock.AsyncMock(return_value=picture)):
            for start, args, get, expect in (
                    ("generate_music", {"lyrics": "[Verse]\nla", "style": "lofi"}, "get_music",
                     f"/audio/{'c' * 32}.wav"),
                    ("generate_image", {"prompt": "a cat"}, "get_image", "/images/abc.png"),
                    ("edit_image", {"prompt": "red", "images": ["/images/x.png"]}, "get_image",
                     "/images/abc.png")):
                started = self.client.post("/mcp", json=_rpc("tools/call", {
                    "name": start, "arguments": args})).json()["result"]
                self.assertFalse(started["isError"])
                self.assertIn("Not done yet", started["content"][0]["text"])
                call = started["structuredContent"]["next_call"]
                self.assertEqual(call["tool"], get)
                done = self.client.post("/mcp", json=_rpc("tools/call", {
                    "name": get, "arguments": call["arguments"]})).json()["result"]
                self.assertFalse(done["isError"])
                self.assertIn(expect, done["content"][0]["text"])

    def test_no_tool_offers_a_wait_knob(self):
        for tool in mcp.TOOLS:
            self.assertNotIn("wait_seconds", tool["inputSchema"].get("properties", {}), tool["name"])

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


class StartStopModelTests(unittest.TestCase):
    """Agents start and stop the media models they list -- and nothing else."""

    def setUp(self):
        from types import SimpleNamespace
        recipe = lambda name, gb, *tags: SimpleNamespace(
            name=name, tags=list(tags), image_defaults=None, video_defaults=None, audio_defaults=None,
            memory_gb=gb, requirements=SimpleNamespace(min_memory_gb=gb))
        self.recipes = {
            "img-big": recipe("Big image", 60, "openai-images", "text-to-image"),
            "img-small": recipe("Small image", 20, "openai-images", "text-to-image"),
            "llm": recipe("Chat LLM", 80),
        }
        self.running = {"img-small", "llm"}
        self.ready = {"img-small", "llm"}
        self.launch = mock.AsyncMock(side_effect=lambda slug, user, hub_url: self.running.add(slug))
        self.stop = mock.AsyncMock(side_effect=lambda slug: (self.running.discard(slug),
                                                             {"status": "stopped"})[1])
        get_recipes = mock.Mock(return_value=self.recipes)
        self.patches = [
            *(mock.patch.object(m, "get_recipes", get_recipes)
              for m in (mcp, image_service, video_service, audio_service, decision_service)),
            mock.patch.object(mcp, "get_installed_slugs", mock.AsyncMock(return_value=set(self.recipes))),
            mock.patch.object(mcp, "is_recipe_running",
                              mock.AsyncMock(side_effect=lambda slug: slug in self.running)),
            mock.patch.object(mcp, "is_ready", side_effect=lambda slug: slug in self.ready),
            mock.patch.object(mcp, "get_pending", return_value=None),
            mock.patch.object(mcp, "start_health_check", mock.AsyncMock()),
            mock.patch.object(mcp, "READY_POLL_SECONDS", 0.01),
            mock.patch.object(mcp, "CHECK_WAIT", 0.05),
            mock.patch.object(mcp.containers, "launch_app", self.launch),
            mock.patch.object(mcp.containers, "stop", self.stop),
            # The memory check itself lives in docker_service.
            mock.patch("daemon.services.registry_service.get_recipes", get_recipes),
            mock.patch.object(docker_service, "get_installed_slugs",
                              mock.AsyncMock(return_value=set(self.recipes))),
            mock.patch.object(docker_service, "is_recipe_running",
                              mock.AsyncMock(side_effect=lambda slug: slug in self.running)),
            mock.patch.object(docker_service, "is_ready", side_effect=lambda slug: slug in self.ready),
            mock.patch.object(docker_service, "get_pending", return_value=None),
            mock.patch.object(docker_service, "available_memory_gb", return_value=50.0),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def call(self, name, **args):
        return asyncio.run(mcp.call_tool(name, args, "http://h", user=OWNER))

    def test_only_installed_media_models(self):
        for name in ("start_model", "stop_model"):
            result = self.call(name, model="llm")
            self.assertTrue(result["isError"])
            self.assertIn("not an installed image, video, music or decision model",
                          result["content"][0]["text"])
        self.launch.assert_not_called()
        self.stop.assert_not_called()

    def test_start_refused_when_memory_is_short_names_what_is_running(self):
        result = self.call("start_model", model="img-big")
        text = result["content"][0]["text"]
        self.assertTrue(result["isError"])
        self.assertIn("img-big needs about 60 GB and only 50 GB is free", text)
        self.assertIn("stop with stop_model: img-small", text)
        self.assertIn("only the Hub can stop these): Chat LLM", text)
        self.launch.assert_not_called()

    def test_a_loading_app_counts_against_free_memory(self):
        self.running.discard("img-small")
        self.ready.discard("llm")
        result = self.call("start_model", model="img-small")
        self.assertTrue(result["isError"])   # 50 GB free, but the LLM still loading takes 80
        self.launch.assert_not_called()

    def test_start_launches_then_reports_starting_then_ready(self):
        self.running.discard("img-small")
        self.ready.discard("img-small")
        pending = self.call("start_model", model="img-small")
        self.launch.assert_awaited_once_with("img-small", OWNER, "http://h")
        self.assertFalse(pending["isError"])
        self.assertEqual(pending["structuredContent"]["next_call"],
                         {"tool": "start_model", "arguments": {"model": "img-small"}})
        self.ready.add("img-small")
        done = self.call("start_model", model="img-small")
        self.assertEqual(done["structuredContent"]["status"], "ready")
        self.launch.assert_awaited_once()     # already running: no second launch

    def test_start_reports_a_model_that_died_while_loading(self):
        self.running.discard("img-small")
        self.ready.discard("img-small")
        self.launch.side_effect = None        # compose up returned, container gone
        result = self.call("start_model", model="img-small")
        self.assertTrue(result["isError"])
        self.assertIn("stopped before it finished loading", result["content"][0]["text"])

    def test_stop_refused_while_rendering(self):
        with image_service.rendering("img-small"):
            result = self.call("stop_model", model="img-small")
        self.assertTrue(result["isError"])
        self.assertIn("rendering 1 job right now", result["content"][0]["text"])
        self.stop.assert_not_called()
        self.assertEqual(self.call("stop_model", model="img-small")["structuredContent"]["status"],
                         "stopped")
        self.stop.assert_awaited_once_with("img-small")

    def test_stop_refused_while_a_video_job_is_open(self):
        with mock.patch.object(video_service, "rendering_on", mock.AsyncMock(return_value=2)):
            result = self.call("stop_model", model="img-small")
        self.assertIn("rendering 2 jobs right now", result["content"][0]["text"])
        self.stop.assert_not_called()

    def test_stopping_a_stopped_model_is_a_no_op(self):
        result = self.call("stop_model", model="img-big")
        self.assertEqual((result["isError"], result["structuredContent"]["status"]), (False, "stopped"))
        self.stop.assert_not_called()


class _FakeDecisionServer:
    """Stands in for aiohttp.ClientSession: records the one POST /v1/systemone."""
    sent: dict = {}
    reply = (200, {"model": "clef-flash", "answers": {}})

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        _FakeDecisionServer.sent = {"url": url, "json": json}
        status, body = _FakeDecisionServer.reply

        class _Resp:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def json(self, content_type=None):
                return body
        _Resp.status = status
        return _Resp()


class DecisionToolTests(unittest.TestCase):
    """decide: typed questions about text and media, answered by a systemone-api model."""

    CATEGORY = {"category": {"type": "choice", "instructions": "Which category?",
                             "criteria": {"stage": "A stage", "crowd": "An audience"}}}

    def setUp(self):
        recipes = {"clef": SimpleNamespace(tags=["systemone-api"]),
                   "img": SimpleNamespace(tags=["openai-images", "text-to-image"])}
        self.running = {"clef"}
        self.patches = [
            mock.patch.object(decision_service, "get_recipes", return_value=recipes),
            mock.patch.object(decision_service, "get_installed_slugs",
                              mock.AsyncMock(return_value=set(recipes))),
            mock.patch.object(decision_service, "is_recipe_running",
                              mock.AsyncMock(side_effect=lambda slug: slug in self.running)),
            mock.patch.object(decision_service, "is_ready", side_effect=lambda slug: slug in self.running),
            mock.patch.object(decision_service.aiohttp, "ClientSession", _FakeDecisionServer),
            mock.patch.object(decision_service.proxy_service, "internal_url", return_value="http://clef"),
            mock.patch.object(decision_service, "load_input",
                              mock.AsyncMock(side_effect=lambda ref, *a, **k: ref.encode())),
        ]
        for patch in self.patches:
            patch.start()
        _FakeDecisionServer.reply = (200, {"model": "clef-flash", "answers": {
            "category": {"type": "choice", "choice": "stage", "confidence": 0.9,
                         "probabilities": {"stage": 0.9, "crowd": 0.1}}}})

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def call(self, **args):
        return asyncio.run(mcp.call_tool("decide", args, "http://h", user=OWNER))

    def test_only_tagged_recipes_are_decision_models(self):
        self.assertEqual(decision_service.backends(), {"clef"})

    def test_media_go_as_base64_and_answers_come_back_in_the_call(self):
        result = self.call(state="Photos from Friday's show", questions=self.CATEGORY,
                           images=["https://x/a.png"], videos=["https://x/b.mp4"])
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["model"], "clef")
        self.assertEqual(result["structuredContent"]["answers"]["category"]["choice"], "stage")
        sent = _FakeDecisionServer.sent
        self.assertEqual(sent["url"], "http://clef/v1/systemone")
        self.assertEqual(sent["json"]["images"], [base64.b64encode(b"https://x/a.png").decode()])
        self.assertEqual(sent["json"]["videos"], [base64.b64encode(b"https://x/b.mp4").decode()])
        self.assertEqual(sent["json"]["questions"], self.CATEGORY)

    def test_questions_sent_as_json_text_are_accepted(self):
        for text in (json.dumps(self.CATEGORY), json.dumps(self.CATEGORY) + "}"):  # stray brace too
            result = self.call(state="s", questions=text)
            self.assertFalse(result["isError"])
            self.assertEqual(_FakeDecisionServer.sent["json"]["questions"], self.CATEGORY)

    def test_badly_formed_questions_are_tool_errors(self):
        for questions, message in (
                ({}, "map each question id"),
                ({"q": {"type": "maybe"}}, "must be choice, score or noul"),
                ({"q": {"type": "choice", "criteria": {"only": "one"}}}, "at least two option ids"),
                ({"q": {"type": "score", "criteria": {"a": "b"}}}, "at least two levels")):
            result = self.call(state="s", questions=questions)
            self.assertTrue(result["isError"])
            self.assertIn(message, result["content"][0]["text"])

    def test_no_running_model_says_how_to_start_one(self):
        self.running.clear()
        result = self.call(state="s", questions=self.CATEGORY)
        self.assertIn("Start clef with start_model", result["content"][0]["text"])

    def test_model_refusal_is_passed_on(self):
        _FakeDecisionServer.reply = (400, {"error": {"message": "at most 4 images"}})
        result = self.call(state="s", questions=self.CATEGORY)
        self.assertTrue(result["isError"])
        self.assertIn("clef refused the request: at most 4 images", result["content"][0]["text"])


class ProxiedApiRecipeTests(unittest.TestCase):
    def _recipe(self, **ui):
        from daemon.models.recipe import Recipe, RecipeUI
        return Recipe(name="x", slug="img", ui=RecipeUI(**ui))

    def test_proxied_api_recipe_links_to_its_api_path(self):
        r = self._recipe(type="api-only", port=30000, proxy=True, path="/v1/models")
        self.assertEqual(r.app_url, "/run/img/v1/models")

    def test_unproxied_api_recipe_keeps_host_port_fallback(self):
        self.assertEqual(self._recipe(type="api-only", port=8000, path="/v1/models").app_url, "")

    def test_proxy_routes_api_only_recipes(self):
        from daemon.services import proxy_service
        recipes = {
            "img": self._recipe(type="api-only", port=30000, proxy=True),
            "llm": self._recipe(type="api-only", port=8000),
        }
        with mock.patch("daemon.services.registry_service.get_recipes", return_value=recipes), \
             mock.patch.object(proxy_service, "_container_name", side_effect=lambda s: f"c-{s}"):
            self.assertEqual(proxy_service._proxied_recipes(), [("img", "c-img", 30000, True)])


if __name__ == "__main__":
    unittest.main()

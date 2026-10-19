import asyncio
import unittest
from unittest import mock

from daemon.models.recipe import Recipe, RecipeVideoDefaults
from daemon.services import lora_service


def _lora(slug="lora-x", bases=("base",), **defaults):
    return Recipe(name=slug, slug=slug, lora={"bases": list(bases), "file": "/lora/x.safetensors",
                                              "alpha": 16},
                  video_defaults=RecipeVideoDefaults(**defaults) if defaults else None)


class MergedDefaultsTests(unittest.TestCase):
    BASE = RecipeVideoDefaults(steps=9, seconds=5, flow_shift=6.0, notes="base", extra_params={
        "task": "t2va", "conditions": [], "target": {"short_edge": 768, "aspect_ratio": "16:9"}})

    def test_no_lora_keeps_the_base_settings(self):
        self.assertIs(lora_service.merged_defaults(self.BASE, None), self.BASE)

    def test_lora_settings_win_and_extra_params_merge_by_key(self):
        lora = _lora(steps=29, flow_shift=12.0, extra_params={"target": {"aspect_ratio": "1:1"}})
        merged = lora_service.merged_defaults(self.BASE, lora)
        self.assertEqual((merged.steps, merged.seconds, merged.flow_shift), (29, 5, 12.0))
        self.assertEqual(merged.extra_params["task"], "t2va")          # the base's, kept
        self.assertEqual(merged.extra_params["target"], {"aspect_ratio": "1:1"})
        self.assertEqual(self.BASE.steps, 9)                           # base untouched


class _Server:
    """SGLang's LoRA endpoints, recording what the Hub asked of them."""

    def __init__(self, active=None, status=200):
        self.active, self.status, self.calls = active, status, []

    async def call(self, session, method, slug, path, body=None):
        self.calls.append((method, path, body))
        if path == "/v1/list_loras":
            on = [{"nickname": self.active}] if self.active else []
            return 200, {"active": {"transformer": on} if on else {}}
        return self.status, {"message": "ok"}


class UseTests(unittest.TestCase):
    def _use(self, server, lora, busy=0, catalog=None):
        catalog = catalog if catalog is not None else {"lora-x": _lora()}
        async def jobs():
            return busy
        with mock.patch.object(lora_service, "_call", server.call), \
                mock.patch.object(lora_service, "catalog", return_value=catalog), \
                mock.patch.object(lora_service, "get_container_name", mock.AsyncMock(return_value="ct")), \
                mock.patch.object(lora_service, "_copy_in", mock.AsyncMock(return_value="/tmp/loras/lora-x.safetensors")):
            asyncio.run(lora_service.use("base", lora, jobs))
        return [(m, p) for m, p, _ in server.calls], server.calls

    def test_a_model_no_lora_loads_into_is_never_asked(self):
        calls, _ = self._use(_Server(), None, catalog={})
        self.assertEqual(calls, [])

    def test_the_loaded_lora_is_left_as_it_is(self):
        calls, _ = self._use(_Server(active="lora-x"), _lora())
        self.assertEqual(calls, [("GET", "/v1/list_loras")])

    def test_switching_on_sends_the_file_its_strength_and_alpha(self):
        calls, raw = self._use(_Server(), _lora())
        self.assertEqual(calls[-1], ("POST", "/v1/set_lora"))
        self.assertEqual(raw[-1][2], {"lora_nickname": "lora-x", "lora_path": "/tmp/loras/lora-x.safetensors",
                                      "strength": 1.0, "lora_alpha": 16, "merge_mode": "dynamic"})

    def test_a_lora_with_its_own_pipeline_and_embeddings_names_both(self):
        lora = Recipe(name="hdr", slug="lora-x", lora={
            "bases": ["base"], "file": "/lora/x.safetensors", "pipeline": "hdr",
            "text_embeddings": "/lora/emb.safetensors"})
        _, raw = self._use(_Server(), lora)
        body = raw[-1][2]
        self.assertEqual(body["pipeline"], "hdr")
        self.assertIn("text_embeddings_path", body)

    def test_an_ordinary_lora_sends_sglang_nothing_new(self):
        _, raw = self._use(_Server(), _lora())
        self.assertNotIn("pipeline", raw[-1][2])
        self.assertNotIn("text_embeddings_path", raw[-1][2])

    def test_no_lora_switches_a_loaded_one_off(self):
        calls, _ = self._use(_Server(active="lora-x"), None)
        self.assertEqual(calls[-1], ("POST", "/v1/unmerge_lora_weights"))

    def test_a_switch_waits_for_nothing_while_the_model_renders(self):
        with self.assertRaisesRegex(lora_service.LoraError, "rendering 1 job with no LoRA"):
            self._use(_Server(), _lora(), busy=1)

    def test_a_refused_switch_says_so(self):
        with self.assertRaisesRegex(lora_service.LoraError, "HTTP 500"):
            self._use(_Server(status=500), _lora())


if __name__ == "__main__":
    unittest.main()

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from daemon.models.recipe import Recipe
from daemon.services import link_lora_service, lora_keys


def bf16(values):
    return lora_keys._bf16(np.asarray(values, dtype=np.float32))


def f32(entry):
    return lora_keys._f32(*entry)


class KeyRewriteTests(unittest.TestCase):
    def convert(self, tensors, family):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp, "in.safetensors"), Path(tmp, "out.safetensors")
            lora_keys.write(src, tensors, {})
            n = lora_keys.convert(src, dst, family)
            return n, lora_keys.read(dst)[0]

    def test_kohya_names_and_alpha_fold_into_b(self):
        a, b = np.ones((4, 8)), np.ones((8, 4))
        n, out = self.convert({"blk.lora_down.weight": (bf16(a), "BF16"),
                               "blk.lora_up.weight": (bf16(b), "BF16"),
                               "blk.alpha": (np.array([2.0], np.float32).view(np.uint32), "F32")}, "")
        self.assertEqual(n, 1)
        self.assertEqual(sorted(out), ["blk.lora_A.weight", "blk.lora_B.weight"])
        self.assertTrue(np.allclose(f32(out["blk.lora_B.weight"]), 0.5))   # alpha 2 / rank 4

    def test_qwen_fused_gate_up_is_split(self):
        b = np.concatenate([np.full((6, 2), 1.0), np.full((6, 2), 2.0)])
        n, out = self.convert({"diffusion_model.transformer_blocks.0.img_mlp.gate_up.lora_A.weight": (bf16(np.ones((2, 4))), "BF16"),
                               "diffusion_model.transformer_blocks.0.img_mlp.gate_up.lora_B.weight": (bf16(b), "BF16")},
                              "qwen-image")
        self.assertEqual(n, 2)
        gate = f32(out["diffusion_model.transformer_blocks.0.img_mlp.gate_layer.lora_B.weight"])
        proj = f32(out["diffusion_model.transformer_blocks.0.img_mlp.proj.lora_B.weight"])
        self.assertTrue(np.all(gate == 1.0) and np.all(proj == 2.0))

    def test_moss_prefix_dropped_and_qkv_split(self):
        n, out = self.convert({"diffusion_model.layers.0.proj.qkv.lora_A.weight": (bf16(np.ones((2, 3))), "BF16"),
                               "diffusion_model.layers.0.proj.qkv.lora_B.weight": (bf16(np.ones((9, 2))), "BF16")},
                              "moss-image")
        self.assertEqual(n, 3)
        self.assertIn("layers.0.proj.k.lora_B.weight", out)
        self.assertEqual(out["layers.0.proj.v.lora_B.weight"][0].shape, (3, 2))

    def test_h3_block_diagonal_qkv_takes_its_own_blocks(self):
        r, o = 2, 3
        a = np.arange(3 * r * 4, dtype=np.float32).reshape(3 * r, 4)
        b = np.zeros((3 * o, 3 * r), np.float32)
        for i in range(3):
            b[i * o:(i + 1) * o, i * r:(i + 1) * r] = i + 1
        n, out = self.convert({"diffusion_model.blocks.0.attn.qkv_proj.lora_A.weight": (bf16(a), "BF16"),
                               "diffusion_model.blocks.0.attn.qkv_proj.lora_B.weight": (bf16(b), "BF16")},
                              "minimax-h3")
        self.assertEqual(n, 3)
        self.assertEqual(out["transformer_blocks.0.attn.to_k.lora_A.weight"][0].shape, (r, 4))
        self.assertTrue(np.all(f32(out["transformer_blocks.0.attn.to_v.lora_B.weight"]) == 3))

    def test_not_a_lora(self):
        with self.assertRaises(ValueError):
            self.convert({"model.weight": (bf16(np.ones((2, 2))), "BF16")}, "")


class _Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, **kw):
        return _Resp(200, self.payload)


CIVITAI = {"name": "Retro", "type": "LORA", "tags": [], "modelVersions": [
    {"id": 1, "name": "Krea 2", "baseModel": "Krea 2", "trainedWords": ["x"],
     "files": [{"name": "k.safetensors", "type": "Model", "downloadUrl": "u1", "sizeKB": 10}]},
    {"id": 2, "name": "Moss", "baseModel": "MOSS Image", "trainedWords": ["retro_artstyle, retro,"],
     "files": [{"name": "m.safetensors", "type": "Model", "downloadUrl": "u2", "sizeKB": 10}]},
]}


class CivitaiTests(unittest.TestCase):
    def test_the_version_for_an_installed_base_is_taken(self):
        moss = Recipe(name="MOSS", slug="sglang-moss-image-sft", engine="SGLang")
        with mock.patch.object(link_lora_service, "_bases_for", mock.AsyncMock(return_value=[moss])) as bases:
            found = asyncio.run(link_lora_service._civitai(_Session(CIVITAI), "https://civitai.com/models/9/retro", None))
        self.assertEqual(found["download"], "u2")
        self.assertEqual(found["trigger"], "retro_artstyle, retro")
        self.assertIn("modelVersionId=2", found["source"])
        # Krea 2 has no family on this Hub, so only MOSS bases were looked up.
        self.assertEqual([c.args[0] for c in bases.await_args_list], ["moss-image"])

    def test_no_fitting_version_names_what_it_is_for(self):
        with mock.patch.object(link_lora_service, "_bases_for", mock.AsyncMock(return_value=[])):
            with self.assertRaises(link_lora_service.LinkLoraError) as caught:
                asyncio.run(link_lora_service._civitai(_Session(CIVITAI), "https://civitai.com/models/9", None))
        self.assertIn("Krea 2", str(caught.exception))
        self.assertIn("MOSS Image", str(caught.exception))

    def test_only_civitai_and_hugging_face(self):
        with self.assertRaises(link_lora_service.LinkLoraError):
            link_lora_service._source_kind("https://example.com/lora.safetensors")


class FamilyTests(unittest.TestCase):
    def test_families(self):
        self.assertEqual(link_lora_service.family(Recipe(name="q", slug="sglang-qwen-image-21-turbo", engine="SGLang")), "qwen-image")
        self.assertIsNone(link_lora_service.family(Recipe(name="v", slug="vllmomni-minimax-h3", engine="vLLM-Omni")))
        self.assertEqual(link_lora_service._variant_hint("H3 Ref2VA character swap"), "Ref2VA")

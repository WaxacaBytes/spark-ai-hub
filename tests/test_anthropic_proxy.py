import asyncio
import json
import unittest

from daemon.routers.anthropic_proxy import (
    _anthropic_to_openai, _split_tool_result, _stream_translate,
)


class _Upstream:
    """Stands in for an aiohttp response streaming OpenAI chunks."""

    def __init__(self, deltas: list[dict]):
        self.content = self
        self._lines = [
            f"data: {json.dumps({'choices': [{'delta': d}]})}\n\n".encode() for d in deltas
        ]

    async def iter_any(self):
        for line in self._lines:
            yield line


class AnthropicProxyTests(unittest.TestCase):
    def test_tool_result_images_become_followup_user_content(self):
        body = {
            "model": "claude-test",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_read",
                            "name": "Read",
                            "input": {"file_path": "/tmp/example.png"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_read",
                            "content": [
                                {"type": "text", "text": "Image file"},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": "abc123",
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
        }

        out = _anthropic_to_openai(body, "live-model")

        self.assertEqual(out["messages"][1], {
            "role": "tool",
            "tool_call_id": "toolu_read",
            "content": "Image file",
        })
        self.assertEqual(out["messages"][2], {
            "role": "user",
            "content": [
                {"type": "text", "text": "[tool_result images]"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc123"},
                },
            ],
        })

    def test_split_tool_result_preserves_plain_strings(self):
        self.assertEqual(_split_tool_result("plain result"), ("plain result", []))

    def test_reasoning_streams_as_a_thinking_block_closed_before_text(self):
        upstream = _Upstream([
            {"reasoning_content": "The user "},
            {"reasoning": "says hi."},
            {"content": "Hi!"},
        ])

        async def collect():
            return [json.loads(ev.decode().split("data: ", 1)[1])
                    async for ev in _stream_translate(upstream, "m")]

        events = asyncio.run(collect())
        shape = [(e["type"], e.get("index"), (e.get("content_block") or e.get("delta") or {}).get("type"))
                 for e in events]
        self.assertEqual(shape, [
            ("message_start", None, None),
            ("content_block_start", 0, "thinking"),
            ("content_block_delta", 0, "thinking_delta"),
            ("content_block_delta", 0, "thinking_delta"),
            ("content_block_stop", 0, None),
            ("content_block_start", 1, "text"),
            ("content_block_delta", 1, "text_delta"),
            ("content_block_stop", 1, None),
            ("message_delta", None, None),
            ("message_stop", None, None),
        ])
        self.assertEqual(events[3]["delta"]["thinking"], "says hi.")


if __name__ == "__main__":
    unittest.main()

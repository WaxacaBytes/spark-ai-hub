import unittest

from daemon.routers.anthropic_proxy import _anthropic_to_openai, _split_tool_result


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


if __name__ == "__main__":
    unittest.main()

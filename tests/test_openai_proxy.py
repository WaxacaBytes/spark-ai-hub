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

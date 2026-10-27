import unittest

from daemon.services.video_service import with_reference_lists


class ReferenceListTests(unittest.TestCase):
    def test_references_go_in_ordered_lists_with_h3_frame_counts(self):
        out = with_reference_lists({"prompt": "p", "seconds": 10, "fps": 24},
                                   [("video", b"v", None), ("image", b"a", None), ("image", b"b", None),
                                    ("audio", b"s", None)])
        self.assertEqual(len(out["image_reference"]), 2)
        self.assertTrue(out["image_reference"][0]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(list(out["video_reference"][0]), ["video_url"])
        self.assertEqual(list(out["audio_reference"][0]), ["audio_url"])
        self.assertEqual(out["num_frames"], 243)

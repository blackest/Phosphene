"""4.19.2 (fleet 4.19.1, input_missing): what still reached its turn to fail.

- 12 Image-mode renders on one Mac whose picture was gone: the Batch door
  (/queue/batch) never ran the up-front checks /queue/add does;
- Keyframes with no start picture ("start_image not found:");
- a picked LoRA that had been moved or deleted ("LoRA file not found").
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402
from test_queue_input_refusal import _H, _QueueTest, routes_queue    # noqa: E402


class TheBatchDoorChecksToo(_QueueTest):
    def batch(self, **form) -> _H:
        h = _H(form)
        routes_queue.post_queue_batch(h, "/queue/batch", {}, "")
        return h

    def test_a_batch_of_image_prompts_whose_picture_is_gone_is_refused(self):
        h = self.batch(mode="i2v", image=str(self.pic) + ".gone",
                       prompts="a lighthouse\n---\na harbour\n---\na boat")
        self.assertEqual((h.status, h.payload.get("code")), (400, "input_missing"), h.payload)
        self.assertIn("no longer on disk", h.payload["error"])
        self.assertEqual(P.STATE["queue"], [])

    def test_a_good_batch_still_queues(self):
        h = self.batch(mode="i2v", image=str(self.pic), prompts="a\n---\nb")
        self.assertEqual(h.payload.get("added"), 2, h.payload)


class KeyframesAndLoras(_QueueTest):
    def refused(self, h, *words):
        self.assertEqual((h.status, h.payload.get("code")), (400, "input_missing"), h.payload)
        for w in words:
            self.assertIn(w, h.payload["error"])
        self.assertEqual(P.STATE["queue"], [])

    def test_keyframes_with_no_start_or_end(self):
        self.refused(self.add(mode="keyframe", prompt="x", end_image=str(self.pic)),
                     "start frame")
        self.refused(self.add(mode="keyframe", prompt="x", start_image=str(self.pic)),
                     "end frame")
        self.refused(self.add(mode="keyframe", prompt="x", start_image=str(self.pic),
                              end_image=str(self.pic) + ".gone"), "no longer on disk")

    def test_a_keyframe_list_with_a_missing_picture(self):
        kf = [{"image_path": str(self.pic), "frame_index": 0},
              {"image_path": str(self.pic) + ".gone", "frame_index": 60}]
        self.refused(self.add(mode="keyframe", prompt="x", keyframes_json=json.dumps(kf)),
                     "no longer on disk")

    def test_keyframes_with_both_pictures_pass_the_input_check(self):
        params = {"mode": "keyframe", "start_image": str(self.pic), "end_image": str(self.pic)}
        self.assertIsNone(P.job_input_refusal(params))
        kf = [{"image_path": str(self.pic), "frame_index": 0},
              {"image_path": str(self.pic), "frame_index": 60}]
        self.assertIsNone(P.job_input_refusal({"mode": "keyframe",
                                               "keyframes_json": json.dumps(kf)}))

    def test_h3_keyframes_may_start_free_but_need_an_end(self):
        self.assertIsNone(P.job_input_refusal(
            {"mode": "keyframe", "engine": "h3", "end_image": str(self.pic)}))
        self.assertIn("end frame", P.job_input_refusal(
            {"mode": "keyframe", "engine": "h3", "start_image": str(self.pic)}))
        kf = [{"image_path": "", "frame_index": 0},
              {"image_path": str(self.pic), "frame_index": 60}]
        self.assertIsNone(P.job_input_refusal(
            {"mode": "keyframe", "engine": "h3", "keyframes_json": json.dumps(kf)}))

    def test_a_picked_lora_that_is_gone(self):
        gone = str(Path(self.tmp.name) / "style_v2.safetensors")
        self.refused(self.add(mode="t2v", prompt="x",
                              loras=json.dumps([{"path": gone, "strength": 1.0}])),
                     "style_v2.safetensors", "no longer on disk")

    def test_a_lora_on_disk_or_a_hub_id_is_not_refused(self):
        here = Path(self.tmp.name) / "here.safetensors"
        here.write_bytes(b"\0")
        for path in (str(here), "someone/some-lora", "relative/name.safetensors"):
            with self.subTest(path=path):
                self.assertIsNone(P.job_input_refusal(
                    {"mode": "t2v", "loras": [{"path": path, "strength": 1.0}]}))


if __name__ == "__main__":
    unittest.main()

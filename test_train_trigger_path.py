"""A training trigger can never be a path (ov-review D-02).

The trigger IS the trained LoRA's file name (<loras>/<trigger>_v2.safetensors
plus its sidecars). /train/start accepted any text, so `/tmp/x` or `../x`
wrote the model and its JSON outside the library - over whatever was there -
and the character never showed up in the panel.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class TriggerIsAFileName(unittest.TestCase):
    def test_real_triggers_pass(self):
        for t in ("hdhtrn", "Evara", "jmvlZwmn", "my char", "chartest_a", "fbk-trn"):
            self.assertEqual(P.train_trigger_problem(t), "", t)

    def test_paths_are_refused(self):
        for t in ("/tmp/review_target", "../../x", "a/b", "a\\b", "..", ".hidden", "-x"):
            self.assertTrue(P.train_trigger_problem(t), t)

    def test_the_worker_refuses_a_queued_job_with_one(self):
        job = {"id": "t1", "params": {"mode": "train", "train_job_id": "trn_abc",
                                      "trigger": "/tmp/review_target",
                                      "train_type": "character"}}
        with self.assertRaises(RuntimeError) as cm:
            P.run_train_job_inner(job)
        self.assertIn("file name", str(cm.exception))

    def test_the_route_checks_it(self):
        src = (ROOT / "panel" / "routes_train.py").read_text(encoding="utf-8")
        start = src[src.index('@post("/train/start")'):]
        self.assertIn("P.train_trigger_problem(", start[:start.index("P.make_job(form)")])


if __name__ == "__main__":
    unittest.main()

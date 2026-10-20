"""An H3 render on a drifted H3 venv repairs it first instead of failing.

FLEET (4.19.5-4.19.6): 10 installs in 5 days had their first H3 render after
an Update refused with "the Hailuo H3 venv is not on the package set this
release was tested with - mlx 0.32.x (tested: 0.32.4)". Every one pressed
Repair and rendered ~10 minutes later. The worker now starts that same repair
by itself when an H3 job is next and the venv is off its set, holds the job
while it runs, and renders after. One automatic try per panel run: a repair
that did not take falls back to the refusal with the manual button.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

FAULT = ("the Hailuo H3 venv is not on the package set this release was "
         "tested with - mlx 0.32.2 (tested: 0.32.4)")
H3_JOB = {"id": "a", "params": {"engine": "h3", "mode": "t2v"}}
LTX_JOB = {"id": "b", "params": {"engine": "ltx", "mode": "t2v"}}


class AutoRepair(unittest.TestCase):
    def setUp(self):
        getattr(P, "_H3_AUTO_REPAIR", {}).clear()
        P.H3_REPAIR.clear()
        P.H3_REPAIR.update({"state": "idle", "active": False})
        self.started = []

        def fake_start():
            self.started.append(1)
            P.H3_REPAIR.update(state="running", active=True)
            return 202, {"ok": True, "started": True}
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        os.environ.pop("LTX_H3_ROOT", None)
        self.patches = [
            mock.patch.object(P, "h3_env_repair_start", fake_start),
            mock.patch.object(P, "_h3_deps_running", lambda: False),
            mock.patch.object(P, "push", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        P.H3_REPAIR.update(active=False)

    def test_a_drifted_venv_is_repaired_and_the_job_waits(self):
        with mock.patch.object(P, "h3_env_fault", lambda: FAULT):
            self.assertIn("Queue held", P._h3_repair_hold(H3_JOB))
            self.assertEqual(self.started, [1])
            # Still running: still held, not started twice.
            self.assertIn("Queue held", P._h3_repair_hold(H3_JOB))
            self.assertEqual(self.started, [1])
            # The repair did not take: the job is released (and is then
            # refused with the manual Repair) - no second automatic try.
            P.H3_REPAIR.update(state="failed", active=False)
            self.assertEqual(P._h3_repair_hold(H3_JOB), "")
            self.assertEqual(self.started, [1])

    def test_a_healthy_venv_starts_nothing(self):
        with mock.patch.object(P, "h3_env_fault", lambda: ""):
            self.assertEqual(P._h3_repair_hold(H3_JOB), "")
        self.assertEqual(self.started, [])

    def test_a_relocated_checkout_is_never_repaired_unasked(self):
        """LTX_H3_ROOT = a checkout someone manages (the owner's engine
        worktree runs mlx 0.32.0 on purpose): refuse with the button as
        before, never rewrite its venv."""
        with mock.patch.object(P, "h3_env_fault", lambda: FAULT), \
             mock.patch.dict(os.environ, {"LTX_H3_ROOT": "/elsewhere/minimax-h3-mlx"}):
            self.assertEqual(P._h3_repair_hold(H3_JOB), "")
        self.assertEqual(self.started, [])

    def test_an_ltx_job_never_triggers_it(self):
        with mock.patch.object(P, "h3_env_fault", lambda: FAULT):
            self.assertEqual(P._h3_repair_hold(LTX_JOB), "")
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()

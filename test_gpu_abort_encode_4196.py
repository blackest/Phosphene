"""4.19.6 (fleet 4.19.2+, 14 days): GPU aborts in PROMPT ENCODING.

46 GPU-class failures on 14 installs; 37 of the 40 LTX ones died in prompt
encoding, 0-30 s into the job. Two mechanisms, both fixed in WarmHelper.run:

A. A WARM helper. On the install that carried most of them, 47 of 49 starts
   in a helper that had just finished a render died in encoding, against 22
   of 74 fresh starts; the 4.17.5 re-run worked because it starts a NEW
   helper. Now a setting macOS already stopped in encoding starts fresh.

B. A non-watchdog GPU abort (Metal "Internal Error", innocent victim, other
   command-buffer errors; mostly M2 Max on macOS 15.x). Never retried: the
   retry only armed on a watchdog line, and the queue moved straight on to
   the next job, which died the same way (re-runs within ~30 s: 29 of 32
   failed; starts 30 s - 5 min later: 16 of 19 passed). Now: once, in a
   fresh helper, after METAL_ABORT_COOLDOWN_SEC, nothing recorded against
   the setting, and the error stays the native one if it comes back.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P  # noqa: E402

ABORT = ("helper exited from SIGABRT (libc++abi: terminating due to uncaught "
         "exception of type std::runtime_error: [METAL] Command buffer execution "
         "failed: Internal Error (0000000e:Internal Error)); returncode=-6")
WATCHDOG = ("helper exited from SIGABRT (the macOS GPU watchdog killed a Metal "
            "command buffer (kIOGPUCommandBufferCallbackErrorTimeout)); returncode=-6")


def _job(**params) -> dict:
    p = {"mode": "i2v", "engine": "ltx", "quality": "standard", "width": 768,
         "height": 448, "frames": 121}
    p.update(params)
    return {"id": "j1", "status": "running", "params": p}


class _Fake(P.WarmHelper):
    """Scripted runs: "ok", "boom", "watchdog_encode", "abort_encode",
    "abort_denoise". Tracks kills and whether each run started warm."""

    def __init__(self, outcomes, warm=0, alive=True):
        super().__init__()
        self.outcomes = list(outcomes)
        self.runs = []                 # _jobs_in_proc at the start of each run
        self.kills = 0
        self._alive = alive
        self._jobs_in_proc = warm

    def is_alive(self):
        return self._alive

    def kill(self):
        self.kills += 1
        self._alive = False
        self._jobs_in_proc = 0

    def _run_once(self, job_spec, timeout=None):
        self._metal_timeout_seen = False
        self._past_prompt_encode = False
        self._last_phase = ""
        self._crash_line = ""
        if not self._alive:            # _ensure spawns a fresh helper
            self._alive = True
            self._jobs_in_proc = 0
        self.runs.append(self._jobs_in_proc)
        what = self.outcomes.pop(0) if self.outcomes else "boom"
        if what == "ok":
            self._jobs_in_proc += 1
            return {"event": "done"}
        if what.startswith("watchdog_"):
            self._metal_timeout_seen = True
            self._last_phase = what.split("_", 1)[1]
            self._past_prompt_encode = self._last_phase != "encode"
            self._crash_line = "gpu_timeout"
            self._alive = False
            raise RuntimeError(WATCHDOG)
        if what.startswith("abort_"):
            self._last_phase = what.split("_", 1)[1]
            self._crash_line = "gpu_internal"
            self._alive = False
            raise RuntimeError(ABORT)
        self._jobs_in_proc += 1
        raise RuntimeError("something else broke")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="abort-"))
        self._p = [mock.patch.object(P, "METAL_WATCHDOG_FILE", self.tmp / "wd.json"),
                   mock.patch.object(P, "METAL_ABORT_COOLDOWN_SEC", 45)]
        for p in self._p:
            p.start()
        self.sleeps = []
        self._clock = [0.0]

        def fake_sleep(sec):
            self.sleeps.append(sec)
            self._clock[0] += sec

        self._t = [mock.patch.object(P.time, "sleep", fake_sleep),
                   mock.patch.object(P.time, "monotonic", lambda: self._clock[0])]
        for p in self._t:
            p.start()

    def tearDown(self):
        for p in self._p + self._t:
            p.stop()
        P._JOB_CTX.job = None

    def run_job(self, helper, job):
        P._JOB_CTX.job = job
        try:
            return helper.run({"action": "generate", "id": job["id"], "params": {}})
        finally:
            P._JOB_CTX.job = None


class AGpuAbortInEncodingIsRerunOnceAfterAPause(_Base):
    def test_an_internal_error_in_encoding_reruns_fresh_after_the_pause(self):
        h = _Fake(["abort_encode", "ok"], warm=1)
        job = _job()
        self.assertEqual(self.run_job(h, job), {"event": "done"})
        self.assertEqual(h.runs, [1, 0], "the re-run starts in a fresh helper")
        self.assertGreaterEqual(sum(self.sleeps), 45)
        self.assertTrue(job["gpu_abort_retry"])
        # Nothing recorded against the setting: no short steps, no refusal.
        key = P.metal_watchdog_key(job["params"])
        self.assertFalse(P.metal_watchdog_wants_short(key))
        self.assertIsNone(P.metal_watchdog_refusal(key))

    def test_only_once_and_the_error_stays_native(self):
        h = _Fake(["abort_encode", "abort_encode", "ok"])
        with self.assertRaises(RuntimeError) as cm:
            self.run_job(h, _job())
        self.assertEqual(len(h.runs), 2)
        self.assertEqual(P._analytics_error_class(str(cm.exception)), "native_crash")

    def test_not_when_no_attempt_is_left(self):
        # Codex 4.19.6: watchdog (denoise) -> watchdog (encode) -> abort.
        h = _Fake(["watchdog_denoise", "watchdog_encode", "abort_encode", "ok"])
        job = _job()
        with self.assertRaises(RuntimeError) as cm:
            self.run_job(h, job)
        self.assertEqual(len(h.runs), 3)
        self.assertNotIn("unreachable", str(cm.exception))
        self.assertNotIn("gpu_abort_retry", job)
        self.assertEqual(self.sleeps, [])

    def test_not_outside_encoding(self):
        h = _Fake(["abort_denoise", "ok"])
        with self.assertRaises(RuntimeError):
            self.run_job(h, _job())
        self.assertEqual(len(h.runs), 1)
        self.assertEqual(self.sleeps, [])

    def test_an_ordinary_failure_is_not_retried(self):
        h = _Fake(["boom", "ok"])
        with self.assertRaises(RuntimeError):
            self.run_job(h, _job())
        self.assertEqual(len(h.runs), 1)

    def test_a_stop_during_the_pause_ends_the_job(self):
        h = _Fake(["abort_encode", "ok"])
        job = _job()

        def stop_after_3(sec):
            self.sleeps.append(sec)
            self._clock[0] += sec
            if len(self.sleeps) == 3:
                job["cancel_requested"] = True

        with mock.patch.object(P.time, "sleep", stop_after_3):
            with self.assertRaises(RuntimeError):
                self.run_job(h, job)
        self.assertEqual(len(h.runs), 1, "no re-run after Stop")
        self.assertLess(sum(self.sleeps), 10)


class ASettingStoppedInEncodingStartsInAFreshHelper(_Base):
    def _remember_encode_kill(self, job):
        key = P.metal_watchdog_key(job["params"])
        P.metal_watchdog_record_kill(key, short=False, phase="encode")
        self.assertTrue(P.metal_watchdog_needs_short_encode(key))

    def test_a_warm_helper_is_retired_first(self):
        job = _job()
        self._remember_encode_kill(job)
        h = _Fake(["ok"], warm=2)
        self.run_job(h, job)
        self.assertGreaterEqual(h.kills, 1)
        self.assertEqual(h.runs, [0], "the render ran in a fresh helper")

    def test_a_fresh_helper_is_kept(self):
        job = _job()
        self._remember_encode_kill(job)
        h = _Fake(["ok"], warm=0)
        self.run_job(h, job)
        self.assertEqual(h.kills, 0)

    def test_other_settings_keep_the_warm_helper(self):
        h = _Fake(["ok"], warm=2)
        self.run_job(h, _job())
        self.assertEqual(h.kills, 0)
        self.assertEqual(h.runs, [2])


class TheFleetCanConfirmIt(_Base):
    def test_the_job_records_warmth_and_the_retry(self):
        h = _Fake(["abort_encode", "ok"], warm=1)
        job = _job()
        self.run_job(h, job)
        self.assertIs(job["helper_warm"], False, "the run that finished was fresh")
        self.assertTrue(job["gpu_abort_retry"])
        warm = _Fake(["ok"], warm=3)
        job2 = _job()
        self.run_job(warm, job2)
        self.assertIs(job2["helper_warm"], True)

    def test_the_event_carries_both_and_the_doc_lists_them(self):
        sent = []
        params = {"mode": "t2v", "engine": "ltx", "quality": "quick", "width": 640,
                  "height": 448, "frames": 97}
        with mock.patch.object(P, "_analytics_capture", lambda e, p: sent.append((e, p))), \
                mock.patch.object(P, "get_settings",
                                  lambda: {"analytics_first_render_reported": True}):
            P._analytics_render_event({"status": "done", "elapsed_sec": 9, "params": params,
                                       "gpu_abort_retry": True, "helper_warm": False})
            P._analytics_render_event({"status": "done", "elapsed_sec": 9, "params": params,
                                       "helper_warm": "yes"})
        self.assertTrue(sent[0][1]["gpu_abort_retry"])
        self.assertIs(sent[0][1]["helper_warm"], False)
        self.assertNotIn("helper_warm", sent[1][1], "booleans only")
        self.assertNotIn("gpu_abort_retry", sent[1][1])
        doc = (ROOT / "docs" / "ANALYTICS.md").read_text(encoding="utf-8")
        self.assertIn("`gpu_abort_retry`", doc)
        self.assertIn("`helper_warm`", doc)


class TheCounterFollowsTheProcess(unittest.TestCase):
    def test_a_new_helper_starts_at_zero(self):
        self.assertEqual(P.WarmHelper()._jobs_in_proc, 0)
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        spawn = src.index('push(f"Spawning warm helper')
        self.assertIn("self._jobs_in_proc = 0", src[spawn:spawn + 300])


if __name__ == "__main__":
    unittest.main()

"""4.19.2: the GPU-watchdog count holds only real watchdog kills, and a helper
that dies from a signal says (in closed words) what its last native line was
and how far it got.

Fleet 4.19.1: 37 render_failed in `metal_watchdog`, but 24 of them (3 M2 Max
Macs on macOS 14/15, dead in under 15 s, before any step line) never had a
watchdog line at all: the panel's generic SIGABRT sentence QUOTED "Caused GPU
Timeout Error" as an example, and the classifier matched the quote. None of
those 24 got the short-steps retry, because the helper never saw the line.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402

WD_LINE = ("libc++abi: terminating due to uncaught exception of type "
           "std::runtime_error: [METAL] Command buffer execution failed: Caused GPU "
           "Timeout Error (00000002:kIOGPUCommandBufferCallbackErrorTimeout)")


class _DeadProc:
    """A helper process that is already gone: SIGABRT, stdout at EOF."""

    def __init__(self, payload: bytes = b"", rc: int = -6):
        r, w = os.pipe()
        os.write(w, payload)
        os.close(w)
        self.stdout = os.fdopen(r, "rb")
        self.stdin = None
        self.pid = 4242
        self._rc = rc

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        return self._rc


def _helper(payload: bytes, rc: int = -6) -> P.WarmHelper:
    h = P.WarmHelper()
    h.proc = _DeadProc(payload, rc)
    h._read_carry = b""
    h.is_alive = lambda: False
    return h


def _fail(h: P.WarmHelper, job: dict) -> str:
    P._JOB_CTX.job = job
    try:
        ev = h._read_until(["done", "error", "exit", "stopped"], timeout=5)
        try:
            h._dispatch_run_event(ev)
        except RuntimeError as exc:
            return str(exc)
        raise AssertionError("expected the dead helper to raise")
    finally:
        P._JOB_CTX.job = None


class TheLineClass(unittest.TestCase):
    def test_classes(self):
        c = P.crash_line_class
        self.assertEqual(c(WD_LINE), "gpu_timeout")
        self.assertEqual(c("Impacting Interactivity (0000000e:"
                           "kIOGPUCommandBufferCallbackErrorImpactingInteractivity)"),
                         "gpu_interactivity")
        self.assertEqual(c("[METAL] Command buffer execution failed: Caused GPU Hang "
                           "Error (00000003:kIOGPUCommandBufferCallbackErrorHang)"), "gpu_hang")
        self.assertEqual(c("Discarded (victim of GPU error/recovery) (00000005:"
                           "kIOGPUCommandBufferCallbackErrorInnocentVictim)"), "gpu_victim")
        self.assertEqual(c("[METAL] Command buffer execution failed: Internal Error"),
                         "gpu_other")
        self.assertEqual(c("[metal::malloc] Resource limit (499000) exceeded."),
                         "resource_limit")
        self.assertEqual(c("libc++abi: terminating due to uncaught exception of type "
                           "std::invalid_argument: [broadcast_shapes] bad"), "cxx_exception")
        self.assertEqual(c("Denoising step 3/8"), "")
        self.assertEqual(set(P.CRASH_LINES) >= {"none", "gpu_timeout"}, True)


class AnUnexplainedAbortIsNotTheWatchdog(unittest.TestCase):
    def test_no_watchdog_line_is_a_native_crash_and_says_so(self):
        job = {"id": "j", "params": {}}
        msg = _fail(_helper(b'{"event": "log", "line": "[mlx] allocator cache capped"}\n'), job)
        self.assertIn("SIGABRT", msg)
        self.assertNotIn("GPU Timeout", msg)
        self.assertEqual(P._analytics_error_class(msg), "native_crash")
        self.assertEqual((job["crash_line"], job["crash_phase"]), ("none", "start"))

    def test_the_watchdog_line_still_classifies_as_the_watchdog(self):
        job = {"id": "j", "params": {}}
        payload = (b'{"event": "log", "line": "step:get_pipe kind=t2v"}\n'
                   + WD_LINE.encode() + b"\n")
        msg = _fail(_helper(payload), job)
        self.assertEqual(P._analytics_error_class(msg), "metal_watchdog")
        self.assertEqual((job["crash_line"], job["crash_phase"]), ("gpu_timeout", "load"))

    def test_a_last_line_without_a_newline_is_still_read(self):
        # The abort's last words used to be dropped at EOF when they had no
        # trailing newline: the watchdog went unseen and unretried.
        job = {"id": "j", "params": {}}
        h = _helper(b'{"event": "log", "line": "Encoding prompt"}\n' + WD_LINE.encode())
        msg = _fail(h, job)
        self.assertTrue(h._metal_timeout_seen)
        self.assertEqual(P._analytics_error_class(msg), "metal_watchdog")
        self.assertEqual((job["crash_line"], job["crash_phase"]), ("gpu_timeout", "encode"))


class TheEventCarriesIt(unittest.TestCase):
    def test_failed_only_and_closed_words_only(self):
        sent = []
        params = {"mode": "t2v", "engine": "ltx", "quality": "quick", "width": 640,
                  "height": 448, "frames": 97}
        with mock.patch.object(P, "_analytics_capture", lambda e, p: sent.append((e, p))), \
                mock.patch.object(P, "get_settings",
                                  lambda: {"analytics_first_render_reported": True}):
            P._analytics_render_event({"status": "failed", "elapsed_sec": 9, "params": params,
                                       "error": "helper exited from SIGABRT (x)",
                                       "crash_line": "none", "crash_phase": "start"})
            P._analytics_render_event({"status": "failed", "elapsed_sec": 9, "params": params,
                                       "error": "helper exited from SIGABRT (x)",
                                       "crash_line": "some free text", "crash_phase": "nope"})
        self.assertEqual((sent[0][1]["crash_line"], sent[0][1]["crash_phase"]), ("none", "start"))
        self.assertNotIn("crash_line", sent[1][1])
        self.assertNotIn("crash_phase", sent[1][1])

    def test_documented(self):
        doc = (ROOT / "docs" / "ANALYTICS.md").read_text(encoding="utf-8")
        for word in ("`crash_line`", "`crash_phase`") + tuple(f"`{c}`" for c in P.CRASH_LINES):
            self.assertIn(word, doc)


if __name__ == "__main__":
    unittest.main()

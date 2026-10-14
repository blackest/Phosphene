"""4.19.2: vocal separation installs survive a slow or flaky connection.

Fleet, 7 days to 2026-10-14: 16 separator_install failures on 11 Macs among
~250 successes - 12 of them the panel's ONE automatic attempt at boot, and
most of those the network or a timeout (pip_failed network/other,
weights_failed timeout/network)."""
from __future__ import annotations

import hashlib
import http.server
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import a2v_separate as SEP                                           # noqa: E402
import mlx_ltx_panel as P                                            # noqa: E402
from test_separator_retry import _App                                 # noqa: E402


class _Trickle:
    """Every GET sends only `step` bytes and drops the connection - a link
    that keeps failing but moves forward each time."""

    def __init__(self, payload: bytes, step: int):
        self.payload, self.step, self.hits = payload, step, 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                rng = self.headers.get("Range")
                start = int(rng.split("=")[1].split("-")[0]) if rng else 0
                self.send_response(206 if rng else 200)
                self.send_header("Content-Length", str(len(outer.payload) - start))
                self.end_headers()
                self.wfile.write(outer.payload[start:start + outer.step])
                self.wfile.flush()
                self.connection.shutdown(2)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class TheWeightsKeepGoingWhileTheyMove(unittest.TestCase):
    def test_a_download_that_moves_every_time_is_not_given_up_after_four(self):
        payload = os.urandom(70_000)
        name = f"955717e8-{hashlib.sha256(payload).hexdigest()[:8]}.th"
        home = Path(tempfile.mkdtemp(prefix="torchhome-"))
        srv = _Trickle(payload, step=10_000)                         # 7 attempts
        try:
            with mock.patch.dict(os.environ, {"TORCH_HOME": str(home)}), \
                    mock.patch.object(SEP, "model_files",
                                      lambda n: [(name, f"{srv.url}/{name}")]):
                SEP.prefetch("htdemucs", sleep=lambda s: None)
        finally:
            srv.close()
        self.assertEqual((home / "hub" / "checkpoints" / name).read_bytes(), payload)
        self.assertGreater(srv.hits, SEP.DOWNLOAD_ATTEMPTS)

    def test_it_is_still_bounded(self):
        payload = os.urandom(200_000)
        name = f"955717e8-{hashlib.sha256(payload).hexdigest()[:8]}.th"
        home = Path(tempfile.mkdtemp(prefix="torchhome-"))
        srv = _Trickle(payload, step=1_000)                          # would need 200
        try:
            with mock.patch.dict(os.environ, {"TORCH_HOME": str(home)}), \
                    mock.patch.object(SEP, "model_files",
                                      lambda n: [(name, f"{srv.url}/{name}")]):
                with self.assertRaises(SEP.DownloadExhausted):
                    SEP.prefetch("htdemucs", sleep=lambda s: None)
        finally:
            srv.close()
        self.assertEqual(srv.hits, SEP.DOWNLOAD_MAX_ATTEMPTS)


class ThePackageStepWaitsLonger(unittest.TestCase):
    def test_uv_gets_a_longer_timeout_and_more_retries_never_less_than_the_users(self):
        for preset, want in (("60", "180"), ("300", "300")):
            app = _App()
            uv = app.bin / "uv"
            uv.write_text(uv.read_text().replace(
                "touch", f"echo \"T=$UV_HTTP_TIMEOUT R=$UV_HTTP_RETRIES\" > '{app.tmp}/uvenv'; touch", 1))
            with mock.patch.dict(os.environ, {"UV_HTTP_TIMEOUT": preset}):
                r = app.run()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((app.tmp / "uvenv").read_text().split(),
                             [f"T={want}", "R=5"])


class AJobMayTryAgainAfterATransientFailure(unittest.TestCase):
    def setUp(self):
        self._auto = dict(P.A2V_SEPARATOR_AUTO)
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            self._inst = dict(P.A2V_SEPARATOR_INSTALL)
        self.addCleanup(self._restore)
        self.started = []
        p = mock.patch.object(P, "a2v_separator_install_start",
                              lambda trigger: (self.started.append(trigger), (202, {}))[1])
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(P, "a2v_separator_status",
                              return_value={"ready": False, "weights": False})
        q.start()
        self.addCleanup(q.stop)

    def _restore(self):
        P.A2V_SEPARATOR_AUTO.clear()
        P.A2V_SEPARATOR_AUTO.update(self._auto)
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            P.A2V_SEPARATOR_INSTALL.clear()
            P.A2V_SEPARATOR_INSTALL.update(self._inst)

    def _failed(self, cause, ago, attempts=1):
        P.A2V_SEPARATOR_AUTO.update(enabled=True, attempted=True, attempts=attempts)
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            P.A2V_SEPARATOR_INSTALL.clear()
            P.A2V_SEPARATOR_INSTALL.update(state="failed", active=False, cause=cause,
                                           finished_ts=time.time() - ago, error="x")

    def test_a_network_failure_ten_minutes_old_is_tried_again_by_a_job(self):
        self._failed("network", 11 * 60)
        self.assertTrue(P.a2v_separator_auto_start("job"))
        self.assertEqual(self.started, ["job"])
        self.assertEqual(P.A2V_SEPARATOR_AUTO["attempts"], 2)

    def test_not_too_soon_not_for_a_lasting_cause_not_forever_not_at_boot(self):
        self._failed("timeout", 60)
        self.assertFalse(P.a2v_separator_auto_start("job"))
        self._failed("uv_error", 3600)
        self.assertFalse(P.a2v_separator_auto_start("job"))
        self._failed("disk", 3600)
        self.assertFalse(P.a2v_separator_auto_start("job"))
        self._failed("network", 3600, attempts=P.A2V_SEPARATOR_AUTO_MAX)
        self.assertFalse(P.a2v_separator_auto_start("job"))
        self._failed("network", 3600)
        self.assertFalse(P.a2v_separator_auto_start("boot"))
        self.assertEqual(self.started, [])

    def test_the_thread_keeps_the_cause_it_failed_with(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertIn("error=error, cause=_cause)", src)


if __name__ == "__main__":
    unittest.main()

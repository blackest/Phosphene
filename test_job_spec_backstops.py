"""Job specs the worker cannot render are caught BEFORE the render (ov-review).

Two fleet signatures (4.19.5) and the paths behind them:

  * `unknown upscale mode: fit_360p` on an i2v job. make_job took the export
    size verbatim and compute_upscale_plan() rejected it only AFTER the
    helper had rendered: the whole generation burned, then the job failed.
    A Retry/Restore of such a job did the same again. Now make_job and the
    worker both fall back to the quality's own default first (B-03).
  * a bare `'stop_comfy'`. New Take replays a sidecar's params verbatim, and
    a Sharp export of an imported mp4 (no settings of its own) records only
    {mode: t2v, sharp_export_*}: New Take queued that as a t2v job and the
    worker died on its first missing key. New Take now refuses such a clip,
    and the worker reads the flag with .get (B-02).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

routes_queue = sys.modules["panel.routes_queue"]


def _form(**extra) -> dict:
    form = {"mode": "t2v", "prompt": "a lighthouse keeper waves", "width": "1024",
            "height": "576", "frames": "121", "seed": "-1", "quality": "standard",
            "accel": "off", "enhance": "off", "upscale": "off", "engine": "ltx"}
    form.update(extra)
    return form


class _Helper:
    ready_info: dict = {}

    def __init__(self):
        self.sent: list[dict] = []

    def is_alive(self):
        return True

    def kill(self, *a, **k):
        pass

    def run(self, spec, *a, **k):
        self.sent.append(spec)
        out = (spec.get("params") or {}).get("output_path")
        if out:
            Path(out).write_bytes(b"x")
        return {"seed_used": 1, "elapsed_sec": 0.1}


def _run(job: dict) -> _Helper:
    helper = _Helper()
    with mock.patch.object(P, "HELPER", helper), \
         mock.patch.object(P, "OUTPUT", Path(tempfile.mkdtemp(prefix="spec-run-"))), \
         mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
         mock.patch.object(P, "write_sidecar", lambda *a, **k: True), \
         mock.patch.object(P, "_gemma4_tower_supported", lambda *a, **k: True), \
         mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
         mock.patch.object(P, "kill_comfy", lambda *a, **k: None):
        P.run_job_inner(job)
    return helper


class UnknownExportSize(unittest.TestCase):
    def test_make_job_falls_back_to_the_quality_default(self):
        for quality, want in (("balanced", "fit_720p"), ("standard", "off")):
            with self.subTest(quality=quality):
                p = P.make_job(_form(quality=quality, upscale="fit_360p"))["params"]
                self.assertEqual(p["upscale"], want)

    def test_known_sizes_pass_through(self):
        for v in ("off", "fit_720p", "fit_1080p", "x2"):
            self.assertEqual(P.normalize_ltx_upscale(v, "standard"), (v, ""))

    def test_a_replayed_job_is_normalised_before_the_render(self):
        """A Retry/Restore carries the stored value past make_job: the worker
        itself must fix it, or the render burns and then fails."""
        job = P.make_job(_form())
        job["params"]["upscale"] = "fit_360p"
        helper = _run(job)
        self.assertEqual(len(helper.sent), 1)
        self.assertEqual(job["params"]["upscale"], "off")
        self.assertTrue(job.get("output_path"))


class StopComfyNotRequired(unittest.TestCase):
    def test_a_job_without_the_flag_renders(self):
        job = P.make_job(_form())
        job["params"].pop("stop_comfy", None)
        helper = _run(job)
        self.assertEqual(len(helper.sent), 1)


class _Handler:
    def __init__(self, form):
        self.form = form
        self.status = None
        self.payload = None

    def _read_form_body(self):
        return b"", {k: [v] for k, v in self.form.items()}

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


class NewTakeOfARecipelessExport(unittest.TestCase):
    def test_refused_with_a_reason_and_nothing_queued(self):
        out = Path(tempfile.mkdtemp(prefix="newtake-"))
        clip = out / "imported_sharp_20261019.mp4"
        clip.write_bytes(b"x")
        # Exactly what run_sharp_export_job_inner writes for a source with no
        # sidecar of its own.
        clip.with_suffix(".mp4.json").write_text(json.dumps({
            "output": str(clip),
            "params": {"mode": "t2v", "sharp_export_source": "/x/imported.mp4",
                       "sharp_export_mode": "x2"}}), encoding="utf-8")
        before = list(P.STATE["queue"])
        h = _Handler({"path": str(clip)})
        with mock.patch.object(P, "OUTPUT", out):
            routes_queue.post_queue_newtake(h, "/queue/newtake", {}, "")
        self.assertEqual(h.status, 400, h.payload)
        self.assertIn("nothing to re-roll", h.payload["error"])
        self.assertEqual(P.STATE["queue"], before)


if __name__ == "__main__":
    unittest.main()

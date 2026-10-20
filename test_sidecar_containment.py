"""/sidecar never reads a file outside the outputs/uploads roots (ov-review A-02).

The route resolved and confined the MEDIA path, then read `<media>.json` as
found. A symlink at that name served whatever it pointed at - the settings
file with its saved keys, say - and the media file did not even have to
exist.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

routes_files = sys.modules["panel.routes_files"]


class _H:
    def __init__(self):
        self.status = None
        self.payload = None
        self.body = None

    def send_error(self, code, *a):
        self.status = code

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status

    def _ok(self, body, ctype="text/html"):
        self.body, self.status = body, 200


class SidecarContainment(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sidecar-"))
        self.out = self.tmp / "outputs"
        self.up = self.tmp / "uploads"
        self.out.mkdir()
        self.up.mkdir()
        self.secret = self.tmp / "panel_settings.json"
        self.secret.write_text('{"civitai_api_key": "SECRET"}')

    def _get(self, media: Path) -> _H:
        h = _H()
        with mock.patch.object(P, "OUTPUT", self.out), mock.patch.object(P, "UPLOADS", self.up):
            routes_files.get_sidecar(h, urlparse(f"/sidecar?path={quote(str(media))}"))
        return h

    def test_a_symlinked_sidecar_pointing_out_is_refused(self):
        (self.out / "probe.mp4.json").symlink_to(self.secret)
        h = self._get(self.out / "probe.mp4")
        self.assertEqual(h.status, 404)
        self.assertIsNone(h.payload)
        self.assertIsNone(h.body)

    def test_a_real_sidecar_is_served(self):
        (self.out / "clip.mp4.json").write_text('{"params": {"prompt": "a hen"}}')
        h = self._get(self.out / "clip.mp4")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.payload["params"]["prompt"], "a hen")


if __name__ == "__main__":
    unittest.main()

"""A storyboard id is one folder name under STATE_DIR/storyboards, never a path.

ov-review A-01. Every board route takes `id` from the request and builds the
folder with storyboard.board_dir(). An absolute id replaced the root and `..`
climbed out, and some of those routes are GETs - which any website can fire at
the loopback panel with an <img src>, because a GET carries no Origin header.
/storyboard/edit/clip-peaks?id=/any/dir created and wrote its peaks cache in
that directory; /storyboard/edit/proxy?id=/any/dir served files out of it.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel  # noqa: E402
import storyboard  # noqa: E402


class _Handler:
    def __init__(self):
        self.status = None
        self.payload = None
        self.served = None

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status

    def send_error(self, code, *a):
        self.status = code

    def _serve_video_with_range(self, path, *a, **k):
        self.served = Path(path)
        self.status = 200

    def get(self, url):
        panel.Handler._storyboard_edit_get(self, urlparse(url))
        return self


class BoardIdIsOneFolderName(unittest.TestCase):
    def test_real_ids_resolve_under_the_root(self):
        state = Path("/x/state")
        for bid in ("sb_20261019_a1b2c3", "sb_amorfati", "sb_mvtest_film"):
            self.assertEqual(storyboard.board_dir(state, bid),
                             state / "storyboards" / bid)

    def test_paths_are_refused(self):
        for bid in ("/tmp/elsewhere", "../../etc", "sb_x/../../y", "a/b",
                    "..", ".hidden", "sb\x00x"):
            with self.subTest(bid=bid):
                with self.assertRaises(storyboard.StoryboardError):
                    storyboard.board_dir(Path("/x/state"), bid)

    def test_cross_site_gets_cannot_write_or_read_outside(self):
        target = Path(tempfile.mkdtemp(prefix="sb-escape-"))
        (target / "proxy").mkdir()
        (target / "proxy" / "clip.mp4").write_bytes(b"secret")
        song = target / "song.wav"
        song.write_bytes(b"RIFF")
        peaks = _Handler().get(
            f"/storyboard/edit/clip-peaks?id={quote(str(target))}&path={quote(str(song))}")
        self.assertEqual(peaks.status, 404)
        self.assertFalse((target / "peaks").exists())
        proxy = _Handler().get(
            f"/storyboard/edit/proxy?id={quote(str(target))}&name=clip.mp4")
        self.assertEqual(proxy.status, 404)
        self.assertIsNone(proxy.served)


if __name__ == "__main__":
    unittest.main()

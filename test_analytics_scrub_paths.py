"""error_signature never carries a username, a URL's query or a Windows path.

ov-review A-03. docs/ANALYTICS.md promises no paths and no usernames in the
one free-text field the panel sends. Three shapes got through the redactor:
a macOS username with a space (the surname survived), a URL (its path was
eaten, its `?token=` query kept), and a Windows path (untouched). The same
redactor feeds the bug-report log tail.
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


class ScrubPaths(unittest.TestCase):
    def test_a_username_with_a_space_is_gone(self):
        out = P._analytics_scrub_text(
            "PermissionError: [Errno 13] Permission denied: "
            "'/Users/Jane Doe/Models/private.safetensors'")
        self.assertNotIn("Doe", out)
        self.assertNotIn("private", out)
        self.assertEqual(out, "PermissionError: [Errno 13] Permission denied: '<path>'")
        tail = P._bug_report_scrub_line("open /Users/Jane Doe/Desktop/clip.mp4 failed")
        self.assertNotIn("Doe", tail)

    def test_a_url_keeps_its_host_and_loses_the_rest(self):
        out = P._analytics_scrub_text(
            "HTTPError 401: https://user:pw@civitai.com/api/download/models/1"
            "?token=PRIVATE_TOKEN failed")
        self.assertEqual(out, "HTTPError 401: https://civitai.com/<url> failed")

    def test_a_windows_path_is_redacted(self):
        out = P._analytics_scrub_text(r"OSError: cannot open C:\Users\Alice\Private\portrait.png")
        self.assertNotIn("Alice", out)
        self.assertEqual(out, "OSError: cannot open <path>")

    def test_panel_prose_is_untouched(self):
        for s in ("this engine needs ~32 GB free RAM but only 13.1 GB is available",
                  "helper exited from SIGKILL (out of memory (jetsam) or external kill)",
                  "[Errno 2] No such file or directory: 'ltx-2-mlx/env/bin/hf'"):
            self.assertEqual(P._analytics_scrub_text(s).split("<")[0], s.split("/env")[0]
                             if "ltx-2-mlx/env" in s else s)


if __name__ == "__main__":
    unittest.main()

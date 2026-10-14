"""4.19.4: the failures the fleet could not explain carry their cause.

Fleet since 4.16: two Macs, 21 FLUX.2 image failures recorded as
"mflux-generate-flux2 failed (exit 1) on batch of 1 seeds. Tail of
stdout/stderr:" - the fleet keeps only that first line - and every one filed as
venv_broken, because the traceback frames under it name ".../ltx-2-mlx/env/lib/".
And since 4.19.2 a helper that dies from a signal reports the class of its last
native line; an M2 Max's three prompt-encoding crashes on 4.19.3 all came back
`gpu_other`, the one bucket that names nothing.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402
import image_engine as IE                                            # noqa: E402


class FailuresSayWhy(unittest.TestCase):
    TAIL = [
        "Traceback (most recent call last):",
        '  File "/Users/x/pinokio/api/phosphene.git/ltx-2-mlx/env/lib/python3.11/'
        'site-packages/mflux/models/flux2/cli.py", line 88, in main',
        "    image = model.generate_image(seed=seed)",
        "ValueError: height must be divisible by 16",
    ]

    def test_image_failure_first_line_names_the_cause(self):
        self.assertEqual(IE._subprocess_cause_line(self.TAIL),
                         "ValueError: height must be divisible by 16")

    def test_traceback_frames_do_not_make_a_broken_venv(self):
        err = ("mflux-generate-flux2 failed (exit 1) on batch of 1 seeds"
               " — last line: ValueError: height must be divisible by 16. "
               "Tail of stdout/stderr:\n" + "\n".join(self.TAIL))
        self.assertNotEqual(P._analytics_error_class(err), "venv_broken")
        self.assertEqual(P._analytics_error_class(err), "helper_exit")
        # ...while a real broken venv still is one.
        self.assertEqual(P._analytics_error_class(
            "mflux-generate-flux2 failed (exit 1) — last line: "
            "ModuleNotFoundError: No module named 'mflux'"), "venv_broken")

    def test_signature_keeps_the_cause(self):
        err = ("mflux-generate-flux2 failed (exit 1) on batch of 1 seeds"
               " — last line: ValueError: height must be divisible by 16. "
               "Tail of stdout/stderr:\n...")
        self.assertIn("ValueError", P._analytics_scrub_text(err))

    def test_metal_command_buffer_errors_are_named(self):
        cases = {
            "[METAL] Command buffer execution failed: Internal Error "
            "(0000000e:Internal Error)": "gpu_internal",
            "Command buffer execution failed: (00000009:kIOGPUCommandBufferCallback"
            "ErrorSubmissionsIgnored)": "gpu_submissions_ignored",
            "(kIOGPUCommandBufferCallbackErrorInvalidResource)": "gpu_invalid_resource",
            "kIOGPUCommandBufferCallbackErrorBackgroundExecutionNotPermitted":
                "gpu_not_permitted",
            "Caused GPU Timeout Error (00000002:kIOGPUCommandBufferCallbackErrorTimeout)":
                "gpu_timeout",
            "[METAL] something else entirely": "gpu_other",
        }
        for line, cls in cases.items():
            with self.subTest(line=line):
                self.assertEqual(P.crash_line_class(line), cls)
                self.assertIn(cls, P.CRASH_LINES)
        # Codex 4.19.4: a permission error that is not about the GPU stays
        # a C++ exception.
        self.assertEqual(P.crash_line_class(
            "libc++abi: terminating due to uncaught exception of type "
            "std::runtime_error: open: Operation not permitted"), "cxx_exception")
        self.assertEqual(P.crash_line_class(
            "libc++abi: terminating due to uncaught exception of type "
            "std::runtime_error: internal error in the decoder"), "cxx_exception")
        doc = (ROOT / "docs" / "ANALYTICS.md").read_text()
        for cls in P.CRASH_LINES:
            self.assertIn(f"`{cls}`", doc)


if __name__ == "__main__":
    unittest.main()

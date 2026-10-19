"""4.19.6: Stop survives a process group that answers EPERM.

On macOS, os.killpg on a group whose members have all exited but are not yet
reaped raises PermissionError, not ProcessLookupError. stop_current_job caught
only the latter, so the EPERM escaped Stop itself - seen as the whole-suite
flake in test_review_416_ltx's Stop-during-the-join test (4.18.0 onward).
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
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="eperm-"))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P  # noqa: E402


class StopSurvivesEperm(unittest.TestCase):
    def test_every_process_group_kill_tolerates_eperm(self):
        def eperm(pgid, sig):
            raise PermissionError(1, "Operation not permitted")

        started = []
        job = {"id": "j-eperm", "status": "running", "params": {}}
        with P.LOCK:
            saved = {k: P.STATE.get(k) for k in ("current", "mux_pgid", "train_pgid", "h3_pgid", "music_pgid")}
            P.STATE.update(current=job, mux_pgid=4242, train_pgid=4343, h3_pgid=4444, music_pgid=None)
        try:
            with mock.patch.object(P.os, "killpg", eperm), \
                 mock.patch.object(P.HELPER, "kill", lambda: None), \
                 mock.patch.object(P.agent_image_engine, "kill_active_image_procs", lambda: 0), \
                 mock.patch.object(P.threading, "Thread") as T:
                T.return_value.start.side_effect = lambda: started.append(1)
                self.assertTrue(P.stop_current_job(timeout=0))
            self.assertTrue(job["cancel_requested"])
            # The delayed SIGKILL threads run the same tolerant kill.
            for call in T.call_args_list:
                target, args = call.kwargs["target"], call.kwargs["args"]
                with mock.patch.object(P.os, "killpg", eperm), \
                     mock.patch.object(P.time, "sleep", lambda s: None):
                    target(0.0, args[1])
        finally:
            with P.LOCK:
                P.STATE.update(saved)


if __name__ == "__main__":
    unittest.main()

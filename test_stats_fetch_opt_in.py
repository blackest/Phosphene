"""Issue #106 (4.19.3): the maintainer's repo-stats fetch is OPT-IN only.

WHAT THIS GUARDS AGAINST. Through 4.19.2 `stats_fetch_loop` started on every
install. To find a GitHub token it read GH_TOKEN / GITHUB_TOKEN and then ran
the user's own `gh auth token`, so a user with the GitHub CLI logged in had
the panel quietly use THEIR credentials once a day to read OUR repo's traffic.
Everyone else got "stats: no GitHub token resolvable ..." in their log, and
on 4.15 that line was the only thing in an otherwise empty bug report (#106).

The one rule: nothing in the stats fetcher runs, reads a token variable,
spawns `gh` or writes a log line unless the process was started with
PHOSPHENE_STATS_FETCH=1. The ~/.phosphene-test-rig marker does NOT turn it
on (it tags analytics; it sits on the maintainer's Mac where every clean room
would otherwise borrow the owner's `gh`). With the opt-in, behaviour is
unchanged. See scripts/STATS_DASHBOARD.md.
"""
from __future__ import annotations

import inspect
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mlx_ltx_panel as p
import panel.routes_stats as rs

ROOT = Path(p.__file__).resolve().parent
TOKEN_VARS = ("PHOSPHENE_REPO_STATS_TOKEN", "GH_STATS_TOKEN", "GH_TOKEN",
              "GITHUB_TOKEN")


class _RecordingEnviron(dict):
    """os.environ stand-in that remembers every key anyone looked up."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.looked_up: list[str] = []

    def get(self, key, default=None):
        self.looked_up.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.looked_up.append(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.looked_up.append(key)
        return super().__contains__(key)

    def copy(self):
        self.looked_up.append("<copy>")
        return dict(self)


def _base_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in TOKEN_VARS and k != "PHOSPHENE_STATS_FETCH"}
    # A user who has every token variable set: none may be read without
    # the opt-in.
    env.update({k: f"secret-{k.lower()}" for k in TOKEN_VARS})
    env.update(extra)
    return env


class WithoutOptIn(unittest.TestCase):
    def setUp(self):
        p._STATS_WARNED_NO_TOKEN = False
        p._STATS_WARNED_FETCH_FAIL = False
        self.addCleanup(setattr, p, "_STATS_WARNED_NO_TOKEN", False)
        self.addCleanup(setattr, p, "_STATS_WARNED_FETCH_FAIL", False)
        self.env = _RecordingEnviron(_base_env())
        patches = [
            mock.patch.object(os, "environ", self.env),
            mock.patch.object(p, "STATS_READONLY", False),
            # Even on the maintainer's Mac (marker present) it stays off.
            mock.patch.object(p, "ANALYTICS_TEST_RIG_MARKER",
                              mock.Mock(exists=mock.Mock(return_value=True))),
        ]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)
        self.run_calls = mock.patch.object(p.subprocess, "run")
        self.popen_calls = mock.patch.object(p.subprocess, "Popen")
        self.run_mock = self.run_calls.start()
        self.popen_mock = self.popen_calls.start()
        self.addCleanup(self.run_calls.stop)
        self.addCleanup(self.popen_calls.stop)

    def _assert_no_token_read(self):
        for var in TOKEN_VARS:
            self.assertNotIn(var, self.env.looked_up,
                             f"{var} was read without the opt-in")
        self.assertNotIn("<copy>", self.env.looked_up)

    def _assert_no_subprocess(self):
        self.run_mock.assert_not_called()
        self.popen_mock.assert_not_called()

    def test_disabled(self):
        self.assertFalse(p._stats_fetch_enabled())

    def test_resolve_token_reads_nothing_and_never_runs_gh(self):
        self.assertEqual(p._resolve_github_token(), "")
        self._assert_no_subprocess()
        self._assert_no_token_read()

    def test_fetch_once_is_silent_and_inert(self):
        with p.LOCK:
            before = len(p.STATE["log"])
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            p._run_stats_fetch_once()
        self._assert_no_subprocess()
        self._assert_no_token_read()
        self.assertEqual(err.getvalue(), "", "a stats line was written")
        with p.LOCK:
            self.assertEqual(len(p.STATE["log"]), before)

    def test_loop_returns_at_once(self):
        with mock.patch.object(p.time, "sleep",
                               side_effect=AssertionError("loop slept")):
            p.stats_fetch_loop()
        self._assert_no_subprocess()

    def test_refresh_route_refuses_without_a_thread(self):
        rs.P = p
        h = mock.Mock()
        with mock.patch.object(rs.threading, "Thread") as th:
            rs.stats_refresh(h, "/stats/refresh", {}, "")
        th.assert_not_called()
        body, code = h._json.call_args[0]
        self.assertEqual(code, 409)
        self.assertTrue(body.get("disabled"))
        self._assert_no_subprocess()

    def test_other_values_do_not_opt_in(self):
        for v in ("", "0", "false", "no", "maybe"):
            self.env["PHOSPHENE_STATS_FETCH"] = v
            self.assertFalse(p._stats_fetch_enabled(), v)


class WithOptIn(unittest.TestCase):
    """Unchanged maintainer behaviour once PHOSPHENE_STATS_FETCH=1."""

    def setUp(self):
        p._STATS_WARNED_NO_TOKEN = False
        p._STATS_WARNED_FETCH_FAIL = False
        self.addCleanup(setattr, p, "_STATS_WARNED_NO_TOKEN", False)
        self.addCleanup(setattr, p, "_STATS_WARNED_FETCH_FAIL", False)
        pt = mock.patch.object(p, "STATS_READONLY", False)
        pt.start()
        self.addCleanup(pt.stop)

    def test_env_token_first(self):
        env = _base_env(PHOSPHENE_STATS_FETCH="1")
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(p.subprocess, "run") as run:
            self.assertEqual(p._resolve_github_token(),
                             "secret-phosphene_repo_stats_token")
        run.assert_not_called()

    def test_falls_back_to_gh(self):
        env = {k: v for k, v in _base_env(PHOSPHENE_STATS_FETCH="1").items()
               if k not in TOKEN_VARS}
        fake = subprocess.CompletedProcess(["gh"], 0, stdout="tok\n", stderr="")
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(p.subprocess, "run", return_value=fake) as run:
            self.assertEqual(p._resolve_github_token(), "tok")
        self.assertEqual(run.call_args[0][0], ["gh", "auth", "token"])

    def test_fetch_runs_with_the_token(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            seen["env"] = kw.get("env") or {}
            return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

        with mock.patch.dict(os.environ, {"PHOSPHENE_STATS_FETCH": "1"}), \
             mock.patch.object(p, "_resolve_github_token", return_value="tok"), \
             mock.patch.object(p.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(sys, "stderr", io.StringIO()):
            p._run_stats_fetch_once()
        self.assertEqual(seen["cmd"][-1], str(p.STATS_FETCHER))
        self.assertEqual(seen["env"].get("GITHUB_TOKEN"), "tok")

    def test_refresh_route_queues(self):
        rs.P = p
        h = mock.Mock()
        with mock.patch.dict(os.environ, {"PHOSPHENE_STATS_FETCH": "1"}), \
             mock.patch.object(rs.threading, "Thread") as th:
            rs.stats_refresh(h, "/stats/refresh", {}, "")
        th.assert_called_once()
        self.assertEqual(h._json.call_args[0][1], 202)


class BootBlock(unittest.TestCase):
    def test_thread_start_is_behind_the_opt_in(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        starts = [m.start() for m in re.finditer(r"target=stats_fetch_loop", src)]
        self.assertEqual(len(starts), 1, "exactly one place starts the loop")
        guard = src.rfind("\n    if ", 0, starts[0])
        self.assertIn("_stats_fetch_enabled()", src[guard:starts[0]])

    def test_every_entry_point_checks_first(self):
        for fn in (p._resolve_github_token, p._run_stats_fetch_once,
                   p.stats_fetch_loop):
            body = inspect.getsource(fn).split('"""')[-1]
            first = [ln.strip() for ln in body.splitlines() if ln.strip()
                     and not ln.strip().startswith(("global", "#"))][0]
            self.assertIn("_stats_fetch_enabled()", first, fn.__name__)


class RealProcessNeverRunsGh(unittest.TestCase):
    """End to end in a fresh interpreter: a fake `gh` first on PATH that
    records being run. Without the opt-in it must never run; with it, it is
    the fallback token source exactly as before."""

    def _run(self, opt_in: bool) -> bool:
        d = Path(tempfile.mkdtemp())
        bindir = d / "bin"
        bindir.mkdir()
        hit = d / "gh-was-run"
        gh = bindir / "gh"
        gh.write_text(f"#!/bin/sh\ntouch '{hit}'\necho fake-token\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        env = {k: v for k, v in os.environ.items()
               if k not in TOKEN_VARS and k != "PHOSPHENE_STATS_FETCH"}
        env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
        env["LTX_STATE_DIR"] = str(d / "state")
        if opt_in:
            env["PHOSPHENE_STATS_FETCH"] = "1"
        code = ("import mlx_ltx_panel as p\n"
                "p.STATS_FETCHER = p.Path('/nonexistent/fetch.py')\n"
                "t = p._resolve_github_token()\n"
                "p._run_stats_fetch_once()\n"
                "print('TOKEN', bool(t))\n")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, errors="replace",
                             timeout=180)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertNotIn("[stats]", out.stderr)
        return hit.exists()

    def test_no_gh_without_opt_in(self):
        self.assertFalse(self._run(opt_in=False))

    def test_gh_is_the_fallback_with_opt_in(self):
        self.assertTrue(self._run(opt_in=True))


if __name__ == "__main__":
    unittest.main()

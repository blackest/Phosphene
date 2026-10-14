"""Switching the LTX generation (2.3 <-> 2.5) from the panel.

THE PAIN. Ingredients' reference adapter is trained on LTX-2.3 and the default
generation is 2.5, so on a stock install the Ingredients chip is greyed. The
only way to a 2.3 render was to hand-edit <install>/ENVIRONMENT and restart from
Pinokio - and the greyed chip (pointer-events:none) swallowed its own tooltip,
so nothing said how. A generation is fixed per process (ACTIVE_MODEL_VERSION is
read once at import), so the honest switch is: persist the choice to ENVIRONMENT
and re-exec the panel, exactly as /restart does.

These tests pin: the ENVIRONMENT writer, which generations are offered, that
refusals happen BEFORE anything is written, and that the page wires it up.
"""
from __future__ import annotations

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

import mlx_ltx_panel as P                                            # noqa: E402

routes_meta = sys.modules["panel.routes_meta"]
# Generation tests must not depend on the headers of whatever packs are on THIS
# disk; IntegrityGate covers the scan's effect explicitly.
_INTEGRITY_OK = {"ok": True, "bad": [], "checked": 0}


class _Handler:
    """Just enough of the request handler for the route under test."""

    def __init__(self, form):
        self._form = form
        self.sent = []

    def _read_form_body(self):
        return b"", {k: [v] for k, v in self._form.items()}

    def _json(self, payload, code=200):
        self.sent.append((code, payload))


class EnvironmentWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(P, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = self.root / "ENVIRONMENT"

    def test_appends_when_absent_and_keeps_other_lines(self):
        self.env.write_text("HF_HOME=./cache/HF_HOME\nGRADIO_TEMP_DIR=./cache/G\n")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(),
                         "HF_HOME=./cache/HF_HOME\nGRADIO_TEMP_DIR=./cache/G\n"
                         "LTX_MODEL_VERSION=ltx23\n")

    def test_replaces_in_place(self):
        self.env.write_text("A=1\nLTX_MODEL_VERSION=ltx25\nB=2\n")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(), "A=1\nLTX_MODEL_VERSION=ltx23\nB=2\n")

    def test_every_duplicate_is_rewritten_so_first_and_last_readers_agree(self):
        # Pinokio/dotenv, pinokio.js and q8_weights.sh take the LAST assignment;
        # download_q8.js takes the FIRST. Rewriting only the last left the Q8
        # downloader on 2.5 while the panel booted 2.3.
        self.env.write_text("LTX_MODEL_VERSION=ltx25\nA=1\nexport LTX_MODEL_VERSION=ltx25\n")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(),
                         "LTX_MODEL_VERSION=ltx23\nA=1\nLTX_MODEL_VERSION=ltx23\n")

    def test_commented_assignment_is_left_alone(self):
        self.env.write_text("# LTX_MODEL_VERSION=ltx25\nA=1\n")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(),
                         "# LTX_MODEL_VERSION=ltx25\nA=1\nLTX_MODEL_VERSION=ltx23\n")

    def test_creates_the_file_when_missing(self):
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(), "LTX_MODEL_VERSION=ltx23\n")

    def test_no_temp_file_left_behind(self):
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["ENVIRONMENT"])

    def test_refuses_unsafe_key_or_value(self):
        for key, value in (("lower", "x"), ("A B", "x"), ("A", "x\nB=1"), ("A", "a b"), ("A", "")):
            with self.assertRaises(ValueError, msg=(key, value)):
                P.set_environment_value(key, value)
        self.assertFalse(self.env.exists())


class GenerationChoices(unittest.TestCase):
    def _choices(self, installed, active):
        def missing(name, version_id=None):
            assert name == "render", name
            return [] if version_id in installed else ["model.safetensors"]
        with mock.patch.object(P, "capability_missing", side_effect=missing), \
                mock.patch.object(P, "_model_integrity", return_value=_INTEGRITY_OK), \
                mock.patch.object(P, "ACTIVE_MODEL_VERSION", active):
            return P.ltx_generation_choices()

    def test_both_installed_lists_both_and_marks_the_active_one(self):
        got = self._choices({"ltx23", "ltx25"}, "ltx25")
        self.assertEqual([c["id"] for c in got if c["id"] in ("ltx23", "ltx25")],
                         [v["id"] for v in P.MODEL_VERSIONS if v["id"] in ("ltx23", "ltx25")])
        self.assertEqual([c["id"] for c in got if c["active"]], ["ltx25"])
        self.assertEqual({c["label"] for c in got}, {"2.3", "2.5"})

    def test_a_generation_that_is_not_on_disk_is_not_offered(self):
        # Offering it would restart the panel into weights it cannot load.
        self.assertEqual(self._choices({"ltx25"}, "ltx25"), [])

    def test_no_real_choice_hides_the_control(self):
        self.assertEqual(self._choices({"ltx23"}, "ltx23"), [])

    def test_the_active_generation_is_listed_even_if_its_check_fails(self):
        got = self._choices({"ltx23"}, "ltx25")
        self.assertEqual({c["id"] for c in got}, {"ltx23", "ltx25"})

    def test_engines_payload_carries_generations_only_on_ltx(self):
        with mock.patch.object(P, "ltx_generation_choices",
                               return_value=[{"id": "ltx23", "label": "2.3",
                                              "active": False, "default": False},
                                             {"id": "ltx25", "label": "2.5",
                                              "active": True, "default": True}]):
            payload = {e["id"]: e for e in P.engines_payload()}
        self.assertEqual(len(payload["ltx"]["generations"]), 2)
        self.assertNotIn("generations", payload["h3"])


class SwitchRoute(unittest.TestCase):
    CHOICES = [{"id": "ltx23", "label": "2.3", "active": False, "default": False},
               {"id": "ltx25", "label": "2.5", "active": True, "default": True}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / "ENVIRONMENT"
        self.env.write_text("A=1\n")
        for patcher in (mock.patch.object(P, "ROOT", self.root),
                        mock.patch.object(P, "ACTIVE_MODEL_VERSION", "ltx25"),
                        mock.patch.object(P, "ltx_generation_choices",
                                          return_value=self.CHOICES),
                        # The switch's own refusals read the live queue and
                        # downloads: a job left by another suite made this a
                        # 409 in a full run.
                        mock.patch.dict(P.STATE, {"current": None, "queue": [], "paused": False}),
                        mock.patch.dict(P.DOWNLOAD, {"active": False}),
                        mock.patch.object(P, "push")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.reexec = mock.patch.object(routes_meta, "reexec_panel").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: os.environ.pop("LTX_MODEL_VERSION", None))
        os.environ.pop("LTX_MODEL_VERSION", None)

    def _post(self, version, refusal=None):
        h = _Handler({"version": version})
        with mock.patch.object(routes_meta, "restart_refusal", return_value=refusal):
            routes_meta.post_ltx_generation(h, "/ltx/generation", {}, "")
        return h

    def test_switch_persists_sets_env_and_restarts(self):
        h = self._post("ltx23")
        self.assertEqual(self.env.read_text(), "A=1\nLTX_MODEL_VERSION=ltx23\n")
        self.assertEqual(os.environ.get("LTX_MODEL_VERSION"), "ltx23")
        self.reexec.assert_called_once_with(h)

    def test_the_switch_holds_the_worker_until_the_exec(self):
        # Codex 4.19.2: the refusals each took the lock on their own, so the
        # worker could start the last waiting job between them. The final check
        # and the pause happen under one lock.
        P.STATE["paused"] = False
        self._post("ltx23")
        self.assertTrue(P.STATE["paused"])

    def test_a_render_that_starts_between_the_checks_is_refused(self):
        with mock.patch.object(routes_meta, "generation_switch_refusal", return_value=None):
            P.STATE["current"] = {"id": "late"}
            try:
                h = self._post("ltx23")
            finally:
                P.STATE["current"] = None
        self.assertEqual(h.sent[0][0], 409)
        self.assertEqual(self.env.read_text(), "A=1\n")
        self.reexec.assert_not_called()

    def test_unknown_or_uninstalled_generation_is_a_400_and_writes_nothing(self):
        for bad in ("ltx99", "", "../../etc", "ltx23; rm -rf /"):
            h = self._post(bad)
            self.assertEqual(h.sent[0][0], 400, bad)
        self.assertEqual(self.env.read_text(), "A=1\n")
        self.reexec.assert_not_called()

    def test_choosing_the_active_generation_is_a_no_op(self):
        h = self._post("ltx25")
        self.assertEqual(h.sent, [(200, {"ok": True, "unchanged": True, "version": "ltx25"})])
        self.assertEqual(self.env.read_text(), "A=1\n")
        self.reexec.assert_not_called()

    def test_a_running_render_refuses_before_anything_is_written(self):
        busy = (409, {"ok": False, "busy": True, "error": "A render is running."})
        h = self._post("ltx23", refusal=busy)
        self.assertEqual(h.sent, [busy])
        self.assertEqual(self.env.read_text(), "A=1\n")
        self.assertNotIn("LTX_MODEL_VERSION", os.environ)
        self.reexec.assert_not_called()

    def test_unwritable_environment_reports_and_does_not_restart(self):
        with mock.patch.object(P, "set_environment_value", side_effect=OSError("read-only")):
            h = self._post("ltx23")
        self.assertEqual(h.sent[0][0], 500)
        self.assertIn("read-only", h.sent[0][1]["error"])
        self.assertNotIn("LTX_MODEL_VERSION", os.environ)
        self.reexec.assert_not_called()
        self.assertFalse(P.STATE["paused"], "a failed switch gives the queue back")


class RestartRefusal(unittest.TestCase):
    def test_idle_panel_may_restart(self):
        with mock.patch.object(P, "post_update_status", return_value={}), \
                mock.patch.dict(P.STATE, {"current": None}):
            self.assertIsNone(routes_meta.restart_refusal())

    def test_running_render_refuses(self):
        with mock.patch.object(P, "post_update_status", return_value={}), \
                mock.patch.dict(P.STATE, {"current": {"id": "x"}}):
            code, payload = routes_meta.restart_refusal()
        self.assertEqual(code, 409)
        self.assertTrue(payload["busy"])

    def test_unfinished_update_refuses(self):
        with mock.patch.object(P, "post_update_status", return_value={"pending": True}):
            code, payload = routes_meta.restart_refusal()
        self.assertEqual(code, 409)
        self.assertTrue(payload["post_update"])


class EnvironmentWriterEdges(unittest.TestCase):
    """The file is the user's: Pinokio writes it, users hand-edit it, and three
    other readers (Pinokio's dotenv, pinokio.js, download_q8.js, q8_weights.sh)
    parse it with three different grammars. Writing it must disturb nothing
    else and leave every reader with the same answer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(P, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = self.root / "ENVIRONMENT"

    def test_crlf_file_stays_crlf(self):
        self.env.write_bytes(b"A=1\r\nLTX_MODEL_VERSION=ltx25\r\nB=2\r\n")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_bytes(), b"A=1\r\nLTX_MODEL_VERSION=ltx23\r\nB=2\r\n")
        self.env.write_bytes(b"A=1\r\nB=2")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_bytes(), b"A=1\r\nB=2\r\nLTX_MODEL_VERSION=ltx23\r\n")

    def test_missing_trailing_newline_is_not_glued_onto(self):
        self.env.write_text("HF_HOME=./cache/HF_HOME")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(),
                         "HF_HOME=./cache/HF_HOME\nLTX_MODEL_VERSION=ltx23\n")

    def test_every_other_byte_survives(self):
        original = ("# Pinokio app environment\n\n"
                    "HF_HOME=./cache/HF_HOME\n"
                    "LTX_H3_ROOT=\"/Volumes/Media Drive/H3\"   # moved\n"
                    "  # LTX_MODEL_VERSION=ltx23 (old note)\n"
                    "EMPTY=\n")
        self.env.write_text(original)
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(), original + "LTX_MODEL_VERSION=ltx23\n")

    def test_quoted_and_commented_value_is_replaced_whole(self):
        self.env.write_text('LTX_MODEL_VERSION="ltx25"   # pinned\n')
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(), "LTX_MODEL_VERSION=ltx23\n")

    def test_file_mode_is_kept(self):
        self.env.write_text("A=1\n")
        os.chmod(self.env, 0o600)
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)

    def test_symlinked_environment_is_written_through(self):
        real = self.root / "shared_env"
        real.write_text("A=1\n")
        self.env.symlink_to(real)
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertTrue(self.env.is_symlink())
        self.assertEqual(real.read_text(), "A=1\nLTX_MODEL_VERSION=ltx23\n")

    def test_no_temp_file_left_after_a_failed_write(self):
        self.env.write_text("A=1\n")
        with mock.patch.object(P.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(), "A=1\n")

    def test_all_four_readers_agree_after_a_write(self):
        import re as _re
        import subprocess as _sp
        self.env.write_text("LTX_MODEL_VERSION=ltx25\n# LTX_MODEL_VERSION=ltx25\n"
                            "A=1\nLTX_MODEL_VERSION=ltx25\n")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        text = self.env.read_text()
        # download_q8.js: comment lines dropped, FIRST match.
        live = "\n".join(l for l in text.split("\n") if not _re.match(r"^\s*#", l))
        first = _re.search(r"^\s*LTX_MODEL_VERSION\s*=\s*(\S+)\s*$", live, _re.M).group(1)
        # pinokio.js / dotenv: LAST assignment.
        last = _re.findall(r"^\s*(?:export\s+)?LTX_MODEL_VERSION\s*=\s*(\S+)", live, _re.M)[-1]
        # scripts/pinokio/q8_weights.sh, its own sed line verbatim.
        sed = _sp.run(["sh", "-c", "sed -n 's/^[[:space:]]*LTX_MODEL_VERSION[[:space:]]*=[[:space:]]*"
                                   "\\([^[:space:]#]*\\).*/\\1/p' ENVIRONMENT | tail -1"],
                      cwd=self.root, capture_output=True, text=True).stdout.strip()
        self.assertEqual({first, last, sed}, {"ltx23"})


class DefaultsUnchanged(unittest.TestCase):
    """2.5 is the default lane and nothing falls back. These pin that the switch
    is inert until clicked: no env line -> 2.5, offering a choice writes
    nothing, and an install that cannot render 2.3 is never offered it."""

    def test_no_environment_line_boots_ltx25(self):
        import subprocess as _sp
        env = {k: v for k, v in os.environ.items() if k != "LTX_MODEL_VERSION"}
        out = _sp.run([sys.executable, "-c",
                       "import mlx_ltx_panel as P; "
                       "print(P.ACTIVE_MODEL_VERSION, P.default_model_version_id(), "
                       "[e.get('generation') for e in P.engines_payload() if e['id']=='ltx'][0])"],
                      cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "ltx25 ltx25 2.5", out.stderr[-2000:])

    def test_listing_generations_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "ROOT", Path(d)), \
                mock.patch.object(P, "_model_integrity", return_value=_INTEGRITY_OK), \
                mock.patch.object(P, "capability_missing", return_value=[]):
            payload = {e["id"]: e for e in P.engines_payload()}
            self.assertEqual(len(payload["ltx"]["generations"]), 2)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_missing_default_pack_never_switches_by_itself(self):
        # 2.5 active with its pack gone and 2.3 complete: both are OFFERED (the
        # user may pick 2.3), but the process stays on 2.5 - nothing is chosen
        # for them.
        def missing(name, version_id=None):
            return ["x"] if version_id == "ltx25" else []
        with mock.patch.object(P, "capability_missing", side_effect=missing), \
                mock.patch.object(P, "_model_integrity", return_value=_INTEGRITY_OK), \
                mock.patch.object(P, "ACTIVE_MODEL_VERSION", "ltx25"):
            got = P.ltx_generation_choices()
        self.assertEqual([c["id"] for c in got if c["active"]], ["ltx25"])

    def test_text_encoder_counts_as_installed(self):
        # Real capability_missing over the real registry: 2.3's q4 pack on disk
        # but Gemma 3 short is NOT a generation the panel can render with.
        def repo_missing(repo):
            return ["model.safetensors"] if repo.get("key") == want_missing else []
        with mock.patch.object(P, "_repo_effective_missing", side_effect=repo_missing), \
                mock.patch.object(P, "_model_integrity", return_value=_INTEGRITY_OK), \
                mock.patch.object(P, "ACTIVE_MODEL_VERSION", "ltx25"):
            want_missing = "gemma"
            self.assertEqual(P.ltx_generation_choices(), [])
            want_missing = "q4"
            self.assertEqual(P.ltx_generation_choices(), [])
            want_missing = None
            self.assertEqual({c["id"] for c in P.ltx_generation_choices()}, {"ltx23", "ltx25"})


class IntegrityGate(unittest.TestCase):
    def test_a_pack_the_integrity_scan_flags_is_not_offered(self):
        # Complete by size, but its headers are garbage: the boot scan would
        # greet the switched panel with the Repair banner. Not a real choice.
        with mock.patch.object(P, "capability_missing", return_value=[]), \
                mock.patch.object(P, "ACTIVE_MODEL_VERSION", "ltx25"), \
                mock.patch.object(P, "_model_integrity", return_value={
                    "ok": False, "bad": [{"repo": "q4", "file": "connector.safetensors",
                                          "reason": "implausible header length (0)"}]}):
            self.assertEqual(P.ltx_generation_choices(), [])
        with mock.patch.object(P, "capability_missing", return_value=[]), \
                mock.patch.object(P, "ACTIVE_MODEL_VERSION", "ltx25"), \
                mock.patch.object(P, "_model_integrity", return_value={"ok": True, "bad": []}):
            self.assertEqual(len(P.ltx_generation_choices()), 2)


class SwitchRouteRealRefusals(unittest.TestCase):
    """The route with the REAL refusals (restart_refusal and
    generation_switch_refusal unmocked), so busy/queue/download/update states
    are proven to refuse before anything touches disk or the environment."""

    CHOICES = SwitchRoute.CHOICES

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / "ENVIRONMENT"
        self.env.write_text("A=1\n")
        for patcher in (mock.patch.object(P, "ROOT", self.root),
                        mock.patch.object(P, "ACTIVE_MODEL_VERSION", "ltx25"),
                        mock.patch.object(P, "ltx_generation_choices", return_value=self.CHOICES),
                        mock.patch.object(P, "post_update_status", return_value={}),
                        mock.patch.dict(P.STATE, {"current": None, "queue": []}),
                        mock.patch.dict(P.DOWNLOAD, {"active": False}),
                        mock.patch.object(P, "push")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.reexec = mock.patch.object(routes_meta, "reexec_panel").start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(lambda: os.environ.pop("LTX_MODEL_VERSION", None))
        os.environ.pop("LTX_MODEL_VERSION", None)

    def _post(self, version):
        h = _Handler({"version": version})
        routes_meta.post_ltx_generation(h, "/ltx/generation", {}, "")
        return h

    def _assert_untouched(self):
        self.assertEqual(self.env.read_text(), "A=1\n")
        self.assertNotIn("LTX_MODEL_VERSION", os.environ)
        self.reexec.assert_not_called()

    def test_idle_switch_goes_through(self):
        self._post("ltx23")
        self.assertEqual(self.env.read_text(), "A=1\nLTX_MODEL_VERSION=ltx23\n")
        self.reexec.assert_called_once()

    def test_running_render_refuses(self):
        P.STATE["current"] = {"id": "j1"}
        h = self._post("ltx23")
        self.assertEqual(h.sent[0][0], 409)
        self.assertTrue(h.sent[0][1]["busy"])
        self._assert_untouched()

    def test_waiting_jobs_refuse(self):
        # A job resolves its weights when it starts; after a restart onto 2.3 a
        # job queued on 2.5 would run there without anyone choosing that.
        P.STATE["queue"] = [{"id": "j2", "params": {"mode": "t2v"}}]
        h = self._post("ltx23")
        self.assertEqual(h.sent[0][0], 409)
        self.assertEqual(h.sent[0][1]["queued"], 1)
        self._assert_untouched()

    def test_running_download_refuses(self):
        P.DOWNLOAD["active"] = True
        h = self._post("ltx23")
        self.assertEqual(h.sent[0][0], 409)
        self.assertTrue(h.sent[0][1]["download"])
        self._assert_untouched()

    def test_unfinished_update_refuses(self):
        with mock.patch.object(P, "post_update_status", return_value={"active": True}):
            h = self._post("ltx23")
        self.assertEqual(h.sent[0][0], 409)
        self.assertTrue(h.sent[0][1]["post_update"])
        self._assert_untouched()

    def test_bad_input_is_400_before_any_state_is_read(self):
        for bad in ("ltx99", "LTX23", "ltx23\nA=1", "ltx23 x", "../ENVIRONMENT", "x" * 5000):
            h = self._post(bad)
            self.assertEqual(h.sent[0][0], 400, bad)
        self._assert_untouched()

    def test_restart_keeps_its_own_refusals_only(self):
        # /restart is unchanged by the refactor: waiting jobs and downloads do
        # not stop it (they survive on disk / are its long-standing contract),
        # a running render does.
        P.STATE["queue"] = [{"id": "j3"}]
        P.DOWNLOAD["active"] = True
        h = _Handler({})
        routes_meta.post_restart(h, "/restart", {}, "")
        self.reexec.assert_called_once_with(h)
        self.reexec.reset_mock()
        P.STATE["current"] = {"id": "j4"}
        h = _Handler({})
        routes_meta.post_restart(h, "/restart", {}, "")
        self.assertEqual(h.sent, [(409, {"ok": False, "busy": True,
                                         "error": "A render is running. Stop it first, or "
                                                  "wait for it to finish — restarting now "
                                                  "would kill it."})])
        self.reexec.assert_not_called()


class PageWiring(unittest.TestCase):
    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf8")

    def test_engine_menu_posts_to_the_route_and_waits_for_the_panel(self):
        js = self._read("webapp/js/engines.js")
        self.assertIn("/ltx/generation", js)
        self.assertIn("switchLtxGeneration", js)
        self.assertIn("/version", js)          # polls until the new image answers

    def test_scripts_do_not_touch_page_helpers_or_later_globals_while_loading(self):
        # Regression for two real load-order bugs. engines.js called
        # renderLtxGenSwitch() at load, which needs escapeHtml from a script that
        # loads AFTER it - the throw aborted the rest of engines.js and the
        # LTX | H3 dropdown vanished. And characters.js read globalThis.ENGINES,
        # which engines.js assigns later, so the chip never saw 2.3.
        engines = self._read("webapp/js/engines.js")
        top_level = [ln for ln in engines.splitlines() if ln.startswith("renderLtxGenSwitch(")
                     or "addEventListener('DOMContentLoaded', renderLtxGenSwitch" in ln]
        self.assertEqual(top_level, [], "renderLtxGenSwitch must not run at script load")
        chars = self._read("webapp/js/characters.js")
        body = chars.split("function _ltx23Switchable()", 1)[1].split("\n}\n", 1)[0]
        code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("//"))
        self.assertIn("BOOT.engines", code)
        self.assertNotIn("ENGINES", code.replace("BOOT.engines", ""))

    def test_ingredients_chip_stays_clickable_when_2_3_can_be_switched_to(self):
        js = self._read("webapp/js/characters.js")
        self.assertIn("_ltx23Switchable", js)
        self.assertIn("needs-switch", js)
        css = self._read("webapp/style/panel.css")
        # .disabled carries pointer-events:none; the switchable chip must not.
        block = css.split(".pill-btn.needs-switch", 1)[1].split("}", 1)[0]
        self.assertNotIn("pointer-events: none", block)


class ClientSwitchExecutes(unittest.TestCase):
    """switchLtxGeneration() RUN in node against stubs - the shipped version
    called a `toast` that does not exist, so every path threw: refusals were
    silent and a successful switch never reloaded the page. A grep cannot see
    that; executing it can."""

    def _run(self, responses):
        import json as _json
        import shutil as _sh
        import subprocess as _sp
        if not _sh.which("node"):
            self.skipTest("node not on PATH")
        from scripts.extract_panel_js import extract_function
        fn = extract_function("switchLtxGeneration", self._engines())
        src = """
const toasts=[]; let reloads=0; const calls=[];
const RESP=%s;
globalThis.phosToast=(m,o)=>toasts.push([m,(o||{}).kind]);
const closeEngineMenu=()=>{};
const location={reload(){reloads++;}};
globalThis.setTimeout=(f)=>f();
globalThis.fetch=async(url,opts)=>{calls.push(url); const r=RESP.shift();
  if(!r||r.throw) throw new TypeError('network');
  return {ok:r.status<400,status:r.status,json:async()=>r.body};};
%s
switchLtxGeneration('ltx23').then(v=>console.log(JSON.stringify({v,toasts,reloads,calls})))
  .catch(e=>console.log(JSON.stringify({threw:String(e)})));
""" % (_json.dumps(responses), fn)
        out = _sp.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return _json.loads(out.stdout.strip().splitlines()[-1])

    def _engines(self):
        return (ROOT / "webapp/js/engines.js").read_text(encoding="utf8")

    def test_refusal_is_shown_and_nothing_reloads(self):
        got = self._run([{"status": 409, "body": {"ok": False, "busy": True,
                                                   "error": "A render is running."}}])
        self.assertEqual(got, {"v": False, "toasts": [["A render is running.", "warning"]],
                               "reloads": 0, "calls": ["/ltx/generation"]})

    def test_success_waits_for_the_new_panel_then_reloads(self):
        got = self._run([{"status": 200, "body": {"ok": True, "restarting": True}},
                         {"throw": True}, {"status": 200, "body": {}}])
        self.assertTrue(got["v"])
        self.assertEqual(got["reloads"], 1)
        self.assertEqual(got["calls"], ["/ltx/generation", "/version", "/version"])
        self.assertEqual(got["toasts"][0][1], "success")

    def test_connection_dropped_by_the_exec_still_reloads(self):
        got = self._run([{"throw": True}, {"status": 200, "body": {}}])
        self.assertEqual((got["v"], got["reloads"]), (True, 1))

    def test_unchanged_is_quiet(self):
        got = self._run([{"status": 200, "body": {"ok": True, "unchanged": True}}])
        self.assertEqual(got, {"v": True, "toasts": [], "reloads": 0, "calls": ["/ltx/generation"]})

    def test_ingredients_chip_can_reach_the_switch(self):
        # characters.js calls switchLtxGeneration across a module boundary, so
        # engines.js must PUBLISH it; and the call must be unguarded so
        # lint_webapp's no-undef fails the build if the publish is ever lost.
        engines = self._engines()
        block = engines.split("Object.assign(globalThis, {", 1)[1].split("});", 1)[0]
        self.assertIn("switchLtxGeneration,", block)
        chars = (ROOT / "webapp/js/characters.js").read_text(encoding="utf8")
        self.assertNotIn("typeof switchLtxGeneration", chars)
        self.assertIn("switchLtxGeneration(g23.id)", chars)


if __name__ == "__main__":
    unittest.main()

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

    def test_last_assignment_wins_so_only_it_is_rewritten(self):
        self.env.write_text("LTX_MODEL_VERSION=ltx25\nLTX_MODEL_VERSION=ltx25\n")
        P.set_environment_value("LTX_MODEL_VERSION", "ltx23")
        self.assertEqual(self.env.read_text(),
                         "LTX_MODEL_VERSION=ltx25\nLTX_MODEL_VERSION=ltx23\n")

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
        def missing(quant, version_id=None):
            return [] if version_id in installed else ["model.safetensors"]
        with mock.patch.object(P, "pack_missing_files", side_effect=missing), \
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


if __name__ == "__main__":
    unittest.main()

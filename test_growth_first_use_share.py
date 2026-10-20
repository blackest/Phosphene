#!/usr/bin/env python3
"""Growth overnight 2026-10-19: the funnel's blind minute, and a share loop.

WHY (fleet, cleaned of the maintainer's own installs, cohorts 2026-09-20 ->
2026-10-11): 101 of 512 new installs (20%) booted the panel exactly once and
never rendered, and 74 of those 101 sent nothing but the boot events in the
same minute. Pinokio auto-runs Start the moment a ~37 GB install finishes
(`default: true` in pinokio.js), so a panel can come up with nobody in front
of it - and the data cannot tell "nobody ever looked" from "looked and left".
Those two need opposite fixes (bring people back vs. fix the first screen).

  1  `install_step` gains ONE closed step, `first_use`: the first click or
     key press anyone makes on the panel page, once per install, ever. The
     browser may report that step and no other (it must not be able to forge
     `first_queue`).
  2  The clip toolbar's More menu (and the lightbox row) gains **Share**:
     copies a one-line "made on my Mac with Phosphene" caption with the repo
     link and selects the file in Finder, ready to drag into a post. Counted
     as `feature_used: share` - nothing about the clip is sent.

No renders, no GPU, no network: the UI route is driven with a fake handler and
the capture function is swapped for a list.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-growth-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from panel.routes import POST_ROUTES  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
HJS = (ROOT / "webapp" / "js" / "health.js").read_text(encoding="utf-8")
MJS = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
DOC = (ROOT / "docs" / "ANALYTICS.md").read_text(encoding="utf-8")
NODE = shutil.which("node")


class _H:
    def __init__(self, body: str = ""):
        self.body, self.out = body, None

    def _read_form_body(self):
        from urllib.parse import parse_qs
        return self.body, parse_qs(self.body)

    def _json(self, obj, code=200):
        self.out = (code, obj)


class _Fleet:
    """A fresh install with the capture function swapped for a list."""

    def __enter__(self):
        self.seen = []
        self.store = {"analytics_install_steps": {}}
        self._orig = (P._analytics_capture, P.get_settings, P._settings_set_internal)
        P._analytics_capture = lambda ev, props=None: self.seen.append((ev, dict(props or {})))
        P.get_settings = lambda: dict(self.store)
        P._settings_set_internal = lambda **kv: self.store.update(kv)
        return self

    def __exit__(self, *a):
        P._analytics_capture, P.get_settings, P._settings_set_internal = self._orig

    def events(self, name):
        return [p for e, p in self.seen if e == name]


def _ui(body: str) -> _H:
    h = _H(body)
    POST_ROUTES["/analytics/ui"](h, "/analytics/ui", {}, "")
    return h


class FirstUseStep(unittest.TestCase):

    def test_first_use_is_a_closed_install_step(self):
        self.assertIn("first_use", P._INSTALL_STEPS)

    def test_the_page_reports_it_once_per_install_ever(self):
        with _Fleet() as f:
            for _ in range(4):
                h = _ui("event=install_step&step=first_use")
                self.assertEqual(h.out[0], 200, h.out)
            ev = f.events("install_step")
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["step"], "first_use")
            self.assertEqual(ev[0]["outcome"], "ok")
            # The same small, closed shape as every other step: no path, no
            # page, no timing.
            self.assertEqual(set(ev[0]), {"step", "outcome", "version", "ram_gb"})

    def test_the_browser_cannot_forge_any_other_step(self):
        with _Fleet() as f:
            for step in ("first_queue", "first_boot", "engine_env", "weights_check", "x"):
                h = _ui(f"event=install_step&step={step}")
                self.assertEqual(h.out[0], 400, step)
            h = _ui("event=install_step&step=first_use&outcome=failed&error_class=disk_full")
            self.assertEqual(h.out[0], 200)
            ev = f.events("install_step")
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["outcome"], "ok")
            self.assertNotIn("error_class", ev[0])

    def test_documented(self):
        self.assertIn("`first_use`", DOC)

    def test_the_page_arms_it_on_a_real_interaction_only(self):
        # The arm function lives in health.js next to the one analytics
        # channel; main.js (run-once startup) calls it.
        self.assertIn("function armFirstUseReport(", HJS)
        self.assertIn("armFirstUseReport()", MJS)
        fn = extract_function("armFirstUseReport", HJS)
        self.assertIn("pointerdown", fn)
        self.assertIn("keydown", fn)
        self.assertNotIn("mousemove", fn)   # a hover is not a person deciding


@unittest.skipIf(NODE is None, "node not on PATH")
class FirstUseInTheBrowser(unittest.TestCase):
    """Executed, not grepped: one POST on the first interaction, none on a
    page that has already reported, none before anyone touches the page."""

    def _run(self, interactions: int, stored: str | None = None):
        fn = extract_function("armFirstUseReport", HJS)
        script = f"""
const posted = [];
function _uiEvent(ev, props) {{ posted.push([ev, props]); }}
const store = {{}};
{"store['phos_first_use_sent'] = " + json.dumps(stored) + ";" if stored is not None else ""}
global.localStorage = {{ getItem: k => (k in store ? store[k] : null), setItem: (k, v) => {{ store[k] = String(v); }} }};
const listeners = {{}};
global.document = {{ addEventListener: (t, f, o) => {{ (listeners[t] = listeners[t] || []).push(f); }},
                     removeEventListener: (t, f) => {{ listeners[t] = (listeners[t] || []).filter(g => g !== f); }} }};
{fn}
armFirstUseReport();
for (let i = 0; i < {interactions}; i++) {{
  for (const t of ['pointerdown', 'keydown']) for (const f of (listeners[t] || []).slice()) f({{}});
}}
console.log(JSON.stringify({{posted, store}}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_nothing_before_an_interaction(self):
        self.assertEqual(self._run(0)["posted"], [])

    def test_one_post_per_page_load_however_many_interactions(self):
        out = self._run(5)
        self.assertEqual(out["posted"], [["install_step", {"step": "first_use"}]])

    def test_no_browser_side_flag_silences_a_reinstall(self):
        """Every install on a Mac shares one origin, so a localStorage flag
        from an earlier install would hide a new install id's first_use for
        good (Codex quick pass). The server owns once-per-install."""
        out = self._run(1, stored="1")
        self.assertEqual(out["posted"], [["install_step", {"step": "first_use"}]])
        self.assertNotIn("localStorage", extract_function("armFirstUseReport", HJS))


class ShareLoop(unittest.TestCase):

    def test_share_is_a_closed_feature_the_page_may_report(self):
        self.assertIn("share", P._ANALYTICS_FEATURES)
        with _Fleet() as f:
            h = _ui("event=feature_used&feature=share")
            self.assertEqual(h.out[0], 200, h.out)
            ev = f.events("feature_used")
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["feature"], "share")
            self.assertEqual(set(ev[0]), {"feature", "version"})

    def test_documented(self):
        self.assertIn("`share`", DOC)

    def test_more_menu_and_lightbox_offer_share(self):
        pop = HTML[HTML.index('id="playerMorePop"'):]
        pop = pop[:pop.index("</div>")]
        self.assertIn("shareActive()", pop)
        self.assertIn(">", pop)
        self.assertRegex(pop, r"\bShare\b")
        lb = QJS[QJS.index("const actions = document.getElementById('expandActions');"):]
        lb = lb[:lb.index("lb.style.display")]
        self.assertIn("shareActive()", lb)

    def test_share_copies_a_caption_with_the_link_and_selects_the_file(self):
        fn = extract_function("shareClip", QJS)
        self.assertIn("github.com/mrbizarro/phosphene", QJS[QJS.index("SHARE_CAPTION"):][:400])
        self.assertIn("/output/reveal", fn)
        self.assertIn("feature: 'share'", fn)
        # Nothing about the clip leaves the machine: the event carries only
        # the feature word, never the path or the prompt.
        call = re.search(r"_uiEvent\('feature_used',\s*\{[^}]*\}\)", fn).group(0)
        self.assertNotIn("path", call)
        self.assertNotIn("prompt", call)

    def test_caption_is_plain_and_names_no_one(self):
        m = re.search(r"const SHARE_CAPTION\s*=\s*(['`\"])(.*?)\1;", QJS)
        self.assertIsNotNone(m)
        cap = m.group(2)
        self.assertLessEqual(len(cap), 160)
        self.assertIn("Phosphene", cap)
        self.assertIn("Mac", cap)
        self.assertNotIn("#", cap)       # no hashtag spam in someone else's post
        self.assertNotIn("@", cap)


class EnhanceIsCounted(unittest.TestCase):
    """`enhance_prompt` sat in the vocabulary and the UI allowlist since
    v4.9.7 and nothing sent it: 0 uses fleet-wide is "unmeasured". A
    successful /prompt/enhance now counts once, with the mode as detail."""

    def _call(self, result):
        sent = []
        h = _H("prompt=a+knight+in+fog&mode=t2v")

        class _Helper:
            def run(self, msg, timeout=None):
                return result

        orig = (P.HELPER, P._analytics_capture, P.enhance_model_missing)
        P.HELPER = _Helper()
        P._analytics_capture = lambda ev, props=None: sent.append((ev, dict(props or {})))
        # Gemma 3 "on disk" - this checkout may have no mlx_models at all.
        P.enhance_model_missing = lambda: False
        try:
            POST_ROUTES["/prompt/enhance"](h, "/prompt/enhance", {}, "")
        finally:
            P.HELPER, P._analytics_capture, P.enhance_model_missing = orig
        return h, [p for e, p in sent if e == "feature_used"]

    def test_a_successful_enhance_counts_once(self):
        h, ev = self._call({"enhanced": "Cinematic: a knight in fog", "elapsed_sec": 0.1})
        self.assertEqual(h.out[0], 200, h.out)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["feature"], "enhance_prompt")
        self.assertEqual(ev[0].get("detail"), "t2v")
        self.assertNotIn("knight", json.dumps(ev[0]))

    def test_a_failed_enhance_does_not_count(self):
        h, ev = self._call({"enhanced": ""})
        self.assertEqual(h.out[0], 500)
        self.assertEqual(ev, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

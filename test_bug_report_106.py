#!/usr/bin/env python3
"""Issue #106 (4.19.3): the in-app bug reporter.

#106 arrived from the in-app reporter with "What happened" and "Expected"
empty and ONE log line - the maintainer stats fetcher saying it had no GitHub
token. Nothing to act on. Pinned here:

  - a report cannot go out without a short "What happened" (>= 10 chars):
    the modal blocks it with inline copy (never alert()), and the server
    refuses it as a backstop, before building a URL or zipping anything;
  - the box's text lands under "## What happened" in the issue body, and a
    bare "[bug]" title borrows its first line;
  - the auto-attached log keeps earlier ERROR lines ahead of routine
    chatter and never carries stats/maintainer lines.

The modal's JS is executed in node (functions extracted from the real file).
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as p                                             # noqa: E402
from extract_panel_js import extract_function                         # noqa: E402
from panel.routes import GET_ROUTES, POST_ROUTES                      # noqa: E402

NODE = shutil.which("node")
SETTINGS_JS = (ROOT / "webapp" / "js" / "settings.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


class _H:
    def __init__(self, payload: dict | None = None):
        self.out = None
        body = json.dumps(payload or {}).encode()
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)

    def _json(self, obj, code=200):
        self.out = (code, obj)

    def send_error(self, code, *a):
        self.out = (code, None)


def _post(payload: dict):
    h = _H(payload)
    POST_ROUTES["/panel/bug-report"](h, "/panel/bug-report", {}, "application/json")
    return h.out


BODY_OK = ("## Environment\n- Phosphene version: 4.19.3\n\n"
           "## What I was doing\n\n\n"
           "## What happened\n\nThe render stopped at 40% with helper failed.\n\n"
           "## Expected\n\n\n## Logs / repro\n```\n(no recent log lines)\n```\n")


# ---------------------------------------------------------------- server ----
class TestServerBackstop:
    def test_extracts_the_section(self):
        assert p._bug_report_what_happened(BODY_OK) == \
            "The render stopped at 40% with helper failed."
        assert p._bug_report_what_happened("## What happened\n\n\n## Expected\nx") == ""
        assert p._bug_report_what_happened("no headings at all") == ""

    def test_minimum_counts_words_not_padding(self):
        assert not p._bug_report_what_ok("")
        assert not p._bug_report_what_ok("crash")
        assert not p._bug_report_what_ok("  a   b   c  ")       # 5 chars once collapsed
        assert p._bug_report_what_ok("it crashed!")

    @pytest.mark.parametrize("body", [
        "",                                                     # nothing at all
        "b",
        "## Environment\n- x\n\n## What happened\n\n\n## Expected\n\n",   # the #106 shape
        "## What happened\n\ncrash\n\n## Expected\n",
    ])
    def test_empty_report_refused_before_anything_is_built(self, body):
        code, out = _post({"title": "[bug] ", "body": body, "includeCrashReports": True})
        assert code == 400
        assert out["field"] == "what_happened"
        assert "issueUrl" not in out and "zipPath" not in out
        assert "What happened" in out["error"] and "10" in out["error"]

    def test_a_description_that_starts_with_a_heading_is_not_refused(self):
        """Codex 4.19.3: "## Reproduction" in the box read as the NEXT section,
        so a long description was refused as empty, forever."""
        what = "## Reproduction\nGenerate stops at 40 percent."
        body = BODY_OK.replace("The render stopped at 40% with helper failed.", what)
        code, out = _post({"title": "[bug] x", "body": body, "whatHappened": what})
        assert code == 200 and out["ok"], out
        # what the modal actually sends: the heading escaped in the body
        esc = BODY_OK.replace("The render stopped at 40% with helper failed.",
                              "\\## Reproduction\nGenerate stops at 40 percent.")
        code, out = _post({"title": "[bug] x", "body": esc})
        assert code == 200 and out["ok"], out
        # the separate field is judged on its own: short is still refused
        code, out = _post({"title": "[bug] x", "body": BODY_OK, "whatHappened": "crash"})
        assert code == 400 and out["field"] == "what_happened"

    def test_real_report_builds_the_issue_url(self):
        code, out = _post({"title": "[bug] stopped", "body": BODY_OK})
        assert code == 200 and out["ok"]
        q = parse_qs(urlparse(out["issueUrl"]).query)
        assert "The render stopped at 40%" in q["body"][0]


# ----------------------------------------------------------- log excerpt ----
class TestLogExcerpt:
    STATS = ["[07:29:36] stats: no GitHub token resolvable (tried PHOSPHENE_REPO_STATS_TOKEN, "
             "GH_STATS_TOKEN, GH_TOKEN, GITHUB_TOKEN, `gh auth token`). Dashboard will be empty "
             "until you set one.",
             "[07:30:00] [stats] repo stats: rate-limit remaining: 4848/5000",
             "[07:31:00] stats: dashboard refresh skipped (HTTP 403)"]

    def test_stats_lines_never_reach_a_report(self):
        log = ["[07:29:00] Panel ready"] + self.STATS + ["[07:32:00] [h3] stats look fine"]
        out = p._bug_report_log_excerpt(log)
        assert out == ["[07:29:00] Panel ready", "[07:32:00] [h3] stats look fine"]

    def test_issue_106_log_leaves_nothing(self):
        assert p._bug_report_log_excerpt(self.STATS[:1]) == []

    def test_an_earlier_error_survives_a_long_quiet_stretch(self):
        log = [f"[08:00:{i % 60:02d}] queued item {i}" for i in range(100)]
        log += ["[08:10:00] step:generate_and_save mode=t2v 1280x704 121f",
                "[08:10:05] ERROR: helper failed to start: python_normal_exit",
                "[08:10:06] Traceback (most recent call last):"]
        log += [f"[08:20:{i % 60:02d}] poll {i}" for i in range(300)]
        out = p._bug_report_log_excerpt(log)
        assert any("helper failed to start" in x for x in out)
        assert any("step:generate_and_save" in x for x in out)      # line before
        assert any("Traceback" in x for x in out)                   # line after
        assert out[-1] == "[08:20:59] poll 299"                     # the tail is kept
        real = [x for x in out if not x.startswith("[… ")]
        assert len(real) == p.BUG_REPORT_LOG_LINES
        assert any(x.startswith("[… ") and "skipped" in x for x in out)
        # oldest first: the error comes before the tail
        assert out.index(next(x for x in out if "helper failed" in x)) < len(out) - 25

    def test_a_traceback_keeps_the_line_that_names_the_cause(self):
        """Codex 4.19.3: "error" with word boundaries never matched
        "ValueError", so the exception line itself was dropped."""
        log = ["routine"] * 100 + [
            "Traceback (most recent call last):", "frame one", "code one",
            "frame two", "code two",
            "ValueError: invalid literal for int() with base 10: 'abc'"] + ["routine"] * 100
        out = p._bug_report_log_excerpt(log)
        assert any(x.startswith("ValueError: invalid literal") for x in out)
        assert any(x == "code two" for x in out)                     # the line before it
        assert not p._BUG_REPORT_EXC_RE.search("mirror terror horror")

    def test_no_errors_means_the_plain_last_lines(self):
        log = [f"line {i}" for i in range(200)]
        assert p._bug_report_log_excerpt(log) == log[-50:]

    def test_short_log_kept_whole(self):
        log = [f"line {i}" for i in range(10)]
        assert p._bug_report_log_excerpt(log) == log

    def test_bug_context_route_uses_the_excerpt_and_scrubs(self, monkeypatch):
        log = (["[09:00:00] ERROR writing /Users/jane.example/x.mp4: no space"] +
               self.STATS + [f"[09:01:{i % 60:02d}] tick {i}" for i in range(200)])
        monkeypatch.setitem(p.STATE, "log", log)
        h = _H()
        GET_ROUTES["/panel/bug-context"](h, None)
        tail = h.out[1]["logTail"]
        assert not any("GitHub token" in x or "stats:" in x for x in tail)
        assert any("ERROR writing <path>" in x for x in tail)
        assert not any("jane.example" in x for x in tail)


# ------------------------------------------------------------------ modal ----
def _node(script: str):
    if NODE is None:
        pytest.skip("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-3000:]
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


def _modal_js() -> str:
    consts = SETTINGS_JS[SETTINGS_JS.index("const BUG_WHAT_MIN"):
                         SETTINGS_JS.index("function bugWhatText")]
    fns = "\n".join(extract_function(n, SETTINGS_JS) for n in (
        "bugWhatText", "bugWhatOk", "bugWhatShow", "bugWhatInput",
        "bugComposeBody", "bugComposeTitle", "submitBugReport"))
    return consts + "\n" + fns


def _run_submit(what: str, title: str = "[bug] ", server: dict | None = None):
    server = server or {"ok": True, "issueUrl": "https://example.invalid/i"}
    return _node(_modal_js() + """
const body0 = "## Environment\\n- Phosphene version: 4.19.3\\n\\n## What I was doing\\n\\n\\n## Expected\\n\\n\\n## Logs / repro\\n```\\nx\\n```\\n";
const classes = new Set();
const wrap = { classList: { toggle(c, on) { on ? classes.add(c) : classes.delete(c); },
                            contains(c) { return classes.has(c); } } };
let focused = 0;
const els = {
  bugWhat: { value: %s, closest: () => wrap, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; },
             focus() { focused++; } },
  bugWhatHint: { textContent: '' },
  bugTitle: { value: %s }, bugBody: { value: body0 },
  bugStatus: { textContent: '', innerHTML: '' },
  bugSubmitBtn: { disabled: false, textContent: 'Open GitHub issue' },
  bugCrashCheck: { checked: false }, bugCrashRow: { style: { display: 'none' } },
};
global.document = { getElementById: id => els[id] || null };
const calls = [];
global.fetch = async (url, opts) => { calls.push({ url, body: JSON.parse(opts.body) });
  return { ok: %s, json: async () => (%s) }; };
global.window = { open() {} };
global.alert = () => { throw new Error('alert() used'); };
global.setTimeout = () => 0;
global.navigator = { clipboard: { writeText: async () => {} } };
(async () => {
  await submitBugReport();
  console.log(JSON.stringify({ calls, invalid: classes.has('is-invalid'),
    hint: els.bugWhatHint.textContent, aria: els.bugWhat.attrs['aria-invalid'] || null,
    focused, status: els.bugStatus.textContent }));
})();
""" % (json.dumps(what), json.dumps(title), "true" if server.get("ok", True) else "false",
       json.dumps(server)))


class TestModal:
    @pytest.mark.parametrize("what", ["", "   ", "crash", "a  b  c  d"])
    def test_short_or_empty_is_blocked_inline(self, what):
        out = _run_submit(what)
        assert out["calls"] == [], "an empty report was sent"
        assert out["invalid"] and out["aria"] == "true" and out["focused"] >= 1
        assert "at least 10 characters" in out["hint"]

    def test_real_description_goes_under_what_happened(self):
        out = _run_submit("Generate stopped at 40% and the card says helper failed.")
        assert len(out["calls"]) == 1 and not out["invalid"]
        sent = out["calls"][0]["body"]
        assert sent["title"] == "[bug] Generate stopped at 40% and the card says helper failed."
        body = sent["body"]
        assert ("## What happened\n\nGenerate stopped at 40% and the card says helper failed.\n\n"
                "## Expected") in body
        assert body.index("## What I was doing") < body.index("## What happened") \
            < body.index("## Logs")

    def test_line_breaks_kept_and_a_leading_heading_escaped(self):
        what = "## Reproduction\nGenerate stops at 40 percent."
        out = _run_submit(what)
        assert len(out["calls"]) == 1, out
        sent = out["calls"][0]["body"]
        assert sent["whatHappened"] == "\\## Reproduction\nGenerate stops at 40 percent."
        assert "## What happened\n\n\\## Reproduction\nGenerate stops at 40 percent.\n" \
            in sent["body"]
        assert sent["title"] == "[bug] Reproduction"

    def test_a_typed_title_is_kept(self):
        out = _run_submit("It crashed while exporting a 4K clip.", title="[bug] export dies")
        assert out["calls"][0]["body"]["title"] == "[bug] export dies"

    def test_server_backstop_shows_the_same_inline_copy(self):
        out = _run_submit("long enough text here",
                          server={"ok": False, "field": "what_happened", "error": "x"})
        assert out["invalid"] and "at least 10 characters" in out["hint"]

    def test_compose_body_fills_an_existing_heading(self):
        out = _node(_modal_js() + """
console.log(JSON.stringify([
  bugComposeBody("## What happened\\n\\nold\\n\\n## Expected\\n", "new text"),
  bugComposeBody("no headings", "new text"),
  bugComposeTitle("", ""), bugComposeTitle("[bug]", "x".repeat(100)).length <= 80,
]));
""")
        assert out[0].startswith("## What happened\n\nnew text\n")
        assert out[1].endswith("## What happened\n\nnew text\n\n")
        assert out[2] == "[bug] (untitled)" and out[3] is True


def test_modal_markup_and_wiring():
    modal = HTML[HTML.index('id="bugModal"'):HTML.index('id="bottomPane"')]
    assert 'id="bugWhat"' in modal and 'aria-required="true"' in modal
    assert 'oninput="bugWhatInput()"' in modal and 'id="bugWhatHint"' in modal
    assert "last 50 log lines" not in modal
    # The body template no longer carries an empty "What happened" to skip.
    open_fn = extract_function("openBugModal", SETTINGS_JS)
    assert "## What happened" not in open_fn
    # A finished report rewires the button to "Done - close"; reopening must
    # restore the submit handler.
    assert "submitBtn.onclick = submitBugReport;" in open_fn
    assert "bugWhatInput," in SETTINGS_JS[SETTINGS_JS.index("Object.assign(globalThis"):]
    assert "alert(" not in extract_function("submitBugReport", SETTINGS_JS)

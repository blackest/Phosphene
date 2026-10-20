"""4.19.7: an update.js dispatch whose own script is MISSING still stops Update.

Pinokio 8.2 ignores a shell.run exit code and stops a run only when a step's
OUTPUT matches /error:/i or /errno /i. `bash scripts/pinokio/update_fetch.sh`
on a tree without that file exits 127 with a bare "No such file or directory",
which matches neither, so Update went on without a fetch; the same for
must.sh on the three later steps. Each dispatch now ends in
`|| [ -f <script> ] || printf 'FATAL \\145rror: ...'`.

Runs the REAL dispatch strings (read from update.js through node) under bash:
- script missing  -> output carries the stop-word (Pinokio stops);
- script present and failing -> its own FATAL line, never the missing-script one;
- script present and passing -> no stop-word;
- the dispatch text itself never carries the stop-word (the #114 class).
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STOP = re.compile(r"error:|errno ", re.IGNORECASE)


def dispatches():
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not on PATH")
    out = subprocess.run(
        [node, "-e", "const r=require(process.argv[1]).run;"
                     "console.log(JSON.stringify(r.map(s=>s.params&&s.params.message)"
                     ".filter(m=>typeof m==='string')))", str(ROOT / "update.js")],
        capture_output=True, text=True, errors="replace", check=True)
    return [m for m in json.loads(out.stdout) if "scripts/pinokio/" in m]


def run(cmd, cwd):
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True,
                       text=True, errors="replace")
    return p.stdout + p.stderr


class UpdateMissingScript(unittest.TestCase):
    def setUp(self):
        self.msgs = dispatches()
        self.assertEqual(len(self.msgs), 4, self.msgs)
        self.tmp = Path(tempfile.mkdtemp(prefix="upd-missing-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_dispatch_text_never_carries_the_stop_word(self):
        for m in self.msgs:
            self.assertIsNone(STOP.search(m), m)
            self.assertNotIn("\n", m)
            self.assertLessEqual(len(m), 350, m)

    def test_a_missing_script_stops_update(self):
        for m in self.msgs:
            out = run(m, self.tmp)          # nothing under scripts/ at all
            self.assertRegex(out, r"FATAL error: scripts/pinokio/\S+ is missing", m)

    def test_a_failing_script_says_it_once_and_a_passing_one_says_nothing(self):
        d = self.tmp / "scripts" / "pinokio"
        d.mkdir(parents=True)
        shutil.copy(ROOT / "scripts" / "pinokio" / "must.sh", d / "must.sh")
        for name in ("update_fetch.sh", "update_obstruction_guard.sh", "update_converge.sh"):
            (d / name).write_text("echo 'FATAL error: own failure'; exit 3\n")
        (self.tmp / "scripts" / "post_update.sh").write_text("echo own failure; exit 4\n")
        for m in self.msgs:
            out = run(m, self.tmp)
            # the script's own stop-word (must.sh adds its reason line) - and
            # never the "script is missing" line on top of a real failure
            self.assertRegex(out, STOP, (m, out))
            self.assertNotIn("FATAL error: scripts/pinokio/", out)
        for name in ("update_fetch.sh", "update_obstruction_guard.sh", "update_converge.sh"):
            (d / name).write_text("echo fine\n")
        (self.tmp / "scripts" / "post_update.sh").write_text("echo fine\n")
        for m in self.msgs:
            out = run(m, self.tmp)
            self.assertIsNone(STOP.search(out), (m, out))


if __name__ == "__main__":
    unittest.main()

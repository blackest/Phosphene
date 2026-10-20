"""A LoRA name can never run as code in the panel (ov-review E-01).

THE HOLE. Inline handlers were built as
    onclick="renameLora(${JSON.stringify(name).replace(/"/g, '&quot;')})"
which escapes the quote but not the ampersand. The browser HTML-decodes an
attribute before it runs it, so a name that already CONTAINS the text
`&quot;` decodes to a real `"` and closes the JS string early:
    renameLora("...", "");globalThis.PWNED=1;//")
LoRA names and trigger words come from CivitAI listings and downloaded
sidecars - text written by strangers - and the panel origin can drive every
local endpoint, so this was script execution on a plain click of the LoRA row.

THE FIX. Every such argument is `escapeHtml(JSON.stringify(x))`: the whole
JSON literal is HTML-escaped (`&` included), so decoding gives back exactly
the JSON the code wrote.

This runs the real loraRowHtml() in node, decodes each handler the way a
browser does, executes it, and checks the stubs received the exact strings
and nothing else ran.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function                        # noqa: E402

NODE = shutil.which("node")

HOSTILE_NAME = '&quot;);globalThis.PWNED="name";//'
HOSTILE_TRIGGER = '&#39;&quot;);globalThis.PWNED="trigger";//'
HOSTILE_PATH = '/loras/&quot;);globalThis.PWNED="path";//x.safetensors'


class InlineHandlerEscaping(unittest.TestCase):
    @unittest.skipIf(NODE is None, "node is not installed")
    def test_hostile_lora_strings_reach_handlers_verbatim(self):
        js = "\n".join([
            extract_function("escapeHtml"),
            extract_function("_loraRowKebabId"),
            extract_function("loraRowHtml"),
            r"""
const LORA_UPDATES = {};
const calls = [];
const rec = (name) => (...args) => calls.push([name, ...args.map(a => (a && typeof a === 'object') ? '[object]' : a)]);
// Handlers compile in global scope (new Function), like a real onclick.
for (const n of ['renameLora', 'downloadLora', 'deleteLora', 'toggleLora',
                 'appendTriggerToPrompt', 'kebabToggle', 'updateLora',
                 'removeLoraFromActive', 'writeLoraGuide', 'setLoraStrength']) {
  globalThis[n] = rec(n);
}
const _guideBusyAttrs = () => '';
const event = { stopPropagation() {}, preventDefault() {} };
// What the HTML parser does to an attribute value before it is compiled.
function decodeAttr(s) {
  return s.replace(/&(#\d+|#x[0-9a-f]+|quot|amp|lt|gt|apos);/gi, (m, e) => {
    const k = e.toLowerCase();
    if (k === 'quot') return '"';
    if (k === 'amp') return '&';
    if (k === 'lt') return '<';
    if (k === 'gt') return '>';
    if (k === 'apos') return "'";
    if (k.startsWith('#x')) return String.fromCodePoint(parseInt(k.slice(2), 16));
    return String.fromCodePoint(parseInt(k.slice(1), 10));
  });
}
const row = JSON.parse(process.argv[2]);
const html = loraRowHtml(row, 'ltx');
const handlers = [...html.matchAll(/\son(?:click|keydown)="([^"]*)"/g)].map(m => decodeAttr(m[1]));
const errors = [];
for (const h of handlers) {
  try { new Function('event', h)(event); } catch (e) { errors.push(String(e)); }
}
console.log(JSON.stringify({ pwned: globalThis.PWNED || null, calls, errors, n: handlers.length }));
"""])
        row = {"path": HOSTILE_PATH, "name": HOSTILE_NAME, "kind": "user",
               "active": False, "recommended_strength": 1.0,
               "trigger_words": [HOSTILE_TRIGGER], "compatible_modes": ["ltx"]}
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(js)
            path = fh.name
        r = subprocess.run([NODE, path, json.dumps(row)], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIsNone(out["pwned"], "a LoRA string executed as code")
        self.assertEqual(out["errors"], [])
        by_name = {c[0]: c[1:] for c in out["calls"]}
        self.assertEqual(by_name["renameLora"], [HOSTILE_PATH, HOSTILE_NAME])
        self.assertEqual(by_name["deleteLora"], [HOSTILE_PATH, HOSTILE_NAME])
        self.assertEqual(by_name["toggleLora"][0], HOSTILE_PATH)
        self.assertEqual(by_name["toggleLora"][3], HOSTILE_NAME)
        self.assertEqual(by_name["appendTriggerToPrompt"], [HOSTILE_TRIGGER])

    def test_no_quote_only_escape_of_a_json_literal_remains(self):
        """The quote-only spelling is the bug itself, wherever it appears."""
        bad = re.compile(r"""JSON\.stringify\(.*\)\.replace\(/"/g,\s*'&quot;'\)""")
        hits = []
        for f in sorted((ROOT / "webapp" / "js").glob("*.js")):
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if bad.search(line):
                    hits.append(f"{f.name}:{i}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

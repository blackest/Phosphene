"""Load Params (and a queue Edit) keeps the export size the clip was made with.

ov-review E-03. loadParams() restored `upscale` / `upscale_method`, then
selected the engine; setEngine()'s LTX branch gives the quality preset its
default export back (it exists to undo H3 forcing it off), so an Export x2
clip reloaded as Fit 720p and its next render came out at another size. A
queue Edit goes through the same loadParams(), so editing only the prompt of
a queued x2 job changed its export too. Runs the real loadParams() in node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function, panel_source  # noqa: E402

NODE = shutil.which("node")

SHIM = r"""
var _els = {};
function _mk(id, v) {
  return _els[id] = {id, value: v === undefined ? '' : String(v), textContent: '', dataset: {},
    hidden: false, style: {}, innerHTML: '', checked: false, open: false,
    classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    setAttribute(){}, getAttribute(){ return null; }, dispatchEvent(){ return true; },
    addEventListener(){}, focus(){}};
}
var document = {getElementById: id => _els[id] || _mk(id), querySelector: () => null,
                querySelectorAll: () => [], body: {dataset: {}, classList: {toggle(){}}}};
var window = globalThis;
var BOOT = {ltx: {qualities: [{key: 'balanced', pipeline: 'distilled'}]}}, currentMode = 't2v',
    activePath = '/x.mp4', FPS = 24;
_mk('mode', 't2v'); _mk('quality', 'balanced');
var EXPORT = {upscale: null, method: null};
function setUpscale(v) { EXPORT.upscale = v; if (v === 'off') EXPORT.method = 'lanczos'; }
function setUpscaleMethod(v) { EXPORT.method = v; }
function engineById(id) { return {id}; }
// What setEngine()'s LTX branch does to the export (queue.js): the quality
// preset's default comes back.
function setEngine(id) { setUpscale('fit_720p'); return id; }
var MODES = [];
function setMode(m) { MODES.push(m); }
var _ingredientPaths = [];
var SYNCED = [];
function _ingredientSync() { SYNCED.push(_ingredientPaths.slice()); }
var _noop = function () {};
var LOOSE = new Proxy({}, {
  has: (_, k) => k !== Symbol.unscopables && !(k in globalThis) && typeof k === 'string',
  get: (_, k) => _noop,
});
"""


class _LoadParams(unittest.TestCase):
    def _load(self, params: dict) -> dict:
        js = SHIM + "with (LOOSE) {\n" + extract_function("loadParams", panel_source()) + """
globalThis.fetch = async () => ({ok: true, json: async () => (%s)});
loadParams().then(() => console.log(JSON.stringify({export: EXPORT, modes: MODES,
    synced: SYNCED,
    control: document.getElementById('control_video_path').value,
    restore: document.getElementById('restore_video_path').value,
    action: document.getElementById('ingredient_action').value})),
                  e => { console.error(e); process.exit(1); });
}""" % json.dumps({"output": "/x.mp4", "params": params})
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node not on PATH")
class LoadParamsKeepsTheExport(_LoadParams):
    def test_an_x2_clip_reloads_as_x2(self):
        got = self._load({"mode": "t2v", "engine": "ltx", "quality": "balanced",
                          "prompt": "a hen skates", "frames": 121,
                          "upscale": "x2", "upscale_method": "pipersr"})
        self.assertEqual(got["export"], {"upscale": "x2", "method": "pipersr"})


@unittest.skipUnless(NODE, "node not on PATH")
class LoadParamsReopensTheRemixTool(_LoadParams):
    """ov-review E-02: Control / Colorize / Ingredients clips fell through to
    setMode('t2v') and lost their source - the next Generate rendered plain
    text-to-video."""

    def test_control_and_colorize_reopen_with_their_source(self):
        got = self._load({"mode": "control", "engine": "ltx", "quality": "balanced",
                          "prompt": "a robot dances", "control_video_path": "/o/dance.mp4"})
        self.assertEqual(got["modes"][-1], "control")
        self.assertEqual(got["control"], "/o/dance.mp4")
        got = self._load({"mode": "restore", "engine": "ltx", "quality": "balanced",
                          "prompt": "colour", "restore_video_path": "/o/bw.mp4"})
        self.assertEqual(got["modes"][-1], "restore")
        self.assertEqual(got["restore"], "/o/bw.mp4")

    def test_ingredients_reopen_with_their_images(self):
        got = self._load({"mode": "ingredients", "engine": "ltx", "quality": "balanced",
                          "prompt": "sheet", "ingredient_action": "they meet",
                          "ingredient_images_json": '["/u/a.png", "/u/b.png"]'})
        self.assertEqual(got["modes"][-1], "ingredients")
        self.assertEqual(got["synced"][-1], ["/u/a.png", "/u/b.png"])
        self.assertEqual(got["action"], "they meet")


if __name__ == "__main__":
    unittest.main()

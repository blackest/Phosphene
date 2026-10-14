"""Video › Text (LTX): the "High · 720p" chip must render High · 720p.

setQuality() treated any key missing from QUALITY_PRESETS as unknown and fell
back to 'standard'. 'high_720p' (added to the server registry in v4.0.2) never
got a row there, so the chip queued quality=standard at 1280×704 with the
distilled 8 steps. These run the REAL webapp functions in node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as p                                             # noqa: E402
from extract_panel_js import extract_function                         # noqa: E402

NODE = shutil.which("node")
CJS = (ROOT / "webapp" / "js" / "characters.js").read_text(encoding="utf-8")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")


def _presets_literal() -> str:
    m = re.search(r"globalThis\.QUALITY_PRESETS = (\{.*?\n\});", CJS, re.S)
    assert m, "QUALITY_PRESETS literal not found in characters.js"
    return m.group(1)


def _node(script: str):
    if NODE is None:
        pytest.skip("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True,
                             errors="replace", timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _set_quality(key: str, aspect: str = "landscape") -> dict:
    fns = "\n".join([extract_function("setQuality", CJS),
                     extract_function("_aspectDims", QJS)])
    return _node("""
const els = {};
const el = id => (els[id] = els[id] || { id, value: '', style: {}, dataset: {} });
global.document = { getElementById: el, querySelectorAll: () => [],
                    body: { dataset: { engine: 'ltx' } } };
global.ASPECTS = { landscape: { w: 1280, h: 704 }, vertical: { w: 704, h: 1280 },
                   square: { w: 768, h: 768 }, portrait_4_5: { w: 896, h: 1120 } };
globalThis.QUALITY_PRESETS = %s;
global.LAST_STATUS = null;
let upscale = null;
global.setUpscale = v => { upscale = v; };
for (const n of ['applyQuality', 'updateAccelAvailability', 'updateTemporalAvailability',
                 'updateCustomizeSummary', 'renderTierAxes']) global[n] = () => {};
el('aspect').value = %s;
%s
setQuality(%s);
console.log(JSON.stringify({ quality: el('quality').value, width: el('width').value,
                             height: el('height').value, upscale }));
""" % (_presets_literal(), json.dumps(aspect), fns, json.dumps(key)))


def test_the_high_720p_chip_sets_high_720p_not_standard():
    got = _set_quality("high_720p")
    assert got["quality"] == "high_720p"
    assert (got["width"], got["height"]) == (1280, 704)
    assert got["upscale"] == "off"


def test_high_720p_vertical_is_the_same_canvas_turned():
    got = _set_quality("high_720p", "vertical")
    assert (got["quality"], got["width"], got["height"]) == ("high_720p", 704, 1280)


def test_every_ltx_registry_quality_has_a_preset_with_its_canvas():
    """A key the server offers as a chip but the form does not know falls back to
    Standard. Hold the two lists together so the next added tier cannot."""
    presets = _node("console.log(JSON.stringify(%s));" % _presets_literal())
    for key, cell in p._ltx_qualities().items():
        assert key in presets, f"chip {key!r} has no QUALITY_PRESETS row (would render Standard)"
        # the registry may clamp a canvas on small Macs; the unclamped one is the preset
        w = int(cell.get("native_width") or cell.get("width") or 0)
        h = int(cell.get("native_height") or cell.get("height") or 0)
        if key != "quick" and w and h:
            assert (presets[key]["w"], presets[key]["h"]) == (w, h), key


def test_unknown_and_legacy_keys_still_fall_back_to_standard():
    assert _set_quality("draft")["quality"] == "standard"
    assert _set_quality("nonsense")["quality"] == "standard"

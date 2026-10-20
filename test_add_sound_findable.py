#!/usr/bin/env python3
"""W6 (2026-10-19): make Add sound findable.

Add sound (Hailuo H3, `mode=v2a`) shipped in 4.19 and was nearly undiscovered:
3 renders by 2 users in 14 days, against 115 Lip-sync renders by 12. Every
SILENT clip now offers it where the user already looks:

* the server decides silence — no sound track in the mp4's own headers, or a
  sound track that is digital silence (one ffmpeg volumedetect pass on a
  background thread, never on the request) — and the outputs listing carries
  it as `silent` (True / False / None = not known yet), cached per (mtime,
  size) and persisted in STATE_DIR so a clip is decoded once in its life;
* the gallery card of a silent clip carries "No sound · Add sound", and the
  player's meta strip says the same with the Add sound action highlighted —
  only when the installed H3 runner can do it; "needs Hailuo H3" (or "needs a
  Hailuo H3 update") as a plain line when this Mac could run it; nothing when
  it cannot;
* one click lands in Add sound with that clip picked — the same
  useAsAddSoundSource the player's own button calls.

The media here are real mp4s made by ffmpeg; the browser halves run in node.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P                                            # noqa: E402
from extract_panel_js import extract_function                        # noqa: E402

NODE = shutil.which("node")
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


def _ffmpeg() -> str | None:
    for c in (str(P.FFMPEG), shutil.which("ffmpeg")):
        if c and Path(c).is_file():
            return c
    return None


FFMPEG = _ffmpeg()


def _make(path: Path, audio: str | None) -> Path:
    """A 0.5 s 64x36 clip; audio None = no sound track, "silence" = a track
    of digital zeros, "tone" = a 440 Hz sine."""
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x36:d=0.5:r=24"]
    if audio == "silence":
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
    elif audio == "tone":
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5",
                "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd += ["-movflags", "+faststart", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    old = time.time() - 60          # list_outputs skips files written < 2 s ago
    os.utime(path, (old, old))
    return path


@unittest.skipUnless(FFMPEG, "ffmpeg not found")
class _Media(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phos-silent-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "out").mkdir()
        (self.tmp / "state").mkdir()
        for p in (mock.patch.object(P, "OUTPUT", self.tmp / "out"),
                  mock.patch.object(P, "STATE_DIR", self.tmp / "state"),
                  mock.patch.dict(P._SILENCE_CACHE, {}, clear=True),
                  mock.patch.dict(P._SILENCE_STATE, {"loaded": False, "thread": None,
                                                     "dirty": False})):
            p.start()
            self.addCleanup(p.stop)
        self.mute = _make(self.tmp / "out" / "mute.mp4", None)
        self.zero = _make(self.tmp / "out" / "zero.mp4", "silence")
        self.tone = _make(self.tmp / "out" / "tone.mp4", "tone")

    def settle(self, path, timeout=20.0):
        """output_is_silent after the background level check has answered."""
        end = time.time() + timeout
        while time.time() < end:
            v = P.output_is_silent(path)
            if v is not None:
                return v
            time.sleep(0.1)
        return None


class Headers(_Media):
    def test_sound_track_is_read_from_the_header(self):
        self.assertFalse(P._mp4_has_audio_track(self.mute))
        self.assertTrue(P._mp4_has_audio_track(self.zero))
        self.assertTrue(P._mp4_has_audio_track(self.tone))
        junk = self.tmp / "junk.mp4"
        junk.write_bytes(b"\0" * 64)
        self.assertIsNone(P._mp4_has_audio_track(junk))

    def test_dims_still_read_after_the_moov_refactor(self):
        self.assertEqual(P._mp4_display_dims(self.tone), (64, 36))

    def test_digital_silence_vs_sound(self):
        self.assertTrue(P._audio_is_digital_silence(self.zero))
        self.assertFalse(P._audio_is_digital_silence(self.tone))
        self.assertIsNone(P._audio_is_digital_silence(self.mute))   # no stream


class Listing(_Media):
    def test_no_track_is_answered_inline_without_ffmpeg(self):
        with mock.patch.object(P, "_audio_is_digital_silence",
                               side_effect=AssertionError("decoded on the request")):
            self.assertIs(P.output_is_silent(self.mute), True)

    def test_a_track_is_checked_in_the_background(self):
        self.assertIsNone(P.output_is_silent(self.zero))           # never blocks
        self.assertIs(self.settle(self.zero), True)
        self.assertIs(self.settle(self.tone), False)

    def test_answers_are_cached_and_persisted(self):
        self.settle(self.zero)
        self.settle(self.tone)
        P.output_is_silent(self.mute)
        P._silence_save()
        saved = json.loads((self.tmp / "state" / P._SILENCE_CACHE_NAME).read_text())
        self.assertEqual({Path(k).name: v[2] for k, v in saved.items()},
                         {"mute.mp4": True, "zero.mp4": True, "tone.mp4": False})
        # A restart reads the file and decodes nothing, reads no header.
        P._SILENCE_CACHE.clear()
        P._SILENCE_STATE["loaded"] = False
        with mock.patch.object(P, "_mp4_has_audio_track", side_effect=AssertionError("re-read")):
            self.assertIs(P.output_is_silent(self.zero), True)
            self.assertIs(P.output_is_silent(self.tone), False)

    def test_a_changed_file_is_asked_again(self):
        self.assertIs(P.output_is_silent(self.mute), True)
        _make(self.mute, "tone")                                    # same path, new file
        self.assertIs(self.settle(self.mute), False)

    def test_list_outputs_carries_it_for_videos_only(self):
        (self.tmp / "out" / "song.wav").write_bytes(b"RIFF" + b"\0" * 64)
        os.utime(self.tmp / "out" / "song.wav", (time.time() - 60,) * 2)
        self.settle(self.zero)
        self.settle(self.tone)
        rows = {o["name"]: o for o in P.list_outputs(limit=0)}
        self.assertIs(rows["mute.mp4"]["silent"], True)
        self.assertIs(rows["zero.mp4"]["silent"], True)
        self.assertIs(rows["tone.mp4"]["silent"], False)
        self.assertIsNone(rows["song.wav"]["silent"])


@unittest.skipUnless(NODE, "node not found")
class BrowserOffer(unittest.TestCase):
    def run_js(self, body: str):
        fns = "\n".join(extract_function(n, QUEUE_JS)
                        for n in ("addSoundOffer", "syncPlayerSoundOffer"))
        shim = r"""
const window = globalThis;
function outputKind(o) { return o.kind; }
let CAN = true;
function h3CanServe(m) { return CAN && m === 'v2a'; }
function mkEl(id) {
  const el = { id, dataset: {}, innerHTML: '', children: [], className: '',
    _cls: new Set(),
    classList: { toggle(c, on) { on ? el._cls.add(c) : el._cls.delete(c); },
                 contains(c) { return el._cls.has(c); } },
    appendChild(c) { c.parent = el; el.children.push(c); ELS[c.id] = c; return c; },
    remove() { delete ELS[el.id]; } };
  return el;
}
const ELS = { playerOverlayMeta: mkEl('playerOverlayMeta'), addSoundBtn: mkEl('addSoundBtn') };
const document = { getElementById: id => ELS[id] || null, createElement: () => mkEl('') };
const out = [];
"""
        src = shim + fns + "\n" + body + "\nconsole.log(JSON.stringify(out));"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(src)
        try:
            r = subprocess.run([NODE, fh.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(fh.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_offer_states(self):
        got = self.run_js("""
const v = (silent) => ({ kind: 'video', silent });
window._ENGINE_PROBES = { h3: { capable: true, available: true } };
out.push(addSoundOffer(v(true)), addSoundOffer(v(false)), addSoundOffer(v(null)),
         addSoundOffer({ kind: 'image', silent: true }), addSoundOffer(null));
CAN = false;
out.push(addSoundOffer(v(true)));
window._ENGINE_PROBES = { h3: { capable: true, available: false } };
out.push(addSoundOffer(v(true)));
window._ENGINE_PROBES = { h3: { capable: false, available: false } };
out.push(addSoundOffer(v(true)));
""")
        self.assertEqual(got, ["run", "", "", "", "", "update", "needs", ""])

    def test_player_line_and_highlight_follow_the_clip(self):
        got = self.run_js("""
window._ENGINE_PROBES = { h3: { capable: true, available: true } };
syncPlayerSoundOffer({ kind: 'video', silent: true });
const el = ELS.poSoundOffer;
out.push(!!el, el && el.innerHTML.includes('No sound · Add sound'),
         el && el.innerHTML.includes('useAsAddSoundSource()'),
         ELS.addSoundBtn.classList.contains('po-act-suggest'));
syncPlayerSoundOffer({ kind: 'video', silent: false });
out.push(!!ELS.poSoundOffer, ELS.addSoundBtn.classList.contains('po-act-suggest'));
CAN = false; window._ENGINE_PROBES = { h3: { capable: true, available: false } };
syncPlayerSoundOffer({ kind: 'video', silent: true });
out.push(ELS.poSoundOffer.innerHTML.includes('needs Hailuo H3'),
         ELS.poSoundOffer.innerHTML.includes('<button'));
""")
        self.assertEqual(got, [True, True, True, True, False, False, True, False])


class Wiring(unittest.TestCase):
    def test_card_offers_it_on_silent_clips_and_lands_in_add_sound(self):
        self.assertIn("addSoundOffer(o) === 'run'", QUEUE_JS)
        self.assertIn("addSoundFromCard(${pathAttr})", QUEUE_JS)
        body = extract_function("addSoundFromCard", QUEUE_JS)
        self.assertIn("useAsAddSoundSource(path)", body)
        self.assertIn("selectOutput(path)", body)

    def test_poll_refreshes_the_player_line(self):
        self.assertIn("syncPlayerSoundOffer(findOutputByPath(activePath))", QUEUE_JS)

    def test_published_for_inline_handlers(self):
        pub = QUEUE_JS[QUEUE_JS.index("Object.assign(globalThis, {"):]
        for n in ("addSoundOffer", "addSoundFromCard", "syncPlayerSoundOffer"):
            self.assertIn(n, pub)

    def test_styles_live_in_panel_css(self):
        for sel in (".car-card .car-add-sound", ".po-silent-add", ".po-act.po-act-suggest"):
            self.assertIn(sel, CSS)
        # The meta strip ignores the pointer; the button inside must not.
        block = CSS[CSS.index(".po-silent-add {"):]
        self.assertIn("pointer-events: auto", block[:300])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Draft characters — a photo-only character record with no trained LoRA.

create_draft_character() and list_characters(include_drafts=True) exist to
break a chicken-and-egg problem: generate_character_sheet() synthesizes the
missing camera angles a good LoRA dataset needs, but used to require the
character to already resolve through list_characters() — which required a
trained LoRA. These tests cover discovery (draft vs trained, default
exclusion), creation (validation, duplicate refusal), and the route.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

from PIL import Image  # noqa: E402


@contextmanager
def draft_env():
    """A real (unpatched-out) list_characters()/create_draft_character()
    world: temp loras/ and characters/ dirs, nothing pre-populated."""
    with tempfile.TemporaryDirectory() as td_raw:
        td = Path(td_raw)
        loras = td / "loras"
        chars = td / "characters"
        loras.mkdir()
        chars.mkdir()
        saved = (P.LORAS_DIR, P._CHARACTERS_CACHE_PATH)
        P.LORAS_DIR = loras
        P._CHARACTERS_CACHE_PATH = chars
        try:
            yield _NS(td, loras, chars)
        finally:
            P.LORAS_DIR, P._CHARACTERS_CACHE_PATH = saved


class _NS:
    def __init__(self, td, loras, chars):
        self.td, self.loras, self.chars = td, loras, chars


def _make_photo(path: Path, size=(64, 64), color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


class TestDraftDiscovery(unittest.TestCase):
    def test_draft_hidden_by_default(self):
        with draft_env() as env:
            photo = _make_photo(env.td / "src.png")
            P.create_draft_character("newtrn", str(photo))
            self.assertEqual(P.list_characters(), [])
            drafts = P.list_characters(include_drafts=True)
            self.assertEqual([c["id"] for c in drafts], ["newtrn"])

    def test_draft_record_shape(self):
        with draft_env() as env:
            photo = _make_photo(env.td / "src.jpg")
            created = P.create_draft_character(
                "newtrn", str(photo), name="Newt", pronoun="she",
                subject_noun="woman")
            self.assertFalse(created["trained"])
            self.assertIsNone(created["face_lora_path"])
            self.assertFalse(created["has_voice"])
            self.assertIsNone(created["ltx_compatible"])
            self.assertEqual(created["ltx_fusion_tallies"], [])
            self.assertEqual(created["name"], "Newt")
            self.assertEqual(created["pronoun"], "she")
            self.assertEqual(created["subject_noun"], "woman")
            self.assertTrue(Path(created["sample_image_path"]).is_file())
            self.assertEqual(created["sample_image_url"],
                             "/characters/newtrn/preview")

    def test_trained_character_is_not_a_draft(self):
        # A real LoRA file makes list_characters() find it the normal
        # way, with trained=True, regardless of include_drafts.
        with draft_env() as env:
            (env.loras / "realtrn_v2.safetensors").write_bytes(b"\x00")
            for include in (False, True):
                out = P.list_characters(include_drafts=include)
                self.assertEqual([c["id"] for c in out], ["realtrn"])
                self.assertTrue(out[0]["trained"])

    def test_trained_wins_over_a_stale_bundle_with_the_same_trigger(self):
        # If a trigger somehow has both a LoRA and a leftover draft-style
        # bundle.json, it must appear exactly once, as trained.
        with draft_env() as env:
            (env.loras / "bothtrn_v2.safetensors").write_bytes(b"\x00")
            char_dir = env.chars / "bothtrn"
            char_dir.mkdir()
            (char_dir / "bundle.json").write_text("{}", encoding="utf-8")
            out = P.list_characters(include_drafts=True)
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0]["trained"])


class TestCreateDraftCharacter(unittest.TestCase):
    def test_duplicate_trigger_refused(self):
        with draft_env() as env:
            photo = _make_photo(env.td / "src.png")
            P.create_draft_character("duptrn", str(photo))
            with self.assertRaises(P.DraftCharacterExistsError):
                P.create_draft_character("duptrn", str(photo))

    def test_refuses_a_trigger_that_is_already_trained(self):
        with draft_env() as env:
            (env.loras / "takentrn_v2.safetensors").write_bytes(b"\x00")
            photo = _make_photo(env.td / "src.png")
            with self.assertRaises(P.DraftCharacterExistsError):
                P.create_draft_character("takentrn", str(photo))

    def test_missing_source_image_is_file_not_found(self):
        with draft_env() as env:
            with self.assertRaises(FileNotFoundError):
                P.create_draft_character("ghosttrn",
                                         str(env.td / "nope.png"))

    def test_unsupported_extension_is_value_error(self):
        with draft_env() as env:
            bad = env.td / "src.gif"
            bad.write_bytes(b"not really a gif")
            with self.assertRaises(ValueError):
                P.create_draft_character("giftrn", str(bad))

    def test_invalid_trigger_is_value_error(self):
        with draft_env() as env:
            photo = _make_photo(env.td / "src.png")
            with self.assertRaises(ValueError):
                P.create_draft_character("../etc/passwd", str(photo))

    def test_oversized_image_is_value_error(self):
        with draft_env() as env:
            big = env.td / "big.png"
            big.write_bytes(b"\x00" * (P.TRAIN_MAX_BYTES_PER_IMAGE + 1))
            with self.assertRaises(ValueError):
                P.create_draft_character("bigtrn", str(big))


class TestSheetGeneratesFromADraft(unittest.TestCase):
    """The actual point of this feature: a photo with no LoRA can reach
    generate_character_sheet() end to end, exactly like a trained one."""

    def test_draft_reference_resolves_through_the_real_lookup(self):
        with draft_env() as env:
            photo = _make_photo(env.td / "src.png", color=(10, 200, 30))
            P.create_draft_character("bizarrotrn", str(photo))

            def fake_generate(*, prompt, n, aspect, output_dir,
                              base_seed=None, refs=None, config=None,
                              on_log=None):
                png = Path(output_dir) / "cand_00_fake.png"
                png.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (32, 32)).save(png)
                return [{"png_path": str(png), "seed": 1, "engine": "fake",
                         "width": 32, "height": 32}]

            saved = (P._preflight_image_job, P.push,
                     P.agent_image_engine.generate)
            P._preflight_image_job = lambda *a, **k: None
            P.push = lambda line: None
            P.agent_image_engine.generate = fake_generate
            try:
                out = P.generate_character_sheet(
                    "bizarrotrn", engine_override="hidream_inline",
                    views=["front"])
            finally:
                (P._preflight_image_job, P.push,
                 P.agent_image_engine.generate) = saved
            self.assertTrue(out["ok"])
            self.assertEqual([v["key"] for v in out["views"]], ["front"])


class TestCreateDraftRoute(unittest.TestCase):
    """Exercises the real POST /characters dispatch (Handler.do_POST),
    same pattern as test_character_sheet.TestSheetServeRoute — a route
    only a socket can reach is a route that regresses quietly."""

    def _post(self, payload) -> dict:
        body = json.dumps(payload).encode()
        h = P.Handler.__new__(P.Handler)
        h.path = "/characters"
        h.headers = {"Content-Type": "application/json",
                     "Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h._is_local_request = lambda: True
        reply: dict = {}
        h._json = lambda payload, status=200: reply.update(
            payload=payload, status=status)
        h.do_POST()
        return reply

    def test_creates_a_draft_end_to_end(self):
        with draft_env() as env:
            photo = _make_photo(env.td / "src.png")
            reply = self._post({"trigger": "routetrn",
                                "source_image_path": str(photo),
                                "name": "Router"})
            self.assertEqual(reply["status"], 200, reply)
            self.assertTrue(reply["payload"]["ok"])
            char = reply["payload"]["character"]
            self.assertEqual(char["id"], "routetrn")
            self.assertFalse(char["trained"])
            self.assertEqual(char["name"], "Router")

    def test_missing_fields_is_400(self):
        with draft_env():
            reply = self._post({"trigger": "notrig"})
            self.assertEqual(reply["status"], 400, reply)

    def test_duplicate_is_409(self):
        with draft_env() as env:
            photo = _make_photo(env.td / "src.png")
            self._post({"trigger": "duproutetrn",
                        "source_image_path": str(photo)})
            reply = self._post({"trigger": "duproutetrn",
                                "source_image_path": str(photo)})
            self.assertEqual(reply["status"], 409, reply)

    def test_missing_source_image_is_404(self):
        with draft_env() as env:
            reply = self._post({"trigger": "notfoundtrn",
                                "source_image_path": str(env.td / "x.png")})
            self.assertEqual(reply["status"], 404, reply)


if __name__ == "__main__":
    unittest.main()

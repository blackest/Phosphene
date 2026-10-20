#!/usr/bin/env python3
"""H3 prompt guards (h3_prompt_guards.py) — the sentences a Hailuo H3 prompt
gets right before the runner, against failures users reported upstream:

* ellipses inside <d>…</d> hurt intelligibility (ComfyUI H3 prompt guide) —
  they become a comma or a full stop, and that is the only edit ever made to
  a user's dialogue words;
* spoken lines come back SUNG (MiniMax-H3 #68) — "spoken, not sung" when the
  prompt has a line and asks for no singing anywhere;
* rings invented on bare hands (#86) — a bare-hands sentence when hands are
  in the shot and no jewellery/gloves are asked for;
* whatever the dialogue mentions appears on screen (#81) — a "the picture
  shows only what is described here" sentence when the line names things the
  description does not.

Also pinned: the guards run inside run_h3_job_inner on the text the runner
encodes (main prompt and every per-window prompt), never on p["prompt"]; the
job field `h3_prompt_guards` passes make_job's allowlist and "off" turns them
off; Add sound skips the picture guards and Lip-sync the voice guard; and the
storyboard planner's laws tell the LLM the same thing (L14).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sets up an isolated state/output dir and imports the panel; reuse its
# Popen-intercepting harness so the runner argv is read, not guessed.
from test_h3_capabilities import P, _Env  # noqa: E402

import h3_prompt_guards as G  # noqa: E402
import storyboard_planner  # noqa: E402

LINE_PROMPT = (
    "integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium "
    "close-up of a man in a grey wool jacket in an empty hallway. The camera holds a "
    "static shot. With a tired voice (S1) he says: <d>[English] I left my red "
    "umbrella... on the train.</d> Exactly as his voice stops, his mouth settles "
    "closed. No text appears at any point.\n\n"
    "overall_soundscape: The hum of a ventilation duct.\n\n"
    "non_diegetic_music: N/A")
HANDS_PROMPT = (
    "integrated_multimodal_description: [Shot 1] A close-up of two hands on a white "
    "tablecloth, her fingers lacing through his. The camera holds a static shot.\n\n"
    "overall_soundscape: Cutlery clinks.\n\n"
    "non_diegetic_music: N/A")


class Ellipses(unittest.TestCase):
    def fix(self, line):
        return G.fix_dialogue_ellipses(f"<d>[English] {line}</d>")

    def test_mid_line_lowercase_becomes_comma(self):
        self.assertEqual(self.fix("I thought... maybe"), "<d>[English] I thought, maybe</d>")

    def test_before_a_capital_becomes_full_stop(self):
        self.assertEqual(self.fix("Well... I knew"), "<d>[English] Well. I knew</d>")

    def test_trailing_becomes_full_stop(self):
        self.assertEqual(self.fix("Never again..."), "<d>[English] Never again.</d>")

    def test_unicode_and_spaced_forms(self):
        self.assertEqual(self.fix("Hmm… Okay"), "<d>[English] Hmm. Okay</d>")
        self.assertEqual(self.fix("One. . . two"), "<d>[English] One, two</d>")

    def test_leading_and_doubled_punctuation_collapse(self):
        self.assertEqual(self.fix("...and then? Wait...?"), "<d>[English] and then? Wait?</d>")

    def test_words_never_change(self):
        out = self.fix("I left my red umbrella... on the train...")
        self.assertEqual(out.replace(",", "").replace(".", "").split(),
                         "<d>[English] I left my red umbrella on the train</d>".split())

    def test_outside_the_tag_is_untouched(self):
        p = "She pauses... then says: <d>[English] Fine...</d> and waits..."
        self.assertEqual(G.fix_dialogue_ellipses(p),
                         "She pauses... then says: <d>[English] Fine.</d> and waits...")


class WhichGuards(unittest.TestCase):
    def test_dialogue_prompt_gets_spoken_and_nouns(self):
        self.assertEqual(G.guards_wanted(LINE_PROMPT), ["spoken", "nouns"])
        self.assertIn("umbrella", G.dialogue_offscreen_nouns(LINE_PROMPT))

    def test_singing_anywhere_keeps_spoken_out(self):
        p = LINE_PROMPT.replace("he says", "he sings")
        self.assertNotIn("spoken", G.guards_wanted(p))
        p = LINE_PROMPT.replace("non_diegetic_music: N/A",
                                "non_diegetic_music: A slow ballad with a choir.")
        self.assertNotIn("spoken", G.guards_wanted(p))

    def test_instrumental_score_and_bird_song_do_not_count_as_singing(self):
        p = LINE_PROMPT.replace("non_diegetic_music: N/A",
                                "non_diegetic_music: A slow fingerpicked guitar.")
        p = p.replace("ventilation duct.", "ventilation duct, birds singing outside.")
        self.assertIn("spoken", G.guards_wanted(p))

    def test_singing_words_inside_the_line_do_not_count(self):
        p = LINE_PROMPT.replace("I left my red umbrella... on the train.",
                                "I hate this song.")
        self.assertIn("spoken", G.guards_wanted(p))

    def test_no_line_no_voice_guards(self):
        self.assertEqual(G.guards_wanted(HANDS_PROMPT), ["hands"])

    def test_hands_guard_respects_asked_jewellery_and_gloves(self):
        for extra in ("She wears a gold wedding ring.", "He wears leather gloves.",
                      "A silver bracelet on her wrist."):
            p = HANDS_PROMPT.replace("The camera", extra + " The camera")
            self.assertNotIn("hands", G.guards_wanted(p), extra)

    def test_handheld_camera_is_not_a_hand(self):
        p = ("A woman walks down a street at night. The camera is handheld, "
             "a hand-held shot that follows her.")
        self.assertEqual(G.guards_wanted(p), [])

    def test_line_about_what_is_shown_needs_no_nouns_guard(self):
        p = ("A man in a desert. He says: <d>[English] I crossed this desert "
             "twice. Never again.</d>")
        self.assertNotIn("nouns", G.guards_wanted(p))

    def test_modes(self):
        p = LINE_PROMPT.replace("he says", "his hands open and he says")
        self.assertEqual(G.guards_wanted(p, "v2a"), ["spoken"])
        self.assertEqual(G.guards_wanted(p, "a2v"), ["hands", "nouns"])


class Apply(unittest.TestCase):
    def test_sentences_land_at_the_end_of_the_description(self):
        out, applied = G.apply_guards(LINE_PROMPT, enabled=G.GUARD_KEYS)
        self.assertEqual(applied, ["ellipsis", "spoken", "nouns"])
        head, rest = G.split_description(out)
        self.assertTrue(head.endswith(G.NOUNS_SENTENCE), head[-120:])
        self.assertIn(G.SPOKEN_SENTENCE, head)
        # Soundscape and music fields untouched, byte for byte.
        self.assertEqual(rest, G.split_description(LINE_PROMPT)[1])
        self.assertIn("<d>[English] I left my red umbrella, on the train.</d>", out)

    def test_idempotent(self):
        once, _ = G.apply_guards(LINE_PROMPT, enabled=G.GUARD_KEYS)
        twice, applied = G.apply_guards(once, enabled=G.GUARD_KEYS)
        self.assertEqual(once, twice)
        self.assertEqual(applied, [])

    def test_prose_prompt_appends_at_the_end(self):
        out, applied = G.apply_guards("Her hands rest on the table")
        self.assertEqual(applied, ["hands"])
        self.assertEqual(out, "Her hands rest on the table. " + G.HANDS_SENTENCE)

    def test_enabled_limits_and_setting_parser(self):
        out, applied = G.apply_guards(LINE_PROMPT, enabled=("ellipsis",))
        self.assertEqual(applied, ["ellipsis"])
        self.assertNotIn(G.SPOKEN_SENTENCE, out)
        self.assertEqual(G.parse_guard_setting(None), G.DEFAULT_GUARDS)
        self.assertEqual(G.parse_guard_setting(""), G.DEFAULT_GUARDS)
        self.assertEqual(G.parse_guard_setting("off"), ())
        self.assertEqual(G.parse_guard_setting("all"), G.GUARD_KEYS)
        self.assertEqual(G.parse_guard_setting("hands, spoken,bogus"), ("spoken", "hands"))

    def test_empty_prompt(self):
        self.assertEqual(G.apply_guards("   "), ("   ", []))

    def test_every_default_is_a_known_guard(self):
        self.assertTrue(set(G.DEFAULT_GUARDS) <= set(G.GUARD_KEYS))

    def test_defaults_follow_the_ab(self):
        # 2026-10-19 Draft Fast A/B: nouns added a stray prop -> opt-in only.
        self.assertEqual(G.DEFAULT_GUARDS, ("ellipsis", "spoken", "hands"))
        out, applied = G.apply_guards(LINE_PROMPT)
        self.assertEqual(applied, ["ellipsis", "spoken"])
        self.assertNotIn(G.NOUNS_SENTENCE, out)


class RenderPath(_Env):
    def test_runner_gets_the_guarded_prompt_and_params_keep_the_typed_one(self):
        cmd, p = self.dispatch({"prompt": LINE_PROMPT})
        sent = [a for a in cmd if "<d>" in a]
        self.assertEqual(len(sent), 1, sent)
        self.assertIn(G.SPOKEN_SENTENCE, sent[0])
        self.assertIn("umbrella, on the train", sent[0])
        self.assertEqual(p["prompt"], LINE_PROMPT)
        self.assertEqual(p["h3_prompt_guards_applied"], ["ellipsis", "spoken"])
        self.assertTrue(any("prompt guards" in m for m in self.logs))

    def test_all_turns_on_the_opt_in_guard(self):
        cmd, p = self.dispatch({"prompt": LINE_PROMPT, "h3_prompt_guards": "all"})
        self.assertEqual(p["h3_prompt_guards_applied"], ["ellipsis", "spoken", "nouns"])
        self.assertTrue(any(G.NOUNS_SENTENCE in a for a in cmd))

    def test_off_sends_the_prompt_verbatim(self):
        cmd, p = self.dispatch({"prompt": LINE_PROMPT, "h3_prompt_guards": "off"})
        self.assertIn(LINE_PROMPT, cmd)
        self.assertEqual(p["h3_prompt_guards_applied"], [])

    def test_every_window_prompt_is_guarded(self):
        cmd, _ = self.dispatch({"prompt": HANDS_PROMPT, "h3_tier": "draft_10s",
                                "h3_chain_prompts": ["", "she lifts her hand away"],
                                "h3_chain_prompts_complete": False})
        windows = json.loads(Path(self.val(cmd, "--chain-prompts")).read_text())
        self.assertEqual(len(windows), 2)
        for w in windows:
            self.assertEqual(w.count(G.HANDS_SENTENCE), 1, w)

    def test_add_sound_keeps_the_picture_guards_out(self):
        clip = Path(P.OUTPUT) / "silent.mp4"
        clip.write_bytes(b"\0" * 64)
        cmd, p = self.dispatch({"mode": "v2a", "video_path": str(clip),
                                "prompt": HANDS_PROMPT})
        self.assertNotIn("hands", p["h3_prompt_guards_applied"])


class Allowlist(unittest.TestCase):
    def test_make_job_keeps_the_field(self):
        job = P.make_job({"engine": "h3", "prompt": "x", "h3_prompt_guards": "OFF"})
        self.assertEqual(job["params"]["h3_prompt_guards"], "off")
        job = P.make_job({"engine": "h3", "prompt": "x"})
        self.assertEqual(job["params"]["h3_prompt_guards"], "")


class PlannerLaw(unittest.TestCase):
    def test_l14_tells_the_planner(self):
        laws = storyboard_planner._LAWS
        self.assertIn("L14 A LINE PUTS NOTHING ON SCREEN", laws)
        self.assertIn('never "..."', laws)


if __name__ == "__main__":
    unittest.main()

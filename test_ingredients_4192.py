"""Issue #101 (@Techy2023): on LTX-2.3 Ingredients renders "do not contain the
reference images at all".

The lane forced Q4 and FUSED its IC-LoRA; fusing re-quantises W + B@A onto the
int4 grid, and on the real adapter against real Q4 block weights what survives
is mostly quantisation noise (PM hub rel4192/evidence/ing_fuse_retention.json).
With no working adapter the sheet was held at strength 0.0, which noises the
reference tokens away. Now: the adapter rides unfused (exact), the sheet at the
official recipe's 1.0, and stage 2 still refines on the clean transformer.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402
from test_base_pack_gate import _Helper                              # noqa: E402


class TheIngredientsJobSpec(unittest.TestCase):
    def spec(self, env=None) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="ing-"))
        pics = []
        for i in range(3):
            pic = tmp / f"ref{i}.png"
            pic.write_bytes(b"\0")
            pics.append(str(pic))
        job = P.make_job({"mode": "ingredients", "prompt": "a woman, a red bike, a harbour",
                          "ingredient_action": "she rides the bike along the quay",
                          "ingredient_images_json": json.dumps(pics),
                          "quality": "balanced", "frames": "49", "seed": "7"})
        helper = _Helper()
        with mock.patch.object(P, "HELPER", helper), \
                mock.patch.object(P, "OUTPUT", tmp), mock.patch.object(P, "UPLOADS", tmp), \
                mock.patch.object(P, "ltx_generation_serves_ingredients", lambda *a: True), \
                mock.patch.object(P, "_compose_ingredient_sheet", lambda paths, out: out), \
                mock.patch.object(P, "_sheet_to_conditioning_clip", lambda *a, **k: None), \
                mock.patch.object(P, "_ensure_ingredients_lora", lambda: "/w/ing.safetensors"), \
                mock.patch.object(P, "ltx_model_dir", lambda *a, **k: "/w/ltx-2.3-q4"), \
                mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
                mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
                mock.patch.object(P, "write_sidecar", lambda *a, **k: True), \
                mock.patch.dict(os.environ, env or {}, clear=False):
            if env is None:
                os.environ.pop("LTX_INGREDIENTS_REF_STRENGTH", None)
            P.run_job_inner(job)
        self.assertEqual(len(helper.sent), 1)
        return helper.sent[0]["params"]

    def test_the_adapter_rides_unfused_and_the_sheet_is_held(self):
        p = self.spec()
        self.assertEqual(p["lora_mode"], "unfused")
        self.assertEqual(p["video_conditioning"][0][1], 1.0)
        self.assertEqual(p["loras"][-1], {"path": "/w/ing.safetensors", "strength": 1.4})
        self.assertFalse(p["skip_stage_2"])              # Balanced still refines

    def test_the_env_override_still_wins(self):
        p = self.spec({"LTX_INGREDIENTS_REF_STRENGTH": "0.3"})
        self.assertEqual(p["video_conditioning"][0][1], 0.3)


class StageTwoStaysClean(unittest.TestCase):
    """The helper's unfused branch must not carry the IC adapter into the
    stage-2 refine (the reference recipe reloads a clean transformer)."""

    def test_the_reload_drops_pending_adapters_for_its_duration(self):
        src = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
        i = src.index('if action == "generate_restore":')
        block = src[i:i + 12000]
        self.assertIn("_reload_without_adapters", block)
        # behaviour: run the wrapper exactly as the helper builds it
        ns: dict = {}
        start = block.index("if hasattr(type(pipe), \"_reload_clean_transformer\"):")
        end_marker = "del _reload_without_adapters, _orig_reload, _pipe_ref"
        end = block.index(end_marker) + len(end_marker)
        import textwrap
        code = textwrap.dedent(" " * 16 + block[start:end])

        class _Pipe:
            seen = None

            def __init__(self):
                self._pending_loras = [("/w/ing.safetensors", 1.4)]

            def _reload_clean_transformer(self):
                _Pipe.seen = list(self._pending_loras)

        pipe = _Pipe()
        ns["pipe"] = pipe
        exec(code, ns)
        pipe._reload_clean_transformer()
        self.assertEqual(_Pipe.seen, [])
        self.assertEqual(pipe._pending_loras, [("/w/ing.safetensors", 1.4)])
        # ...and the wrapper must not keep a finished pipeline alive (Codex):
        # nothing at the helper's module scope may hold it once `pipe` is gone.
        import gc
        import weakref
        ref = weakref.ref(pipe)
        ns.pop("pipe")
        del pipe
        gc.collect()
        self.assertIsNone(ref(), "the stage-2 wrapper kept the pipeline alive")


if __name__ == "__main__":
    unittest.main()

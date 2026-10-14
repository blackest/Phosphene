"""4.19.4: the engine venv stays on a known-good package set, an install that
already left it is caught before a render and repaired by Update, and the
failures the fleet could not explain now carry their cause.

Fleet since 4.19.3 (2026-10-14): one Mac, 7 i2v renders in a row, every one
"tokenizers>=0.22.0,<=0.23.0 is required for a normal functioning of this
module, but found tokenizers==0.23.3" - filed as bad_params. tokenizers 0.23.3
shipped on 2026-10-09; Update's `uv pip install --upgrade 'litellm>=1.83.14'`
(uv's --upgrade is eager) moved it there, transformers <5.13 refuses it at
import, and every render loads transformers. Re-running Install fixed that Mac.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402

EC = ROOT / "scripts" / "pinokio" / "engine_constraints.txt"
HK_ERROR = ("tokenizers>=0.22.0,<=0.23.0 is required for a normal functioning "
            "of this module, but found tokenizers==0.23.3.")
TABLE = '''deps = {
    "numpy": "numpy>=1.17",
    "tokenizers": "tokenizers>=0.22.0,<=0.23.0",
    "huggingface-hub": "huggingface-hub>=1.5.0,<2.0",
    "safetensors": "safetensors>=0.4.3",
    "tqdm": "tqdm>=4.27",
}
'''


def _constraints() -> list[str]:
    return [l.strip() for l in EC.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


class KnownGoodSet(unittest.TestCase):
    def test_constraints_file_holds_the_transformers_stack(self):
        lines = _constraints()
        self.assertIn("transformers>=5.0.0,<5.13.0", lines)
        tok = [l for l in lines if l.startswith("tokenizers")]
        self.assertEqual(len(tok), 1, lines)
        # transformers <5.13 requires tokenizers <=0.23.0 - 0.23.3 must be out.
        from packaging.requirements import Requirement
        spec = Requirement(tok[0]).specifier
        self.assertFalse(spec.contains("0.23.3"))
        self.assertTrue(spec.contains("0.22.2"))
        self.assertIn("huggingface-hub>=1.5.0,<2.0", lines)
        for pin in ("mlx==0.31.1", "mlx-lm==0.31.1", "mlx-metal==0.31.1"):
            self.assertIn(pin, lines)

    def _installs(self, rel: str, pattern: str) -> list[str]:
        text = (ROOT / rel).read_text().replace("\\\n", " ")
        out = []
        for line in text.splitlines():
            if re.match(r"^\s*(#|//|\*)", line):
                continue
            out += [line] * len(re.findall(pattern, line))
        return out

    def test_every_install_into_the_engine_venv_carries_the_constraints(self):
        ref = re.compile(r'-c\s+("\$EC"|scripts/pinokio/engine_constraints\.txt)'
                         r'|\$\{EC_ARGS\[@\]')
        for rel in ("scripts/post_update.sh", "scripts/pinokio/ltx_engine_env.sh",
                    "scripts/pinokio/mflux_pack.sh",
                    "scripts/pinokio/a2v_stems_deps.sh",
                    "install_qwen.js", "install_sharp.js"):
            text = (ROOT / rel).read_text().replace("\\\n", " ")
            n_install = n_ref = 0
            for line in text.splitlines():
                if re.match(r"^\s*(#|//|\*)", line):
                    continue
                k = len(re.findall(r"\bpip install\b", line))
                n_install += k
                n_ref += min(k, len(ref.findall(line)))
            with self.subTest(rel=rel):
                self.assertGreater(n_install, 0)
                self.assertEqual(n_install, n_ref,
                                 f"{rel}: {n_install} installs, {n_ref} carry -c")

    def test_no_eager_upgrade_of_a_deep_package(self):
        text = (ROOT / "scripts" / "post_update.sh").read_text()
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if "--upgrade" in line:
                self.assertNotRegex(line, r"litellm|smolagents|huggingface-hub|transformers")

    def test_update_import_gate_imports_transformers(self):
        text = (ROOT / "scripts" / "post_update.sh").read_text()
        gate = [l for l in text.splitlines()
                if l.startswith('require "the render engine import gate"')]
        self.assertEqual(len(gate), 1)
        self.assertIn("transformers", gate[0])
        self.assertIn("mlx_lm.tokenizer_utils", gate[0])

    def test_the_js_gate_passes_and_catches_a_bare_install(self):
        r = subprocess.run(["node", str(ROOT / "scripts" / "check_post_update.js")],
                           capture_output=True, text=True, errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        with TemporaryDirectory() as tmp:
            # A copy of the repo's scripts with ONE install stripped of -c.
            t = Path(tmp)
            for rel in ("scripts/check_post_update.js", "scripts/post_update.sh",
                        "scripts/pinokio/ltx_engine_env.sh",
                        "scripts/pinokio/mflux_pack.sh",
                        "scripts/pinokio/a2v_stems_deps.sh",
                        "scripts/pinokio/engine_constraints.txt",
                        "install.js", "install_qwen.js", "install_sharp.js"):
                (t / rel).parent.mkdir(parents=True, exist_ok=True)
                (t / rel).write_text((ROOT / rel).read_text())
            mp = t / "scripts" / "pinokio" / "mflux_pack.sh"
            mp.write_text(mp.read_text().replace(
                "-c scripts/pinokio/engine_constraints.txt 'mlx-teacache", "'mlx-teacache", 1))
            r = subprocess.run(["node", str(t / "scripts" / "check_post_update.js")],
                               capture_output=True, text=True, errors="replace")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("mflux_pack.sh", r.stdout)


class _Venv:
    def __init__(self, tokenizers="0.22.2", table=TABLE):
        self.tokenizers, self.table = tokenizers, table

    def __enter__(self):
        self.tmp = TemporaryDirectory()
        env = Path(self.tmp.name) / "ltx-2-mlx" / "env"
        (env / "bin").mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("home = /usr/bin\n")
        (env / "bin" / "python3.11").write_text("#!/bin/sh\n")
        self.site = site = env / "lib" / "python3.11" / "site-packages"
        (site / "ltx_pipelines_mlx").mkdir(parents=True)
        (site / "transformers").mkdir()
        if self.table is not None:
            (site / "transformers" / "dependency_versions_table.py").write_text(self.table)
        for d in ("transformers-5.7.0", f"tokenizers-{self.tokenizers}",
                  "huggingface_hub-1.14.0", "numpy-2.4.6", "safetensors-0.8.0",
                  "tqdm-4.70.0", "PyYAML-6.0.2"):
            (site / f"{d}.dist-info").mkdir()
        self.prev = P.HELPER_PYTHON
        P.HELPER_PYTHON = env / "bin" / "python3.11"
        return self

    def __exit__(self, *a):
        P.HELPER_PYTHON = self.prev
        self.tmp.cleanup()


class CaughtBeforeARender(unittest.TestCase):
    def test_good_set_is_not_a_fault(self):
        with _Venv("0.22.2") as v:
            self.assertEqual(P.engine_dependency_mismatch(v.site), "")
            self.assertEqual(P.engine_env_fault(), "")

    def test_tokenizers_0_23_3_is_a_fault_with_the_repair(self):
        with _Venv("0.23.3") as v:
            m = P.engine_dependency_mismatch(v.site)
            self.assertIn("tokenizers 0.23.3", m)
            self.assertIn("transformers 5.7.0", m)
            fault = P.engine_env_fault()
            self.assertIn("do not fit together", fault)
            self.assertEqual(P.engine_env_cause(), "mismatch")
            # The worker's sentence: a broken venv, with the way out.
            msg = f"engine venv is not usable: {fault}. {P.ENGINE_ENV_REPAIR}"
            self.assertEqual(P._analytics_error_class(msg), "venv_broken")
            self.assertIn("Repair engine", msg)

    def test_a_stale_duplicate_dist_info_fails_open(self):
        # Codex 4.19.4: 0.22.2 AND a leftover 0.23.3 folder - which one
        # importlib picks is not ours to guess; never block on it.
        with _Venv("0.22.2") as v:
            (v.site / "tokenizers-0.23.3.dist-info").mkdir()
            self.assertEqual(P.engine_dependency_mismatch(v.site), "")

    def test_unreadable_table_fails_open(self):
        with _Venv("0.23.3", table="deps = not a dict(") as v:
            self.assertEqual(P.engine_dependency_mismatch(v.site), "")
        with _Venv("0.23.3", table=None) as v:
            self.assertEqual(P.engine_dependency_mismatch(v.site), "")

    def test_helper_refuses_to_spawn_on_a_mismatch(self):
        with _Venv("0.23.3"):
            h = P.WarmHelper()
            with self.assertRaises(RuntimeError) as cm:
                h._ensure()
            self.assertIn("do not fit together", str(cm.exception))
            self.assertIsNone(h.proc)

    def test_the_fleet_error_is_a_broken_venv_not_bad_params(self):
        self.assertEqual(P._analytics_error_class(HK_ERROR), "venv_broken")

    def test_mismatch_is_a_documented_cause(self):
        self.assertIn("mismatch", P.ENGINE_ENV_CAUSES)
        self.assertIn("`mismatch`", (ROOT / "docs" / "ANALYTICS.md").read_text())


if __name__ == "__main__":
    unittest.main()

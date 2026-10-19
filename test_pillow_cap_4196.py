"""4.19.6: Pillow 13 broke FLUX.2 and Qwen-Edit on every install made after
2026-10-16.

Pillow 13.0.0 (released 2026-10-16) rewrote PIL/_typing.py to import
`from numbers import _IntegralLike` at module level, relying on Python 3.15's
lazy imports (`__lazy_modules__`) never to execute it. The engine venv is
Python 3.11: the import runs and raises. mflux 0.18.0 imports PIL._typing
(mflux/utils/image_util.py), so every mflux image render died at start with

    ImportError: cannot import name '_IntegralLike' from 'numbers'

Fleet 2026-10-17..19: 9 installs, every one a fresh 4.19.5 install made after
the Pillow release; video unaffected. Nothing pinned Pillow: mflux asks for
pillow>=12.1.1 and engine_constraints.txt did not mention it.

These tests pin the three halves of the fix:
  * engine_constraints.txt caps Pillow below 13 (every engine-venv install
    passes it with -c, so new installs resolve 12.x);
  * post_update.sh asks for the range, which moves an installed 13.x back on
    Update (proved with real uv against a real Pillow 13 wheel when the local
    uv cache has both wheels);
  * an image job on a venv that still has Pillow 13 stops before the mflux
    subprocess with a sentence that names the cause and the way out, filed
    under venv_broken.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import image_engine as IE  # noqa: E402

EC = ROOT / "scripts" / "pinokio" / "engine_constraints.txt"
POST_UPDATE = ROOT / "scripts" / "post_update.sh"


def _pillow_spec() -> str:
    return IE._engine_constraint_specs().get("pillow", "")


class TheConstraintCapsPillow(unittest.TestCase):
    def test_engine_constraints_hold_pillow_below_13(self):
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
        spec = _pillow_spec()
        self.assertTrue(spec, "engine_constraints.txt has no pillow line")
        s = SpecifierSet(spec)
        self.assertFalse(s.contains(Version("13.0.0")), spec)
        self.assertFalse(s.contains(Version("13.1.0")), spec)
        # mflux 0.18.0's own floor, and what installs have today.
        for ok in ("12.1.1", "12.2.0", "12.3.0"):
            self.assertTrue(s.contains(Version(ok)), (ok, spec))
        self.assertFalse(s.contains(Version("12.0.0")), "below mflux's pillow>=12.1.1")


class UpdateMovesPillowBack(unittest.TestCase):
    def setUp(self):
        self.src = POST_UPDATE.read_text(encoding="utf-8")

    def _line(self, needle: str) -> int:
        for n, line in enumerate(self.src.splitlines()):
            if needle in line and not line.lstrip().startswith("#"):
                return n
        self.fail(f"post_update.sh has no live line with {needle!r}")

    def test_post_update_asks_for_the_range_under_the_constraints(self):
        n = self._line("'pillow>=12.1.1,<13'")
        line = self.src.splitlines()[n]
        self.assertIn('-c "$EC"', line)
        self.assertIn('--python "$VENV_PY"', line)
        self.assertNotIn("--upgrade", line)          # eager upgrades move the world

    def test_it_runs_before_the_vendored_reinstall_and_the_mflux_pack(self):
        pil = self._line("'pillow>=12.1.1,<13'")
        self.assertLess(pil, self._line("re-installing the three vendored packages"))
        self.assertLess(pil, self._line("'mflux==0.18.0'"))

    def test_its_output_and_warning_cannot_stop_a_pinokio_run(self):
        # Pinokio 8.2.0 ends a run on any output line containing "error:" - a
        # uv network failure prints one (Codex 4.19.6). The step is best-effort.
        n = self._line("'pillow>=12.1.1,<13'")
        block = self.src.splitlines()[n - 1:n + 4]
        text = "\n".join(block)
        self.assertIn("set -o pipefail", block[0])
        self.assertIn("s/[Ee][Rr][Rr][Oo][Rr]:/problem -/g", text)
        warn = next(l for l in block if "WARN" in l)
        self.assertNotIn("error:", warn.lower())


def _fake_tree(root: Path, pillow: str | None, ec: bool = True) -> Path:
    """<root>/ltx-2-mlx/env with a pyvenv.cfg, a pillow dist-info and an
    mflux binary; <root>/scripts/pinokio/engine_constraints.txt copied."""
    env = root / "ltx-2-mlx" / "env"
    site = env / "lib" / "python3.11" / "site-packages"
    site.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = /x\nversion = 3.11.15\n")
    if pillow:
        (site / f"pillow-{pillow}.dist-info").mkdir()
    (site / "mflux-0.18.0.dist-info").mkdir()
    bindir = env / "bin"
    bindir.mkdir()
    b = bindir / "mflux-generate-flux2"
    b.write_text("#!/bin/sh\nexit 0\n")
    b.chmod(0o755)
    if ec:
        (root / "scripts" / "pinokio").mkdir(parents=True)
        shutil.copy(EC, root / "scripts" / "pinokio" / "engine_constraints.txt")
    return b


class AnImageJobOnPillow13StopsWithTheWayOut(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pil13-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _fault(self, pillow, ec=True, root=None):
        b = _fake_tree(self.tmp, pillow, ec=ec)
        with mock.patch.object(IE, "_REPO_ROOT", (root or self.tmp).resolve()):
            return IE.mflux_env_fault(str(b))

    def test_pillow_13_is_a_fault_that_names_cause_and_remedy(self):
        msg = self._fault("13.0.0")
        self.assertIn("Pillow 13.0.0", msg)
        self.assertIn("_IntegralLike", msg)
        self.assertIn("Press Update in Pinokio, then restart", msg)
        self.assertIn("Video renders are not affected", msg)

    def test_pillow_12_is_healthy(self):
        self.assertEqual(self._fault("12.3.0"), "")
        shutil.rmtree(self.tmp)
        self.tmp.mkdir()
        self.assertEqual(self._fault("12.2.0"), "")

    def test_fails_open(self):
        self.assertEqual(self._fault(None), "")                 # no pillow at all
        shutil.rmtree(self.tmp)
        self.tmp.mkdir()
        self.assertEqual(self._fault("13.0.0", ec=False), "")   # no constraints file

    def test_a_venv_outside_the_app_is_not_ours_to_judge(self):
        other = Path(tempfile.mkdtemp(prefix="elsewhere-"))
        self.addCleanup(shutil.rmtree, other, True)
        self.assertEqual(self._fault("13.0.0", root=other), "")

    def test_the_fleet_files_it_under_venv_broken(self):
        msg = self._fault("13.0.0")
        os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="pil13-state-"))
        os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
        os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
        import mlx_ltx_panel as P
        self.assertEqual(P._analytics_error_class(msg), "venv_broken")

    def test_generate_stops_before_the_subprocess(self):
        b = _fake_tree(self.tmp, "13.0.0")
        cfg = IE.ImageEngineConfig(kind="mflux", mflux_model="flux2-klein-4b")
        with mock.patch.object(IE, "_REPO_ROOT", self.tmp.resolve()), \
             mock.patch.object(IE, "_resolve_mflux_bin", lambda c: str(b)), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("spawned mflux")):
            with self.assertRaises(RuntimeError) as cm:
                IE._generate_mflux("a cat", 1, 512, 512, self.tmp / "out", 1, cfg)
        self.assertIn("Pillow 13.0.0", str(cm.exception))


def _uv() -> str | None:
    for c in (shutil.which("uv"), str(Path.home() / "pinokio/bin/miniforge/bin/uv")):
        if c and Path(c).is_file():
            return c
    return None


@unittest.skipUnless(sys.platform == "darwin", "macOS wheels")
class RealUvMovesAnInstalledPillow13Back(unittest.TestCase):
    """The repair, with real uv and real wheels, offline. Skips (does not
    fail) when the local uv cache lacks Pillow 13.0.0 or a 12.x wheel."""

    def test_constrained_install_moves_13_back_and_PIL_typing_imports(self):
        uv = _uv()
        if not uv:
            self.skipTest("no uv")
        tmp = Path(tempfile.mkdtemp(prefix="pil13-uv-"))
        self.addCleanup(shutil.rmtree, tmp, True)

        def run(*a):
            return subprocess.run([uv, *a], cwd=tmp, capture_output=True, text=True,
                                  errors="replace", timeout=300)

        r = run("venv", "-q", "--offline", "-p", "3.11", "v")
        if r.returncode != 0:
            self.skipTest(f"no Python 3.11 for uv offline: {r.stderr[-200:]}")
        py = str(tmp / "v" / "bin" / "python")
        r = run("pip", "install", "--offline", "--python", py, "pillow==13.0.0")
        if r.returncode != 0:
            self.skipTest("Pillow 13.0.0 not in the local uv cache")
        probe = [py, "-I", "-c", "from PIL._typing import StrOrBytesPath"]
        before = subprocess.run(probe, cwd=tmp, capture_output=True, text=True, errors="replace")
        self.assertNotEqual(before.returncode, 0)
        self.assertIn("_IntegralLike", before.stderr)          # the fleet's failure, reproduced
        r = run("pip", "install", "--offline", "--python", py, "-c", str(EC), "pillow>=12.1.1,<13")
        if r.returncode != 0 and ("offline" in (r.stderr + r.stdout).lower()
                                  or "network was disabled" in (r.stderr + r.stdout).lower()):
            self.skipTest("no Pillow 12.x wheel in the local uv cache")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = subprocess.run(probe, cwd=tmp, capture_output=True, text=True, errors="replace")
        self.assertEqual(after.returncode, 0, after.stderr)
        ver = subprocess.run([py, "-I", "-c", "import PIL; print(PIL.__version__)"],
                             capture_output=True, text=True, errors="replace").stdout.strip()
        self.assertTrue(ver.startswith("12."), ver)


if __name__ == "__main__":
    unittest.main()

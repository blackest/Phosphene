#!/usr/bin/env python3
"""4.20: Start after the renderer, not after everything.

WHY (fleet, overnight 2026-10-19): about 1 in 5 Pinokio install starts reaches
a first panel boot. install.js downloaded ~38 GB before Start appeared - an
hour on a fast line - with no disk check, no size and no time. Now:

  * required_files.json -> install.before_start (the LTX-2.5 engine, its text
    encoder, the 22 MB preview decoder: 27.5 GB) is all install.js fetches;
  * install.after_start (Gemma 3 for Enhance / the Storyboard planner, and four
    IC-LoRAs: 10.7 GB) is queued in state/install_deferred.json and the PANEL
    downloads it in the background after the first boot;
  * a disk preflight with plain numbers runs before anything big;
  * a feature whose pack has not landed answers 409 "downloading / Download
    now" instead of failing;
  * no marker - every UPDATED install - means no background download at all.

No network, no GPU, no real weights: weight files are sparse (truncate), the
downloader is a stand-in that writes the declared files.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-install-deferred-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import install_plan  # noqa: E402
import mlx_ltx_panel as P  # noqa: E402
from panel.routes import POST_ROUTES, GET_ROUTES  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

REG = json.loads((ROOT / "required_files.json").read_text(encoding="utf-8"))
BY_KEY = {r["key"]: r for r in REG["repos"]}
NODE = shutil.which("node")
INSTALL_JS = (ROOT / "install.js").read_text(encoding="utf-8")
WEIGHTS_SH = (ROOT / "scripts" / "pinokio" / "ltx25_weights.sh").read_text(encoding="utf-8")
PJS = (ROOT / "webapp" / "js" / "preview.js").read_text(encoding="utf-8")


def _sparse(root: Path, repo: dict, missing: tuple = ()) -> None:
    """Every declared file of `repo` at its full size, without using disk."""
    sizes = repo.get("file_sizes") or {}
    files = repo.get("files") or []
    big = max(2048, int(install_plan.expected_bytes(repo) / max(1, len(files))))
    for name in files:
        if name in missing:
            continue
        f = root / repo["local_dir"] / name
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "wb") as fh:
            fh.truncate(int(sizes.get(name) or big))


def _install_steps() -> list:
    out = subprocess.run(
        [NODE, "-e", "const m=require(process.argv[1]);console.log(JSON.stringify(m.run.map(s=>"
                     "({method:s.method,when:s.when||'',message:[].concat((s.params||{}).message||[]).join('\\n'),"
                     "html:(s.params||{}).html||''}))))", str(ROOT / "install.js")],
        capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ---------------------------------------------------------------------------
class RegistrySplit(unittest.TestCase):
    def setUp(self):
        self.blk = install_plan.install_block(REG)

    def test_before_start_is_what_a_first_render_needs_and_nothing_else(self):
        render = REG["capabilities"]["render"]
        renders = set(render["repos_by_version"][render["default_version"]])
        self.assertEqual(set(self.blk["before_start"]), renders | {"tae"})

    def test_after_start_is_everything_else_a_fresh_install_used_to_fetch(self):
        self.assertEqual(self.blk["after_start"],
                         ["gemma", "ic_upscale_x2", "ic_union_control", "ic_ingredients", "ic_colorize"])
        self.assertFalse(set(self.blk["before_start"]) & set(self.blk["after_start"]))
        for k in self.blk["before_start"] + self.blk["after_start"]:
            self.assertIn(k, BY_KEY)

    def test_nothing_deferred_can_block_a_render(self):
        # On the generation a fresh install boots into. (LTX-2.3, which a user
        # can pin back to, renders on Gemma 3 - and a fresh install never
        # fetched 2.3's own pack either, before or after 4.20.)
        for name, cap in REG["capabilities"].items():
            if name.startswith("_") or not cap.get("blocking"):
                continue
            keys = set(cap.get("repos") or [])
            by_v = cap.get("repos_by_version") or {}
            keys |= set(by_v.get(cap.get("default_version"), []))
            self.assertFalse(keys & set(self.blk["after_start"]), name)

    def test_gemma3_size_is_the_measured_one(self):
        # 6 understated it by 2 GB, and every size line inherited that.
        self.assertGreaterEqual(BY_KEY["gemma"]["size_gb"], 8.0)


# ---------------------------------------------------------------------------
@unittest.skipUnless(NODE, "node not on PATH")
class InstallScriptOrder(unittest.TestCase):
    def setUp(self):
        self.steps = _install_steps()
        self.msgs = [s["message"] for s in self.steps]

    def _idx(self, needle: str) -> int:
        hits = [i for i, m in enumerate(self.msgs) if needle in m]
        self.assertTrue(hits, f"no step runs {needle!r}")
        return hits[0]

    def test_disk_preflight_runs_after_fs_link_and_before_anything_big(self):
        pre = self._idx("install_preflight.sh")
        link = [i for i, s in enumerate(self.steps) if s["method"] == "fs.link"][0]
        self.assertLess(link, pre)
        for big in ("git clone", "ltx_venv.sh", "ltx_engine_env.sh", "ltx25_weights.sh", "mflux_pack.sh"):
            self.assertLess(pre, self._idx(big), big)

    def test_install_downloads_nothing_from_after_start(self):
        joined = "\n".join(self.msgs)
        self.assertNotIn("hf download", joined)
        for k in install_plan.install_block(REG)["after_start"]:
            repo = BY_KEY[k]
            self.assertNotIn(repo["repo_id"], joined, k)
            self.assertNotRegex(WEIGHTS_SH, rf"--repo-key\s+{k}\b", k)

    def test_the_weights_script_fetches_exactly_before_start(self):
        keys = re.findall(r"--repo-key\s+(\w+)", WEIGHTS_SH)
        self.assertEqual(set(keys), set(install_plan.install_block(REG)["before_start"]))

    def test_the_marker_is_written_after_every_venv_step_and_last_is_ready(self):
        defer = self._idx("install_plan.py defer")
        for before in ("ltx25_weights.sh", "mflux_pack.sh", "a2v_stems_deps.sh"):
            self.assertLess(self._idx(before), defer, before)
        self.assertEqual(self.steps[-1]["method"], "notify")
        self.assertIn("Phosphene is ready", self.steps[-1]["html"])
        self.assertIn("|| echo", self.msgs[defer])   # never fails the install

    def test_the_size_line_is_the_plan_s_own_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = install_plan.plan(Path(tmp), REG)
        first = next(s["html"] for s in self.steps if s["method"] == "notify" and not s["when"])
        for txt in (install_plan.fmt_gb(p["before_total"]),
                    install_plan.fmt_gb(p["after_total"]),
                    install_plan.fmt_gb(p["need_start"])):
            self.assertIn(txt, first)
        for mbps, label in ((100, "100 Mbps"), (300, "300 Mbps"), (1000, "1 Gbps")):
            m = install_plan.fmt_minutes(install_plan.minutes_at(p["before_total"], mbps))
            self.assertIn(f"{m} at {label}", first.replace("<b>", "").replace("</b>", ""))

    def test_no_dispatch_over_the_safe_width(self):
        # skill gotcha 5: <= 350 is the bisected-safe width; the gate allows 500.
        for m in self.msgs:
            self.assertLessEqual(len(m), 350, m[:80])


# ---------------------------------------------------------------------------
class Preflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy(ROOT / "required_files.json", self.root / "required_files.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _plan(self, free):
        p = install_plan.plan(self.root, REG)
        p["free"] = free
        return p

    def test_short_disk_stops_the_install_with_both_numbers(self):
        p = self._plan(10 * install_plan.GB)
        code, lines = install_plan.preflight_report(p)
        text = "\n".join(lines)
        self.assertEqual(code, 1)
        self.assertIn("NOT ENOUGH DISK SPACE", text)
        self.assertIn(f"needs about {install_plan.fmt_gb(p['need_start'])} free", text)
        self.assertIn("has 10.0 GB free", text)
        self.assertIn(f"Free up at least {install_plan.fmt_gb(p['need_start'] - 10 * install_plan.GB)}", text)

    def test_enough_room_passes_and_says_size_and_time(self):
        code, lines = install_plan.preflight_report(self._plan(500 * install_plan.GB))
        text = "\n".join(lines)
        self.assertEqual(code, 0)
        self.assertIn("at 100 Mbps", text)
        self.assertIn("in the background", text)

    def test_a_resumed_install_needs_room_only_for_what_is_left(self):
        full = install_plan.plan(self.root, REG)
        _sparse(self.root, BY_KEY["q4_25"])
        (self.root / "ltx-2-mlx" / "env").mkdir(parents=True)
        (self.root / "ltx-2-mlx" / "env" / "pyvenv.cfg").write_text("x")
        resumed = install_plan.plan(self.root, REG)
        self.assertEqual(full["need_start"] - resumed["need_start"],
                         install_plan.expected_bytes(BY_KEY["q4_25"]) + full["env_bytes"])

    def test_a_half_fetched_mirror_file_counts_as_fetched(self):
        repo = BY_KEY["q4_25"]
        before = install_plan.remaining_bytes(self.root, repo)
        part = self.root / repo["local_dir"] / "transformer-distilled.safetensors.partial"
        part.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "wb") as fh:
            fh.truncate(5 * install_plan.GB)
        self.assertEqual(before - install_plan.remaining_bytes(self.root, repo), 5 * install_plan.GB)

    def test_unknown_free_space_is_fail_open(self):
        code, _ = install_plan.preflight_report(self._plan(None))
        self.assertEqual(code, 0)

    def _shell(self, fake_rc: int | None) -> int:
        app = self.root
        shutil.copytree(ROOT / "scripts" / "pinokio", app / "scripts" / "pinokio", dirs_exist_ok=True)
        bindir = app / "bin"
        bindir.mkdir(exist_ok=True)
        if fake_rc is not None:
            (bindir / "python3").write_text(f"#!/bin/sh\necho fake-plan\nexit {fake_rc}\n")
            (bindir / "python3").chmod(0o755)
        env = {"PATH": f"{bindir}:/bin:/usr/sbin:/sbin", "HOME": str(app)}
        if fake_rc is None:
            env["PATH"] = f"{bindir}:/bin"   # no python anywhere
        r = subprocess.run(["/bin/bash", "scripts/pinokio/install_preflight.sh"], cwd=app, env=env,
                           capture_output=True, text=True, timeout=30)
        return r.returncode

    # Pinokio 8.2 ignores a shell.run step's exit code. A step stops install.js
    # only when its OUTPUT matches one of the kernel's default break handlers
    # (pinokiod kernel/shells.js): /error:/i or /errno /i, with /error:.*triton/i
    # exempt. An `exit 1` that prints neither marches on.
    BREAK = re.compile(r"error:|errno ", re.I)

    def _breaks(self, text: str) -> bool:
        return bool(self.BREAK.search(re.sub(r"error:.*triton", "", text.replace("\n", ""), flags=re.I)))

    def test_a_shortfall_stops_pinokio_and_a_pass_does_not(self):
        _, short = install_plan.preflight_report(self._plan(10 * install_plan.GB))
        _, fine = install_plan.preflight_report(self._plan(500 * install_plan.GB))
        _, unknown = install_plan.preflight_report(self._plan(None))
        self.assertTrue(self._breaks("\n".join(short)))
        self.assertFalse(self._breaks("\n".join(fine)))
        self.assertFalse(self._breaks("\n".join(unknown)))

    def test_a_failed_weights_fetch_stops_pinokio(self):
        app = self.root
        (app / "scripts").mkdir(exist_ok=True)
        shutil.copytree(ROOT / "scripts" / "pinokio", app / "scripts" / "pinokio", dirs_exist_ok=True)
        py = app / "ltx-2-mlx" / "env" / "bin" / "python3.11"
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\necho 'urlopen failed'\nexit 1\n")
        py.chmod(0o755)
        r = subprocess.run(["/bin/bash", "scripts/pinokio/ltx25_weights.sh"], cwd=app,
                           capture_output=True, text=True, timeout=30)
        self.assertTrue(self._breaks(r.stdout + r.stderr), r.stdout)
        py.write_text("#!/bin/sh\nexit 0\n")
        r = subprocess.run(["/bin/bash", "scripts/pinokio/ltx25_weights.sh"], cwd=app,
                           capture_output=True, text=True, timeout=30)
        self.assertFalse(self._breaks(r.stdout + r.stderr), r.stdout)

    def test_wrapper_stops_only_on_a_known_shortfall(self):
        self.assertEqual(self._shell(1), 1)      # the plan said: not enough room
        self.assertEqual(self._shell(2), 0)      # the plan crashed: carry on
        self.assertEqual(self._shell(None), 0)   # no Python yet: carry on


# ---------------------------------------------------------------------------
class Marker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy(ROOT / "required_files.json", self.root / "required_files.json")
        self.sdir = self.root / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defer_queues_only_missing_after_start_packs_in_registry_order(self):
        _sparse(self.root, BY_KEY["ic_colorize"])
        pending = install_plan.defer(self.root, self.sdir)
        self.assertEqual(pending, ["gemma", "ic_upscale_x2", "ic_union_control", "ic_ingredients"])
        m = install_plan.read_marker(self.sdir)
        self.assertEqual(m["pending"], pending)
        self.assertEqual(m["created_by"], "install.js")

    def test_a_second_install_keeps_what_the_first_queued(self):
        install_plan.write_marker(self.sdir, {"pending": ["ic_colorize"]})
        for k in ("gemma", "ic_upscale_x2", "ic_union_control", "ic_ingredients", "ic_colorize"):
            _sparse(self.root, BY_KEY[k])
        (self.root / BY_KEY["ic_colorize"]["local_dir"] / BY_KEY["ic_colorize"]["files"][0]).unlink()
        self.assertEqual(install_plan.defer(self.root, self.sdir), ["ic_colorize"])

    def test_nothing_missing_means_no_marker(self):
        install_plan.write_marker(self.sdir, {"pending": ["gemma"]})
        for k in install_plan.install_block(REG)["after_start"]:
            _sparse(self.root, BY_KEY[k])
        self.assertEqual(install_plan.defer(self.root, self.sdir), [])
        self.assertFalse((self.sdir / install_plan.MARKER_NAME).exists())

    def test_defer_never_queues_a_before_start_pack(self):
        pending = install_plan.defer(self.root, self.sdir)
        self.assertFalse(set(pending) & set(install_plan.install_block(REG)["before_start"]))


# ---------------------------------------------------------------------------
class _PanelSandbox:
    """The panel module pointed at a throwaway app root + state dir, with the
    downloader swapped for a stand-in that writes the pack's files (or not)."""

    def __init__(self, free_gb: float = 500.0, outcome: str = "ok", land: bool = True):
        self.free, self.outcome, self.land = free_gb, outcome, land
        self.fetched: list[str] = []

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sdir = self.root / "state"
        self.sdir.mkdir()
        self._orig = (P.ROOT, P.STATE_DIR, P._download_thread, P.install_plan.free_bytes,
                      P.HF_BIN, dict(P.DEFERRED), dict(P.DOWNLOAD))
        P.ROOT, P.STATE_DIR = self.root, self.sdir
        P.HF_BIN = Path("/usr/bin/true")
        P.install_plan.free_bytes = lambda root: int(self.free * install_plan.GB)
        P.DEFERRED.update({"state": "idle", "key": None, "message": "", "failures": {},
                           "retry_at": {}, "finished": []})

        def fake(repo):
            self.fetched.append(repo["key"])
            if self.land:
                _sparse(self.root, repo)
            with P.DOWNLOAD_LOCK:
                P.DOWNLOAD.update({"active": False, "key": None, "background": False})
            return self.outcome
        P._download_thread = fake
        return self

    def __exit__(self, *a):
        (P.ROOT, P.STATE_DIR, P._download_thread, P.install_plan.free_bytes,
         P.HF_BIN, deferred, download) = self._orig
        P.DEFERRED.clear(); P.DEFERRED.update(deferred)
        P.DOWNLOAD.clear(); P.DOWNLOAD.update(download)
        self.tmp.cleanup()

    def queue(self, keys):
        install_plan.write_marker(self.sdir, {"pending": list(keys)})

    def drain(self, limit: int = 20) -> list:
        waits = []
        for _ in range(limit):
            w = P.deferred_fetch_step()
            waits.append(w)
            if w is None or w >= P.DEFERRED_DISK_RECHECK_SEC:
                break
        return waits


class BackgroundFetcher(unittest.TestCase):
    def test_an_updated_install_without_a_marker_downloads_nothing(self):
        with _PanelSandbox() as sb:
            self.assertFalse(P.deferred_fetch_enabled())
            self.assertIsNone(P.deferred_fetch_step())
            self.assertEqual(sb.fetched, [])
            self.assertEqual(P.deferred_status()["pending"], [])

    def test_fetches_one_at_a_time_in_order_and_empties_the_marker(self):
        with _PanelSandbox() as sb:
            sb.queue(["gemma", "ic_upscale_x2", "ic_colorize"])
            self.assertTrue(P.deferred_fetch_enabled())
            waits = sb.drain()
            self.assertEqual(sb.fetched, ["gemma", "ic_upscale_x2", "ic_colorize"])
            self.assertIsNone(waits[-1])
            self.assertFalse((sb.sdir / install_plan.MARKER_NAME).exists())
            self.assertEqual(P.DEFERRED["finished"], ["gemma", "ic_upscale_x2", "ic_colorize"])

    def test_waits_rather_than_fill_the_disk(self):
        with _PanelSandbox(free_gb=12.0) as sb:     # gemma 8.07 + 10 GB floor > 12
            sb.queue(["gemma"])
            w = P.deferred_fetch_step()
            self.assertEqual(w, P.DEFERRED_DISK_RECHECK_SEC)
            self.assertEqual(sb.fetched, [])
            st = P.deferred_status()
            self.assertEqual(st["state"], "waiting_disk")
            self.assertIn("12.0 GB free", st["message"])
            self.assertEqual(P.deferred_pack_state("gemma"), "waiting_disk")

    def test_a_big_pack_that_does_not_fit_does_not_hold_back_small_ones(self):
        with _PanelSandbox(free_gb=11.0) as sb:     # gemma 8.07 + 10 > 11, colorize 0.33 + 10 < 11
            sb.queue(["gemma", "ic_colorize"])
            sb.drain()
            self.assertEqual(sb.fetched, ["ic_colorize"])
            self.assertEqual(P.DEFERRED["state"], "waiting_disk")
            self.assertEqual(P.deferred_pack_state("gemma"), "waiting_disk")
            self.assertEqual(install_plan.read_marker(sb.sdir)["pending"], ["gemma"])

    def test_a_skipped_pack_is_dropped_not_retried(self):
        with _PanelSandbox(outcome="cancelled", land=False) as sb:
            sb.queue(["gemma", "ic_colorize"])
            P.deferred_fetch_step()
            self.assertEqual(install_plan.read_marker(sb.sdir)["pending"], ["ic_colorize"])

    def test_a_failure_backs_off_then_gives_up(self):
        with _PanelSandbox(outcome="failed", land=False) as sb:
            sb.queue(["ic_colorize"])
            P.deferred_fetch_step()
            self.assertEqual(P.DEFERRED["failures"]["ic_colorize"], 1)
            self.assertEqual(P.deferred_fetch_step() > 1, True)   # in its back-off window
            self.assertEqual(sb.fetched, ["ic_colorize"])
            P.DEFERRED["failures"]["ic_colorize"] = P.DEFERRED_MAX_FAILURES
            self.assertIsNone(P.deferred_fetch_step())
            self.assertEqual(P.DEFERRED["state"], "failed")
            # still queued for the next boot
            self.assertEqual(install_plan.read_marker(sb.sdir)["pending"], ["ic_colorize"])

    def test_a_busy_slot_is_waited_for(self):
        with _PanelSandbox() as sb:
            sb.queue(["gemma"])
            P.DOWNLOAD.update({"active": True, "key": "q8_25", "background": False})
            self.assertEqual(P.deferred_fetch_step(), P.DEFERRED_SLOT_WAIT_SEC)
            self.assertEqual(sb.fetched, [])

    def test_a_click_on_a_queued_pack_moves_it_to_the_front(self):
        with _PanelSandbox() as sb:
            sb.queue(["gemma", "ic_upscale_x2", "ic_colorize"])
            P.DOWNLOAD.update({"active": True, "key": "gemma", "repo_id": "x", "background": True})
            h = _H("repo_key=ic_colorize")
            POST_ROUTES["/models/download"](h, "/models/download", {}, "")
            self.assertEqual(h.out[0], 202, h.out)
            self.assertTrue(h.out[1]["queued"])
            self.assertEqual(install_plan.read_marker(sb.sdir)["pending"][0], "ic_colorize")
            h = _H("repo_key=gemma")
            POST_ROUTES["/models/download"](h, "/models/download", {}, "")
            self.assertTrue(h.out[1].get("already"), h.out)
            # A CLICKED download still blocks others, as before.
            P.DOWNLOAD.update({"background": False})
            h = _H("repo_key=ic_upscale_x2")
            POST_ROUTES["/models/download"](h, "/models/download", {}, "")
            self.assertEqual(h.out[0], 409)


class _H:
    def __init__(self, body: str = ""):
        self.body, self.out = body, None

    def _read_form_body(self):
        return self.body, urllib.parse.parse_qs(self.body)

    def _json(self, obj, code=200):
        self.out = (code, obj)


# ---------------------------------------------------------------------------
class FeaturesWithoutGemma3(unittest.TestCase):
    """Enhance, the Storyboard planner, auto-caption and one-shot beats run on
    Gemma 3. Until it lands they say so - with the one action that helps."""

    def _enhance(self):
        class _Helper:
            def run(self, *a, **k):
                raise AssertionError("the helper must not be asked to load a missing model")
        h = _H("prompt=a+lighthouse+in+fog&mode=t2v")
        orig = P.HELPER
        P.HELPER = _Helper()
        try:
            POST_ROUTES["/prompt/enhance"](h, "/prompt/enhance", {}, "")
        finally:
            P.HELPER = orig
        return h.out

    def test_enhance_says_downloading_while_the_background_fetch_runs(self):
        with _PanelSandbox():
            P.GEMMA_ORIG = P.GEMMA
            try:
                P.GEMMA = P.ROOT / BY_KEY["gemma"]["local_dir"]
                install_plan.write_marker(P.STATE_DIR, {"pending": ["gemma"]})
                P.DOWNLOAD.update({"active": True, "key": "gemma", "background": True})
                code, body = self._enhance()
                self.assertEqual(code, 409)
                self.assertEqual(body["needs_pack"], "gemma")
                self.assertEqual(body["pack_state"], "downloading")
                self.assertIn("still downloading in the background", body["error"])
                self.assertIn("8.1 GB", body["error"])
                P.DOWNLOAD.update({"active": False, "key": None, "background": False})
                code, body = self._enhance()
                self.assertEqual(body["pack_state"], "queued")
                P.install_plan.drop_from_marker(P.STATE_DIR, "gemma")
                code, body = self._enhance()
                self.assertEqual(body["pack_state"], "missing")
                self.assertIn("not downloaded yet", body["error"])
            finally:
                P.GEMMA = P.GEMMA_ORIG

    def test_enhance_runs_once_the_model_is_there(self):
        with _PanelSandbox() as sb:
            orig = P.GEMMA
            try:
                P.GEMMA = P.ROOT / BY_KEY["gemma"]["local_dir"]
                _sparse(sb.root, BY_KEY["gemma"])
                self.assertFalse(P.enhance_model_missing())
            finally:
                P.GEMMA = orig

    def test_a_moved_model_path_names_the_setting_not_a_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = P.gemma3_refusal("Enhance", "its language model, Gemma 3", Path(tmp) / "nope")
            self.assertNotIn("needs_pack", body)
            self.assertIn("LTX_GEMMA_PATH", body["error"])

    def test_a_mirrored_pack_reserves_room_for_one_shard(self):
        with _PanelSandbox(free_gb=10.5) as sb:     # 0.33 + 10 fits; + the 1.9 GB shard does not
            sb.queue(["ic_upscale_x2"])
            P.deferred_fetch_step()
            self.assertEqual(sb.fetched, [])
            self.assertEqual(P.DEFERRED["state"], "waiting_disk")

    def test_a_moved_model_path_is_trusted_when_it_holds_a_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(P._gemma3_missing_at(Path(tmp)))
            (Path(tmp) / "config.json").write_text("{}")
            self.assertFalse(P._gemma3_missing_at(Path(tmp)))

    def test_storyboard_plan_refuses_with_the_pack_payload(self):
        orig = P.planner_model_missing
        P.planner_model_missing = lambda: True
        try:
            body = urllib.parse.urlencode({"concept": "a lighthouse keeper finds a door"}).encode()
            handler = object.__new__(P.Handler)
            handler.path = "/storyboard/plan"
            handler.command = "POST"
            handler.request_version = "HTTP/1.1"
            handler.requestline = "POST /storyboard/plan HTTP/1.1"
            handler.client_address = ("127.0.0.1", 0)
            handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(body)),
                               "Content-Type": "application/x-www-form-urlencoded"}
            handler.rfile = io.BytesIO(body)
            handler.wfile = io.BytesIO()
            handler.do_POST()
            head, out = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
            self.assertIn(b" 409 ", head.splitlines()[0])
            payload = json.loads(out)
            self.assertEqual(payload["needs_pack"], "gemma")
            self.assertIn("Storyboard planner", payload["error"])
            self.assertEqual(P.storyboard_status()["planner_present"], False)
        finally:
            P.planner_model_missing = orig

    def test_one_shot_beats_say_so_too(self):
        orig = P.planner_model_missing
        P.planner_model_missing = lambda: True
        try:
            res = P.oneshot_plan_beats("a lighthouse keeper", P.TAKE_SECONDS[0])
            self.assertEqual(res["needs_pack"], "gemma")
        finally:
            P.planner_model_missing = orig

    def test_auto_caption_says_so_too(self):
        orig = P.enhance_model_missing
        P.enhance_model_missing = lambda: True
        try:
            jid = "cap_test_job"
            ds = P._train_dataset_dir(jid) / "images"
            ds.mkdir(parents=True, exist_ok=True)
            (ds / "a.png").write_bytes(b"x")
            h = _H(f"train_job_id={jid}&trigger=captrn")
            POST_ROUTES["/train/auto-caption"](h, "/train/auto-caption", {}, "")
            self.assertEqual(h.out[0], 409, h.out)
            self.assertEqual(h.out[1]["needs_pack"], "gemma")
        finally:
            P.enhance_model_missing = orig

    def test_status_carries_the_background_list(self):
        with _PanelSandbox() as sb:
            sb.queue(["gemma", "ic_colorize"])
            h = _H()
            GET_ROUTES["/status"](h, urllib.parse.urlparse("/status"))
            st = h.out[1]["deferred_downloads"]
            self.assertEqual([r["key"] for r in st["pending"]], ["gemma", "ic_colorize"])
            self.assertAlmostEqual(st["left_gb"], 8.07 + 0.33, places=1)
            h = _H()
            GET_ROUTES["/models"](h, urllib.parse.urlparse("/models"))
            self.assertEqual(h.out[1]["deferred_pending"], ["gemma", "ic_colorize"])


# ---------------------------------------------------------------------------
@unittest.skipUnless(NODE, "node not on PATH")
class PinokioMenu(unittest.TestCase):
    """Start is offered the moment the renderer is on disk - with Gemma 3 and
    every IC-LoRA still missing - and it auto-runs on a fresh install."""

    def test_start_appears_with_only_before_start_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(ROOT / "pinokio.js", root / "pinokio.js")
            shutil.copy(ROOT / "required_files.json", root / "required_files.json")
            for rel in ("ltx-2-mlx/env/pyvenv.cfg", "ltx-2-mlx/env/bin/python3.11"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("x")
            (root / "ltx-2-mlx/env/lib/python3.11/site-packages/ltx_pipelines_mlx").mkdir(parents=True)
            for k in install_plan.install_block(REG)["before_start"]:
                _sparse(root, BY_KEY[k])
            probe = (
                "const path=require('path'),fs=require('fs');const root=process.argv[1];"
                "const mod=require(path.join(root,'pinokio.js'));"
                "const info={path:(...a)=>path.join(root,...a),exists:p=>fs.existsSync(path.join(root,p)),"
                "running:s=>false,local:s=>({})};"
                "mod.menu({},info).then(m=>console.log(JSON.stringify(m.map(i=>({text:i.text,default:!!i.default})))))")
            out = subprocess.run([NODE, "-e", probe, str(root)], capture_output=True, text=True, timeout=30)
            self.assertEqual(out.returncode, 0, out.stderr)
            menu = json.loads(out.stdout)
            self.assertEqual(menu[0], {"text": "Start", "default": True}, menu)
            self.assertFalse([i for i in menu if "Resume Install" in i["text"]], menu)


# ---------------------------------------------------------------------------
@unittest.skipUnless(NODE, "node not on PATH")
class PackNeededToastInTheBrowser(unittest.TestCase):
    """Executed, not grepped: the toast's one action is the right one."""

    def _run(self, res: dict) -> dict:
        fn = extract_function("packNeededToast", PJS)
        script = f"""
const calls = [];
global.last = null;
function phosToast(msg, opts) {{ const el = {{ msg, opts, kids: [], appendChild(k) {{ this.kids.push(k); }},
  remove() {{ calls.push('removed'); }} }}; global.last = el; return el; }}
function openModelsModal() {{ calls.push('modal'); }}
function startDownload(k) {{ calls.push('download:' + k); }}
global.document = {{ createElement: () => ({{ }}) }};
{fn}
const handled = packNeededToast({json.dumps(res)});
const a = last && last.kids[0];
if (a) a.onclick({{ preventDefault() {{}} }});
console.log(JSON.stringify({{ handled, msg: last && last.msg, label: a && a.textContent, calls }}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_offers_download_now_with_the_size(self):
        out = self._run({"error": "Enhance needs ...", "needs_pack": "gemma",
                         "pack_size": "8.1 GB", "pack_state": "missing"})
        self.assertTrue(out["handled"])
        self.assertEqual(out["label"], "Download now (8.1 GB)")
        self.assertIn("download:gemma", out["calls"])

    def test_downloading_offers_progress_not_a_second_download(self):
        out = self._run({"error": "still downloading", "needs_pack": "gemma",
                         "pack_size": "8.1 GB", "pack_state": "downloading"})
        self.assertEqual(out["label"], "Show progress")
        self.assertIn("modal", out["calls"])
        self.assertNotIn("download:gemma", out["calls"])

    def test_paused_for_disk_offers_the_explicit_download(self):
        out = self._run({"error": "paused", "needs_pack": "gemma",
                         "pack_size": "8.1 GB", "pack_state": "waiting_disk"})
        self.assertEqual(out["label"], "Download now (8.1 GB)")

    def test_an_ordinary_error_is_left_to_the_caller(self):
        self.assertFalse(self._run({"error": "a render is using the GPU"})["handled"])

    def test_every_gemma3_caller_routes_through_it(self):
        js = ROOT / "webapp" / "js"
        enh = extract_function("enhancePrompt", (js / "queue.js").read_text(encoding="utf-8"))
        self.assertIn("packNeededToast(res)", enh)
        for f, n in (("storyboard.js", 4), ("characters.js", 2), ("oneshot.js", 1)):
            self.assertGreaterEqual((js / f).read_text(encoding="utf-8").count("packNeededToast("), n, f)
        self.assertIn("packNeededToast,", PJS)   # published


if __name__ == "__main__":
    unittest.main(verbosity=2)

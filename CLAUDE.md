# Phosphene — agent operating manual

This file is the single source of truth for how an AI assistant should
work on this codebase. Read it first, every session, before touching
any file. Generic Pinokio launcher rules live in the parent
`Pinkio/CLAUDE.md`; this file overrides it where they conflict.

---

## 0. Current state — read `docs/STATE.md` first

**THE RELEASE FACTS IN THIS SECTION ARE OLD.** `VERSION` on this branch and
`docs/STATE.md` are the two things that are current; the v3.0 history below is
kept as background and nothing else. As of 2026-08-19: `VERSION` reads
**4.6.0**, and public `main` is at **v4.5.0** — v4.6.0 was promoted and
withdrawn the same day (the Editor shipped before the owner had cut with it),
so what a user can install and what this branch is are two different numbers.
Which release the front page advertises, and when v4.6.0 goes back out, are
the owner's calls; do not settle them from a doc-hygiene pass.

Full v3.0 history, the 2.0 capability checklist and benchmark table: `docs/HISTORY.md`.

### Repository layout (now — both clones documented)

`~/pinokio/api/phosphene.git/` is the **production** clone.
Tracks `main`. Pinokio's daily-driver Phosphene panel runs from here on
port 8198.

`~/pinokio/api/phosphene-dev.git/` is the **dev** clone.
Tracks `dev`. Set up via `scripts/setup_dev_panel.sh`. Runs on port 8199
with the amber `DEV` profile badge in the header. Symlinks
`mlx_models/`, `mlx_outputs/`, `panel_uploads/`, and `state/` from a
shared Pinokio fs.link drive so weights aren't duplicated and outputs
land in one gallery regardless of which panel rendered them.

When a session starts: `git checkout dev` in the dev clone, work, push.
Promote to main only when Mr Bizarro gives the explicit green light. See §7.

---

## 1. What Phosphene is

A free desktop panel that wraps Lightricks' **LTX 2.3** model running
natively on Apple's **MLX** framework, exposed as a one-click install
through **Pinokio**. Generates video and audio jointly in a single
forward pass on Apple Silicon Macs. Apple-Silicon-only.

Public artifacts:
- GitHub: `github.com/mrbizarro/phosphene` (public, MIT for the panel)
- Pinokio Discover: searchable as "Phosphene"
- Twitter handle: `@AIBizarrothe`

The panel itself is MIT. Model weights have Lightricks' own license.

## 2. Where to work

**The Pinokio install dir IS the canonical workspace.** There is no
separate dev folder. Old "local dev" copy was deleted to consolidate.

```
~/pinokio/api/phosphene.git/        ← edit here
├── .git/                                       ← real working copy of mrbizarro/phosphene
├── ltx-2-mlx/                                  ← upstream package, cloned at install
│   ├── env/                                    ← Python 3.11 venv
│   └── packages/{ltx-core-mlx,ltx-pipelines-mlx}
├── mlx_models/                                 ← downloaded weights
│   ├── ltx-2.3-mlx-q4/                         ← Q4 base (~20 GB, filtered Y1.024+)
│   ├── ltx-2.3-mlx-q8/                         ← optional Q8 (~37 GB, filtered Y1.024+)
│   └── gemma-3-12b-it-4bit/                    ← text encoder (~7.5 GB)
├── mlx_outputs/                                ← generated mp4s
├── panel_uploads/                              ← user-uploaded reference images
├── assets/                                     ← logos, banner, hero clip
├── launch/                                     ← launch post drafts (X, Reddit, Pinokio, CivitAI)
├── mlx_ltx_panel.py                            ← MAIN: HTTP server, HTML UI, queue
├── webapp/                                     ← THE FRONTEND, served from disk (docs/ARCHITECTURE.md is the map)
│   ├── index.html                              ← the page markup; inline JS is ONE line (the __BOOTSTRAP__ seam)
│   ├── js/                                     ← 12 ES modules (boot…main) — new JS goes in the MATCHING module
│   └── style/panel.css                         ← ALL panel CSS — new styles go HERE, never in the Python string
├── panel/                                      ← ALL HTTP route handlers (routes_*.py) — new endpoints REGISTER
│                                                 here (@get/@post/@get_when/@post_when), never as do_GET/do_POST arms
├── mlx_warm_helper.py                          ← subprocess holding MLX pipelines warm
├── pinokio.js / install.js / start.js / update.js / reset.js / download_q8.js
├── patch_ltx_codec.py                          ← applies patches to upstream package
└── required_files.json                         ← single source of truth for "installed"
```

To work: `cd ~/pinokio/api/phosphene.git/`, edit, `git commit`,
`git push`. Pinokio's Update button does `git pull` here. No separate
copy to keep in sync.

### THE STRUCTURE LAW — non-negotiable, 2026-09-01+

The 72k-line monolith is gone (`docs/ARCHITECTURE.md` is the full map
and the reasoning). Every kind of change now has EXACTLY ONE home, and
the point of this section is that no session — human or AI — ever has to
guess. Before touching panel code, read the map; then obey:

1. **Styles** → `webapp/style/panel.css`. Never a `<style>` block, never
   CSS-in-Python, anywhere else.
2. **Markup** → `webapp/index.html`. Its inline `<script>` is EXACTLY
   one line (`const BOOT = __BOOTSTRAP__;`) and stays that way.
3. **JS** → the ONE `webapp/js/` module that owns the screen (editor,
   queue, characters, storyboard, stage, preview, engines, loras,
   settings, boot, health). A function or state another file needs is
   PUBLISHED (the `Object.assign(globalThis, {...})` block / a column-0
   `globalThis.X =`); everything else stays module-private.
4. **Run-once startup calls** → `webapp/js/main.js`, which loads last on
   purpose. Never at a feature module's top level.
5. **Routes** → a handler in the matching `panel/routes_*.py`,
   registered with `@get`/`@post`/`@get_when`/`@post_when`. NEVER an
   if-arm in `do_GET`/`do_POST` — those are ~35-line dispatchers now and
   hold no logic.
6. **First-paint data** → a field in BOOT built server-side in `page()`;
   live data → a field in `/status`. The browser computes no tier, no
   canvas, no estimate of its own.
7. **Render behaviour** → `mlx_warm_helper.py` + the job spec
   (`make_job()`), not the frontend.
8. **No frontend in Python, no Python-side page strings — ever again.**

Enforcement is mechanical, not honorary — these fail the build:
`test_routes.py` (a route claimed once, chains stay empty),
`test_no_duplicate_defs.py` (nothing defined twice — the built-twice
tombstone), `test_panel_assets.py` (files really served; the one-line
inline seam and the no-markup-in-Python pins), and
`scripts/lint_webapp.mjs` (every cross-module reference resolves; run
`npm install` once, then it rides in release_gates). After ANY change:
`bash scripts/release_gates.sh --fast` must exit 0 before commit — key
the commit on the runner's own exit code, never on grep output.

## 3. Architecture (request → frame)

```
Pinokio app (port 42000)
   └── proxies to → mlx_ltx_panel.py (port 8198)
                       │
                       ├── serves the single-page UI from webapp/ (index.html +
                       │   12 ES modules + panel.css, files on disk; the HTTP
                       │   handlers live in panel/routes_*.py — ARCHITECTURE.md)
                       ├── exposes HTTP API: /run, /status, /upload, /helper/restart, ...
                       ├── owns a job queue (FIFO) + worker thread
                       └── speaks JSON-over-stdin/stdout to:
                              │
                              └── mlx_warm_helper.py (subprocess in same venv)
                                     ├── holds MLX pipeline objects warm across jobs
                                     ├── pipeline classes from ltx-2-mlx package
                                     ├── reaps itself after LTX_IDLE_TIMEOUT (1800s default)
                                     ├── emits events: log / done / error / exit
                                     └── on death → panel sees stdout EOF
```

Both processes run inside `ltx-2-mlx/env` (the Python 3.11 venv). Helper
restarts cheaply via `POST /helper/restart` to pick up code changes
without restarting the panel.

## 4. Locked dependency versions

These are not arbitrary — every pin is a paid lesson:

| Pin | Why |
|---|---|
| `python>=3.11` (force `uv venv --python 3.11`) | Pinokio's `venv:` shortcut defaults to conda's base 3.10. ltx-core-mlx requires `>=3.11`. |
| `mlx==0.31.1` | 0.31.2 attenuates the LTX 2.3 vocoder ~22 dB (-42 dB vs -9 dB peak, same seed). 0.32.x fixed audio but was REFUSED twice (2026-08-28): its active memory is higher, putting 3 of 4 RAM tiers at a higher share of physical RAM and the 48 GB tier on the 82% pressure line. `mlx_warm_helper.py` caps the MLX cache at 1/8 RAM (2–8 GiB). mflux stays 0.18.0. Full measurements: `docs/DEPENDENCY_PINS.md`. |
| `mlx-lm==0.31.1` `mlx-metal==0.31.1` | Same release line, kept consistent with mlx. |
| `huggingface-hub>=1.0` | `hf` CLI replaced `huggingface-cli`; older Pinokio bundles ship < 1.0. |
| `ltx-2-mlx` PINNED to the FORK TAG **`v0.14.19+ltx25.7`** (`mrbizarro/ltx-2-mlx`) | **STAY PINNED** — not main HEAD, not upstream's tip. The pin is a TAG and lives in `scripts/pinokio/ltx_checkout.sh`, the ONE checkout implementation (install.js and `scripts/post_update.sh` both call it); `check_ltx_pin.js` is the gate. The packages report **`0.14.19+ltx25.7`**; `_LTX_EXPECTED_VERSION` in mlx_warm_helper.py must equal that string or renders log VERSION SKEW — move the two together. The pin is what lets LTX-2.5 render (registry default everywhere since v4.0.0; 2.3 stays selectable via `LTX_MODEL_VERSION=ltx23`). The `+ltx25.N` tags carry the 2.5 sampler/schedule, i2v anchor re-pin (both loops, incl. res_2s), and IC-LoRA `source_video=`; 2.3 output is proven sha256-identical across them. To bump: read upstream notes, bump `_LTX_EXPECTED_VERSION`, smoke-test on dev, bump install.js and update.js in one commit. Per-tag history and proofs: `docs/DEPENDENCY_PINS.md`. The `dcd639e (0.1.0)` detour broke Extend `cfg_scale`; do not revisit. |
| `hatchling<1.32` (build-time only, `pip-build-constraints.txt`) | Not a runtime dep — the **wheel build backend** for the three vendored `ltx-*` packages. All three declare `readme = "../../README.md"`, which hatchling 1.32.0 turned into a hard error, so both installer paths died at `metadata-generation-failed` on *every* pinned tag from the day 1.32 shipped. **Both are now `uv pip install --build-constraints ../pip-build-constraints.txt`** — install.js always was, update.js as of the v3.8.0 port-back. **NOT `PIP_CONSTRAINT=`**: modern pip spawns its build-dependency sub-install with `_PIP_IN_BUILD_IGNORE_CONSTRAINTS=1` and drops the env constraint on purpose, so that spelling silently resolved hatchling 1.32.0 and failed anyway (proven on pip 26.1/26.2.1, relative and absolute paths). pip's `--build-constraint` flag would work but does not exist on older pips, and Update runs on venvs seeded years apart. Remove only when a pinned upstream tag stops pointing `readme` outside the package dir. |

When changing a version, document the test that proved the new pin is OK.
The v0.14.19 pin was proved by: 607 passed / 22 skipped on the vendored
`tests/` suite; `patch_ltx_codec.py` → `applied` with the installed tree
diverging from the tag in exactly one file and one hunk (the ffmpeg line);
and a real T2V draft render on a throwaway instance (isolated state +
output dirs, GPU lock held) producing video **and** audio.

## 5. The patch system (`patch_ltx_codec.py`)

We patch the upstream `ltx-2-mlx` package after `pip install`. Patches
are idempotent and fail loud on upstream drift (don't silently ship a
broken pipeline). Each patch has an `OLD` text it expects to find and a
`NEW` text it replaces with, plus a `marker` to detect "already patched."

As of the **v0.14.8** pin (2026-06-01 catch-up) only ONE patch remains —
upstream absorbed the other six natively, so we dropped them and cut our
divergence from 7 patches to 1 (the whole reason for the catch-up). The
**v0.14.19** pin (2026-08-11) re-validated that: the `decode_and_stream`
ffmpeg line moved 482 → 487 but its text is character-identical, so the
patch applied with no edit to `PATCH_CODEC_OLD`:

1. **Codec (`yuv444p crf 0` + `+faststart`)** — required.
   Patches `ltx_core_mlx/model/video_vae/video_vae.py`. Default ffmpeg
   args become lossless H.264 (yuv444p crf 0) and the moov atom moves
   to the front of the file so gallery thumbnails render the first
   frame without downloading the full clip.

**Dropped at v0.14.8 (now native upstream — do NOT re-add):**

- **I2V / pre-denoise / base-load memory frees** (old patches 2,3,4) →
  native `low_memory` path. v0.14.8 frees generation components via
  composition-block `.free()` (`prompt_encoder`/`image_conditioner`) in
  `ti2vid_one_stage.py` + `_base.py` before loading decoders.
- **VAE temporal streaming** (old patch 5) → native budget-aware tiled
  `decode_and_stream` in `video_vae.py` (`LTX2_VAE_DECODE_BUDGET_GB`,
  default 8 GB; single-pass for short clips). Strictly better than ours.
- **Metal-watchdog DiT-eval-cadence fix** (old patch 7) → native
  `_pre_denoise_flush` (an `mx.eval` barrier before every denoise loop).
  `_DIT_EVAL_EVERY` no longer exists. The flush is wired into the Q4
  one-stage path (`ti2vid_one_stage.py`) — the exact path that produced
  the I2V "mosaic" on memory-pressured Macs (#17) — plus all pipelines.
- **one-stage `frame_rate` / 12→24 fps long clips** (old patch 6) →
  native, first-class, keyword-only `frame_rate` threaded end-to-end.

The helper calls the new API defensively (`hasattr` guard on
`generate`/`generate_from_image`, `inspect.signature` probing, and
`_filter_unsupported_kwargs`), so nothing depends on the dropped patches.
Re-implementing any of them against the pinned tag would only re-introduce
the divergence the catch-up removed.

The "upgrade" path in `apply_patch()` lets old patched installs receive
new patch versions without a venv rebuild — used when we added
`+faststart` to the existing codec patch.

## 6. Critical history (so we don't relitigate)

- **mlx 0.31.2 audio regression** (resolved): symptom was "audio sounds
  like static / very quiet." Root cause was an MLX numerical change
  attenuating the vocoder output by ~22 dB. Fix: `mlx==0.31.1` pin.
  Bisecting which mlx commit caused it is a v1.1 task.
- **dcd639e pin detour** (resolved): briefly pinned `ltx-2-mlx` to
  commit `dcd639e` (0.1.0) thinking the audio regression was in
  dgrauet's commits. It wasn't. Pin caused Extend API mismatch
  (`cfg_scale` kwarg unknown) and required a transformer.safetensors
  symlink because the 0.1.0 loader hardcoded that filename. Reverted.
- **Pinokio menu API mismatch** (resolved): `info.path` is a function
  on newer Pinokio, a string on older. `pinokio.js` now detects both
  shapes via `getInstallRoot(info)`.
- **start.js URL hijack** (resolved): the `errno`/`error:` event
  patterns were cargo-culted from comfy.git's start.js. They captured
  any "Errno" line from the helper into `input.event[0]` and the panel
  URL got set to the literal string "Errno." Removed those patterns.
- **silent helper death** (mitigated, not fixed): on memory-pressured
  Macs the helper subprocess is SIGKILL'd by macOS jetsam during I2V
  pipeline load. Patches 3 and 4 reduced peak; the panel now names
  the exit signal (SIGKILL/SIGSEGV/SIGABRT/SIGBUS) and points users at
  `~/Library/Logs/DiagnosticReports/python3.11_*.crash` for diagnosis.
- **HTML5 form validation silent block** (resolved): a hidden Extend
  input had `value=2 min=0.4 step=0.5`. Chrome's validator only accepts
  values on the (min + N*step) sequence, so 2 was off-grid → invalid
  → entire form silently failed to submit. Fixed `min=0.5`.

## 7. Working rules — non-negotiable

### Identity
- **Author/committer:** `mrbizarro <mrbizarro@users.noreply.github.com>`.
  Already set in this repo's local git config. Never override.
- **Never use the user's real first name** in commits, code, file
  contents, or any artifact that lands on disk or in git. Same for
  any third party (reviewers, collaborators) — refer to them by
  technical role only ("upstream review", "external reviewer", "user
  feedback") if at all.
- **Never add `Co-Authored-By:` trailers.** Phosphene reads as a
  serious project shipped by `mrbizarro`. Co-author trailers showing
  "Claude" make it look like a vibe-coded toy.

### Branch model — `main` is production, `dev` is staging (Y1.015+)

**TL;DR for agents — non-negotiable:**

> **REPO SPLIT (2026-05-22) — read this carefully, the old "push to
> origin/dev" instruction is now WRONG.** There are two repos:
> `mrbizarro/phosphene` (PUBLIC, `main`-only — what users install) and
> `mrbizarro/phosphene-beta` (PRIVATE — daily dev). The public `dev`
> branch was **DELETED**. The local working branch is still named `dev`
> but it **tracks `beta/main`** (the private repo). So: **push daily work
> to `beta` (private). Never push to a public `origin/dev` — it does not
> exist.** Promotion to PUBLIC `main` is the gated step that needs Mr
> Bizarro's explicit "push to main" / "ship to production". "It works on
> my machine" / "tests pass" is not permission.

The why:

- **public `origin/main`** is what real users get. Pinokio's Discover URL
  points here; every commit promoted to public `main` ships to every user
  on their next Update. Production.
- **private `beta/main`** is the staging line. The local `dev` branch
  tracks it; push WIP here freely (`git push beta dev:main`). The local
  "Phosphene Dev" panel tracks `dev` on port 8199 with an amber DEV badge.
- **Workflow**:
  1. `git checkout dev` at the start of any Phosphene session. `git fetch
     beta && git fetch origin` to surface drift on both remotes.
  2. Commit, then `git push beta dev:main` whenever a logical change is
     done (lands on the PRIVATE beta).
  3. **Stop there.** Tell Mr Bizarro it's on beta. Wait for explicit
     instruction to promote to public.
  4. When Mr Bizarro says ship/push to main: `git push origin dev:main`,
     then tag + `gh release create` (see the release workflow in memory
     `phosphene_repo_split.md`).
  5. Never force-push or rewrite public `main`. If histories have
     diverged, say so and ask before anything destructive.
- The panel auto-detects which profile it is from `git rev-parse
  --abbrev-ref HEAD`: branch == `dev` → port 8199 + DEV badge; anything
  else → port 8198 + normal header. No config files to edit per panel.
- `update.js` is branch-aware as of Y1.015 (`git pull --ff-only origin
  <current branch>`) so the dev panel's Update pulls dev and the
  production panel's Update pulls main, from the same shared code.
- The dev panel symlinks `mlx_models/` from the production install
  (saves ~36 GB of duplicate weights). State / outputs / uploads stay
  separate per panel.
- Hotfixes bypassing dev — only allowed when Mr Bizarro explicitly authorises
  ("hotfix straight to main" or similar). Otherwise, even one-line
  fixes go via dev → tested in Phosphene Dev panel → merged.

### Git policy — NEVER FORCE-PUSH `origin/main`
- A history-rewrite + force-push on 2026-05-01 (to scrub identity
  leaks from old commits) broke `git pull` for every existing user
  whose local clone contained orphaned commits — Pinokio's Update
  silently stalled for them. GitHub aggressively GC'd the orphaned
  objects within hours, so the pre-rewrite history is unrecoverable.
- **Do not run `git filter-branch`, `git rebase` (on shared history),
  `git push --force`, `git push --force-with-lease`, or anything else
  that rewrites already-published commits on `origin/main`. Ever.**
- If a commit needs amending and it's already pushed, **make a new
  commit that reverts/fixes it.** The history is append-only.
- If the user requests a history rewrite (e.g. "scrub my name"), say
  no and explain why: existing users get orphaned, their `git pull`
  fails, the entire user base needs out-of-band recovery.
- Recovery from the 2026-05-01 incident — primary path is **Pinokio
  Reset → Install** (clean, no shell scripts, models re-download is a
  one-time cost). The `recover.sh` script is an escape hatch for users
  who want to keep their downloaded models — kept in the repo with a
  clear "read before running" header, NOT promoted as a `curl | bash`
  command. Don't remove `recover.sh` or weaken `update.js`'s
  fetch+reset fallback.

### `fs.link` — orthogonal to the history-divergence story
- `fs.link` is in `install.js` only (NOT `update.js`). It maps the
  heavy/personal directories — `mlx_models/`, `mlx_outputs/`,
  `panel_uploads/`, `state/` — to a Pinokio virtual drive that lives
  outside the panel install dir. After a Pinokio Reset → Install, the
  drive is rediscovered and the symlinks reattach, so models +
  outputs + settings survive a clean reinstall.
- Why install.js only: future users get the drive on first install;
  existing pre-Y1.004 users get it whenever they next reinstall (which
  is the recommended recovery path anyway). Adding `fs.link` to
  update.js would technically migrate existing real folders into the
  drive without a Reset, but it conflates routine code updates with a
  one-time 36 GB merge step, so we keep them separate.
- `fs.link` is unrelated to the 2026-05-01 history-rewrite incident
  — don't conflate them in commit messages or docs.

### Commit message style
- Subject line in imperative mood, 50–72 chars max, no trailing period.
  Use `feat:` / `fix:` / `refactor:` / `docs:` / `chore:` / `perf:` /
  `test:` prefixes when they help.
- Body explains the **why**: the symptom, the root cause, the chosen
  fix. The diff already shows the what.
- No first-person voice. Third person, neutral, technical.
- No conversation transcript. No "user said X", "after we discussed Y",
  "per the review on Tuesday." Just describe the change.
- Cite concrete things: file paths, line numbers, package versions,
  upstream commits, signal names. Avoid vague nouns ("the issue").
- Reference upstream context where it helps future maintainers (HF
  metadata size, upstream commit SHA, GitHub issue number).
- One commit = one logical change. Don't bundle UI polish with bug
  fixes with version pins.

### Examples — bad vs good

❌ Bad:
```
fix the audio thing peanut found

cocktailpeanut said the audio sounded like static so we tried a few
things and pinning mlx fixed it. great catch!
```

✅ Good:
```
fix(audio): pin mlx==0.31.1 to recover LTX 2.3 vocoder amplitude

mlx 0.31.2 introduces a numerical change that attenuates the LTX 2.3
vocoder output by ~22 dB. Verified empirically with the same prompt,
seed, and weights:
  mlx 0.31.2 → max_volume -42.8 dB (audible only with significant boost)
  mlx 0.31.1 → max_volume   -9.2 dB

install.js installs the pinned trio (mlx, mlx-lm, mlx-metal) before
the ltx-* packages so transitive resolution does not pull latest.
update.js force-reinstalls them with --no-deps so existing installs
that already pulled 0.31.2 can recover by clicking Update.
```

### Code style
- Comments explain non-obvious decisions and trade-offs. They earn
  their keep by explaining context the diff cannot.
- When fixing a bug, leave a comment with the symptom + cause + fix
  near the change so the next reader doesn't reintroduce it.
- Patches in `patch_ltx_codec.py` always include a comment block
  above each patch explaining what the upstream code does, why we
  patch it, and what the patch achieves.

### Working with the running install
- The Pinokio panel may already be running on port 8198 when you
  start a session. **Never spawn your own panel from the command
  line** — Pinokio expects to own that port and will fail Start
  with `Address already in use` if you do.
- To pick up code changes without restarting Pinokio, hit
  `POST /helper/restart` — that reloads the helper subprocess
  against the patched site-packages.
- For HTML/JS/CSS changes you do need a panel restart. Either
  click Stop + Start in Pinokio, or `pkill -f mlx_ltx_panel.py`
  and click Start.

### The GPU lock — BOTH of them, and staleness is a TIMESTAMP

This machine runs several agents at once and one 40 GiB render. Two of
them do not fit. Every render, benchmark or experiment that touches the
GPU takes **both** locks for its whole duration and releases both after:

| lock | shape | who introduced it |
|---|---|---|
| `~/AI/projects/hailuo-mlx/.gpu_lock` | a **directory** (`mkdir` = atomic acquire, `rmdir` = release) | the H3 tree |
| `/tmp/phosphene_gpu.lock` | a **file** (`set -o noclobber` + `>` = atomic create-or-fail) | the Phosphene agents |

**A lock only one party reads is not a lock.** Both exist, both are
live, and an agent that honours one of them will happily start a second
render next to an agent honouring the other. That is not hypothetical:
on 2026-08-05 two ~40 GiB jobs overlapped and the OS killed the panel
*and* its child mid-test.

Acquire in that order (directory first, then file; release in reverse),
so two agents racing cannot each hold one and deadlock. `gpu_lock.sh` in
the LTX-2.5 port working tree is the reference implementation.

**Staleness is decided by the lock's TIMESTAMP, never by a PID.**
Write the owner string into the file for humans to read, but do not
parse a pid out of it and do not ask whether that pid is alive:

- pids are recycled, so "that pid exists" is not "that render is
  running" — the crash-reap work already learned this the hard way and
  matches on the process **cmdline**, not the number alone;
- the holder may be a *different container of work* than the process
  that wrote the file (a driver script that spawns the render), so the
  writer can legitimately be gone while the GPU is still busy;
- and a pid check invites the failure it is meant to prevent: an agent
  that concludes "stale" and steals the lock starts the second render.

Use an age threshold longer than any render this repo can produce
(the longest measured is a 15 s H3 chain at ~27 min, so **60 minutes**
is the floor), announce that you are overriding, and say so in the
session record. If in doubt, **wait** — a blocked agent costs minutes,
a collided render costs the machine.

**Never touch `:8199`.** It is the owner's daemon. Do not kill it, do
not restart it, do not queue to it. Test instances go on **:8240+**
with isolated `LTX_STATE_DIR` / `LTX_OUTPUT_DIR` / `LTX_UPLOADS_DIR`,
and are killed by the PID you saved when you started them.

### Disk consolidation
- Old `~/Documents/Codex/...` dev folder was deleted.
  Don't recreate. The Pinokio install is canonical.
- `~/.cache/huggingface/hub/models--*` was also deleted. Pinokio
  install pulls weights into `mlx_models/` directly via `hf
  download --local-dir`, not the cache. Don't reintroduce HF cache
  symlinks.

## 8. Test workflow

### The gates — run these before pushing anything they cover

Each exists because the thing it guards broke in the field at least once,
and each is cheap enough that "I forgot" is not a reason.

| gate | run it when you touch | what it refuses |
|---|---|---|
| `node scripts/check_pinokio_scripts.js` | any launcher `.js` | a single dispatch over 500 chars — Pinokio 8.0.x wedges at 764 and at 1,417 |
| `node scripts/check_ltx_pin.js` | the vendored pin, `update.js`, `install.js`, `_LTX_EXPECTED_VERSION` | a pin that is a bare SHA, a pin that disagrees with the version the runtime reports, a second checkout implementation, or anything in `update.js` that belongs post-pull |
| `node scripts/check_post_update.js` | `scripts/post_update.sh` | the codec patch running before the reinstall or after anything optional, and a load-bearing step that cannot fail the Update |
| `./ltx-2-mlx/env/bin/python3.11 scripts/assert_registry.py` | `MODEL_VERSIONS`, `required_files.json`, pack paths, the text-encoder seam, deep-verify sources | a generation that resolves another generation's weights or text encoder — the two bugs that shipped silently on 2026-08-12, neither of which raised anything |
| `./ltx-2-mlx/env/bin/python3.11 scripts/assert_schedules.py` | `make_job`, any `*_steps` default, a new render mode, a pin move | a job dict that asks a checkpoint to PAD a fixed sigma table — the class that let Colorize/Restore/Ingredients/Control/HDR burn 218 s and die on "cannot thin a 9-point schedule (8 steps) up to 10 steps" |
| `./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py` | the vendored pin, any package reinstall | a bypassed codec patch — v3.8.1 shipped silent 4:2:0 for a whole release because this ran eleven steps too late and never executed |
| `python3 scripts/check_output_codec.py` | **every release (mandatory pre-promote)**; the vendored pin, `patch_ltx_codec.py`, the encode/upscale path | a PRODUCED mp4 whose pix_fmt is not what its sidecar/env requested, or missing the patched encoder's `+faststart` fingerprint — the render-level check the v3.8.1 class proved was missing (every static gate passed while all output was 4:2:0). The panel imports the same script for `/status.model_integrity.output_codec`, so every install self-reports it too |
|  `./ltx-2-mlx/env/bin/python3.11 test_character_roundtrip.py` | character defaults, the Characters submit, Load Params, `_CHARACTER_QUALITY_RESOLUTION`, `character_render_quality()`, a `character_quality` registry field | a job the panel can render but cannot reload — the two surfaces disagreeing on the strength pair, a Draft render reopening as Pro because Load Params never learned the new canvas, or a voice strength that is submitted and then silently dropped on restore — and a Characters submit whose form carries a different pipeline than the generation was graded on (the hardcoded `"quality": ["high"]` class that survived f65ea9b: the variable was fixed, the form three screens away was not; asserted per model version through the real `do_POST`) |

**Why that last one is a different kind of gate.** Every other row is
structural — dispatch length, step ordering, pin text, schema — and not one of
them could have caught the Character contract drifting, because the defect only
exists in the *round trip*: payload → sidecar → Load Params → payload. An
external review found three of those at once in v4.0.

**And it EXECUTES the panel's JavaScript.** Its first version asserted the
contract by grepping `mlx_ltx_panel.py` for the calls it hoped were there; all
twelve tests passed while the live loader was still broken and the slider
displayed a different number from the one being submitted. A test that passes
by finding strings is worse than no test, because it also reports the area as
covered. `scripts/extract_panel_js.py` pulls the real functions and the real
markup out of the panel and the suite runs them in node against a DOM shim.
Extraction *raises* on a renamed function, so coverage cannot quietly decay
back to grepping. If you add a field a sidecar records, assert it there.

**The codec rule, stated correctly.** It has been passed around as *"`grep -rn
libx264` in the vendored tree must return exactly one hit"*, and read literally
that is false — repo-wide the grep is 8 (the trainer's `video_utils` and
`slice_clips`, `media_io`, `video_vae`). What the rule actually asserts:

> `grep -rn --include="*.py" libx264 ltx-2-mlx/packages/ltx-core-mlx` returns
> **exactly one** hit — the UNPATCHED source at `video_vae.py:487` — while the
> installed copy in `ltx-2-mlx/env/lib/python3.11/site-packages/` is patched.
> `patch_ltx_codec.py` resolves its target through the INTERPRETER and verifies
> its own work, so its own exit code is the real gate; the grep is the
> human-readable cross-check that the source tree was left clean.

A second hit in `ltx-core-mlx` means the patch was applied to the git-tracked
source instead of site-packages — the editable-install failure that made every
v3.8.0 pin move fail.

`assert_registry.py` also **pins known defects**: it asserts the current
*wrong* value, prints a `DEFECT` banner every run, and turns RED when
somebody fixes it, so a fix cannot land without the marker being removed.
A pinned defect is not a pass — read the banners.

For HTML/UI/JS changes:
1. Edit `mlx_ltx_panel.py`
2. Restart panel (Pinokio Stop + Start, OR `pkill -f mlx_ltx_panel.py`
   then click Start in Pinokio)
3. Hard-refresh browser (Cmd+Shift+R)
4. Verify the served HTML contains the change:
   `python3 -c "import urllib.request; print('your-marker' in urllib.request.urlopen('http://127.0.0.1:8198/').read().decode())"`

For helper changes (Python pipeline, patches):
1. Edit `mlx_warm_helper.py` or `patch_ltx_codec.py`
2. If touching patches: re-run `./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py`
3. `curl -X POST http://127.0.0.1:8198/helper/restart`
4. Submit a test job via `curl -X POST http://127.0.0.1:8198/run -d "mode=t2v" -d "prompt=..." -d "width=512" -d "height=288" -d "frames=49" -d "steps=8" -d "quality=draft"`
5. Poll `/status` until `current` is null and verify the new
   `history[0]` entry is `done`.

For audio testing — do not use generic "wizard in forest" prompts.
Use prompts with explicit dialogue or sound events so artifacts are
audible:

```
A woman in her 30s with auburn hair, sitting at a wooden table in a
softly lit kitchen, speaks directly to the camera with a small smile
and clear, confident voice. She says "I really did not think I would
see you again, but here we are." Audio: clear human voice, slight
room reverb, gentle ambient kitchen sounds.
```

Measure with ffmpeg:
```
ffmpeg -i out.mp4 -af "volumedetect" -f null - 2>&1 | grep -E "max_volume|mean_volume"
```
Healthy levels: `max_volume` between -2 dB and -15 dB on dialogue
prompts. Below -25 dB peak suggests a regression.

## 9. Pinokio lifecycle scripts

| Script | Trigger | What it does |
|---|---|---|
| `install.js` | First Install or Resume Install | Clone `ltx-2-mlx`, force Python 3.11 venv, install MLX trio at pinned versions, install ltx-* packages, run `patch_ltx_codec.py`, `hf download` Q4 + Gemma. Idempotent. |
| `start.js` | Start button | Spawn panel inside the venv with `LTX_*` env vars set. Capture URL via regex with capture group, route to `local.set` via `input.event[1]`. |
| `update.js` | Update button | `git pull` panel + ltx-2-mlx, force-reinstall mlx pin, force-reinstall ltx-* packages, re-run patches. |
| `reset.js` | Reset button | Wipe venv + clones. **Preserves** `mlx_outputs/`, `panel_uploads/`, `mlx_models/` (those are user content / huge weight files). |
| `download_q8.js` | Download Q8 button | `hf download dgrauet/ltx-2.3-mlx-q8` to `mlx_models/ltx-2.3-mlx-q8`. |
| `pinokio.js` | Always | Renders the menu. State driven by `required_files.json` (env marker + per-repo file completeness). |

## 10. Files to never touch unprompted

- Anything inside `mlx_models/` — these are downloaded weights, GB-scale
- Anything inside `mlx_outputs/` — user-generated content
- Anything inside `panel_uploads/` — user uploads
- `panel_queue.json`, `panel_hidden.json` — runtime state
- `logs/`, `cache/`, `__pycache__/` — runtime caches
- `GEMINI.md`, `QWEN.md`, `AGENTS.md` are **symlinks to this `CLAUDE.md`**
  (consolidated 2026-05-31 — they used to be hand-maintained near-duplicate
  copies that silently drifted). `CLAUDE.md` is the single source of truth;
  the other three exist only so Gemini / Qwen / Codex(AGENTS) load the same
  guide. Edit `CLAUDE.md`; never edit the symlinks. If a tool ever refuses a
  symlinked instruction file, replace that one with a 5-line stub pointing
  here — do not fork the full content back out.

## 11. HTTP API reference — see `docs/API.md`

**There is one API reference and it is `docs/API.md`.** This section used to
carry a second copy of the endpoint tables, and a second copy is a copy that
drifts: it listed ~13 GET and ~20 POST routes and none of Storyboard, Editor,
Characters, Train or the Ideogram API, and it stated the port as `8198` flatly
while §7 states the rule correctly. Both of those were wrong in ways a reader
could not detect from inside this file.

The two things worth keeping here, because they are rules rather than tables:

- **The port follows the profile.** `DEFAULT_PORT = 8199 if PROFILE == "dev"
  else 8198` (in `mlx_ltx_panel.py`), overridable with `LTX_PORT`. A dev
  checkout answers on 8199 and shows the DEV badge; a normal install answers
  on 8198. Pinokio proxies its own port 42000 to the panel.
- **Everything is unauthenticated and loopback-only.** There is no auth layer
  to add a route to, so a route that should not be reachable must not exist.

## 11a. Analytics — it exists, it is ON, and it ships a key

- **Phosphene sends anonymous usage counts, on by default.** Do not write docs,
  release notes or UI copy that imply otherwise.
- **The shipped build carries a working PostHog project key** —
  `ANALYTICS_KEY_DEFAULT` in `mlx_ltx_panel.py`. Phosphene is distributed as
  source, so an empty key would mean no counts at all.
- **Counts and hardware classes only** — never prompts, filenames, paths, seeds,
  images, video, audio, or LoRA/character names. One random per-install UUID.
- **Off switch:** Settings → *Anonymous usage analytics*, or
  `PHOSPHENE_ANALYTICS_DISABLED=1`. Users can read everything sent in
  `state/usage-log.jsonl`.
- **The fleet view is not automatic:** the stats dashboard needs a PostHog
  personal API key in `analytics_query_key` (Settings), separate from the
  shipped project key.
- **`docs/ANALYTICS.md` is the complete contract** — every event, every field,
  every value. If the panel sends something not listed there, that is a bug.
  Change the code and that document together.

## 12. Job spec — the contract for `/run` and `/queue/add`

Every form field below maps to `params.<field>` on the resulting job.
`make_job()` in `mlx_ltx_panel.py` is the canonical reference (search for
`def make_job(` — it is around line 16145 and moves with every edit above it).

| Field | Type | Default | Meaning |
|---|---|---|---|
| `mode` | str | `"t2v"` | One of: `t2v`, `i2v`, `i2v_clean_audio`, `extend`, `keyframe`. |
| `prompt` | str | `"A cinematic atmospheric scene"` | Empty falls back to the default. |
| `width` | int | `1280` | Clamped to `>= 32`. T2V only — image flows derive from aspect+quality. |
| `height` | int | `704` | Same as width. |
| `frames` | int | `121` | Number of video frames. The 8k+1 rule (latents are groups of 8). |
| `steps` | int | `8` | Denoising steps. The T2V/I2V distilled lane always runs exactly `8` (fixed 9-point table): `clamp_distilled_steps()` rewrites any other value to 8 with a note (4.17.4 — it used to refuse). H3 reads its own counts from this field. |
| `seed` | int-as-str | `"-1"` | `"-1"` → random. Stored as string so the form roundtrip is lossless. |
| `image` | path | `REFERENCE` | Only used when `mode != "t2v"`. |
| `audio` | path | `AUDIO_DEFAULT` | Only used when `mode == "i2v_clean_audio"` (mux external audio). |
| `video_path` | path | `""` | **Required for `mode=extend`**. Source mp4 to extend. |
| `extend_frames` | int | `5` | Latent-frame count to add. Each latent = 8 video frames. So 5 latents = 40 frames ≈ 1.67s at 24 fps. |
| `extend_direction` | str | `"after"` | `"after"` or `"before"`. |
| `extend_steps` | int | `12` | Denoising steps for the extend pipeline (uses dev transformer). |
| `extend_cfg` | float | `1.0` | CFG scale. 1.0 = no guidance (faster, less RAM). 3.0 = guided (Quality mode). |
| `start_image` | path | `""` | **Required for `mode=keyframe`**. First frame. |
| `end_image` | path | `""` | **Required for `mode=keyframe`**. Last frame. |
| `enhance` | "on"/"off" | `"off"` | Reserved (panel uses the explicit `/prompt/enhance` button instead). |
| `stop_comfy` | "on"/"off" | `"off"` | Kill ComfyUI before this render to free MPS memory. |
| `open_when_done` | "on"/"off" | `"off"` | macOS `open` the output when finished. |
| `quality` | str | `"balanced"` | `"quick"`, `"balanced"`, `"standard"`, or `"high"`. Drives dimensions, step counts, and pipeline. |
| `accel` | str | `"off"` | `"off"` (alias for exact) / `"boost"` / `"turbo"`. Adaptive-cache denoise; boost skips up to 2 cached steps, turbo up to 3. T2V/I2V Standard only — disabled for High, Extend, FFLF. |
| `upscale` | str | `"off"` | `"off"` / `"fit_720p"` / `"x2"`. Post-render export. Balanced defaults to `fit_720p`. |
| `upscale_method` | str | `"lanczos"` | `"lanczos"` (ffmpeg) or `"pipersr"` (Sharp, ANE-accelerated). PiperSR adds ~26 s on 5-sec 1024×576. |
| `no_music` | "on"/"off" | `"off"` | When on, the panel appends a no-music audio constraint to the prompt at submit time. |
| `preset_label` | str | `""` | Optional human-readable label for the queue/history rows. |

## 13. Mode → pipeline → transformer routing

| Mode | Pipeline class | Transformer | Quality flag | Notes |
|---|---|---|---|---|
| `t2v` | `TextToVideoPipeline` | distilled Q4 | quick → smaller dims; balanced → 1024×576 + 720p export; standard → 1280×704; high → `TwoStageHQPipeline` w/ Q8 dev | Most common path. |
| `i2v` | `ImageToVideoPipeline` | distilled Q4 | same tier ladder as T2V | Adds VAE-encoder pass on the input image. |
| `i2v_clean_audio` | `ImageToVideoPipeline` | distilled Q4 | same | Generates LTX video, then panel-side ffmpeg mux replaces the LTX audio with `audio` param. |
| `extend` | `ExtendPipeline` | **Q8 dev** (Y1.036+) | always uses Q8 `transformer-dev.safetensors`; cfg_scale dial via `extend_cfg` | Heavier (~75–80 s/step on 768×416 cfg=1.0). Pre-Y1.024 a duplicate `transformer-dev.safetensors` shipped in the Q4 dir as download bloat — Extend silently loaded from there. Y1.024 download filter pruned the dupe and exposed that Extend is structurally Q8-class. Panel auto-routes to Q8 dir; UI gates on Q8 download (same shape as FFLF). |
| `keyframe` (FFLF) | `KeyframePipeline` | **Q8 dev + distilled LoRA** | requires Q8 download | Disabled on `base` tier. Comfortable tier clamps to 768 max-side (the stage-1 → stage-2 transition OOMs at 1280×704 on 64 GB). |

The helper `get_pipe(kind)` lazily loads the pipeline class corresponding
to the mode. Switching modes between jobs frees the previous pipeline
(`release_pipelines(keep_kind=...)`) — strict one-pipeline-at-a-time
policy keeps memory bounded.

## 14. Quality tier mapping

`quality` is independent of the hardware tier. Hardware tier *clamps*
the maximum dimensions per mode; `quality` picks the pipeline + step
count *within* that clamp.

| `quality` | Pipeline used | Dimensions | Steps | Approx wall on M4 Max 64 GB |
|---|---|---|---|---|
| `quick` | distilled Q4 | 640×480 (4:3 fast sanity) | 8 | ~ 2 m 14 s @ 121 frames |
| `balanced` | distilled Q4 + ffmpeg or PiperSR fit_720p export | 1024×576 → 1280×720 export | 8 | ~ 4 m 36 s (Lanczos) / ~ 5 m 02 s (Sharp) @ 121 frames |
| `standard` | distilled Q4 | 1280×704 (full target, clamped by tier) | 8 | ~ 7 m 40 s @ 121 frames; ~ 5 m 26 s with `accel=turbo` |
| `high` | `TwoStageHQPipeline` (Q8 dev + res_2s sampler + TeaCache) | 1280×704 | 15 stage-1 + 3 stage-2 | ~ 11 m 51 s @ 121 frames |

`high` and `keyframe` require the Q8 model on disk (~37 GB filtered).
`extend` also requires Q8 since Y1.036 (transformer-dev). Panel surfaces
a "Download Q8" CTA + disables the Generate button when the user picks
a Q8-required mode without Q8 installed.

Independent of `quality`, T2V/I2V Standard takes an `accel` flag
(`exact` / `boost` / `turbo`) that drives an adaptive-cache denoise
loop. Boost can skip up to 2 stable middle steps, Turbo up to 3.
Cached steps near-zero wall; full steps stay ~53 s/it on 1280×704.
Per-step decisions are recorded in the sidecar's `accel_metrics` block.

## 15. Hardware tier system

`SYSTEM_TIER` is detected once at boot from `sysctl hw.memsize`. The
override `LTX_TIER_OVERRIDE=base|standard|high|pro` forces a tier
(useful for testing what other users see). `CAPABILITIES` in
`mlx_ltx_panel.py:584` is the canonical table; abbreviated:

| Tier | RAM range | Friendly | t2v/i2v max dim | Q8 | FFLF | Extend |
|---|---|---|---|---|---|---|
| `base` | < 48 GB | Compact | 768 | ❌ | ❌ | ❌ |
| `standard` | 48–79 GB | Comfortable | 1280 | ✅ | ✅ | ✅ |
| `high` | 80–119 GB | Roomy | 1280 | ✅ | ✅ (more frames) | ✅ |
| `pro` | ≥ 120 GB | Studio | 0 (no clamp) | ✅ | ✅ | ✅ |

The thresholds are intentional. M-Studio 64 GB is the canonical
"standard" hardware. Anything below 48 GB cannot fit Q8 weights
(transformer-dev.safetensors is 19.18 GiB plus working tensors).

## 15a–17. Reference — see `docs/PANEL_REFERENCE.md`

`panel_settings.json` schema and `GET /settings` secrets contract, the LoRA system (`mlx_models/loras/`, `CURATED_LORAS`, CivitAI bridge), `required_files.json` schema, and the panel↔helper JSON protocol. Edit `required_files.json` first when the model layout changes; `GET /settings` never returns secret values.

## 18. Memory budget model

Concrete weight sizes (HuggingFace repo metadata, Q4 unless noted):

| Component | Size on disk | Resident peak (rough) | Notes |
|---|---|---|---|
| `transformer-distilled.safetensors` (Q4) | 10.54 GiB | ~12 GiB | T2V/I2V "DiT" |
| `transformer-dev.safetensors` (Q4) | 11.3 GiB (rough) | ~13 GiB | Used by Extend |
| `transformer-dev.safetensors` (Q8) | 19.18 GiB | ~22 GiB | High-quality + FFLF |
| `connector.safetensors` (Q4) | 5.91 GiB | ~6 GiB | Lives inside `feature_extractor` |
| `Gemma-3-12B-it-4bit` | 7.48 GiB | ~9 GiB | Text encoder, freed before DiT load |
| `vae_decoder.safetensors` | 814 MB | ~1 GiB | Used at the end of generation |
| `vae_encoder.safetensors` | 638 MB | ~800 MB | I2V only (encodes input image) |
| `audio_vae.safetensors` | 107 MB | ~150 MB | |
| `vocoder.safetensors` | 258 MB | ~350 MB | |

Peak during T2V Standard 1280×704 (with patches applied): roughly
DiT + active activations + decoder ≈ 20–22 GiB resident. Without
the patches it's 26+ GiB and OOMs on memory-pressured 64 GB Macs.

Peak during I2V is +800 MB for the encoder pass + ~6 GiB for the
connector that lingered in `feature_extractor` (Patches 3 + 4 fix
the lingering — see §5).

**The MLX allocator cache is part of that peak, and until 2026-08-28 it
was uncapped.** MLX does not return freed Metal buffers to the driver; it
keeps them, and its default ceiling for that cache is `0.95 * hw.memsize`
— a number about the machine, not the job. `mlx_warm_helper.py` now caps
it at **one eighth of physical RAM** (floor 2 GiB, ceiling 8 GiB: 16 GiB
Mac → 2, 32 → 4, 48 → 6, 64 and up → 8), applied at helper start and
re-asserted at the main-loop choke point before every render — the
pipelines' own low-memory paths call `set_cache_limit(0)` and never put it
back. Override with `LTX_MLX_CACHE_GIB` (a number in GiB, `0` for no cache,
`off` for MLX's default). It takes about a third off the peak footprint of
every render at no wall-clock cost, and `test_mlx_cache_policy.py` pins the
value at each tier. The numbers are in §4's mlx row.

macOS `jetsam` is the OOM killer. It enforces memory pressure based
on a percentage that varies with paging activity, swap usage, and other
heuristics — there is no fixed "kill at X% pressure" rule. Closing
Chrome, Slack, iOS Simulator before a Standard render is the safest
single thing a user can do.

## 19–20. Debug recipes and patch authoring — see `docs/DEBUG_RECIPES.md`

## 21. Glossary

- **LTX 2.3** — Lightricks' latent text-to-video diffusion model with
  joint audio synthesis. Trained on a large internal dataset.
- **MLX** — Apple's open-source ML array framework, native to Metal.
  Like JAX/PyTorch but built for Apple Silicon. Not a torch shim.
- **Q4 / Q8** — Quantization levels of the LTX 2.3 weights. Q4 is
  4-bit quantized (smaller, faster, slightly lower quality), Q8 is
  8-bit (larger, slower, sharper detail). Phosphene defaults to Q4;
  Q8 is an optional upgrade.
- **DiT** — Diffusion Transformer. The main denoising network. The
  largest single weight tensor.
- **VAE** — Variational Autoencoder. Encodes pixels ↔ latents. The
  encoder is used in I2V (encodes the input image); the decoder is
  used at the end of every generation.
- **FFLF** — First-Frame / Last-Frame interpolation. Provide two images,
  the model generates the motion between them. Requires Q8.
- **BWE** — Bandwidth Extension. Inside the audio vocoder, upsamples
  a low-rate signal to full audio rate (108 sequential BigVGAN convs).
- **Vocoder** — Converts mel-spectrogram features to PCM audio. The
  final stage of LTX's audio pipeline.
- **TeaCache** — A diffusion-step caching technique that skips
  redundant computation when consecutive sigmas are similar. Used in
  the High quality two-stage path.
- **jetsam** — macOS' kernel-level memory pressure killer. Sends SIGKILL
  to processes when system memory gets tight. The dominant cause of
  silent helper death on Macs at full RAM.
- **moov atom** — The MP4 metadata index. With `+faststart` it lives
  at the front of the file (allows progressive playback / instant
  thumbnails); without, it lives at the end (browser must download
  the whole file before showing the first frame).
- **Pinokio** — One-click installer + runtime for AI apps on Mac/Linux/Windows.
  Phosphene ships as a Pinokio app. cocktailpeanut authored Pinokio.
- **mrbizarro** — The GitHub handle that authors this repo. Use this,
  always, in commits and metadata.

## 22. Open questions & v1.1 candidates

Tracked here so they don't get lost between sessions.

- **The mlx 0.32.x move** — **CLOSED 2026-08-28, second refusal, with
  the mitigation tried.** The route was: (1) ship an MLX cache-limit
  policy on the current pin; (2) move mlx 0.32.1 + mflux 0.19.1 in one
  commit; (3) re-run the tier table. Step 1 SHIPPED and is a standalone
  win (one eighth of RAM: Q4 25.53 → 16.50 GB, Q8@48 GiB 39.49 → 25.39 GB,
  sha256-identical, wall unchanged or better). Step 3 then failed step 2:
  with the same policy on both versions, 0.32.1 is at a HIGHER share of
  physical RAM than 0.31.1 on three of four tiers (16 GiB 101→116%,
  48 GiB Q8 77→82%, 64 GiB 37→41%) and the 48 GiB Comfortable tier lands
  on the 82% line that forces the streamed decode. The cap cannot reach
  it: the excess is **active** memory, +12.0 GB on Q4 and +29.2 GB on Q8
  at an identical cap. Cache 0 gets it under and erases the speed win in
  the same move. So the pin holds at 0.31.1 and mflux at 0.18.0 — the
  mechanics are all proved and reversible (mflux 0.19.1's FBCache anchors
  applied cleanly to a real install; the post_update sequence resolves to
  mlx 0.32.1 / mflux 0.19.1 / mlx-lm 0.31.1 with nothing walked back), so
  a future attempt is a pin move plus this measurement, not a
  re-derivation. What would change the answer is an upstream fix to
  0.32.x's active-memory appetite, not another knob on our side. Full
  tables in the mlx row of §4 and in `install.js` above the pin. The old
  "bisect 0.31.1 → 0.31.2" task is retired — 0.32 fixed it upstream.
- **A2V (audio-to-video) mode** — `ltx-2-mlx` upstream has
  `a2vid_two_stage.py` and `a2vid_two_stage_hq.py` but the panel
  doesn't expose them. UI work + helper plumbing required.
- **In-app HF token field** — currently we recommend `hf auth login`
  in Terminal. A panel-side settings field that writes
  `~/.cache/huggingface/token` would be friendlier for non-CLI users.
- **Pre-flight RAM advisory** — before submitting a heavy job, warn
  if `s.memory.swap_gb > 8` or `pressure_pct > 75`. Suggest Draft.
- **Audio mode dropdown** — supersede the binary "No music" toggle
  with: With music / Voice + ambient / Sound effects / Silent. Each
  option appends/prepends a different prompt constraint.
- **Deeper helper breadcrumbs** — current `step:*` markers cover the
  outer call. Adding `image_encode_start`, `text_encode_done`,
  `denoise_start`, `decode_start` inside the pipeline (via wrapper
  patches) would localize silent deaths down to the phase.
- **Q8 path validation** — Q8 generation has not been end-to-end
  tested on a 64 GB Mac since the codec/I2V patches landed. Schedule
  a 25 GB Q8 download + High quality T2V test.
- **FFLF validation** — same as above. Requires Q8.

## 23. External references

- LTX-2 paper / model card: https://huggingface.co/Lightricks
- MLX framework: https://github.com/ml-explore/mlx
- `ltx-2-mlx` (the MLX port we wrap): https://github.com/dgrauet/ltx-2-mlx
- Pinokio runtime: https://pinokio.computer
- Q4 weights: `dgrauet/ltx-2.3-mlx-q4` on HF
- Q8 weights: `dgrauet/ltx-2.3-mlx-q8` on HF
- Gemma text encoder: `mlx-community/gemma-3-12b-it-4bit` on HF

## 24. Agentic Flows — REMOVED 2026-05-15

The in-panel chat agent and its `agent/` package are gone: the `/agent/*`
namespace answers 410 Gone, `agent/` is not on disk and neither is the
`docs/AGENTIC_FLOWS.md` this section used to point at. External agents drive
the panel over the HTTP API instead — `docs/API.md`.

This heading is kept rather than deleted because the section it replaces
survived three months of "read CLAUDE.md first" sessions, teaching every one
of them about a subsystem it could not find.


Reference material moved out of this file (read when relevant):

- `docs/PANEL_REFERENCE.md` — `panel_settings.json`, LoRA system, `required_files.json`, helper protocol
- `docs/DEBUG_RECIPES.md` — debug recipes, patch authoring template
- `docs/DEPENDENCY_PINS.md` — full mlx / ltx-2-mlx pin rationale and measurements
- `docs/HISTORY.md` — v3.0 history, 2.0 capabilities, benchmarks

The generic Pinokio scaffolding guide that used to follow here is the parent `Pinkio/CLAUDE.md`; it is not duplicated in this repo.

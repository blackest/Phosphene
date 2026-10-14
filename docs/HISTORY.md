# Phosphene history: v3.0 line, 2.0 capabilities, benchmarks

Moved out of CLAUDE.md §0. Historical; `docs/STATE.md` is current.

### Historical — the v3.0 line

Public release was **v3.0.6**. The v3.0 line launched 2026-05-23 and has
shipped six patch releases: v3.0.0 (Characters/Voice/Image Studio/A2V) →
v3.0.1 (FFLF crash fix) → v3.0.2 (Boost/Turbo accel restored after a
2-month silent regression) → v3.0.3 (HiDream hidden, issue #15) → v3.0.4
(CivitAI SSL cert) → v3.0.5 (A2V kwarg signature shim, #5) → v3.0.6
(deep-review hardening: CivitAI token-leak fix, dead-HDR revival,
GPU-contention guard, boot version-gate). Published git tags: `v2.0.0`–
`v2.0.5` then `v3.0.0`–`v3.0.6` — there is **no `v2.0.6` tag** (that was
the dev codename that became v3.0.0). The `VERSION` file should read
`3.0.6`. Versioning: `3.0.x` for patches. Pre-2.0 commit messages /
comments still use the `Y1.NNN` counter (Y1.001 → Y1.039) — historical;
do not introduce new `Y` labels.

The authoritative current engineering snapshot is `docs/STATE.md`
(current capabilities, version history, known bugs). The `## 2.0`
capability list
below is retained as historical baseline — everything in it shipped; the
v3.0 additions (Characters, voice LoRA, Image Studio, A2V, Train tab,
capability tiers) are now baseline too.

### What ships in 2.0 (capabilities checklist)

- **Four modes**: T2V, I2V, FFLF (keyframe interpolation), Extend.
- **Joint audio + video** in a single LTX 2.3 forward pass — sound is
  generated WITH the video. Lip-sync, footsteps, ambient noise all
  align at the frame level. mlx 0.31.1 pin is what keeps the vocoder
  output at healthy levels (-9 to -15 dB peak on dialogue prompts).
- **Quality tiers**: `quick` / `balanced` / `standard` / `high`. The
  earlier `draft` key was renamed; do not reintroduce it. `high` =
  Q8 two-stage HQ pipeline + TeaCache, requires Q8 download.
- **Speed modes** for Standard renders: `exact` (full sampler) /
  `boost` (skip up to 2 stable middle steps via adaptive caching) /
  `turbo` (skip up to 3). 23–34 % wall-time savings on stable scenes.
  Disabled for High, Extend, and FFLF where step-skipping breaks
  quality contracts.
- **Sharp upscale** = PiperSR (CoreML) on the Apple Neural Engine.
  Real x2 detail recovery before the ffmpeg fit/encode tail; ~26 s
  overhead on a 5 s × 1024×576 native. The earlier "model" upscale
  path (LTX latent x2) was retired in Y1.026; it remains hidden
  behind `LTX_ENABLE_MODEL_UPSCALE=1` for lab archaeology only.
- **VAE temporal streaming** — long clips no longer freeze at the
  final decode. Patch in `patch_ltx_codec.py` defaults to
  `LTX_VAE_STREAMING=auto`: streams when latent frames > 121 (≈ 5 s),
  full-decodes shorter clips. Helper-side env-var override keeps
  installs that haven't re-applied the patch on the right path too.
- **Filtered hf downloads** (Y1.024+): Q4 ≈ 20 GB, Q8 ≈ 37 GB,
  Gemma ≈ 7.5 GB, optional spatial upscaler ≈ 1 GB. Older builds
  pulled the full upstream repos and bloated to Q4=56 GB / Q8=82 GB
  by including duplicate transformer variants and unused upscalers.
  `update.js` deletes the dupes on existing installs.
- **Hardware-tier auto-config** (`base` / `standard` / `high` / `pro`)
  with realistic per-tier time estimates surfaced on the Quality
  pills. Compact-tier users see honest "~12 min" instead of M-Studio
  numbers.
- **Now-card progress bar** (Y1.039) — phase-aware, denoise-step-aware.
  Computed server-side from history bucket + helper log signals.
  Replaces the old `elapsed / global_avg` ratio that mis-paced both
  Quick and High renders.
- **Gallery race-free** (Y1.039) — `list_outputs()` skips files that
  ffmpeg is still writing (in-flight skip + 2 s mtime cutoff), and
  the `/file` URL carries an `&v=<mtime>` cache-bust so the browser
  fetches the freshest bytes when a file is replaced.
- **CivitAI LoRA browser** built into the panel. `mlx_models/loras/`
  is scanned at boot; sidecar JSON metadata travels with each safetensors.
- **Phosphene 2.0 release badge** — violet `2.0` chip next to the
  wordmark in the panel header. Distinct from the amber `DEV` profile
  badge so dev/prod tabs are distinguishable at a glance.

### Empirical benchmarks (M4 Max 64 GB, "Comfortable" tier)

All numbers are real wall-clock from sidecar `elapsed_sec` on Y1.039 /
v2.0.0 runs today. Output mp4 carries audio in every case.

| Config | Frames | Wall |
|---|---|---|
| T2V Quick · exact · 640×480 | 121 (5 s) | ~ 2 m 14 s |
| T2V Standard · exact · 1280×704 | 121 (5 s) | ~ 7 m 40 s |
| T2V Standard · boost · 1280×704 | 121 (5 s) | ~ 6 m 20 s |
| T2V Standard · turbo · 1280×704 | 121 (5 s) | ~ 5 m 26 s |
| T2V High Q8 · 1280×704 | 121 (5 s) | ~ 11 m 51 s |
| T2V Balanced · turbo · 720p Sharp | 121 (5 s) | **~ 3 m 30 s** |
| T2V Balanced · turbo · 720p Sharp | 241 (10 s) | **~ 8 m 07 s** |
| I2V Standard · exact · 1280×704 | 121 (5 s) | ~ 7 m 51 s |
| I2V Balanced · turbo · 720p Sharp | 121 (5 s) | ~ 3 m 37 s |
| I2V Balanced · turbo · 720p Sharp | 241 (10 s) | ~ 8 m 26 s |
| FFLF · clamped to 768 px (Comfortable cap) | 121 (5 s) | ~ 5 m 29 s |
| Extend · 768 px (Comfortable cap), +3 s | source 121 → output 184 | ~ 15 m 50 s |

The marquee number for the launch thread is the
**Balanced + Turbo + 720p Sharp** recipe: 5-sec clip in 3 m 30 s, 10-sec
in 8 m 07 s. That's the recommended default for "fast HD with audio".


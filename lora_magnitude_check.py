#!/usr/bin/env python3
"""Approximate LoRA "did training actually do anything" magnitude check.

Not the official mrbizarro tool - this is a fan-made reconstruction of the
same idea he described in the Phosphene GitHub issue (character LoRA
training producing files that attach cleanly but carry no usable signal).
It measures the size of the weight delta each LoRA actually encodes,
independent of whether it "attaches" cleanly (attachment was already proven
fine by lora_compat.py's key-matching check).

Metric: for every lora_A/lora_B-style module pair, compute the delta matrix
    delta = B @ A * (alpha / rank)
and take its per-element RMS magnitude (sqrt(mean(delta**2))), NOT a raw
Frobenius norm - a Frobenius norm scales with matrix size, which makes
different-shaped modules incomparable. RMS-per-element does not.

This is NOT calibrated to match mrbizarro's own published thresholds - his
exact normalization (likely relative to base-weight norm) isn't public.
Treat this as a RELATIVE ranking within one batch of files, not an absolute
health certificate. A same-dataset, preset-only comparison (e.g. Quick vs
High on identical images) is the strongest use of this tool, since it holds
everything except the training settings constant.

Usage:
    python3 lora_magnitude_check.py mlx_models/loras/
    python3 lora_magnitude_check.py mlx_models/loras/*.safetensors
    python3 lora_magnitude_check.py --json mlx_models/loras/ > report.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("ERROR: numpy is required. pip install numpy --break-system-packages", file=sys.stderr)
    sys.exit(1)

try:
    from safetensors import safe_open
except ImportError:
    print("ERROR: safetensors is required. pip install safetensors --break-system-packages", file=sys.stderr)
    sys.exit(1)

# Prefer a torch backend if available - it decodes bf16 natively, which the
# numpy safetensors backend cannot. Falls back to numpy (bf16 files will be
# skipped with a clear error rather than crashing the whole batch).
try:
    import torch
    _HAVE_TORCH = True
except ImportError:
    _HAVE_TORCH = False


# Different trainers use different LoRA key suffixes. We try each convention
# in turn per file. (A, B) here always means "the down/rank-reducing matrix"
# and "the up/rank-expanding matrix" respectively, matching lora_A/lora_B.
KEY_CONVENTIONS: list[tuple[str, str]] = [
    (".lora_A.weight", ".lora_B.weight"),        # diffusers / LTX native
    (".lora_down.weight", ".lora_up.weight"),    # kohya / ComfyUI style
    (".lora.down.weight", ".lora.up.weight"),    # dotted variant
    (".lora_A", ".lora_B"),                      # no trailing .weight
    (".lora_down", ".lora_up"),
]


@dataclass
class LoraMagnitudeReport:
    path: str
    module_count: int
    median_delta_norm: float
    mean_delta_norm: float
    min_delta_norm: float
    max_delta_norm: float
    alpha_over_rank: float | None
    verdict: str
    error: str | None = None


def _read_metadata_scale(path: Path) -> float | None:
    """Try to recover alpha/rank scaling from safetensors metadata, if present."""
    try:
        with safe_open(str(path), framework="numpy") as f:
            meta = f.metadata() or {}
    except Exception:
        return None
    alpha = meta.get("lora_alpha") or meta.get("alpha")
    rank = meta.get("lora_rank") or meta.get("rank") or meta.get("r")
    try:
        if alpha is not None and rank is not None:
            alpha = float(alpha)
            rank = float(rank)
            if rank > 0:
                return alpha / rank
    except (TypeError, ValueError):
        pass
    return None


def _get_tensor_as_f32_numpy(f, key: str) -> np.ndarray:
    """Fetch one tensor as float32 numpy, handling bf16 via torch if needed."""
    try:
        arr = f.get_tensor(key)
    except Exception:
        raise
    # safetensors numpy backend raises on bf16 access for some versions and
    # returns a raw view for others; normalise everything through torch when
    # available since it always decodes bf16 correctly.
    if isinstance(arr, np.ndarray):
        return arr.astype(np.float32)
    if _HAVE_TORCH and hasattr(arr, "to"):
        return arr.to(torch.float32).numpy()
    raise TypeError(f"Unrecognised tensor type for key {key}: {type(arr)}")


def _open_backend(path: Path):
    """Return (backend_name, context) - tries torch first for bf16 safety."""
    if _HAVE_TORCH:
        return "pt"
    return "numpy"


def measure_lora(path: Path) -> LoraMagnitudeReport:
    backend = _open_backend(path)
    try:
        with safe_open(str(path), framework=backend) as f:
            keys = list(f.keys())

            paired: list[str] = []
            a_lookup: dict[str, str] = {}
            b_lookup: dict[str, str] = {}
            used_convention = None
            for a_suffix, b_suffix in KEY_CONVENTIONS:
                a_modules = {k[: -len(a_suffix)]: k for k in keys if k.endswith(a_suffix)}
                b_modules = {k[: -len(b_suffix)]: k for k in keys if k.endswith(b_suffix)}
                common = sorted(set(a_modules) & set(b_modules))
                if common:
                    paired = common
                    a_lookup = a_modules
                    b_lookup = b_modules
                    used_convention = (a_suffix, b_suffix)
                    break

            if not paired:
                sample_keys = ", ".join(keys[:4])
                return LoraMagnitudeReport(
                    path=str(path),
                    module_count=0,
                    median_delta_norm=0.0,
                    mean_delta_norm=0.0,
                    min_delta_norm=0.0,
                    max_delta_norm=0.0,
                    alpha_over_rank=None,
                    verdict="NO_LORA_PAIRS",
                    error=f"No recognised A/B key pairs. Sample keys: {sample_keys}",
                )

            scale = _read_metadata_scale(path)
            if scale is None:
                scale = 1.0

            per_element_norms = []
            for module in paired:
                a = _get_tensor_as_f32_numpy(f, a_lookup[module])
                b = _get_tensor_as_f32_numpy(f, b_lookup[module])
                try:
                    delta = b @ a
                except ValueError:
                    delta = (a.T @ b.T).T
                delta = delta * scale
                # Per-element RMS magnitude, not raw Frobenius norm - this is
                # size-invariant across modules of different shapes, which a
                # raw Frobenius norm is not (bigger matrix = bigger norm even
                # at identical per-element magnitude).
                rms = float(np.sqrt(np.mean(np.square(delta))))
                per_element_norms.append(rms)

            norms = per_element_norms
            median = statistics.median(norms)
            mean = statistics.fmean(norms)

            # NOTE: these thresholds are NOT calibrated to mrbizarro's published
            # numbers - his metric's exact normalization (likely relative to
            # base-weight norm, which this script does not load) is unknown.
            # Verdicts below are relative labels for sorting your own batch,
            # not an absolute health certificate. Trust the RANKING, re-derive
            # the labels once the official tool ships.
            verdict = "SEE_RANKING"

            return LoraMagnitudeReport(
                path=str(path),
                module_count=len(paired),
                median_delta_norm=median,
                mean_delta_norm=mean,
                min_delta_norm=min(norms),
                max_delta_norm=max(norms),
                alpha_over_rank=scale,
                verdict=verdict,
            )
    except Exception as exc:  # noqa: BLE001 - report and move on, don't crash the batch
        return LoraMagnitudeReport(
            path=str(path),
            module_count=0,
            median_delta_norm=0.0,
            mean_delta_norm=0.0,
            min_delta_norm=0.0,
            max_delta_norm=0.0,
            alpha_over_rank=None,
            verdict="ERROR",
            error=str(exc),
        )


def collect_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.safetensors")))
        elif p.is_file():
            paths.append(p)
        else:
            # allow shell-unexpanded globs on some setups
            paths.extend(sorted(Path(".").glob(arg)))
    # de-dupe, keep order
    seen = set()
    out = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="LoRA .safetensors files, or a directory to scan")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="SUBSTRING",
        help="Case-insensitive filename substring to omit from output entirely "
        "(repeatable). Useful for leaving NSFW-named or otherwise private "
        "files out of a table you're about to share publicly.",
    )
    args = parser.parse_args()

    files = collect_paths(args.paths)
    if not files:
        print("No .safetensors files found for the given path(s).", file=sys.stderr)
        sys.exit(1)

    if args.exclude:
        needles = [e.lower() for e in args.exclude]
        before = len(files)
        files = [f for f in files if not any(n in f.name.lower() for n in needles)]
        skipped = before - len(files)
        if skipped and not args.json:
            print(f"(excluded {skipped} file(s) matching --exclude filters)", file=sys.stderr)

    reports = [measure_lora(p) for p in files]

    if args.json:
        print(json.dumps([asdict(r) for r in reports], indent=2))
        return

    errored = [r for r in reports if r.error]
    measured = [r for r in reports if not r.error]
    measured.sort(key=lambda r: r.median_delta_norm)

    print(f"\nScanned {len(reports)} file(s): {len(measured)} measured, {len(errored)} could not be read.\n")

    if measured:
        print(f"{'file':45s}  {'median RMS/elem':>16s}  {'mean RMS/elem':>16s}  {'range':>26s}  {'mods':>4s}")
        print("-" * 120)
        for r in measured:
            print(
                f"{Path(r.path).name:45s}  "
                f"{r.median_delta_norm:16.4e}  "
                f"{r.mean_delta_norm:16.4e}  "
                f"[{r.min_delta_norm:.2e}, {r.max_delta_norm:.2e}]".rjust(26) + "  "
                f"{r.module_count:4d}"
            )
        print(
            "\nSorted weakest -> strongest by median per-element RMS delta magnitude.\n"
            "This is a RELATIVE ranking within your own batch, not calibrated to an\n"
            "absolute 'healthy' threshold - the official tool's exact normalization\n"
            "(likely relative to base-transformer weight norm) is not reproduced here.\n"
            "\n"
            "What to look for:\n"
            "  - A cluster of files sitting orders of magnitude below the rest is the\n"
            "    signature from the GitHub thread: trained-but-inert adapters.\n"
            "  - If your Phosphene-trained files (gdd94_v2, bizarrotrn_v2, gcz97_v2, etc.)\n"
            "    sit in the same range as your CivitAI files, they're probably fine and\n"
            "    the bug is elsewhere. If they sit far below, that's the training-magnitude\n"
            "    bug from the issue thread.\n"
        )

    if errored:
        print(f"\n{len(errored)} file(s) could not be measured:\n")
        for r in errored:
            print(f"  {Path(r.path).name}: {r.error}")
        print(
            "\n'No recognised A/B key pairs' files may be a different architecture entirely\n"
            "(Qwen-Image, SDXL, Flux, etc. from CivitAI) rather than a broken LTX LoRA -\n"
            "worth checking what each of those was actually trained for before assuming\n"
            "anything is wrong with them.\n"
        )


if __name__ == "__main__":
    main()

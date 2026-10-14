"""Upscale ×2 above QHD is refused before it renders (PR #103, @blackest).

A 3072×1792 output exhausted 96 GB of RAM plus swap (2026-10-12). The limit is
QHD's pixels in EITHER orientation (a portrait 1440×2560 is the same memory as
2560×1440), judged on the canvas the lane really renders, and enforced at every
door: the one-click Upscale & Face Fix, /queue/add, and the worker itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as p                                             # noqa: E402
from panel import routes_queue                                        # noqa: E402


@pytest.fixture
def big_mac(monkeypatch):
    # a tier with no i2v size cap (every Q8-capable tier): the ×2 is a full ×2
    monkeypatch.setattr(p, "tier_max_dim", lambda kind: 0)


def test_landscape_up_to_qhd_is_allowed(big_mac):
    assert p.upscale_output_refusal(1280, 720) is None
    assert p.upscale_output_refusal(1024, 576) is None


def test_portrait_qhd_is_the_same_memory_and_is_allowed(big_mac):
    # 720×1280 → 1408×2560 used to be refused because 2560 > 1440 (height)
    assert p.upscale_output_refusal(720, 1280) is None
    assert p.upscale_output_refusal(576, 1024) is None


def test_a_square_under_qhds_pixels_is_allowed(big_mac):
    # Codex 4.19.2: 768x768 -> 1536x1536 is 2.4 MP, under QHD's 3.7 MP
    assert p.upscale_output_refusal(768, 768) is None
    assert p.upscale_output_refusal(1024, 1024)                      # 2048x2048 = 4.2 MP


def test_above_qhd_is_refused_in_either_orientation(big_mac):
    msg = p.upscale_output_refusal(1536, 896)                        # → 3072×1792, the OOM
    assert msg and "3072×1792" in msg and "2560×1440" in msg and "1280×720" in msg
    msg = p.upscale_output_refusal(896, 1536)
    assert msg and "1792×3072" in msg and "1440×2560" in msg and "720×1280" in msg
    assert p.upscale_output_refusal(1920, 1080)                      # 1080p → 4K


def test_unknown_size_is_not_refused(big_mac):
    assert p.upscale_output_refusal(0, 0) is None
    assert p.upscale_output_refusal(None, "x") is None


def test_a_capped_tier_judges_the_canvas_it_really_renders(monkeypatch):
    # a tier whose cap keeps the output under QHD is not refused for a ×2 it never makes
    monkeypatch.setattr(p, "tier_max_dim", lambda kind: 2048)
    assert p.upscale_output_canvas(1536, 864) == p.ltx_floor_canvas(2048, 1152)
    assert p.upscale_output_refusal(1536, 864) is None


def test_one_click_face_fix_refuses_above_qhd(big_mac, monkeypatch, tmp_path):
    src = tmp_path / "big.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(p, "face_fix_adapter_ready", lambda: True)
    monkeypatch.setattr(p, "_probe_video_dims", lambda path: (1536, 896))
    monkeypatch.setattr(p, "_probe_video_frames", lambda path: 121)
    before = list(p.STATE["queue"])
    out = p.queue_face_fix(str(src))
    assert out["ok"] is False and out["code"] == "hardware_tier"
    assert "3072×1792" in out["error"]
    assert p.STATE["queue"] == before


class _FormH:
    def __init__(self, form):
        self.status, self.payload, self._form = None, None, form

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status

    def _read_form_body(self):
        return b"", self._form


def test_queue_add_refuses_an_upscale_above_qhd(big_mac, monkeypatch, tmp_path):
    src = tmp_path / "big.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(p, "_probe_video_dims", lambda path: (1536, 896))
    monkeypatch.setattr(p, "face_fix_adapter_ready", lambda: True)
    before = list(p.STATE["queue"])
    h = _FormH({"mode": ["upscale"], "engine": ["ltx"], "upscale_source_path": [str(src)]})
    routes_queue.post_run(h, "/queue/add", {}, "")
    assert h.status == 400 and h.payload["code"] == "hardware_tier", h.payload
    assert "3072×1792" in h.payload["error"]
    assert p.STATE["queue"] == before

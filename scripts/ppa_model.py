#!/usr/bin/env python3
"""RATIO-aware PPA model for the architecture selector.

The old tech/arch_db.yaml carried a single relative metric triple per
candidate (e.g. word_streaming: area 0.80). Those numbers were guesses and
were contradicted by synthesis. This module replaces them with a linear
cell-count model fitted to two measured points per architecture.

Measurement method (repeatable):

  python3 scripts/gen_ip.py --spec <spec> --out <out> --force
  yosys -p "read_verilog <filelist_sv2v.f>; hierarchy -top <design>
      -chparam <each SYNTH_PARAMETERS entry>; synth -top <design>; stat"

SYNTH_PARAMETERS from pnr/librelane.yaml supplies DATA_WIDTH, WORDS,
CAPTURE_MODE, OVERSAMPLE and CDR_MODE, so each variant is measured at its
real parameterisation rather than at the RTL defaults. Two RATIO points:
RATIO = 32 (8 words x 4 bits) and RATIO = 256 (32 words x 8 bits),
sky130 generic-gate mapping, 25 Mb/s.

Measured cells / DFF:

  tx parallel_mux            109 / 41   @32      582 / 268  @256
  tx word_group_capture      142 / 46   @32      626 / 277  @256
  tx word_streaming          308 / 44   @32     1977 / 272  @256
  rx fixed_oversampling OS4   218 / 67  @32      758 / 319  @256
  rx fixed_oversampling OS8   228 / 68  @32      767 / 320  @256
  rx adaptive_oversampling    847 / 115 @32     1382 / 367  @256
  rx source_sync              120 / 50  @32      656 / 302  @256

Scope and limits (do not overclaim):

* cells are Yosys generic gates, not PDK-mapped area. No dfflibmap / ABC /
  placement / routing was run. Use them for relative ranking only.
* rel_power is a clock-load proxy DFF x f_clock. It does not include
  combinational switching, glitching, leakage, clock-tree capacitance or I/O.
  It is the correct quantity for comparing oversampling factors, and is NOT
  a power measurement. Real power needs SAIF + report_power.
* rel_latency comes from the documented handshake behaviour, not from STA.
* confidence is modeled_yosys_cells -- never measured_post_route.
  A measured ppa_measured.yaml entry always overrides this model.
"""
from __future__ import annotations

# arch -> (cells_base, cells_per_ratio_bit, dff_base, dff_per_ratio_bit)
CELL_MODEL: dict[str, tuple[float, float, float, float]] = {
    "parallel_mux":           (41.4, 2.112,  8.6, 1.013),
    "word_group_capture":     (72.8, 2.161, 13.0, 1.031),
    "word_streaming":         (69.6, 7.451, 11.4, 1.018),
    "fixed_oversampling":    (140.8, 2.411, 31.0, 1.124),
    "fixed_oversampling_os2": (140.8, 2.411, 31.0, 1.124),
    "bang_bang_oversampling": (150.0, 2.42, 34.0, 1.124),
    "adaptive_oversampling": (770.7, 2.384, 79.0, 1.124),
    "source_sync":            (43.4, 2.393, 14.0, 1.124),
}

# Baseline each candidate is normalised against: (arch, oversample).
REFERENCE: dict[str, tuple[str, int]] = {
    "tx": ("parallel_mux", 1),
    "rx": ("fixed_oversampling", 4),
}

MODEL_SOURCE = "scripts/ppa_model.py (Yosys generic-gate counts, 8x4 + 32x8)"


def estimate(arch: str, ratio: int, oversample: int,
             line_rate_mbps: float, kind: str) -> dict[str, float]:
    """Return raw cell/DFF/clock estimates for one architecture."""
    if arch not in CELL_MODEL:
        raise KeyError(f"no cell model for architecture {arch!r}")
    cb, cp, db, dp = CELL_MODEL[arch]
    cells = cb + cp * max(1, int(ratio))
    dff = db + dp * max(1, int(ratio))
    # TX runs at the bit clock; RX runs at line_rate x OVERSAMPLE.
    clk_mhz = line_rate_mbps * (oversample if kind == "rx" else 1)
    return {
        "cells": cells,
        "dff": dff,
        "clock_mhz": clk_mhz,
        "clock_load": dff * clk_mhz,
    }


def relative_metrics(kind: str, arch: str, ratio: int, oversample: int,
                     line_rate_mbps: float, latency_factor: float = 1.0,
                     reference_latency: float = 1.0) -> dict[str, float]:
    """Return normalised area/power/latency metrics for scoring.

    Measured entries and explicit tech_overrides may supply their own
    metrics dict; call those first and only fall through to this model.
    """
    ref_arch, ref_os = REFERENCE[kind]
    ref = estimate(ref_arch, ratio, ref_os, line_rate_mbps, kind)
    cur = estimate(arch, ratio, oversample, line_rate_mbps, kind)
    return {
        "area": round(cur["cells"] / ref["cells"], 4),
        "power": round(cur["clock_load"] / ref["clock_load"], 4),
        "latency": round(latency_factor / reference_latency, 4),
    }

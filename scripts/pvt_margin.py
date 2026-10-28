#!/usr/bin/env python3
"""Map PVT LUT rows to behavioral eye/BER margin distributions.

This is a *behavioral sensitivity* pass, not an RTL or silicon BER claim:
each PVT row's TX rise time is converted to an equivalent RC tau (rise/ln4)
and fed into the existing stressed-eye/Gaussian-tail framework.  It answers
"which corners move the eye/ISI margin?" before spending RTL JTOL time.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analysis.ber import gaussian_ber  # noqa: E402
from verification.analysis.eye import eye_metrics, fold_eye  # noqa: E402
from verification.channel.channel_impulse import RCReflectionChannel, nrz_waveform  # noqa: E402
from verification.channel.jitter import build_tie  # noqa: E402
from verification.cli import load_profile  # noqa: E402
from verification.patterns.prbs import prbs_bits  # noqa: E402


def _summary(values: list[float]) -> dict[str, float]:
    arr = np.array(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {}
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        "p05": float(np.percentile(arr, 5.0)),
        "p50": float(np.percentile(arr, 50.0)),
        "p95": float(np.percentile(arr, 95.0)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def margin_for_row(row: dict[str, Any], profile: dict[str, Any], bits_n: int) -> dict[str, Any]:
    rise = row.get("tx_rise_ns")
    if rise is None or rise <= 0:
        return {"corner": row.get("corner"), "vdd_v": row.get("vdd_v"),
                "temperature_c": row.get("temperature_c"), "valid": False}
    ui_ns = 1e9 / float(profile["rate_mbps"])
    tau_ns = float(rise) / math.log(4.0)
    tau_ui = tau_ns / ui_ns
    bits = prbs_bits(profile["prbs_width"], bits_n)
    rng = np.random.default_rng(profile["seed"])
    tie, _ = build_tie(
        bits, profile["ui_s"], profile["rj_rms_ui"], profile["sj_peak_ui"],
        profile["sj_freq_hz"], profile["dcd_peak_ui"], rng=rng,
    )
    wave = nrz_waveform(bits, profile["sps"], tie_ui=tie / profile["ui_s"])
    channel = RCReflectionChannel(
        tau_ui=tau_ui, reflections=profile["channel"]["reflections"]
    )
    wave = channel.apply(wave, profile["sps"])
    if profile["noise_rms_v"] > 0.0:
        wave = wave + rng.normal(0.0, profile["noise_rms_v"], size=wave.size)
    metrics = eye_metrics(
        fold_eye(wave, profile["sps"]), threshold_v=profile["threshold_v"],
        sps=profile["sps"],
    )
    stat = gaussian_ber(
        wave, bits, profile["sps"], phases=[0.5],
        threshold_v=profile["threshold_v"], noise_rms_v=0.0,
        discard=profile["discard"],
    )
    return {
        "corner": row.get("corner"),
        "vdd_v": row.get("vdd_v"),
        "temperature_c": row.get("temperature_c"),
        "tx_rise_ns": float(rise),
        "tau_ui": float(tau_ui),
        "eye_width_ui": float(metrics["eye_width_ui"]),
        "eye_height_v": float(metrics["eye_height_v"]),
        "statistical_ber": float(stat["ber"][0]),
        "valid": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pvt", default=str(ROOT / "build/verify/sky130_pvt_lut.json"))
    ap.add_argument("--profile", default=str(ROOT / "verification/profiles/clean.yaml"))
    ap.add_argument("--bits", type=int, default=8000)
    ap.add_argument("--out", default=str(ROOT / "build/verify/pvt_margin.json"))
    args = ap.parse_args()
    data = json.loads(Path(args.pvt).read_text())
    profile = load_profile(args.profile)
    rows = [margin_for_row(r, profile, args.bits) for r in data.get("rows", [])]
    valid = [r for r in rows if r.get("valid")]
    out = {
        "pvt": data.get("pvt"),
        "profile": args.profile,
        "n_rows": len(rows),
        "n_valid": len(valid),
        "note": ("Behavioral eye/Gaussian-tail sensitivity from PVT TX rise; "
                 "not RTL BER, not silicon, not a yield claim."),
        "rows": rows,
        "summary": {
            key: _summary([r[key] for r in valid])
            for key in ("eye_width_ui", "eye_height_v", "statistical_ber", "tau_ui")
        },
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"rows: {len(rows)} valid: {len(valid)}")
    for key, summary in out["summary"].items():
        print(f"  {key}: p05={summary.get('p05'):.4g} "
              f"p50={summary.get('p50'):.4g} p95={summary.get('p95'):.4g}")
    print(f"wrote {path}")
    return 0 if len(valid) == len(rows) and rows else 1


if __name__ == "__main__":
    raise SystemExit(main())

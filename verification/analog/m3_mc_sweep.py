#!/usr/bin/env python3
"""PDK local-mismatch spot check through the RX PEX chains.

This uses the sky130 ngspice ``mc_mm_switch`` hook (per-device AGAUSS mismatch)
and a deterministic seed list.  It is a mismatch spot check, not a global
process Monte Carlo campaign: the PDK library does not wire a global-process
switch into these device models.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.m3_ber_sweep import wilson  # noqa: E402
from verification.analog.mixed_signal_e2e import run as run_pex  # noqa: E402

DEFAULT_PDK = Path("/home/yanlu/.ciel/ciel/sky130/versions/792ef67037d74c8c51811eab3ce23cf793491296")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tx-pex", default="build/verify/magic_pex/Inv_Transmitter.pex.spice")
    ap.add_argument("--fb-pex", default="build/verify/magic_pex/Resist_FB_INV.pex.spice")
    ap.add_argument("--par-pex", default="build/verify/analog_stdcell/rx_parallel_inv_macro.pex.spice")
    ap.add_argument("--pcell-pex", default="build/verify/pcell_rx_fb/rx_fb_pcell.pex.spice")
    ap.add_argument("--pdk-root", default=str(DEFAULT_PDK))
    ap.add_argument("--seeds", default="1,2,3,4")
    ap.add_argument("--cases", default="feedback,parallel,pcell_feedback")
    ap.add_argument("--rate-mbps", type=float, default=800.0)
    ap.add_argument("--bits", default="10110010101100101011001010110010")
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--r-ohm", type=float, default=100.0)
    ap.add_argument("--c-pf", type=float, default=16.0)
    ap.add_argument("--out", default="build/verify/m3_mc_sweep.json")
    args = ap.parse_args()

    pdk = Path(args.pdk_root)
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    bits = [int(c) for c in args.bits if c in "01"]
    selected = {x.strip() for x in args.cases.split(",") if x.strip()}
    calls = {"feedback": Path(args.fb_pex)}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "kind": "m3-pdk-local-mismatch-spot-check",
        "condition": {
            "rate_mbps": args.rate_mbps,
            "bits": len(bits),
            "corner": args.corner,
            "channel": {"r_ohm": args.r_ohm, "c_pf": args.c_pf},
            "mc_mode": "sky130 mc_mm_switch=1 (local mismatch only)",
        },
        "results": {},
        "note": ("Small seed spot check with identical bits; per-seed errors are "
                 "diagnostic and aggregate intervals are not production BER. "
                 "Global process variation is not enabled by this PDK model."),
    }
    with tempfile.TemporaryDirectory() as td:
        wrap = Path(td) / "parallel_wrap.spice"
        wrap.write_text(
            ".subckt Resist_FB_INV GND VDD INP OUT\n"
            "Xcore VDD GND INP OUT rx_parallel_inv_macro\n.ends\n"
            f'.include "{Path(args.par_pex).resolve()}"\n'
        )
        calls["parallel"] = wrap
        pcell = Path(args.pcell_pex)
        if pcell.is_file():
            pwrap = Path(td) / "pcell_wrap.spice"
            pwrap.write_text(
                ".subckt Resist_FB_INV GND VDD INP OUT\n"
                "Xcore VDD GND INP OUT rx_fb_pcell\n.ends\n"
                f'.include "{pcell.resolve()}"\n'
            )
            calls["pcell_feedback"] = pwrap
        calls = {k: v for k, v in calls.items() if k in selected}
        if not calls:
            raise SystemExit(f"no matching cases in {sorted(selected)}")

        for case, rx in calls.items():
            report["results"][case] = []
            for seed in seeds:
                try:
                    r = run_pex(Path(args.tx_pex), rx, bits, 1000.0 / args.rate_mbps,
                                pdk, Path(td) / f"{case}_mc{seed}", args.corner,
                                args.r_ohm, args.c_pf, mc_seed=seed)
                    entry = {
                        "mc_seed": seed,
                        "errors": r["errors"],
                        "bits": len(bits),
                        "ber": r["ber"],
                        "offset_ns": r["sample_offset_ns"],
                        "eye_width_ns": r.get("eye_width_ns"),
                        "eye_height_v": r.get("eye_height_v"),
                        "out_swing_v": [r["out_min_v"], r["out_max_v"]],
                    }
                except Exception as exc:  # noqa: BLE001
                    entry = {"mc_seed": seed, "error": str(exc)[-500:]}
                report["results"][case].append(entry)
                out.write_text(json.dumps(report, indent=2) + "\n")
                print(f"{case} seed={seed}: {entry.get('errors', 'ERROR')}", flush=True)

    for case, rows in list(report["results"].items()):
        valid = [r for r in rows if r.get("errors") is not None]
        total = sum(r["bits"] for r in valid)
        errors = sum(r["errors"] for r in valid)
        if total:
            report["results"][case + "_summary"] = {
                "runs": len(valid),
                "errors": errors,
                "bits": total,
                "ber": errors / total,
                "ber_ci95": list(wilson(errors, total)),
            }
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["results"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

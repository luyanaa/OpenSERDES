#!/usr/bin/env python3
"""Held-out M3 BER screening: train the sampling phase, then freeze it.

The training prefix and validation suffix use independent PRBS-7 start states
and are concatenated into one transient.  Only the training prefix selects the
open-loop sampling offset; the reported validation BER is measured on the
disjoint suffix with that offset frozen.  This is still not a CDR loop.
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


def prbs7(length: int, state: int) -> list[int]:
    out = []
    s = state & 0x7F
    for _ in range(length):
        out.append(s & 1)
        fb = ((s >> 6) ^ (s >> 5)) & 1
        s = ((s << 1) | fb) & 0x7F
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tx-pex", default="build/verify/magic_pex/Inv_Transmitter.pex.spice")
    ap.add_argument("--fb-pex", default="build/verify/magic_pex/Resist_FB_INV.pex.spice")
    ap.add_argument("--par-pex", default="build/verify/analog_stdcell/rx_parallel_inv_macro.pex.spice")
    ap.add_argument("--pcell-pex", default="build/verify/pcell_rx_fb/rx_fb_pcell.pex.spice")
    ap.add_argument("--pdk-root", default=str(DEFAULT_PDK))
    ap.add_argument("--rates", default="800")
    ap.add_argument("--train-bits", type=int, default=64)
    ap.add_argument("--val-bits", type=int, default=64)
    ap.add_argument("--train-seed", type=int, default=0x5D)
    ap.add_argument("--val-seed", type=int, default=0x33)
    ap.add_argument("--noise-mv", default="0")
    ap.add_argument("--noise-seeds", default="7")
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--r-ohm", type=float, default=100.0)
    ap.add_argument("--c-pf", type=float, default=16.0)
    ap.add_argument("--cases", default="feedback,parallel,pcell_feedback")
    ap.add_argument("--out", default="build/verify/m3_holdout.json")
    args = ap.parse_args()

    pdk = Path(args.pdk_root)
    rates = [float(x) for x in args.rates.split(",") if x.strip()]
    noises = [float(x) for x in args.noise_mv.split(",") if x.strip()]
    noise_seeds = [int(x) for x in args.noise_seeds.split(",") if x.strip()]
    selected = {x.strip() for x in args.cases.split(",") if x.strip()}
    train = prbs7(args.train_bits, args.train_seed)
    validate = prbs7(args.val_bits, args.val_seed)
    bits = train + validate

    calls = {"feedback": Path(args.fb_pex)}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "kind": "m3-heldout-phase-ber",
        "condition": {
            "rates_mbps": rates,
            "train_bits": len(train),
            "validation_bits": len(validate),
            "train_seed": args.train_seed,
            "validation_seed": args.val_seed,
            "noise_mv": noises,
            "noise_seeds": noise_seeds,
            "corner": args.corner,
            "channel": {"r_ohm": args.r_ohm, "c_pf": args.c_pf},
        },
        "results": {},
        "note": ("Phase is selected only on the training prefix, then frozen "
                 "for the independent validation suffix. Open-loop, short-frame "
                 "screening; intervals are diagnostic, not production BER."),
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
            for rate in rates:
                for noise in noises:
                    for seed in noise_seeds:
                        key = f"{case}_{rate:g}_{noise:g}_{seed}"
                        try:
                            r = run_pex(
                                Path(args.tx_pex), rx, bits, 1000.0 / rate, pdk,
                                Path(td) / key, args.corner, args.r_ohm,
                                args.c_pf, noise_mv=noise, noise_seed=seed,
                                phase_len=len(train),
                            )
                            lo, hi = wilson(r["validation_errors"], len(validate))
                            entry = {
                                "case": case,
                                "rate_mbps": rate,
                                "noise_mv": noise,
                                "noise_seed": seed,
                                "train_bits": len(train),
                                "validation_bits": len(validate),
                                "phase_offset_ns": r["sample_offset_ns"],
                                "train_best_errors": int(min(r["phase_errors"])),
                                "validation_errors": r["validation_errors"],
                                "validation_ber": r["validation_ber"],
                                "validation_ci95": [lo, hi],
                                "full_errors": r["errors"],
                                "eye_width_ns": r.get("eye_width_ns"),
                                "eye_height_v": r.get("eye_height_v"),
                                "out_swing_v": [r["out_min_v"], r["out_max_v"]],
                            }
                        except Exception as exc:  # noqa: BLE001
                            entry = {"case": case, "rate_mbps": rate,
                                     "noise_mv": noise, "noise_seed": seed,
                                     "error": str(exc)[-500:]}
                        report["results"][case].append(entry)
                        out.write_text(json.dumps(report, indent=2) + "\n")
                        print(f"{case} {rate:g} noise={noise:g} seed={seed}: "
                              f"val={entry.get('validation_errors', 'ERROR')}",
                              flush=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

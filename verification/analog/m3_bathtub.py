#!/usr/bin/env python3
"""Sampled-offset BER bathtub for the RX PEX routes.

This is a characterization scan over the same 19 sampling offsets used by
``mixed_signal_e2e.run``.  It is not a CDR loop or a full eye-mask measurement;
the offset grid is open-loop and the frame is short.
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
    ap.add_argument("--rates", default="800,1200")
    ap.add_argument("--bits", default="10110010101100101011001010110010")
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--r-ohm", type=float, default=100.0)
    ap.add_argument("--c-pf", type=float, default=16.0)
    ap.add_argument("--cases", default="feedback,parallel,pcell_feedback")
    ap.add_argument("--out", default="build/verify/m3_bathtub.json")
    args = ap.parse_args()

    pdk = Path(args.pdk_root)
    rates = [float(x) for x in args.rates.split(",") if x.strip()]
    bits = [int(c) for c in args.bits if c in "01"]
    selected = {x.strip() for x in args.cases.split(",") if x.strip()}
    calls = {"feedback": Path(args.fb_pex)}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "kind": "m3-sampled-offset-bathtub",
        "bits": len(bits),
        "corner": args.corner,
        "channel": {"r_ohm": args.r_ohm, "c_pf": args.c_pf},
        "results": {},
        "note": ("Open-loop sampled-offset bathtub; not a CDR loop or full eye mask. "
                 "Short frame and Wilson intervals are diagnostic."),
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
            for rate in rates:
                key = f"{case}_{rate:g}"
                try:
                    r = run_pex(Path(args.tx_pex), rx, bits, 1000.0 / rate, pdk,
                                Path(td) / key, args.corner, args.r_ohm, args.c_pf)
                    points = []
                    for offset, errors in zip(r["offset_scan_ns"], r["offset_errors"]):
                        lo, hi = wilson(errors, len(bits))
                        points.append({
                            "offset_ns": offset,
                            "errors": errors,
                            "ber": errors / len(bits),
                            "ber_ci95": [lo, hi],
                        })
                    entry = {
                        "case": case,
                        "rate_mbps": rate,
                        "bits": len(bits),
                        "best_errors": r["errors"],
                        "best_offset_ns": r["sample_offset_ns"],
                        "eye_width_ns": r.get("eye_width_ns"),
                        "eye_height_v": r.get("eye_height_v"),
                        "bathtub": points,
                    }
                except Exception as exc:  # noqa: BLE001
                    entry = {"case": case, "rate_mbps": rate,
                             "error": str(exc)[-500:]}
                report["results"][key] = entry
                out.write_text(json.dumps(report, indent=2) + "\n")
                print(f"{case} {rate:g} Mb/s: best {entry.get('best_errors', 'ERROR')}", flush=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

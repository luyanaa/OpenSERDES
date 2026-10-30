#!/usr/bin/env python3
"""Voltage/temperature spot sweeps through the RX PEX chains.

This is a characterization screening run, not a full signoff PVT matrix.  For
voltage points the input swing follows VDD; for temperature points the input
swing stays at the nominal 1.8 V.
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
    ap.add_argument("--mode", choices=("voltage", "temp", "both"), default="both")
    ap.add_argument("--vdds", default="1.62,1.98")
    ap.add_argument("--temps", default="-40,125")
    ap.add_argument("--base-vdd", type=float, default=1.8)
    ap.add_argument("--base-temp", type=float, default=25.0)
    ap.add_argument("--rate-mbps", type=float, default=800.0)
    ap.add_argument("--bits", default="10110010101100101011001010110010")
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--r-ohm", type=float, default=100.0)
    ap.add_argument("--c-pf", type=float, default=16.0)
    ap.add_argument("--cases", default="feedback,parallel,pcell_feedback")
    ap.add_argument("--out", default="build/verify/m3_pvt_sweep.json")
    args = ap.parse_args()

    pdk = Path(args.pdk_root)
    bits = [int(c) for c in args.bits if c in "01"]
    selected = {x.strip() for x in args.cases.split(",") if x.strip()}
    vdds = [float(x) for x in args.vdds.split(",") if x.strip()]
    temps = [float(x) for x in args.temps.split(",") if x.strip()]
    conditions: list[tuple[float, float]] = []
    if args.mode in ("voltage", "both"):
        conditions += [(v, args.base_temp) for v in vdds]
    if args.mode in ("temp", "both"):
        conditions += [(args.base_vdd, t) for t in temps]
    # De-duplicate the nominal intersection when both modes are requested.
    conditions = list(dict.fromkeys(conditions))

    calls = {"feedback": Path(args.fb_pex)}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "kind": "m3-voltage-temperature-spot-sweep",
        "condition": {
            "rate_mbps": args.rate_mbps,
            "bits": len(bits),
            "corner": args.corner,
            "channel": {"r_ohm": args.r_ohm, "c_pf": args.c_pf},
            "input_swing": "VDD for voltage points; 1.8 V for temperature points",
        },
        "results": {},
        "note": "Small spot matrix; not a full PVT signoff or production BER claim.",
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
            for vdd, temp_c in conditions:
                key = f"{case}_v{vdd:g}_t{temp_c:g}"
                try:
                    r = run_pex(
                        Path(args.tx_pex), rx, bits, 1000.0 / args.rate_mbps, pdk,
                        Path(td) / key, args.corner, args.r_ohm, args.c_pf,
                        vin_high=vdd, vdd=vdd, temp_c=temp_c,
                    )
                    lo, hi = wilson(r["errors"], len(bits))
                    entry = {
                        "case": case,
                        "vdd_v": vdd,
                        "temp_c": temp_c,
                        "errors": r["errors"],
                        "bits": len(bits),
                        "ber": r["ber"],
                        "ber_ci95": [lo, hi],
                        "offset_ns": r["sample_offset_ns"],
                        "eye_width_ns": r.get("eye_width_ns"),
                        "eye_height_v": r.get("eye_height_v"),
                        "out_swing_v": [r["out_min_v"], r["out_max_v"]],
                    }
                except Exception as exc:  # noqa: BLE001
                    entry = {"case": case, "vdd_v": vdd, "temp_c": temp_c,
                             "error": str(exc)[-500:]}
                report["results"][case].append(entry)
                out.write_text(json.dumps(report, indent=2) + "\n")
                print(f"{case} v={vdd:g} t={temp_c:g}: {entry.get('errors', 'ERROR')}",
                      flush=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

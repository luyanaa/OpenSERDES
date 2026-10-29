#!/usr/bin/env python3
"""Same-channel BER rate sweep for the two RX PEX routes."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.mixed_signal_e2e import run as run_pex  # noqa: E402
from verification.analog.magic_pex import find_pdk_root  # noqa: E402

DEFAULT_PDK = Path("/home/yanlu/.ciel/ciel/sky130/versions/792ef67037d74c8c51811eab3ce23cf793491296")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx-pex", default="build/verify/magic_pex/Inv_Transmitter.pex.spice")
    ap.add_argument("--fb-pex", default="build/verify/magic_pex/Resist_FB_INV.pex.spice")
    ap.add_argument("--par-pex", default="build/verify/analog_stdcell/rx_parallel_inv_macro.pex.spice")
    ap.add_argument("--pdk-root", default=str(DEFAULT_PDK))
    ap.add_argument("--rates", default="25,100,200,400,800,1200,1600")
    ap.add_argument("--bits", default="10110010101100101011001010110010")
    ap.add_argument("--r-ohm", type=float, default=100.0)
    ap.add_argument("--c-pf", type=float, default=16.0)
    ap.add_argument("--out", default="build/verify/m3_ber_sweep.json")
    args = ap.parse_args()
    pdk = Path(args.pdk_root)
    rates = [float(x) for x in args.rates.split(",") if x.strip()]
    bits = [int(c) for c in args.bits if c in "01"]
    calls = {"feedback": Path(args.fb_pex)}
    with tempfile.TemporaryDirectory() as td:
        wrap = Path(td) / "parallel_wrap.spice"
        wrap.write_text(
            ".subckt Resist_FB_INV GND VDD INP OUT\n"
            "Xcore VDD GND INP OUT rx_parallel_inv_macro\n.ends\n"
            f'.include "{Path(args.par_pex).resolve()}"\n'
        )
        calls["parallel"] = wrap
        results = {}
        for case, rx in calls.items():
            results[case] = []
            for rate in rates:
                try:
                    r = run_pex(Path(args.tx_pex), rx, bits, 1000.0 / rate, pdk,
                                Path(td) / f"{case}_{rate}", "tt",
                                args.r_ohm, args.c_pf)
                    results[case].append({
                        "rate_mbps": rate,
                        "errors": r["errors"],
                        "bits": len(bits),
                        "ber": r["ber"],
                        "offset_ns": r["sample_offset_ns"],
                        "out_swing_v": [r["out_min_v"], r["out_max_v"]],
                    })
                except Exception as exc:  # noqa: BLE001
                    results[case].append({"rate_mbps": rate, "error": str(exc)[-500:]})
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"kind": "m3-ber-rate-sweep", "bits": bits,
              "channel": {"r_ohm": args.r_ohm, "c_pf": args.c_pf},
              "results": results,
              "note": "Same TX PEX, channel, bits and sampling scan; tt only, small-N."}
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Same-channel noise spot check for the three RX PEX routes."""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx-pex", default="build/verify/magic_pex/Inv_Transmitter.pex.spice")
    ap.add_argument("--fb-pex", default="build/verify/magic_pex/Resist_FB_INV.pex.spice")
    ap.add_argument("--par-pex", default="build/verify/analog_stdcell/rx_parallel_inv_macro.pex.spice")
    ap.add_argument("--pcell-pex", default="build/verify/pcell_rx_fb/rx_fb_pcell.pex.spice")
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--rates", default="800,1200")
    ap.add_argument("--noise-mv", default="0,50,100")
    ap.add_argument("--bits", default="10110010101100101011001010110010")
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--out", default="build/verify/m3_noise_sweep.json")
    args = ap.parse_args()
    pdk = find_pdk_root(args.pdk_root)
    rates = [float(v) for v in args.rates.split(",") if v.strip()]
    noises = [float(v) for v in args.noise_mv.split(",") if v.strip()]
    bits = [int(c) for c in args.bits if c in "01"]
    calls = {"feedback": Path(args.fb_pex)}
    with tempfile.TemporaryDirectory() as td:
        par = Path(td) / "parallel_wrap.spice"
        par.write_text(
            ".subckt Resist_FB_INV GND VDD INP OUT\n"
            "Xcore VDD GND INP OUT rx_parallel_inv_macro\n.ends\n"
            f'.include "{Path(args.par_pex).resolve()}"\n'
        )
        calls["parallel"] = par
        pcell = Path(args.pcell_pex)
        if pcell.is_file():
            p = Path(td) / "pcell_wrap.spice"
            p.write_text(
                ".subckt Resist_FB_INV GND VDD INP OUT\n"
                "Xcore VDD GND INP OUT rx_fb_pcell\n.ends\n"
                f'.include "{pcell.resolve()}"\n'
            )
            calls["pcell_feedback"] = p
        results = []
        for noise_mv in noises:
            for rate in rates:
                for case, rx in calls.items():
                    try:
                        r = run_pex(Path(args.tx_pex), rx, bits, 1000.0 / rate, pdk,
                                    Path(td) / f"{case}_{rate}_{noise_mv}", args.corner,
                                    100.0, 16.0, 1.8, noise_mv, 7)
                        results.append({
                            "case": case, "rate_mbps": rate, "noise_mv": noise_mv,
                            "errors": r["errors"], "bits": len(bits), "ber": r["ber"],
                            "out_swing_v": [r["out_min_v"], r["out_max_v"]],
                        })
                    except Exception as exc:  # noqa: BLE001
                        results.append({"case": case, "rate_mbps": rate,
                                        "noise_mv": noise_mv, "error": str(exc)[-400:]})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "kind": "m3-noise-spot-check",
        "corner": args.corner,
        "channel": {"r_ohm": 100.0, "c_pf": 16.0},
        "noise_model": "deterministic Gaussian series voltage source, sigma in mV, seed 7",
        "results": results,
        "note": "Small-N noise spot check; not a production BER or noise-margin claim.",
    }, indent=2) + "\n")
    print(json.dumps({"results": len(results), "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

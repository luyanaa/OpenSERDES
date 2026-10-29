#!/usr/bin/env python3
"""Input-swing sensitivity spot check for the RX PEX routes."""
from __future__ import annotations
import argparse, json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from verification.analog.mixed_signal_e2e import run as run_pex
from verification.analog.magic_pex import find_pdk_root

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--tx-pex",default="build/verify/magic_pex/Inv_Transmitter.pex.spice")
    ap.add_argument("--fb-pex",default="build/verify/magic_pex/Resist_FB_INV.pex.spice")
    ap.add_argument("--par-pex",default="build/verify/analog_stdcell/rx_parallel_inv_macro.pex.spice")
    ap.add_argument("--pcell-pex",default="build/verify/pcell_rx_fb/rx_fb_pcell.pex.spice")
    ap.add_argument("--pdk-root",default=None)
    ap.add_argument("--rate",type=float,default=800.0)
    ap.add_argument("--vin-highs",default="0.6,0.9,1.2,1.5,1.8")
    ap.add_argument("--bits",default="10110010101100101011001010110010")
    ap.add_argument("--out",default="build/verify/m3_sensitivity.json")
    args=ap.parse_args(); pdk=find_pdk_root(args.pdk_root)
    highs=[float(v) for v in args.vin_highs.split(",") if v.strip()]
    bits=[int(c) for c in args.bits if c in "01"]
    calls={"feedback":Path(args.fb_pex)}
    with tempfile.TemporaryDirectory() as td:
        par=Path(td)/"parallel_wrap.spice"; par.write_text(
            ".subckt Resist_FB_INV GND VDD INP OUT\nXcore VDD GND INP OUT rx_parallel_inv_macro\n.ends\n"
            f'.include "{Path(args.par_pex).resolve()}"\n'); calls["parallel"]=par
        pc=Path(args.pcell_pex)
        if pc.is_file():
            p=Path(td)/"pcell_wrap.spice"; p.write_text(
                ".subckt Resist_FB_INV GND VDD INP OUT\nXcore VDD GND INP OUT rx_fb_pcell\n.ends\n"
                f'.include "{pc.resolve()}"\n'); calls["pcell_feedback"]=p
        results=[]
        for vh in highs:
            for case,rx in calls.items():
                try:
                    r=run_pex(Path(args.tx_pex),rx,bits,1000.0/args.rate,pdk,
                              Path(td)/f"{case}_{vh}", "tt",100.0,16.0,vh,0.0,7)
                    results.append({"case":case,"vin_high_v":vh,"errors":r["errors"],
                                    "ber":r["ber"],"eye_height_v":r.get("eye_height_v"),
                                    "out_swing_v":[r["out_min_v"],r["out_max_v"]]})
                except Exception as exc:  # noqa: BLE001
                    results.append({"case":case,"vin_high_v":vh,"error":str(exc)[-400:]})
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({"kind":"m3-sensitivity-spot-check","rate_mbps":args.rate,
        "bits":bits,"results":results,
        "note":"Small-N sensitivity screening; not a production sensitivity or BER claim."},indent=2)+"\n")
    print(json.dumps({"results":len(results),"out":str(out)})); return 0
if __name__=="__main__": raise SystemExit(main())

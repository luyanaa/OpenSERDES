#!/usr/bin/env python3
"""M3 RX comparison: parallel stdcell inverter vs resistive-FB PMOS.

The comparison uses the packaged canonical SPICE views and the same supply,
input stimulus and load.  It measures DC trip/swing and transient drive/current.
It deliberately labels BER/eye/PVT/MC as not measured unless a common channel
and model library are supplied.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_one(canonical: Path, top: str, work: Path, name: str) -> dict:
    common = f".include \"{canonical.as_posix()}\"\nVDD vdd 0 1.8\nVSS vss 0 0\nXDUT vdd vss in out {top}\nCLOAD out 0 10f\n"
    dc = work / f"{name}_dc.sp"
    dc.write_text(common + "VIN in 0 0\n.dc VIN 0 1.8 0.01\n.meas dc vout_mid WHEN v(out)=0.9\n.meas dc vout_low FIND v(out) AT=0.1\n.meas dc vout_high FIND v(out) AT=1.7\n.end\n")
    tr = work / f"{name}_tran.sp"
    tr.write_text(common + "VIN in 0 PULSE(0 1.8 1n 50p 50p 20n 40n)\n.tran 10p 40n\n.meas tran vhigh MAX v(out) FROM=5n TO=15n\n.meas tran vlow MIN v(out) FROM=25n TO=35n\n.meas tran ivdd AVG i(VDD) FROM=0 TO=40n\n.end\n")
    logs = []
    def run(tb: Path) -> str:
        proc = subprocess.run(["ngspice", "-b", tb.name], cwd=work,
                              text=True, capture_output=True, timeout=180)
        logs.append(proc.stdout + proc.stderr)
        return proc.stdout + proc.stderr
    text = run(dc) + "\n" + run(tr)
    def meas(key: str) -> float | None:
        m = re.search(rf"{key}\s*=\s*([-+0-9.eE]+)", text)
        return float(m.group(1)) if m else None
    return {
        "name": name,
        "top": top,
        "returncode": 0 if "Error" not in text else 1,
        "dc_trip_v": meas("vout_mid"),
        "dc_low_v": meas("vout_low"),
        "dc_high_v": meas("vout_high"),
        "tran_high_v": meas("vhigh"),
        "tran_low_v": meas("vlow"),
        "avg_supply_current_a": abs(meas("ivdd") or 0.0),
        "power_uw": abs(meas("ivdd") or 0.0) * 1.8 * 1e6,
        "output_swing_v": abs((meas("vhigh") or 0.0) - (meas("vlow") or 0.0)),
        "log_tail": text[-1200:],
    }

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="build/verify/m3_rx_compare.json")
    args = ap.parse_args()
    if shutil.which("ngspice") is None:
        raise SystemExit("ngspice is required")
    from verification.analog.macro_package import stage
    with tempfile.TemporaryDirectory() as td:
        pkg = Path(td) / "pkg"; pkg.mkdir(); (pkg / "manifest.json").write_text('{"design":"m3"}')
        stage(pkg, pkg / "analog")
        run = pkg / "run"; shutil.copytree(pkg / "analog", run)
        def area(name: str):
            try:
                v = json.loads((ROOT / f"analog/macros/{name}/views.json").read_text())
                w, h = v.get("size_um", [0, 0])
                return round(float(w) * float(h), 4)
            except Exception:
                return None
        results = {
            "kind": "m3-rx-comparison",
            "condition": {"vdd_v": 1.8, "load_ff": 10.0,
                          "input": "0..1.8 V DC and 40 ns pulse",
                          "models": "package level-1 smoke models"},
            "rx_parallel_inv_macro": {**run_one(run / "rx_parallel_inv_macro/canonical.spice",
                                             "rx_parallel_inv_macro", run, "rx_parallel"),
                                      "area_um2": area("rx_parallel_inv_macro")},
            "rx_res_fb_pmos": {**run_one(run / "rx_res_fb_pmos/canonical.spice",
                                      "rx_res_fb_pmos", run, "rx_feedback"),
                               "area_um2": area("rx_res_fb_pmos")},
            "not_measured": ["BER", "eye margin", "input sensitivity BER",
                             "process/temperature corners", "Monte Carlo",
                             "same-channel noise"],
            "note": "Electrical smoke comparison only; no BER/PVT/MC claim.",
        }
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "log_tail"}
                          for k, v in results.items() if isinstance(v, dict)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

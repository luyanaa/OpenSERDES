#!/usr/bin/env python3
"""Characterize the RX resistive-feedback macro schematic vs Magic PEX.

This is a first independent macro-level evidence path: DC transfer, trip point,
small-signal slope and corner sweep for ``rx_res_fb_inv``.  It deliberately
does not invent a digital timing Liberty view or a linear-resistor equivalent.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root  # noqa: E402
from verification.analog.magic_pex_compare import convert_schematic  # noqa: E402


def _run(dut: Path, top: str, ports: list[str], corner: str, pdk: Path,
         workdir: Path, *, mc_mm: bool = False, seed: int = 0) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    wave = workdir / "dc.dat"
    tb = workdir / "dc.sp"
    lib = pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"
    order = ["vss", "vdd", "in", "out"]
    if [p.upper() for p in ports] != ["GND", "VDD", "INP", "OUT"]:
        raise ValueError(f"RX macro port order must be GND,VDD,INP,OUT; got {ports}")
    mc_line = ".param mc_mm_switch=1\n" if mc_mm else ".param mc_mm_switch=0\n"
    control_head = ".control\n" + (f"set seed={seed}\n" if mc_mm else "")
    tb.write_text(f"""* RX feedback macro DC
.lib "{lib}" {corner}
{mc_line}.param VDD=1.8
Vdd vdd 0 {{VDD}}
Vin in 0 0.9
.include "{dut.resolve()}"
Xdut {' '.join(order)} {top}
Cload out 0 20f
.dc Vin 0 1.8 0.05
{control_head}run
wrdata {wave.resolve()} v(in) v(out)
.endc
.end
""")
    proc = subprocess.run(["ngspice", "-b", str(tb)], text=True,
                          capture_output=True, timeout=240, cwd=workdir)
    if proc.returncode != 0 or not wave.is_file():
        raise RuntimeError(
            f"ngspice failed for {dut}: {proc.returncode}\n"
            f"{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}"
        )
    arr = np.loadtxt(wave)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    # wrdata writes (time,value) pairs for v(in), v(out).
    vin = arr[:, 1]
    vout = arr[:, 3]
    order_idx = np.argsort(vin)
    vin, vout = vin[order_idx], vout[order_idx]
    low = float(np.min(vout))
    high = float(np.max(vout))
    half = 0.9
    # Interpolate the first crossing of the mid-supply threshold.
    trip = None
    for i in range(1, len(vin)):
        if (vout[i - 1] - half) * (vout[i] - half) <= 0.0 and vout[i] != vout[i - 1]:
            trip = float(vin[i - 1] + (half - vout[i - 1]) *
                         (vin[i] - vin[i - 1]) / (vout[i] - vout[i - 1]))
            break
    # Local slope around the 0.9 V point.
    slope = None
    if vin[-1] > 0.91 and vin[0] < 0.89:
        slope = float((np.interp(0.91, vin, vout) - np.interp(0.89, vin, vout))
                      / 0.02)
    return {
        "dut": str(dut),
        "corner": corner,
        "vin_v": vin.tolist(),
        "vout_v": vout.tolist(),
        "vout_high_v": high,
        "vout_low_v": low,
        "trip_v": trip,
        "slope_v_per_v": slope,
        "inverting": bool(vout[0] > vout[-1]),
        "returncode": proc.returncode,
        "wave": str(wave),
        "mc_mm": bool(mc_mm),
        "seed": int(seed),
    }


def _run_ac(dut: Path, top: str, ports: list[str], corner: str, pdk: Path,
            workdir: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    wave = workdir / "ac.dat"
    tb = workdir / "ac.sp"
    lib = pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"
    order = ["vss", "vdd", "in", "out"]
    tb.write_text(f"""* RX feedback macro AC
.lib "{lib}" {corner}
.param VDD=1.8
Vdd vdd 0 {{VDD}}
Vin in 0 DC 0.9 AC 1
.include "{dut.resolve()}"
Xdut {' '.join(order)} {top}
Cload out 0 20f
.ac dec 20 1 1e9
.control
run
wrdata {wave.resolve()} v(out)
.endc
.end
""")
    proc = subprocess.run(["ngspice", "-b", str(tb)], text=True,
                          capture_output=True, timeout=240, cwd=workdir)
    if proc.returncode != 0 or not wave.is_file():
        raise RuntimeError(f"ngspice AC failed\n{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}")
    arr = np.loadtxt(wave)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    f = arr[:, 0]
    gain = np.sqrt(arr[:, 1] ** 2 + (arr[:, 2] if arr.shape[1] > 2 else 0.0) ** 2)
    gain_db = 20.0 * np.log10(np.maximum(gain, 1e-30))
    def at(freq: float) -> float:
        return float(np.interp(freq, f, gain_db))
    # Last crossing of the low-frequency gain - 3 dB.
    dc = at(1.0)
    below = np.where(gain_db < dc - 3.0)[0]
    bw = float(f[below[0]]) if below.size else None
    return {"dut": str(dut), "corner": corner,
            "gain_db_1hz": dc, "gain_db_1mhz": at(1e6),
            "gain_db_10mhz": at(1e7), "bandwidth_3db_hz": bw,
            "returncode": proc.returncode, "wave": str(wave)}


def _compare(pex: dict[str, Any], sch: dict[str, Any]) -> dict[str, Any]:
    pv = np.asarray(pex["vin_v"])
    sv = np.asarray(sch["vin_v"])
    lo = max(float(pv[0]), float(sv[0]))
    hi = min(float(pv[-1]), float(sv[-1]))
    if hi <= lo:
        return {}
    grid = np.linspace(lo, hi, max(len(pv), len(sv)))
    a = np.interp(grid, pv, pex["vout_v"])
    b = np.interp(grid, sv, sch["vout_v"])
    return {
        "max_abs_v": float(np.max(np.abs(a - b))),
        "rms_v": float(np.sqrt(np.mean((a - b) ** 2))),
        "trip_delta_v": (None if pex["trip_v"] is None or sch["trip_v"] is None
                         else float(pex["trip_v"] - sch["trip_v"])),
        "slope_delta": (None if pex["slope_v_per_v"] is None or sch["slope_v_per_v"] is None
                        else float(pex["slope_v_per_v"] - sch["slope_v_per_v"])),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    for key in ("trip_v", "slope_v_per_v", "vout_high_v", "vout_low_v"):
        vals = np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
        if vals.size:
            out[key] = {
                "mean": float(vals.mean()),
                "std": float(vals.std(ddof=1)) if vals.size > 1 else 0.0,
                "min": float(vals.min()),
                "p05": float(np.percentile(vals, 5.0)),
                "p50": float(np.percentile(vals, 50.0)),
                "p95": float(np.percentile(vals, 95.0)),
                "max": float(vals.max()),
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pex", default=str(ROOT / "build/verify/magic_pex/Resist_FB_INV.pex.spice"))
    ap.add_argument("--schematic", default=str(ROOT / "Resistive_FB_inverter/Resist_FB_INV.sp"))
    ap.add_argument("--top", default="Resist_FB_INV")
    ap.add_argument("--ports", default="GND,VDD,INP,OUT")
    ap.add_argument("--corners", nargs="*", default=["tt"])
    ap.add_argument("--mc-samples", type=int, default=0)
    ap.add_argument("--seed-start", type=int, default=1)
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--out", default=str(ROOT / "build/verify/rx_macro_dc.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build/verify/rx_macro_dc"))
    args = ap.parse_args()
    if not Path(args.pex).is_file():
        print(f"error: RX Magic PEX not found: {args.pex}; run "
              "`python3 verification/analog/magic_pex.py ...`", file=sys.stderr)
        return 2
    pdk = find_pdk_root(args.pdk_root)
    work = Path(args.workdir)
    sch = convert_schematic(Path(args.schematic), work / "rx_schematic.spice")
    ports = [p.strip() for p in args.ports.split(",") if p.strip()]
    rows = []
    for corner in args.corners:
        cdir = work / corner
        pex = _run(Path(args.pex), args.top, ports, corner, pdk, cdir / "pex")
        s = _run(Path(sch["out"]), args.top, ports, corner, pdk, cdir / "schematic")
        rows.append({"corner": corner, "pex": pex, "schematic": s,
                     "delta": _compare(pex, s)})
    ac = None
    if rows:
        first = rows[0]["corner"]
        ac = {
            "pex": _run_ac(Path(args.pex), args.top, ports, first, pdk, work / first / "pex_ac"),
            "schematic": _run_ac(Path(sch["out"]), args.top, ports, first, pdk, work / first / "sch_ac"),
        }
    mc_rows: list[dict[str, Any]] = []
    if args.mc_samples > 0:
        for i in range(args.mc_samples):
            seed = args.seed_start + i
            mc_rows.append(_run(
                Path(args.pex), args.top, ports, "tt", pdk,
                work / "mc" / f"{seed:04d}", mc_mm=True, seed=seed,
            ))
    report = {
        "kind": "rx-res-fb-dc",
        "pex": str(args.pex),
        "schematic": str(sch["source"]),
        "top": args.top,
        "ports": ports,
        "corners": list(args.corners),
        "rows": rows,
        "ac": ac,
        "mc": {
            "samples": args.mc_samples,
            "seed_start": args.seed_start,
            "rows": mc_rows,
            "summary": _summary(mc_rows),
            "note": "ngspice mc_mm_switch=1 local mismatch only; not global process.",
        },
        "note": ("DC transfer/slope comparison only; no AC/noise/PVT-MC signoff. "
                 "Feedback is nonlinear PMOS, not a linear resistor."),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    for row in rows:
        print(f"{row['corner']}: pex trip={row['pex']['trip_v']} "
              f"slope={row['pex']['slope_v_per_v']} "
              f"delta={row['delta']}")
    if ac:
        print(f"ac: pex={ac['pex']['gain_db_1mhz']} dB @1MHz, "
              f"bw={ac['pex']['bandwidth_3db_hz']} Hz")
    if mc_rows:
        print(f"mc: {_summary(mc_rows)}")
    print(f"wrote {out}")
    return 0 if all(r["inverting"] for row in rows for r in (row["pex"], row["schematic"])) else 1


if __name__ == "__main__":
    raise SystemExit(main())

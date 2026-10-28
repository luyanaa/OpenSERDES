#!/usr/bin/env python3
"""Compare a Magic-extracted PEX netlist with the matching schematic.

The schematic is the original physical-macro SPICE source, with legacy
``nshort``/``pshort`` MOS calls converted to the sky130 ngspice device
subcircuits.  The PEX side is the Magic RC extraction from the matching GDS.
Both sides are driven by the same generated testbench and measured with the
same ngspice invocation.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root  # noqa: E402

VDD = 1.8


def convert_schematic(src: Path, out: Path) -> dict[str, Any]:
    """Convert legacy nshort/pshort M cards to sky130 subckt instances."""
    lines = src.read_text().splitlines()
    body: list[str] = []
    mos = 0
    for line in lines:
        if re.match(r"^\s*\.(include|inc)\b", line, re.I):
            continue
        m = re.match(
            r"^M(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(nshort|pshort)\b(.*)$",
            line,
        )
        if m:
            name, d, g, s, b, typ, rest = m.groups()
            model = "sky130_fd_pr__nfet_01v8" if typ == "nshort" else "sky130_fd_pr__pfet_01v8"
            lm = re.search(r"\bL=([0-9.eE+-]+)", rest)
            wm = re.search(r"\bW=([0-9.eE+-]+)", rest)
            l = lm.group(1) if lm else "0.15"
            w = wm.group(1) if wm else "1"
            body.append(f"X{name} {d} {g} {s} {b} {model} l={l} w={w}")
            mos += 1
        elif line.startswith("X"):
            # Keep real child instances, including the pfet_CDNS helper.
            # Drop only schematic-only parasitic/diode pseudo-instances.
            if "$[" in line or "nwdiode" in line:
                continue
            body.append(line.split("$T=")[0].rstrip())
        elif re.match(r"^\s*\.end\s*$", line, re.I):
            continue
        else:
            body.append(line)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"* converted schematic {src}\n" + "\n".join(body) + "\n"
    )
    return {"source": str(src), "out": str(out), "mos": mos}


def testbench(dut: Path, top: str, mode: str, ports: list[str],
              wave: Path, workdir: Path, corner: str, pdk: Path,
              load_ff: float) -> Path:
    model_lib = pdk / "sky130A" / "libs.tech" / "ngspice" / "sky130.lib.spice"
    if not model_lib.is_file():
        raise FileNotFoundError(model_lib)
    if mode == "tx":
        order = ["vss", "vdd", "in", "out"]
        if [p.upper() for p in ports] != ["GND", "VDD", "VIN", "VOUT"]:
            raise ValueError(f"TX port order must be GND,VDD,VIN,VOUT, got {ports}")
        drive = f"""Vin in 0 PULSE(0 {{VDD}} 0.2n 50p 50p 2n 4n)
Xdut {' '.join(order)} {top}
Cload out 0 {load_ff}f
"""
        meas = f""".meas tran vmax max v(out)
.meas tran vmin min v(out)
.meas tran trise trig v(out) val={VDD*0.2} rise=1 targ v(out) val={VDD*0.8} rise=1
.meas tran tfall trig v(out) val={VDD*0.8} fall=1 targ v(out) val={VDD*0.2} fall=1
"""
        save = "v(out)"
    elif mode == "rx":
        order = ["vss", "vdd", "in", "clk", "out_b", "out"]
        if [p.upper() for p in ports] != ["GND", "VDD", "INP", "CLK", "OUT_B", "OUT"]:
            raise ValueError(
                "RX port order must be GND,VDD,INP,CLK,OUT_B,OUT, "
                f"got {ports}"
            )
        # Clocked receiver: one data-high window and one data-low window so
        # both the rising and falling clock-to-output paths are exercised.
        drive = f"""Vin in 0 PWL(0 0 0.1n 0 0.11n {{VDD}} 2.1n {{VDD}} 2.11n 0 4n 0)
Vclk clk 0 PULSE(0 {{VDD}} 0.5n 20p 20p 1n 2n)
Xdut {' '.join(order)} {top}
Cload out 0 {load_ff}f
Cloadb out_b 0 {load_ff}f
"""
        meas = f""".meas tran tco_rise trig v(clk) val={VDD*0.5} rise=1 targ v(out) val={VDD*0.5} rise=1
.meas tran tco_fall trig v(clk) val={VDD*0.5} rise=2 targ v(out) val={VDD*0.5} fall=1
.meas tran tco_b_rise trig v(clk) val={VDD*0.5} rise=1 targ v(out_b) val={VDD*0.5} rise=1
.meas tran tco_b_fall trig v(clk) val={VDD*0.5} rise=2 targ v(out_b) val={VDD*0.5} fall=1
"""
        save = "v(out) v(out_b) v(in) v(clk)"
    else:
        raise ValueError(mode)
    text = f"""* generated PEX/schematic comparison
.lib "{model_lib}" {corner}
.param VDD={VDD}
Vdd vdd 0 {{VDD}}
.include "{dut.resolve()}"
{drive}.tran 0.5p 4n
{meas}.control
run
wrdata {wave.resolve()} {save}
.endc
.end
"""
    path = workdir / f"tb_{mode}.sp"
    path.write_text(text)
    return path


def _read_waves(path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    if not path.is_file() or path.stat().st_size == 0:
        return np.array([]), {}
    arr = np.loadtxt(path)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.shape[1] < 2:
        return np.array([]), {}
    # ngspice wrdata writes (time, value) pairs for every requested vector.
    # A single vector therefore has 2 columns; four vectors have 8.  Keep the
    # first time base and align each value to it.
    if arr.shape[1] == 2:
        return arr[:, 0], {"out": arr[:, 1]}
    if arr.shape[1] % 2 != 0:
        return arr[:, 0], {"out": arr[:, 1]}
    names = ["out", "out_b", "in", "clk"]
    time = arr[:, 0]
    waves: dict[str, np.ndarray] = {}
    for idx, name in enumerate(names):
        tcol = 2 * idx
        vcol = tcol + 1
        if vcol >= arr.shape[1]:
            break
        if tcol != 0 and not np.allclose(arr[:, tcol], time, rtol=0.0, atol=0.0):
            # Keep a vector only if its time base matches; otherwise the
            # wrdata layout was different and a silent misalignment would be
            # worse than omitting it.
            continue
        waves[name] = arr[:, vcol]
    return time, waves


def _measure(text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key in ("vmax", "vmin", "trise", "tfall", "tphl", "tplh",
                "tco_rise", "tco_fall", "tco_b_rise", "tco_b_fall"):
        m = re.search(rf"^{key}\s*=\s*([-+0-9.eE]+)", text, re.M)
        out[key] = float(m.group(1)) if m else None
    return out


def run_one(dut: Path, top: str, mode: str, ports: list[str], workdir: Path,
            corner: str, pdk: Path, load_ff: float) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    wave = workdir / "wave.dat"
    tb = testbench(dut, top, mode, ports, wave, workdir, corner, pdk, load_ff)
    proc = subprocess.run(
        ["ngspice", "-b", str(tb)], text=True, capture_output=True,
        timeout=600, cwd=workdir,
    )
    waves_t, waves = _read_waves(wave)
    result: dict[str, Any] = {
        "dut": str(dut),
        "returncode": proc.returncode,
        "measures": _measure(proc.stdout + proc.stderr),
        "wave_points": int(waves_t.size),
        "errors": [] if proc.returncode == 0 else [
            line for line in (proc.stdout + proc.stderr).splitlines()
            if "Error" in line or "error" in line
        ][:10],
    }
    if waves_t.size:
        np.savez(workdir / "waves.npz", time=waves_t, **waves)
    return result


def compare_waves(a: dict[str, np.ndarray], b: dict[str, np.ndarray],
                 ta: np.ndarray, tb: np.ndarray) -> dict[str, float | None]:
    if ta.size == 0 or tb.size == 0:
        return {}
    lo = max(float(ta[0]), float(tb[0]))
    hi = min(float(ta[-1]), float(tb[-1]))
    if hi <= lo:
        return {}
    grid = np.linspace(lo, hi, min(20000, max(ta.size, tb.size)))
    out: dict[str, float | None] = {}
    for name in ("out", "out_b"):
        if name not in a or name not in b:
            continue
        va = np.interp(grid, ta, a[name])
        vb = np.interp(grid, tb, b[name])
        d = va - vb
        out[f"{name}_max_abs_v"] = float(np.max(np.abs(d)))
        out[f"{name}_rms_v"] = float(np.sqrt(np.mean(d * d)))
        # Report a delay-aware comparison: the best cross-correlation lag and
        # the residual after shifting, so a pure RC delay is not counted as a
        # waveform-shape error.
        va0 = va - va.mean()
        vb0 = vb - vb.mean()
        denom = float(np.sqrt(np.sum(va0 * va0) * np.sum(vb0 * vb0)))
        if denom > 0:
            corr = np.correlate(va0, vb0, mode="full")
            idx = int(np.argmax(np.abs(corr)))
            lag_samples = idx - (vb0.size - 1)
            dt = float(grid[1] - grid[0]) if grid.size > 1 else 0.0
            lag = lag_samples * dt
            shifted = np.interp(grid - lag, grid, vb)
            out[f"{name}_best_lag_s"] = float(lag)
            out[f"{name}_shifted_rms_v"] = float(
                np.sqrt(np.mean((va - shifted) ** 2))
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pex", required=True)
    ap.add_argument("--schematic", required=True,
                    help="original macro SPICE source (.sp)")
    ap.add_argument("--top", required=True)
    ap.add_argument("--mode", choices=("tx", "rx"), required=True)
    ap.add_argument("--ports", required=True,
                    help="comma-separated top port order")
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--load-ff", type=float, default=50.0)
    ap.add_argument("--out", default=str(ROOT / "build" / "verify" / "magic_pex" / "compare.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build" / "verify" / "magic_pex" / "compare"))
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if shutil.which("ngspice") is None:
        print("error: ngspice is required", file=sys.stderr)
        return 2
    try:
        pdk = find_pdk_root(args.pdk_root)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ports = [p.strip() for p in args.ports.split(",") if p.strip()]
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    sch = convert_schematic(Path(args.schematic), work / "schematic_sky130.spice")
    if args.dry_run:
        print(f"pex={args.pex} schematic={sch['out']} top={args.top} ports={ports}")
        return 0
    pex = run_one(Path(args.pex), args.top, args.mode, ports, work / "pex",
                  args.corner, pdk, args.load_ff)
    sch_run = run_one(Path(sch["out"]), args.top, args.mode, ports,
                      work / "schematic", args.corner, pdk, args.load_ff)
    wave_data = {}
    for label, run in (("pex", pex), ("schematic", sch_run)):
        npz = work / label / "waves.npz"
        if npz.is_file():
            z = np.load(npz)
            wave_data[label] = (z["time"], {k: z[k] for k in z.files if k != "time"})
        else:
            wave_data[label] = (np.array([]), {})
    wave_delta = compare_waves(
        wave_data["pex"][1], wave_data["schematic"][1],
        wave_data["pex"][0], wave_data["schematic"][0],
    )
    report = {
        "kind": "magic-pex-vs-schematic",
        "mode": args.mode,
        "top": args.top,
        "ports": ports,
        "corner": args.corner,
        "vdd_v": VDD,
        "load_ff": args.load_ff,
        "schematic": sch,
        "pex": pex,
        "schematic_run": sch_run,
        "waveform_delta": wave_delta,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": ("Magic RC extraction vs converted schematic; same testbench "
                 "and PDK ngspice library. No DRC/LVS/signoff claim."),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")
    for label, run in (("pex", pex), ("schem", sch_run)):
        print(f"  {label}: rc={run['returncode']} measures={run['measures']}")
    if wave_delta:
        print(f"  waveform delta: {wave_delta}")
    return 0 if pex["returncode"] == 0 and sch_run["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

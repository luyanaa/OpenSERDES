#!/usr/bin/env python3
"""First mixed-signal PEX chain: TX PEX -> RC channel -> RX PEX.

This is deliberately a transistor/RC transient chain, not the digital CDR E2E.
It checks polarity and bit decisions after two inverting analog macros and a
simple external RC channel.  Digital recovery is a separate layer.

The TX/RX netlists must be Magic RC PEX (``make magic-pex`` /
``make analog-macro-pex``), not Calibre RVE.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root  # noqa: E402


def _pwl(bits: list[int], ui_ns: float, edge_ns: float = 0.08,
         high_v: float = 1.8) -> str:
    pts = []
    last = 0.0
    for i, bit in enumerate(bits):
        t = i * ui_ns
        pts.append(f"{t:.6g}n {0.0 if i == 0 else (high_v if bits[i-1] else 0.0):.6g}")
        # Add a finite edge at the beginning of each bit.
        v = high_v if bit else 0.0
        pts.append(f"{(t + edge_ns):.6g}n {v:.6g}")
        last = t + ui_ns
    pts.append(f"{last:.6g}n {high_v if bits[-1] else 0.0:.6g}")
    return " ".join(pts)


def run(tx: Path, rx: Path, bits: list[int], ui_ns: float, pdk: Path,
        workdir: Path, corner: str = "tt", r_ohm: float = 100.0,
        c_pf: float = 16.0, vin_high: float = 1.8,
        noise_mv: float = 0.0, noise_seed: int = 1,
        mc_seed: int | None = None,
        vdd: float = 1.8, temp_c: float | None = None,
        phase_len: int = 0) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    wave = workdir / "wave.dat"
    tb = workdir / "mixed.sp"
    lib = pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"
    total_ns = max(2.0, (len(bits) + 4) * ui_ns)
    noise = ""
    if noise_mv > 0.0:
        rng = random.Random(noise_seed)
        points = []
        t = 0.0
        while t <= total_ns:
            points.append(f"{t:.6g}n {rng.gauss(0.0, noise_mv * 1e-3):.6g}")
            t += max(0.1, ui_ns / 20.0)
        noise = f"Rch VOUT RXIN_R {r_ohm:g}\nVn RXIN_R RXIN PWL({' '.join(points)})\nCch RXIN 0 {c_pf:g}p\n"
    else:
        noise = f"Rch VOUT RXIN {r_ohm:g}\nCch RXIN 0 {c_pf:g}p\n"
    mc = ""
    if mc_seed is not None:
        mc = ".param mc_pr_switch=0\n.param mc_mm_switch=1\n"
    temp = f".temp {temp_c:g}\n" if temp_c is not None else ""
    tb.write_text(f"""* mixed-signal PEX chain
.lib "{lib}" {corner}
{mc}{temp}.param VDD={vdd:g}
Vdd VDD 0 {{VDD}}
Vin VIN 0 PWL({_pwl(bits, ui_ns, high_v=vin_high)})
.include "{tx.resolve()}"
.include "{rx.resolve()}"
Xtx GND VDD VIN VOUT Inv_Transmitter
{noise}Xrx GND VDD RXIN OUT Resist_FB_INV
Cload OUT 0 20f
.tran 0.1n {total_ns:g}n
.control
{"set seed=" + str(mc_seed) if mc_seed is not None else ""}
run
wrdata {wave.resolve()} v(VIN) v(VOUT) v(RXIN) v(OUT)
.endc
.end
""")
    proc = subprocess.run(["ngspice", "-b", str(tb)], text=True,
                          capture_output=True, timeout=600, cwd=workdir)
    if proc.returncode != 0 or not wave.is_file():
        raise RuntimeError(f"ngspice failed\n{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}")
    arr = np.loadtxt(wave)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    # wrdata pair columns: time,value for each vector.
    t = arr[:, 0] * 1e9  # ns for the PWL/UI grid
    vin = arr[:, 1]
    txout = arr[:, 3]
    rxin = arr[:, 5]
    out = arr[:, 7]
    # Open-loop delay scan: when phase_len is set, only the training prefix
    # chooses the phase.  The returned score is evaluated on all bits, and
    # validation_errors covers the held-out suffix.
    phase_len = min(phase_len, len(bits)) if phase_len > 0 else len(bits)
    ui = ui_ns
    offset_grid = np.linspace(0.0, 0.9 * ui, 19)
    offset_errors = []
    phase_errors = []
    samples_by_offset = []
    sample_v_by_offset = []
    for offset_ns in offset_grid:
        samples = []
        sample_v = []
        for i, bit in enumerate(bits):
            ts = (i + 0.5) * ui + offset_ns
            v = float(np.interp(ts, t, out))
            sample_v.append(v)
            samples.append(1 if v > (vin_high / 2.0) else 0)
        # The chain has two inverting stages: expected output is non-inverted.
        errs = sum(1 for a, b in zip(samples, bits) if a != b)
        phase_errs = sum(1 for a, b in zip(samples[:phase_len], bits[:phase_len])
                         if a != b)
        offset_errors.append(errs)
        phase_errors.append(phase_errs)
        samples_by_offset.append(samples)
        sample_v_by_offset.append(sample_v)
    min_phase_errors = min(phase_errors)
    good_indices = [i for i, e in enumerate(phase_errors)
                    if e == min_phase_errors]
    # Pick the centre of the best training region, not its first edge, so the
    # frozen validation phase has margin on both sides.
    best_index = good_indices[len(good_indices) // 2]
    off = offset_grid[best_index]
    samples = samples_by_offset[best_index]
    sample_v = sample_v_by_offset[best_index]
    errs = sum(1 for a, b in zip(samples, bits) if a != b)
    validation_errors = (sum(1 for a, b in zip(samples[phase_len:], bits[phase_len:])
                             if a != b) if phase_len < len(bits) else 0)
    good_offsets = [offset_grid[i] for i in good_indices]
    eye_width_ns = max(good_offsets) - min(good_offsets) if len(good_offsets) > 1 else 0.0
    high_v = [v for v, b in zip(sample_v, bits) if b == 1]
    low_v = [v for v, b in zip(sample_v, bits) if b == 0]
    eye_height_v = (float(np.median(high_v)) - float(np.median(low_v))
                    if high_v and low_v else 0.0)
    return {
        "tx_pex": str(tx),
        "rx_pex": str(rx),
        "corner": corner,
        "ui_ns": ui_ns,
        "bits": bits,
        "samples": samples,
        "errors": int(errs),
        "ber": float(errs) / len(bits),
        "sample_offset_ns": float(off),
        "eye_width_ns": float(eye_width_ns),
        "eye_height_v": float(eye_height_v),
        "offset_scan_ns": [float(x) for x in offset_grid],
        "offset_errors": [int(x) for x in offset_errors],
        "phase_len": int(phase_len),
        "phase_errors": [int(x) for x in phase_errors],
        "validation_errors": int(validation_errors),
        "validation_ber": (float(validation_errors) / (len(bits) - phase_len)
                           if len(bits) > phase_len else 0.0),
        "txout_min_v": float(np.min(txout)),
        "txout_max_v": float(np.max(txout)),
        "rxin_min_v": float(np.min(rxin)),
        "rxin_max_v": float(np.max(rxin)),
        "out_min_v": float(np.min(out)),
        "out_max_v": float(np.max(out)),
        "vin_high_v": vin_high,
        "noise_mv": noise_mv,
        "noise_seed": noise_seed,
        "mc_seed": mc_seed,
        "vdd_v": vdd,
        "temp_c": temp_c,
        "wave": str(wave),
        "returncode": proc.returncode,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tx-pex", default=str(ROOT / "build/verify/magic_pex/Inv_Transmitter.pex.spice"))
    ap.add_argument("--rx-pex", default=str(ROOT / "build/verify/magic_pex/Resist_FB_INV.pex.spice"))
    ap.add_argument("--rate-mbps", type=float, default=100.0)
    ap.add_argument("--bits", default="01011001")
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--r-ohm", type=float, default=100.0)
    ap.add_argument("--c-pf", type=float, default=16.0)
    ap.add_argument("--mc-seed", type=int, default=None,
                    help="enable sky130 local mismatch with this ngspice seed")
    ap.add_argument("--vdd", type=float, default=1.8)
    ap.add_argument("--temp-c", type=float, default=None)
    ap.add_argument("--phase-len", type=int, default=0,
                    help="prefix bits used only to select the sampling phase")
    ap.add_argument("--out", default=str(ROOT / "build/verify/mixed_signal_e2e.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build/verify/mixed_signal_e2e"))
    args = ap.parse_args()
    for p in (args.tx_pex, args.rx_pex):
        if not Path(p).is_file():
            print(f"error: missing {p}; run make magic-pex / analog-macro-pex",
                  file=sys.stderr)
            return 2
    bits = [int(c) for c in args.bits if c in "01"]
    if len(bits) < 8:
        print("error: need at least 8 bits", file=sys.stderr)
        return 2
    pdk = find_pdk_root()
    report = run(Path(args.tx_pex), Path(args.rx_pex), bits,
                 1000.0 / args.rate_mbps, pdk, Path(args.workdir),
                 args.corner, args.r_ohm, args.c_pf, mc_seed=args.mc_seed,
                 vdd=args.vdd, temp_c=args.temp_c, phase_len=args.phase_len)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"mixed PEX: errors={report['errors']}/{len(bits)} "
          f"BER={report['ber']:.3g} offset={report['sample_offset_ns']:.3g} ns")
    print(f"wrote {out}")
    return 0 if report["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

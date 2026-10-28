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
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root  # noqa: E402


def _pwl(bits: list[int], ui_ns: float, edge_ns: float = 0.08) -> str:
    pts = []
    last = 0.0
    for i, bit in enumerate(bits):
        t = i * ui_ns
        pts.append(f"{t:.6g}n {0.0 if i == 0 else (1.8 if bits[i-1] else 0.0):.6g}")
        # Add a finite edge at the beginning of each bit.
        v = 1.8 if bit else 0.0
        pts.append(f"{(t + edge_ns):.6g}n {v:.6g}")
        last = t + ui_ns
    pts.append(f"{last:.6g}n {1.8 if bits[-1] else 0.0:.6g}")
    return " ".join(pts)


def run(tx: Path, rx: Path, bits: list[int], ui_ns: float, pdk: Path,
        workdir: Path, corner: str = "tt", r_ohm: float = 100.0,
        c_pf: float = 16.0) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    wave = workdir / "wave.dat"
    tb = workdir / "mixed.sp"
    lib = pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"
    total_ns = max(2.0, (len(bits) + 4) * ui_ns)
    tb.write_text(f"""* mixed-signal PEX chain
.lib "{lib}" {corner}
.param VDD=1.8
Vdd VDD 0 {{VDD}}
Vin VIN 0 PWL({_pwl(bits, ui_ns)})
.include "{tx.resolve()}"
.include "{rx.resolve()}"
Xtx GND VDD VIN VOUT Inv_Transmitter
Rch VOUT RXIN {r_ohm:g}
Cch RXIN 0 {c_pf:g}p
Xrx GND VDD RXIN OUT Resist_FB_INV
Cload OUT 0 20f
.tran 0.1n {total_ns:g}n
.control
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
    # A simple delay scan: choose the sampling offset that minimizes errors
    # over the middle bits.  This is a characterization search, not a CDR.
    best = None
    ui = ui_ns
    for offset_ns in np.linspace(0.0, 0.9 * ui, 19):
        samples = []
        valid = True
        for i, bit in enumerate(bits):
            ts = (i + 0.5) * ui + offset_ns
            v = float(np.interp(ts, t, out))
            samples.append(1 if v > 0.9 else 0)
        # The chain has two inverting stages: expected output is non-inverted.
        errs = sum(1 for a, b in zip(samples, bits) if a != b)
        if best is None or errs < best[0]:
            best = (errs, offset_ns, samples)
    errs, off, samples = best
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
        "txout_min_v": float(np.min(txout)),
        "txout_max_v": float(np.max(txout)),
        "rxin_min_v": float(np.min(rxin)),
        "rxin_max_v": float(np.max(rxin)),
        "out_min_v": float(np.min(out)),
        "out_max_v": float(np.max(out)),
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
                 args.corner, args.r_ohm, args.c_pf)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"mixed PEX: errors={report['errors']}/{len(bits)} "
          f"BER={report['ber']:.3g} offset={report['sample_offset_ns']:.3g} ns")
    print(f"wrote {out}")
    return 0 if report["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

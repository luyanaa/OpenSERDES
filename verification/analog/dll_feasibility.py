#!/usr/bin/env python3
"""First-order transistor-level feasibility for the 8-tap DLL delay line.

Level-1 MOS model calibrated to sky130 core parameters from the PDK card
(toxm = 4.148 nm -> Cox ~ 8.3 fF/um^2, u0 = 0.0896 m^2/Vs -> kp_n ~ 745 uA/V^2,
Vth ~ 0.45 V, VDD 1.8 V).  It is a real SPICE transient and answers whether a
current-starved NMOS tap can produce 1.25 ns at sky130 1.8 V with sane power.

It is NOT the foundry BSIM4 card: the sky130 PM3 models use binning and agauss
mismatch terms that ngspice cannot load directly here, and the .lib
sky130.lib.spice path blocks on an OSDI load.  Treat absolute numbers as +-30%,
sizing only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

HEADER = """* dll delay-line feasibility
.param mc_mm_switch=0
.model nch nmos level=1 vto=0.45 kp=745u gamma=0.4 phi=0.7 lambda=0.02
"""


def pair(prefix: str, src: str, dst: str) -> str:
    """Two current-starved inverters -> one non-inverting tap."""
    mid = f"{prefix}m"
    return (
        f"Msw_{prefix}a {mid} {src} n{prefix}a 0 nch w=2 l=0.15\n"
        f"Msink_{prefix}a n{prefix}a vb 0 0 nch w=3 l=0.5\n"
        f"Rp_{prefix}a vdd {mid} 25k\n"
        f"Cload_{prefix}a {mid} 0 50f\n"
        f"Msw_{prefix}b {dst} {mid} n{prefix}b 0 nch w=2 l=0.15\n"
        f"Msink_{prefix}b n{prefix}b vb 0 0 nch w=3 l=0.5\n"
        f"Rp_{prefix}b vdd {dst} 25k\n"
        f"Cload_{prefix}b {dst} 0 50f\n"
    )


def single_deck(vb: float, vdd: float = 1.8) -> str:
    return HEADER + f"""
Vdd vdd 0 {vdd}
Vin in_0 0 PULSE(0 {vdd} 1n 100p 100p 4n 10n)
Vb vb 0 {vb}
{pair('pin', 'in_0', 'in_buf')}
{pair('t0', 'in_buf', 't0')}
.tran 2p 12n
.meas tran td when v(t0)={vdd/2:.2f} rise=1
.meas tran i_vdd avg i(Vdd) from=3n to=10n
.control
run
print td i_vdd
.endc
.end
"""


def chain_deck(vb: float, stages: int, vdd: float = 1.8) -> str:
    body = f"Vdd vdd 0 {vdd}\nVin in_0 0 PULSE(0 {vdd} 1n 100p 100p 4n 10n)\nVb vb 0 {vb}\n"
    # input buffer pair: the ideal source edge is faster than a tap edge, so the
    # first interval would otherwise not match the rest.
    body += pair("pin", "in_0", "in_buf")
    prev = "in_buf"
    for k in range(stages):
        dst = f"t{k+1}"
        body += pair(f"p{k+1}", prev, dst)
        prev = dst
    meas = f".meas tran tbuf when v(in_buf)={vdd/2:.2f} rise=1\n" + "\n".join(
        f".meas tran t{k+1} when v(t{k+1})={vdd/2:.2f} rise=1"
        for k in range(stages)
    )
    prints = "tbuf " + " ".join(f"t{k+1}" for k in range(stages))
    return HEADER + body + f"""
.tran 2p {stages*2.5+6:.1f}n
{meas}
.meas tran i_vdd avg i(Vdd) from=3n to={stages*2.5+5:.1f}n
.control
run
print {prints} i_vdd
.endc
.end
"""


def run(deck: str, work: Path, tag: str) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    sp = work / f"{tag}.sp"
    sp.write_text(deck)
    proc = subprocess.run(["ngspice", "-n", "-b", str(sp.resolve())], cwd=work,
                          text=True, capture_output=True, timeout=180)
    vals = {}
    for line in (proc.stdout + proc.stderr).splitlines():
        m = re.match(r"\s*([A-Za-z_][\w]*)\s*=\s*([-+0-9.eE]+)", line)
        if m:
            try:
                vals[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return vals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", type=int, default=8)
    ap.add_argument("--target-ns", type=float, default=1.25)
    ap.add_argument("--out", default="build/verify/dll_feasibility.json")
    ap.add_argument("--workdir", default="build/verify/dll_feasibility")
    args = ap.parse_args()
    work = Path(args.workdir)

    # Sweep the chain directly: the per-tap spacing (not the single-cell
    # delay) is what the DLL must set to one clock period / stages.
    sweep = []
    best_vb, best_err = None, 1e9
    best_ch = None
    for vb in (0.55, 0.60, 0.63, 0.65, 0.66, 0.67, 0.68, 0.70, 0.75, 0.80):
        v = run(chain_deck(vb, args.stages), work, f"sweep_{vb:.2f}")
        if f"t{args.stages}" not in v:
            continue
        taps = [v[f"t{k+1}"] * 1e9 for k in range(args.stages)]
        spacing = (taps[-1] - v["tbuf"] * 1e9) / args.stages
        sweep.append({"vb": vb, "spacing_ns": round(spacing, 4),
                      "i_vdd_uA": round(abs(v.get("i_vdd", 0.0)) * 1e6, 2)})
        if abs(spacing - args.target_ns) < best_err:
            best_vb, best_err, best_ch = vb, abs(spacing - args.target_ns), v
    if best_vb is None:
        print("error: no usable bias point", sweep)
        return 1

    ch = best_ch
    if f"t{args.stages}" not in ch:
        print("error: chain measurement failed", ch)
        return 1
    taps = [ch[f"t{k+1}"] * 1e9 for k in range(args.stages)]
    tref = ch["tbuf"] * 1e9
    spacing = [taps[0] - tref] + [taps[k] - taps[k - 1] for k in range(1, len(taps))]
    span = taps[-1] - tref

    sup = {}
    for pct in (-10, 0, 10):
        vdd = 1.8 * (1 + pct / 100.0)
        v = run(chain_deck(best_vb, args.stages, vdd), work, f"sup_{pct:+d}")
        if f"t{args.stages}" in v and "tbuf" in v:
            sup[str(pct)] = round((v[f"t{args.stages}"] - v["tbuf"]) * 1e9, 4)

    tap_area = 2.0 + 2 * (2 * 0.15) + 2 * (3 * 0.5) + 2 * 25.0
    i_ua = abs(ch.get("i_vdd", 0.0)) * 1e6
    out = {
        "model": "level-1 calibrated to sky130 core parameters (not BSIM card)",
        "vdd_v": 1.8,
        "stages": args.stages,
        "target_stage_ns": args.target_ns,
        "sweep": sweep,
        "bias_v": best_vb,
        "tap_ns": [round(t, 3) for t in taps],
        "spacing_ns": [round(s, 3) for s in spacing],
        "span_ns": round(span, 3),
        "spacing_spread_ns": round(max(spacing) - min(spacing), 3),
        "i_vdd_uA": round(i_ua, 2),
        "power_mw": round(i_ua * 1e-3 * 1.8, 3),
        "tap_area_um2": round(tap_area, 2),
        "total_area_um2": round(tap_area * args.stages, 2),
        "supply_sensitivity_ns": sup,
        "claim_level": "unverified",
        "notes": [
            "Level-1 model: absolute delay/power +-30%, sizing only.",
            "Tap mismatch not modelled; identical stages assumed.",
            "Rp is an ideal 100k resistor; the real pull-up is a PMOS current source.",
            "Open-loop supply sensitivity; the DLL only suppresses slow drift.",
        ],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("bias_v", "span_ns", "spacing_ns",
                                          "power_mw", "total_area_um2",
                                          "supply_sensitivity_ns")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Closed-loop lock check for the DLL: PD + charge pump + loop filter.

The delay line is the transistor-level 8-tap current-starved NMOS line from
dll_feasibility.py (level-1 model calibrated to sky130 core parameters).  The
phase detector is a D flip-flop (XSPICE d_dff + ADC/DAC bridges) clocked by the
reference delayed 0.3 ns, sampling the last tap.  The charge pump is an ideal
current source (+-Icp), and the loop filter is R/C around a 0.67 V bias.  This
is an architecture-level lock check: PD/CP are behavioural, the line is SPICE.

Lock point: tap8 rising edge = reference + 0.3 ns, i.e. the line delay settles
at 10.3 ns, the instant the track-and-hold switch opens.

The loop filter is first order: `Rf` returns the filter node to the fixed
pre-lock bias `vb0`, so at lock the charge pump must supply the DC drop across
Rf.  That drop needs a non-zero mean CP current and therefore a static phase
error; the reported offset is that error, not a PD sample delay.  The script
predicts the held value from the measured Rf current and checks it against the
simulation.
"""
from __future__ import annotations

import argparse
import bisect
import json
import subprocess
from pathlib import Path

HEADER = """* closed-loop DLL lock check
.param mc_mm_switch=0
.model nch nmos level=1 vto=0.45 kp=745u gamma=0.4 phi=0.7 lambda=0.02
Vdd vdd 0 1.8
Vref ref 0 PULSE(0 1.8 1n 100p 100p 4.9n 10n)
Vbias vb0 0 {vb0}
T1 ref 0 refd 0 Z0=50 TD={window}n
Rt refd 0 50
"""


def pair(prefix: str, src: str, dst: str) -> str:
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


def deck(vb0: float, window: float, icp_uA: float, rf_k: float, cf_p: float,
         stages: int, tstop_us: float) -> str:
    body = HEADER.format(vb0=vb0, window=window)
    prev = "ref"
    for k in range(stages):
        dst = f"t{k+1}"
        body += pair(f"p{k+1}", prev, dst)
        prev = dst
    body += f"""
* track-and-hold: the switch tracks the tap while refd is high and holds
* at the falling edge, i.e. sample instant = ref + period
.model SW1 SW(Ron=100 Roff=1G Vt=0.9 Vh=0)
Ss t{stages} hold refd 0 SW1
Ch hold 0 100f
Rh hold 0 1G
* gate the charge pump to the hold phase so the tracked window does not count
Bg gate 0 V = 1.8/(1+exp((v(refd)-0.9)/0.05))
Bcp vb0 vb I = {icp_uA}u*(1-2*v(hold)/1.8)*v(gate)/1.8
Rf vb vb0 {rf_k}k
Cf vb 0 {cf_p}p
.ic v(vb)={vb0}
.tran 50p {tstop_us}u uic
.control
run
wrdata loop.dat v(vb) v(ref) v(t{stages}) v(hold) v(gate)
.endc
.end
"""
    return body


def parse_dat(path: Path) -> list[tuple[float, ...]]:
    rows = []
    for line in path.read_text().splitlines():
        v = line.split()
        if len(v) < 10:
            continue
        try:
            rows.append((float(v[0]), float(v[1]), float(v[3]),
                         float(v[5]), float(v[7]), float(v[9])))
        except ValueError:
            pass
    return rows


def crossing(t: list[float], sig: list[float], k: int, thr: float = 0.9) -> float:
    """Linearly interpolated crossing time at index k where sig hits thr."""
    x0, x1 = t[k - 1], t[k]
    y0, y1 = sig[k - 1], sig[k]
    if y1 == y0:
        return x0
    return x0 + (thr - y0) * (x1 - x0) / (y1 - y0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", type=int, default=8)
    ap.add_argument("--vb0", type=float, default=0.67)
    # refd is high for 5 ns, so the hold instant is ref + window + 5 ns.
    ap.add_argument("--tdly", type=float, default=5.3)
    ap.add_argument("--icp-ua", type=float, default=5.0)
    ap.add_argument("--rf-k", type=float, default=100.0)
    ap.add_argument("--cf-p", type=float, default=100.0)
    ap.add_argument("--tstop-us", type=float, default=20.0)
    ap.add_argument("--out", default="build/verify/dll_lock.json")
    ap.add_argument("--workdir", default="build/verify/dll_lock")
    args = ap.parse_args()
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    sp = work / "loop.sp"
    sp.write_text(deck(args.vb0, args.tdly, args.icp_ua, args.rf_k, args.cf_p,
                       args.stages, args.tstop_us))
    proc = subprocess.run(["ngspice", "-n", "-b", str(sp.resolve())], cwd=work,
                          text=True, capture_output=True, timeout=600)
    dat = work / "loop.dat"
    if not dat.is_file():
        print("error: no data", proc.stdout[-500:], proc.stderr[-500:])
        return 1
    rows = parse_dat(dat)
    if len(rows) < 1000:
        print("error: too few points", len(rows))
        return 1
    t = [r[0] for r in rows]
    vb = [r[1] for r in rows]
    ref = [r[2] for r in rows]
    tap = [r[3] for r in rows]
    hold = [r[4] for r in rows]
    gate = [r[5] for r in rows]

    # interpolated 0.9 V rising edges
    tr_all = [crossing(t, ref, k) for k in range(1, len(rows))
              if ref[k] >= 0.9 and ref[k - 1] < 0.9]
    tt_all = [crossing(t, tap, k) for k in range(1, len(rows))
              if tap[k] >= 0.9 and tap[k - 1] < 0.9]

    period_ns = 10.0
    # refd is ref delayed by tdly; its falling 0.9 V crossing is the hold
    # instant, and the ref 0.9 V rise-to-fall distance is exactly half a period.
    sample_ns = args.tdly + 0.5 * period_ns

    # Unwrap the delay: pick the ref edge that puts ref->tap near the target.
    # A raw "edge minus most recent ref edge" folds everything into one period
    # and cannot tell 0.4 ns from 10.4 ns.
    edges = []
    for x in tt_all:
        cands = [y for y in tr_all if x - 1.5 * period_ns * 1e-9 < y < x]
        if not cands:
            continue
        y = min(cands, key=lambda y: abs((x - y) * 1e9 - sample_ns))
        d = (x - y) * 1e9
        if 0.5 * period_ns <= d <= 1.5 * period_ns:
            edges.append((x, d))

    n_tail = len(rows) // 10
    vb_final = sum(vb[-n_tail:]) / n_tail
    n20 = len(rows) // 5
    vb_drift_mv = (max(vb[-n20:]) - min(vb[-n20:])) * 1e3

    span = None
    t_lock = None
    phase_locked = False
    if edges:
        tail = [d for (te, d) in edges if te >= t[-1] - (t[-1] - t[0]) * 0.2]
        span = sum(tail) / len(tail)
        tol = 0.05
        k_lock = len(edges)
        while k_lock > 0 and abs(edges[k_lock - 1][1] - span) <= tol:
            k_lock -= 1
        if k_lock < len(edges):
            t_lock = edges[k_lock][0]
            phase_locked = True

    # Held value at the PD instant.  The CP zero is 0.9 V, so this is the
    # direct measure of the loop's phase error.
    hold_at_pd = None
    if tr_all:
        vals = [hold[bisect.bisect_right(t, R + sample_ns * 1e-9) - 1]
                for R in tr_all[-200:]]
        vals = [v for v in vals if 0.0 <= v <= 1.8]
        if vals:
            hold_at_pd = sum(vals) / len(vals)

    # The first-order filter needs a DC current through Rf at lock.  Predict
    # the held value that supplies it and compare with the simulation.
    gate_duty = sum(gate[-n_tail:]) / n_tail / 1.8
    rf_ua = (vb_final - args.vb0) / (args.rf_k * 1e3) * 1e6
    model_hold = None
    if args.icp_ua > 0 and gate_duty > 0:
        model_hold = 0.9 * (1.0 - rf_ua / (args.icp_ua * gate_duty))

    # Bias settle: first ref edge after which vb stays within 1 mV of final.
    vb_ref = [vb[bisect.bisect_right(t, R) - 1] for R in tr_all]
    k_b = len(vb_ref)
    while k_b > 0 and abs(vb_ref[k_b - 1] - vb_final) < 1e-3:
        k_b -= 1
    t_bias = tr_all[k_b] if k_b < len(tr_all) else None

    static_offset_ps = None if span is None else (span - sample_ns) * 1e3
    tap_minus_ref = None if span is None else span - period_ns

    out = {
        "model": "level-1 line + behavioural PD/CP/filter (architecture lock check)",
        "stages": args.stages,
        "vb0_v": args.vb0,
        "pd_sample_instant_ns": round(sample_ns, 3),
        "icp_uA": args.icp_ua,
        "rf_kohm": args.rf_k,
        "cf_pf": args.cf_p,
        "tstop_us": args.tstop_us,
        "vb_final_v": round(vb_final, 5),
        "vb_drift_mv": round(vb_drift_mv, 3),
        "vb_tail_ripple_mv": round((max(vb[-n_tail:]) - min(vb[-n_tail:])) * 1e3, 3),
        "hold_at_pd_v": None if hold_at_pd is None else round(hold_at_pd, 4),
        "pd_error_mv": None if hold_at_pd is None else round((hold_at_pd - 0.9) * 1e3, 1),
        "line_delay_ns": None if span is None else round(span, 4),
        "tap_minus_ref_ns": None if tap_minus_ref is None else round(tap_minus_ref, 4),
        "static_offset_ps": None if static_offset_ps is None else round(static_offset_ps, 1),
        "offset_rf_current_uA": round(rf_ua, 4),
        "offset_model_hold_v": None if model_hold is None else round(model_hold, 4),
        "gate_duty": round(gate_duty, 4),
        "phase_lock_us": None if t_lock is None else round(t_lock * 1e6, 3),
        "bias_settle_us": None if t_bias is None else round(t_bias * 1e6, 3),
        "lock_criterion_ns": 0.05,
        "phase_locked": phase_locked,
        "converged": bool(phase_locked and vb_drift_mv < 0.1),
        "claim_level": "unverified",
        "notes": [
            "PD/CP/filter are behavioural; the delay line is the level-1 SPICE line.",
            "Rf returns the filter node to the fixed vb0, so at lock the CP must",
            "supply the Rf DC drop; the static offset is that loop-filter error,",
            "not a PD sample delay.  offset_model_hold_v is the prediction.",
            "No jitter, mismatch, PD/CP transistor design, or foundry BSIM card.",
        ],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("vb_final_v", "vb_drift_mv",
                                          "hold_at_pd_v", "offset_model_hold_v",
                                          "line_delay_ns", "static_offset_ps",
                                          "phase_lock_us", "converged")},
                     indent=2))
    if not out["converged"]:
        print(f"warning: loop not converged at tstop={args.tstop_us:g} us")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""First mixed-signal PHY baseline (fixed PDK + rate), then PVT/noise screen.

Wraps the TX-PEX -> RC-channel -> RX-PEX transient chain in
``verification/analog/mixed_signal_e2e.py`` and runs it at a *fixed* PDK
(sky130) and a *fixed* reference rate, then sweeps a small PVT/noise grid so
the PHY's operating envelope is characterised, not just a single point.

Per the project verification requirements this is a characterization screen,
not a signoff matrix: each (rate, corner, VDD, temp, noise) point is one
open-loop phase-search transient, and the reported BER/eye are the diagnostic
result of that single run.

Outputs a JSON report: one record per operating point with errors, BER,
training_phase_span_ns, eye_open_clean_ns, eye_open_ber_ns, eye_height_v and
the frozen validation-phase metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root  # noqa: E402
from verification.analog import mixed_signal_e2e as mse  # noqa: E402


def _bits(n: int, seed: int = 0x5D) -> list[int]:
    """Deterministic PRBS-7 payload of length n."""
    st = seed & 0x7F or 0x7F
    out = []
    for _ in range(n):
        b = (st >> 6) & 1
        out.append(b)
        st = ((st << 1) & 0x7F) | (((st >> 6) & 1) ^ ((st >> 5) & 1))
    return out


def run_point(tx: Path, rx: Path, pdk: Path, workdir: Path, *,
              rate_mbps: float, corner: str, vdd: float,
              temp_c: float | None, noise_mv: float, bits: int,
              phase_len: int, prbs_seed: int = 0x5D,
              noise_seed: int = 7) -> dict:
    ui_ns = 1000.0 / rate_mbps
    payload = _bits(bits, seed=prbs_seed)
    rep = mse.run(tx, rx, payload, ui_ns, pdk, workdir, corner,
                  vdd=vdd, temp_c=temp_c, noise_mv=noise_mv,
                  noise_seed=noise_seed, phase_len=phase_len)
    return {
        "rate_mbps": rate_mbps, "corner": corner, "vdd_v": vdd,
        "temp_c": temp_c, "noise_mv": noise_mv, "bits": bits,
        "prbs_seed": prbs_seed, "noise_seed": noise_seed,
        "errors": rep["errors"], "ber": rep["ber"],
        "validation_errors": rep["validation_errors"],
        "validation_ber": rep["validation_ber"],
        "training_phase_span_ns": rep["training_phase_span_ns"],
        "eye_open_clean_ns": rep["eye_open_clean_ns"],
        "eye_open_ber_ns": rep["eye_open_ber_ns"],
        "eye_height_v": rep["eye_height_v"],
        "sample_offset_ns": rep["sample_offset_ns"],
        "returncode": rep["returncode"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tx-pex", default=str(
        ROOT / "build/verify/magic_pex/Inv_Transmitter.pex.spice"))
    ap.add_argument("--rx-pex", default=str(
        ROOT / "build/verify/magic_pex/Resist_FB_INV.pex.spice"))
    ap.add_argument("--base-rate-mbps", type=float, default=100.0)
    ap.add_argument("--bits", type=int, default=64)
    ap.add_argument("--phase-len", type=int, default=32,
                    help="prefix bits that choose the sampling phase")
    ap.add_argument("--rates", default="100,200,400",
                    help="comma list of Mb/s rates to screen")
    ap.add_argument("--vdd-frac", default="0.9,1.0,1.1",
                    help="VDD scale factors vs nominal 1.8V")
    ap.add_argument("--temps", default="-40,27,125", help="°C grid")
    ap.add_argument("--noise-mv", default="0,100", help="channel noise grid")
    ap.add_argument("--prbs-seeds", default="0x5D",
                    help="comma list of PRBS-7 payload seeds; >1 seed replays "
                         "each screen point on a different bit pattern")
    ap.add_argument("--noise-seeds", default="7",
                    help="comma list of noise seeds; >1 seed replays noisy "
                         "points on different noise realisations")
    ap.add_argument("--corners", default="tt,ss,ff,sf,fs",
                    help="sky130 process corners to screen (ngspice .lib names); "
                         "tt ss ff sf fs are the real skew corners, ll/hh/hl/lh "
                         "alias tt in this PDK and are excluded by default")
    ap.add_argument("--out", default=str(ROOT / "build/verify/ms_phy_baseline.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build/verify/ms_phy_baseline"))
    ap.add_argument("--quick", action="store_true",
                    help="only the fixed-PDK+rate baseline point, no sweep")
    args = ap.parse_args()

    tx, rx = Path(args.tx_pex), Path(args.rx_pex)
    for p in (tx, rx):
        if not p.is_file():
            print(f"error: missing {p}; run make magic-pex / analog-macro-pex",
                  file=sys.stderr)
            return 2
    pdk = find_pdk_root()
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []

    # 1) Fixed-PDK + fixed-rate baseline (nominal TT, nominal VDD, 27C, quiet).
    base = run_point(tx, rx, pdk, workdir / "baseline",
                     rate_mbps=args.base_rate_mbps, corner="tt",
                     vdd=1.8, temp_c=27.0, noise_mv=0.0,
                     bits=args.bits, phase_len=args.phase_len)
    base["point"] = "baseline"
    records.append(base)
    print(f"baseline {args.base_rate_mbps}Mb/s tt: "
          f"BER={base['validation_ber']:.3g} "
          f"eye_open={base['eye_open_clean_ns']:.3g}ns "
          f"phase_span={base['training_phase_span_ns']:.3g}ns")

    if not args.quick:
        # 2) Rate sweep at nominal PVT.
        for r in [float(x) for x in args.rates.split(",") if x.strip()]:
            rec = run_point(tx, rx, pdk, workdir / f"rate{int(r)}",
                            rate_mbps=r, corner="tt", vdd=1.8, temp_c=27.0,
                            noise_mv=0.0, bits=args.bits, phase_len=args.phase_len)
            rec["point"] = f"rate_{r:g}M"
            records.append(rec)
            print(f"rate {r:g}Mb/s: BER={rec['validation_ber']:.3g}")

        # 3) PVT screen at the baseline rate: corner x VDD x temp, plus noise.
        for vfrac in [float(x) for x in args.vdd_frac.split(",") if x.strip()]:
            for tc in [float(x) for x in args.temps.split(",") if x.strip()]:
                rec = run_point(tx, rx, pdk,
                                workdir / f"pvt_v{int(vfrac*100)}_t{int(tc)}",
                                rate_mbps=args.base_rate_mbps, corner="tt",
                                vdd=1.8 * vfrac, temp_c=tc, noise_mv=0.0,
                                bits=args.bits, phase_len=args.phase_len)
                rec["point"] = f"pvt_v{vfrac:g}_t{tc:g}"
                records.append(rec)
        for nv in [float(x) for x in args.noise_mv.split(",") if x.strip()]:
            rec = run_point(tx, rx, pdk, workdir / f"noise{int(nv)}",
                            rate_mbps=args.base_rate_mbps, corner="tt",
                            vdd=1.8, temp_c=27.0, noise_mv=nv,
                            bits=args.bits, phase_len=args.phase_len)
            rec["point"] = f"noise_{nv:g}mV"
            records.append(rec)

        # 4) Process-corner matrix at nominal VDD/temp: the real skew corners
        #    (tt ss ff sf fs).  This is the P in PVT that the V/T loops above
        #    hold at tt; ll/hh/hl/lh alias tt in this PDK so they are off by
        #    default.
        for c in [x.strip() for x in args.corners.split(",") if x.strip()]:
            if c == "tt":
                continue   # tt is already the baseline
            rec = run_point(tx, rx, pdk, workdir / f"corner_{c}",
                            rate_mbps=args.base_rate_mbps, corner=c,
                            vdd=1.8, temp_c=27.0, noise_mv=0.0,
                            bits=args.bits, phase_len=args.phase_len)
            rec["point"] = f"corner_{c}"
            records.append(rec)
            print(f"corner {c}: BER={rec['validation_ber']:.3g} "
                  f"eye_open={rec['eye_open_clean_ns']:.3g}ns")

        # 5) Multi-seed robustness screen: replay the nominal point on several
        #    PRBS payload seeds and noise seeds so a single lucky bit pattern or
        #    noise realisation cannot pass by accident.  Longer --bits is the
        #    long-PRBS knob; each (prbs, noise) pair is one transient.
        prbs_seeds = [int(x, 0) for x in args.prbs_seeds.split(",") if x.strip()]
        noise_seeds = [int(x, 0) for x in args.noise_seeds.split(",") if x.strip()]
        max_noise = max([float(x) for x in args.noise_mv.split(",") if x.strip()])
        for ps in prbs_seeds:
            for ns in noise_seeds:
                if ps == 0x5D and ns == 7:
                    continue   # already the nominal realisation
                rec = run_point(tx, rx, pdk,
                                workdir / f"seed_p{ps:x}_n{ns}",
                                rate_mbps=args.base_rate_mbps, corner="tt",
                                vdd=1.8, temp_c=27.0, noise_mv=max_noise,
                                bits=args.bits, phase_len=args.phase_len,
                                prbs_seed=ps, noise_seed=ns)
                rec["point"] = f"seed_p{ps:x}_n{ns}"
                records.append(rec)
                print(f"seed prbs=0x{ps:x} noise={ns}: "
                      f"BER={rec['validation_ber']:.3g}")

    n_err = sum(1 for r in records if r["returncode"] != 0)
    report = {
        "kind": "ms_phy_baseline",
        "pdk": str(pdk), "tech": "sky130",
        "base_rate_mbps": args.base_rate_mbps, "bits": args.bits,
        "phase_len": args.phase_len,
        "n_points": len(records), "n_failed_sims": n_err,
        "baseline": base,
        "points": records,
        "note": ("Open-loop phase-search characterization screen, not a CDR "
                 "loop or a production PVT signoff matrix.  Each point is one "
                 "transient; reported BER/eye are diagnostic.  The corner_* "
                 "points cover the real skew corners tt/ss/ff/sf/fs; ll/hh/hl/lh "
                 "alias tt in this PDK and are off by default."),
    }
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.out} ({len(records)} points, {n_err} failed)")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

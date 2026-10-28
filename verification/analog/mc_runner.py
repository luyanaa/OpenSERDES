#!/usr/bin/env python3
"""Monte Carlo driver for the tech-configured analog LUT.

Two modes are deliberately separated:

* ``behavioral``: samples independent series gate offsets for the NMOS and
  PMOS of the inverter (a Vth-like local mismatch proxy).  This is a useful
  sensitivity/screening study, **not** a PDK statistical model.
* ``pdk``: only runs if the tech config declares a verified PDK statistical
  model; this install does not, so it fails closed instead of relabeling the
  behavioral injection as PDK MC.

Each sample keeps its joint parameter vector (offsets + measured rise/fall/
swing/tPHL/tPLH), so later link simulations can preserve correlations.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.extract_lut import extract, load_tech  # noqa: E402


def _args(tech: str, workdir: Path, corner: str, vdd: float, temp: float,
          load_ff: float, slew_ps: float, n_off: float, p_off: float) -> argparse.Namespace:
    return argparse.Namespace(
        tech=tech, tech_root=None, model=None, demo=(tech == "generic_demo"),
        allow_scaffold=False, check_tools=False, out="", workdir=str(workdir),
        wn_um=None, wp_um=None, l_um=None, nmos_model=None, pmos_model=None,
        tx_edge_ps=50.0, tx_loads_ff=[load_ff], rx_slews_ps=[slew_ps],
        corner=corner, vdd=vdd, temp_c=temp,
        nmos_offset_v=n_off, pmos_offset_v=p_off,
    )


def _summary(rows: list[dict]) -> dict:
    out = {}
    for key in ("tx_rise_ns", "tx_fall_ns", "tx_swing_v",
                "rx_tphl_ns", "rx_tplh_ns"):
        vals = np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
        if vals.size:
            out[key] = {
                "mean": float(vals.mean()), "std": float(vals.std(ddof=1)) if vals.size > 1 else 0.0,
                "min": float(vals.min()),
                "p05": float(np.percentile(vals, 5.0)),
                "p50": float(np.percentile(vals, 50.0)),
                "p95": float(np.percentile(vals, 95.0)),
                "max": float(vals.max()),
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tech", default="sky130", choices=("sky130", "generic_demo"))
    ap.add_argument("--mode", choices=("behavioral", "pdk"), default="behavioral")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument("--sigma-nmos-v", type=float, default=None)
    ap.add_argument("--sigma-pmos-v", type=float, default=None)
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--vdd", type=float, default=None)
    ap.add_argument("--temp-c", type=float, default=25.0)
    ap.add_argument("--load-ff", type=float, default=50.0)
    ap.add_argument("--slew-ps", type=float, default=20.0)
    ap.add_argument("--out", default=str(ROOT / "build" / "verify" / "sky130_mc.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build" / "verify" / "mc"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_tech(args.tech)
    if args.mode == "pdk":
        mc = cfg.get("mc", {}) or {}
        if mc.get("status") != "available":
            print(f"error: global PDK Monte Carlo is not available for {args.tech}: "
                  f"{mc.get('reason', 'no verified statistical model')}", file=sys.stderr)
            if mc.get("pdk_mismatch") == "available":
                print("hint: use `python3 verification/analog/pdk_mc.py "
                      "--mode mismatch` for the verified per-device PDK "
                      "mismatch path.", file=sys.stderr)
            if str(mc.get("pdk_global", "")).startswith("available"):
                print("hint: use `python3 verification/analog/xyce_global_mc.py "
                      "--mode global` for the global-process PDK MC path.",
                      file=sys.stderr)
            return 2
        raise SystemExit("PDK MC path is not implemented; refusing to relabel behavioral samples")
    if shutil.which("ngspice") is None and not args.dry_run:
        print("error: ngspice is required for Monte Carlo", file=sys.stderr)
        return 2

    defaults = (cfg.get("mc", {}) or {}).get("behavioral", {})
    sigma_n = (args.sigma_nmos_v if args.sigma_nmos_v is not None
               else float(defaults.get("sigma_nmos_v", 0.02)))
    sigma_p = (args.sigma_pmos_v if args.sigma_pmos_v is not None
               else float(defaults.get("sigma_pmos_v", 0.02)))
    vdd = args.vdd if args.vdd is not None else float(cfg.get("vdd_v", 1.8))
    rng = np.random.default_rng(args.seed)
    offsets = [(float(rng.normal(0, sigma_n)), float(rng.normal(0, sigma_p)))
               for _ in range(args.samples)]

    print(f"tech: {args.tech} mode=behavioral (NOT PDK statistical MC)")
    print(f"samples={args.samples} seed={args.seed} sigma_n={sigma_n:g} V "
          f"sigma_p={sigma_p:g} V corner={args.corner} vdd={vdd:g} temp={args.temp_c:g} C")
    if args.dry_run:
        for i, (n, p) in enumerate(offsets[:10]):
            print(f"  sample {i}: nmos={n:+.5f} V pmos={p:+.5f} V")
        if args.samples > 10:
            print(f"  ... {args.samples - 10} more")
        return 0

    workdir = Path(args.workdir)
    rows = []
    failures = []
    for i, (n_off, p_off) in enumerate(offsets):
        point_dir = workdir / f"{i:04d}"
        try:
            report = extract(_args(args.tech, point_dir, args.corner, vdd,
                                   args.temp_c, args.load_ff, args.slew_ps,
                                   n_off, p_off))
            tx = report["tx"]["rows"][0]
            rx = report["rx"]["rows"][0]
            rows.append({
                "sample_id": i,
                "nmos_offset_v": n_off,
                "pmos_offset_v": p_off,
                "tx_rise_ns": tx.get("rise_ns"),
                "tx_fall_ns": tx.get("fall_ns"),
                "tx_swing_v": tx.get("swing_v"),
                "rx_tphl_ns": rx.get("tphl_ns"),
                "rx_tplh_ns": rx.get("tplh_ns"),
            })
            print(f"  sample {i:4d}: n={n_off:+.4f} p={p_off:+.4f} "
                  f"rise={tx.get('rise_ns')} tphl={rx.get('tphl_ns')}")
        except Exception as exc:  # noqa: BLE001
            failures.append({"sample_id": i, "error": str(exc)})
            print(f"  sample {i:4d}: FAIL {exc}")

    out = {
        "tech": args.tech,
        "pdk": cfg.get("pdk"),
        "mode": "behavioral-mismatch",
        "note": ("Behavioral series-gate offset injection; not a PDK statistical "
                 "model and not a yield claim."),
        "samples": args.samples,
        "seed": args.seed,
        "sigma_nmos_v": sigma_n,
        "sigma_pmos_v": sigma_p,
        "corner": args.corner,
        "vdd_v": vdd,
        "temperature_c": args.temp_c,
        "load_ff": args.load_ff,
        "rx_slew_ps": args.slew_ps,
        "rows": rows,
        "failures": failures,
        "summary": _summary(rows),
        "git_commit": _git_commit(),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "limits": [
            "Behavioral offset proxy, not PDK global/local statistical models.",
            "No noise/temporal variation; no PEX; no yield inference.",
        ],
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path} ({len(rows)} samples, {len(failures)} failures)")
    return 0 if not failures else 1


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())

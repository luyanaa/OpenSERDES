#!/usr/bin/env python3
"""PVT characterization runner for the tech-configured analog LUT.

This is a first-order corner/voltage/temperature sweep of the schematic-level
TX driver and RX inverter.  It keeps each point independent (separate ngspice
invocations, separate work directory) and records continuous measurements, not
just pass/fail:

    corner x voltage x temperature -> {rise, fall, swing, tPHL, tPLH}

The default sky130 matrix is 5 process corners x 3 voltages x 3 temperatures at
one fixed load and input slew.  It is a characterization matrix, not a signoff
claim: no PEX, no Monte Carlo, no package/channel, and the model organization
must be validated against the target PDK release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUT = ROOT / "build" / "verify" / "sky130_pvt_lut.json"

from verification.analog.extract_lut import extract, load_tech  # noqa: E402


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _ngspice_version() -> str:
    try:
        out = subprocess.run(["ngspice", "--version"], text=True,
                             capture_output=True).stdout
        return out.strip().splitlines()[0] if out.strip() else "unknown"
    except OSError:
        return "missing"


def _args_for(tech: str, workdir: Path, corner: str, vdd: float,
              temp_c: float, load_ff: float, slew_ps: float) -> argparse.Namespace:
    return argparse.Namespace(
        tech=tech,
        tech_root=None,
        model=None,
        demo=(tech == "generic_demo"),
        allow_scaffold=False,
        check_tools=False,
        out="",
        workdir=str(workdir),
        wn_um=None,
        wp_um=None,
        l_um=None,
        nmos_model=None,
        pmos_model=None,
        tx_edge_ps=50.0,
        tx_loads_ff=[load_ff],
        rx_slews_ps=[slew_ps],
        corner=corner,
        vdd=vdd,
        temp_c=temp_c,
    )


def build_matrix(args: argparse.Namespace) -> list[tuple[str, float, float]]:
    return [
        (corner, vdd, temp)
        for corner in args.corners
        for vdd in args.voltages
        for temp in args.temperatures
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tech", default="sky130", choices=("sky130", "generic_demo"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--workdir", default=str(ROOT / "build" / "verify" / "pvt"))
    ap.add_argument("--corners", nargs="*", default=["tt", "ss", "ff", "sf", "fs"])
    ap.add_argument("--voltages", type=float, nargs="*", default=[1.62, 1.80, 1.98])
    ap.add_argument("--temperatures", type=float, nargs="*", default=[-40.0, 25.0, 100.0])
    ap.add_argument("--load-ff", type=float, default=50.0)
    ap.add_argument("--slew-ps", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fail-fast", action="store_true")
    args = ap.parse_args()

    if shutil.which("ngspice") is None and not args.dry_run:
        print("error: ngspice is required for PVT characterization",
              file=sys.stderr)
        return 2

    cfg = load_tech(args.tech)
    matrix = build_matrix(args)
    print(f"tech: {args.tech} ({cfg.get('pdk')}, status={cfg.get('status')})")
    print(f"matrix: {len(args.corners)} corners x {len(args.voltages)} voltages "
          f"x {len(args.temperatures)} temperatures = {len(matrix)} points")
    print(f"fixed: load={args.load_ff:g} fF, RX slew={args.slew_ps:g} ps")
    if args.dry_run:
        for corner, vdd, temp in matrix:
            print(f"  {corner:3s} {vdd:5.2f} V {temp:7.1f} C")
        return 0

    workdir = Path(args.workdir)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    model_files: list[str] = []
    for index, (corner, vdd, temp) in enumerate(matrix):
        point_dir = workdir / f"{index:03d}_{corner}_{vdd:.2f}V_{temp:g}C"
        try:
            report = extract(_args_for(
                args.tech, point_dir, corner, vdd, temp,
                args.load_ff, args.slew_ps,
            ))
            tx = report["tx"]["rows"][0]
            rx = report["rx"]["rows"][0]
            rows.append({
                "corner": corner,
                "vdd_v": vdd,
                "temperature_c": temp,
                "tx_rise_ns": tx.get("rise_ns"),
                "tx_fall_ns": tx.get("fall_ns"),
                "tx_swing_v": tx.get("swing_v"),
                "rx_tphl_ns": rx.get("tphl_ns"),
                "rx_tplh_ns": rx.get("tplh_ns"),
                "model_kind": report.get("model_kind"),
            })
            if not model_files:
                model_files = report.get("model_files", [])
            print(f"  {corner:3s} {vdd:5.2f} V {temp:7.1f} C: "
                  f"rise={tx.get('rise_ns')} ns "
                  f"tphl={rx.get('tphl_ns')} ns")
        except Exception as exc:  # noqa: BLE001 - record the failing point
            failures.append({
                "corner": corner, "vdd_v": vdd, "temperature_c": temp,
                "error": str(exc),
            })
            print(f"  {corner:3s} {vdd:5.2f} V {temp:7.1f} C: FAIL {exc}")
            if args.fail_fast:
                break

    out = {
        "tech": args.tech,
        "pdk": cfg.get("pdk"),
        "tech_config": cfg.get("_path"),
        "tech_sha256": cfg.get("_sha256"),
        "model_kind": "pvt-schematic",
        "pvt": {
            "corners": list(args.corners),
            "voltages_v": [float(v) for v in args.voltages],
            "temperatures_c": [float(t) for t in args.temperatures],
            "load_ff": args.load_ff,
            "rx_slew_ps": args.slew_ps,
        },
        "rows": rows,
        "failures": failures,
        "model_files": model_files,
        "ngspice_version": _ngspice_version(),
        "git_commit": _git_commit(),
        "extracted_utc": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Schematic-level PVT only; no PEX, no Monte Carlo, no package/channel.",
            "Each point uses a separate ngspice process and work directory.",
            "Validate the model organization and corner names against the target PDK release.",
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {out_path} ({len(rows)} points, {len(failures)} failures)")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

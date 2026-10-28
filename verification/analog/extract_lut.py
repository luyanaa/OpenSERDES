#!/usr/bin/env python3
"""Extract sky130 schematic-level TX/RX LUTs with ngspice.

This is the transistor-level *schematic* layer, not PEX and not silicon: the
netlists use the PDK device models without extracted parasitics.  The resulting
LUTs describe driver rise/fall/swing and inverter delay versus load and input
slew.  They seed the behavioral channel model; they are not a PDK signoff.

The default flow uses the sky130 PDK model tree (auto-detected under
``~/.ciel/ciel`` or ``SKY130_ROOT``).  ``--model`` accepts a single model-card
file for a self-contained flow, and ``--demo`` uses ngspice built-in level-1
MOSFETs.  Demo output is explicitly labelled ``generic-demo`` and must never be
reported as sky130 data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "build" / "verify" / "sky130_lut.json"

# Typical 1.8 V sky130 inverter sizing; schematic-level only.
DEFAULT_WN_UM = 0.65
DEFAULT_WP_UM = 1.40
DEFAULT_L_UM = 0.15
VDD = 1.8

DEMO_MODELS = """* generic level-1 MOSFETs (demo only, not sky130)
.model DEMO_NMOS NMOS (LEVEL=1 VTO=0.45 KP=120u GAMMA=0.4 PHI=0.7 LAMBDA=0.02)
.model DEMO_PMOS PMOS (LEVEL=1 VTO=-0.45 KP=40u GAMMA=0.4 PHI=0.7 LAMBDA=0.02)
"""

SKY130_NFET = "sky130_fd_pr__nfet_01v8"
SKY130_PFET = "sky130_fd_pr__pfet_01v8"


def find_sky130_root() -> Path | None:
    """Locate sky130_fd_pr/latest with the ngspice pm3 model files."""
    env = os.environ.get("SKY130_ROOT")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    home = Path.home()
    candidates += sorted((home / ".ciel" / "ciel" / "sky130" / "versions").glob(
        "*/skywater-pdk/libraries/sky130_fd_pr/latest"))
    for candidate in candidates:
        if (candidate / "cells" / "nfet_01v8" /
                "sky130_fd_pr__nfet_01v8__tt.pm3.spice").is_file() and (
                candidate / "cells" / "pfet_01v8" /
                "sky130_fd_pr__pfet_01v8__tt.pm3.spice").is_file():
            return candidate
    return None


def real_model_card(path: Path) -> bool:
    """Reject alias-only/placeholder single-file model cards."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    if f".subckt {SKY130_NFET}" in text or f".subckt  {SKY130_NFET}" in text:
        return True
    model_lines = [ln.lower() for ln in text.splitlines()
                   if ln.strip().lower().startswith(".model")]
    return any(("level=" in ln or "vto=" in ln or "kp=" in ln)
               for ln in model_lines)


def _device_block(style: str, nmos: str, pmos: str, wn: float, wp: float,
                  l_um: float) -> str:
    if style == "subckt":
        return (f"XMp out in vdd vdd {pmos} l={l_um}u w={wp}u nf=1\n"
                f"XMn out in 0 0 {nmos} l={l_um}u w={wn}u nf=1")
    return (f"Mp out in vdd vdd {pmos} W={wp}u L={l_um}u\n"
            f"Mn out in 0 0 {nmos} W={wn}u L={l_um}u")


def tx_netlist(*, includes: str, devices: str, load_ff: float,
               edge_ps: float) -> str:
    tstep_ps = max(0.2, min(2.0, edge_ps / 20.0))
    stop_ns = 3.0 + edge_ps / 1000.0 * 4.0
    return f"""* OpenSERDES TX driver LUT
.param VDD={VDD}
{includes}
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {edge_ps}p {edge_ps}p 2n 4n)
{devices}
Cload out 0 {load_ff}f
.tran {tstep_ps}p {stop_ns}n
.meas tran vmax max v(out) from=0.0n to={stop_ns}n
.meas tran vmin min v(out) from=0.3n to={stop_ns}n
.meas tran trise trig v(out) val={VDD*0.2} rise=1 targ v(out) val={VDD*0.8} rise=1
.meas tran tfall trig v(out) val={VDD*0.8} fall=1 targ v(out) val={VDD*0.2} fall=1
.end
"""


def rx_netlist(*, includes: str, devices: str, slew_ps: float) -> str:
    tstep_ps = max(0.2, min(2.0, slew_ps / 20.0))
    stop_ns = 4.0
    return f"""* OpenSERDES RX inverter slicer LUT
.param VDD={VDD}
{includes}
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {slew_ps}p {slew_ps}p 2n 4n)
{devices}
Cload out 0 20f
.tran {tstep_ps}p {stop_ns}n
.meas tran tphl trig v(in) val={VDD*0.5} rise=1 targ v(out) val={VDD*0.5} fall=1
.meas tran tplh trig v(in) val={VDD*0.5} fall=1 targ v(out) val={VDD*0.5} rise=1
.end
"""


def run_ngspice(netlist: str, workdir: Path, name: str) -> dict[str, float]:
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"{name}.sp"
    path.write_text(netlist)
    proc = subprocess.run(
        ["ngspice", "-b", str(path)], text=True, capture_output=True, timeout=180
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ngspice failed for {name}:\n{proc.stdout}\n{proc.stderr}")
    values: dict[str, float] = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"\s*([A-Za-z_]\w*)\s*=\s*([-+0-9.eE]+)", line)
        if match:
            values[match.group(1).lower()] = float(match.group(2))
    if not values:
        raise RuntimeError(f"no measurements parsed for {name}:\n{proc.stdout}")
    return values


def _model_config(args: argparse.Namespace, workdir: Path) -> dict[str, Any]:
    if args.demo:
        model_file = workdir / "demo_models.sp"
        model_file.write_text(DEMO_MODELS)
        return {
            "pdk": "generic",
            "model_kind": "generic-demo",
            "model_files": [str(model_file)],
            "includes": f'.include "{model_file}"',
            "style": "mos",
            "nmos": args.nmos_model or "DEMO_NMOS",
            "pmos": args.pmos_model or "DEMO_PMOS",
            "notes": [
                "Demo uses ngspice built-in level-1 MOSFETs.",
                "This is NOT sky130 data and must not be used for a PDK claim.",
            ],
        }

    if args.model:
        model = Path(args.model)
        if not real_model_card(model):
            raise RuntimeError(
                f"{model} contains no real MOSFET model cards (level/vto/kp "
                "or sky130 subckt). It looks like an alias/placeholder file. "
                "Pass a real sky130 model card, set SKY130_ROOT, or use --demo."
            )
        return {
            "pdk": "sky130",
            "model_kind": "sky130-schematic",
            "model_files": [str(model)],
            "includes": f'.include "{model}"',
            "style": "mos",
            "nmos": args.nmos_model or SKY130_NFET,
            "pmos": args.pmos_model or SKY130_PFET,
            "notes": [
                "Single model-card file; schematic-level only, no PEX/corners.",
            ],
        }

    root = Path(args.sky130_root) if args.sky130_root else find_sky130_root()
    if root is None or not root.is_dir():
        raise RuntimeError(
            "no sky130 PM3 model tree found; set SKY130_ROOT or --sky130-root, "
            "pass --model, or use --demo for a generic smoke test"
        )
    files = [
        root / "models" / "parameters" / "invariant.spice",
        root / "models" / "parameters" / "typical.spice",
        root / "cells" / "nfet_01v8" / "sky130_fd_pr__nfet_01v8__tt.pm3.spice",
        root / "cells" / "pfet_01v8" / "sky130_fd_pr__pfet_01v8__tt.pm3.spice",
    ]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise RuntimeError(f"sky130 model tree is incomplete: {missing}")
    includes = "\n".join(f'.include "{p}"' for p in files)
    return {
        "pdk": "sky130",
        "model_kind": "sky130-schematic-tt",
        "model_files": [str(p) for p in files],
        "includes": includes,
        "style": "subckt",
        "nmos": args.nmos_model or SKY130_NFET,
        "pmos": args.pmos_model or SKY130_PFET,
        "notes": [
            "Schematic-level sky130 tt models; no PEX, no Monte Carlo, no silicon.",
            "Includes PDK invariant/typical parameters and pm3 device subckts.",
        ],
    }


def extract(args: argparse.Namespace) -> dict[str, Any]:
    if shutil.which("ngspice") is None:
        raise RuntimeError("ngspice is required for LUT extraction")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = _model_config(args, workdir)
    devices = _device_block(
        cfg["style"], cfg["nmos"], cfg["pmos"], args.wn_um, args.wp_um, args.l_um
    )

    tx_rows = []
    for load_ff in args.tx_loads_ff:
        values = run_ngspice(
            tx_netlist(includes=cfg["includes"], devices=devices,
                       load_ff=load_ff, edge_ps=args.tx_edge_ps),
            workdir, f"tx_{int(load_ff)}ff",
        )
        rise = values.get("trise")
        fall = values.get("tfall")
        vmax = values.get("vmax")
        vmin = values.get("vmin")
        tx_rows.append({
            "load_ff": float(load_ff),
            "rise_ns": None if rise is None else rise * 1e9,
            "fall_ns": None if fall is None else fall * 1e9,
            "vmax_v": vmax,
            "vmin_v": vmin,
            "swing_v": None if vmax is None or vmin is None else vmax - vmin,
        })

    rx_rows = []
    for slew_ps in args.rx_slews_ps:
        values = run_ngspice(
            rx_netlist(includes=cfg["includes"], devices=devices, slew_ps=slew_ps),
            workdir, f"rx_{int(slew_ps)}ps",
        )
        tphl = values.get("tphl")
        tplh = values.get("tplh")
        rx_rows.append({
            "slew_ps": float(slew_ps),
            "tphl_ns": None if tphl is None else tphl * 1e9,
            "tplh_ns": None if tplh is None else tplh * 1e9,
        })

    return {
        "pdk": cfg["pdk"],
        "model_kind": cfg["model_kind"],
        "model_files": cfg["model_files"],
        "device_models": {"nmos": cfg["nmos"], "pmos": cfg["pmos"]},
        "sizing_um": {"wn": args.wn_um, "wp": args.wp_um, "l": args.l_um},
        "vdd_v": VDD,
        "tx": {"loads_ff": [float(x) for x in args.tx_loads_ff], "rows": tx_rows},
        "rx": {"slews_ps": [float(x) for x in args.rx_slews_ps], "rows": rx_rows},
        "extracted_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "notes": cfg["notes"],
    }


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sky130-root", default=None,
                    help="sky130_fd_pr/latest tree (default: auto-detect ~/.ciel/ciel)")
    ap.add_argument("--model", default=None, help="single model-card file")
    ap.add_argument("--nmos-model", default=None,
                    help="NMOS model/subckt name (sky130 default: sky130_fd_pr__nfet_01v8)")
    ap.add_argument("--pmos-model", default=None,
                    help="PMOS model/subckt name (sky130 default: sky130_fd_pr__pfet_01v8)")
    ap.add_argument("--demo", action="store_true",
                    help="use generic level-1 models (NOT sky130)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--workdir", default=str(ROOT / "build" / "verify" / "analog"))
    ap.add_argument("--wn-um", type=float, default=DEFAULT_WN_UM)
    ap.add_argument("--wp-um", type=float, default=DEFAULT_WP_UM)
    ap.add_argument("--l-um", type=float, default=DEFAULT_L_UM)
    ap.add_argument("--tx-edge-ps", type=float, default=50.0)
    ap.add_argument("--tx-loads-ff", type=float, nargs="*", default=[10.0, 50.0, 100.0, 200.0])
    ap.add_argument("--rx-slews-ps", type=float, nargs="*", default=[20.0, 50.0, 100.0, 200.0])
    args = ap.parse_args()

    report = extract(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"pdk: {report['pdk']} ({report['model_kind']})")
    for row in report["tx"]["rows"]:
        print(f"  TX load {row['load_ff']:6.1f} fF: "
              f"rise={row['rise_ns']} ns fall={row['fall_ns']} ns "
              f"swing={row['swing_v']} V")
    for row in report["rx"]["rows"]:
        print(f"  RX slew {row['slew_ps']:6.1f} ps: "
              f"tphl={row['tphl_ns']} ns tplh={row['tplh_ns']} ns")
    print(f"wrote {out}")
    if args.demo:
        print("WARNING: generic-demo data is not sky130 and is not a PDK claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

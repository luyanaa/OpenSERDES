#!/usr/bin/env python3
"""Extract sky130 schematic-level TX/RX LUTs with ngspice.

This is the transistor-level *schematic* layer, not PEX and not silicon:
the netlists use the PDK device models without extracted parasitics.  The
resulting LUTs describe driver rise/fall/saving and inverter threshold/delay
versus load and input slew.  They are intended to seed the behavioral channel
model, not to replace it.

The script refuses to call a placeholder model file "sky130".  Provide a real
model card file via ``--model`` or ``SKY130_MODEL``.  ``--demo`` uses ngspice
built-in level-1 MOSFETs for a runnable smoke test; the output is explicitly
labelled ``generic-demo`` and must not be reported as sky130 data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
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

MODEL_CANDIDATES = [
    ("SKY130_MODEL", None),
    ("PDK_ROOT", "sky130A/libs.ref/sky130_fd_pr/spice/sky130_fd_pr__models.spice"),
    ("PDK_ROOT", "sky130A/libs.ref/sky130_fd_pr/spice/sky130_fd_pr__nfet_01v8.model.spice"),
    ("PDK_LIB_ROOT", "sky130A/libs.ref/sky130_fd_pr/spice/sky130_fd_pr__models.spice"),
]


def find_model() -> Path | None:
    env_model = os.environ.get("SKY130_MODEL")
    if env_model:
        return Path(env_model)
    for env_name, suffix in MODEL_CANDIDATES:
        if suffix is None:
            continue
        base = os.environ.get(env_name)
        if base:
            candidate = Path(base) / suffix
            if candidate.is_file():
                return candidate
    for candidate in (
        Path("/opt/ALIGN-pdk-sky130/SKY130_PDK/models.sp"),
        Path("/usr/share/pdk/sky130A/libs.ref/sky130_fd_pr/spice/sky130_fd_pr__models.spice"),
    ):
        if candidate.is_file():
            return candidate
    return None


def real_model_card(path: Path) -> bool:
    """Reject alias-only/placeholder model files."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    model_lines = [ln.lower() for ln in text.splitlines()
                   if ln.strip().lower().startswith(".model")]
    if not model_lines:
        return False
    for line in model_lines:
        if "level=" in line or "vto=" in line or "kp=" in line:
            return True
    return False


DEMO_MODELS = """* generic level-1 MOSFETs (demo only, not sky130)
.model DEMO_NMOS NMOS (LEVEL=1 VTO=0.45 KP=120u GAMMA=0.4 PHI=0.7 LAMBDA=0.02)
.model DEMO_PMOS PMOS (LEVEL=1 VTO=-0.45 KP=40u GAMMA=0.4 PHI=0.7 LAMBDA=0.02)
"""


def tx_netlist(*, include: str, nmos: str, pmos: str, load_ff: float,
               wn_um: float, wp_um: float, l_um: float, edge_ps: float) -> str:
    tstep_ps = max(0.2, min(2.0, edge_ps / 20.0))
    stop_ns = 3.0 + edge_ps / 1000.0 * 4.0
    return f"""* OpenSERDES TX driver LUT
.param VDD={VDD}
.include "{include}"
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {edge_ps}p {edge_ps}p 2n 4n)
Mp out in vdd vdd {pmos} W={wp_um}u L={l_um}u
Mn out in 0 0 {nmos} W={wn_um}u L={l_um}u
Cload out 0 {load_ff}f
.tran {tstep_ps}p {stop_ns}n
.meas tran vmax max v(out) from=0.0n to={stop_ns}n
.meas tran vmin min v(out) from=0.3n to={stop_ns}n
.meas tran trise trig v(out) val={VDD*0.2} rise=1 targ v(out) val={VDD*0.8} rise=1
.meas tran tfall trig v(out) val={VDD*0.8} fall=1 targ v(out) val={VDD*0.2} fall=1
.end
"""


def rx_netlist(*, include: str, nmos: str, pmos: str, slew_ps: float,
               wn_um: float, wp_um: float, l_um: float) -> str:
    tstep_ps = max(0.2, min(2.0, slew_ps / 20.0))
    stop_ns = 4.0
    return f"""* OpenSERDES RX inverter slicer LUT
.param VDD={VDD}
.include "{include}"
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {slew_ps}p {slew_ps}p 2n 4n)
Mp out in vdd vdd {pmos} W={wp_um}u L={l_um}u
Mn out in 0 0 {nmos} W={wn_um}u L={l_um}u
Cload out 0 20f
.tran {tstep_ps}p {stop_ns}n
.meas tran tphl trig v(in) val={VDD*0.5} rise=1 targ v(out) val={VDD*0.5} fall=1
.meas tran tplh trig v(in) val={VDD*0.5} fall=1 targ v(out) val={VDD*0.5} rise=1
.end
"""


def run_ngspice(netlist: str, workdir: Path, name: str) -> dict[str, float] | None:
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"{name}.sp"
    path.write_text(netlist)
    proc = subprocess.run(
        ["ngspice", "-b", str(path)], text=True, capture_output=True, timeout=120
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ngspice failed for {name}:\n{proc.stdout}\n{proc.stderr}")
    values: dict[str, float] = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"\s*([A-Za-z_]\w*)\s*=\s*([-+0-9.eE]+)", line)
        if match:
            values[match.group(1).lower()] = float(match.group(2))
    return values or None


def extract(args: argparse.Namespace) -> dict[str, Any]:
    if shutil.which("ngspice") is None:
        raise RuntimeError("ngspice is required for LUT extraction")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if args.demo:
        model_file = workdir / "demo_models.sp"
        model_file.write_text(DEMO_MODELS)
        model_kind = "generic-demo"
        pdk = "generic"
        nmos = args.nmos_model or "DEMO_NMOS"
        pmos = args.pmos_model or "DEMO_PMOS"
        include = str(model_file)
        notes = [
            "Demo uses ngspice built-in level-1 MOSFETs.",
            "This is NOT sky130 data and must not be used for a PDK claim.",
        ]
    else:
        model = Path(args.model) if args.model else find_model()
        if model is None:
            raise RuntimeError(
                "no sky130 model file found; set SKY130_MODEL or pass --model "
                "(run with --demo for a non-sky130 smoke test)"
            )
        if not real_model_card(model):
            raise RuntimeError(
                f"{model} contains no real MOSFET model cards (level/vto/kp). "
                "It looks like an alias/placeholder file. Set SKY130_MODEL to "
                "the PDK's sky130_fd_pr model file, or use --demo."
            )
        model_file = model
        model_kind = "sky130-schematic"
        pdk = "sky130"
        nmos = args.nmos_model or "sky130_fd_pr__nfet_01v8"
        pmos = args.pmos_model or "sky130_fd_pr__pfet_01v8"
        include = str(model)
        notes = [
            "Schematic-level sky130 models only; no PEX, no Monte Carlo, no silicon.",
            "The model file must define sky130_fd_pr__nfet_01v8/pfet_01v8.",
        ]

    tx_rows = []
    for load_ff in args.tx_loads_ff:
        name = f"tx_{int(load_ff)}ff"
        values = run_ngspice(
            tx_netlist(include=include, nmos=nmos, pmos=pmos, load_ff=load_ff,
                       wn_um=args.wn_um, wp_um=args.wp_um, l_um=args.l_um,
                       edge_ps=args.tx_edge_ps),
            workdir, name,
        )
        if not values:
            raise RuntimeError(f"no measurements parsed for {name}")
        rise = values.get("trise")
        fall = values.get("tfall")
        tx_rows.append({
            "load_ff": float(load_ff),
            "rise_ns": None if rise is None else rise * 1e9,
            "fall_ns": None if fall is None else fall * 1e9,
            "vmax_v": values.get("vmax"),
            "vmin_v": values.get("vmin"),
            "swing_v": (
                None if values.get("vmax") is None or values.get("vmin") is None
                else values["vmax"] - values["vmin"]
            ),
        })

    rx_rows = []
    for slew_ps in args.rx_slews_ps:
        name = f"rx_{int(slew_ps)}ps"
        values = run_ngspice(
            rx_netlist(include=include, nmos=nmos, pmos=pmos, slew_ps=slew_ps,
                       wn_um=args.wn_um, wp_um=args.wp_um, l_um=args.l_um),
            workdir, name,
        )
        if not values:
            raise RuntimeError(f"no measurements parsed for {name}")
        rx_rows.append({
            "slew_ps": float(slew_ps),
            "tphl_ns": None if values.get("tphl") is None else values["tphl"] * 1e9,
            "tplh_ns": None if values.get("tplh") is None else values["tplh"] * 1e9,
        })

    return {
        "pdk": pdk,
        "model_kind": model_kind,
        "model_file": str(model_file),
        "device_models": {"nmos": nmos, "pmos": pmos},
        "sizing_um": {"wn": args.wn_um, "wp": args.wp_um, "l": args.l_um},
        "vdd_v": VDD,
        "tx": {"loads_ff": [float(x) for x in args.tx_loads_ff], "rows": tx_rows},
        "rx": {"slews_ps": [float(x) for x in args.rx_slews_ps], "rows": rx_rows},
        "extracted_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "notes": notes,
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
    ap.add_argument("--model", default=None, help="real sky130 model-card file")
    ap.add_argument("--nmos-model", default=None,
                    help="NMOS model name (default sky130_fd_pr__nfet_01v8)")
    ap.add_argument("--pmos-model", default=None,
                    help="PMOS model name (default sky130_fd_pr__pfet_01v8)")
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

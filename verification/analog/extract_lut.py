#!/usr/bin/env python3
"""Extract schematic-level TX/RX LUTs with ngspice, driven by a tech config.

The technology is selected with ``--tech`` and read from
``verification/analog/tech/<name>.yaml``.  A config describes the device style,
model include tree, sizing, supply and (for later PEX) the Magic/KLayout tech
paths.  Adding a PDK means adding a YAML file, not editing this script.

This is the transistor-level *schematic* layer, not PEX and not silicon.  The
default sky130 flow auto-detects the CIEL PM3 tree under ``~/.ciel/ciel`` or
``SKY130_ROOT``.  ``--tech generic_demo`` uses ngspice built-in level-1 MOSFETs
and is labelled as demo data; it must never be reported as PDK evidence.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
TECH_DIR = Path(__file__).resolve().parent / "tech"
DEFAULT_OUT = ROOT / "build" / "verify" / "sky130_lut.json"

DEMO_MODELS = """* generic level-1 MOSFETs (demo only, not a PDK)
.model DEMO_NMOS NMOS (LEVEL=1 VTO=0.45 KP=120u GAMMA=0.4 PHI=0.7 LAMBDA=0.02)
.model DEMO_PMOS PMOS (LEVEL=1 VTO=-0.45 KP=40u GAMMA=0.4 PHI=0.7 LAMBDA=0.02)
"""


def list_techs() -> list[str]:
    return sorted(p.stem for p in TECH_DIR.glob("*.yaml"))


def load_tech(name: str) -> dict[str, Any]:
    path = TECH_DIR / f"{name}.yaml"
    if not path.is_file():
        raise RuntimeError(
            f"unknown analog tech {name!r}; available: {', '.join(list_techs())}"
        )
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a YAML mapping")
    data["_path"] = str(path)
    data["_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(value)))


def resolve_root(cfg: dict[str, Any], override: str | None) -> Path | None:
    candidates: list[Path] = []
    if override:
        candidates.append(Path(_expand(override)))
    for pattern in cfg.get("model", {}).get("root_search", []) or []:
        expanded = _expand(pattern)
        if any(ch in expanded for ch in "*?["):
            candidates += [Path(p) for p in sorted(glob.glob(expanded))]
        else:
            candidates.append(Path(expanded))
    for candidate in candidates:
        if candidate.is_dir() and (
            candidate / "cells" / "nfet_01v8" /
            "sky130_fd_pr__nfet_01v8__tt.pm3.spice"
        ).is_file():
            return candidate
    # PM3 roots are PDK-specific; for a generic config just require a dir.
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def version_dir(root: Path) -> Path | None:
    # .../versions/<hash>/skywater-pdk/libraries/sky130_fd_pr/latest
    if root.name == "latest" and len(root.parents) > 3:
        return root.parents[3]
    return None


def _device_block(cfg: dict[str, Any], wn: float, wp: float, l_um: float) -> str:
    dev = cfg["devices"]
    if dev["style"] == "subckt":
        return (f"XMp out in vdd vdd {dev['pmos']} l={l_um}u w={wp}u nf=1\n"
                f"XMn out in 0 0 {dev['nmos']} l={l_um}u w={wn}u nf=1")
    return (f"Mp out in vdd vdd {dev['pmos']} W={wp}u L={l_um}u\n"
            f"Mn out in 0 0 {dev['nmos']} W={wn}u L={l_um}u")


def _tx_netlist(*, includes: str, devices: str, load_ff: float, edge_ps: float,
                vdd: float) -> str:
    tstep_ps = max(0.2, min(2.0, edge_ps / 20.0))
    stop_ns = 3.0 + edge_ps / 1000.0 * 4.0
    return f"""* OpenSERDES TX driver LUT
.param VDD={vdd}
{includes}
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {edge_ps}p {edge_ps}p 2n 4n)
{devices}
Cload out 0 {load_ff}f
.tran {tstep_ps}p {stop_ns}n
.meas tran vmax max v(out) from=0.0n to={stop_ns}n
.meas tran vmin min v(out) from=0.3n to={stop_ns}n
.meas tran trise trig v(out) val={vdd*0.2} rise=1 targ v(out) val={vdd*0.8} rise=1
.meas tran tfall trig v(out) val={vdd*0.8} fall=1 targ v(out) val={vdd*0.2} fall=1
.end
"""


def _rx_netlist(*, includes: str, devices: str, slew_ps: float, vdd: float) -> str:
    tstep_ps = max(0.2, min(2.0, slew_ps / 20.0))
    stop_ns = 4.0
    return f"""* OpenSERDES RX inverter slicer LUT
.param VDD={vdd}
{includes}
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {slew_ps}p {slew_ps}p 2n 4n)
{devices}
Cload out 0 20f
.tran {tstep_ps}p {stop_ns}n
.meas tran tphl trig v(in) val={vdd*0.5} rise=1 targ v(out) val={vdd*0.5} fall=1
.meas tran tplh trig v(in) val={vdd*0.5} fall=1 targ v(out) val={vdd*0.5} rise=1
.end
"""


def run_ngspice(netlist: str, workdir: Path, name: str) -> dict[str, float]:
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"{name}.sp"
    path.write_text(netlist)
    proc = subprocess.run(
        ["ngspice", "-b", str(path)], text=True, capture_output=True, timeout=240
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


def real_model_card(path: Path) -> bool:
    """Reject alias-only/placeholder single-file model cards."""
    try:
        text = path.read_text(errors="ignore").lower()
    except OSError:
        return False
    if ".subckt" in text and "sky130_fd_pr__nfet_01v8" in text:
        return True
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith(".model"):
            window = " ".join(lines[index:index + 3])
            if "level=" in window or "vto=" in window or "kp=" in window:
                return True
    return False


def _model_plan(args: argparse.Namespace, cfg: dict[str, Any],
                workdir: Path) -> dict[str, Any]:
    mode = cfg.get("model", {}).get("mode", "pm3")
    notes = list(cfg.get("notes", []) or [])

    if args.model:
        model = Path(args.model)
        if not real_model_card(model):
            raise RuntimeError(
                f"{model} has no real model cards (level/vto/kp or sky130 "
                "subckt); refusing to treat a placeholder as a PDK model"
            )
        return {
            "includes": f'.include "{model}"',
            "style": "mos",
            "nmos": args.nmos_model or cfg["devices"]["nmos"],
            "pmos": args.pmos_model or cfg["devices"]["pmos"],
            "model_files": [str(model)],
            "model_kind": f"{cfg['name']}-single-file",
            "notes": notes + ["Single model-card file; schematic only."],
        }

    if mode == "demo":
        model_file = workdir / "demo_models.sp"
        model_file.write_text(DEMO_MODELS)
        return {
            "includes": f'.include "{model_file}"',
            "style": cfg["devices"]["style"],
            "nmos": args.nmos_model or cfg["devices"]["nmos"],
            "pmos": args.pmos_model or cfg["devices"]["pmos"],
            "model_files": [str(model_file)],
            "model_kind": "generic-demo",
            "notes": notes + ["Generic level-1 demo; not PDK evidence."],
        }

    root = resolve_root(cfg, args.tech_root)
    if root is None:
        raise RuntimeError(
            f"no model tree found for tech {cfg['name']}; set the env/path in "
            f"{cfg['_path']} or pass --tech-root (or use --tech generic_demo)"
        )
    includes = []
    for rel in cfg.get("model", {}).get("includes", []) or []:
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"missing model include: {path}")
        includes.append(f'.include "{path}"')
    if not includes:
        raise RuntimeError(f"tech {cfg['name']} has no model includes")
    return {
        "includes": "\n".join(includes),
        "style": cfg["devices"]["style"],
        "nmos": args.nmos_model or cfg["devices"]["nmos"],
        "pmos": args.pmos_model or cfg["devices"]["pmos"],
        "model_files": [str(root / rel) for rel in cfg["model"]["includes"]],
        "model_kind": f"{cfg['name']}-schematic-{cfg.get('default_corner', 'tt')}",
        "notes": notes,
        "root": str(root),
    }


def _resolve_pex(cfg: dict[str, Any], model_plan: dict[str, Any]) -> dict[str, Any]:
    pex = cfg.get("pex", {}) or {}
    if pex.get("status", "none") in ("none", "scaffold"):
        return {"status": pex.get("status", "none"), "note": pex.get("note")}
    root = Path(model_plan["root"]) if model_plan.get("root") else None
    vdir = version_dir(root) if root else None
    if vdir is None:
        return {"status": "unresolved",
                "note": "cannot derive version dir from model root"}
    out: dict[str, Any] = {"status": pex.get("status"), "version_dir": str(vdir),
                           "note": pex.get("note")}
    magic = pex.get("magic", {}) or {}
    if magic:
        out["magic"] = {
            "tech": magic.get("tech"),
            "rcfile": str(vdir / magic["rcfile_rel"]) if magic.get("rcfile_rel") else None,
            "tech_file": str(vdir / magic["tech_file_rel"]) if magic.get("tech_file_rel") else None,
            "pdk_root": str((vdir / magic.get("pdk_root_rel", ".")).resolve()),
            "extractor": magic.get("extractor"),
            "available": shutil.which("magic") is not None,
        }
    klayout = pex.get("klayout", {}) or {}
    if klayout:
        out["klayout"] = {
            "tech": str(vdir / klayout["tech_rel"]) if klayout.get("tech_rel") else None,
            "lyp": str(vdir / klayout["lyp_rel"]) if klayout.get("lyp_rel") else None,
            "available": shutil.which("klayout") is not None,
        }
    return out


def check_tools(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    plan = _model_plan(args, cfg, Path(tempfile.mkdtemp(prefix="analog_check_")))
    pex = _resolve_pex(cfg, plan)
    report: dict[str, Any] = {
        "tech": cfg["name"],
        "tech_config": cfg["_path"],
        "tech_sha256": cfg["_sha256"],
        "ngspice": shutil.which("ngspice"),
        "klayout": shutil.which("klayout"),
        "magic": shutil.which("magic"),
        "model_kind": plan["model_kind"],
        "model_files": plan["model_files"],
        "pex": pex,
    }
    if report["magic"] and pex.get("magic", {}).get("rcfile"):
        rc = pex["magic"]["rcfile"]
        env = dict(os.environ)
        env["PDK_ROOT"] = pex["magic"]["pdk_root"]
        script = Path(tempfile.mkstemp(suffix=".tcl")[1])
        script.write_text('puts "TECH=[tech name]"\nquit\n')
        try:
            proc = subprocess.run(
                [report["magic"], "-dnull", "-noconsole", "-rcfile", rc, str(script)],
                text=True, capture_output=True, timeout=60, env=env,
            )
            match = re.search(r"TECH=(\S+)", proc.stdout)
            report["magic_tech"] = match.group(1) if match else None
            report["magic_ok"] = proc.returncode == 0 and bool(match)
        finally:
            script.unlink(missing_ok=True)
    if report["klayout"]:
        proc = subprocess.run(["klayout", "-v"], text=True, capture_output=True)
        report["klayout_version"] = (proc.stdout or proc.stderr).strip().splitlines()[0]
    return report


def extract(args: argparse.Namespace) -> dict[str, Any]:
    if shutil.which("ngspice") is None:
        raise RuntimeError("ngspice is required for LUT extraction")
    cfg = load_tech(args.tech)
    if cfg.get("status") == "scaffold" and not args.allow_scaffold:
        raise RuntimeError(
            f"tech {cfg['name']} is a scaffold (analog migration not done); "
            "pass --allow-scaffold only to inspect the plumbing"
        )
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if getattr(args, "check_tools", False):
        return check_tools(cfg, args)

    plan = _model_plan(args, cfg, workdir)
    pex = _resolve_pex(cfg, plan)
    wn = args.wn_um if args.wn_um is not None else float(cfg["sizing_um"]["wn"])
    wp = args.wp_um if args.wp_um is not None else float(cfg["sizing_um"]["wp"])
    l_um = args.l_um if args.l_um is not None else float(cfg["sizing_um"]["l"])
    vdd = float(cfg.get("vdd_v", 1.8))
    devices = _device_block(cfg, wn, wp, l_um)

    tx_rows = []
    for load_ff in args.tx_loads_ff:
        values = run_ngspice(
            _tx_netlist(includes=plan["includes"], devices=devices,
                        load_ff=load_ff, edge_ps=args.tx_edge_ps, vdd=vdd),
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
            _rx_netlist(includes=plan["includes"], devices=devices,
                        slew_ps=slew_ps, vdd=vdd),
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
        "tech": cfg["name"],
        "tech_config": cfg["_path"],
        "tech_sha256": cfg["_sha256"],
        "model_kind": plan["model_kind"],
        "model_files": plan["model_files"],
        "device_models": {"nmos": plan["nmos"], "pmos": plan["pmos"]},
        "sizing_um": {"wn": wn, "wp": wp, "l": l_um},
        "vdd_v": vdd,
        "corner": cfg.get("default_corner"),
        "pex": pex,
        "tx": {"loads_ff": [float(x) for x in args.tx_loads_ff], "rows": tx_rows},
        "rx": {"slews_ps": [float(x) for x in args.rx_slews_ps], "rows": rx_rows},
        "extracted_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "notes": plan["notes"],
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
    ap.add_argument("--tech", default="sky130", choices=list_techs())
    ap.add_argument("--list-tech", action="store_true")
    ap.add_argument("--tech-root", default=None,
                    help="override the tech config's model root")
    ap.add_argument("--model", default=None, help="single model-card file")
    ap.add_argument("--demo", action="store_true",
                    help="alias for --tech generic_demo")
    ap.add_argument("--allow-scaffold", action="store_true")
    ap.add_argument("--check-tools", action="store_true",
                    help="resolve model/PEX paths and report magic/klayout")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--workdir", default=str(ROOT / "build" / "verify" / "analog"))
    ap.add_argument("--wn-um", type=float, default=None)
    ap.add_argument("--wp-um", type=float, default=None)
    ap.add_argument("--l-um", type=float, default=None)
    ap.add_argument("--nmos-model", default=None)
    ap.add_argument("--pmos-model", default=None)
    ap.add_argument("--tx-edge-ps", type=float, default=50.0)
    ap.add_argument("--tx-loads-ff", type=float, nargs="*",
                    default=[10.0, 50.0, 100.0, 200.0])
    ap.add_argument("--rx-slews-ps", type=float, nargs="*",
                    default=[20.0, 50.0, 100.0, 200.0])
    args = ap.parse_args()

    if args.list_tech:
        for name in list_techs():
            cfg = load_tech(name)
            print(f"{name:16s} pdk={cfg.get('pdk'):12s} status={cfg.get('status')}")
        return 0
    if args.demo:
        args.tech = "generic_demo"

    report = extract(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    if args.check_tools:
        print(f"tech: {report['tech']} ({report['tech_config']})")
        print(f"  model: {report['model_kind']}")
        for path in report["model_files"]:
            print(f"    {path}")
        print(f"  ngspice: {report['ngspice']}")
        print(f"  klayout: {report['klayout']} {report.get('klayout_version', '')}")
        print(f"  magic: {report['magic']} tech={report.get('magic_tech')}")
        print(f"  pex: {json.dumps(report['pex'], indent=2)}")
        return 0

    print(f"tech: {report['tech']} pdk={report['pdk']} ({report['model_kind']})")
    for row in report["tx"]["rows"]:
        print(f"  TX load {row['load_ff']:6.1f} fF: "
              f"rise={row['rise_ns']} ns fall={row['fall_ns']} ns "
              f"swing={row['swing_v']} V")
    for row in report["rx"]["rows"]:
        print(f"  RX slew {row['slew_ps']:6.1f} ps: "
              f"tphl={row['tphl_ns']} ns tplh={row['tplh_ns']} ns")
    print(f"wrote {out}")
    if report["pdk"] == "generic":
        print("WARNING: generic-demo data is not PDK data and is not a claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

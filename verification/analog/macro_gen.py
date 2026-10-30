#!/usr/bin/env python3
"""Portable analog macro generator (contract + SPICE + blackbox Verilog).

Layers:
  topology   : small process-independent circuit descriptions
  device     : tech YAML device names, W/L, supply
  physical   : only emitted when a PDK layout template/GDS is supplied

This generator does not invent GDS/LEF.  sky130 has a supported schematic view;
gf180mcu and IHP are scaffolds and fail closed for generated physical macros.

Supported topologies:
  tx_inv_chain   : odd CMOS inverter chain with N/P parallel fingers
  rx_res_fb_inv  : inverter + series diode-connected long-channel PMOS feedback
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.gds_audit import audit as audit_gds  # noqa: E402
TECH_DIR = ROOT / "verification/analog/tech"


def load_tech(name: str) -> dict:
    path = TECH_DIR / f"{name}.yaml"
    if not path.is_file():
        raise SystemExit(f"unknown tech {name!r}: {path} not found")
    data = yaml.safe_load(path.read_text()) or {}
    caps = data.get("capabilities") or {}
    if not caps.get("macro_generation", False):
        raise SystemExit(
            f"tech {name}: capabilities.macro_generation=false; macro generation "
            "is fail-closed until the device -> PCell -> DRC/LVS -> PEX -> "
            "electrical-characterization backend is available. status is not a "
            "substitute for physical evidence."
        )
    dev = data.get("devices", {})
    if str(dev.get("nmos", "")).startswith("TBD") or str(dev.get("pmos", "")).startswith("TBD"):
        raise SystemExit(f"tech {name}: device names are still TBD")
    return data


def _mos(xname: str, d: str, g: str, s: str, b: str, model: str,
         w: float, l: float, nf: int = 1, finger_param: str = "nf") -> str:
    # IHP's SG13G2 subcircuit uses ``ng`` for the finger count; sky130 and
    # gf180mcu use ``nf``.  Keep this PDK-specific instead of hardcoding nf.
    return f"X{xname} {d} {g} {s} {b} {model} w={w:g}u l={l:g}u {finger_param}={nf}"


def tx_chain(tech: dict, stages: list[int], w_finger_um: float,
             l_um: float, name: str = "tx_inv_chain") -> tuple[str, dict]:
    nmos = tech["devices"]["nmos"]
    pmos = tech["devices"]["pmos"]
    fp = tech.get("devices", {}).get("finger_param", "nf")
    lines = [f"* Generated {tech['name']} TX inverter chain", f".subckt {name} VDD VSS IN OUT"]
    prev = "IN"
    for i, npar in enumerate(stages):
        ppar = npar * 2
        out = "OUT" if i == len(stages) - 1 else f"n{i}"
        for k in range(npar):
            lines.append(_mos(f"MN{i}_{k}", out, prev, "VSS", "VSS", nmos,
                              w_finger_um, l_um, finger_param=fp))
        for k in range(ppar):
            lines.append(_mos(f"MP{i}_{k}", out, prev, "VDD", "VDD", pmos,
                              w_finger_um, l_um, finger_param=fp))
        prev = out
    lines.append(".ends")
    meta = {
        "topology": "tx_inv_chain",
        "stages_n_parallel": stages,
        "stages_p_parallel": [n * 2 for n in stages],
        "w_finger_um": w_finger_um,
        "l_um": l_um,
        "polarity": "inverting",
    }
    return "\n".join(lines) + "\n", meta


def rx_feedback(tech: dict, n_parallel: int, p_parallel: int,
                w_finger_um: float, l_um: float,
                fb_w_um: float, fb_l_um: float,
                name: str = "rx_res_fb_inv") -> tuple[str, dict]:
    nmos = tech["devices"]["nmos"]
    pmos = tech["devices"]["pmos"]
    fp = tech.get("devices", {}).get("finger_param", "nf")
    lines = [f"* Generated {tech['name']} RX feedback inverter",
             f".subckt {name} VDD VSS IN OUT"]
    for k in range(n_parallel):
        lines.append(_mos(f"MN{k}", "OUT", "IN", "VSS", "VSS", nmos,
                          w_finger_um, l_um, finger_param=fp))
    for k in range(p_parallel):
        lines.append(_mos(f"MP{k}", "OUT", "IN", "VDD", "VDD", pmos,
                          w_finger_um, l_um, finger_param=fp))
    # Two series diode-connected long-channel PMOS: OUT -> net06 -> IN.
    lines.append(_mos("MFB1", "OUT", "OUT", "fb", "VDD", pmos, fb_w_um, fb_l_um,
                      finger_param=fp))
    lines.append(_mos("MFB0", "fb", "fb", "IN", "VDD", pmos, fb_w_um, fb_l_um,
                      finger_param=fp))
    lines.append(".ends")
    meta = {
        "topology": "rx_res_fb_inv",
        "inverter_n_parallel": n_parallel,
        "inverter_p_parallel": p_parallel,
        "w_finger_um": w_finger_um,
        "l_um": l_um,
        "feedback": {
            "kind": "nonlinear_pmos",
            "series_devices": 2,
            "w_um": fb_w_um,
            "l_um": fb_l_um,
            "body": "VDD",
            "not_a_linear_resistor": True,
        },
        "feedback_devices": 2,
        "feedback_w_um": fb_w_um,
        "feedback_l_um": fb_l_um,
        "feedback_kind": "nonlinear_pmos",
        "polarity": "inverting",
    }
    return "\n".join(lines) + "\n", meta


def macro_v(name: str) -> str:
    return f"""// Generated canonical analog black box; physical views are not implied.
(* blackbox *)
module {name} (
    input  wire VDD,
    input  wire VSS,
    input  wire IN,
    output wire OUT
);
endmodule
"""


def _report_match(path: Path | None) -> bool:
    if path is None:
        return False
    text = path.read_text(errors="replace")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        if isinstance(data, dict):
            if data.get("match") is True or data.get("clean") is True:
                return True
            if str(data.get("status", "")).lower() in ("match", "matched", "clean", "pass"):
                return True
        return False
    lowered = text.lower()
    return ("circuits match uniquely" in lowered
            or "lvs match" in lowered
            or "lvs clean" in lowered)


def _drc_clean(path: Path | None) -> bool:
    if path is None:
        return False
    text = path.read_text(errors="replace")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        return bool(isinstance(data, dict) and data.get("clean") is True)
    return "drc clean" in text.lower() or "errors=0" in text.lower()


def write_macro(outdir: Path, tech: dict, topology: str, name: str,
                circuit: str, meta: dict, layout_gds: Path | None,
                lef_path: Path | None = None, cdl_path: Path | None = None,
                pex_path: Path | None = None,
                lvs_report: Path | None = None,
                drc_report: Path | None = None) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    layout_physical = False
    layout_audit = None
    if layout_gds is not None:
        layout_audit = audit_gds(layout_gds, tech["name"])
        layout_physical = bool(layout_audit.get("physical"))
        if not layout_physical:
            raise SystemExit(
                f"{layout_gds}: not a physical transistor layout " 
                f"(missing {layout_audit.get('missing_physical_layers')}); " 
                "refusing to advertise it as a signoff view"
            )
        if layout_audit.get("cell") != name:
            raise SystemExit(
                f"{layout_gds}: top cell {layout_audit.get('cell')!r} != requested name {name!r}; "
                "refusing to attach views to the wrong macro"
            )
    lvs_ok = _report_match(lvs_report)
    drc_ok = _drc_clean(drc_report)
    pex_ok = bool(pex_path and Path(pex_path).is_file())
    signoff = bool(layout_physical and drc_ok and lvs_ok and pex_ok)
    if lvs_report is not None and not lvs_ok:
        raise SystemExit(
            f"{lvs_report}: LVS report does not prove a unique match; "
            "refusing to attach it as signoff evidence"
        )
    if drc_report is not None and not drc_ok:
        raise SystemExit(f"{drc_report}: DRC report is not clean")
    if signoff:
        stage = "SIGNOFF_ELIGIBLE"
    elif lvs_ok:
        stage = "LVS_MATCHED"
    elif drc_ok:
        stage = "GEOMETRY_DRC_PASS"
    elif layout_physical:
        stage = "LAYOUT_ATTACHED"
    else:
        stage = "SCHEMATIC_ONLY"
    (outdir / "circuit.spice").write_text(circuit)
    (outdir / "macro.v").write_text(macro_v(name))
    views = {
        "circuit_spice": {"path": str(outdir / "circuit.spice"), "status": "available",
                          "format": "spice", "top": name},
        "macro_v": {"path": str(outdir / "macro.v"), "status": "available"},
        "layout": ({"path": str(layout_gds), "status": "available", "format": "gds",
                    "physical": layout_physical, "audit": layout_audit}
                   if layout_gds else {"status": "missing", "physical": False}),
        "lef": ({"path": str(lef_path), "status": "available", "format": "lef", "top": name}
                if lef_path else {"status": "missing"}),
        "cdl": ({"path": str(cdl_path), "status": "available", "format": "cdl", "top": name}
                if cdl_path else {"status": "missing"}),
        "liberty": {"status": "missing"},
        "drc": ({"path": str(drc_report), "status": "clean" if drc_ok else "unusable"}
                if drc_report else {"status": "missing"}),
        "lvs": ({"path": str(lvs_report), "status": "match-unique" if lvs_ok else "unusable"}
                if lvs_report else {"status": "missing"}),
        "pex": ({"path": str(pex_path), "status": "available", "format": "spice", "top": name}
                if pex_path else {"status": "missing"}),
    }
    manifest = {
        "schema": 1,
        "name": name,
        "kind": "analog_macro_generated",
        "tech": tech["name"],
        "pdk": tech.get("pdk"),
        "supply_v": tech.get("vdd_v"),
        **meta,
        "status": stage,
        "physical_layout": layout_physical,
        "physical_signoff_eligible": signoff,
        "topology_match": bool(lvs_ok),
        "canonical_ports": [
            {"name": p, "index": i, "direction": "power" if p == "VDD" else
             "ground" if p == "VSS" else "input" if p == "IN" else "output"}
            for i, p in enumerate(("VDD", "VSS", "IN", "OUT"))
        ],
        "views": views,
        "note": ("GDS attachment alone is LAYOUT_ATTACHED, not topology proof; "
                 "no fabricated GDS/LEF/Liberty is emitted. SIGNOFF_ELIGIBLE "
                 "requires clean DRC report, unique-match LVS report and PEX "
                 "for this exact generated circuit."),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tech", default="sky130")
    ap.add_argument("--topology", choices=("tx_inv_chain", "rx_res_fb_inv"),
                    required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stages", default="1,3,9")
    ap.add_argument("--n-parallel", type=int, default=3)
    ap.add_argument("--p-parallel", type=int, default=6)
    ap.add_argument("--w-finger-um", type=float, default=5.0)
    ap.add_argument("--l-um", type=float, default=0.15)
    ap.add_argument("--fb-w-um", type=float, default=0.55)
    ap.add_argument("--fb-l-um", type=float, default=8.0)
    ap.add_argument("--layout-gds", default=None,
                    help="explicit PDK layout template; omitted means physical views are missing")
    ap.add_argument("--lef", default=None, help="explicit LEF view for the layout")
    ap.add_argument("--cdl", default=None, help="explicit CDL/LVS view for the layout")
    ap.add_argument("--pex", default=None, help="explicit Magic/RC PEX SPICE view")
    ap.add_argument("--lvs-report", default=None,
                    help="explicit LVS report proving this exact circuit matches the GDS")
    ap.add_argument("--drc-report", default=None,
                    help="explicit DRC report for the attached GDS")
    args = ap.parse_args()
    tech = load_tech(args.tech)
    layout = Path(args.layout_gds) if args.layout_gds else None
    if args.name:
        name = args.name
    elif layout is not None:
        name = audit_gds(layout, tech["name"]).get("cell") or args.topology
    else:
        name = args.topology
    if args.topology == "tx_inv_chain":
        stages = [int(x) for x in args.stages.split(",") if x.strip()]
        if not stages or any(x <= 0 for x in stages):
            raise SystemExit("stages must be positive comma-separated integers")
        circuit, meta = tx_chain(tech, stages, args.w_finger_um, args.l_um, name)
    else:
        circuit, meta = rx_feedback(
            tech, args.n_parallel, args.p_parallel, args.w_finger_um, args.l_um,
            args.fb_w_um, args.fb_l_um, name,
        )
    lef = Path(args.lef) if args.lef else None
    cdl = Path(args.cdl) if args.cdl else None
    pex = Path(args.pex) if args.pex else None
    if bool(lef) != bool(cdl):
        raise SystemExit("--lef and --cdl must be supplied together")
    lvs_report = Path(args.lvs_report) if args.lvs_report else None
    drc_report = Path(args.drc_report) if args.drc_report else None
    manifest = write_macro(Path(args.out), tech, args.topology, name,
                           circuit, meta, layout, lef, cdl, pex,
                           lvs_report, drc_report)
    print(f"generated {name} ({args.tech}/{args.topology}) -> {args.out}")
    print(f"  physical layout: {manifest['views']['layout']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

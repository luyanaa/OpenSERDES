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
    if data.get("status") != "supported":
        raise SystemExit(
            f"tech {name}: status={data.get('status')!r}; "
            "macro generation is fail-closed until device models/layout are verified"
        )
    dev = data.get("devices", {})
    if str(dev.get("nmos", "")).startswith("TBD") or str(dev.get("pmos", "")).startswith("TBD"):
        raise SystemExit(f"tech {name}: device names are still TBD")
    return data


def _mos(xname: str, d: str, g: str, s: str, b: str, model: str,
         w: float, l: float, nf: int = 1) -> str:
    return f"X{xname} {d} {g} {s} {b} {model} w={w:g}u l={l:g}u nf={nf}"


def tx_chain(tech: dict, stages: list[int], w_finger_um: float,
             l_um: float) -> tuple[str, dict]:
    nmos = tech["devices"]["nmos"]
    pmos = tech["devices"]["pmos"]
    lines = [f"* Generated {tech['name']} TX inverter chain", ".subckt tx_inv_chain VDD VSS IN OUT"]
    prev = "IN"
    for i, npar in enumerate(stages):
        ppar = npar * 2
        out = "OUT" if i == len(stages) - 1 else f"n{i}"
        for k in range(npar):
            lines.append(_mos(f"MN{i}_{k}", out, prev, "VSS", "VSS", nmos,
                              w_finger_um, l_um))
        for k in range(ppar):
            lines.append(_mos(f"MP{i}_{k}", out, prev, "VDD", "VDD", pmos,
                              w_finger_um, l_um))
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
                fb_w_um: float, fb_l_um: float) -> tuple[str, dict]:
    nmos = tech["devices"]["nmos"]
    pmos = tech["devices"]["pmos"]
    lines = [f"* Generated {tech['name']} RX feedback inverter",
             ".subckt rx_res_fb_inv VDD VSS IN OUT"]
    for k in range(n_parallel):
        lines.append(_mos(f"MN{k}", "OUT", "IN", "VSS", "VSS", nmos,
                          w_finger_um, l_um))
    for k in range(p_parallel):
        lines.append(_mos(f"MP{k}", "OUT", "IN", "VDD", "VDD", pmos,
                          w_finger_um, l_um))
    # Two series diode-connected long-channel PMOS: OUT -> net06 -> IN.
    lines.append(_mos("MFB1", "OUT", "OUT", "fb", "VDD", pmos, fb_w_um, fb_l_um))
    lines.append(_mos("MFB0", "fb", "fb", "IN", "VDD", pmos, fb_w_um, fb_l_um))
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


def write_macro(outdir: Path, tech: dict, topology: str, name: str,
                circuit: str, meta: dict, layout_gds: Path | None) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    layout_physical = False
    layout_audit = None
    if layout_gds is not None:
        layout_audit = audit_gds(layout_gds)
        layout_physical = bool(layout_audit.get("physical"))
        if not layout_physical:
            raise SystemExit(
                f"{layout_gds}: not a physical transistor layout " 
                f"(missing {layout_audit.get('missing_physical_layers')}); " 
                "refusing to advertise it as a signoff view"
            )
    (outdir / "circuit.spice").write_text(circuit)
    (outdir / "macro.v").write_text(macro_v(name))
    views = {
        "circuit_spice": {"path": str(outdir / "circuit.spice"), "status": "available",
                          "format": "spice", "top": name},
        "macro_v": {"path": str(outdir / "macro.v"), "status": "available"},
        "layout": ({"path": str(layout_gds), "status": "available", "format": "gds",
                    "physical": layout_physical, "audit": layout_audit}
                   if layout_gds else {"status": "missing", "physical": False}),
        "lef": {"status": "missing"},
        "liberty": {"status": "missing"},
        "pex": {"status": "missing"},
    }
    manifest = {
        "schema": 1,
        "name": name,
        "kind": "analog_macro_generated",
        "tech": tech["name"],
        "pdk": tech.get("pdk"),
        "supply_v": tech.get("vdd_v"),
        **meta,
        "canonical_ports": [
            {"name": p, "index": i, "direction": "power" if p == "VDD" else
             "ground" if p == "VSS" else "input" if p == "IN" else "output"}
            for i, p in enumerate(("VDD", "VSS", "IN", "OUT"))
        ],
        "views": views,
        "physical_layout": layout_physical,
        "note": ("Schematic/blackbox generation only unless an explicit physical "
                 "PDK layout template is supplied; no fabricated GDS/LEF/Liberty."),
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
    args = ap.parse_args()
    tech = load_tech(args.tech)
    name = args.name or args.topology
    if args.topology == "tx_inv_chain":
        stages = [int(x) for x in args.stages.split(",") if x.strip()]
        if not stages or any(x <= 0 for x in stages):
            raise SystemExit("stages must be positive comma-separated integers")
        circuit, meta = tx_chain(tech, stages, args.w_finger_um, args.l_um)
    else:
        circuit, meta = rx_feedback(
            tech, args.n_parallel, args.p_parallel, args.w_finger_um, args.l_um,
            args.fb_w_um, args.fb_l_um,
        )
    layout = Path(args.layout_gds) if args.layout_gds else None
    manifest = write_macro(Path(args.out), tech, args.topology, name,
                           circuit, meta, layout)
    print(f"generated {name} ({args.tech}/{args.topology}) -> {args.out}")
    print(f"  physical layout: {manifest['views']['layout']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Portable stdcell-backed analog-macro netlist generator.

This backend emits a physical-intent standard-cell netlist only.  It does not
claim GDS/LEF/Liberty; layout and timing views are produced by the target PDK's
standard-cell flow, and unsupported PDKs fail closed.

The current C integration uses the emitted TX (inv_1 -> inv_4 -> inv_16) and
RX (inv_4) soft modules.  Hard macro layout/PEX evidence is separate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TECH_DIR = ROOT / "verification/analog/tech"


def load_tech(name: str) -> dict:
    path = TECH_DIR / f"{name}.yaml"
    if not path.is_file():
        raise SystemExit(f"unknown tech {name!r}")
    tech = yaml.safe_load(path.read_text()) or {}
    if tech.get("status") != "supported":
        raise SystemExit(f"tech {name}: fail-closed (status={tech.get('status')})")
    if not tech.get("stdcell"):
        raise SystemExit(f"tech {name}: no verified stdcell binding")
    return tech


def tx_netlist(tech: dict, name: str) -> str:
    sc = tech["stdcell"]
    return f"""// Generated {tech['name']} stdcell TX macro; physical views are flow-owned.
module {name} (
    input  wire VDD,
    input  wire VSS,
    input  wire IN,
    output wire OUT
);
    wire n1, n2;
    {sc['inv_1']}  u0 (.A(IN), .Y(n1));
    {sc['inv_4']}  u1 (.A(n1), .Y(n2));
    {sc['inv_16']} u2 (.A(n2), .Y(OUT));
endmodule
"""


def rx_netlist(tech: dict, name: str) -> str:
    sc = tech["stdcell"]
    return f"""// Generated {tech['name']} stdcell RX macro; physical views are flow-owned.
module {name} (
    input  wire VDD,
    input  wire VSS,
    input  wire IN,
    output wire OUT
);
    {sc['inv_4']} u0 (.A(IN), .Y(OUT));
endmodule
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tech", default="sky130")
    ap.add_argument("--topology", choices=("tx_inv_chain", "rx_res_fb_inv"),
                    required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    tech = load_tech(args.tech)
    name = args.name or args.topology
    text = (tx_netlist if args.topology == "tx_inv_chain" else rx_netlist)(tech, name)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "macro.v").write_text(text)
    manifest = {
        "schema": 1,
        "name": name,
        "topology": args.topology,
        "backend": "stdcell",
        "tech": tech["name"],
        "stdcell": tech["stdcell"],
        "views": {
            "macro_v": {"path": str(out / "macro.v"), "status": "available"},
            "layout": {"status": "flow-owned"},
            "lef": {"status": "flow-owned"},
            "liberty": {"status": "flow-owned"},
            "pex": {"status": "flow-owned"},
        },
        "note": "Netlist backend only; no fabricated physical views.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"generated {name} stdcell backend -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

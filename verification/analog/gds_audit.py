#!/usr/bin/env python3
"""Classify an analog macro GDS as physical vs pin/abstract-only.

The repository's original macro exports contain only met1/pin and nwell/poly
pin layers.  They are useful for LEF/PDN experiments but are not a transistor
layout and must never be used as DRC/LVS/PEX signoff evidence.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = r"""
import json, os, pya
ly = pya.Layout(); ly.read(os.environ["GDS"]); c = ly.top_cell()
layers = []
for li in ly.layer_indexes():
    info = ly.get_info(li)
    n = sum(1 for _ in c.begin_shapes_rec(li))
    if n:
        layers.append({"layer": info.layer, "datatype": info.datatype,
                       "name": info.name, "count": n})
print("AUDIT_JSON=" + json.dumps({"cell": c.name, "layers": layers,
                                  "bbox": [c.bbox().p1.x, c.bbox().p1.y,
                                           c.bbox().p2.x, c.bbox().p2.y]}))
"""

# PDK-specific physical layer maps.  A GDS is never judged with another
# process's layer numbers: the same (65,20) can mean different things in a
# different stack.
TECH_LAYERS = {
    "sky130": {
        "required": {(65, 20): "diff", (66, 20): "poly", (67, 20): "li1"},
        "abstract_hints": {(68, 16): "met1.pin"},
    },
    "gf180mcu": {
        "required": {(22, 0): "comp", (30, 0): "poly2", (34, 0): "metal1"},
        "abstract_hints": {(34, 10): "metal1.label"},
    },
    "ihp_sg13g2": {
        "required": {(1, 0): "active", (5, 0): "gatpoly", (8, 0): "metal1"},
        "abstract_hints": {(8, 2): "metal1.label"},
    },
}


def audit(gds: Path, tech: str = "sky130") -> dict:
    if tech not in TECH_LAYERS:
        raise ValueError(f"unknown PDK layer map {tech!r}; choose from {sorted(TECH_LAYERS)}")
    required = TECH_LAYERS[tech]["required"]
    abstract_hints = TECH_LAYERS[tech]["abstract_hints"]
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "audit.py"
        script.write_text(SCRIPT)
        proc = subprocess.run(["klayout", "-b", "-r", str(script)],
                              text=True, capture_output=True,
                              env={**__import__("os").environ, "GDS": str(gds)},
                              timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    for line in proc.stdout.splitlines():
        if line.startswith("AUDIT_JSON="):
            data = json.loads(line.split("=", 1)[1])
            break
    else:
        raise RuntimeError("klayout audit produced no JSON")
    present = {(x["layer"], x["datatype"]): x["count"] for x in data["layers"]}
    missing = [name for key, name in required.items() if not present.get(key)]
    data["physical"] = not missing
    data["missing_physical_layers"] = missing
    data["abstract_only"] = bool(missing)
    data["tech"] = tech
    data["required_layers"] = {f"{k[0]}/{k[1]}": v for k, v in required.items()}
    data["abstract_hints"] = {f"{k[0]}/{k[1]}": v for k, v in abstract_hints.items()}
    data["gds"] = str(gds)
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tech", default="sky130",
                    choices=sorted(TECH_LAYERS))
    args = ap.parse_args()
    data = audit(Path(args.gds), args.tech)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps({k: data[k] for k in ("gds", "cell", "physical", "missing_physical_layers")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

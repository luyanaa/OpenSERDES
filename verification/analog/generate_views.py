#!/usr/bin/env python3
"""Generate abstract LEF and black-box Liberty from a sky130 macro GDS.

This is deliberately an **abstract** view: pin rectangles are placed at the
GDS text-label coordinates, not extracted metal shapes.  The generated Liberty
has no timing arcs because these are analog macros.  Both are inputs for a
mixed-signal integration placeholder, not physical/timing signoff.

KLayout is used only to read the GDS bbox and text labels.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.macro_contract import load_manifest  # noqa: E402

KLAYOUT_SCRIPT = r'''
import pya, json
layout = pya.Layout()
layout.read(r"{gds}")
c = layout.top_cell()
b = c.bbox()
labels = []
for li in layout.layer_indexes():
    try:
        for s in c.shapes(li).each():
            if s.is_text():
                p = s.text_dpos
                labels.append({{"text": s.text.string, "x": p.x, "y": p.y}})
    except Exception:
        pass
print("VIEW_JSON=" + json.dumps({{"cell": c.name, "dbu": layout.dbu,
    "bbox": [b.p1.x * layout.dbu, b.p1.y * layout.dbu,
             b.p2.x * layout.dbu, b.p2.y * layout.dbu],
    "labels": labels}}))
'''


def gds_info(gds: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "read_gds.py"
        script.write_text(KLAYOUT_SCRIPT.format(gds=str(gds)))
        proc = subprocess.run(["klayout", "-b", "-r", str(script)],
                              text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"klayout failed:\n{proc.stdout}\n{proc.stderr}")
    for line in proc.stdout.splitlines():
        if line.startswith("VIEW_JSON="):
            return json.loads(line.split("=", 1)[1])
    raise RuntimeError(f"klayout did not emit VIEW_JSON:\n{proc.stdout[-2000:]}")


def _canonical_label(text: str, aliases: dict[str, str]) -> str | None:
    return aliases.get(text.upper())


def generate(manifest_path: Path, outdir: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    name = manifest["name"]
    ports = [p["name"] for p in manifest["canonical_ports"]]
    aliases: dict[str, str] = {}
    for canonical in ports:
        for alias in [canonical, *manifest.get("aliases", {}).get(canonical, [])]:
            aliases[str(alias).upper()] = canonical
    gds_rel = manifest["views"]["layout"]["path"]
    gds = ROOT / gds_rel
    info = gds_info(gds)
    x0, y0, x1, y1 = [float(v) for v in info["bbox"]]
    width, height = x1 - x0, y1 - y0
    pins: dict[str, tuple[float, float]] = {}
    for lab in info["labels"]:
        canonical = _canonical_label(lab["text"], aliases)
        if canonical:
            # KLayout Text.text_dpos is reported in micron coordinates.
            pins[canonical] = (float(lab["x"]) - x0, float(lab["y"]) - y0)
    missing = [p for p in ports if p not in pins]
    if missing:
        raise RuntimeError(f"{name}: missing GDS labels for pins {missing}")
    outdir.mkdir(parents=True, exist_ok=True)
    lef = outdir / "abstract.lef"
    lib = outdir / "macro.lib"
    lines = [
        "VERSION 5.8 ;", 'BUSBITCHARS "[]" ;', 'DIVIDERCHAR "/" ;',
        f"MACRO {name}", "  CLASS CORE ;", "  ORIGIN 0 0 ;",
        f"  FOREIGN {name} 0 0 ;", f"  SIZE {width:.3f} BY {height:.3f} ;",
        "  SYMMETRY X Y ;",
    ]
    for port in ports:
        x, y = pins[port]
        half = 0.10
        xa, ya = max(0.0, x - half), max(0.0, y - half)
        xb, yb = min(width, x + half), min(height, y + half)
        use = ("POWER" if port == "VDD" else
               "GROUND" if port == "VSS" else "SIGNAL")
        direction = ("INOUT" if port in ("VDD", "VSS") else
                     "INPUT" if port == "IN" else "OUTPUT")
        lines += [f"  PIN {port}", f"    DIRECTION {direction} ;",
                  f"    USE {use} ;", "    PORT", "      LAYER met1 ;",
                  f"        RECT {xa:.3f} {ya:.3f} {xb:.3f} {yb:.3f} ;",
                  "    END", f"  END {port}"]
    lines += [f"END {name}", "END LIBRARY", ""]
    lef.write_text("\n".join(lines))
    llines = [
        "library (sky130_analog_blackbox) {",
        '  delay_model : table_lookup ;', '  time_unit : "1ns" ;',
        '  voltage_unit : "1V" ;', '  current_unit : "1mA" ;',
        '  capacitive_load_unit (1, pf) ;',
        f"  cell ({name}) {{", f"    area : {width * height:.4f} ;",
        "    dont_touch : true ;",
    ]
    for port in ports:
        direction = "inout" if port in ("VDD", "VSS") else (
            "input" if port == "IN" else "output")
        pg = ("pg_type : primary_power ;" if port == "VDD" else
              "pg_type : primary_ground ;" if port == "VSS" else "")
        llines += [f"    pin ({port}) {{", f"      direction : {direction} ;"]
        if pg:
            llines.append("      " + pg)
        llines += ["    }"]
    llines += ["  }", "}", ""]
    lib.write_text("\n".join(llines))
    meta = {
        "name": name,
        "gds": str(gds),
        "cell": info["cell"],
        "bbox_um": [x0, y0, x1, y1],
        "size_um": [width, height],
        "pins_um": {k: list(v) for k, v in pins.items()},
        "lef": str(lef), "liberty": str(lib),
        "lef_kind": "abstract-label-pins-not-metal-extracted",
        "liberty_kind": "analog-blackbox-no-timing-arcs",
    }
    (outdir / "views.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", nargs="*", default=None)
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()
    manifests = args.manifest or [
        str(ROOT / "analog/macros/tx_inv_chain_1_3_9/manifest.json"),
        str(ROOT / "analog/macros/rx_res_fb_inv/manifest.json"),
    ]
    for m in manifests:
        path = Path(m)
        outdir = Path(args.outdir) if args.outdir else path.parent
        meta = generate(path, outdir)
        print(f"{meta['name']}: {meta['size_um']} -> {meta['lef']}, {meta['liberty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

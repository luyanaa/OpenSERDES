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



PIN_SCRIPT = r'''
import pya, os, json
l = pya.Layout(); l.read(os.environ["GDS"]); c = l.top_cell(); c.flatten(True)
boxes = []
for li in l.layer_indexes():
    info = l.get_info(li)
    if info.layer == 68 and info.datatype == 16:
        for s in c.shapes(li).each():
            if s.is_box():
                b = s.bbox()
                boxes.append([b.p1.x * l.dbu, b.p1.y * l.dbu,
                              b.p2.x * l.dbu, b.p2.y * l.dbu])
print("PIN_JSON=" + json.dumps(boxes))
'''


def gds_pin_shapes(gds: Path) -> list[list[float]]:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "read_pins.py"
        script.write_text(PIN_SCRIPT)
        proc = subprocess.run(["klayout", "-b", "-r", str(script)],
                              text=True, capture_output=True, timeout=120,
                              env={**__import__("os").environ, "GDS": str(gds)})
    if proc.returncode != 0:
        raise RuntimeError(f"klayout pin read failed:\n{proc.stdout}\n{proc.stderr}")
    for line in proc.stdout.splitlines():
        if line.startswith("PIN_JSON="):
            return json.loads(line.split("=", 1)[1])
    raise RuntimeError(f"klayout no PIN_JSON:\n{proc.stdout[-2000:]}")

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
    pin_boxes: dict[str, list[float]] = {}
    shapes = gds_pin_shapes(gds)
    for lab in info["labels"]:
        canonical = _canonical_label(lab["text"], aliases)
        if not canonical or not shapes:
            continue
        x, y = float(lab["x"]), float(lab["y"])
        def distance(box):
            return abs((box[0] + box[2]) / 2 - x) + abs((box[1] + box[3]) / 2 - y)
        best = min(shapes, key=distance)
        if distance(best) <= 1.5:
            pin_boxes[canonical] = [
                max(0.0, best[0] - x0), max(0.0, best[1] - y0),
                min(width, best[2] - x0), min(height, best[3] - y0),
            ]
    missing = [p for p in ports if p not in pin_boxes]
    if missing:
        raise RuntimeError(f"{name}: missing met1 pin shapes for pins {missing}")
    outdir.mkdir(parents=True, exist_ok=True)
    lef = outdir / "abstract.lef"
    lib = outdir / "macro.lib"
    lines = [
        "VERSION 5.8 ;", 'BUSBITCHARS "[]" ;', 'DIVIDERCHAR "/" ;',
        f"MACRO {name}", "  CLASS BLOCK ;", "  ORIGIN 0 0 ;",
        f"  FOREIGN {name} 0 0 ;", f"  SIZE {width:.3f} BY {height:.3f} ;",
        "  SYMMETRY X Y ;",
    ]
    for port in ports:
        xa, ya, xb, yb = pin_boxes[port]
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
        '  nom_process : 1.0 ;', '  nom_temperature : 25.0 ;',
        '  nom_voltage : 1.8 ;',
        '  slew_lower_threshold_pct_rise : 20.0 ;',
        '  slew_upper_threshold_pct_rise : 80.0 ;',
        '  slew_lower_threshold_pct_fall : 20.0 ;',
        '  slew_upper_threshold_pct_fall : 80.0 ;',
        '  input_threshold_pct_rise : 50.0 ;',
        '  input_threshold_pct_fall : 50.0 ;',
        '  output_threshold_pct_rise : 50.0 ;',
        '  output_threshold_pct_fall : 50.0 ;',
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
        "pins_um": {k: list(v) for k, v in pin_boxes.items()},
        "lef": str(lef), "liberty": str(lib),
        "lef_kind": "gds-met1-pin-shapes",
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
    made = []
    for m in manifests:
        path = Path(m)
        outdir = Path(args.outdir) if args.outdir else path.parent
        meta = generate(path, outdir)
        made.append(Path(meta["lef"]))
        print(f"{meta['name']}: {meta['size_um']} -> {meta['lef']}, {meta['liberty']}")
    if made:
        combined = ROOT / "analog/macros/sky130_analog_macros.lef"
        lines = ["VERSION 5.8 ;", 'BUSBITCHARS "[]" ;', 'DIVIDERCHAR "/" ;']
        for lef in made:
            inside = False
            for line in lef.read_text().splitlines():
                if line.startswith("MACRO "):
                    inside = True
                if inside:
                    lines.append(line)
                if inside and line.startswith("END ") and not line.startswith("END LIBRARY"):
                    inside = False
        lines.append("END LIBRARY")
        combined.write_text("\n".join(lines) + "\n")
        print(f"combined LEF: {combined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

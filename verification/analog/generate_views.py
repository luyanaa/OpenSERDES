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
    if info.datatype == 16 and info.layer in (68, 69, 70, 71, 72):
        layer = {68: "met1", 69: "met2", 70: "met3", 71: "met4", 72: "met5"}[info.layer]
        for s in c.shapes(li).each():
            if s.is_box():
                b = s.bbox()
                boxes.append({"layer": layer,
                              "rect": [b.p1.x * l.dbu, b.p1.y * l.dbu,
                                       b.p2.x * l.dbu, b.p2.y * l.dbu]})
print("PIN_JSON=" + json.dumps(boxes))
'''


def gds_pin_shapes(gds: Path) -> list[dict[str, Any]]:
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
    power_layer = manifest.get("pdn_power_layer")
    pin_boxes: dict[str, dict[str, Any]] = {}
    shapes = gds_pin_shapes(gds)
    for lab in info["labels"]:
        canonical = _canonical_label(lab["text"], aliases)
        if not canonical or not shapes:
            continue
        x, y = float(lab["x"]), float(lab["y"])
        def distance(shape):
            b = shape["rect"]
            dx = max(b[0] - x, 0.0, x - b[2])
            dy = max(b[1] - y, 0.0, y - b[3])
            return dx + dy
        candidates = shapes
        if canonical in ("VDD", "VSS"):
            # Power-pin layer comes from the manifest's "pdn_power_layer"
            # (default met4).  Macros whose GDS carries real met4 power rails
            # (tx_inv_chain_1_3_9, rx_parallel_inv_macro) expose met4 so the
            # sky130 PDN macro grid's add_pdn_connect(met4, met5) reaches them
            # (fixes PDN-0233).  rx_res_fb_pmos carries power only on met1
            # stubs, so its manifest declares "pdn_power_layer": "met1" and the
            # PDN cfg adds a met1<->met4 via connect instead of fabricating a
            # met4 strap that does not exist in the layout.
            pref = power_layer or "met4"
            order = [pref] + [l for l in ("met4", "met5", "met1") if l != pref]
            power = next(
                ([s for s in shapes if s["layer"] == l] for l in order
                 if any(s["layer"] == l for s in shapes)),
                [])
            if power:
                candidates = power
        else:
            signal = [s for s in shapes if s["layer"] == "met1"]
            if signal:
                candidates = signal
        best = min(candidates, key=distance)
        if distance(best) <= 3.0:
            b = best["rect"]
            pin_boxes[canonical] = {
                "layer": best["layer"],
                "rect": [max(0.0, b[0] - x0), max(0.0, b[1] - y0),
                         min(width, b[2] - x0), min(height, b[3] - y0)],
            }
    missing = [p for p in ports if p not in pin_boxes]
    if missing:
        raise RuntimeError(f"{name}: missing pin shapes for pins {missing}")
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
        entry = pin_boxes[port]
        xa, ya, xb, yb = entry["rect"]
        use = ("POWER" if port == "VDD" else
               "GROUND" if port == "VSS" else "SIGNAL")
        direction = ("INOUT" if port in ("VDD", "VSS") else
                     "INPUT" if port == "IN" else "OUTPUT")
        lines += [f"  PIN {port}", f"    DIRECTION {direction} ;",
                  f"    USE {use} ;", "    PORT", f"      LAYER {entry['layer']} ;",
                  f"        RECT {xa:.3f} {ya:.3f} {xb:.3f} {yb:.3f} ;",
                  "    END", f"  END {port}"]
    # Routing obstructions: the macros' internal metal crowds their cell
    # boundary, so top-level signal metal routed over the interior lands
    # within min-spacing and fails DRC.  Block every routing layer the macro
    # does NOT use for a pin across the whole cell, so the detailed router
    # keeps its geometry clear while the pins stay fully reachable.
    pin_layers = {pb["layer"] for pb in pin_boxes.values()}
    obs_layers = [l for l in ("met1", "met2", "met3", "met4", "met5")
                  if l not in pin_layers]
    obs_rects: list[tuple[str, list[float]]] = [
        (l, [0.0, 0.0, width, height]) for l in obs_layers]
    # Partial met1 blockage.  The macros carry met1 pins, so met1 cannot be
    # fully obstructed -- but the macro's internal met1 is a fragmented mesh
    # (power rail + interleaved signal stubs) and the detail router drops
    # signal met1/M1M2 via pads inside the footprint that land 28-40 nm off
    # those shapes -> the met1.2 / via2.1a cluster.  Block met1 over the
    # whole cell EXCEPT a small access window around each met1 pin, so the
    # router can reach the pin but cannot drop via pads into the mesh.  We
    # decompose (cell_rect - union(pin_windows)) into disjoint OBS rects via
    # a guillotine cut along each pin window's x-span.
    if "met1" in pin_layers:
        win_margin = 0.85  # um; must clear the access via's full met1 pad
        wins = sorted(
            (max(0.0, r[0] - win_margin), max(0.0, r[1] - win_margin),
             min(width, r[2] + win_margin), min(height, r[3] + win_margin))
            for pb in pin_boxes.values()
            for r in [pb["rect"]] if pb["layer"] == "met1")
        # merge overlapping windows
        merged: list[list[float]] = []
        for xa, ya, xb, yb in wins:
            if merged and xa <= merged[-1][2] and ya <= merged[-1][3] and \
                    xb >= merged[-1][0] and yb >= merged[-1][1]:
                m = merged[-1]
                m[0], m[1] = min(m[0], xa), min(m[1], ya)
                m[2], m[3] = max(m[2], xb), max(m[3], yb)
            else:
                merged.append([xa, ya, xb, yb])
        # vertical strips between consecutive pin windows span full height
        cur = 0.0
        for xa, ya, xb, yb in merged:
            if xa - cur > 0.02:
                obs_rects.append(("met1", [cur, 0.0, xa, height]))
            # within the window's x-span, block below & above the window
            if ya > 0.02:
                obs_rects.append(("met1", [xa, 0.0, xb, ya]))
            if height - yb > 0.02:
                obs_rects.append(("met1", [xa, yb, xb, height]))
            cur = max(cur, xb)
        if width - cur > 0.02:
            obs_rects.append(("met1", [cur, 0.0, width, height]))
    if obs_rects:
        lines.append("  OBS")
        for lay, r in obs_rects:
            lines.append(f"    LAYER {lay} ;")
            lines.append(
                f"      RECT {r[0]:.3f} {r[1]:.3f} {r[2]:.3f} {r[3]:.3f} ;")
        lines.append("  END")
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
        "pins_um": {k: v["rect"] for k, v in pin_boxes.items()},
        "pin_layers": {k: v["layer"] for k, v in pin_boxes.items()},
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
        str(ROOT / "analog/macros/rx_parallel_inv_macro/manifest.json"),
        str(ROOT / "analog/macros/rx_res_fb_pmos/manifest.json"),
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

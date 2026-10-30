#!/usr/bin/env python3
"""Build a PCell-device unit inverter with pure-pya geometry.

Run inside KLayout:
  OUT=/tmp/inv.gds klayout -b -r verification/analog/pya_inverter_probe.py
This is a DRC/LVS-oriented unit cell, not yet a complete TX/RX macro.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pya

from verification.analog.pya_draw import shim  # noqa: F401
from verification.analog.pya_draw.draw_fet import draw_nfet, draw_pfet


def _labels(cell) -> None:
    layout = cell.layout()
    m1 = layout.layer(68, 20)
    lab = layout.layer(68, 5)
    boxes = [s.box for s in cell.shapes(m1).each() if s.is_box()]
    if len(boxes) < 3:
        raise RuntimeError("device has fewer than three met1 terminals")
    center = lambda b: ((b.left + b.right) // 2, (b.bottom + b.top) // 2)
    gate = max(boxes, key=lambda b: center(b)[1])
    lower = [b for b in boxes if b is not gate]
    src = min(lower, key=lambda b: center(b)[0])
    drain = max(lower, key=lambda b: center(b)[0])
    for text, b in (("S", src), ("D", drain), ("G", gate)):
        x, y = center(b)
        cell.shapes(lab).insert(pya.Text(text, pya.Trans(pya.Vector(x, y))))


def box(top, layer, x1, y1, x2, y2):
    top.shapes(layer).insert(pya.Box(*(int(v * 1000) for v in (x1, y1, x2, y2))))


def build_inverter(ly, name: str = "inv", x: int = 0, y: int = 0,
                   label_ports: bool = True, bulk: str = "None"):
    """Create one DRC/LVS-oriented inverter cell at (x, y) in database units."""
    n = ly.create_cell(f"{name}_nfet")
    draw_nfet(n, l=0.15, w=5.0, nf=1, type="sky130_fd_pr__nfet_01v8",
              bulk=bulk, gate_con_pos="top")
    if label_ports and bulk != "bulk tie":
        _labels(n)
    p = ly.create_cell(f"{name}_pfet")
    draw_pfet(p, l=0.15, w=5.0, nf=1, type="sky130_fd_pr__pfet_01v8",
              bulk=bulk, gate_con_pos="top")
    if label_ports and bulk != "bulk tie":
        _labels(p)
    top = ly.create_cell(name)
    top.insert(pya.CellInstArray(n, pya.Trans(pya.Vector(x, y))))
    top.insert(pya.CellInstArray(p, pya.Trans(pya.Vector(x + 2500, y))))
    m1, v1 = ly.layer(68, 20), ly.layer(68, 44)
    m2, v2 = ly.layer(69, 20), ly.layer(69, 44)
    m3 = ly.layer(70, 20)
    # gates via met2 bridge (avoids the source rails)
    box(top, v1, 0.30, 5.30, 0.45, 5.45); box(top, v1, 2.80, 5.30, 2.95, 5.45)
    box(top, m2, 0.30, 5.30, 0.45, 7.10); box(top, m2, 2.80, 5.30, 2.95, 7.10)
    box(top, m2, 0.30, 6.90, 2.95, 7.10)
    box(top, m1, 0.0, 5.35, 0.30, 5.45)
    if label_ports:
        top.shapes(ly.layer(68, 5)).insert(pya.Text("IN", pya.Trans(pya.Vector(x + 120, y + 5400))))
    # drains to met3 output bridge
    box(top, v1, 0.55, 2.45, 0.65, 2.55); box(top, v1, 3.05, 2.45, 3.15, 2.55)
    box(top, m2, 0.56, 2.45, 0.64, 6.60); box(top, m2, 3.06, 2.45, 3.14, 6.60)
    box(top, v2, 0.56, 6.40, 0.64, 6.60); box(top, v2, 3.06, 6.40, 3.14, 6.60)
    box(top, m3, 0.55, 6.45, 3.15, 6.65)
    if label_ports:
        top.shapes(ly.layer(70, 5)).insert(pya.Text("OUT", pya.Trans(pya.Vector(x + 1800, y + 6550))))
    # sources / rails
    box(top, m1, 0.035, 0.0, 0.265, 0.195); box(top, m1, 2.535, 4.805, 2.650, 6.0)
    box(top, m1, 0.0, 0.0, 3.0, 0.12); box(top, m1, 0.0, 6.0, 3.0, 6.12)
    if bulk == "bulk tie":
        # Extend the local rails over the body taps and tie them explicitly.
        box(top, m1, 3.0, 0.0, 3.95, 0.12)
        box(top, m1, 3.0, 6.0, 3.95, 6.12)
        top.shapes(m1).insert(pya.Box(1180, 0, 1410, 195))
        top.shapes(m1).insert(pya.Box(3650, 4805, 3880, 6120))
    if label_ports:
        top.shapes(ly.layer(68, 5)).insert(pya.Text("VSS", pya.Trans(pya.Vector(x + 100, y + 60))))
        top.shapes(ly.layer(68, 5)).insert(pya.Text("VDD", pya.Trans(pya.Vector(x + 100, y + 6060))))
    return top


def main() -> int:
    out = Path(os.environ.get("OUT", "/tmp/openserdes_pcell_inv.gds"))
    ly = pya.Layout()
    top = build_inverter(ly)
    out.parent.mkdir(parents=True, exist_ok=True)
    ly.write(str(out))
    print(f"wrote {out} bbox={top.bbox().to_s()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

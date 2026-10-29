#!/usr/bin/env python3
"""Build a PCell-device TX inverter array (1 -> 3 -> 9) in pure pya.

Run inside KLayout:
  OUT=/tmp/tx_pcell.gds klayout -b -r verification/analog/pya_tx_array.py

The unit inverter is the DRC/LVS-verified cell from pya_inverter_probe.py.
This script only adds hierarchical placement and upper-metal interconnect; it
does not claim LVS/PEX signoff until the extracted top-level netlist is checked.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pya  # noqa: E402

from verification.analog.pya_inverter_probe import build_inverter  # noqa: E402


def box(cell, layer, x1, y1, x2, y2):
    """Insert a rectangle in database units."""
    cell.shapes(layer).insert(pya.Box(int(x1), int(y1), int(x2), int(y2)))


def via(cell, layer, x, y, size):
    half = size // 2
    box(cell, layer, x - half, y - half, x + half, y + half)


def build_tx(ly, name: str = "tx_pcell"):
    inv = build_inverter(ly, "tx_inv", label_ports=False)
    top = ly.create_cell(name)

    # 1 -> 3 -> 9 fanout; 4.5 um pitch leaves room for upper-metal routes.
    stage1 = [0]
    stage2 = [4500, 8500, 12500]
    stage3 = [17000 + i * 4500 for i in range(9)]
    for x in stage1 + stage2 + stage3:
        top.insert(pya.CellInstArray(inv, pya.Trans(pya.Vector(x, 0))))

    m1 = ly.layer(68, 20)
    m2 = ly.layer(69, 20)
    m3 = ly.layer(70, 20)
    v1 = ly.layer(68, 44)
    v2 = ly.layer(69, 44)
    v3 = ly.layer(70, 44)
    m4 = ly.layer(71, 20)
    lab1 = ly.layer(68, 5)
    lab3 = ly.layer(70, 5)

    last_x = stage3[-1]
    rail_end = last_x + 3600
    # Continuous source rails through all hierarchical units.
    box(top, m1, 0, 0, rail_end, 120)
    box(top, m1, 0, 6000, rail_end, 6120)
    top.shapes(lab1).insert(pya.Text("VSS", pya.Trans(pya.Vector(100, 60))))
    top.shapes(lab1).insert(pya.Text("VDD", pya.Trans(pya.Vector(100, 6060))))

    # Stage 1 output is met3; distribute it on met3 at y=8.0 um.
    box(top, m3, 1000, 6450, 1100, 8000)
    box(top, m3, 1000, 8000, stage2[-1] + 150, 8100)
    for x in stage2:
        target = x + 150
        box(top, m3, target - 50, 5600, target + 50, 8000)
        via(top, v2, target, 5600, 100)
        box(top, m2, target - 100, 5300, target + 100, 5800)
        via(top, v1, target, 5400, 100)

    # Stage 2 output is met3; move to met4 and distribute at y=7.2 um.
    for x in stage2:
        source = x + 1000
        via(top, v3, source, 6550, 200)
        box(top, m4, source - 100, 6550, source + 100, 7200)
    box(top, m4, stage2[0] + 900, 7200, stage3[-1] + 150, 7300)
    for x in stage3:
        target = x + 150
        box(top, m4, target - 100, 5600, target + 100, 7200)
        via(top, v3, target, 5600, 200)
        box(top, m3, target - 100, 5450, target + 100, 5750)
        via(top, v2, target, 5500, 100)
        box(top, m2, target - 100, 5300, target + 100, 5700)
        via(top, v1, target, 5400, 100)

    # Stage 3 drains: tie on met3 below the cells, then expose the TX output.
    for x in stage3:
        source = x + 1000
        box(top, m3, source - 50, 4900, source + 50, 6550)
    box(top, m3, stage3[0] + 950, 4850, stage3[-1] + 1050, 4950)
    top.shapes(lab3).insert(pya.Text("OUT", pya.Trans(pya.Vector(stage3[-1] + 1000, 4900))))

    top.shapes(lab1).insert(pya.Text("IN", pya.Trans(pya.Vector(150, 5400))))
    return top


def main() -> int:
    out = Path(os.environ.get("OUT", "/tmp/openserdes_pcell_tx.gds"))
    ly = pya.Layout()
    top = build_tx(ly)
    out.parent.mkdir(parents=True, exist_ok=True)
    ly.write(str(out))
    print(f"wrote {out} bbox={top.bbox().to_s()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

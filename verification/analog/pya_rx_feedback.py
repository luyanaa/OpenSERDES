#!/usr/bin/env python3
"""Build the pure-pya PCell RX nonlinear-feedback inverter.

Run inside KLayout:
  OUT=/tmp/rx_fb_pcell.gds klayout -b -r verification/analog/pya_rx_feedback.py

Topology: a 3N/6P CMOS inverter (three verified unit inverters plus three
parallel PMOS devices) with two series diode-connected W=0.55/L=8 PMOS
feedback devices from OUT to IN.  This mirrors the electrical topology of
``rx_res_fb_pmos``.

The pya device cells use bulk tie and the top-level rails are strapped to the
NMOS/PMOS body taps.  The feedback pair is separated and its net06 bridge is
routed on met2 so it cannot short to the first feedback device body tap.
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
from verification.analog.pya_draw.draw_fet import draw_pfet  # noqa: E402


def box(cell, layer, x1, y1, x2, y2):
    cell.shapes(layer).insert(pya.Box(int(x1), int(y1), int(x2), int(y2)))


def via(cell, layer, x, y, size):
    half = size // 2
    box(cell, layer, x - half, y - half, x + half, y + half)


def label_pfet_terminals(cell, lab):
    m1 = cell.layout().layer(68, 20)
    boxes = [s.box for s in cell.shapes(m1).each() if s.is_box()]
    if len(boxes) < 4:
        raise RuntimeError("bulk-tied PMOS needs four met1 terminals")
    center = lambda b: ((b.left + b.right) // 2, (b.bottom + b.top) // 2)
    gate = max(boxes, key=lambda b: center(b)[1])
    body = max(boxes, key=lambda b: center(b)[0])
    lower = [b for b in boxes if b is not gate and b is not body]
    src = min(lower, key=lambda b: center(b)[0])
    drain = max(lower, key=lambda b: center(b)[0])
    for text, b in (("S", src), ("D", drain), ("G", gate), ("B", body)):
        x, y = center(b)
        cell.shapes(lab).insert(pya.Text(text, pya.Trans(pya.Vector(x, y))))


def build_rx(ly, name: str = "rx_fb_pcell", drop_body_tie: bool = False):
    inv = build_inverter(ly, "rx_inv", label_ports=True, bulk="bulk tie")
    top = ly.create_cell(name)

    core_x = [0, 4500, 9000]
    extra_p_x = [13500, 18000, 22500]
    for x in core_x:
        top.insert(pya.CellInstArray(inv, pya.Trans(pya.Vector(x, 0))))

    pfet5 = ly.create_cell("rx_pfet_w5")
    draw_pfet(pfet5, l=0.15, w=5.0, nf=1,
              type="sky130_fd_pr__pfet_01v8", bulk="bulk tie", gate_con_pos="top")
    label_pfet_terminals(pfet5, ly.layer(68, 5))
    for x in extra_p_x:
        top.insert(pya.CellInstArray(pfet5, pya.Trans(pya.Vector(x, 0))))

    fb = ly.create_cell("rx_pfet_fb")
    draw_pfet(fb, l=8.0, w=0.55, nf=1,
              type="sky130_fd_pr__pfet_01v8", bulk="bulk tie", gate_con_pos="top")
    label_pfet_terminals(fb, ly.layer(68, 5))
    fb_x = (0, 9500)
    for x in fb_x:
        top.insert(pya.CellInstArray(fb, pya.Trans(pya.Vector(x, 9000))))

    m1 = ly.layer(68, 20)
    v1 = ly.layer(68, 44)
    m2 = ly.layer(69, 20)
    v2 = ly.layer(69, 44)
    m3 = ly.layer(70, 20)
    v3 = ly.layer(70, 44)
    m4 = ly.layer(71, 20)
    lab1 = ly.layer(68, 5)
    lab3 = ly.layer(70, 5)
    lab4 = ly.layer(71, 5)

    # The L=8 device body tap is the right-most met1 terminal; strap it to
    # the top VDD rail.  net06 is on met2, so this m1 strap cannot short it.
    if not drop_body_tie:
        for x in fb_x:
            box(top, m1, x + 8970, 6120, x + 9260, 9130)

    rail_end = extra_p_x[-1] + 1500
    box(top, m1, 0, 0, rail_end, 120)
    box(top, m1, 0, 6000, rail_end, 6120)
    top.shapes(lab1).insert(pya.Text("VSS", pya.Trans(pya.Vector(100, 60))))
    top.shapes(lab1).insert(pya.Text("VDD", pya.Trans(pya.Vector(100, 6060))))

    # Extra PMOS body taps to VDD.
    if not drop_body_tie:
        for x in extra_p_x:
            box(top, m1, x + 1150, 4805, x + 1380, 6120)

    # Common drain/output bus.  It deliberately overlaps the first core
    # inverter's met3 output and every extra PMOS drain.
    box(top, m3, 100, 6450, extra_p_x[-1] + 750, 6650)

    # Core inverter gates: m4 bus -> via3 -> m3 patch -> via2 -> m2 -> via1.
    for x in core_x:
        box(top, m4, x + 100, 5500, x + 200, 8000)
        via(top, v3, x + 150, 5600, 150)
        box(top, m3, x + 50, 5350, x + 250, 5750)
        via(top, v2, x + 150, 5500, 100)
        box(top, m2, x + 50, 5300, x + 250, 5700)
        via(top, v1, x + 150, 5400, 100)

    # Extra parallel PMOS: source to VDD, drain to OUT, gate to IN.
    for x in extra_p_x:
        box(top, m1, x + 35, 4805, x + 175, 6000)
        via(top, v1, x + 600, 2500, 100)
        box(top, m2, x + 550, 2450, x + 650, 6600)
        via(top, v2, x + 600, 6550, 100)
        via(top, v1, x + 375, 5365, 100)
        box(top, m2, x + 325, 5300, x + 425, 7900)
        via(top, v2, x + 375, 7900, 100)
        box(top, m3, x + 275, 7800, x + 475, 8000)
        via(top, v3, x + 375, 7950, 150)
        box(top, m4, x + 275, 7950, x + 475, 8050)

    # IN is a met4 bus; keep it above the met3 output bus.
    box(top, m4, 100, 7950, extra_p_x[-1] + 475, 8050)

    # Feedback stage 1 at x=0,y=9000: gate/source on OUT, drain on net06.
    box(top, m3, 100, 6450, 200, 9300)
    via(top, v2, 150, 9300, 200)
    box(top, m2, 50, 9200, 250, 9400)
    via(top, v1, 150, 9300, 100)
    box(top, m1, 250, 9420, 495, 9900)

    # net06 bridge on met2 between the two feedback devices.  An m1 bridge
    # would cross the first L=8 device body tap at x=8970..9260.
    via(top, v1, 8460, 9300, 150)
    via(top, v1, 9600, 9300, 150)
    box(top, m2, 8400, 9250, 9650, 9350)
    # Feedback stage 2: gate/source on net06; right terminal goes to IN.
    box(top, m1, 9500, 9420, 9995, 9900)
    box(top, m3, 17900, 8000, 18000, 9300)
    via(top, v2, 17950, 9300, 200)
    box(top, m2, 17850, 9200, 18050, 9400)
    via(top, v1, 17950, 9300, 100)
    via(top, v3, 17950, 8000, 200)
    box(top, m4, 17850, 7950, 18050, 8050)

    top.shapes(lab4).insert(pya.Text("IN", pya.Trans(pya.Vector(150, 8000))))
    top.shapes(lab3).insert(pya.Text("OUT", pya.Trans(pya.Vector(150, 6550))))
    return top


def main() -> int:
    out = Path(os.environ.get("OUT", "/tmp/openserdes_rx_fb_pcell.gds"))
    ly = pya.Layout()
    top = build_rx(ly, drop_body_tie=os.environ.get("DROP_BODY_TIE") == "1")
    out.parent.mkdir(parents=True, exist_ok=True)
    ly.write(str(out))
    print(f"wrote {out} bbox={top.bbox().to_s()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

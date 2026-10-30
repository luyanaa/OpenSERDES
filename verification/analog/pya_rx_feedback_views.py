#!/usr/bin/env python3
"""Export LEF and CDL views for the pure-pya PCell RX feedback inverter.

Run inside KLayout:
  IN=build/verify/pcell_rx_fb/rx_fb_pcell.gds \\
    LEF_OUT=.../rx_fb_pcell.lef CDL_OUT=.../rx_fb_pcell.cdl \\
    klayout -b -r verification/analog/pya_rx_feedback_views.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pya

src = Path(os.environ.get("IN", "build/verify/pcell_rx_fb/rx_fb_pcell.gds"))
lef_out = Path(os.environ.get("LEF_OUT", "build/verify/pcell_rx_fb/rx_fb_pcell.lef"))
cdl_out = Path(os.environ.get("CDL_OUT", "build/verify/pcell_rx_fb/rx_fb_pcell.cdl"))

ly = pya.Layout()
ly.read(str(src))
top = ly.cell("rx_fb_pcell")
if top is None:
    raise SystemExit(f"rx_fb_pcell not found in {src}")
bb = top.bbox()
x0, y0 = bb.left, bb.bottom
w, h = (bb.right - bb.left) / 1000.0, (bb.top - bb.bottom) / 1000.0


def um(v: int) -> float:
    return round(v / 1000.0, 3)


def rect(x1: int, y1: int, x2: int, y2: int) -> str:
    return f"{um(x1 - x0)} {um(y1 - y0)} {um(x2 - x0)} {um(y2 - y0)}"


lef = f"""VERSION 5.8 ;
BUSBITCHARS "[]" ;
DIVIDERCHAR "/" ;
MACRO rx_fb_pcell
  CLASS BLOCK ;
  ORIGIN 0 0 ;
  FOREIGN rx_fb_pcell 0 0 ;
  SIZE {w:.3f} BY {h:.3f} ;
  SYMMETRY X Y ;
  PIN VDD
    DIRECTION INOUT ;
    USE POWER ;
    PORT
      LAYER met1 ;
        RECT {rect(0, 6000, bb.right - x0, 6120)} ;
    END
  END VDD
  PIN VSS
    DIRECTION INOUT ;
    USE GROUND ;
    PORT
      LAYER met1 ;
        RECT {rect(0, 0, bb.right - x0, 120)} ;
    END
  END VSS
  PIN IN
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER met4 ;
        RECT {rect(100, 7950, 475, 8050)} ;
    END
  END IN
  PIN OUT
    DIRECTION OUTPUT ;
    USE SIGNAL ;
    PORT
      LAYER met3 ;
        RECT {rect(100, 6450, 475, 6650)} ;
    END
  END OUT
END rx_fb_pcell
END LIBRARY
"""

cdl = """* Pure-pya PCell RX: 3N/6P inverter + two series PMOS feedback devices
.SUBCKT rx_fb_pcell VSS VDD OUT IN
Xinv0 OUT IN VDD VSS rx_inv
Xinv1 OUT IN VDD VSS rx_inv
Xinv2 OUT IN VDD VSS rx_inv
Xp0 IN OUT VDD VDD rx_pfet_w5
Xp1 IN OUT VDD VDD rx_pfet_w5
Xp2 IN OUT VDD VDD rx_pfet_w5
Xfb0 net06 IN VDD net06 rx_pfet_fb
Xfb1 OUT net06 VDD OUT rx_pfet_fb
.ENDS

.SUBCKT rx_inv OUT IN VDD VSS
Xn OUT IN VSS VSS sky130_fd_pr__nfet_01v8 w=5 l=0.15
Xp OUT IN VDD VDD sky130_fd_pr__pfet_01v8 w=5 l=0.15
.ENDS

.SUBCKT rx_pfet_w5 G D B S
Xp D G S B sky130_fd_pr__pfet_01v8 w=5 l=0.15
.ENDS

.SUBCKT rx_pfet_fb G D B S
Xp D G S B sky130_fd_pr__pfet_01v8 w=0.55 l=8
.ENDS
"""

lef_out.parent.mkdir(parents=True, exist_ok=True)
cdl_out.parent.mkdir(parents=True, exist_ok=True)
lef_out.write_text(lef)
cdl_out.write_text(cdl)
print(f"wrote {lef_out} {w:.3f}x{h:.3f} um")
print(f"wrote {cdl_out}")

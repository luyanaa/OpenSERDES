#!/usr/bin/env python3
"""Export LEF and CDL views for the pure-pya PCell TX array.

Run inside KLayout:
  IN=build/verify/pcell_tx/tx_pcell.gds \\
    LEF_OUT=.../tx_pcell.lef CDL_OUT=.../tx_pcell.cdl \\
    klayout -b -r verification/analog/pya_tx_array_views.py

The LEF pin rectangles are derived from the actual GDS bounding box.  The CDL
uses the same 1 -> 3 -> 9 inverter hierarchy as pya_tx_array_verify.py.
"""
from __future__ import annotations

import os
from pathlib import Path

import pya

src = Path(os.environ.get("IN", "build/verify/pcell_tx/tx_pcell.gds"))
lef_out = Path(os.environ.get("LEF_OUT", "build/verify/pcell_tx/tx_pcell.lef"))
cdl_out = Path(os.environ.get("CDL_OUT", "build/verify/pcell_tx/tx_pcell.cdl"))

ly = pya.Layout()
ly.read(str(src))
top = ly.cell("tx_pcell")
if top is None:
    raise SystemExit(f"tx_pcell not found in {src}")
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
MACRO tx_pcell
  CLASS BLOCK ;
  ORIGIN 0 0 ;
  FOREIGN tx_pcell 0 0 ;
  SIZE {w:.3f} BY {h:.3f} ;
  SYMMETRY X Y ;
  PIN VDD
    DIRECTION INOUT ;
    USE POWER ;
    PORT
      LAYER met1 ;
        RECT {rect(0, 6000, 56600, 6120)} ;
    END
  END VDD
  PIN VSS
    DIRECTION INOUT ;
    USE GROUND ;
    PORT
      LAYER met1 ;
        RECT {rect(0, 0, 56600, 120)} ;
    END
  END VSS
  PIN IN
    DIRECTION INPUT ;
    USE SIGNAL ;
    PORT
      LAYER met1 ;
        RECT {rect(0, 5350, 300, 5450)} ;
    END
  END IN
  PIN OUT
    DIRECTION OUTPUT ;
    USE SIGNAL ;
    PORT
      LAYER met3 ;
        RECT {rect(17950, 4850, 54050, 4950)} ;
    END
  END OUT
END tx_pcell
END LIBRARY
"""

cdl = """* Pure-pya PCell TX array: 1 -> 3 -> 9 unit inverters
.SUBCKT tx_pcell VDD VSS IN OUT
Xinv0 IN VDD n1 VSS VSUBS tx_inv
Xinv1 n1 VDD n2 VSS VSUBS tx_inv
Xinv2 n1 VDD n2 VSS VSUBS tx_inv
Xinv3 n1 VDD n2 VSS VSUBS tx_inv
Xinv4 n2 VDD OUT VSS VSUBS tx_inv
Xinv5 n2 VDD OUT VSS VSUBS tx_inv
Xinv6 n2 VDD OUT VSS VSUBS tx_inv
Xinv7 n2 VDD OUT VSS VSUBS tx_inv
Xinv8 n2 VDD OUT VSS VSUBS tx_inv
Xinv9 n2 VDD OUT VSS VSUBS tx_inv
Xinv10 n2 VDD OUT VSS VSUBS tx_inv
Xinv11 n2 VDD OUT VSS VSUBS tx_inv
Xinv12 n2 VDD OUT VSS VSUBS tx_inv
.ENDS

.SUBCKT tx_inv G VDD D VSS VSUBS
Xn D G VSS VSUBS sky130_fd_pr__nfet_01v8 w=5 l=0.15
Xp D G VDD body sky130_fd_pr__pfet_01v8 w=5 l=0.15
.ENDS
"""

lef_out.parent.mkdir(parents=True, exist_ok=True)
cdl_out.parent.mkdir(parents=True, exist_ok=True)
lef_out.write_text(lef)
cdl_out.write_text(cdl)
print(f"wrote {lef_out} {w:.3f}x{h:.3f} um")
print(f"wrote {cdl_out}")

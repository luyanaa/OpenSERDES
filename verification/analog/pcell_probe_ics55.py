#!/usr/bin/env python3
"""Generate ICsprout55 analog/RF MOS PCell probes.

Run inside KLayout:
  ICS55_PDK=/grid/icsprout55-pdk OUT=/tmp/ics55_pcells.gds \\
    klayout -b -r verification/analog/pcell_probe_ics55.py

The PDK's KLayout PCells are provisional geometry generators, not signoff
devices.  This probe only demonstrates that both the analog MOS and RF MOS
PCells instantiate with arbitrary W/L/finger parameters.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pya

pdk = Path(os.environ.get("ICS55_PDK", "/grid/icsprout55-pdk"))
pcells = pdk / "libs.tech/klayout/pcells"
sys.path.insert(0, str(pcells))
import ics55_mos_analog  # noqa: E402,F401  (registers library)
import ics55_rf_mos  # noqa: E402,F401

out = Path(os.environ.get("OUT", "/tmp/openserdes_ics55_pcells.gds"))
ly = pya.Layout()
ly.dbu = 0.001
top = ly.create_cell("TOP")
cases = [
    ("mos_n", "ics55_mos_analog", {
        "dev_type": "n", "w": 1.0, "l": 0.06, "fingers": 2,
        "guard_ring": 1, "ring_metal": 1,
    }),
    ("mos_p", "ics55_mos_analog", {
        "dev_type": "p", "w": 1.0, "l": 0.06, "fingers": 2,
        "guard_ring": 1, "ring_metal": 1,
    }),
    ("rf_n", "ics55_rf_mos", {
        "polarity": "n", "wf": 2.0, "l": 0.06, "nf": 4,
        "gate_contact": "single", "body_tie": "bar", "source_left": True,
    }),
    ("rf_p", "ics55_rf_mos", {
        "polarity": "p", "wf": 2.0, "l": 0.06, "nf": 4,
        "gate_contact": "single", "body_tie": "bar", "source_left": True,
    }),
]
for index, (name, pcell, params) in enumerate(cases):
    cell = ly.create_cell(pcell, "ICsprout55", params)
    if cell is None:
        raise RuntimeError(f"PCell {pcell} did not produce a cell")
    wrapper = ly.create_cell(name)
    wrapper.insert(pya.CellInstArray(cell, pya.Trans()))
    top.insert(pya.CellInstArray(wrapper, pya.Trans(pya.Vector(index * 10000, 0))))
    print(f"{name}: {pcell} bbox={wrapper.bbox().to_s()}")

out.parent.mkdir(parents=True, exist_ok=True)
ly.write(str(out))
print(f"wrote {out}")

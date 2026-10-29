#!/usr/bin/env python3
"""Pure-pya KLayout PCell probe for the Sky130 fixed device family.

Run with KLayout's embedded interpreter:
  GDS=... OUT=... klayout -b -r verification/analog/klayout_pcell_probe.py
The probe is intentionally fixed-cell based.  It proves PCell registration and
headless instantiation; arbitrary W/L requires a pure-pya geometry port and
independent DRC/LVS, which is not claimed here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def run(argv: list[str]) -> int:
    import pya
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--gds", default=os.environ.get("GDS"))
    ap.add_argument("--device", default=os.environ.get("DEVICE",
        "sky130_fd_pr__rf_nfet_01v8_aM04W5p00L0p15"))
    ap.add_argument("--out", default=os.environ.get("OUT"))
    args = ap.parse_args(argv)
    if not args.gds or not args.out:
        raise SystemExit("set GDS and OUT (or pass args from a wrapper)")

    source = pya.Layout()
    source.read(args.gds)

    class FixedDevice(pya.PCellDeclarationHelper):
        def __init__(self):
            super().__init__()
            self.param("device", self.TypeString, "Device", default=args.device)

        def produce_impl(self):
            src = source.cell(self.device)
            if src is None:
                raise RuntimeError(f"unknown fixed device {self.device}")
            cm = pya.CellMapping()
            cm.for_single_cell(self.cell, src)
            self.cell.copy_tree_shapes(src, cm)

    lib = pya.Library()
    lib.layout().register_pcell("fixed_device", FixedDevice())
    lib.register("openserdes_pcell_probe")
    top = pya.Layout()
    dev = top.create_cell("fixed_device", "openserdes_pcell_probe",
                          {"device": args.device})
    if dev is None or dev.bbox().empty():
        raise SystemExit("PCell produced an empty device")
    out = top.create_cell("TOP")
    out.insert(pya.CellInstArray(dev, pya.Trans()))
    top.write(args.out)
    print(f"{args.device}: {dev.bbox().to_s()} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))

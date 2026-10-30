#!/usr/bin/env python3
"""Pure-pya KLayout PCell port of the Sky130 `draw_fet` subset.

Supported:
  nfet/pfet 01v8, arbitrary W/L, nf=1..N, bulk=None/bulk tie,
  gate_con_pos=top/bottom
Unsupported:
  guard-ring boolean operations, interdigitated multi-gate patterns, RF devices
Unsupported combinations fail rather than silently approximating geometry.
"""
from __future__ import annotations

import pya

from .pya_draw import shim  # registers the minimal pya-backed gdsfactory shim
from .pya_draw.draw_fet import draw_nfet, draw_pfet


class MosPcell(pya.PCellDeclarationHelper):
    def __init__(self) -> None:
        super().__init__()
        self.param("kind", self.TypeString, "Device kind")
        self.kind_handle = self.param("kind", self.TypeString, "Device kind")
        self.kind_handle.add_choice("nfet", "nfet")
        self.kind_handle.add_choice("pfet", "pfet")
        self.kind_handle.default = "nfet"
        self.param("l", self.TypeDouble, "L (um)", default=0.15, unit="um")
        self.param("w", self.TypeDouble, "W (um)", default=5.0, unit="um")
        self.param("nf", self.TypeInt, "Fingers", default=1)
        self.param("bulk", self.TypeString, "Bulk mode")
        self.bulk_handle = self.param("bulk", self.TypeString, "Bulk")
        self.bulk_handle.add_choice("None", "None")
        self.bulk_handle.add_choice("bulk tie", "bulk tie")
        self.bulk_handle.add_choice("guard ring", "guard ring")
        self.bulk_handle.default = "None"
        self.param("interdig", self.TypeBoolean, "Interdigitated gates", default=False)
        self.param("patt", self.TypeString, "Interdigitated pattern", default="")
        self.param("gate_con_pos", self.TypeString, "Gate contact position")
        self.gate_handle = self.param("gate_con_pos", self.TypeString, "Gate contact")
        self.gate_handle.add_choice("top", "top")
        self.gate_handle.add_choice("bottom", "bottom")
        self.gate_handle.default = "top"

    def coerce_parameters_impl(self) -> None:
        if self.l < 0.15:
            self.l = 0.15
        if self.w < 0.42:
            self.w = 0.42
        if self.nf < 1:
            self.nf = 1

    def produce_impl(self) -> None:
        model = ("sky130_fd_pr__nfet_01v8" if self.kind == "nfet"
                 else "sky130_fd_pr__pfet_01v8")
        if self.kind == "nfet":
            draw_nfet(self.cell, l=self.l, w=self.w, nf=self.nf,
                      type=model, bulk=self.bulk,
                      gate_con_pos=self.gate_con_pos,
                      interdig=1 if self.interdig else 0, patt=self.patt)
        else:
            draw_pfet(self.cell, l=self.l, w=self.w, nf=self.nf,
                      type=model, bulk=self.bulk,
                      gate_con_pos=self.gate_con_pos,
                      interdig=1 if self.interdig else 0, patt=self.patt)
        self._label_terminals()

    def _label_terminals(self) -> None:
        """Put D/G/S labels on the generated met1 terminals for extraction."""
        layout = self.cell.layout()
        layer = layout.layer(68, 20)
        lab = layout.layer(68, 5)
        boxes = []
        # Include the nested via-stack shapes; bulk-tie met1 is a child ref.
        it = self.cell.begin_shapes_rec(layer)
        while not it.at_end():
            shape = it.shape()
            if shape.is_box():
                b = shape.box
                boxes.append((b.left, b.bottom, b.right, b.top))
            it.next()
        if not boxes:
            raise RuntimeError("pure-pya MOS produced no met1 terminals")
        # The gate contact is the terminal with the largest y center.
        center = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        gate = max(boxes, key=lambda b: center(b)[1])
        body = None
        if self.bulk == "bulk tie":
            # The tap met1 strap is the right-most terminal.
            body = max(boxes, key=lambda b: center(b)[0])
        lower = [b for b in boxes if b is not gate and b is not body]
        if len(lower) < 2:
            raise RuntimeError("pure-pya MOS needs source and drain met1 terminals")
        src = min(lower, key=lambda b: center(b)[0])
        drain = max(lower, key=lambda b: center(b)[0])
        for text, b in (("S", src), ("D", drain), ("G", gate)):
            x, y = center(b)
            self.cell.shapes(lab).insert(
                pya.Text(text, pya.Trans(pya.Vector(int(round(x)), int(round(y))))))
        if body is not None:
            x, y = center(body)
            self.cell.shapes(lab).insert(
                pya.Text("B", pya.Trans(pya.Vector(int(round(x)), int(round(y))))))
        if self.bulk == "guard ring":
            # Guard ring is li1; label the body net for LVS/assembly.
            lli = layout.layer(67, 5)
            bb = self.cell.bbox()
            self.cell.shapes(lli).insert(pya.Text(
                "B", pya.Trans(pya.Vector(bb.left + 100, (bb.bottom + bb.top) // 2))))


def register() -> None:
    lib = pya.Library()
    lib.layout().register_pcell("mos", MosPcell())
    lib.register("openserdes_sky130")


if __name__ == "__main__":
    register()

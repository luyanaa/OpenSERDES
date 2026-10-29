#!/usr/bin/env python3
"""Pure-pya KLayout PCell port of the Sky130 `draw_fet` subset.

Supported:
  nfet/pfet 01v8, arbitrary W/L, nf=1..N, bulk=None, gate_con_pos=top/bottom
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
                      type=model, bulk="None", gate_con_pos=self.gate_con_pos)
        else:
            draw_pfet(self.cell, l=self.l, w=self.w, nf=self.nf,
                      type=model, bulk="None", gate_con_pos=self.gate_con_pos)


def register() -> None:
    lib = pya.Library()
    lib.layout().register_pcell("mos", MosPcell())
    lib.register("openserdes_sky130")


if __name__ == "__main__":
    register()

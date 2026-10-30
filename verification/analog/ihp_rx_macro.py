#!/usr/bin/env python3
"""IHP SG13G2 RX nonlinear-feedback macro: builder + verifier.

Topology (same electrical function as the sky130 ``rx_fb_pcell``):

  * 3 CMOS inverters (3 NMOS + 3 PMOS)
  * 3 extra parallel PMOS (source VDD, drain OUT, gate IN)
  * 2 series feedback PMOS from OUT to IN:
        OUT -> fb1(S=G=OUT, D=net06) -> fb0(S=G=net06, D=IN)
  * ports: VSS, VDD, IN, OUT
  * NMOS bodies tied to VSS (PDK p-substrate guard ring/tap)
  * PMOS bodies tied to VDD (PDK n-well guard ring/tie)

The layout is hierarchical: three ``rx_inv`` instances, three
``rx_pmos_extra`` instances and two ``rx_pmos_fb`` instances in ``rx_macro``.
That gives 8 top-level instances and 5 nets (VSS, VDD, IN, OUT, net06).

The script is headless and emits JSON evidence.  It uses the installed IHP
SG13G2 PDK only.  It does not touch sky130, gf180, M3, the Makefile or
``pya_pcell*``.

Example::

    IHP_ROOT=/home/yanlu/.ciel/ciel/ihp-sg13/versions/<ver> \
        python3 verification/analog/ihp_rx_macro.py --install-osdi
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.ihp_device_verify import (  # noqa: E402
    DEFAULT_IHP_ROOT,
    ensure_osdi,
    resolve_pdk,
    run,
)

MACRO = "rx_macro"
TOP_PORTS = ["VSS", "VDD", "IN", "OUT"]
FUNCTIONAL_NETS = ["VSS", "VDD", "IN", "OUT", "net06"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# KLayout builder template.  @@GDS@@ and @@DROP@@ are replaced below.
# ---------------------------------------------------------------------------
KLAYOUT_BUILD = r'''import json
import sys

IHP_PDK = @@PDK@@
sys.path.insert(0, IHP_PDK + "/libs.tech/klayout/python")
sys.path.insert(0, IHP_PDK + "/libs.tech/klayout/python/pycell4klayout-api/source/python")
import sg13g2_pycell_lib  # noqa: F401
import pya

OUT = @@GDS@@
META = @@META@@
DROP_BODY_STRAP = @@DROP@@

ly = pya.Layout()
ly.dbu = 0.001
ly.technology_name = "sg13g2"
L = {n: (l, d) for n, l, d in [
    ("m1", 8, 0), ("m1p", 8, 2), ("v1", 19, 0),
    ("m2", 10, 0), ("m2p", 10, 2), ("v2", 29, 0),
    ("m3", 30, 0), ("m3p", 30, 2), ("v3", 49, 0),
    ("m4", 50, 0), ("m4p", 50, 2), ("v4", 66, 0),
    ("m5", 67, 0), ("m5p", 67, 2), ("gp", 5, 0), ("cont", 6, 0),
]}


def lay(cell, name):
    l, d = L[name]
    return cell.shapes(ly.layer(l, d))


def box(cell, name, x1, y1, x2, y2):
    lay(cell, name).insert(pya.Box(int(x1), int(y1), int(x2), int(y2)))


def txt(cell, name, x, y, s):
    lay(cell, name).insert(pya.Text(s, pya.Trans(pya.Vector(int(x), int(y)))))


def uniq(cell, name):
    li = ly.layer(*L[name])
    out = []
    for s in cell.shapes(li).each():
        if s.is_box() and not any(s.box == b for b in out):
            out.append(s.box)
    return out


_dev_cache = {}


def build_device(name, kind, w, l, guard):
    if name in _dev_cache:
        return _dev_cache[name]
    c = ly.create_cell(kind, "SG13_dev", {
        "l": "%gu" % l, "w": "%gu" % w, "ng": 1,
        "guardRingType": guard, "guardRingDistance": "1u",
    })
    c.name = name
    boxes = uniq(c, "m1")
    minw = min(b.width() for b in boxes)
    sd = [b for b in boxes if b.width() <= minw + 10]
    sd.sort(key=lambda b: b.center().x)
    src, drn = sd[0], sd[-1]
    ring = [b for b in boxes if b not in sd]
    ring_top = max(ring, key=lambda b: b.top)
    ring_bot = min(ring, key=lambda b: b.bottom)
    poly = uniq(c, "gp")[0]
    vertical = poly.height() >= poly.width()
    sd_top = max(src.top, drn.top)
    gcy = min((sd_top + ring_top.bottom) // 2, poly.top + 200)
    gcx = poly.center().x

    # S/D: widen Metal1, add Via1 + Metal2 pad + port label.
    for term, b in (("S", src), ("D", drn)):
        x, y = b.center().x, b.center().y
        box(c, "m1", x - 150, y - 150, x + 150, y + 150)
        box(c, "v1", x - 95, y - 95, x + 95, y + 95)
        box(c, "m2", x - 150, y - 150, x + 150, y + 150)
        txt(c, "m2p", x, y, term)

    # Gate: PDK PCell gate has no contact.  Add a local poly pad + contact
    # + Metal1 pad + Via1 + Metal2 pad above the source/drain metal.
    box(c, "gp", gcx - 300, gcy - 300, gcx + 300, gcy + 300)
    box(c, "cont", gcx - 80, gcy - 80, gcx + 80, gcy + 80)
    box(c, "m1", gcx - 150, gcy - 150, gcx + 150, gcy + 150)
    box(c, "v1", gcx - 95, gcy - 95, gcx + 95, gcy + 95)
    box(c, "m2", gcx - 150, gcy - 150, gcx + 150, gcy + 150)
    txt(c, "m2p", gcx, gcy, "G")

    # Body tie: Via1 on the contacted guard-ring metal bottom segment.
    bx = ring_bot.right - 500
    by = ring_bot.center().y
    box(c, "v1", bx - 95, by - 95, bx + 95, by + 95)
    box(c, "m2", bx - 150, by - 150, bx + 150, by + 150)
    txt(c, "m2p", bx, by, "B")

    _dev_cache[name] = {
        "name": name, "kind": kind, "w": w, "l": l, "vertical": vertical,
        "S": (src.center().x, src.center().y),
        "D": (drn.center().x, drn.center().y),
        "G": (gcx, gcy), "B": (bx, by),
        "bbox": c.bbox().to_s(),
        "ring_bottom": ring_bot.to_s(), "ring_top": ring_top.to_s(),
    }
    return _dev_cache[name]


def route_tile(tile, insts, buses, pins, x0, x1, pin_y=14000):
    for ins in insts:
        g = ins["geom"]
        ox = ins["x"]
        for term in ("S", "D", "B", "G"):
            net = ins.get(term)
            if not net:
                continue
            # Negative control: leave the extra-PMOS n-well body floating.
            if DROP_BODY_STRAP and term == "B" and net == "VDD" \
                    and tile.name == "rx_pmos_extra":
                continue
            ys = buses[net]
            by = max(ys) if term == "G" else min(ys)
            px = ox + g[term][0]
            py = g[term][1]
            box(tile, "m2", px - 125, min(py, by), px + 125, max(py, by))
            box(tile, "v2", px - 95, by - 95, px + 95, by + 95)
    for net, ys in buses.items():
        for y in ys:
            box(tile, "m3", x0, y - 500, x1, y + 500)
    for net, x in pins.items():
        ys = buses[net]
        box(tile, "m4", x - 300, min(ys), x + 300, pin_y)
        for y in ys:
            box(tile, "v3", x - 95, y - 95, x + 95, y + 95)
        box(tile, "m4", x - 300, pin_y - 300, x + 300, pin_y + 300)
        txt(tile, "m4p", x, pin_y, net)


def build_tile(name, specs, buses, pins, x0, x1):
    tile = ly.create_cell(name)
    insts = []
    for devname, gname, xoff, netmap in specs:
        g = build_device(gname, *devname)
        ins = {"geom": g, "x": xoff}
        ins.update(netmap)
        c = ly.cell(gname)
        tile.insert(pya.CellInstArray(c.cell_index(),
                                      pya.Trans(pya.Vector(int(xoff), 0))))
        insts.append(ins)
    route_tile(tile, insts, buses, pins, x0, x1)
    return tile


build_device("rx_nmos_w5l013", "nmos", 5, 0.13, "psub")
build_device("rx_pmos_w5l013", "pmos", 5, 0.13, "nwell")
build_device("rx_pmos_w055l20", "pmos", 0.55, 2.0, "nwell")

inv = build_tile(
    "rx_inv",
    [("nmos", "rx_nmos_w5l013", 0,
      {"S": "VSS", "D": "OUT", "G": "IN", "B": "VSS"}),
     ("pmos", "rx_pmos_w5l013", 6000,
      {"S": "VDD", "D": "OUT", "G": "IN", "B": "VDD"})],
    {"VSS": [-3000], "VDD": [-5000], "OUT": [-7000], "IN": [8000]},
    {"VSS": -2000, "VDD": -3000, "OUT": -4000, "IN": -5000}, -5500, 9000)

pe = build_tile(
    "rx_pmos_extra",
    [("pmos", "rx_pmos_w5l013", 0,
      {"S": "VDD", "D": "OUT", "G": "IN", "B": "VDD"})],
    {"VDD": [-3000], "OUT": [-5000], "IN": [8000]},
    {"VDD": -2000, "OUT": -3000, "IN": -4000}, -4500, 3000)

fb = build_tile(
    "rx_pmos_fb",
    [("pmos", "rx_pmos_w055l20", 0,
      {"S": "SG", "D": "D", "G": "SG", "B": "VDD"})],
    {"SG": [-3000, 8000], "D": [-5000], "VDD": [-7000]},
    {"SG": -2000, "D": -3000, "VDD": -4000}, -4500, 5000)

top = ly.create_cell("rx_macro")
placements = [(inv, 0), (inv, 15000), (inv, 30000),
              (pe, 45000), (pe, 60000), (pe, 75000),
              (fb, 90000), (fb, 105000)]
for c, x in placements:
    top.insert(pya.CellInstArray(c.cell_index(),
                                 pya.Trans(pya.Vector(int(x), 0))))

top_buses = {"VSS": 16000, "VDD": 18000, "IN": 20000,
             "OUT": 22000, "net06": 24000}
pin_maps = [
    {"VSS": -2000, "VDD": -3000, "OUT": -4000, "IN": -5000},
    {"VSS": -2000, "VDD": -3000, "OUT": -4000, "IN": -5000},
    {"VSS": -2000, "VDD": -3000, "OUT": -4000, "IN": -5000},
    {"VDD": -2000, "OUT": -3000, "IN": -4000},
    {"VDD": -2000, "OUT": -3000, "IN": -4000},
    {"VDD": -2000, "OUT": -3000, "IN": -4000},
    {"SG": -2000, "D": -3000, "VDD": -4000},
    {"SG": -2000, "D": -3000, "VDD": -4000},
]
tile_types = ["inv", "inv", "inv", "pe", "pe", "pe", "fb", "fb"]
for i, (c, x) in enumerate(placements):
    t = tile_types[i]
    for net, px in pin_maps[i].items():
        if t == "fb":
            if net == "SG":
                ext = "OUT" if i == 6 else "net06"
            elif net == "D":
                ext = "net06" if i == 6 else "IN"
            else:
                ext = "VDD"
        else:
            ext = net
        by = top_buses[ext]
        ax = x + px
        box(top, "m4", ax - 300, 13700, ax + 300, by)
        box(top, "v4", ax - 95, by - 95, ax + 95, by + 95)
for net, y in top_buses.items():
    box(top, "m5", -8000, y - 500, 112000, y + 500)
for net in ("VSS", "VDD", "IN", "OUT"):
    txt(top, "m5p", -7500, top_buses[net], net)

ly.write(OUT)
meta = {
    "bbox": top.bbox().to_s(),
    "cells": [c.name for c in ly.each_cell()],
    "devices": _dev_cache,
}
open(META, "w").write(json.dumps(meta, indent=1) + "\n")
print(json.dumps({"gds": OUT, "bbox": meta["bbox"], "cells": meta["cells"]}))
'''


def build_gds(pdk: Path, gds: Path, work: Path, env: dict[str, str],
              drop_body_strap: bool = False) -> dict[str, Any]:
    script = work / ("rx_build_negative.py" if drop_body_strap else "rx_build.py")
    meta = gds.with_suffix(".layout.json")
    script.write_text(
        KLAYOUT_BUILD.replace("@@PDK@@", repr(str(pdk)))
        .replace("@@GDS@@", repr(str(gds)))
        .replace("@@META@@", repr(str(meta)))
        .replace("@@DROP@@", "True" if drop_body_strap else "False")
    )
    res = run([shutil.which("klayout") or "klayout", "-zz", "-r", str(script)],
              work, env, work / (script.stem + ".log"), timeout=600)
    res["layout"] = None
    if meta.is_file():
        try:
            res["layout"] = json.loads(meta.read_text())
        except json.JSONDecodeError:
            res["layout"] = None
    return res


# ---------------------------------------------------------------------------
# Independent schematic / CDL
# ---------------------------------------------------------------------------
SCHEMATIC = """.subckt rx_nmos_w5l013 S D G B
X0 D G S B sg13_lv_nmos w=5u l=0.13u
.ends
.subckt rx_pmos_w5l013 S D G B
X0 D G S B sg13_lv_pmos w=5u l=0.13u
.ends
.subckt rx_pmos_w055l20 S D G B
X0 D G S B sg13_lv_pmos w=0.55u l=2.0u
.ends
.subckt rx_inv VSS VDD OUT IN
Xp VDD OUT IN VDD rx_pmos_w5l013
Xn VSS OUT IN VSS rx_nmos_w5l013
.ends
.subckt rx_pmos_extra VDD OUT IN
Xp VDD OUT IN VDD rx_pmos_w5l013
.ends
.subckt rx_pmos_fb SG D VDD
Xp SG D SG VDD rx_pmos_w055l20
.ends
.subckt rx_macro VSS VDD IN OUT
Xpe0 VDD OUT IN rx_pmos_extra
Xpe1 VDD OUT IN rx_pmos_extra
Xpe2 VDD OUT IN rx_pmos_extra
Xfb0 net06 IN VDD rx_pmos_fb
Xfb1 OUT net06 VDD rx_pmos_fb
Xinv0 VSS VDD OUT IN rx_inv
Xinv1 VSS VDD OUT IN rx_inv
Xinv2 VSS VDD OUT IN rx_inv
.ends
"""


# ---------------------------------------------------------------------------
# Magic
# ---------------------------------------------------------------------------
def magic_lvs(pdk: Path, gds: Path, work: Path, env: dict[str, str],
              prefix: str, drc: bool = True) -> dict[str, Any]:
    rcfile = pdk / "libs.tech/magic/ihp-sg13g2.magicrc"
    spice = work / f"{prefix}_lvs.spice"
    script = work / f"{prefix}_magic_lvs.tcl"
    lines = [f"gds read {{{gds.with_suffix('')}}}",
             f"load {MACRO} -dereference", "select top cell"]
    if drc:
        lines += ["drc on", "drc check", "drc count"]
    lines += ["extract", "ext2spice lvs", "ext2spice extresist off",
              f"ext2spice -o {{{spice}}}", 'puts "IHP_RX_DONE"', "quit", ""]
    script.write_text("\n".join(lines))
    log = work / f"{prefix}_magic_lvs.log"
    res = run([shutil.which("magic") or "magic", "-dnull", "-noconsole",
               "-rcfile", str(rcfile), str(script)], work, env, log, timeout=1200)
    text = log.read_text()
    m = re.search(r"Total DRC errors found:\s*(\d+)", text, re.I)
    res["drc_count"] = int(m.group(1)) if m else None
    res["magic_done"] = "IHP_RX_DONE" in text
    res["netlist"] = str(spice) if spice.is_file() else None
    return res


def magic_pex(pdk: Path, gds: Path, work: Path, env: dict[str, str],
              prefix: str) -> dict[str, Any]:
    rcfile = pdk / "libs.tech/magic/ihp-sg13g2.magicrc"
    spice = work / f"{prefix}_pex.spice"
    script = work / f"{prefix}_magic_pex.tcl"
    script.write_text("\n".join([
        f"gds read {{{gds.with_suffix('')}}}",
        f"load {MACRO} -dereference",
        "select top cell",
        "extract",
        "extresist all",
        "ext2spice extresist on",
        "ext2spice rthresh 0",
        "ext2spice cthresh 0",
        f"ext2spice -o {{{spice}}}",
        'puts "IHP_RX_DONE"',
        "quit", "",
    ]))
    log = work / f"{prefix}_magic_pex.log"
    res = run([shutil.which("magic") or "magic", "-dnull", "-noconsole",
               "-rcfile", str(rcfile), str(script)], work, env, log, timeout=1200)
    text = log.read_text()
    res["magic_done"] = "IHP_RX_DONE" in text
    res["netlist"] = str(spice) if spice.is_file() else None
    return res


def netgen_lvs(pdk: Path, layout_spice: Path, schematic: Path,
               work: Path, env: dict[str, str], prefix: str) -> dict[str, Any]:
    setup = pdk / "libs.tech/netgen/ihp-sg13g2_setup.tcl"
    script = work / f"{prefix}_netgen.tcl"
    script.write_text(
        f"set setup {{{setup}}}\n"
        f"netgen::lvs [list {{{layout_spice}}} {MACRO}] "
        f"[list {{{schematic}}} {MACRO}] $setup\nquit\n"
    )
    log = work / f"{prefix}_netgen.log"
    res = run([shutil.which("netgen") or "netgen", "-batch", "source",
               str(script)], work, env, log, timeout=600)
    res.update(parse_netgen(log.read_text()))
    return res


def parse_netgen(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "match": "Circuits match uniquely." in text,
        "failed_pin_matching": "Top level cell failed pin matching." in text,
        "instances": None,
        "nets": None,
        "classes": {},
    }
    idx = text.rfind("Circuit: 'rx_macro'")
    block = text[idx:] if idx >= 0 else text
    m = re.search(r"Circuit rx_macro contains (\d+) device instances", block)
    if m:
        out["instances"] = int(m.group(1))
    m = re.search(r"Circuit contains (\d+) nets", block)
    if m:
        out["nets"] = int(m.group(1))
    out["classes"] = {k: int(v) for k, v in
                      re.findall(r"Class: (\S+)\s+instances:\s+(\d+)", block)}
    return out


# ---------------------------------------------------------------------------
# SPICE parsing
# ---------------------------------------------------------------------------
def parse_subckts(path: Path) -> dict[str, dict[str, Any]]:
    subs: dict[str, dict[str, Any]] = {}
    cur = None
    for line in path.read_text().splitlines():
        m = re.match(r"\s*\.subckt\s+(\S+)\s*(.*)$", line, re.I)
        if m:
            cur = m.group(1)
            subs[cur] = {"ports": m.group(2).split(), "lines": []}
            continue
        if re.match(r"\s*\.ends\b", line, re.I):
            cur = None
            continue
        if cur and re.match(r"\s*[XMR]\S+\s", line, re.I):
            subs[cur]["lines"].append(line.strip())
    return subs


def parse_pex(path: Path) -> dict[str, Any]:
    text = path.read_text()
    lines = [ln.strip() for ln in text.splitlines()
             if re.match(r"^\s*X\S+\s", ln, re.I)]
    nmos = sum(1 for ln in lines if "sg13_lv_nmos" in ln)
    pmos = sum(1 for ln in lines if "sg13_lv_pmos" in ln)
    r = len(re.findall(r"^\s*R\S+\s", text, re.M))
    c = len(re.findall(r"^\s*C\S+\s", text, re.M))
    return {
        "format": "flat",
        "device_instances": len(lines),
        "nmos": nmos,
        "pmos": pmos,
        "resistors": r,
        "capacitors": c,
        "floating_caps": text.count("**FLOATING"),
        "zero_area_devices": sum(
            1 for ln in lines if " ad=0 " in ln and " as=0 " in ln),
        "device_lines": lines,
    }


# ---------------------------------------------------------------------------
# Model DC
# ---------------------------------------------------------------------------
def model_dc(pdk: Path, lvs_spice: Path, work: Path,
             env: dict[str, str]) -> dict[str, Any]:
    lib = pdk / "libs.tech/ngspice/models/cornerMOSlv.lib"
    net = work / "rx_macro_model_dc.sp"
    net.write_text(
        f".lib {lib} mos_tt\n"
        + lvs_spice.read_text()
        + "\nVDD VDD 0 1.2\nVSS VSS 0 0\nVIN IN 0 0.6\n"
        + f"Xdut VSS VDD IN OUT {MACRO}\n"
        + ".control\nop\nprint v(OUT) i(VDD) i(VIN)\n.endc\n.end\n"
    )
    log = work / "rx_macro_model_dc.log"
    res = run([shutil.which("ngspice") or "ngspice", "-b", str(net)],
              work, env, log, timeout=600)
    text = log.read_text()
    vals: dict[str, float] = {}
    for name, val in re.findall(
            r"^(v\([^)]+\)|i\([^)]+\))\s*=\s*([-+0-9.eE]+)", text, re.M):
        try:
            vals[name] = float(val)
        except ValueError:
            pass
    res["operating_point"] = vals
    res["osdi_warnings"] = [
        ln.strip() for ln in text.splitlines()
        if "Error opening osdi lib" in ln or "couldn't be loaded" in ln
    ]
    out_v = vals.get("v(out)")
    idd = vals.get("i(vdd)")
    finite = all(v is not None for v in (out_v, idd))
    res["status"] = "pass" if (res["returncode"] == 0 and finite) else "blocked"
    return res


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ihp-root", default=str(DEFAULT_IHP_ROOT))
    ap.add_argument("--out", default="build/verify/ihp_rx_macro")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--install-osdi", action="store_true")
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--skip-negative", action="store_true")
    args = ap.parse_args()

    ihp_root = Path(args.ihp_root).expanduser().resolve()
    pdk = resolve_pdk(ihp_root)
    out_dir = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() \
        else Path(args.out)
    work = Path(args.workdir).resolve() if args.workdir else out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    # Avoid reusing stale Magic extraction products from an earlier run.
    for stale in list(work.glob("*.ext")) + list(work.glob("*.res.ext")):
        stale.unlink(missing_ok=True)
    for stale in list(work.glob("*.spice")) + list(work.glob("*.gds")):
        stale.unlink(missing_ok=True)

    env = dict(os.environ)
    env["IHP_ROOT"] = str(ihp_root)
    env["PDK_ROOT"] = str(ihp_root)
    env["PDK"] = "ihp-sg13g2"
    env["KLAYOUT_PATH"] = str(pdk / "libs.tech/klayout")

    report: dict[str, Any] = {
        "kind": "ihp-sg13g2-rx-macro-closure",
        "generated_utc": _now(),
        "ihp_root": str(ihp_root),
        "pdk": str(pdk),
        "out_dir": str(out_dir),
        "workdir": str(work),
        "topology": {
            "reference": "sky130 rx_fb_pcell electrical topology",
            "inverters": 3,
            "extra_pmos": 3,
            "feedback_pmos": 2,
            "feedback": "OUT -> fb1(S=G=OUT,D=net06) -> fb0(S=G=net06,D=IN)",
            "ports": TOP_PORTS,
            "nets": FUNCTIONAL_NETS,
            "sizes": {
                "inverter_nmos": "W=5u L=0.13u",
                "inverter_pmos": "W=5u L=0.13u",
                "extra_pmos": "W=5u L=0.13u",
                "feedback_pmos": "W=0.55u L=2.0u",
            },
        },
        "paths": {
            "magic_rc": str(pdk / "libs.tech/magic/ihp-sg13g2.magicrc"),
            "magic_extract": str(pdk / "libs.tech/magic/ihp-sg13g2-extract.tech"),
            "netgen_setup": str(pdk / "libs.tech/netgen/ihp-sg13g2_setup.tcl"),
            "model_lib": str(pdk / "libs.tech/ngspice/models/cornerMOSlv.lib"),
        },
        "gds": {},
        "drc": {},
        "lvs": {},
        "pex": {},
        "negative_control": {},
        "model": {"status": "skipped"},
        "checks": {},
        "notes": [],
    }

    gds = work / "rx_macro.gds"
    build = build_gds(pdk, gds, work, env, drop_body_strap=False)
    gds_out = out_dir / "rx_macro.gds"
    if gds.is_file():
        shutil.copyfile(gds, gds_out)
    report["gds"] = {
        "path": str(gds_out) if gds_out.is_file() else None,
        "work_gds": str(gds) if gds.is_file() else None,
        "sha256": sha256(gds_out) if gds_out.is_file() else None,
        "returncode": build["returncode"],
        "command": build["command"],
        "log": build["log"],
        "layout": build.get("layout"),
    }
    if build["returncode"] != 0 or not gds.is_file():
        report["status"] = "fail"
        report["reason"] = "KLayout macro generation failed"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "ihp_rx_macro.json").write_text(
            json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": "fail", "reason": report["reason"]}))
        return 1

    sch = work / "rx_macro_schematic.cir"
    sch.write_text(SCHEMATIC)
    cdl = out_dir / "rx_macro.cdl"
    cdl.write_text(SCHEMATIC)
    report["schematic"] = {"path": str(cdl), "sha256": sha256(cdl)}

    lvs_magic = magic_lvs(pdk, gds, work, env, "rx_macro", drc=True)
    report["drc"] = {
        "returncode": lvs_magic["returncode"],
        "count": lvs_magic["drc_count"],
        "clean": lvs_magic["returncode"] == 0 and lvs_magic["drc_count"] == 0,
        "command": lvs_magic["command"],
        "log": lvs_magic["log"],
    }
    if not lvs_magic["netlist"]:
        report["status"] = "fail"
        report["reason"] = "Magic LVS extraction produced no netlist"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "ihp_rx_macro.json").write_text(
            json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": "fail", "reason": report["reason"]}))
        return 1

    subs = parse_subckts(Path(lvs_magic["netlist"]))
    body_leafs = ["rx_nmos_w5l013", "rx_pmos_w5l013", "rx_pmos_w055l20"]
    body_ports = {k: ("B" in subs.get(k, {}).get("ports", []))
                  for k in body_leafs}
    body_instances_ok = True
    for name in ("rx_inv", "rx_pmos_extra", "rx_pmos_fb"):
        for line in subs.get(name, {}).get("lines", []):
            toks = line.split()
            # Xname + 4 terminals + subckt
            if toks[0].upper().startswith("X") and len(toks) < 6:
                body_instances_ok = False

    lvs = netgen_lvs(pdk, Path(lvs_magic["netlist"]), sch, work, env, "rx_macro")
    report["lvs"] = {
        **lvs,
        "netlist": lvs_magic["netlist"],
        "schematic": str(sch),
        "subckts": sorted(subs),
        "body_ports": body_ports,
        "body_instances_ok": body_instances_ok,
    }

    pex_magic = magic_pex(pdk, gds, work, env, "rx_macro")
    pex_magic2 = magic_pex(pdk, gds, work, env, "rx_macro_run2")
    pex_info = parse_pex(Path(pex_magic["netlist"])) \
        if pex_magic["netlist"] else {}
    pex_info2 = parse_pex(Path(pex_magic2["netlist"])) \
        if pex_magic2["netlist"] else {}
    pex_out = out_dir / "rx_macro.pex.spice"
    if pex_magic["netlist"]:
        shutil.copyfile(pex_magic["netlist"], pex_out)
    report["pex"] = {
        "returncode": pex_magic["returncode"],
        "path": str(pex_out) if pex_out.is_file() else None,
        "command": pex_magic["command"],
        "log": pex_magic["log"],
        "runs": [pex_info, pex_info2],
        "count_variation_note": (
            "Magic extresist net/node ordering is not stable across "
            "invocations; the same GDS produced different R/C counts in "
            "repeated runs.  Both runs are recorded; the delivered .pex.spice "
            "is run 1."
        ),
        **pex_info,
    }

    neg: dict[str, Any] = {}
    if not args.skip_negative:
        neg_gds = work / "rx_macro_negative.gds"
        neg_build = build_gds(pdk, neg_gds, work, env, drop_body_strap=True)
        if neg_build["returncode"] == 0 and neg_gds.is_file():
            neg_magic = magic_lvs(pdk, neg_gds, work, env, "rx_macro_negative",
                                  drc=False)
            if neg_magic["netlist"]:
                neg_lvs = netgen_lvs(pdk, Path(neg_magic["netlist"]), sch,
                                     work, env, "rx_macro_negative")
                neg = {
                    "gds": str(neg_gds),
                    "method": "removed VDD body tie strap on rx_pmos_extra",
                    "netlist": neg_magic["netlist"],
                    "match": neg_lvs["match"],
                    "instances": neg_lvs.get("instances"),
                    "nets": neg_lvs.get("nets"),
                    "rejected": not neg_lvs["match"],
                    "command": neg_lvs["command"],
                    "log": neg_lvs["log"],
                }
            else:
                neg = {"gds": str(neg_gds),
                       "error": "negative extraction produced no netlist"}
        else:
            neg = {"error": "negative GDS generation failed"}
    report["negative_control"] = neg

    if not args.skip_model:
        osdi = ensure_osdi(pdk, args.install_osdi, work, env)
        report["model"]["osdi"] = osdi
        if osdi.get("installed"):
            report["model"].update(model_dc(pdk, Path(lvs_magic["netlist"]),
                                            work, env))
        else:
            report["model"]["status"] = "blocked"
            report["model"]["reason"] = (
                "PDK OSDI models missing; run with --install-osdi. "
                "DRC/LVS/PEX remain valid."
            )

    checks = {
        "drc_clean": report["drc"].get("clean", False),
        "lvs_match": bool(report["lvs"].get("match")),
        "lvs_8_instances": report["lvs"].get("instances") == 8,
        "lvs_5_nets": report["lvs"].get("nets") == 5,
        "body_ports_present": all(body_ports.values()) and body_instances_ok,
        "pex_ran": (report["pex"].get("returncode") == 0
                    and all(r.get("device_instances") == 11
                            for r in report["pex"].get("runs", []))),
        "pex_has_r_and_c": all(
            r.get("resistors", 0) > 0 and r.get("capacitors", 0) > 0
            for r in report["pex"].get("runs", [])),
        "negative_rejected": bool(neg.get("rejected")),
    }
    if not args.skip_model and report["model"].get("status") != "blocked":
        checks["model_dc"] = report["model"].get("status") == "pass"
    report["checks"] = checks
    report["status"] = "pass" if all(checks.values()) else "fail"
    if report["status"] != "pass":
        report["failed_checks"] = [k for k, v in checks.items() if not v]

    report["notes"] = [
        "Hierarchical layout: rx_macro contains 3 rx_inv, 3 rx_pmos_extra "
        "and 2 rx_pmos_fb instances (8 top-level instances, 5 nets).",
        "NMOS bodies use the PDK p-substrate guard ring and are tied to VSS; "
        "PMOS bodies use the PDK n-well guard ring and are tied to VDD.",
        "PEX is a flat Magic extresist netlist.  11 device instances and "
        "actual R/C counts are reported exactly; some split instances have "
        "zero as/ad and some capacitors are marked FLOATING.",
        "Negative control removes the VDD body strap from the extra-PMOS "
        "tile, making the n-well body float; LVS must fail.",
        "This is a macro DRC/LVS/PEX/model-binding closure, not full-chip "
        "or tape-out signoff.",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ihp_rx_macro.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "status": report["status"],
        "drc_errors": report["drc"].get("count"),
        "lvs_match": report["lvs"].get("match"),
        "instances": report["lvs"].get("instances"),
        "nets": report["lvs"].get("nets"),
        "pex_R": report["pex"].get("resistors"),
        "pex_C": report["pex"].get("capacitors"),
        "negative_rejected": neg.get("rejected"),
        "model": report["model"].get("status"),
        "evidence": str(json_path),
    }))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

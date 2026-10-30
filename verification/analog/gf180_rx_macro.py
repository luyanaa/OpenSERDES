#!/usr/bin/env python3
"""Build and verify a bounded gf180mcuD RX macro.

Topology (same electrical intent as the sky130 ``rx_fb_pcell``):

  * 3 CMOS inverters (3 x nfet_03v3 + 3 x pfet_03v3, W=5u/L=0.28u)
  * 3 extra parallel pfet_03v3 (W=5u/L=0.28u), source VDD, drain OUT,
    gate IN, bulk VDD
  * 2 series feedback pfet_03v3 (W=0.55u/L=8u), OUT -> fb1 -> net06 ->
    fb0 -> IN, bulk VDD
  * ports VSS VDD OUT IN

The builder uses only the installed gf180mcuD PDK: the KLayout-API PCell
library ``gf180mcu_klayoutapi`` for the MOS devices, the same probe-only gate
contact/pad and MET1/M2 port-label technique as
``verification/analog/gf180_device_verify.py``, and explicit M1/M2/M3 straps
for the inverter/extra/feedback connections.

Evidence produced under ``build/verify/gf180_rx_macro``:

  * Magic DRC with the gf180mcuD deck,
  * KLayout LVS against a flat primitive-M schematic,
  * netgen LVS against the KLayout target netlist,
  * Magic ``extract; extresist all; ext2spice cthresh 0; ext2spice extresist on``
    PEX with device/body/R/C checks,
  * a negative control that removes one PMOS bulk-to-VDD strap and shows LVS
    fails.

This is a macro-level DRC/LVS/PEX closure for one placement and one size set,
not a full-chip or foundry signoff, and not a claim about arbitrary W/L,
guard-ring/multi-finger variants, or the generic analog LUT flow.
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
DEFAULT_OUT = ROOT / "build" / "verify" / "gf180_rx_macro" / "gf180_rx_macro_evidence.json"
DEFAULT_WORKDIR = ROOT / "build" / "verify" / "gf180_rx_macro"

CELL = "gf180_rx_macro"
PORTS = ["VSS", "VDD", "OUT", "IN"]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_gf_root(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for key in ("GF_ROOT", "GF180_ROOT"):
        val = os.environ.get(key)
        if val:
            candidates.append(Path(val).expanduser())
    candidates += [
        p for p in sorted(Path.home().glob(".ciel/ciel/gf180mcu/versions/*"),
                          reverse=True)
    ]
    for cand in candidates:
        try:
            cand = cand.resolve()
        except OSError:
            continue
        if (cand / "gf180mcuD/libs.tech/magic/gf180mcuD.magicrc").is_file():
            return cand
        if (cand / "libs.tech/magic/gf180mcuD.magicrc").is_file():
            return cand.parent
    raise FileNotFoundError("no gf180mcuD PDK found; set GF_ROOT or pass --gf-root")


def _gf_paths(gf_root: Path) -> dict[str, Path]:
    if (gf_root / "gf180mcuD").is_dir():
        pdk = gf_root / "gf180mcuD"
    else:
        pdk = gf_root
        gf_root = pdk.parent
    return {
        "gf_root": gf_root,
        "pdk": pdk,
        "magic_rc": pdk / "libs.tech/magic/gf180mcuD.magicrc",
        "netgen_setup": pdk / "libs.tech/netgen/gf180mcuD_setup.tcl",
        "klayout_lvs": pdk / "libs.tech/klayout/tech/lvs/gf180mcu.lvs",
        "ngspice_models": pdk / "libs.tech/ngspice/sm141064.spice",
    }


def _env(gf_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["GF_ROOT"] = str(gf_root)
    env["GF180_ROOT"] = str(gf_root)
    env["PDK_ROOT"] = str(gf_root)
    env["PDK"] = "gf180mcuD"
    pdk = gf_root / "gf180mcuD" if (gf_root / "gf180mcuD").is_dir() else gf_root
    env["GF_PDK"] = str(pdk)
    env["KLAYOUT_PATH"] = str(pdk / "libs.tech/klayout")
    return env


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str], log_path: Path,
         timeout: int = 1200) -> dict[str, Any]:
    cwd.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, text=True,
                          capture_output=True, timeout=timeout)
    text = proc.stdout + proc.stderr
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(text)
    return {
        "command": cmd,
        "cwd": str(cwd),
        "returncode": proc.returncode,
        "log": str(log_path),
        "stdout_tail": text[-8000:],
    }


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise FileNotFoundError(f"required tool not found: {name}")
    return found


# KLayout builder.  The script is kept as a plain string so its dict braces do
# not need f-string escaping.  __OUT__ / __GEOM__ are replaced before running.
_KL_BUILD = r'''
import json, os
import pya

OUT = "__OUT__"
GEOM = "__GEOM__"
LY = pya.Layout()
LY.dbu = 0.001

L_COMP = (22, 0)
L_POLY = (30, 0)
L_CONTACT = (33, 0)
L_M1 = (34, 0)
L_V1 = (35, 0)
L_M2 = (36, 0)
L_V2 = (38, 0)
L_M3 = (42, 0)
T_M1 = (34, 10)
T_M2 = (36, 10)

TOP = LY.create_cell("gf180_rx_macro")

def box(cell, layer, x1, y1, x2, y2):
    cell.shapes(LY.layer(*layer)).insert(pya.Box(int(x1), int(y1), int(x2), int(y2)))

def via1(cell, x, y):
    # 260 nm via, M2 pad 500 x 400
    box(cell, L_V1, x-130, y-130, x+130, y+130)
    box(cell, L_M2, x-250, y-200, x+250, y+200)

def via2(cell, x, y, pad_w=500):
    # 280 nm via, M2/M3 pads pad_w x 420
    hw = pad_w // 2
    box(cell, L_V2, x-140, y-140, x+140, y+140)
    box(cell, L_M2, x-hw, y-210, x+hw, y+210)
    box(cell, L_M3, x-hw, y-210, x+hw, y+210)

def make_device(pcell, w, l, name):
    params = {"volt": "3.3V", "bulk": "Bulk Tie", "w": w, "l": l,
              "ld": 0.44, "nf": 1, "grw": 0.36}
    c = LY.create_cell(pcell, "gf180mcu_klayoutapi", params)
    orig = c.cell_index()
    ci = LY.convert_cell_to_static(orig)
    LY.delete_cell(orig)
    c = LY.cell(ci)
    c.name = name
    c.flatten(True)

    def boxes(layer):
        return sorted([p.bbox() for p in pya.Region(c.shapes(LY.layer(*layer))).each()],
                      key=lambda b: (b.left, b.bottom))

    m1 = boxes(L_M1)
    if len(m1) < 3:
        raise RuntimeError("%s: expected >=3 M1 boxes" % name)
    bulk, left, right = m1[0], m1[1], m1[2]
    poly = pya.Region(c.shapes(LY.layer(*L_POLY))).bbox()
    comp = pya.Region(c.shapes(LY.layer(*L_COMP))).bbox()

    contact_size = 220
    for b in boxes(L_CONTACT):
        contact_size = max(contact_size, b.width(), b.height())

    poly_top = poly.top
    cx = (poly.left + poly.right) // 2
    cb = poly_top + 65
    ct = cb + contact_size
    cl = cx - contact_size // 2
    cr = cl + contact_size
    pl = cx - 250
    pr = cx + 250
    pt = ct + 150
    mb = cb - 35
    mt = pt
    while (pr - pl) * (mt - mb) < 144400:
        pt += 10
        mt = pt

    box(c, L_POLY, pl, poly_top, pr, pt)
    box(c, L_CONTACT, cl, cb, cr, ct)
    box(c, L_M1, pl, mb, pr, mt)
    return ci, {
        "name": name,
        "bbox": [c.bbox().left, c.bbox().bottom, c.bbox().right, c.bbox().top],
        "bulk": [bulk.left, bulk.bottom, bulk.right, bulk.top],
        "left": [left.left, left.bottom, left.right, left.top],
        "right": [right.left, right.bottom, right.right, right.top],
        "gate": [pl, mb, pr, mt],
    }

ci_n, g_n = make_device("nfet", 5.0, 0.28, "rx_n5")
ci_p, g_p = make_device("pfet", 5.0, 0.28, "rx_p5")
ci_f, g_f = make_device("pfet", 0.55, 8.0, "rx_pfb")

def place(ci, x, y, mirror=False):
    if mirror:
        t = pya.Trans(pya.Vector(x, y)) * pya.Trans(pya.Trans.M0)
    else:
        t = pya.Trans(pya.Vector(x, y))
    TOP.insert(pya.CellInstArray(ci, t))

def tb(g, x, y, mirror=False):
    def f(key):
        b = g[key]
        if mirror:
            return (x + b[0], y - b[3], x + b[2], y - b[1])
        return (x + b[0], y + b[1], x + b[2], y + b[3])
    return {k: f(k) for k in ("bulk", "left", "right", "gate")}

xn = [0, 8000, 16000]
xp = [0, 8000, 16000, 24000, 32000, 40000]
xf = [0, 12000]
for x in xn:
    place(ci_n, x, 0, False)
for x in xp:
    place(ci_p, x, 25000, True)
for x in xf:
    place(ci_f, x, 28000, False)

nT = [tb(g_n, x, 0, False) for x in xn]
pT = [tb(g_p, x, 25000, True) for x in xp]
fT = [tb(g_f, x, 28000, False) for x in xf]

VSS_Y0, VSS_Y1 = -800, -500
VDD_Y0, VDD_Y1 = 25500, 25800
box(TOP, L_M1, -1500, VSS_Y0, 18000, VSS_Y1)
box(TOP, L_M1, -1500, VDD_Y0, 42000, VDD_Y1)
TOP.shapes(LY.layer(*T_M1)).insert(pya.Text("VSS", pya.Trans(pya.Vector(-1000, -650))))
TOP.shapes(LY.layer(*T_M1)).insert(pya.Text("VDD", pya.Trans(pya.Vector(-1000, 25650))))

for t in nT:
    b = t["bulk"]; box(TOP, L_M1, b[0], VSS_Y0, b[2], 0)
    l = t["left"]; box(TOP, L_M1, l[0], VSS_Y0, l[2], 0)

for t in nT:
    r = t["right"]
    ytap = 2100
    box(TOP, L_M1, r[2]-40, ytap-250, r[2]+400, ytap+250)
    xv = r[2] + 160
    via1(TOP, xv, ytap)
    box(TOP, L_M2, xv-150, ytap-150, xv+150, 8000)

for t in nT:
    g = t["gate"]
    xg = (g[0]+g[2])//2
    yg = (g[1]+g[3])//2
    via1(TOP, xg, yg)
    via2(TOP, xg, yg)
    box(TOP, L_M3, xg+60, yg-150, xg+360, 10250)
    xin = xg+260
    via2(TOP, xin, 10250, pad_w=400)

for _i, t in enumerate(pT):
    b = t["bulk"]
    if not (os.environ.get("DROP_PMOS_BULK") == "1" and _i == 0):
        box(TOP, L_M1, b[0], b[3]-10, b[2], VDD_Y1)
    r = t["right"]; box(TOP, L_M1, r[0], r[3]-10, r[2], VDD_Y1)

for t in pT:
    l = t["left"]
    xd = (l[0]+l[2])//2
    yd = 22500
    via1(TOP, xd, yd)
    via2(TOP, xd, yd)
    box(TOP, L_M3, xd-150, 8000, xd+150, yd)
    via2(TOP, xd-20, 8250, pad_w=380)

for t in pT:
    g = t["gate"]
    xg = (g[0]+g[2])//2
    yg = (g[1]+g[3])//2
    via1(TOP, xg, yg)
    box(TOP, L_M2, xg-150, 10000, xg+150, yg+150)

box(TOP, L_M2, -3400, 8000, 41800, 8500)
box(TOP, L_M2, -2000, 10000, 41800, 10500)
TOP.shapes(LY.layer(*T_M2)).insert(pya.Text("OUT", pya.Trans(pya.Vector(-2500, 8250))))
TOP.shapes(LY.layer(*T_M2)).insert(pya.Text("IN", pya.Trans(pya.Vector(-1500, 10250))))

for t in fT:
    b = t["bulk"]
    box(TOP, L_M1, b[0], VDD_Y1-100, b[2], 28560)

t = fT[0]
l = t["left"]; g = t["gate"]
ys = 28200
via1(TOP, (l[0]+l[2])//2, ys)
box(TOP, L_M2, (l[0]+l[2])//2-150, ys, (l[0]+l[2])//2+150, 29400)
xg = (g[0]+g[2])//2; yg = (g[1]+g[3])//2
via1(TOP, xg, yg)
box(TOP, L_M2, xg-250, yg, xg+250, 29400)
box(TOP, L_M2, -3300, 29250, xg+200, 29750)
via2(TOP, -3100, 29500)
box(TOP, L_M3, -3250, 8000, -2950, 29500)
via2(TOP, -3100, 8250)

r = t["right"]
xd1 = (r[0]+r[2])//2
via1(TOP, xd1, ys)
box(TOP, L_M2, xd1-150, ys, xd1+150, 30350)
box(TOP, L_M2, xd1-150, 30100, 16620, 30600)

t = fT[1]
l = t["left"]; g = t["gate"]
xs0 = (l[0]+l[2])//2
via1(TOP, xs0, ys)
box(TOP, L_M2, xs0-150, ys, xs0+150, 30350)
xg0 = (g[0]+g[2])//2; yg0 = (g[1]+g[3])//2
via1(TOP, xg0, yg0)
box(TOP, L_M2, xg0-150, yg0, xg0+150, 30350)

r = t["right"]
xd0 = (r[0]+r[2])//2
via1(TOP, xd0, ys)
box(TOP, L_M2, xd0-150, 10000, xd0+150, ys)

LY.write(OUT)
with open(GEOM, "w") as fh:
    json.dump({
        "cell": TOP.name,
        "bbox": [TOP.bbox().left, TOP.bbox().bottom, TOP.bbox().right, TOP.bbox().top],
        "devices": 11,
        "block_instances": 8,
        "counts": {"nfet_03v3": 3, "pfet_03v3": 8},
        "ports": ["VSS", "VDD", "OUT", "IN"],
    }, fh, indent=2)
print("GF180_RX_MACRO_OK")
'''


def _gen_macro(gds: Path, workdir: Path, env: dict[str, str],
               klayout: str, drop_pmos_bulk: bool = False) -> dict[str, Any]:
    script = workdir / "build_macro.py"
    geom = workdir / "macro_geom.json"
    script.write_text(_KL_BUILD.replace("__OUT__", str(gds))
                      .replace("__GEOM__", str(geom)))
    gen_env = dict(env)
    if drop_pmos_bulk:
        gen_env["DROP_PMOS_BULK"] = "1"
    rep = _run([klayout, "-zz", "-r", str(script)], cwd=workdir,
               env=gen_env, log_path=workdir / "build.log", timeout=600)
    rep["ok"] = bool(rep["returncode"] == 0 and gds.is_file()
                     and "GF180_RX_MACRO_OK" in rep["stdout_tail"])
    rep["gds"] = str(gds)
    rep["script"] = str(script)
    if gds.is_file():
        rep["gds_sha256"] = _sha256(gds)
    rep["geom"] = json.loads(geom.read_text()) if geom.is_file() else None
    return rep


def _write_netlists(workdir: Path) -> dict[str, str]:
    flat = workdir / "gf180_rx_macro.spice"
    cdl = workdir / "gf180_rx_macro.cdl"
    flat_text = (
        "* gf180mcuD RX macro flat primitive-M schematic for LVS\n"
        ".subckt gf180_rx_macro VSS VDD OUT IN\n"
        "MN0 OUT IN VSS VSS nfet_03v3 L=0.28U W=5U\n"
        "MP0 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "MN1 OUT IN VSS VSS nfet_03v3 L=0.28U W=5U\n"
        "MP1 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "MN2 OUT IN VSS VSS nfet_03v3 L=0.28U W=5U\n"
        "MP2 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "MP3 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "MP4 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "MP5 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "MFB1 net06 OUT OUT VDD pfet_03v3 L=8U W=0.55U\n"
        "MFB0 IN net06 net06 VDD pfet_03v3 L=8U W=0.55U\n"
        ".ends gf180_rx_macro\n"
    )
    cdl_text = (
        "* gf180mcuD RX macro hierarchical CDL: 8 block instances\n"
        ".SUBCKT gf180_inv IN OUT VDD VSS\n"
        "MN OUT IN VSS VSS nfet_03v3 L=0.28U W=5U\n"
        "MP OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        ".ENDS gf180_inv\n"
        ".SUBCKT gf180_rx_macro VSS VDD OUT IN\n"
        "XINV0 IN OUT VDD VSS gf180_inv\n"
        "XINV1 IN OUT VDD VSS gf180_inv\n"
        "XINV2 IN OUT VDD VSS gf180_inv\n"
        "XP3 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "XP4 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "XP5 OUT IN VDD VDD pfet_03v3 L=0.28U W=5U\n"
        "XFB1 OUT OUT net06 VDD pfet_03v3 L=8U W=0.55U\n"
        "XFB0 net06 net06 IN VDD pfet_03v3 L=8U W=0.55U\n"
        ".ENDS gf180_rx_macro\n"
    )
    flat.write_text(flat_text)
    cdl.write_text(cdl_text)
    return {
        "flat_spice": str(flat),
        "flat_spice_sha256": _sha256(flat),
        "cdl": str(cdl),
        "cdl_sha256": _sha256(cdl),
        "flat_text": flat_text,
        "cdl_text": cdl_text,
    }


def _magic_drc(gds: Path, workdir: Path, env: dict[str, str],
               magic: str, rc: Path) -> dict[str, Any]:
    script = workdir / "drc.tcl"
    script.write_text(
        f"gds read {{{gds.with_suffix('')}}}\n"
        f"load {CELL} -dereference\n"
        "select top cell\n"
        "drc check\n"
        "drc catchup\n"
        'puts "GF180_DRC_BEGIN"\n'
        "drc count total\n"
        'puts "GF180_DRC_END"\n'
        "quit\n"
    )
    rep = _run([magic, "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
               cwd=workdir, env=env, log_path=workdir / "drc.log")
    log = Path(rep["log"])
    text = log.read_text(errors="replace") if log.is_file() else rep["stdout_tail"]
    m = re.search(r"Total DRC errors found:\s*(\d+)", text, re.I)
    rep["drc_errors"] = int(m.group(1)) if m else None
    rep["clean"] = bool(rep["returncode"] == 0 and rep["drc_errors"] == 0)
    rep["script"] = str(script)
    return rep


def _magic_pex(gds: Path, workdir: Path, env: dict[str, str],
               magic: str, rc: Path) -> dict[str, Any]:
    raw = workdir / f"{CELL}.raw.spice"
    script = workdir / "pex.tcl"
    script.write_text(
        f"gds read {{{gds.with_suffix('')}}}\n"
        f"load {CELL} -dereference\n"
        "select top cell\n"
        "port makeall\n"
        "extract\n"
        "extresist all\n"
        "ext2spice cthresh 0\n"
        "ext2spice extresist on\n"
        f"ext2spice -o {{{raw}}}\n"
        'puts "GF180_PEX_DONE"\n'
        "quit\n"
    )
    rep = _run([magic, "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
               cwd=workdir, env=env, log_path=workdir / "pex.log", timeout=900)
    ext = workdir / f"{CELL}.ext"
    ext_ports: list[str] = []
    if ext.is_file():
        for line in ext.read_text(errors="replace").splitlines():
            m = re.match(r'port\s+"([^"]+)"', line)
            if m and m.group(1) not in ext_ports:
                ext_ports.append(m.group(1))
    counts = {"R": 0, "C": 0, "X": 0}
    devices: list[dict[str, Any]] = []
    raw_text = raw.read_text(errors="replace") if raw.is_file() else ""
    for line in raw_text.splitlines():
        s = line.strip()
        if not s or s.startswith("*") or s.startswith("."):
            continue
        if s[0] in counts:
            counts[s[0]] += 1
        if s.startswith("X"):
            tok = s.split()
            if len(tok) >= 6:
                devices.append({
                    "line": s,
                    "nodes": tok[1:5],
                    "model": tok[5],
                })
    body_ok = True
    type_counts: dict[str, int] = {}
    for d in devices:
        model = d["model"]
        type_counts[model] = type_counts.get(model, 0) + 1
        body = d["nodes"][3]
        if model == "nfet_03v3" and body != "VSS":
            body_ok = False
        if model == "pfet_03v3" and body != "VDD":
            body_ok = False
    log = Path(rep["log"])
    log_text = log.read_text(errors="replace") if log.is_file() else rep["stdout_tail"]
    rep.update({
        "raw": str(raw),
        "ext": str(ext),
        "script": str(script),
        "ext_ports": ext_ports,
        "counts": counts,
        "devices": devices,
        "type_counts": type_counts,
        "body_nets_ok": body_ok,
        "ok": bool(
            rep["returncode"] == 0
            and raw.is_file()
            and "GF180_PEX_DONE" in log_text
            and counts["X"] == 11
            and type_counts.get("nfet_03v3") == 3
            and type_counts.get("pfet_03v3") == 8
            and body_ok
            and set(ext_ports) == set(PORTS)
        ),
    })
    if raw.is_file():
        rep["raw_sha256"] = _sha256(raw)
    return rep


def _klayout_lvs(gds: Path, schematic: Path, workdir: Path, env: dict[str, str],
                 klayout: str, deck: Path, tag: str = "lvs") -> dict[str, Any]:
    report = workdir / f"{tag}.lyrdb"
    netlist = workdir / f"{tag}.layout.spice"
    rep = _run([
        klayout, "-b", "-r", str(deck),
        "-rd", f"input={gds}",
        "-rd", f"topcell={CELL}",
        "-rd", f"schematic={schematic}",
        "-rd", f"report={report}",
        "-rd", f"target_netlist={netlist}",
        "-rd", "schematic_translate=false",
        "-rd", "run_mode=flat",
        "-rd", "top_lvl_pins=true",
        "-rd", "check_ports=false",
    ], cwd=workdir, env=env, log_path=workdir / f"{tag}.log", timeout=1200)
    log = Path(rep["log"])
    text = log.read_text(errors="replace") if log.is_file() else rep["stdout_tail"]
    rep["match"] = bool(rep["returncode"] == 0 and "Netlists match." in text)
    rep["report"] = str(report)
    rep["target_netlist"] = str(netlist)
    rep["target_netlist_exists"] = netlist.is_file()
    if netlist.is_file():
        net_text = netlist.read_text(errors="replace")
        rep["target_netlist_text"] = net_text
        rep["target_netlist_sha256"] = _sha256(netlist)
        mdevs = [l for l in net_text.splitlines() if l.startswith("M$")]
        rep["device_count"] = len(mdevs)
        rep["device_types"] = {
            "nfet_03v3": sum(1 for l in mdevs if " nfet_03v3 " in l),
            "pfet_03v3": sum(1 for l in mdevs if " pfet_03v3 " in l),
        }
        nets = set()
        for l in mdevs:
            tok = l.split()
            if len(tok) >= 5:
                nets.update(tok[1:5])
        rep["net_count"] = len(nets)
        rep["nets"] = sorted(nets)
    return rep


def _netgen_lvs(layout: Path, schematic: Path, workdir: Path, env: dict[str, str],
                netgen: str, setup: Path, tag: str = "netgen") -> dict[str, Any]:
    report = workdir / f"{tag}.out"
    rep = _run([
        netgen, "-batch", "lvs",
        f"{layout} {CELL}", f"{schematic} {CELL}", str(setup), str(report),
    ], cwd=workdir, env=env, log_path=workdir / f"{tag}.log", timeout=600)
    log = Path(rep["log"])
    text = (log.read_text(errors="replace") if log.is_file() else rep["stdout_tail"])
    text += report.read_text(errors="replace") if report.is_file() else ""
    m = re.search(r"Final result:\s*\n?\s*([^\n]+)", text)
    final_line = m.group(1).strip() if m else ""
    rep["final_result"] = final_line
    rep["match"] = bool(
        rep["returncode"] == 0
        and final_line.startswith(("Circuits match uniquely", "Netlists match uniquely"))
        and "failed pin matching" not in text.lower()
    )
    rep["report"] = str(report)
    mdev = re.search(r"Circuit .* contains (\d+) device instances", text)
    mnet = re.search(r"Circuit contains (\d+) nets", text)
    rep["device_count"] = int(mdev.group(1)) if mdev else None
    rep["net_count"] = int(mnet.group(1)) if mnet else None
    return rep


def _model_binding(models: Path) -> dict[str, Any]:
    text = models.read_text(errors="replace") if models.is_file() else ""
    out = {"models_file": str(models), "exists": models.is_file(), "subckts": {}}
    for m in ("nfet_03v3", "pfet_03v3"):
        out["subckts"][m] = bool(
            re.search(rf"^\s*\.subckt\s+{re.escape(m)}\s", text, re.I | re.M)
        )
    return out


def verify(*, paths: dict[str, Path], env: dict[str, str], workdir: Path,
           tools: dict[str, str], out_path: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    gds = workdir / f"{CELL}.gds"
    netlists = _write_netlists(workdir)
    gen = _gen_macro(gds, workdir, env, tools["klayout"])
    report: dict[str, Any] = {
        "kind": "gf180-rx-macro-closure",
        "generated_utc": _utc(),
        "cell": CELL,
        "ports": PORTS,
        "topology": {
            "inverters": 3,
            "inverter_devices": {"nfet_03v3": 3, "pfet_03v3": 3},
            "extra_pmos": 3,
            "feedback_pmos": 2,
            "block_instances": 8,
            "device_instances": 11,
            "nets": 5,
            "sizes": {
                "inverter_n": "nfet_03v3 W=5u L=0.28u",
                "inverter_p": "pfet_03v3 W=5u L=0.28u",
                "extra_p": "pfet_03v3 W=5u L=0.28u",
                "feedback": "pfet_03v3 W=0.55u L=8u",
            },
        },
        "environment": {
            "gf_root": str(paths["gf_root"]),
            "gf_pdk": str(paths["pdk"]),
            "magic_rc": str(paths["magic_rc"]),
            "klayout_lvs": str(paths["klayout_lvs"]),
            "netgen_setup": str(paths["netgen_setup"]),
            "tools": tools,
            "versions": {},
        },
        "layout": gen,
        "netlists": netlists,
        "model_binding": _model_binding(paths["ngspice_models"]),
        "scope": (
            "One gf180mcuD RX macro placement: 3 inverters + 3 extra PMOS + "
            "2 series feedback PMOS, ports VSS VDD OUT IN. Macro-level "
            "DRC/LVS/PEX only; not full-chip or foundry signoff."
        ),
    }
    if not gen["ok"]:
        report["status"] = "FAIL"
        report["reason"] = "KLayout macro generation failed"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n")
        return report

    drc = _magic_drc(gds, workdir, env, tools["magic"], paths["magic_rc"])
    report["drc"] = drc

    pex = _magic_pex(gds, workdir, env, tools["magic"], paths["magic_rc"])
    report["pex"] = pex

    lvs = _klayout_lvs(gds, Path(netlists["flat_spice"]), workdir, env,
                       tools["klayout"], paths["klayout_lvs"], tag="lvs")
    report["lvs_klayout"] = lvs

    if lvs.get("target_netlist_exists"):
        nglvs = _netgen_lvs(Path(lvs["target_netlist"]),
                            Path(netlists["flat_spice"]), workdir, env,
                            tools["netgen"], paths["netgen_setup"], tag="netgen")
    else:
        nglvs = {"match": False, "note": "no KLayout target netlist"}
    report["lvs_netgen"] = nglvs

    # The hierarchical CDL has the 8 block instances.  netgen flattens it,
    # while the PDK KLayout schematic reader does not; use netgen for the
    # hierarchical reference check.
    if lvs.get("target_netlist_exists"):
        nglvs_h = _netgen_lvs(Path(lvs["target_netlist"]),
                              Path(netlists["cdl"]), workdir, env,
                              tools["netgen"], paths["netgen_setup"],
                              tag="netgen_hier")
    else:
        nglvs_h = {"match": False, "note": "no KLayout target netlist"}
    report["lvs_netgen_hierarchical"] = nglvs_h

    neg_dir = workdir / "negative_drop_pmos_bulk"
    neg_dir.mkdir(parents=True, exist_ok=True)
    neg_gds = neg_dir / f"{CELL}.gds"
    neg_gen = _gen_macro(neg_gds, neg_dir, env, tools["klayout"],
                         drop_pmos_bulk=True)
    if neg_gen["ok"]:
        neg_lvs = _klayout_lvs(neg_gds, Path(netlists["flat_spice"]),
                               neg_dir, env, tools["klayout"],
                               paths["klayout_lvs"], tag="negative_lvs")
    else:
        neg_lvs = {"match": False, "note": "negative generation failed"}
    report["negative_control"] = {
        "change": "removed first PMOS bulk-to-VDD M1 strap",
        "gds": str(neg_gds),
        "generation": neg_gen,
        "lvs_klayout": neg_lvs,
        "failed_as_expected": bool(neg_gen["ok"] and not neg_lvs.get("match")),
    }

    checks = {
        "layout_generated": gen["ok"],
        "drc_clean": drc["clean"],
        "pex_extracted": pex["ok"],
        "pex_11_devices": pex["counts"]["X"] == 11,
        "pex_bodies_vss_vdd": pex["body_nets_ok"],
        "lvs_klayout_match": lvs.get("match", False),
        "lvs_device_count_11": lvs.get("device_count") == 11,
        "lvs_net_count_5": lvs.get("net_count") == 5,
        "netgen_match": nglvs.get("match", False),
        "netgen_hierarchical_match": nglvs_h.get("match", False),
        "negative_control_fails": report["negative_control"]["failed_as_expected"],
        "model_binding": all(report["model_binding"]["subckts"].values()),
    }
    report["checks"] = checks
    report["status"] = "PASS" if all(checks.values()) else "FAIL"
    if report["status"] != "PASS":
        report["reason"] = ", ".join(k for k, v in checks.items() if not v)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gf-root", default=None)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    out_path = Path(args.out).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        gf_root = _find_gf_root(args.gf_root)
        paths = _gf_paths(gf_root)
        env = _env(gf_root)
        tools = {
            "magic": _tool("magic"),
            "klayout": _tool("klayout"),
            "netgen": _tool("netgen"),
        }
        for key in ("magic_rc", "netgen_setup", "klayout_lvs", "ngspice_models"):
            if not paths[key].is_file():
                raise FileNotFoundError(f"missing PDK file: {paths[key]}")
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = verify(paths=paths, env=env, workdir=workdir, tools=tools,
                    out_path=out_path)

    # Tool versions (best-effort; never affect the pass/fail gate).
    versions = report["environment"]["versions"]
    try:
        p = subprocess.run([tools["klayout"], "-v"], text=True,
                           capture_output=True, timeout=30)
        versions["klayout"] = (p.stdout or p.stderr).strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        versions["klayout"] = "unknown"
    try:
        p = subprocess.run([tools["netgen"], "-batch", "version"], text=True,
                           capture_output=True, timeout=30)
        versions["netgen"] = (p.stdout or p.stderr).strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        versions["netgen"] = "unknown"
    try:
        vscript = workdir / "version.tcl"
        vscript.write_text('puts "MAGIC_VERSION_PROBE"\nquit\n')
        p = subprocess.run([tools["magic"], "-dnull", "-noconsole", "-rcfile",
                            str(paths["magic_rc"]), str(vscript)],
                           text=True, capture_output=True, timeout=60, env=env)
        m = re.search(r"Magic\s+([0-9][^\n]*)", p.stdout + p.stderr)
        versions["magic"] = f"Magic {m.group(1)}" if m else "unknown"
    except Exception:  # noqa: BLE001
        versions["magic"] = "unknown"
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    print(json.dumps({
        "status": report["status"],
        "checks": report.get("checks", {}),
        "drc_errors": report.get("drc", {}).get("drc_errors"),
        "pex_counts": report.get("pex", {}).get("counts"),
        "pex_types": report.get("pex", {}).get("type_counts"),
        "lvs_klayout": report.get("lvs_klayout", {}).get("match"),
        "lvs_netgen": report.get("lvs_netgen", {}).get("match"),
        "lvs_netgen_hierarchical": report.get("lvs_netgen_hierarchical", {}).get("match"),
        "negative_control_fails": report.get("negative_control", {}).get("failed_as_expected"),
        "out": str(out_path),
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

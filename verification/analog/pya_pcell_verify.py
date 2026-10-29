#!/usr/bin/env python3
"""Headless pure-pya PCell device-level DRC/LVS probe."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from verification.analog.magic_pex import find_pdk_root, magic_rcfile

DEFAULT_PDK = Path("/home/yanlu/.ciel/ciel/sky130/versions/792ef67037d74c8c51811eab3ce23cf793491296")


def klayout_make(out: Path, kind: str, l: float, w: float, nf: int) -> dict:
    script = out.parent / "make_pcell.py"
    script.write_text(f'''import sys, pya, json
sys.path.insert(0, {str(ROOT)!r})
from verification.analog import pya_pcell
pya_pcell.register()
ly = pya.Layout()
mos = ly.create_cell("mos", "openserdes_sky130", {{"kind": {kind!r}, "l": {l}, "w": {w}, "nf": {nf}, "gate_con_pos": "top"}})
top = ly.create_cell("TOP")
top.insert(pya.CellInstArray(mos, pya.Trans()))
# Promote D/G/S labels to the parent cell; Magic makes these subckt pins.
m1 = ly.layer(68, 20); lab = ly.layer(68, 5)
boxes = [s.box for s in mos.shapes(m1).each() if s.is_box()]
center = lambda b: ((b.left + b.right) // 2, (b.bottom + b.top) // 2)
gate = max(boxes, key=lambda b: center(b)[1])
lower = [b for b in boxes if b is not gate]
src = min(lower, key=lambda b: center(b)[0])
drain = max(lower, key=lambda b: center(b)[0])
for text, b in (("D", drain), ("G", gate), ("S", src)):
    x, y = center(b)
    top.shapes(lab).insert(pya.Text(text, pya.Trans(pya.Vector(x, y))))
ly.write({str(out)!r})
print(json.dumps({{"cell": "TOP", "bbox": top.bbox().to_s()}}))
''')
    proc = subprocess.run(["klayout", "-b", "-r", str(script)], text=True,
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def magic_extract(gds: Path, work: Path) -> Path:
    script = work / "extract.tcl"
    script.write_text(f"""gds read {gds}
load TOP -dereference
select top cell
extract
ext2spice lvs
ext2spice -o {work / 'extracted.spice'}
quit
""")
    pdk = Path(os.environ.get("PDK_ROOT", str(DEFAULT_PDK)))
    rc = magic_rcfile(pdk)
    proc = subprocess.run(["magic", "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
                          cwd=work, text=True, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    return work / "extracted.spice"


def netgen_check(extracted: Path, work: Path, kind: str, l: float, w: float, nf: int) -> dict:
    # Extracted top is "mos" with D/G/S/VSUBS pins; compare against the PDK
    # subcircuit-style device, not a bare M card.
    model = "sky130_fd_pr__nfet_01v8" if kind == "nfet" else "sky130_fd_pr__pfet_01v8"
    sch = work / "schematic.cir"
    lines = []
    if kind == "nfet":
        lines.append(".subckt mos G D S VSUBS")
        for i in range(nf):
            lines.append(f"X{i} D G S VSUBS {model} w={w:g} l={l:g}")
    else:
        # bulk=None pfet has no extracted body pin
        lines.append(".subckt mos G D S")
        for i in range(nf):
            lines.append(f"X{i} D G S {model} w={w:g} l={l:g}")
    lines.append(".ends")
    sch.write_text("\n".join(lines) + "\n")
    pdk = Path(os.environ.get("PDK_ROOT", str(DEFAULT_PDK)))
    setup = pdk / "sky130A/libs.tech/netgen/sky130A_setup.tcl"
    proc = subprocess.run(["netgen", "-batch", "lvs",
                           f"{extracted} mos", f"{sch} mos", str(setup)],
                          cwd=work, text=True, capture_output=True, timeout=300)
    text = proc.stdout + proc.stderr
    return {"match": "Circuits match uniquely." in text,
            "returncode": proc.returncode,
            "tail": text[-1200:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=("nfet", "pfet"), default="nfet")
    ap.add_argument("--l", type=float, default=0.15)
    ap.add_argument("--w", type=float, default=5.0)
    ap.add_argument("--nf", type=int, default=1)
    ap.add_argument("--out", default="build/verify/pya_pcell_lvs.json")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--pdk-root", default=None)
    args = ap.parse_args()
    if args.pdk_root:
        os.environ["PDK_ROOT"] = args.pdk_root
    work = Path(args.workdir) if getattr(args, "workdir", None) else Path(tempfile.mkdtemp())
    if True:
        pass
        gds = work / "pcell.gds"
        info = klayout_make(gds, args.kind, args.l, args.w, args.nf)
        raw = magic_extract(gds, work)
        lvs = netgen_check(raw, work, args.kind, args.l, args.w, args.nf)
        result = {"kind": "pure-pya-pcell-lvs", "device": vars(args),
                  "gds": str(gds), "layout": info, "lvs": lvs}
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"device": f"{args.kind} {args.w}/{args.l} nf={args.nf}",
                          "lvs_match": lvs["match"], "workdir": str(work)}))
    return 0 if lvs["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

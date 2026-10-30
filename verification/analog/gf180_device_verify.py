#!/usr/bin/env python3
"""Bounded gf180mcuD device-level closure for nfet_03v3 / pfet_03v3.

This script uses the *installed* gf180mcuD PDK (not sky130) to build a single
bulk-tied MOS device from the PDK's KLayout-API PCell, add probe-only gate
access and top-level labels, then run:

  * Magic DRC with the gf180mcuD deck,
  * Magic extraction (``extract`` + ``extresist all`` + ``ext2spice extresist on``),
  * KLayout LVS and netgen LVS against a primitive-M schematic,
  * an ngspice DC operating point using the extracted PEX netlist and the
    installed ``design.ngspice`` / ``sm141064.spice typical`` models.

The result is evidence for one W/L bias point, not a foundry signoff and not a
claim about arbitrary cells, guard rings, multi-finger layouts or the generic
schematic LUT flow.

Example::

    python3 verification/analog/gf180_device_verify.py \
        --out build/verify/gf180_device/gf180_device_evidence.json

The script is intentionally bounded: it only writes under ``--workdir`` and
never edits PDK files or other technology configs.
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
DEFAULT_OUT = ROOT / "build" / "verify" / "gf180_device" / "gf180_device_evidence.json"
DEFAULT_WORKDIR = ROOT / "build" / "verify" / "gf180_device"

DEVICES = {
    "nfet": {"cell": "nfet_03v3", "pcell": "nfet", "model": "nfet_03v3"},
    "pfet": {"cell": "pfet_03v3", "pcell": "pfet", "model": "pfet_03v3"},
}

PCell_PARAMS = {
    "volt": "3.3V",
    "bulk": "Bulk Tie",
    "w": 1.0,
    "l": 0.28,
    "ld": 0.44,
    "nf": 1,
    "grw": 0.36,
}

# GDS layer/datatype numbers used by the gf180mcuD stack.
LY_COMP = (22, 0)
LY_POLY = (30, 0)
LY_CONTACT = (33, 0)
LY_METAL1 = (34, 0)
LY_METAL1_LABEL = (34, 10)


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
        p
        for p in sorted(
            Path.home().glob(".ciel/ciel/gf180mcu/versions/*"), reverse=True
        )
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
    raise FileNotFoundError(
        "no gf180mcuD PDK found; set GF_ROOT or pass --gf-root"
    )


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
        "magic_tech": pdk / "libs.tech/magic/gf180mcuD.tech",
        "netgen_setup": pdk / "libs.tech/netgen/gf180mcuD_setup.tcl",
        "klayout_lvs": pdk / "libs.tech/klayout/tech/lvs/gf180mcu.lvs",
        "ngspice_design": pdk / "libs.tech/ngspice/design.ngspice",
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


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    timeout: int = 900,
) -> dict[str, Any]:
    cwd.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
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


def _klayout_generate(
    dev: str,
    gds: Path,
    workdir: Path,
    env: dict[str, str],
    klayout: str,
) -> dict[str, Any]:
    """Generate the PCell plus probe-only gate access and labels."""
    script = workdir / "generate.py"
    geom = workdir / "geom.json"
    script.write_text(
        f'''import json, os
import pya

dev = {dev!r}
params = {PCell_PARAMS!r}
ly = pya.Layout()
ly.dbu = 0.001
c = ly.create_cell(dev, "gf180mcu_klayoutapi", params)
orig = c.cell_index()
ci = ly.convert_cell_to_static(orig)
ly.delete_cell(orig)
c = ly.cell(ci)
c.name = dev + "_03v3"
c.flatten(True)

def boxes(layer):
    return sorted(
        [p.bbox() for p in pya.Region(c.shapes(ly.layer(*layer))).each()],
        key=lambda b: (b.left, b.bottom),
    )

comp_boxes = boxes({LY_COMP!r})
poly_boxes = boxes({LY_POLY!r})
contact_boxes = boxes({LY_CONTACT!r})
m1_boxes = boxes({LY_METAL1!r})
if len(m1_boxes) < 3:
    raise RuntimeError("expected at least three Metal1 shapes, got %d" % len(m1_boxes))
bulk_m1, left_m1, right_m1 = m1_boxes[0], m1_boxes[1], m1_boxes[2]
comp_bb = pya.Region(c.shapes(ly.layer(*{LY_COMP!r}))).bbox()
poly_bb = pya.Region(c.shapes(ly.layer(*{LY_POLY!r}))).bbox()

# 220 nm is the PDK contact size used by this PCell; keep at least that.
contact_size = 220
for b in contact_boxes:
    contact_size = max(contact_size, b.width(), b.height())

poly_top = poly_bb.top
active_top = comp_bb.top
cx = (poly_bb.left + poly_bb.right) // 2
contact_bottom = poly_top + 65       # CO.3 poly overlap >= 0.065 um
contact_top = contact_bottom + contact_size
contact_left = cx - contact_size // 2
contact_right = contact_left + contact_size

pad_half = 180                        # 360 nm wide poly/M1 pad
pad_left = cx - pad_half
pad_right = cx + pad_half
poly_pad_top = contact_top + 150
m1_pad_bottom = contact_bottom - 35
m1_pad_top = poly_pad_top

# M1 minimum area is 0.1444 um^2; expand the pad if needed.
while (pad_right - pad_left) * (m1_pad_top - m1_pad_bottom) < 144400:
    poly_pad_top += 10
    m1_pad_top = poly_pad_top

# Keep the gate pad >= 0.23 um (Euclidean) from the source/drain M1.
def gap(a, b):
    dx = max(a.left - b.right, b.left - a.right, 0)
    dy = max(a.bottom - b.top, b.bottom - a.top, 0)
    return (dx * dx + dy * dy) ** 0.5

gate_pad = pya.Box(pad_left, m1_pad_bottom, pad_right, m1_pad_top)
for name, b in (("left", left_m1), ("right", right_m1), ("bulk", bulk_m1)):
    g = gap(gate_pad, b)
    if g < 230:
        raise RuntimeError("gate M1 pad to %s M1 gap %.1f nm < 230 nm" % (name, g))

c.shapes(ly.layer(*{LY_POLY!r})).insert(
    pya.Box(pad_left, poly_top, pad_right, poly_pad_top)
)
c.shapes(ly.layer(*{LY_CONTACT!r})).insert(
    pya.Box(contact_left, contact_bottom, contact_right, contact_top)
)
c.shapes(ly.layer(*{LY_METAL1!r})).insert(
    pya.Box(pad_left, m1_pad_bottom, pad_right, m1_pad_top)
)
for name, b in (("S", left_m1), ("D", right_m1), ("B", bulk_m1),
                ("G", gate_pad)):
    x = (b.left + b.right) // 2
    y = (b.bottom + b.top) // 2
    c.shapes(ly.layer(*{LY_METAL1_LABEL!r})).insert(
        pya.Text(name, pya.Trans(pya.Point(x, y)))
    )

ly.write({str(gds)!r})
with open({str(geom)!r}, "w") as fh:
    json.dump({{
        "cell": c.name,
        "bbox_dbu": [c.bbox().left, c.bbox().bottom, c.bbox().right, c.bbox().top],
        "comp_bbox": [comp_bb.left, comp_bb.bottom, comp_bb.right, comp_bb.top],
        "poly_bbox": [poly_bb.left, poly_bb.bottom, poly_bb.right, poly_bb.top],
        "metal1_boxes": [[b.left, b.bottom, b.right, b.top] for b in m1_boxes],
        "gate_poly": [pad_left, poly_top, pad_right, poly_pad_top],
        "gate_contact": [contact_left, contact_bottom, contact_right, contact_top],
        "gate_metal1": [pad_left, m1_pad_bottom, pad_right, m1_pad_top],
        "labels": {{"S": "left_m1", "D": "right_m1", "B": "bulk_m1", "G": "gate_pad"}},
    }}, fh, indent=2)
print("GF180_GENERATE_OK", c.name)
''',
        encoding="utf-8",
    )
    rep = _run(
        [klayout, "-zz", "-r", str(script)],
        cwd=workdir,
        env=env,
        log_path=workdir / "generate.log",
    )
    rep["ok"] = (
        rep["returncode"] == 0
        and gds.is_file()
        and "GF180_GENERATE_OK" in rep["stdout_tail"]
    )
    rep["gds"] = str(gds)
    rep["script"] = str(script)
    if gds.is_file():
        rep["gds_sha256"] = _sha256(gds)
    rep["geom"] = json.loads(geom.read_text()) if geom.is_file() else None
    return rep


def _write_schematic(dev: str, model: str, path: Path) -> str:
    text = (
        "* gf180mcuD primitive-M schematic for device-level LVS\n"
        f".subckt {model} S D B G\n"
        f"M0 D G S B {model} L=0.28U W=1U\n"
        f".ends {model}\n"
    )
    path.write_text(text)
    return text


def _magic_drc(
    gds: Path,
    cell: str,
    workdir: Path,
    env: dict[str, str],
    magic: str,
    rc: Path,
) -> dict[str, Any]:
    script = workdir / "drc.tcl"
    script.write_text(
        f"gds read {{{gds.with_suffix('')}}}\n"
        f"load {cell} -dereference\n"
        "select top cell\n"
        "drc check\n"
        "drc catchup\n"
        'puts "GF180_DRC_BEGIN"\n'
        "drc count total\n"
        'puts "GF180_DRC_END"\n'
        "quit\n"
    )
    rep = _run(
        [magic, "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
        cwd=workdir,
        env=env,
        log_path=workdir / "drc.log",
    )
    log = Path(rep["log"])
    text = log.read_text(errors="replace") if log.is_file() else rep["stdout_tail"]
    m = re.search(r"Total DRC errors found:\s*(\d+)", text, re.I)
    rep["drc_errors"] = int(m.group(1)) if m else None
    rep["clean"] = bool(rep["returncode"] == 0 and rep["drc_errors"] == 0)
    rep["script"] = str(script)
    rep["magic_steps"] = ["gds read", "load -dereference", "drc check",
                          "drc catchup", "drc count total"]
    return rep


def _magic_pex(
    gds: Path,
    cell: str,
    workdir: Path,
    env: dict[str, str],
    magic: str,
    rc: Path,
) -> dict[str, Any]:
    raw = workdir / f"{cell}.raw.spice"
    script = workdir / "pex.tcl"
    script.write_text(
        f"gds read {{{gds.with_suffix('')}}}\n"
        f"load {cell} -dereference\n"
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
    rep = _run(
        [magic, "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
        cwd=workdir,
        env=env,
        log_path=workdir / "pex.log",
    )
    ext = workdir / f"{cell}.ext"
    ext_ports = []
    ext_model = None
    if ext.is_file():
        for line in ext.read_text(errors="replace").splitlines():
            pm = re.match(r'port\s+"([^"]+)"', line)
            if pm and pm.group(1) not in ext_ports:
                ext_ports.append(pm.group(1))
            dm = re.match(r"device\s+msubckt\s+(\S+)", line)
            if dm:
                ext_model = dm.group(1)
    counts = {"R": 0, "C": 0, "X": 0}
    raw_lines: list[str] = []
    raw_text = ""
    if raw.is_file():
        raw_text = raw.read_text(errors="replace")
        for line in raw_text.splitlines():
            s = line.strip()
            if not s or s.startswith("*") or s.startswith("."):
                continue
            if s[0] in counts:
                counts[s[0]] += 1
            if s.startswith("X"):
                raw_lines.append(s)
    log = Path(rep["log"])
    log_text = log.read_text(errors="replace") if log.is_file() else rep["stdout_tail"]
    rep.update(
        {
            "script": str(script),
            "magic_steps": ["extract", "extresist all", "ext2spice cthresh 0",
                            "ext2spice extresist on", "ext2spice -o"],
            "raw": str(raw),
            "ext": str(ext),
            "ext_ports": ext_ports,
            "ext_model": ext_model,
            "counts": counts,
            "device_lines": raw_lines,
            "ok": bool(
                rep["returncode"] == 0
                and raw.is_file()
                and "GF180_PEX_DONE" in log_text
                and len(raw_lines) == 1
                and set(ext_ports) == {"S", "D", "B", "G"}
            ),
        }
    )
    if raw.is_file():
        rep["raw_sha256"] = _sha256(raw)
    return rep


def _klayout_lvs(
    gds: Path,
    cell: str,
    schematic: Path,
    workdir: Path,
    env: dict[str, str],
    klayout: str,
    lvs_deck: Path,
    tag: str = "lvs",
) -> dict[str, Any]:
    report = workdir / f"{tag}.lyrdb"
    netlist = workdir / f"{tag}.layout.spice"
    rep = _run(
        [
            klayout,
            "-b",
            "-r",
            str(lvs_deck),
            f"-rd",
            f"input={gds}",
            "-rd",
            f"topcell={cell}",
            "-rd",
            f"schematic={schematic}",
            "-rd",
            f"report={report}",
            "-rd",
            f"target_netlist={netlist}",
            "-rd",
            "schematic_translate=false",
            "-rd",
            "run_mode=flat",
            "-rd",
            "top_lvl_pins=true",
            "-rd",
            "check_ports=false",
        ],
        cwd=workdir,
        env=env,
        log_path=workdir / f"{tag}.log",
        timeout=1200,
    )
    log = Path(rep["log"])
    text = log.read_text(errors="replace") if log.is_file() else rep["stdout_tail"]
    rep["match"] = bool(
        rep["returncode"] == 0
        and ("Netlists match." in text or "Netlists match" in text)
    )
    rep["report"] = str(report)
    rep["target_netlist"] = str(netlist)
    rep["target_netlist_exists"] = netlist.is_file()
    if netlist.is_file():
        rep["target_netlist_sha256"] = _sha256(netlist)
        rep["target_netlist_text"] = netlist.read_text(errors="replace")
    return rep


def _netgen_lvs(
    layout: Path,
    schematic: Path,
    cell: str,
    workdir: Path,
    env: dict[str, str],
    netgen: str,
    setup: Path,
    tag: str = "netgen",
) -> dict[str, Any]:
    report = workdir / f"{tag}.out"
    rep = _run(
        [
            netgen,
            "-batch",
            "lvs",
            f"{layout} {cell}",
            f"{schematic} {cell}",
            str(setup),
            str(report),
        ],
        cwd=workdir,
        env=env,
        log_path=workdir / f"{tag}.log",
        timeout=600,
    )
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
    rep["failed_pin_matching"] = "failed pin matching" in text.lower()
    rep["no_devices"] = "contains no devices" in text.lower()
    return rep


def _model_sim(
    dev: str,
    model: str,
    pex: dict[str, Any],
    workdir: Path,
    env: dict[str, str],
    ngspice: str,
    design: Path,
    models: Path,
) -> dict[str, Any]:
    raw_path = Path(pex["raw"]) if pex.get("raw") else None
    element_lines = []
    if raw_path is not None and raw_path.is_file():
        for line in raw_path.read_text(errors="replace").splitlines():
            s = line.strip()
            if s and s[0] in "XRC" and not s.startswith("*"):
                element_lines.append(s)
    if not any(s.startswith("X") for s in element_lines):
        return {"ok": False, "note": "no extracted X device line"}
    sim = workdir / "sim.spice"
    if dev == "nfet":
        sources = (
            "VDD d 0 3.3\n"
            "VG g 0 1.65\n"
            "VS s 0 0\n"
            "VB b 0 0\n"
        )
        prints = "print v(d) v(g) v(s) v(b) i(VDD)"
    else:
        sources = (
            "VDD s 0 3.3\n"
            "VG g 0 1.65\n"
            "VB b 0 3.3\n"
            "VDS d 0 0.1\n"
        )
        prints = "print v(d) v(g) v(s) v(b) i(VDD) i(VDS)"
    has_vsubs = any("VSUBS" in s for s in element_lines)
    subckt_ports = "d g s b vsubs" if has_vsubs else "d g s b"
    xdut_nodes = "d g s b vsubs" if has_vsubs else "d g s b"
    if has_vsubs:
        # The pfet PEX netlist has parasitic caps to the p-substrate node
        # VSUBS.  Expose it as a testbench port and tie it to ground; leaving
        # it floating inside the subckt makes the DC matrix singular.
        sources += "VSUB vsubs 0 0\n"
    sim.write_text(
        f"* gf180mcuD {model} Magic PEX DC operating point\n"
        f'.include "{design}"\n'
        f'.lib "{models}" typical\n'
        ".option scale=5n\n"
        f".subckt {dev}_pex {subckt_ports}\n"
        + "\n".join(element_lines)
        + "\n.ends\n"
        f"{sources}"
        f"XDUT {xdut_nodes} {dev}_pex\n"
        ".op\n"
        ".control\n"
        "run\n"
        f"{prints}\n"
        ".endc\n"
        ".end\n"
    )
    rep = _run(
        [ngspice, "-n", "-b", str(sim)],
        cwd=workdir,
        env=env,
        log_path=workdir / "sim.log",
        timeout=300,
    )
    log_path = Path(rep["log"])
    text = log_path.read_text(errors="replace") if log_path.is_file() else rep["stdout_tail"]
    values: dict[str, float] = {}
    for line in text.splitlines():
        m = re.match(r"\s*([vi]\([^)]+\))\s*=\s*([-+0-9.eE]+)\s*$", line)
        if m:
            values[m.group(1).lower()] = float(m.group(2))
    rep["values"] = values
    lower = text.lower()
    rep["convergence_warnings"] = any(
        s in lower
        for s in (
            "singular matrix",
            "no convergence",
            "source stepping failed",
            "gmin stepping failed",
        )
    )
    rep["sim_ok"] = bool(
        rep["returncode"] == 0
        and not rep["convergence_warnings"]
        and all(k in values for k in ("v(d)", "v(g)", "v(s)", "v(b)"))
        and (
            abs(values.get("i(vdd)", 0.0)) > 1e-9
            or abs(values.get("i(vds)", 0.0)) > 1e-9
        )
    )
    rep["sim_spice"] = str(sim)
    return rep


def _negative_no_b_label(
    good_gds: Path,
    cell: str,
    schematic: Path,
    workdir: Path,
    env: dict[str, str],
    klayout: str,
    netgen: str,
    lvs_deck: Path,
    setup: Path,
) -> dict[str, Any]:
    """Remove the B label and confirm a pin-sensitive LVS fails."""
    nob = workdir / "negative_nob.gds"
    script = workdir / "negative_nob.py"
    script.write_text(
        f'''import pya
ly = pya.Layout()
ly.read({str(good_gds)!r})
c = ly.top_cell()
shapes = c.shapes(ly.layer(*{LY_METAL1_LABEL!r}))
removed = 0
for s in list(shapes.each()):
    if s.is_text() and s.text.string == "B":
        shapes.erase(s)
        removed += 1
if removed != 1:
    raise RuntimeError("expected exactly one B label, removed %d" % removed)
ly.write({str(nob)!r})
print("GF180_NEGATIVE_OK")
''',
        encoding="utf-8",
    )
    gen = _run(
        [klayout, "-zz", "-r", str(script)],
        cwd=workdir,
        env=env,
        log_path=workdir / "negative_nob_gen.log",
    )
    gen["ok"] = (
        gen["returncode"] == 0
        and nob.is_file()
        and "GF180_NEGATIVE_OK" in gen["stdout_tail"]
    )
    if not gen["ok"]:
        return {"generation": gen, "failed_as_expected": False}
    klvs = _klayout_lvs(
        nob,
        cell,
        schematic,
        workdir,
        env,
        klayout,
        lvs_deck,
        tag="negative_nob_klayout",
    )
    nglvs = _netgen_lvs(
        Path(klvs["target_netlist"]),
        schematic,
        cell,
        workdir,
        env,
        netgen,
        setup,
        tag="negative_nob_netgen",
    )
    # KLayout's check_ports=false mode is deliberately pin-insensitive; the
    # pin-sensitive negative control is netgen.  Record both honestly.
    return {
        "generation": gen,
        "klayout": klvs,
        "netgen": nglvs,
        "failed_as_expected": bool(not nglvs["match"]),
        "note": (
            "B label removed. KLayout check_ports=false remains pin-insensitive; "
            "netgen fails pin matching. The PDK deck's check_ports=true path "
            "raises an undefined-variable error in this PDK release."
        ),
    }


def _model_binding(models: Path, model: str) -> dict[str, Any]:
    text = models.read_text(errors="replace") if models.is_file() else ""
    pat = re.compile(rf"^\s*\.subckt\s+{re.escape(model)}\s", re.I | re.M)
    return {
        "models_file": str(models),
        "exists": models.is_file(),
        "subckt": model,
        "subckt_found": bool(pat.search(text)),
    }


def verify_device(
    dev: str,
    spec: dict[str, str],
    *,
    paths: dict[str, Path],
    env: dict[str, str],
    workdir: Path,
    tools: dict[str, str],
) -> dict[str, Any]:
    cell = spec["cell"]
    model = spec["model"]
    ddir = workdir / dev
    ddir.mkdir(parents=True, exist_ok=True)
    gds = ddir / f"{cell}.gds"
    schematic = ddir / f"{cell}.spice"

    out: dict[str, Any] = {
        "device": cell,
        "model": model,
        "generated_utc": _utc(),
        "pcell_params": PCell_PARAMS,
    }
    gen = _klayout_generate(dev, gds, ddir, env, tools["klayout"])
    out["layout"] = gen
    _write_schematic(dev, model, schematic)
    out["schematic"] = {
        "path": str(schematic),
        "sha256": _sha256(schematic),
        "text": schematic.read_text(),
    }

    if not gen["ok"]:
        out["status"] = "FAIL"
        out["reason"] = "KLayout PCell generation failed"
        return out

    drc = _magic_drc(
        gds, cell, ddir, env, tools["magic"], paths["magic_rc"]
    )
    out["drc"] = drc

    pex = _magic_pex(
        gds, cell, ddir, env, tools["magic"], paths["magic_rc"]
    )
    out["pex"] = pex
    out["model_binding"] = _model_binding(paths["ngspice_models"], model)

    lvs_k = _klayout_lvs(
        gds,
        cell,
        schematic,
        ddir,
        env,
        tools["klayout"],
        paths["klayout_lvs"],
        tag="lvs_klayout",
    )
    out["lvs_klayout"] = lvs_k

    netgen_layout = Path(lvs_k["target_netlist"])
    if netgen_layout.is_file():
        lvs_n = _netgen_lvs(
            netgen_layout,
            schematic,
            cell,
            ddir,
            env,
            tools["netgen"],
            paths["netgen_setup"],
            tag="lvs_netgen",
        )
    else:
        lvs_n = {"match": False, "note": "KLayout did not produce a target netlist"}
    out["lvs_netgen"] = lvs_n

    sim = _model_sim(
        dev,
        model,
        pex,
        ddir,
        env,
        tools["ngspice"],
        paths["ngspice_design"],
        paths["ngspice_models"],
    )
    out["model_sim"] = sim

    neg = _negative_no_b_label(
        gds,
        cell,
        schematic,
        ddir,
        env,
        tools["klayout"],
        tools["netgen"],
        paths["klayout_lvs"],
        paths["netgen_setup"],
    )
    out["negative_control"] = neg

    checks = {
        "layout_generated": gen["ok"],
        "drc_clean": drc["clean"],
        "pex_extracted": pex["ok"],
        "pex_has_one_device": pex["counts"]["X"] == 1,
        "pex_device_model_bound": pex["ext_model"] == model,
        "klayout_lvs_match": lvs_k["match"],
        "netgen_lvs_match": lvs_n["match"],
        "model_sim_ok": sim["sim_ok"],
        "negative_control_fails": neg["failed_as_expected"],
    }
    out["checks"] = checks
    out["status"] = "PASS" if all(checks.values()) else "FAIL"
    if out["status"] != "PASS":
        out["reason"] = ", ".join(k for k, v in checks.items() if not v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gf-root", default=None, help="gf180mcu CIEL version root")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="JSON evidence output")
    ap.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    ap.add_argument(
        "--devices",
        default="nfet,pfet",
        help="comma-separated subset of nfet,pfet",
    )
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
            "ngspice": _tool("ngspice"),
        }
        for key in ("magic_rc", "netgen_setup", "klayout_lvs",
                    "ngspice_design", "ngspice_models"):
            if not paths[key].is_file():
                raise FileNotFoundError(f"missing PDK file: {paths[key]}")
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2

    devs = [d.strip() for d in args.devices.split(",") if d.strip()]
    unknown = [d for d in devs if d not in DEVICES]
    if unknown:
        print(f"error: unknown devices: {', '.join(unknown)}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "kind": "gf180-device-closure",
        "generated_utc": _utc(),
        "environment": {
            "gf_root": str(gf_root),
            "gf_pdk": str(paths["pdk"]),
            "magic_rc": str(paths["magic_rc"]),
            "klayout_lvs": str(paths["klayout_lvs"]),
            "netgen_setup": str(paths["netgen_setup"]),
            "ngspice_design": str(paths["ngspice_design"]),
            "ngspice_models": str(paths["ngspice_models"]),
            "tools": tools,
            "versions": {},
        },
        "scope": (
            "Single bulk-tied gf180mcuD 3.3V MOS at W=1.0um L=0.28um, "
            "nf=1. Probe-only gate contact/pad and MET1 labels. Not a "
            "foundry signoff; not arbitrary W/L, guard-ring, multi-finger "
            "or full-cell coverage."
        ),
        "devices": {},
        "summary": {},
    }
    versions = report["environment"]["versions"]
    for name, cmd in (
        ("klayout", [tools["klayout"], "-v"]),
        ("netgen", [tools["netgen"], "-batch", "version"]),
        ("ngspice", [tools["ngspice"], "-v"]),
    ):
        try:
            p = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
            out = (p.stdout or p.stderr).strip()
            if name == "ngspice":
                m = re.search(r"ngspice-([0-9.]+)", out, re.I)
                versions[name] = f"ngspice-{m.group(1)}" if m else out.splitlines()[0]
            else:
                versions[name] = out.splitlines()[0] if out else "unknown"
        except Exception:  # noqa: BLE001
            versions[name] = "unknown"
    try:
        vscript = workdir / "version.tcl"
        vscript.write_text('puts "MAGIC_VERSION_PROBE"\nquit\n')
        p = subprocess.run(
            [tools["magic"], "-dnull", "-noconsole", "-rcfile",
             str(paths["magic_rc"]), str(vscript)],
            text=True, capture_output=True, timeout=60, env=env,
        )
        out = p.stdout + p.stderr
        m = re.search(r"Magic\s+([0-9][^\n]*)", out)
        versions["magic"] = f"Magic {m.group(1)}" if m else "unknown"
    except Exception:  # noqa: BLE001
        versions["magic"] = "unknown"

    for dev in devs:
        print(f"[gf180] verifying {dev} ...", flush=True)
        try:
            rep = verify_device(
                dev,
                DEVICES[dev],
                paths=paths,
                env=env,
                workdir=workdir,
                tools=tools,
            )
        except Exception as exc:  # noqa: BLE001
            rep = {
                "device": DEVICES[dev]["cell"],
                "status": "FAIL",
                "reason": f"exception: {exc}",
                "generated_utc": _utc(),
            }
        report["devices"][dev] = rep
        print(f"[gf180] {dev}: {rep.get('status')} {rep.get('reason', '')}", flush=True)

    statuses = {k: v.get("status") for k, v in report["devices"].items()}
    report["summary"] = {
        "statuses": statuses,
        "overall": (
            "PASS"
            if statuses and all(s == "PASS" for s in statuses.values())
            else "FAIL"
        ),
        "all_checks": {
            dev: rep.get("checks", {}) for dev, rep in report["devices"].items()
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out_path}")
    return 0 if report["summary"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

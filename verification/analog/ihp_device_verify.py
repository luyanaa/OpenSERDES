#!/usr/bin/env python3
"""Bounded IHP SG13G2 device-level closure lane.

This script verifies the installed IHP SG13G2 PDK low-voltage devices
(``sg13_lv_nmos`` / ``sg13_lv_pmos``) end-to-end with real tools:

  * KLayout + the PDK ``SG13_dev`` PCells  -> GDS + labels
  * Magic (ihp-sg13g2 deck)                -> DRC, extraction, extresist PEX
  * Netgen (ihp-sg13g2 setup)              -> LVS against the PDK subckt
  * ngspice + PDK OSDI models              -> model DC operating point

It does not touch sky130, gf180, the Makefile, M3 files or any ``pya_pcell*``
module.  The only side effect outside ``build/verify/ihp_device`` is the
optional ``libs.tech/ngspice/install.py`` run, which is explicitly requested
with ``--install-osdi`` and recorded in the JSON evidence.

Example::

    IHP_ROOT=/home/yanlu/.ciel/ciel/ihp-sg13/versions/<ver> \
        python3 verification/analog/ihp_device_verify.py --install-osdi

Exit status is 0 only when every requested check is clean (DRC 0, LVS match,
negative control rejected, PEX command clean, and model DC if OSDI is used).
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

DEFAULT_IHP_ROOT = Path(
    os.environ.get(
        "IHP_ROOT",
        "/home/yanlu/.ciel/ciel/ihp-sg13/versions/"
        "89d42ee6d2fd9b50b86a1aada6fee6673c3e6008",
    )
)
DEVICES = {
    "nmos": {
        "pcell": "nmos",
        "subckt": "sg13_lv_nmos",
        # Extracted port names from Magic for the labelled cell.
        "ports": ["S", "D", "G", "sub"],
        "instance": "D G S sub sg13_lv_nmos",
        "negative_drop": "S",
        "model_body": "0",
        "model_drain": "d",
        "model_source": "0",
    },
    "pmos": {
        "pcell": "pmos",
        "subckt": "sg13_lv_pmos",
        "ports": ["S", "D", "G", "b"],
        "instance": "D G S b sg13_lv_pmos",
        "negative_drop": "b",
        "model_body": "1.2",
        "model_drain": "0",
        "model_source": "s",
    },
}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_version(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        for line in (p.stdout + p.stderr).splitlines():
            line = line.strip().lstrip("*").strip()
            if line:
                return line
        return "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def run(cmd: list[str], cwd: Path, env: dict[str, str], log: Path,
        timeout: int = 900) -> dict[str, Any]:
    """Run a command, persist its log and return structured evidence."""
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, text=True,
                              capture_output=True, timeout=timeout)
        text = proc.stdout + proc.stderr
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") + (exc.stderr or "") + f"\nTIMEOUT after {timeout}s\n"
        if isinstance(text, bytes):
            text = text.decode(errors="replace")
        rc = 124
    log.write_text(text)
    return {
        "command": cmd,
        "cwd": str(cwd),
        "returncode": rc,
        "log": str(log),
        "stdout_tail": text[-3000:],
    }


# ---------------------------------------------------------------------------
# KLayout: build the PDK PCell layout, labels and a small source strap
# ---------------------------------------------------------------------------

KLAYOUT_TEMPLATE = r'''import json
import sys

IHP_PDK = {pdk!r}
sys.path.insert(0, IHP_PDK + "/libs.tech/klayout/python")
sys.path.insert(0, IHP_PDK + "/libs.tech/klayout/python/pycell4klayout-api/source/python")
import sg13g2_pycell_lib  # noqa: F401  (registers SG13_dev)
import pya

dev = {dev!r}
out = {out!r}
drop_label = {drop!r}
add_guard_ring = {guard!r}

ly = pya.Layout()
ly.dbu = 0.001
ly.technology_name = "sg13g2"
params = {{"l": "0.13u", "w": "1u", "ng": 1}}
if add_guard_ring:
    # The bare pmos PCell leaves the n-well body as an internal node.  The PDK
    # guard ring gives it a contacted n-well tie so it can be a real port.
    params["guardRingType"] = "nwell"
    params["guardRingDistance"] = "1u"

cell = ly.create_cell(dev, "SG13_dev", params)
if cell is None or cell.bbox().empty():
    raise SystemExit("PCell creation failed for %s %r" % (dev, params))

m1 = ly.layer(8, 0)      # Metal1.drawing
m1p = ly.layer(8, 2)     # Metal1.pin
gpd = ly.layer(5, 0)     # GatPoly.drawing
gp = ly.layer(5, 2)      # GatPoly.pin


def uniq_boxes(layer):
    out_boxes = []
    for shape in cell.shapes(layer).each():
        if shape.is_box() and not any(shape.box == b for b in out_boxes):
            out_boxes.append(shape.box)
    return out_boxes


all_m1 = uniq_boxes(m1)
inner = [b for b in all_m1
         if b.bottom <= 0 and b.top >= 900 and b.width() < 400]
if add_guard_ring:
    inner = [b for b in inner if b.left >= 0 and b.right <= 1000]
inner.sort(key=lambda b: b.center().x)
if len(inner) < 2:
    raise SystemExit("could not identify source/drain Metal1: %r" %
                     [b.to_s() for b in all_m1])
src, drn = inner[0], inner[-1]
gate = uniq_boxes(gpd)[0]

labels = []
def label(layer, name, x, y):
    cell.shapes(layer).insert(pya.Text(name, pya.Trans(pya.Vector(x, y))))
    labels.append({{"text": name, "layer": str(layer), "x": x, "y": y}})

if drop_label != "S":
    label(m1p, "S", src.center().x, src.center().y)
if drop_label != "D":
    label(m1p, "D", drn.center().x, drn.center().y)
if drop_label != "G":
    label(gp, "G", gate.center().x, gate.center().y)

# PMOS body: label the contacted n-well guard-ring metal.  NMOS body is the
# PDK global substrate node ("sub") and needs no label.
if add_guard_ring and drop_label != "b":
    ring = [b for b in all_m1 if b not in inner]
    if not ring:
        raise SystemExit("guard ring requested but no ring metal found")
    top_seg = max(ring, key=lambda b: b.top)
    label(m1p, "b", top_seg.center().x, top_seg.center().y)

# Short Metal1 source strap.  It stays on the source net (no connectivity
# change) but gives extresist a wire segment and gives PEX an extra node.
strap = None
if not drop_label == "S_STRAP_DISABLED":
    y0, y1 = src.bottom - 400, src.bottom
    box = pya.Box(src.left, y1, src.right, y0)
    cell.shapes(m1).insert(box)
    cell.shapes(m1p).insert(box)
    strap = box.to_s()
    if drop_label != "S":
        label(m1p, "S", src.center().x, y0 + 100)

top = ly.create_cell("TOP")
top.insert(pya.DCellInstArray(cell, pya.DTrans()))
ly.write(out)
print(json.dumps({{
    "pcell": dev,
    "params": params,
    "bbox": cell.bbox().to_s(),
    "metal1_boxes": [b.to_s() for b in all_m1],
    "source": src.to_s(),
    "drain": drn.to_s(),
    "gate": gate.to_s(),
    "strap": strap,
    "labels": labels,
    "gds": out,
}}))
'''


def klayout_make(pdk: Path, dev: str, gds: Path, script: Path, log: Path,
                 work: Path, env: dict[str, str], drop: str | None = None,
                 guard_ring: bool = False) -> dict[str, Any]:
    script.write_text(KLAYOUT_TEMPLATE.format(
        pdk=str(pdk), dev=dev, out=str(gds), drop=drop or "", guard=guard_ring,
    ))
    res = run([shutil.which("klayout") or "klayout", "-zz", "-r", str(script)],
              work, env, log, timeout=300)
    res["layout"] = None
    if res["returncode"] == 0:
        for line in reversed(res["stdout_tail"].splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    res["layout"] = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    return res


# ---------------------------------------------------------------------------
# Magic: DRC + extraction + extresist
# ---------------------------------------------------------------------------

def trim_to_first_subckt(path: Path, out: Path) -> Path | None:
    """Keep only the first .subckt block (the device), dropping the TOP wrapper.

    Magic emits the TOP instance as a naked top-level X line for some devices;
    Netgen then warns about self-instantiation.  Comparing the device subckt
    alone is the intended device-level LVS.
    """
    lines = path.read_text().splitlines()
    kept: list[str] = []
    inside = False
    for line in lines:
        if re.match(r"\s*\.subckt\s+\S+", line, re.I):
            inside = True
        if inside:
            kept.append(line)
        if inside and re.match(r"\s*\.ends\b", line, re.I):
            break
    if not kept:
        return None
    out.write_text("\n".join(kept) + "\n")
    return out


def magic_lvs(pdk: Path, gds: Path, work: Path, env: dict[str, str],
              prefix: str, drc: bool = True) -> dict[str, Any]:
    """Magic DRC + plain (no extresist) extraction, in a fresh process.

    The TOP cell is extracted, not the device cell.  Magic then promotes the
    substrate/body net to a real subcircuit port on the device subcell, which
    is what the PDK LVS schematic expects (e.g. ``nmos S D G sub``).
    """
    rcfile = pdk / "libs.tech/magic/ihp-sg13g2.magicrc"
    lvs_spice = work / f"{prefix}_lvs.spice"
    script = work / f"{prefix}_magic_lvs.tcl"
    lines = [
        f"gds read {{{gds.with_suffix('')}}}",
        "load TOP -dereference",
        "select top cell",
    ]
    if drc:
        lines += ["drc on", "drc check", "drc count"]
    lines += [
        "extract",
        "ext2spice lvs",
        "ext2spice extresist off",
        f"ext2spice -o {{{lvs_spice}}}",
        'puts "IHP_MAGIC_DONE"',
        "quit",
        "",
    ]
    script.write_text("\n".join(lines))
    log = work / f"{prefix}_magic_lvs.log"
    res = run([shutil.which("magic") or "magic", "-dnull", "-noconsole",
               "-rcfile", str(rcfile), str(script)], work, env, log, timeout=900)
    text = log.read_text()
    m = re.search(r"Total DRC errors found:\s*(\d+)", text, re.I)
    res["drc_count"] = int(m.group(1)) if m else None
    res["magic_done"] = "IHP_MAGIC_DONE" in text
    res["lvs_netlist_full"] = str(lvs_spice) if lvs_spice.is_file() else None
    trimmed = None
    if lvs_spice.is_file():
        trimmed = trim_to_first_subckt(lvs_spice, work / f"{prefix}_device_lvs.spice")
    res["lvs_netlist"] = str(trimmed) if trimmed else None
    res["lvs_netlist_parsed"] = parse_spice(trimmed) if trimmed else {}
    return res


def magic_pex(pdk: Path, gds: Path, work: Path, env: dict[str, str],
              prefix: str) -> dict[str, Any]:
    """Magic extresist PEX in a fresh process (no state from the LVS run)."""
    rcfile = pdk / "libs.tech/magic/ihp-sg13g2.magicrc"
    pex_spice = work / f"{prefix}_pex.spice"
    script = work / f"{prefix}_magic_pex.tcl"
    script.write_text("\n".join([
        f"gds read {{{gds.with_suffix('')}}}",
        "load TOP -dereference",
        "select top cell",
        "extract",
        "extresist all",
        "ext2spice extresist on",
        "ext2spice rthresh 0",
        "ext2spice cthresh 0",
        f"ext2spice -o {{{pex_spice}}}",
        'puts "IHP_MAGIC_DONE"',
        "quit",
        "",
    ]))
    log = work / f"{prefix}_magic_pex.log"
    res = run([shutil.which("magic") or "magic", "-dnull", "-noconsole",
               "-rcfile", str(rcfile), str(script)], work, env, log, timeout=900)
    text = log.read_text()
    res["magic_done"] = "IHP_MAGIC_DONE" in text
    res["pex_netlist"] = str(pex_spice) if pex_spice.is_file() else None
    res["pex"] = parse_spice(pex_spice) if pex_spice.is_file() else {}
    return res


def parse_spice(path: Path) -> dict[str, Any]:
    text = path.read_text() if path.is_file() else ""
    subckt = None
    ports: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*\.subckt\s+(\S+)\s*(.*)$", line, re.I)
        if m:
            subckt = m.group(1)
            ports = m.group(2).split()
            break
    resistors = len(re.findall(r"^\s*R\S+\s", text, re.M | re.I))
    capacitors = len(re.findall(r"^\s*C\S+\s", text, re.M | re.I))
    floating = len(re.findall(r"\*\*FLOATING", text))
    devices = [ln.strip() for ln in text.splitlines()
               if re.match(r"^\s*[XM]\S+\s", ln, re.I)]
    device_terminals: dict[str, list[str]] = {}
    for ln in devices:
        toks = ln.split()
        cls = next((t for t in toks if t.startswith("sg13_")), None)
        if cls and len(toks) > 1:
            # First tokens after the instance name are the terminals.
            device_terminals[cls] = toks[1:5]
    return {
        "format": "subckt" if subckt else "flat",
        "subckt": subckt,
        "ports": ports,
        "resistors": resistors,
        "capacitors": capacitors,
        "floating_caps": floating,
        "device_lines": devices,
        "device_terminals": device_terminals,
        "device_classes": sorted(set(
            "sg13_lv_nmos" if "sg13_lv_nmos" in ln else "sg13_lv_pmos"
            for ln in devices if "sg13_lv_" in ln
        )),
        "bytes": len(text),
    }


# ---------------------------------------------------------------------------
# Netgen LVS
# ---------------------------------------------------------------------------

def write_schematic(path: Path, dev: str, info: dict[str, Any]) -> None:
    ports = " ".join(info["ports"])
    path.write_text(
        f".subckt {dev} {ports}\n"
        f"X0 {info['instance']} w=1u l=0.13u\n"
        f".ends\n"
    )


def netgen_lvs(pdk: Path, layout_spice: Path, schematic: Path, cell: str,
               work: Path, env: dict[str, str], prefix: str) -> dict[str, Any]:
    setup = pdk / "libs.tech/netgen/ihp-sg13g2_setup.tcl"
    script = work / f"{prefix}_netgen_lvs.tcl"
    # Netgen 1.5.3xx no longer accepts the old "lvs file1 cell1 file2 cell2"
    # CLI; its Tcl ``lvs`` takes {file cell} pairs.  Pass explicit lists so
    # files and cells cannot be re-parsed or confused with each other.
    script.write_text(
        f"set setup {{{setup}}}\n"
        f"netgen::lvs [list {{{layout_spice}}} {{{cell}}}] "
        f"[list {{{schematic}}} {{{cell}}}] $setup\n"
        f"quit\n"
    )
    log = work / f"{prefix}_netgen.log"
    res = run([shutil.which("netgen") or "netgen", "-batch", "source", str(script)],
              work, env, log, timeout=300)
    text = log.read_text()
    res["match"] = "Circuits match uniquely." in text
    res["pin_match"] = "Cell pin lists are equivalent." in text
    res["failed_pin_matching"] = "Top level cell failed pin matching." in text
    return res


# ---------------------------------------------------------------------------
# Model DC (OSDI)
# ---------------------------------------------------------------------------

def ensure_osdi(pdk: Path, do_install: bool, work: Path,
                env: dict[str, str]) -> dict[str, Any]:
    osdi = pdk / "libs.tech/ngspice/osdi"
    required = ["psp103.osdi", "psp103_nqs.osdi", "r3_cmc.osdi", "mosvar.osdi"]
    info: dict[str, Any] = {
        "dir": str(osdi),
        "files": sorted(p.name for p in osdi.glob("*.osdi")) if osdi.is_dir() else [],
        "spiceinit": str(Path.home() / ".spiceinit"),
        "spiceinit_target": None,
        "openvaf": shutil.which("openvaf") or shutil.which("openvaf-r")
        or ("/opt/openvaf" if Path("/opt/openvaf").is_file() else None),
        "installed": False,
        "install_attempted": False,
        "install_log": None,
        "install_returncode": None,
        "install_note": None,
    }
    try:
        info["spiceinit_target"] = str((Path.home() / ".spiceinit").resolve())
    except OSError:
        pass
    if all((osdi / name).is_file() for name in required):
        info["installed"] = True
        info["install_note"] = (
            "OSDI models already present; installer not run this invocation. "
            "Use --install-osdi on a PDK without them."
        )
        return info
    if not do_install:
        return info

    info["install_attempted"] = True
    bin_dir = Path("/tmp/opencode/bin")
    bin_dir.mkdir(parents=True, exist_ok=True)
    openvaf = shutil.which("openvaf") or shutil.which("openvaf-r")
    link = bin_dir / "openvaf"
    if not openvaf and Path("/opt/openvaf").is_file():
        openvaf = "/opt/openvaf"
    if openvaf:
        try:
            link.unlink(missing_ok=True)
            link.symlink_to(openvaf)
        except OSError:
            pass
    install_env = dict(env)
    install_env["PATH"] = f"{bin_dir}:{install_env.get('PATH', '')}"
    install_env["PDK_ROOT"] = str(pdk.parent)
    log = work / "osdi_install.log"
    res = run([sys.executable, str(pdk / "libs.tech/ngspice/install.py")],
              work, install_env, log, timeout=900)
    info["install_log"] = str(log)
    info["install_returncode"] = res["returncode"]
    info["files"] = sorted(p.name for p in osdi.glob("*.osdi")) if osdi.is_dir() else []
    info["installed"] = all((osdi / name).is_file() for name in required)
    info["install_note"] = (
        "Ran PDK ngspice/install.py with OpenVAF to generate OSDI models and "
        "~/.spiceinit; see install_log."
    )
    try:
        info["spiceinit_target"] = str((Path.home() / ".spiceinit").resolve())
    except OSError:
        pass
    return info


def model_dc(pdk: Path, dev: str, info: dict[str, Any], work: Path,
             env: dict[str, str]) -> dict[str, Any]:
    lib = pdk / "libs.tech/ngspice/models/cornerMOSlv.lib"
    net = work / f"{dev}_model.sp"
    if dev == "nmos":
        body = f"VDD d 0 1.2\nVG g 0 0.6\nVB b 0 0\n"
        inst = "X1 d g 0 b sg13_lv_nmos w=1u l=0.13u"
        probe = "print v(d) i(vdd)"
    else:
        body = f"VDD s 0 1.2\nVG g 0 0.6\nVB b 0 1.2\n"
        inst = "X1 0 g s b sg13_lv_pmos w=1u l=0.13u"
        probe = "print v(s) i(vdd)"
    net.write_text(
        f".lib {lib} mos_tt\n{body}{inst}\n"
        f".control\nop\n{probe}\n.endc\n.end\n"
    )
    log = work / f"{dev}_model.log"
    res = run([shutil.which("ngspice") or "ngspice", "-b", str(net)],
              work, env, log, timeout=300)
    text = log.read_text()
    vals: dict[str, float] = {}
    for name, val in re.findall(r"^(v\([^)]+\)|i\([^)]+\))\s*=\s*([-+0-9.eE]+)",
                                text, re.M):
        try:
            vals[name] = float(val)
        except ValueError:
            pass
    res["operating_point"] = vals
    res["osdi_warnings"] = [
        ln.strip() for ln in text.splitlines()
        if "Error opening osdi lib" in ln or "couldn't be loaded" in ln
    ]
    current = next((v for k, v in vals.items() if k.startswith("i(")), None)
    res["current_a"] = current
    res["finite"] = current is not None and abs(current) > 1e-12
    res["status"] = "pass" if (res["returncode"] == 0 and res["finite"]) else "blocked"
    return res


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def resolve_pdk(ihp_root: Path) -> Path:
    pdk = ihp_root if ihp_root.name == "ihp-sg13g2" else ihp_root / "ihp-sg13g2"
    if not (pdk / "libs.tech/magic/ihp-sg13g2.magicrc").is_file():
        raise FileNotFoundError(f"no IHP SG13G2 PDK under {pdk}")
    return pdk


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ihp-root", default=str(DEFAULT_IHP_ROOT),
                    help="IHP PDK version root (parent of ihp-sg13g2)")
    ap.add_argument("--device", choices=("nmos", "pmos", "both"), default="both")
    ap.add_argument("--out", default="build/verify/ihp_device")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--install-osdi", action="store_true",
                    help="run the PDK ngspice/install.py when OSDI models are missing")
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--skip-negative", action="store_true")
    args = ap.parse_args()

    ihp_root = Path(args.ihp_root).expanduser().resolve()
    pdk = resolve_pdk(ihp_root)
    out_dir = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    work = Path(args.workdir).resolve() if args.workdir else out_dir / "work"
    logs = out_dir / "logs"
    work.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["IHP_ROOT"] = str(ihp_root)
    env["PDK_ROOT"] = str(ihp_root)
    env["PDK"] = "ihp-sg13g2"
    env["KLAYOUT_PATH"] = str(pdk / "libs.tech/klayout")

    selected = ["nmos", "pmos"] if args.device == "both" else [args.device]
    report: dict[str, Any] = {
        "kind": "ihp-sg13g2-device-closure",
        "generated_utc": _now(),
        "ihp_root": str(ihp_root),
        "pdk": str(pdk),
        "out_dir": str(out_dir),
        "workdir": str(work),
        "tools": {
            "klayout": _tool_version(["klayout", "-v"]),
            "magic": _tool_version(["magic", "-dnull", "-noconsole", "-rcfile",
                                    str(pdk / "libs.tech/magic/ihp-sg13g2.magicrc"),
                                    "/dev/null"]),
            "netgen": _tool_version(["netgen", "-batch", "version"]),
            "ngspice": _tool_version(["ngspice", "--version"]),
        },
        "paths": {
            "magic_rc": str(pdk / "libs.tech/magic/ihp-sg13g2.magicrc"),
            "magic_extract": str(pdk / "libs.tech/magic/ihp-sg13g2-extract.tech"),
            "netgen_setup": str(pdk / "libs.tech/netgen/ihp-sg13g2_setup.tcl"),
            "model_lib": str(pdk / "libs.tech/ngspice/models/cornerMOSlv.lib"),
        },
        "devices": {},
        "model": {"status": "skipped"},
        "checks": {},
        "notes": [],
    }

    if not args.skip_model:
        osdi = ensure_osdi(pdk, args.install_osdi, work, env)
        report["model"]["osdi"] = osdi
        if not osdi["installed"]:
            report["model"]["status"] = "blocked"
            report["model"]["reason"] = (
                "PDK OSDI models missing (rerun with --install-osdi to compile "
                "with OpenVAF); LVS/PEX results below remain valid."
            )

    for dev in selected:
        info = DEVICES[dev]
        prefix = dev
        gds = work / f"{dev}.gds"
        mk = klayout_make(pdk, info["pcell"], gds, work / f"{dev}_make.py",
                          logs / f"{dev}_klayout.log", work, env,
                          guard_ring=(dev == "pmos"))
        rec: dict[str, Any] = {
            "gds": str(gds),
            "gds_sha256": _sha(gds) if gds.is_file() else None,
            "layout": mk.get("layout"),
            "klayout": mk,
        }
        if mk["returncode"] != 0 or not gds.is_file():
            rec["status"] = "fail"
            rec["reason"] = "KLayout PCell generation failed"
            report["devices"][dev] = rec
            continue

        magic = magic_lvs(pdk, gds, work, env, prefix, drc=True)
        rec["drc"] = {
            "returncode": magic["returncode"],
            "count": magic["drc_count"],
            "clean": magic["returncode"] == 0 and magic["drc_count"] == 0,
            "command": magic["command"],
            "log": magic["log"],
        }
        pex = magic_pex(pdk, gds, work, env, prefix)
        rec["extraction"] = {
            "lvs_returncode": magic["returncode"],
            "pex_returncode": pex["returncode"],
            "lvs_netlist": magic["lvs_netlist"],
            "lvs_netlist_full": magic.get("lvs_netlist_full"),
            "pex_netlist": pex["pex_netlist"],
            "lvs_netlist_parsed": magic["lvs_netlist_parsed"],
            "pex": pex.get("pex", {}),
            "lvs_command": magic["command"],
            "pex_command": pex["command"],
            "lvs_log": magic["log"],
            "pex_log": pex["log"],
        }

        sch = work / f"{dev}_schematic.cir"
        write_schematic(sch, dev, info)
        lvs = netgen_lvs(pdk, Path(magic["lvs_netlist"]), sch, dev,
                         work, env, prefix)
        rec["lvs"] = {**lvs, "schematic": str(sch),
                      "extracted_ports": magic["lvs_netlist_parsed"].get("ports")}

        neg = None
        if not args.skip_negative:
            neg_gds = work / f"{dev}_negative.gds"
            neg_mk = klayout_make(pdk, info["pcell"], neg_gds,
                                  work / f"{dev}_negative_make.py",
                                  logs / f"{dev}_negative_klayout.log", work, env,
                                  drop=info["negative_drop"],
                                  guard_ring=(dev == "pmos"))
            if neg_mk["returncode"] == 0 and neg_gds.is_file():
                neg_magic = magic_lvs(pdk, neg_gds, work, env,
                                      f"{dev}_negative", drc=False)
                if neg_magic["lvs_netlist"]:
                    neg_lvs = netgen_lvs(pdk, Path(neg_magic["lvs_netlist"]), sch,
                                         dev, work, env, f"{dev}_negative")
                    neg = {
                        "removed_label": info["negative_drop"],
                        "expected": "LVS must NOT match without the port label",
                        "drc_netlist": neg_magic["lvs_netlist_parsed"],
                        "match": neg_lvs["match"],
                        "rejected": not neg_lvs["match"],
                        "command": neg_lvs["command"],
                        "log": neg_lvs["log"],
                    }
                else:
                    neg = {"removed_label": info["negative_drop"],
                           "error": "negative extraction produced no netlist"}
            else:
                neg = {"removed_label": info["negative_drop"],
                       "error": "negative layout generation failed"}
        rec["negative_control"] = neg

        if not args.skip_model:
            osdi_info = report["model"].get("osdi", {})
            if osdi_info.get("installed"):
                mdl = model_dc(pdk, dev, info, work, env)
                rec["model"] = mdl
            else:
                rec["model"] = {"status": "blocked", "reason": "OSDI missing"}

        # Provenance of the extracted device class.
        classes = magic["lvs_netlist_parsed"].get("device_classes", [])
        rec["device_class_ok"] = classes == [info["subckt"]]
        rec["status"] = (
            "pass" if (
                rec["drc"]["clean"]
                and lvs["match"]
                and (neg is None or neg.get("rejected"))
                and rec["device_class_ok"]
                and bool(pex.get("pex"))
                and (rec.get("model", {}).get("status") in ("pass", "blocked", "skipped"))
            ) else "fail"
        )
        report["devices"][dev] = rec

    checks = {
        "drc_clean": all(d.get("drc", {}).get("clean", False)
                         for d in report["devices"].values()),
        "lvs_match": all(d.get("lvs", {}).get("match", False)
                         for d in report["devices"].values()),
        "negative_controls_rejected": all(
            (d.get("negative_control") is None)
            or d["negative_control"].get("rejected", False)
            for d in report["devices"].values()),
        "pex_ran": all(
            d.get("extraction", {}).get("pex_netlist")
            and d.get("extraction", {}).get("pex_returncode") == 0
            for d in report["devices"].values()),
        "device_classes_ok": all(d.get("device_class_ok", False)
                                 for d in report["devices"].values()),
    }
    model_status = report["model"]["status"]
    if not args.skip_model:
        checks["model_dc"] = all(
            d.get("model", {}).get("status") == "pass"
            for d in report["devices"].values())
    report["checks"] = checks
    if not args.skip_model and model_status != "blocked":
        report["model"]["status"] = "pass" if checks.get("model_dc") else "blocked"
    report["status"] = "pass" if all(checks.values()) else "fail"

    report["notes"] = [
        "This is device-level DRC/LVS/PEX/model binding evidence for the "
        "IHP SG13G2 PCells, not a full-chip or analog-macro signoff.",
        "PMOS body is the n-well.  The bare SG13_dev pmos PCell leaves it as "
        "an internal node, so this lane adds the PDK n-well guard ring "
        "(guardRingType=nwell) and labels the contacted Metal1 ring 'b'.",
        "NMOS body is the PDK global substrate node ('sub'); no body label "
        "is needed in the layout.",
        "PEX uses the IHP Magic extract deck: extract; extresist all; "
        "ext2spice extresist on with rthresh=0 and cthresh=0.",
        "A short Metal1 source strap is added to give extresist a wire "
        "segment.  Counts are recorded exactly; zero R in a given device is "
        "reported as zero, not fabricated.",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ihp_device_verify.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n")

    for dev, rec in report["devices"].items():
        print(f"{dev}: status={rec.get('status')} "
              f"drc={rec.get('drc', {}).get('count')} "
              f"lvs={rec.get('lvs', {}).get('match')} "
              f"pex_R={rec.get('extraction', {}).get('pex', {}).get('resistors')} "
              f"pex_C={rec.get('extraction', {}).get('pex', {}).get('capacitors')} "
              f"model={rec.get('model', {}).get('status', 'skipped')}")
    print(f"overall={report['status']} evidence={json_path}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

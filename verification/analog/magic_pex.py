#!/usr/bin/env python3
"""Extract a sky130 layout with Magic and emit a ngspice-readable PEX netlist.

This is the supported replacement for the legacy Calibre RVE exports.  Magic
performs the capacitance/resistance extraction from the GDS using the sky130A
tech file installed under ``PDK_ROOT``; the resulting SPICE is wrapped in a
``.subckt`` with the requested port order.  No Calibre/RVE file is translated.

Example::

    python3 verification/analog/magic_pex.py \
        --gds Inverter_Based_Tx/Inv_Transmitter.gds \
        --cell Inv_Transmitter --ports GND,VDD,VIN,VOUT \
        --out build/verify/pex/Inv_Transmitter.pex.spice

The output intentionally does not include a PDK model library; the comparison
script adds ``.lib .../sky130.lib.spice <corner>`` so nominal and MC runs use the
same models.
"""

from __future__ import annotations

import argparse
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

DEFAULT_CELL_PORTS = {
    "Inv_Transmitter": ["GND", "VDD", "VIN", "VOUT"],
    "Receiver": ["GND", "VDD", "INP", "CLK", "OUT_B", "OUT"],
}


def find_pdk_root(explicit: str | None = None) -> Path:
    """Return a CIEL sky130 version directory (the parent of sky130A)."""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if p.name == "sky130A":
            return p.parent
        if (p / "sky130A").is_dir():
            return p
        raise FileNotFoundError(f"{p} is not a sky130 version root")
    env = os.environ.get("PDK_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if p.name == "sky130A":
            return p.parent
        if (p / "sky130A").is_dir():
            return p
    candidates = sorted(Path.home().glob(".ciel/ciel/sky130/versions/*"))
    for p in reversed(candidates):
        if (p / "sky130A/libs.tech/magic/sky130A.magicrc").is_file():
            return p
    raise FileNotFoundError("no sky130A Magic tech found; set PDK_ROOT")


def magic_rcfile(pdk_root: Path, explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(p)
    p = pdk_root / "sky130A" / "libs.tech" / "magic" / "sky130A.magicrc"
    if not p.is_file():
        raise FileNotFoundError(f"missing Magic rc: {p}")
    return p


def gds_read_path(gds: Path) -> Path:
    """Magic appends '.gds'; pass the stem when the input has that suffix."""
    if gds.suffix.lower() == ".gds":
        return gds.with_suffix("")
    return gds


def _run_magic(script: str, workdir: Path, pdk_root: Path, rc: Path,
               magic_bin: str) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    script_path = workdir / "magic_extract.tcl"
    script_path.write_text(script)
    env = dict(os.environ)
    env["PDK_ROOT"] = str(pdk_root)
    proc = subprocess.run(
        [magic_bin, "-dnull", "-noconsole", "-rcfile", str(rc), str(script_path)],
        cwd=workdir, env=env, text=True, capture_output=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"magic failed ({proc.returncode})\nSTDOUT:\n{proc.stdout[-4000:]}\n"
            f"STDERR:\n{proc.stderr[-4000:]}"
        )
    return proc.stdout + proc.stderr


def _ports_from_ext(ext: Path) -> list[str]:
    if not ext.is_file():
        raise FileNotFoundError(ext)
    ports = []
    for line in ext.read_text(errors="replace").splitlines():
        m = re.match(r'port\s+"([^"]+)"', line)
        if m and m.group(1) not in ports:
            ports.append(m.group(1))
    return ports


def _clean_body(raw: Path) -> list[str]:
    body = []
    for line in raw.read_text(errors="replace").splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        if s.lstrip().startswith("* SPICE3"):
            continue
        if s.lstrip().lower().startswith(".end"):
            continue
        # Magic marks floating caps with a trailing "**FLOATING"; ngspice
        # treats '**' as a continuation/unknown parameter, so remove it.
        s = re.sub(r"\s+\*\*FLOATING\b.*$", "", s)
        body.append(s)
    return body


def extract_pex(gds: Path, cell: str, ports: list[str] | None,
                out: Path, workdir: Path, pdk_root: Path,
                rc: Path, magic_bin: str, extresist: bool = True) -> dict[str, Any]:
    gds = gds.resolve()
    if not gds.is_file():
        raise FileNotFoundError(gds)
    raw = workdir / f"{cell}.raw.spice"
    ext = workdir / f"{cell}.ext"
    # Remove stale extraction products (including hierarchy .ext/.res.ext)
    # so repeated runs are deterministic and a failed run cannot leave a fake
    # success artifact.
    workdir.mkdir(parents=True, exist_ok=True)
    for p in list(workdir.glob("*.ext")) + list(workdir.glob("*.res.ext")):
        p.unlink(missing_ok=True)
    for p in (raw, ext):
        p.unlink(missing_ok=True)
    script = f"""gds read {{{gds_read_path(gds)}}}
load {cell} -dereference
select top cell
port makeall
extract
""" + ("extresist all\n" if extresist else "") + f"""ext2spice extresist {'on' if extresist else 'off'}
ext2spice -o {{{raw}}}
puts "MAGIC_PEX_DONE"
quit
"""
    log = _run_magic(script, workdir, pdk_root, rc, magic_bin)
    if not raw.is_file():
        raise RuntimeError(f"Magic did not write {raw}\n{log[-3000:]}")
    ext_ports = _ports_from_ext(ext)
    if ports is None or not ports:
        ports = DEFAULT_CELL_PORTS.get(cell) or ext_ports
    missing = [p for p in ports if p not in ext_ports]
    if missing:
        raise RuntimeError(
            f"requested ports {ports} not present in extracted ports {ext_ports}"
        )
    body = _clean_body(raw)
    if not body:
        raise RuntimeError("Magic extraction produced an empty body")
    text = (
        f"* Magic RC PEX extracted from {gds}\n"
        f"* cell {cell}; ports {','.join(ports)}; "
        f"generated_utc={datetime.now(timezone.utc).isoformat()}\n"
        f".subckt {cell} {' '.join(ports)}\n"
        + "\n".join(body) + "\n.ends\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    counts = {
        "devices": sum(1 for l in body if re.match(r"^X", l)),
        "resistors": sum(1 for l in body if re.match(r"^R", l)),
        "capacitors": sum(1 for l in body if re.match(r"^C", l)),
    }
    meta = {
        "kind": "magic-pex",
        "gds": str(gds),
        "cell": cell,
        "ports": ports,
        "extracted_ports": ext_ports,
        "out": str(out),
        "workdir": str(workdir),
        "pdk_root": str(pdk_root),
        "magic_rc": str(rc),
        "magic": magic_bin,
        "extresist": extresist,
        "counts": counts,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out.with_suffix(".json")).write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gds", required=True)
    ap.add_argument("--cell", default=None, help="top cell (default: GDS stem)")
    ap.add_argument("--ports", default=None,
                    help="comma-separated subckt port order; default cell map")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--magic-rc", default=None)
    ap.add_argument("--magic", default=None)
    ap.add_argument("--no-extresist", action="store_true",
                    help="capacitance-only extraction (not PEX)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    gds = Path(args.gds)
    cell = args.cell or gds.stem
    ports = ([p.strip() for p in args.ports.split(",") if p.strip()]
             if args.ports else None)
    if args.magic is None:
        args.magic = shutil.which("magic") or "magic"
    try:
        pdk = find_pdk_root(args.pdk_root)
        rc = magic_rcfile(pdk, args.magic_rc)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    workdir = Path(args.workdir) if args.workdir else (
        ROOT / "build" / "verify" / "magic_pex" / cell
    )
    if args.dry_run:
        print(f"gds={gds} cell={cell} ports={ports or 'auto'} out={args.out}")
        print(f"pdk_root={pdk} rc={rc} magic={args.magic} workdir={workdir}")
        return 0
    if shutil.which(args.magic) is None and not Path(args.magic).is_file():
        print(f"error: magic not found: {args.magic}", file=sys.stderr)
        return 2
    try:
        meta = extract_pex(gds, cell, ports, Path(args.out), workdir, pdk, rc,
                           args.magic, extresist=not args.no_extresist)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {meta['out']} {meta['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

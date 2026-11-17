#!/usr/bin/env python3
"""Install sky130 stdcell families into a ciel PDK as a flow SCL view.

The installed sky130A only ships ``sky130_fd_sc_hd`` (and ``hvl``) views.  The
``/grid/sky130`` trees carry more families (``hdll``, ``ms``, ``lp``, ``ls``,
``as_sc_hs``) with per-cell GDS/LEF/SPICE and per-corner ``.lib.json`` timing,
but not the ``libs.ref`` layout LibreLane expects (``lib/``, ``lef/``,
``gds/``, ``spice/``, ``cdl/``, ``verilog/``, ``techlef/`` with
``<scl>__{nom,min,max}.tlef``).

This script builds that layout in a staging directory and can install it into
the ciel PDK tree plus the matching ``libs.tech/librelane/<scl>/config.tcl``.

Two modes:

* one family -> one SCL (e.g. ``sky130_fd_sc_hdll``): a plain library install
  used to benchmark each family on its own;
* several families -> one **mixed** SCL: this is the ics55 pattern (H7CL/H7CH/
  H7CR are three 7-track libraries merged so the flow picks per cell).  Only
  families with the same cell height can be mixed, otherwise rows stop
  tiling: 2.72 µm for ``hd``/``hdll``/``as_sc_hs``, 3.33 µm for
  ``ms``/``lp``/``ls``.

Usage:
    python3 scripts/sky_install_family.py --scl sky130_fd_sc_hdll \
        --family sky130_fd_sc_hdll=hdll_lib \
        [--corners tt_025C_1v80,ss_100C_1v60,ff_n40C_1v65] \
        [--install] [--pdk-root ~/.ciel/ciel/sky130/versions/<hash>]

    python3 scripts/sky_install_family.py --scl sky130_mix9t \
        --family hd:installed --family sky130_fd_sc_hdll=/grid/sky130/sky130_fd_sc_hdll \
        --install

``--family name=path`` takes a ``/grid`` source tree; ``--family
hd:installed`` takes the already installed ``libs.ref`` view of that family
(used for ``hd``, which has no ``/grid`` source tree).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sky_json2lib  # noqa: E402

DEFAULT_CORNERS = ("tt_025C_1v80", "ss_100C_1v60", "ff_n40C_1v65")
CIEL_SKY130 = Path.home() / ".ciel/ciel/sky130/versions"


def installed_ref(family: str) -> Path:
    for v in sorted(CIEL_SKY130.iterdir()):
        ref = v / f"sky130A/libs.ref/{family}"
        if ref.is_dir():
            return ref
    raise SystemExit(f"{family} is not installed in ciel")


def cell_height(root: Path, family: str) -> float | None:
    """Cell height from an inv LEF, per-cell file or merged abstract."""
    cands = sorted(root.rglob(f"{family}__inv_*.lef"))
    if not cands:
        cands = sorted((root / "lef").glob("*.lef")) if (root / "lef").is_dir() else []
    for lef in cands:
        text = lef.read_text()
        m = re.search(rf"MACRO {family}__inv_.*?SIZE\s+[\d.]+\s+BY\s+([\d.]+)",
                      text, re.S)
        if m:
            return float(m.group(1))
        m = re.search(r"SIZE\s+[\d.]+\s+BY\s+([\d.]+)", text)
        if m and not (root / "lef").is_dir():
            return float(m.group(1))
    return None


def merge_lef(root: Path, family: str, dest: Path) -> int:
    """Concatenate the per-cell abstract LEFs into one file."""
    files = sorted(root.rglob(f"{family}__*.lef"))
    if not files:
        raise SystemExit(f"no LEF for {family}")
    header = None
    blocks: list[str] = []
    for f in files:
        text = f.read_text()
        idx = text.find("MACRO")
        if idx < 0:
            continue
        if header is None:
            header = text[:idx]
        blocks.append(text[idx:])
    if header is None:
        raise SystemExit(f"no MACRO blocks in {family} LEF")
    dest.write_text(header + "\n".join(blocks) + "\nEND LIBRARY\n")
    return len(blocks)


def merge_text(patterns, dest: Path, skip=()) -> int:
    out: list[str] = []
    n = 0
    for f in sorted(patterns):
        if any(s in f.name for s in skip):
            continue
        out.append(f.read_text())
        n += 1
    dest.write_text("\n".join(out))
    return n


def lef_blackbox(lef_text: str) -> str:
    """Verilog blackbox stubs from LEF MACRO/PIN declarations."""
    out = ["`ifndef VERILATOR", "`default_nettype none"], 
    out = ["`default_nettype none"]
    for m in re.finditer(r"^MACRO (\S+)\n(.*?)^END \1", lef_text, re.M | re.S):
        name, body = m.group(1), m.group(2)
        pins: list[tuple[str, str, bool]] = []   # (name, dir, is_supply)
        for pm in re.finditer(
                r"^  PIN (\S+)\n(.*?)(?=^  PIN |^  OBS |^END)",
                body, re.M | re.S):
            pname, pbody = pm.group(1), pm.group(2)
            d = re.search(r"DIRECTION (INPUT|OUTPUT|INOUT)", pbody)
            use = re.search(r"USE (\S+)", pbody)
            direction = d.group(1).lower() if d else "input"
            supply = bool(use and use.group(1) in ("POWER", "GROUND"))
            pins.append((pname, direction, supply))
        if not pins:
            continue
        # LEF decomposes buses into bit-select pins (D[0]..D[7]); Verilog
        # wants one vector port (input [7:0] D).
        merged: list[tuple[str, str, bool, int | None]] = []
        bus: dict[str, dict] = {}
        for pname, direction, supply in pins:
            m2 = re.match(r"^(\w+)\[(\d+)\]$", pname)
            if m2 and not supply:
                entry = bus.setdefault(m2.group(1), {"dir": direction,
                                                     "hi": 0})
                entry["hi"] = max(entry["hi"], int(m2.group(2)))
                entry["dir"] = direction
            else:
                merged.append((pname, direction, supply, None))
        for base, info in bus.items():
            merged.append((base, info["dir"], False, info["hi"]))
        sig = [p for p in merged if not p[2]]
        sup = [p for p in merged if p[2]]
        ports = ",\n    ".join(p[0] for p in sig)
        lines = [f"module {name} ("]
        if sig:
            lines.append(f"    {ports}")
        lines.append(");")
        for pname, direction, _, hi in sig:
            width = f"[{hi}:0] " if hi is not None else ""
            lines.append(f"    {direction} wire {width}{pname};")
        if sup:
            lines.append("")
            lines.append("    // Voltage supply signals")
            for pname, _, _, _ in sup:
                kind = "supply0" if pname.upper().startswith("VGND") or \
                    pname.upper().startswith("VNB") else "supply1"
                lines.append(f"    {kind} {pname};")
        lines.append("endmodule")
        out.append("\n".join(lines))
    return "\n".join(out) + "\n"


def _lef_pin_blocks(text: str) -> dict[str, dict[str, str]]:
    """{cell_base: {pin: "  PIN ... END"}} from a merged LEF (line based)."""
    out: dict[str, dict[str, str]] = {}
    cell: str | None = None
    pin: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("MACRO "):
            cell = line.split()[1]
            out.setdefault(cell.split("__", 1)[-1], {})
            pin = None
            continue
        if cell is None:
            continue
        if line.startswith("  PIN "):
            pin = line.split()[1]
            buf = [line]
            continue
        if pin is not None:
            buf.append(line)
            # A pin ends with "  END <pin>" (two-space indent); the inner
            # "    END" closes a PORT.
            if line.startswith("  END "):
                out[cell.split("__", 1)[-1]][pin] = "\n".join(buf)
                pin = None
            continue
        if line.startswith("END "):
            cell = None
    return out


def _synth_well_pin(pin: str, width: float) -> str:
    """Fallback well-bias pin for a cell with no same-name donor.

    All these families use the same 2.72 um `unithd` site, so the nwell sits in
    the upper half of the row and the pwell in the lower half.  The pin is an
    annotation on an existing well layer (no new geometry), which is what Magic
    needs to name the net; it is never routed.
    """
    if pin == "VPB":
        return ("  PIN VPB\n    DIRECTION INOUT ;\n    USE POWER ;\n"
                "    PORT\n      LAYER nwell ;\n"
                f"        RECT -0.190 1.305 {width + 0.190:.3f} 2.910 ;\n"
                "    END\n  END VPB")
    if pin == "VNB":
        return ("  PIN VNB\n    DIRECTION INOUT ;\n    USE GROUND ;\n"
                "    PORT\n      LAYER pwell ;\n"
                f"        RECT 0.005 0.105 {max(0.2, width - 0.090):.3f} 1.015 ;\n"
                "    END\n  END VNB")
    return ""


def add_missing_well_pins(text: str, donor: dict[str, dict[str, str]],
                          family: str, pins=("VNB", "VPB")) -> str:
    """Copy well-bias PIN blocks from a same-site donor library.

    The skywater-pdk per-cell LEFs for the low-leakage / medium-speed families
    declare VPWR and VGND but omit VNB and VPB, while their CDL and SPICE carry
    both.  Netgen then reports a top-level pin mismatch and a net-count
    difference on an otherwise matching design.  The well geometry is identical
    for the same site, so the donor's PIN block is valid here.
    """
    out: list[str] = []
    cell: str | None = None
    have: set[str] = set()
    width: float | None = None
    pending: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("MACRO "):
            cell = line.split()[1]
            have = set()
            width = None
            pending = []
            out.append(line)
            continue
        if line.startswith("END ") and cell is not None:
            # flush borrowed pins just before the macro ends
            base = cell.split("__", 1)[-1]
            for p in pins:
                if p in have:
                    continue
                if p in donor.get(base, {}):
                    out.append(donor[base][p] + "\n")
                elif width is not None:
                    out.append(_synth_well_pin(p, width) + "\n")
            out.append(line)
            cell = None
            continue
        if line.startswith("  SIZE ") and cell is not None:
            try:
                width = float(line.split()[1])
            except (IndexError, ValueError):
                width = None
        if line.startswith("  PIN ") and cell is not None:
            have.add(line.split()[1])
        out.append(line)
    return "".join(out)


def filter_lef_macros(text: str, family: str, keep) -> str:
    """Keep only the MACRO blocks whose base cell name passes `keep`."""
    out: list[str] = []
    i = 0
    while True:
        m = re.search(r"^MACRO (\S+)", text[i:], re.M)
        if not m:
            out.append(text[i:])
            break
        start = i + m.start()
        out.append(text[i:start])
        name = m.group(1)
        end = re.search(rf"^END {re.escape(name)}\s*$", text[start:], re.M)
        stop = start + (end.end() if end else len(text) - start)
        if keep(family, name.replace(f"{family}__", "")):
            out.append(text[start:stop])
        i = stop
    return "".join(out)


def techlef(hd_nom: Path, src: Path, dest_nom: Path, dest_min: Path,
            dest_max: Path, family: str) -> None:
    """Write nom/min/max tech LEFs, scaling RC keys by hd's corner ratios.

    The sky130 metal stack is identical across stdcell families, so the
    per-layer min/nom/max ratios measured from the installed hd tech LEFs
    apply unchanged to any other family's single nominal tech LEF.
    """
    siblings = hd_nom.parent
    nom_ref = hd_nom.read_text()
    table: dict[tuple[str, str], dict[str, float]] = {}
    layer = None
    for line in nom_ref.splitlines():
        m = re.match(r"\s*(?:LAYER|VIA)\s+(\S+)", line)
        if m:
            layer = m.group(1)
        m = re.match(r"\s*(RESISTANCE|RPERSQ|RCPERSQ|CAPACITANCE|"
                     r"CPERSQDIST|EDGECAPACITANCE)\s+([\d.eE+-]+)\s*;", line)
        if m and layer:
            table.setdefault((layer, m.group(1)), {})["nom"] = float(m.group(2))
    for corner in ("min", "max"):
        p = siblings / hd_nom.name.replace("__nom.tlef", f"__{corner}.tlef")
        if not p.is_file():
            continue
        layer = None
        for line in p.read_text().splitlines():
            m = re.match(r"\s*(?:LAYER|VIA)\s+(\S+)", line)
            if m:
                layer = m.group(1)
            m = re.match(r"\s*(RESISTANCE|RPERSQ|RCPERSQ|CAPACITANCE|"
                         r"CPERSQDIST|EDGECAPACITANCE)\s+([\d.eE+-]+)\s*;", line)
            if m and layer:
                key = (layer, m.group(1))
                if key in table and table[key]["nom"]:
                    table[key][corner] = (float(m.group(2))
                                          / table[key]["nom"])
    body = src.read_text()
    dest_nom.write_text(body)

    def scaled(ratio: str) -> str:
        layer = None
        out = []
        for line in body.splitlines():
            m = re.match(r"\s*(?:LAYER|VIA)\s+(\S+)", line)
            if m:
                layer = m.group(1)
            m = re.match(r"(\s*(RESISTANCE|RPERSQ|RCPERSQ|CAPACITANCE|"
                         r"CPERSQDIST|EDGECAPACITANCE)\s+)"
                         r"([\d.eE+-]+)(\s*;)", line)
            if m and layer:
                r = table.get((layer, m.group(2)), {}).get(ratio)
                if r:
                    line = (f"{m.group(1)}"
                            f"{float(m.group(3)) * r:.6g}{m.group(4)}")
            out.append(line)
        return "\n".join(out) + "\n"

    for corner, dest in (("min", dest_min), ("max", dest_max)):
        if any(k for k in table if table[k].get(corner)):
            dest.write_text(scaled(corner))
        else:
            dest.write_text(body)


def filter_liberty_cells(text: str, family: str, keep) -> str:
    """Drop `cell ("...")` blocks whose base name is not in `keep`."""
    out: list[str] = []
    i = 0
    n = 0
    while True:
        j = text.find('    cell (', i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        # find the matching closing brace of this cell block
        k = j
        depth = 0
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        block = text[j:k]
        m = re.match(r'    cell \("([^"]+)"\)', block)
        base = m.group(1).replace(f"{family}__", "") if m else ""
        if keep(family, base):
            out.append(block)
            n += 1
        i = k
    return "".join(out)


def family_lib(root: Path, family: str, corners, dest_dir: Path,
               scl: str, keep=None) -> list[str]:
    # An installed libs.ref view already has consolidated Liberty; reuse it
    # (its numbers are the open_pdks rendering of the same JSON).
    installed = root / "lib"
    if installed.is_dir() and not list(root.rglob("*__*.lib.json")):
        written = []
        for corner in corners:
            src = installed / f"{family}__{corner}.lib"
            if not src.is_file():
                print(f"  ! {family}: no installed liberty for {corner}",
                      file=sys.stderr)
                continue
            text = src.read_text().replace(f'library ("{family}__{corner}")',
                                           f'library ("{scl}__{corner}")', 1)
            if keep is not None:
                text = filter_liberty_cells(text, family, keep)
            (dest_dir / f"{scl}__{corner}.lib").write_text(text)
            written.append(corner)
            print(f"  lib {scl}__{corner}.lib: installed {family}")
        return written
    avail = sky_json2lib.corners(root, family)
    written = []
    for corner in corners:
        if corner not in avail:
            print(f"  ! {family}: no per-cell JSON for {corner}",
                  file=sys.stderr)
            continue
        info = avail[corner]
        if keep is not None:
            info = {"header": info["header"],
                    "cells": {c: p for c, p in info["cells"].items()
                              if keep(family, c)}}
        text, n = sky_json2lib.convert(root, family, corner, info)
        text = text.replace(f'library ("{family}__{corner}")',
                            f'library ("{scl}__{corner}")', 1)
        (dest_dir / f"{scl}__{corner}.lib").write_text(text)
        written.append(corner)
        print(f"  lib {scl}__{corner}.lib: {n} cells from {family}")
    return written


def header_plus_cells(family_lib_texts: list[str]) -> str:
    """One library header + the cell blocks of every input library.

    Bus declarations (``type(bus8)``) live in the family headers, so the
    merged header keeps the union: hd has no bus cells and hdll's
    ``muxb8to1_*`` would reference an undefined bus type otherwise.
    """
    header = family_lib_texts[0][: family_lib_texts[0].find("    cell (")]
    known = set(re.findall(r'    type\("(\w+)"\)', header))
    extra_types: list[str] = []
    for text in family_lib_texts[1:]:
        head = text[: text.find("    cell (")]
        for name in re.findall(r'    type\("(\w+)"\)', head):
            if name in known:
                continue
            known.add(name)
            m = re.search(rf'    type\("{name}"\) \{{.*?\n    \}}', head, re.S)
            if m:
                extra_types.append(m.group(0))
    if extra_types:
        header = header.rstrip() + "\n" + "\n".join(extra_types) + "\n"
    body = []
    for text in family_lib_texts:
        i = text.find("    cell (")
        part = text[i:].rstrip()
        part = part[: part.rfind("}")].rstrip()  # drop the library closing brace
        body.append(part)
    return header + "\n".join(body) + "\n}\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scl", required=True)
    ap.add_argument("--family", action="append", required=True,
                    help="name=path (source tree) or name:installed")
    ap.add_argument("--corners", default=",".join(DEFAULT_CORNERS))
    ap.add_argument("--stage", default="build/sky130_lib/installed")
    ap.add_argument("--only-cells", default=None,
                    help="file listing base cell names (one per line) to keep "
                         "from --filter-family; every other cell of that "
                         "family is skipped")
    ap.add_argument("--structural-family", default=None,
                    help="family whose tap/decap/fill/tie/CTS cells the SCL "
                         "config names (default: first --family)")
    ap.add_argument("--filter-family", default=None,
                    help="family the --only-cells list applies to (default: "
                         "the first --family)")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--pdk-root", default=None)
    args = ap.parse_args()

    corners = [c.strip() for c in args.corners.split(",") if c.strip()]
    stage = Path(args.stage) / args.scl
    if stage.exists():
        shutil.rmtree(stage)
    for sub in ("lib", "lef", "gds", "spice", "cdl", "verilog", "techlef"):
        (stage / sub).mkdir(parents=True)

    only: set[str] | None = None
    filter_family: str | None = None
    if args.only_cells:
        only = {ln.strip() for ln in Path(args.only_cells).read_text().splitlines()
                if ln.strip() and not ln.startswith("#")}

    specs = []
    for spec in args.family:
        if spec.endswith(":installed"):
            fam = spec.split(":")[0]
            specs.append((fam, installed_ref(fam)))
        else:
            fam, path = spec.split("=", 1)
            specs.append((fam, Path(path)))

    filter_family = args.filter_family or specs[0][0]
    if only is not None and filter_family not in {f for f, _ in specs}:
        raise SystemExit(f"--filter-family {filter_family} is not in --family")

    def keep(fam: str, cell: str) -> bool:
        if only is None or fam != filter_family:
            return True
        return cell in only

    heights = {fam: cell_height(root, fam) for fam, root in specs}
    print("cell heights:", heights)
    if len(set(h for h in heights.values() if h)) > 1:
        raise SystemExit(f"refusing to mix different cell heights: {heights}")

    # --- liberty: convert per family, then merge into one library per corner
    per_corner: dict[str, list[str]] = {}
    for fam, root in specs:
        tmp = stage / "_libs" / fam
        tmp.mkdir(parents=True, exist_ok=True)
        family_lib(root, fam, corners, tmp, fam,
                   keep=(lambda f, c: keep(f, c))
                   if (only is not None and fam == filter_family) else None)
        for f in sorted(tmp.glob("*.lib")):
            corner = f.name[len(fam) + 2:-4]
            per_corner.setdefault(corner, []).append(f.read_text())
    if not per_corner:
        raise SystemExit("no liberty data produced")
    for corner, texts in sorted(per_corner.items()):
        if len(specs) == 1:
            out = texts[0]
        else:
            out = header_plus_cells(texts)
            # header_plus_cells keeps the first library's header; rename it.
            out = re.sub(r'library \("\S+__%s"\)' % re.escape(corner),
                         f'library ("{args.scl}__{corner}")', out, count=1)
        (stage / "lib" / f"{args.scl}__{corner}.lib").write_text(out)
        ncells = out.count('    cell (')
        print(f"merged lib {args.scl}__{corner}.lib: {ncells} cells")

    # --- lef / gds / spice / cdl / verilog
    lef_parts: list[str] = []
    lef_header = None
    for fam, root in specs:
        lefs = [f for f in sorted(root.rglob(f"{fam}__*.lef"))
                if not f.name.endswith(".magic.lef")]
        if not lefs and (root / "lef").is_dir():
            lefs = sorted((root / "lef").glob("*.lef"))
        for f in lefs:
            merged = f.name == f"{fam}.lef"
            if not merged and not keep(
                    fam, f.name.replace(f"{fam}__", "").replace(".lef", "")):
                continue
            text = f.read_text()
            if merged and only is not None and fam == filter_family:
                text = filter_lef_macros(text, fam, keep)
            # Anchor on a MACRO block at column 0: "MACRO" also appears inside
            # PROPERTYDEFINITIONS ("  MACRO prCellType STRING ;"), and cutting
            # there would leak those property lines into the merged library.
            m = re.search(r"^MACRO ", text, re.M)
            if not m:
                continue
            if lef_header is None:
                lef_header = text[:m.start()]
            # Each per-cell file ends with its own "END LIBRARY"; keep only the
            # final one in the merged library.
            part = re.sub(r"END LIBRARY\s*$", "", text[m.start():]).rstrip()
            lef_parts.append(part)
    merged_lef = (lef_header or "") + "\n".join(lef_parts) + "\nEND LIBRARY\n"
    # Well-bias pins are missing from the hdll/ms/ls/lp abstracts; borrow them
    # from the installed hd library (same site, same footprint per cell name).
    try:
        hd_lef_dir = installed_ref("sky130_fd_sc_hd") / "lef"
        hd_lef = hd_lef_dir / "sky130_fd_sc_hd.lef"
        if not hd_lef.is_file():
            hd_lef = next(hd_lef_dir.glob("*.lef"))
        merged_lef = add_missing_well_pins(merged_lef,
                                           _lef_pin_blocks(hd_lef.read_text()),
                                           args.scl)
        print("patched missing VNB/VPB pins from sky130_fd_sc_hd")
    except (StopIteration, OSError) as exc:
        print(f"  ! well-pin patch skipped: {exc}", file=sys.stderr)
    (stage / "lef" / f"{args.scl}.lef").write_text(merged_lef)

    n_gds = n_spice = n_cdl = 0
    cdl_texts: list[str] = []
    for fam, root in specs:
        # Source trees are per cell; installed views are merged per library.
        gdss = sorted(root.rglob(f"{fam}__*.gds"))
        sps = sorted(root.rglob(f"{fam}__*.spice"))
        cds = [f for f in sorted(root.rglob(f"{fam}__*.cdl"))
               if "__udp" not in f.name]
        if not gdss and (root / "gds").is_dir():
            gdss = sorted((root / "gds").glob("*.gds"))
        if not sps and (root / "spice").is_dir():
            sps = sorted((root / "spice").glob("*.spice"))
        if not cds and (root / "cdl").is_dir():
            cds = sorted((root / "cdl").glob("*.cdl"))
        def _base(path: Path) -> str:
            n = path.name
            for suf in (".gds", ".spice", ".cdl"):
                n = n.replace(suf, "")
            return n.replace(f"{fam}__", "")

        # A merged library file (name == <family>.gds) carries every cell; it
        # cannot be filtered by name and must always be copied whole, or the
        # kept cells lose their layout.
        gdss = [f for f in gdss
                if f.name == f"{fam}.gds" or keep(fam, _base(f))]
        sps = [f for f in sps
               if f.name == f"{fam}.spice" or keep(fam, _base(f))]
        cds = [f for f in cds
               if f.name == f"{fam}.cdl" or keep(fam, _base(f))]
        for f in gdss:
            shutil.copy2(f, stage / "gds" / f.name)
        for f in sps:
            shutil.copy2(f, stage / "spice" / f.name)
        for f in cds:
            cdl_texts.append(f.read_text())
        n_gds += len(gdss)
        n_spice += len(sps)
        n_cdl += len(cds)
    # The skywater-pdk CDLs name the device models without the PDK prefix
    # ("nfet_01v8"), while Magic extraction and the open_pdks CDL use
    # "sky130_fd_pr__nfet_01v8".  Netgen then matches no devices at all and
    # reports "circuit contains no devices" for every such cell.
    cdl_text = "\n".join(cdl_texts)
    cdl_text = re.sub(r"(?<!__)\b((?:n|p)fet_0[13]v[38]\w*)",
                      r"sky130_fd_pr__\1", cdl_text)
    (stage / "cdl" / f"{args.scl}.cdl").write_text(cdl_text)
    (stage / "spice" / f"{args.scl}.spice").write_text(cdl_text)
    # verilog: blackbox stubs generated from the merged LEF.  The full
    # skywater body models instantiate UDPs through an undefined `UNIT_DELAY
    # macro, which fails Verilator lint; the installed hd view ships
    # <scl>__blackbox.v for exactly this reason and the flow uses it.
    verilog = lef_blackbox((stage / "lef" / f"{args.scl}.lef").read_text())
    (stage / "verilog" / f"{args.scl}.v").write_text(verilog)
    (stage / "verilog" / f"{args.scl}__blackbox.v").write_text(verilog)

    # --- tech LEF: nom/min/max
    hd_tech = installed_ref("sky130_fd_sc_hd") / "techlef"
    hd_nom = next(hd_tech.glob("*__nom.tlef"))
    src_tlef = None
    for fam, root in specs:
        cands = [f for f in sorted(root.rglob("*.tlef"))
                 if f.is_file()]
        if cands:
            src_tlef = cands[0]
            break
    if src_tlef is None:
        # hd installed view: reuse its own tech LEFs
        for corner in ("nom", "min", "max"):
            shutil.copy2(hd_nom.with_name(hd_nom.name.replace("__nom.tlef",
                                                            f"__{corner}.tlef")),
                         stage / "techlef" / f"{args.scl}__{corner}.tlef")
    else:
        techlef(hd_nom,
                src_tlef,
                stage / "techlef" / f"{args.scl}__nom.tlef",
                stage / "techlef" / f"{args.scl}__min.tlef",
                stage / "techlef" / f"{args.scl}__max.tlef",
                args.scl)

    summary = {
        "scl": args.scl,
        "families": [f for f, _ in specs],
        "cell_height_um": next(iter(heights.values())),
        "corners": sorted(per_corner),
        "gds_files": n_gds,
        "spice_files": n_spice,
        "cdl_cells": n_cdl,
        "lef_macros": sum(1 for p in lef_parts if p.startswith("MACRO")),
        "stage": str(stage),
    }
    (stage / "install.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))

    if args.install:
        pdk_root = Path(args.pdk_root) if args.pdk_root else \
            Path.home() / ".ciel/ciel/sky130/versions/792ef67037d74c8c51811eab3ce23cf793491296"
        ref_dest = pdk_root / "sky130A/libs.ref" / args.scl
        tech_dest = pdk_root / "sky130A/libs.tech/librelane" / args.scl
        if ref_dest.is_symlink() or ref_dest.is_file():
            ref_dest.unlink()
        elif ref_dest.is_dir():
            shutil.rmtree(ref_dest)
        ref_dest.parent.mkdir(parents=True, exist_ok=True)
        ref_dest.symlink_to(stage.resolve())
        tech_dest.mkdir(parents=True, exist_ok=True)
        # LibreLane needs the SCL support files next to config.tcl: synthesis
        # implementation maps, tracks.info, excluded-cell lists.  They are
        # expressed with the base family's cell names, which the merged library
        # always contains (structural cells come from the first family).
        hd_cfg = (installed_ref("sky130_fd_sc_hd")
                  .parent.parent / "libs.tech/librelane/sky130_fd_sc_hd")
        for f in hd_cfg.iterdir():
            if f.is_file() and f.name != "config.tcl":
                shutil.copy2(f, tech_dest / f.name)
        scl_cfg = make_scl_config(specs, args.structural_family)
        (tech_dest / "config.tcl").write_text(scl_cfg)
        print(f"installed {ref_dest} -> {stage}")
        print(f"installed {tech_dest}/config.tcl")
    return 0


def make_scl_config(specs, structural: str | None = None) -> str:
    """SCL config for a family install or a mixed library.

    The vendor configs use ``$::env(STD_CELL_LIBRARY)__<cell>``; a merged
    library keeps the families' own cell names, so the flow cells are named
    explicitly (structural cells always from the first family so rows, taps
    and fills stay consistent).
    """
    fams = [f for f, _ in specs]
    base = structural or fams[0]
    if base not in fams:
        raise SystemExit(f"--structural-family {base} is not in --family")
    p = f"{base}__"
    lines = [
        "set current_folder [file dirname [file normalize [info script]]]",
        "",
        'set ::env(PLACE_SITE) "unithd"',
        "",
        f'set ::env(WELLTAP_CELL) "{p}tapvpwrvgnd_1"',
        f'set ::env(ENDCAP_CELL) "{p}decap_3"',
        f'set ::env(SYNTH_DRIVING_CELL) "{p}inv_2/Y"',
        f'set ::env(SYNTH_CLK_DRIVING_CELL) "{p}clkinv_2/Y"',
        'set ::env(OUTPUT_CAP_LOAD) "33.442"',
        f'set ::env(SYNTH_BUFFER_CELL) "{p}buf_2/A/X"',
        f'set ::env(SYNTH_TIEHI_CELL) "{p}conb_1/HI"',
        f'set ::env(SYNTH_TIELO_CELL) "{p}conb_1/LO"',
        f'set ::env(CTS_ROOT_BUFFER) "{p}clkbuf_16"',
        f'set ::env(FILL_CELLS) "{p}fill*"',
        f'set ::env(DECAP_CELLS) "{p}decap*"',
        f'set ::env(DIODE_CELL) "{p}diode_2/DIODE"',
        "set ::env(GPL_CELL_PADDING) {0}",
        "set ::env(DPL_CELL_PADDING) {0}",
        f'set ::env(CELL_PAD_EXCLUDE) "{p}tap* {p}decap* {p}fill*"',
        f'set ::env(CTS_CLK_BUFFERS) "{p}clkbuf_*"',
        "set ::env(MAX_TRANSITION_CONSTRAINT) 0.75",
        "set ::env(MAX_FANOUT_CONSTRAINT) 10",
        "set ::env(MAX_CAPACITANCE_CONSTRAINT) 0.2",
        "set ::env(PDN_RAIL_WIDTH) 0.48",
        # Tristate cells: only list families that actually have them.
        'set ::env(TRISTATE_CELLS) "'
        + " ".join(f"{f}__ebuf* {f}__einv* {f}__muxb*"
                   for f, root in specs
                   if any(root.rglob(f"{f}__muxb*")) or
                   any(root.rglob(f"{f}__ebuf*")))
        + '"',
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())

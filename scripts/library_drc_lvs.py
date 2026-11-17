#!/usr/bin/env python3
"""Cell-level Magic DRC + Netgen LVS validation for sky130 stdcell families.

Method (same as the gf180 AS/OSU evidence in docs/library_selection.md):

- DRC: load each cell layout into Magic with the sky130A tech file and run
  ``drc check`` / ``drc catchup``; record the error count.  Families that ship
  GDS only are read through ``gds read``; families with .mag load the layout.
- LVS: Magic extracts the cell (``extract all`` + ``ext2spice lvs``), then
  Netgen compares the extracted netlist against the per-cell CDL with the
  PDK's ``sky130A_setup.tcl``.  A cell passes when the report says the
  circuits match uniquely.

Usage:
    python3 scripts/library_drc_lvs.py --family sky130_fd_sc_hdll \
        [--limit N] [--out build/library_checks] [--drc-only]

Exit status is 0 when the batch completed; the JSON holds the verdicts.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import re
import subprocess
import sys
from pathlib import Path

CIEL = Path.home() / ".ciel/ciel/sky130/versions"
PDK = Path("/grid/sky130")

# Row-level well/latch-up rules a lone cell cannot satisfy (taps come from
# tapvpwrvgnd in the row).  Validated at the routed-design DRC instead.
TAP_RULES = (
    "distance to N-tap",
    "distance to P-tap",
    "must contain metal-connected N+ taps",
    "must contain metal-connected P+ taps",
)

# Cells with no transistors or no meaningful LVS: fills, decaps, taps, ties.
SKIP_RE = re.compile(
    r"__(fill|decap|tap|tapvpwrvgnd|fakediode|conb|probe|unused)", re.I)


PDK_ROOT: Path | None = None


def pdk_view() -> tuple[Path, Path]:
    """Return (magicrc, netgen_setup) from the installed sky130A.

    The shipped .magicrc bakes a build-time PDK_ROOT but honours $PDK_ROOT,
    so every magic/netgen call runs with that variable set.
    """
    global PDK_ROOT
    for v in sorted(CIEL.iterdir()):
        mt = v / "sky130A/libs.tech/magic/sky130A.magicrc"
        ns = v / "sky130A/libs.tech/netgen/sky130A_setup.tcl"
        if mt.is_file() and ns.is_file():
            PDK_ROOT = v
            return mt, ns
    raise SystemExit("no installed sky130A magic/netgen tech files in ciel")


def family_root(family: str) -> Path:
    """/grid source tree first, then the installed ciel libs.ref view."""
    for cand in (PDK / family, *(v / f"sky130A/libs.ref/{family}"
                                 for v in sorted(CIEL.iterdir()))):
        if cand.is_dir():
            return cand
    raise SystemExit(f"no layout tree for {family}")


def cell_layouts(family: str) -> dict[str, Path]:
    """cell name -> layout (GDS preferred, .mag fallback)."""
    root = family_root(family)
    cells: dict[str, Path] = {}
    for g in sorted(root.rglob("*.gds")):
        m = re.match(rf"{family}__(\S+)\.gds$", g.name)
        if m:
            cells.setdefault(m.group(1), g)
    for g in sorted(root.rglob("*.mag")):
        m = re.match(rf"{family}__(\S+)\.mag$", g.name)
        if m:
            cells.setdefault(m.group(1), g)
    return cells


def cell_netlists(family: str) -> dict[str, Path]:
    """cell name -> golden netlist.

    Per-cell ``.spice`` is preferred: it is the netlist the layout was
    generated from and its port order matches Magic's extraction.  The
    open_pdks merged ``.cdl`` reorders ports, which makes Netgen report a
    pin-order failure ("Device classes ... are equivalent" but "failed pin
    matching") even though the netlists are equivalent.
    """
    out: dict[str, Path] = {}
    source_roots = [v / f"skywater-pdk/libraries/{family}/latest"
                    for v in sorted(CIEL.iterdir())
                    if (v / f"skywater-pdk/libraries/{family}/latest").is_dir()]
    for root in source_roots + [family_root(family)]:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.spice")):
            m = re.match(rf"{family}__(\S+)\.spice$", f.name)
            if not m or "__udp" in m.group(1) or "udp_" in f.name:
                continue
            out.setdefault(m.group(1), f)
        if out:
            return out
        if root == source_roots[0] if source_roots else False:
            continue
        for f in sorted(root.rglob("*.cdl")):
            m = re.match(rf"{family}__(\S+)\.cdl$", f.name)
            if m and "__udp" not in m.group(1):
                out.setdefault(m.group(1), f)
        if out:
            return out
    if not out:
        merged = sorted(root.rglob(f"{family}.cdl")) + \
            sorted(root.glob("cdl/*.cdl"))
        if merged:
            return {"*": merged[0]}
    return out


def _magic(magicrc: Path, cell: str, layout: Path, script: str) -> str:
    env = dict(os.environ)
    if PDK_ROOT is not None:
        env["PDK_ROOT"] = str(PDK_ROOT)
    r = subprocess.run(
        ["magic", "-dnull", "-noconsole", "-rcfile", str(magicrc), "/dev/stdin"],
        input=script, cwd=str(layout.parent), env=env,
        capture_output=True, text=True, timeout=900)
    return r.stdout + r.stderr


def magic_drc(magicrc: Path, cell: str, layout: Path, workdir: Path,
              full: str | None = None) -> int | None:
    workdir.mkdir(parents=True, exist_ok=True)
    rep = (workdir / (cell + ".drc")).resolve()
    if layout.suffix == ".gds":
        load = f"gds read [file normalize {layout}]\nload {cell} -hier\n"
    else:
        load = f"load [file rootname [file normalize {layout}]] -hier\n"
    # This is run_standard_drc.py's own recipe: full style, expand, listall.
    # Row-level well-tap rules (latch-up distance, nwell tap connection) are
    # structurally unsatisfiable on a lone stdcell: taps come from the
    # neighbouring tapvpwrvgnd cells in a placed row.  Ignore them per cell;
    # the routed-design Magic DRC in the PnR flow validates the rows.
    script = ("crashbackups stop\ndrc euclidean on\ndrc style drc(full)\n"
              "drc on\nsnap internal\n"
              + load
              + "select top cell\nexpand\n"
              + "set bx [box values]\n"
              + f"set fh [open [file normalize {rep}] w]\n"
              + "puts $fh \"BBOX $bx\"\n"
              + "if {[llength $bx] != 4} {\n"
              + "  puts $fh \"ERRORS 999999 LOAD-FAIL\"\n"
              + "  close $fh\n"
              + "  quit\n"
              + "}\n"
              + "drc catchup\n"
              + "set allerrors [drc listall why]\n"
              + "set nerr 0\n"
              + "foreach {whytext rectlist} $allerrors {\n"
              + "  incr nerr [llength $rectlist]\n"
              + "  puts $fh $whytext\n"
              + "}\n"
              + "puts $fh \"ERRORS $nerr\"\n"
              + "close $fh\nquit\n")
    log = _magic(magicrc, cell, layout, script)
    if not rep.is_file():
        sys.stderr.write(f"[drc] {cell}: no report; {log[-300:]}\n")
        return None
    txt = rep.read_text()
    if "LOAD-FAIL" in txt:
        sys.stderr.write(f"[drc] {cell}: layout did not load (empty cell)\n")
        return None
    # Count rects per rule; tap-distance rules are unsatisfiable on a lone
    # cell (taps come from tapvpwrvgnd in the placed row) and are reported
    # separately from real violations.
    total = tap = 0
    non_tap: list[str] = []
    for block in txt.split("\n\n"):
        if not block.strip():
            continue
        lines = [l for l in block.splitlines() if l.strip()]
        rule = lines[0]
        nrect = len(lines) - 1
        if rule.startswith("BBOX "):
            continue
        if rule.startswith("ERRORS "):
            continue
        total += nrect
        if any(s in rule for s in TAP_RULES):
            tap += nrect
        else:
            non_tap.append(rule)
    rep.with_suffix(".summary").write_text(
        f"total={total} tap={tap} non_tap_rules={len(non_tap)}\n"
        + "\n".join(non_tap))
    return total, tap


def magic_extract(magicrc: Path, cell: str, layout: Path,
                  workdir: Path, full: str | None = None) -> Path | None:
    workdir.mkdir(parents=True, exist_ok=True)
    spice = (workdir / f"{cell}.spice").resolve()
    name = full or cell
    if layout.suffix == ".gds":
        load = ("gds flatglob *__example_*\ngds flatten true\n"
                f"gds read [file normalize {layout}]\nload {name}\n")
    else:
        load = f"load [file rootname [file normalize {layout}]] -dereference\n"
    script = (load
              + "extract all\n"
              + "ext2spice lvs\n"
              + f"ext2spice -o [file normalize {spice}]\n"
              + "quit\n")
    log = _magic(magicrc, cell, layout, script)
    if not spice.is_file() or spice.stat().st_size == 0:
        sys.stderr.write(f"[ext] {cell}: no netlist; {log[-300:]}\n")
        return None
    return spice


def _reorder_golden(cell: str, layout_spice: Path, golden: Path,
                    workdir: Path) -> Path:
    """Rewrite the golden `.subckt` port list into the extracted port order.

    Netgen matches top-level pins by position, not by name.  The skywater
    per-cell SPICE and the Magic extraction list the same ports in different
    orders, which makes Netgen report "failed pin matching" even when the
    devices are equivalent.  Rewriting the golden port list keeps the
    connectivity comparison intact.
    """
    sub = re.compile(rf"^(\s*\.subckt\s+{re.escape(cell)}\s+)([^\n]*)$",
                     re.M | re.I)
    m_sp = sub.search(layout_spice.read_text(errors="ignore"))
    m_go = sub.search(golden.read_text(errors="ignore"))
    if not m_sp or not m_go:
        return golden
    p_sp = m_sp.group(2).split()
    p_go = m_go.group(2).split()
    if p_sp == p_go or set(p_sp) != set(p_go):
        return golden
    ordered = " ".join(p_sp)
    text = golden.read_text(errors="ignore")
    text = sub.sub(lambda m: m.group(1) + ordered, text, count=1)
    dest = (workdir / f"{cell}.golden.reordered.spice").resolve()
    dest.write_text(text)
    return dest


def netgen_lvs(setup: Path, cell: str, layout_spice: Path, golden: Path,
               workdir: Path) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    rep = (workdir / f"lvs_{cell}.log").resolve()
    lay = "{" + str(layout_spice) + "}"
    gol = "{" + str(golden) + "}"
    golden = _reorder_golden(cell.split("__")[-1], layout_spice, golden,
                             workdir)
    env = dict(os.environ)
    if PDK_ROOT is not None:
        env["PDK_ROOT"] = str(PDK_ROOT)
    r = subprocess.run(
        ["netgen", "-batch", "lvs",
         f"{lay} {cell}", f"{gol} {cell}",
         str(setup), str(rep)],
        capture_output=True, text=True, timeout=900, env=env)
    if not rep.is_file():
        sys.stderr.write(f"[lvs] {cell}: no report; "
                         f"{(r.stdout + r.stderr)[-300:]}\n")
        return "no-report"
    txt = rep.read_text()
    if "Circuits match uniquely" in txt or "Netlists match uniquely" in txt:
        return "match"
    return "mismatch"


def run_family(family: str, outdir: Path, limit: int | None,
               drc_only: bool, skip_drc: bool = False) -> dict:
    magicrc, netgen_setup = pdk_view()
    layouts = cell_layouts(family)
    netlists = cell_netlists(family)
    outdir.mkdir(parents=True, exist_ok=True)
    drcdir = outdir / family / "drc"
    extdir = outdir / family / "lvs"
    cells = sorted(c for c in layouts if not SKIP_RE.search(c))
    if limit:
        cells = cells[:limit]
    previous: dict = {}
    if skip_drc:
        prev_path = outdir / f"{family}.json"
        if prev_path.is_file():
            previous = json.loads(prev_path.read_text()).get("cells", {})
    res: dict[str, dict] = {}
    for cell in cells:
        entry: dict = dict(previous.get(cell, {}))
        if skip_drc and entry.get("drc"):
            n = None
        else:
            n = magic_drc(magicrc, cell, layouts[cell], drcdir,
                          full=f"{family}__{cell}")
        if skip_drc and entry.get("drc"):
            pass
        elif n is None:
            entry["drc_errors"] = entry["drc_tap_rules"] = None
            entry["drc"] = "no-report"
        else:
            entry["drc_errors"], entry["drc_tap_rules"] = n
            # A lone cell cannot satisfy the row-level well-tap rules; the
            # cell passes when every remaining rule is clean.
            clean = (entry["drc_errors"] - entry["drc_tap_rules"]) == 0
            entry["drc"] = "pass" if clean else "fail"
        if drc_only:
            entry["lvs"] = None
        full = f"{family}__{cell}"
        golden = netlists.get("*") or netlists.get(cell)
        if not drc_only and golden is not None:
            sp = magic_extract(magicrc, cell, layouts[cell], extdir,
                               full=f"{family}__{cell}")
            entry["lvs"] = ("no-netlist" if sp is None
                            else netgen_lvs(netgen_setup, full, sp,
                                            golden, extdir))
        elif not drc_only:
            entry["lvs"] = ("no-golden-library" if not netlists
                            else "no-golden-netlist")
        else:
            entry["lvs"] = "no-golden-netlist"
        res[cell] = entry
        print(f"  {cell}: drc={entry.get('drc')}({entry['drc_errors']}) "
              f"lvs={entry.get('lvs')}", flush=True)
    summary = {
        "family": family,
        "cells_checked": len(res),
        "drc_pass": sum(1 for e in res.values() if e["drc"] == "pass"),
        "drc_fail": sum(1 for e in res.values() if e["drc"] == "fail"),
        "drc_only_tap_rules": sum(1 for e in res.values()
                                  if e["drc"] == "fail" and e["drc_tap_rules"]
                                  == e["drc_errors"]),
        "drc_no_report": sum(1 for e in res.values() if e["drc"] == "no-report"),
        "lvs_match": sum(1 for e in res.values() if e["lvs"] == "match"),
        "lvs_bad": sum(1 for e in res.values()
                       if e["lvs"] not in (None, "match", "no-golden-netlist",
                                           "no-golden-library")),
        "lvs_no_golden": sum(1 for e in res.values()
                             if e["lvs"] in ("no-golden-netlist",
                                             "no-golden-library")),
    }
    (outdir / f"{family}.json").write_text(
        json.dumps({"summary": summary, "cells": res}, indent=1))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", action="append", required=True)
    ap.add_argument("--limit", type=int, default=None,
                    help="check at most N cells per family (debug)")
    ap.add_argument("--out", default="build/library_checks")
    ap.add_argument("--drc-only", action="store_true")
    ap.add_argument("--skip-drc", action="store_true",
                    help="reuse existing DRC verdicts, run LVS only")
    args = ap.parse_args()
    outdir = Path(args.out)
    totals = {}
    for fam in args.family:
        print(f"== {fam} ==", flush=True)
        totals[fam] = run_family(fam, outdir, args.limit, args.drc_only,
                                 args.skip_drc)
        print(json.dumps(totals[fam], indent=1), flush=True)
    # Per-family summary only: several batches run concurrently and must not
    # race on a shared file.
    for fam, summary in totals.items():
        (outdir / f"{fam}_summary.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

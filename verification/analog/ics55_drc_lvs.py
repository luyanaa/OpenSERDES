#!/usr/bin/env python3
"""Run the ICsprout55 KLayout DRC and LVS decks on a cell or macro.

The PDK now ships a KLayout DRC deck (`libs.tech/drc/ics55_drc.drc`) and a
KLayout/netgen-compatible LVS runner (`libs.tech/lvs/run_lvs.py`).  This
wrapper exercises both on the released H7CR `INVX1H7R` fixture by default and
records the exact DRC categories/counts and the LVS compare verdict.

The decks are provisional ports, not foundry signoff: the DRC README records
the omitted Calibre qualifiers, and the LVS README states there is no
same-input Calibre differential.  A cell-level PASS therefore proves the decks
run and agree on this fixture; it is not a full-design or foundry claim.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PDK_DEFAULT = Path("/grid/icsprout55-pdk")
LIB = "ics55_LLSC_H7CR"
CELL = "INVX1H7R"


def parse_lyrdb(path: Path) -> tuple[int, dict[str, int]]:
    text = path.read_text(errors="ignore")
    items = re.findall(r"<item>(.*?)</item>", text, re.S)
    categories: dict[str, int] = {}
    for item in items:
        match = re.search(r"<category>([^<]*)</category>", item)
        name = match.group(1).strip("'\"") if match else "?"
        categories[name] = categories.get(name, 0) + 1
    return len(items), dict(sorted(categories.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdk", default=str(PDK_DEFAULT))
    ap.add_argument("--gds", default=None,
                    help=f"default: libs.ref/{LIB}/gds/{LIB}.gds")
    ap.add_argument("--top", default=CELL)
    ap.add_argument("--netlist", default=None,
                    help=f"default: libs.ref/{LIB}/cdl/{LIB}.cdl")
    ap.add_argument("--tables", default="main",
                    help="DRC table family: main|feol|beol|... (see the deck README)")
    ap.add_argument("--out", default="build/verify/ics55_drc_lvs.json")
    ap.add_argument("--workdir", default="build/verify/ics55_drc_lvs")
    ap.add_argument("--require-drc-clean", action="store_true",
                    help="fail when the DRC report has any item (default: record only)")
    ap.add_argument("--drc-timeout", type=int, default=1800)
    ap.add_argument("--lvs-timeout", type=int, default=1800)
    args = ap.parse_args()

    pdk = Path(args.pdk).resolve()
    gds = (Path(args.gds) if args.gds
           else pdk / f"libs.ref/{LIB}/gds/{LIB}.gds").resolve()
    netlist = (Path(args.netlist) if args.netlist
               else pdk / f"libs.ref/{LIB}/cdl/{LIB}.cdl").resolve()
    work = Path(args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    klayout = shutil.which("klayout")
    if not klayout:
        print("error: klayout not found on PATH", file=sys.stderr)
        return 2
    for path in (gds, netlist):
        if not path.is_file():
            print(f"error: missing input {path}", file=sys.stderr)
            return 2

    drc_deck = pdk / "libs.tech/drc/ics55_drc.drc"
    lvs_runner = pdk / "libs.tech/lvs/run_lvs.py"
    drc_report = (work / "drc.lyrdb").resolve()
    lvs_report = (work / "lvs.lvsdb").resolve()
    lvs_extracted = (work / "extracted.cir").resolve()
    for path in (drc_report, lvs_report, lvs_extracted):
        path.unlink(missing_ok=True)

    drc_cmd = [
        klayout, "-b", "-r", str(drc_deck),
        "-rd", f"input={gds}", "-rd", f"top={args.top}",
        "-rd", f"report={drc_report}", "-rd", f"tables={args.tables}", "-z",
    ]
    try:
        drc = subprocess.run(drc_cmd, cwd=work, text=True,
                             capture_output=True, timeout=args.drc_timeout)
        drc_rc = drc.returncode
        drc_log = (drc.stdout + drc.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        drc_rc = None
        drc_log = f"timeout after {args.drc_timeout}s"
    drc_items, drc_categories = ((None, {}) if not drc_report.is_file()
                                 else parse_lyrdb(drc_report))

    lvs_cmd = [
        sys.executable, str(lvs_runner),
        "--layout", str(gds), "--top", args.top,
        "--netlist", str(netlist),
        "--report", str(lvs_report), "--target-netlist", str(lvs_extracted),
    ]
    try:
        lvs = subprocess.run(lvs_cmd, cwd=work, text=True,
                             capture_output=True, timeout=args.lvs_timeout)
        lvs_rc = lvs.returncode
        lvs_out = lvs.stdout + lvs.stderr
    except subprocess.TimeoutExpired:
        lvs_rc = None
        lvs_out = f"timeout after {args.lvs_timeout}s"
    if "ICS55_LVS_COMPARE=PASS" in lvs_out:
        compare = "PASS"
    elif "ICS55_LVS_COMPARE=FAIL" in lvs_out:
        compare = "FAIL"
    else:
        compare = "UNKNOWN"

    result = {
        "kind": "ics55-drc-lvs-cell-evidence",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "pdk": str(pdk),
        "gds": str(gds),
        "top": args.top,
        "netlist": str(netlist),
        "drc": {
            "deck": str(drc_deck),
            "tables": args.tables,
            "returncode": drc_rc,
            "report": str(drc_report),
            "items": drc_items,
            "categories": drc_categories,
            "log_tail": drc_log,
        },
        "lvs": {
            "runner": str(lvs_runner),
            "returncode": lvs_rc,
            "compare": compare,
            "report": str(lvs_report),
            "extracted": str(lvs_extracted),
            "log_tail": lvs_out[-4000:],
        },
        "note": (
            "Provisional KLayout DRC/LVS decks, not foundry signoff.  The DRC "
            "deck is a partial Calibre translation (see libs.tech/drc/README.md); "
            "the LVS deck has no same-input Calibre differential.  A cell-level "
            "LVS PASS proves the deck agrees on this released fixture."),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "top": args.top,
        "drc_returncode": drc_rc,
        "drc_items": drc_items,
        "drc_categories": drc_categories,
        "lvs_returncode": lvs_rc,
        "lvs_compare": compare,
        "out": str(out),
    }, indent=2))

    ok = (drc_rc == 0 and drc_report.is_file()
          and lvs_rc == 0 and compare == "PASS")
    if args.require_drc_clean and drc_items:
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run an ETRI050 0.5 um NMOS4/PMOS4 inverter model smoke test.

The ETRI050 PDK is an analog 5 V process with a vendor ngspice model set.
This probe uses nmos4/pmos4 and establishes only model usability, not a
physical PCell, DRC/LVS, or signoff claim.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path("/grid/ETRI050")


def find_root(override: str | None) -> Path:
    if override:
        return Path(override)
    if os.environ.get("ETRI050_ROOT"):
        return Path(os.environ["ETRI050_ROOT"])
    if DEFAULT_ROOT.is_dir():
        return DEFAULT_ROOT
    raise FileNotFoundError("ETRI050 root not found; set ETRI050_ROOT")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", default="build/verify/etri050_model.json")
    ap.add_argument("--workdir", default="build/verify/etri050_model")
    args = ap.parse_args()

    root = find_root(args.root)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    model = root / "libs.tech/models/05cmos_model_260210.lib"
    deck = work / "inv_dc.cir"
    deck.write_text(
        f".lib {model} MOS\n"
        "VDD vdd 0 5\n"
        "VIN in 0 0\n"
        "MP out in vdd vdd pmos4 W=10u L=0.6u\n"
        "MN out in 0 0 nmos4 W=5u L=0.6u\n"
        ".dc VIN 0 5 0.25\n"
        ".control\nrun\nprint v(out)\n.endc\n.end\n"
    )
    proc = subprocess.run(["ngspice", "-b", str(deck.resolve())], cwd=work,
                          text=True, capture_output=True, timeout=300)
    text = proc.stdout + proc.stderr
    (work / "ngspice.log").write_text(text)
    rows = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*$", line)
        if m:
            rows.append((float(m.group(2)), float(m.group(3))))
    result = {
        "kind": "etri050-inverter-model-smoke",
        "root": str(root),
        "netlist": str(deck),
        "returncode": proc.returncode,
        "points": len(rows),
        "vin_first": rows[0][0] if rows else None,
        "vout_first": rows[0][1] if rows else None,
        "vin_last": rows[-1][0] if rows else None,
        "vout_last": rows[-1][1] if rows else None,
        "inverting": bool(rows and rows[0][1] > 4.9 and rows[-1][1] < 0.1),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Model smoke only; no ETRI050 PCell, DRC/LVS, PEX, or vendor signoff claim.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if proc.returncode == 0 and result["inverting"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

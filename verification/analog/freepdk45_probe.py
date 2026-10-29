#!/usr/bin/env python3
"""Run a FreePDK45 Nangate45 inverter model smoke test with ngspice.

This is a model/usage supplement.  FreePDK45 has no KLayout device PCell in the
installed tree; the probe uses the released INV cell and the FreePDK45 ngspice
library.  It is not a DRC/LVS or manufacturability claim.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_VERSION = "784b1a483a5f4de362d6f3629378de1199453f3c"
DEFAULT_ROOT = Path(
    f"/home/yanlu/.ciel/ciel/freepdk45/versions/{DEFAULT_VERSION}/freepdk45"
)


def find_root(override: str | None) -> Path:
    if override:
        return Path(override)
    if os.environ.get("FREEPDK45_ROOT"):
        return Path(os.environ["FREEPDK45_ROOT"])
    if DEFAULT_ROOT.is_dir():
        return DEFAULT_ROOT
    candidates = sorted(Path("/home/yanlu/.ciel/ciel/freepdk45/versions").glob("*/freepdk45"))
    if not candidates:
        raise FileNotFoundError("FreePDK45 root not found; set FREEPDK45_ROOT")
    return candidates[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", default="build/verify/freepdk45_model.json")
    ap.add_argument("--workdir", default="build/verify/freepdk45_model")
    args = ap.parse_args()

    root = find_root(args.root)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    model = root / "libs.tech/ngspice/freepdk45.lib"
    inv = root / "libs.tech/klayout/example/INV.sp"
    deck = work / "inv_dc.cir"
    deck.write_text(
        f".include {inv}\n"
        f".lib {model} tt\n"
        "VDD vdd 0 1.0\n"
        "VIN A 0 0\n"
        "X1 A Z 0 vdd INV\n"
        ".dc VIN 0 1 0.1\n"
        ".control\nrun\nprint v(Z)\n.endc\n.end\n"
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
        "kind": "freepdk45-inverter-model-smoke",
        "root": str(root),
        "netlist": str(deck),
        "returncode": proc.returncode,
        "points": len(rows),
        "vin_first": rows[0][0] if rows else None,
        "vout_first": rows[0][1] if rows else None,
        "vin_last": rows[-1][0] if rows else None,
        "vout_last": rows[-1][1] if rows else None,
        "inverting": bool(rows and rows[0][1] > 0.9 and rows[-1][1] < 0.1),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Model smoke only; FreePDK45/Nangate45 is research, non-manufacturable, and has no repo DRC/LVS signoff.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if proc.returncode == 0 and result["inverting"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

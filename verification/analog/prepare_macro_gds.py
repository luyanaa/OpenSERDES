#!/usr/bin/env python3
"""Rename a macro GDS top cell and add the sky130 prBoundary layer.

LibreLane requires the GDS top cell name to equal the LEF MACRO name and a
prBoundary (235/4) for PR-boundary extraction.  This script performs only those
two packaging edits; it does not alter the analog geometry.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KLAYOUT_SCRIPT = r'''
import pya, os, json
src=os.environ['SRC']; dst=os.environ['DST']; name=os.environ['NAME']
l=pya.Layout(); l.read(src); c=l.top_cell(); c.name=name
b=c.bbox()
c.shapes(l.layer(235, 4)).insert(b)
l.write(dst)
print(json.dumps({'dst':dst,'top':c.name,
 'bbox':[b.p1.x*l.dbu,b.p1.y*l.dbu,b.p2.x*l.dbu,b.p2.y*l.dbu]}))
'''


def prepare(src: Path, dst: Path, name: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "prep.py"
        script.write_text(KLAYOUT_SCRIPT)
        env = {"SRC": str(src), "DST": str(dst), "NAME": name}
        proc = subprocess.run(["klayout", "-b", "-r", str(script)],
                              text=True, capture_output=True, env=env,
                              timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"klayout failed:\n{proc.stdout}\n{proc.stderr}")
    for line in proc.stdout.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(proc.stdout[-2000:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    try:
        meta = prepare(Path(args.src), Path(args.dst), args.name)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}")
        return 1
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

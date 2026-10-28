#!/usr/bin/env python3
"""Magic DRC evidence for an analog macro GDS.

This runs the sky130A Magic DRC deck, records the exact command, GDS hash,
cell, tool version and error count.  It is a DRC check, not an LVS claim.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root, magic_rcfile, gds_read_path  # noqa: E402


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_drc(gds: Path, cell: str, out: Path, workdir: Path,
            pdk_root: str | None = None) -> dict[str, Any]:
    pdk = find_pdk_root(pdk_root)
    rc = magic_rcfile(pdk)
    magic = shutil.which("magic") or "magic"
    gds = gds.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "drc.tcl"
    script.write_text(f"""gds read {{{gds_read_path(gds)}}}
load {cell} -dereference
select top cell
drc check
drc catchup
puts "DRC_COUNT_BEGIN"
drc count
puts "DRC_COUNT_END"
quit
""")
    env = dict(os.environ)
    env["PDK_ROOT"] = str(pdk)
    proc = subprocess.run(
        [magic, "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
        cwd=workdir, env=env, text=True, capture_output=True, timeout=900,
    )
    text = proc.stdout + proc.stderr
    m = re.search(r"Total DRC errors found:\s*(\d+)", text, re.I)
    errors = int(m.group(1)) if m else None
    report = {
        "kind": "magic-drc",
        "gds": str(gds),
        "gds_sha256": _sha(gds),
        "cell": cell,
        "pdk_root": str(pdk),
        "magic_rc": str(rc),
        "magic": magic,
        "command": [magic, "-dnull", "-noconsole", "-rcfile", str(rc), str(script)],
        "log": str(workdir / "drc.log"),
        "returncode": proc.returncode,
        "drc_errors": errors,
        "clean": bool(proc.returncode == 0 and errors == 0),
        "stdout_tail": text[-4000:],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Magic DRC only; no LVS or PEX signoff.",
    }
    (workdir / "drc.log").write_text(text)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gds", required=True)
    ap.add_argument("--cell", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--pdk-root", default=None)
    args = ap.parse_args()
    work = Path(args.workdir) if args.workdir else (
        ROOT / "build" / "verify" / "drc" / args.cell
    )
    try:
        rep = run_drc(Path(args.gds), args.cell, Path(args.out), work, args.pdk_root)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.cell}: DRC errors={rep['drc_errors']} clean={rep['clean']}")
    return 0 if rep["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

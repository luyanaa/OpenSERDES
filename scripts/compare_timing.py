#!/usr/bin/env python3
"""Yosys+OpenSTA timing comparison for serializer variants.

This is a local characterization helper, not a CI gate: it needs a Liberty
file and the `yosys`/`sta` binaries.  It maps the design to the target library
with dfflibmap + ABC, then reports the worst register-to-register path slack.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd or ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def synthesize(top: str, liberty: Path, rtl: Path, work: Path) -> Path:
    netlist = work / f"{top}.v"
    script = (
        f"read_verilog -sv {rtl}; "
        f"synth -top {top} -flatten; "
        f"dfflibmap -liberty {liberty}; "
        f"abc -liberty {liberty}; "
        f"opt_clean; write_verilog -noattr {netlist}"
    )
    _run(["yosys", "-q", "-p", script])
    return netlist


def worst_slack(top: str, liberty: Path, netlist: Path, period_ns: float,
                work: Path) -> float | None:
    tcl = work / f"{top}.tcl"
    tcl.write_text(
        f"read_liberty {liberty}\n"
        f"read_verilog {netlist}\n"
        f"link_design {top}\n"
        f"create_clock -name clk -period {period_ns} [get_ports clk]\n"
        "report_checks -path_delay max -format summary -digits 3\n"
    )
    out = _run(["sta", "-no_init", "-exit", str(tcl)])
    slacks = []
    for line in out.splitlines():
        match = re.search(r"(-?\d+\.\d+)\s*$", line)
        if match and "Slack" not in line:
            slacks.append(float(match.group(1)))
    return min(slacks) if slacks else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--liberty", required=True)
    ap.add_argument("--rtl", default=str(ROOT / "rtl/serializer_stream.sv"))
    ap.add_argument("--tops", nargs="*", default=["serializer_stream", "serializer_hier_2to1"])
    ap.add_argument("--period-ns", type=float, default=5.0)
    ap.add_argument("--out", default="build/timing_compare.json")
    args = ap.parse_args()
    for tool in ("yosys", "sta"):
        if shutil.which(tool) is None:
            raise SystemExit(f"error: {tool} not found")
    liberty = Path(args.liberty)
    rows = []
    with tempfile.TemporaryDirectory(prefix="timing_cmp_") as tmp:
        work = Path(tmp)
        for top in args.tops:
            rtl = (Path(args.rtl) if top == "serializer_stream"
                   else ROOT / "rtl" / f"{top}.sv")
            netlist = synthesize(top, liberty, rtl, work)
            slack = worst_slack(top, liberty, netlist, args.period_ns, work)
            rows.append({"top": top, "period_ns": args.period_ns, "worst_slack_ns": slack})
            print(f"{top:28s} period={args.period_ns:g} ns slack={slack}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"liberty": str(liberty), "rows": rows}, indent=2) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

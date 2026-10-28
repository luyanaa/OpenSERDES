#!/usr/bin/env python3
"""ngspice PDK Monte Carlo using the sky130 statistical model switches.

The sky130 ngspice library embeds the Spectre ``statistics { ... }`` mismatch
variables in its model expressions as ``MC_MM_SWITCH * AGAUSS(...)``.  ngspice
44 evaluates those expressions, so setting ``mc_mm_switch=1`` gives a real
per-device PDK mismatch draw; ``set seed`` controls the draw.  The PDK does not
wire a global-process switch into these device models, so this script keeps
global and mismatch modes separate and fails closed for global MC.

The two-device probe intentionally reports both the common-mode current and the
device-to-device difference: a real local mismatch must vary the difference,
while a global-only variation must not.
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
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root  # noqa: E402

MODES = {
    "nominal": (0, 0),
    "global": (1, 0),
    "mismatch": (0, 1),
    "both": (1, 1),
}


def parse_statistics(path: Path) -> dict[str, Any]:
    """Parse commented Spectre statistics blocks without executing them."""
    text = path.read_text(errors="replace") if path.is_file() else ""
    sections: dict[str, list[dict[str, Any]]] = {}
    current: str | None = None
    for line in text.splitlines():
        s = line.strip().lstrip("*").strip()
        if s.startswith("statistics"):
            continue
        if s in ("process {", "process{"):
            current = "process"
            sections.setdefault(current, [])
            continue
        if s in ("mismatch {", "mismatch{"):
            current = "mismatch"
            sections.setdefault(current, [])
            continue
        if current and s.startswith("vary "):
            m = re.match(r"vary\s+(\S+)\s+dist=(\S+)\s+std\s*=\s*([0-9.eE+-]+)", s)
            if m:
                sections[current].append({
                    "name": m.group(1),
                    "dist": m.group(2),
                    "std": float(m.group(3)),
                })
        if s == "}" and current:
            current = None
    return sections


def find_statistics_files(pdk: Path) -> list[Path]:
    roots = [
        pdk / "skywater-pdk/libraries/sky130_fd_pr/latest/models/parameters",
        pdk / "sky130A/libs.ref/sky130_fd_pr/spice",
    ]
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.glob("*.spice")):
            if "critical" in p.name or "__tt.pm3" in p.name:
                files.append(p)
    # Also include the consolidated critical file if present.
    for p in pdk.rglob("critical.spice"):
        if p not in files:
            files.append(p)
    return files


def _netlist(pdk: Path, pr: int, mm: int, seed: int) -> str:
    lib = pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"
    return f"""* sky130 PDK MC probe (ngspice)
.lib "{lib}" tt
.param mc_pr_switch={pr}
.param mc_mm_switch={mm}
Vdd vdd 0 1.8
Vg g 0 0.9
Xd1 d1 g 0 0 sky130_fd_pr__nfet_01v8 l=0.15 w=1
Xd2 d2 g 0 0 sky130_fd_pr__nfet_01v8 l=0.15 w=1
Vd1 d1 0 0.9
Vd2 d2 0 0.9
.control
set seed={seed}
op
print i(Vd1) i(Vd2)
.endc
.end
"""


def _currents(text: str) -> tuple[float | None, float | None]:
    vals: list[float] = []
    for m in re.finditer(r"i\(vd[12]\)\s*=\s*([-+0-9.eE]+)", text):
        vals.append(float(m.group(1)))
    if len(vals) < 2:
        # ngspice may print lowercase 'i(vd1)' exactly; fallback to any i(vd*).
        vals = [float(m.group(1)) for m in re.finditer(
            r"i\(vd[12]\)\s*=\s*([-+0-9.eE]+)", text, re.I)]
    return (vals[0], vals[1]) if len(vals) >= 2 else (None, None)


def run_point(pdk: Path, pr: int, mm: int, seed: int, workdir: Path,
              name: str) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    p = workdir / f"{name}.sp"
    p.write_text(_netlist(pdk, pr, mm, seed))
    proc = subprocess.run(["ngspice", "-b", str(p)], text=True,
                          capture_output=True, timeout=240)
    i1, i2 = _currents(proc.stdout + proc.stderr)
    return {
        "seed": seed,
        "i1_a": i1,
        "i2_a": i2,
        "common_a": ((i1 + i2) / 2.0) if i1 is not None and i2 is not None else None,
        "differential_a": (i1 - i2) if i1 is not None and i2 is not None else None,
        "returncode": proc.returncode,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def vals(key: str) -> np.ndarray:
        return np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
    common = vals("common_a")
    diff = vals("differential_a")
    out = {
        "n": len(rows),
        "seed_effective": len({round(float(r["i1_a"]), 15) for r in rows
                               if r.get("i1_a") is not None}) > 1,
    }
    if common.size:
        out["common"] = {"mean": float(common.mean()),
                         "std": float(common.std(ddof=1)) if common.size > 1 else 0.0,
                         "min": float(common.min()), "max": float(common.max())}
    if diff.size:
        out["differential"] = {"mean": float(diff.mean()),
                               "std": float(diff.std(ddof=1)) if diff.size > 1 else 0.0,
                               "min": float(diff.min()), "max": float(diff.max())}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=tuple(MODES), default="mismatch")
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--seed-start", type=int, default=1)
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--out", default=str(ROOT / "build" / "verify" / "pdk_mc.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build" / "verify" / "pdk_mc"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if shutil.which("ngspice") is None and not args.dry_run:
        print("error: ngspice is required", file=sys.stderr)
        return 2
    try:
        pdk = find_pdk_root(args.pdk_root)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    pr, mm = MODES[args.mode]
    if args.dry_run:
        print(f"pdk={pdk} mode={args.mode} pr={pr} mm={mm} "
              f"samples={args.samples} seeds={args.seed_start}..")
        return 0
    work = Path(args.workdir)
    rows = [
        run_point(pdk, pr, mm, args.seed_start + i, work,
                  f"{args.mode}_{i:04d}")
        for i in range(args.samples)
    ]
    summary = summarize(rows)
    stats = {}
    for path in find_statistics_files(pdk):
        parsed = parse_statistics(path)
        if parsed:
            stats[str(path)] = parsed
    # Global process is only credible if seed changes the common mode while
    # leaving the differential mode near zero; this PDK has no such switch.
    # Only the pr=1/mm=0 probe can attribute common-mode variation to global
    # process.  In the both-switches mode, local mismatch also moves the
    # common mode, so it must not be counted as evidence of global variation.
    global_supported = args.mode == "global" and (
        summary.get("common", {}).get("std", 0.0) > 0.0
    )
    local_supported = args.mode in ("mismatch", "both") and (
        summary.get("differential", {}).get("std", 0.0) > 0.0
    )
    if args.mode == "nominal":
        verdict = "nominal-seed-invariant" if not summary["seed_effective"] else "unexpected-variation"
    elif args.mode == "global":
        verdict = "global-supported" if global_supported else "global-unavailable"
    elif args.mode == "mismatch":
        verdict = "local-mismatch-supported" if local_supported else "local-mismatch-unavailable"
    else:
        verdict = ("local-mismatch-supported-global-unavailable"
                   if local_supported and not global_supported
                   else ("both-supported" if local_supported and global_supported
                         else "partial"))
    report = {
        "kind": "sky130-pdk-ngspice-mc",
        "pdk": str(pdk),
        "model_lib": str(pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"),
        "mode": args.mode,
        "switches": {"mc_pr_switch": pr, "mc_mm_switch": mm},
        "samples": args.samples,
        "seed_start": args.seed_start,
        "rows": rows,
        "summary": summary,
        "verdict": verdict,
        "global_supported": bool(global_supported),
        "local_mismatch_supported": bool(local_supported),
        "spectre_statistics": stats,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": ("PDK ngspice model switches evaluate the Spectre mismatch "
                 "variables per device; global-process variation is not wired "
                 "by this PDK/library and is reported as unavailable."),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"mode={args.mode} verdict={verdict} rows={len(rows)}")
    print(f"  common_std={summary.get('common', {}).get('std')}")
    print(f"  differential_std={summary.get('differential', {}).get('std')}")
    print(f"  seed_effective={summary['seed_effective']}")
    print(f"wrote {out}")
    return 0 if args.mode in ("nominal", "mismatch") else (
        0 if (args.mode == "global" and global_supported) else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())

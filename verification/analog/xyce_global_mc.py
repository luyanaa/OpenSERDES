#!/usr/bin/env python3
"""Global-process sky130 MC with Xyce .SAMPLING.

Why Xyce: the sky130 `critical.spice` `statistics { process { vary ... } }`
block is not executed by ngspice.  Xyce supports global-parameter sampling
through `.SAMPLING`, but the parameters must be declared **before** the model
library so that the model's `.param` expressions resolve to the sampled values.
The critical detail is:

    .global_param lvn_threshold=0.0
    ...
    .lib ".../sky130.lib.spice" mc

Putting the `.global_param` declarations before `.lib` makes the global scope
visible to `critical.spice`/`montecarlo.spice`.  Xyce then samples the PDK
process variables with the standard deviations declared in `critical.spice`.
The model sees one common-process value for all devices, which is exactly the
global-process behavior.

The probe uses two identical devices and reports common-mode and differential
current.  A valid global MC changes the common mode while leaving the
device-to-device difference at zero; local mismatch is tested separately.
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

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_pex import find_pdk_root  # noqa: E402


def find_xyce() -> Path | None:
    env = os.environ.get("XYCE") or os.environ.get("XYCE_BIN")
    if env and Path(env).is_file():
        return Path(env)
    which = shutil.which("Xyce")
    if which:
        return Path(which)
    candidates = sorted(Path.home().glob("spack/opt/spack/*/xyce-*/bin/Xyce"))
    return candidates[-1] if candidates else None


def find_mpirun() -> str | None:
    env = os.environ.get("MPIRUN") or os.environ.get("MPIEXEC")
    if env:
        return env
    return shutil.which("mpirun")


def critical_file(pdk: Path) -> Path:
    candidates = [
        pdk / "skywater-pdk/libraries/sky130_fd_pr/latest/models/parameters/critical.spice",
        pdk / "sky130A/libs.ref/sky130_fd_pr/spice/critical.spice",
    ]
    for p in candidates:
        if p.is_file():
            return p
    found = next(iter(pdk.rglob("critical.spice")), None)
    if found is None:
        raise FileNotFoundError("critical.spice not found")
    return found


def process_vars(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(errors="replace")
    out: list[dict[str, Any]] = []
    in_process = False
    for line in text.splitlines():
        s = line.strip().lstrip("*").strip()
        if s == "process {":
            in_process = True
            continue
        if in_process and s == "}":
            in_process = False
            continue
        if not in_process:
            continue
        m = re.match(r"vary\s+(\S+)\s+dist=(\S+)\s+std\s*=\s*([0-9.eE+-]+)", s)
        if m:
            out.append({"name": m.group(1), "dist": m.group(2),
                        "std": float(m.group(3))})
    if not out:
        raise RuntimeError(f"no process vary entries in {path}")
    return out


def _netlist(pdk: Path, stats: list[dict[str, Any]], mode: str,
             samples: int, seed: int, np_: int) -> str:
    names = [s["name"] for s in stats]
    stds = [str(s["std"]) for s in stats]
    lib = pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"
    if not lib.is_file():
        raise FileNotFoundError(lib)
    mm = 1 if mode in ("local", "both") else 0
    # Global .global_param declarations MUST precede .lib mc.  The library's
    # internal critical.spice then resolves to the sampled global values.
    lines = ["* OpenSERDES Xyce sky130 global-process MC"]
    lines += [f".global_param {name}=0.0" for name in names]
    lines += [
        f'.lib "{lib}" mc',
        f".param mc_mm_switch={mm}",
        ".param mc_pr_switch=1",
        "Vdd vdd 0 1.8",
        "Vg g 0 0.9",
        "Xd1 d1 g 0 0 sky130_fd_pr__nfet_01v8 l=0.15 w=1",
        "Xd2 d2 g 0 0 sky130_fd_pr__nfet_01v8 l=0.15 w=1",
        "Vd1 d1 0 0.9",
        "Vd2 d2 0 0.9",
        ".dc Vg 0.9 0.9 1",
        ".print dc i(Vd1) i(Vd2)",
    ]
    if mode in ("global", "both"):
        lines += [
            ".SAMPLING",
            "+ param=" + ",".join(names),
            "+ type=" + ",".join(["normal"] * len(names)),
            "+ means=" + ",".join(["0.0"] * len(names)),
            "+ std_deviations=" + ",".join(stds),
            f".options SAMPLES numsamples={samples} SAMPLE_TYPE=MC SEED={seed}",
        ]
    else:
        # A single nominal/local point.
        lines.append(f".options SAMPLES numsamples=1 SAMPLE_TYPE=MC SEED={seed}")
    lines.append(".end")
    return "\n".join(lines) + "\n"


def parse_prn(path: Path) -> list[dict[str, float]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, float]] = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip() or "Index" in line or line.startswith("End"):
            continue
        nums = re.findall(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", line)
        vals = [float(x) for x in nums]
        if len(vals) >= 3:
            if not (np.isfinite(vals[1]) and np.isfinite(vals[2])):
                raise RuntimeError(f"non-finite sample in {path}: {line!r}")
            rows.append({"i1_a": vals[1], "i2_a": vals[2]})
    if not rows:
        raise RuntimeError(f"no numeric rows in {path}")
    for r in rows:
        r["common_a"] = (r["i1_a"] + r["i2_a"]) / 2.0
        r["differential_a"] = r["i1_a"] - r["i2_a"]
    return rows


def expected_samples(mode: str, samples: int) -> int:
    return int(samples) if mode in ("global", "both") else 1


def summarize(rows: list[dict[str, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    for key in ("i1_a", "i2_a", "common_a", "differential_a"):
        vals = np.array([r[key] for r in rows], dtype=float)
        if vals.size:
            out[key] = {
                "mean": float(vals.mean()),
                "std": float(vals.std(ddof=1)) if vals.size > 1 else 0.0,
                "min": float(vals.min()),
                "max": float(vals.max()),
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("nominal", "global", "local", "both"),
                    default="global")
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument("--np", type=int, default=1, help="mpirun process count")
    ap.add_argument("--xyce", default=None)
    ap.add_argument("--mpirun", default=None)
    ap.add_argument("--no-mpirun", action="store_true")
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--out", default=str(ROOT / "build/verify/xyce_global_mc.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build/verify/xyce_global_mc"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        pdk = find_pdk_root(args.pdk_root)
        crit = critical_file(pdk)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    stats = process_vars(crit)
    xyce = Path(args.xyce) if args.xyce else find_xyce()
    mpirun = args.mpirun or find_mpirun()
    if xyce is None or not Path(xyce).is_file():
        print("error: Xyce not found; set XYCE or install via spack", file=sys.stderr)
        return 2
    if not args.no_mpirun and mpirun is None:
        print("error: mpirun not found; use --no-mpirun only for diagnostics",
              file=sys.stderr)
        return 2
    text = _netlist(pdk, stats, args.mode, args.samples, args.seed, args.np)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    netlist = work / "xyce_global.cir"
    netlist.write_text(text)
    if args.dry_run:
        print(f"xyce={xyce} mpirun={mpirun} mode={args.mode} "
              f"samples={args.samples} process_vars={len(stats)}")
        print(f"netlist={netlist}")
        return 0
    # Xyce writes <netlist>.prn and <netlist>.res next to the netlist.
    for p in (work / "xyce_global.cir.prn", work / "xyce_global.cir.res"):
        p.unlink(missing_ok=True)
    cmd = ([mpirun, "-np", str(args.np)] if not args.no_mpirun else []) + [
        str(xyce), "-b", str(netlist)
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=900,
                          cwd=work)
    prn = work / "xyce_global.cir.prn"
    res = work / "xyce_global.cir.res"
    expected = expected_samples(args.mode, args.samples)
    if proc.returncode != 0:
        print(f"error: Xyce/mpirun exited {proc.returncode}\n"
              f"STDOUT:\n{proc.stdout[-5000:]}\nSTDERR:\n{proc.stderr[-3000:]}",
              file=sys.stderr)
        return 1
    try:
        rows = parse_prn(prn)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}\nSTDOUT:\n{proc.stdout[-5000:]}\nSTDERR:\n{proc.stderr[-3000:]}",
              file=sys.stderr)
        return 1
    complete = len(rows) == expected
    summary = summarize(rows)
    common_std = summary.get("common_a", {}).get("std", 0.0)
    diff_std = summary.get("differential_a", {}).get("std", 0.0)
    if args.mode == "global":
        supported = (complete and common_std > 0.0
                     and diff_std <= max(1e-15, abs(common_std) * 1e-6))
        verdict = ("global-process-supported" if supported
                   else "incomplete-samples" if not complete
                   else "global-process-not-observed")
    elif args.mode == "local":
        supported = complete and diff_std > 0.0
        verdict = ("local-mismatch-supported" if supported
                   else "incomplete-samples" if not complete
                   else "local-mismatch-not-observed")
    elif args.mode == "both":
        supported = complete and common_std > 0.0 and diff_std > 0.0
        verdict = ("global-and-local-supported" if supported
                   else "incomplete-samples" if not complete else "partial")
    else:
        supported = complete
        verdict = "nominal" if complete else "incomplete-samples"
    out = {
        "kind": "sky130-xyce-global-process-mc",
        "pdk": str(pdk),
        "critical_spice": str(crit),
        "xyce": str(xyce),
        "mpirun": mpirun,
        "mpi_np": args.np,
        "mode": args.mode,
        "samples": args.samples,
        "expected_samples": expected,
        "observed_samples": len(rows),
        "complete": bool(complete),
        "process_returncode": proc.returncode,
        "seed": args.seed,
        "process_variables": stats,
        "rows": rows,
        "summary": summary,
        "verdict": verdict,
        "supported": bool(supported),
        "command": cmd,
        "prn": str(prn),
        "res": str(res),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": ("Xyce .SAMPLING of the PDK process variables with .global_param "
                 "declared before .lib mc; common-mode change with zero "
                 "device-to-device difference is the global-process signature."),
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"mode={args.mode} verdict={verdict} rows={len(rows)}")
    print(f"  common_std={common_std:.6e} A")
    print(f"  differential_std={diff_std:.6e} A")
    print(f"  process_vars={len(stats)} seed={args.seed} np={args.np}")
    print(f"wrote {path}")
    return 0 if supported else 1


if __name__ == "__main__":
    raise SystemExit(main())

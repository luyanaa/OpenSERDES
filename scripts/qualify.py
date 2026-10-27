#!/usr/bin/env python3
"""Qualification gate for generated SerDes packages.

Levels are deliberately cumulative.  A package cannot claim POST_ROUTE_TIMING
from a pre-PnR WNS, and cannot claim LINK_VALIDATED from RTL simulation alone.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LEVELS = [
    ("RTL_FUNCTIONAL", ["lint", "sim", "synth"]),
    ("PRE_PNR_TIMING", ["pre_pnr_setup_wns", "pre_pnr_hold_wns"]),
    ("POST_ROUTE_TIMING", ["post_route_setup_wns", "post_route_hold_wns"]),
    ("PHYSICAL_SIGNOFF", ["drc_clean", "lvs_clean", "antenna_clean", "cdc_clean"]),
    ("LINK_VALIDATED", ["ber", "jtol_pass", "eye_margin_ui"]),
]

BOOLEAN_CHECKS = {"lint", "sim", "synth", "drc_clean", "lvs_clean",
                  "antenna_clean", "cdc_clean", "jtol_pass"}
WNS_CHECKS = {"pre_pnr_setup_wns", "pre_pnr_hold_wns",
              "post_route_setup_wns", "post_route_hold_wns"}


def git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check(name: str, value, ber_target: float) -> tuple[bool, str]:
    if value is None:
        return False, "missing"
    if name in BOOLEAN_CHECKS:
        return bool(value), "pass" if value else "fail"
    if name in WNS_CHECKS:
        ok = float(value) >= 0.0
        return ok, f"{float(value):+.4f} ns"
    if name == "ber":
        ok = float(value) <= ber_target
        return ok, f"{float(value):.3e} (target {ber_target:.1e})"
    if name == "eye_margin_ui":
        ok = float(value) > 0.0
        return ok, f"{float(value):.4f} UI"
    return bool(value), "pass" if value else "fail"


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate a SerDes qualification report")
    ap.add_argument("--report", required=True)
    ap.add_argument("--level", default="RTL_FUNCTIONAL",
                    choices=[name for name, _ in LEVELS])
    ap.add_argument("--out", default=None, help="write qualification.json")
    args = ap.parse_args()

    report = json.loads(Path(args.report).read_text())
    checks = report.get("checks", {})
    ber_target = float(report.get("ber_target", 1e-12))

    achieved = "UNVERIFIED"
    details: dict[str, dict[str, str]] = {}
    for level, required in LEVELS:
        missing = []
        level_details = {}
        for name in required:
            ok, detail = check(name, checks.get(name), ber_target)
            level_details[name] = detail
            if not ok:
                missing.append(name)
        details[level] = level_details
        if not missing:
            achieved = level
        else:
            break

    rank = [name for name, _ in LEVELS]
    requested_ok = achieved in rank and rank.index(achieved) >= rank.index(args.level)
    missing_for_requested = []
    if not requested_ok:
        for level, required in LEVELS:
            if rank.index(level) > rank.index(args.level):
                break
            missing_for_requested += [n for n in required if not check(n, checks.get(n), ber_target)[0]]

    print(f"report: {args.report}")
    print(f"achieved: {achieved}")
    print(f"requested: {args.level} -> {'PASS' if requested_ok else 'FAIL'}")
    for level, level_details in details.items():
        state = "checked" if achieved != "UNVERIFIED" and rank.index(level) <= rank.index(achieved) else "not reached"
        print(f"  {level}: {state}")
        for name, detail in level_details.items():
            print(f"    {name}: {detail}")
    if missing_for_requested:
        print("missing for requested level: " + ", ".join(sorted(set(missing_for_requested))))

    if args.out:
        payload = {
            "level": achieved,
            "requested_level": args.level,
            "requested_pass": requested_ok,
            "checks": checks,
            "details": details,
            "missing_for_requested": sorted(set(missing_for_requested)),
            "ber_target": ber_target,
            "evaluated_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit(Path(__file__).resolve().parent.parent),
        }
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {args.out}")

    return 0 if requested_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

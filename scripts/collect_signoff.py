#!/usr/bin/env python3
"""Collect LibreLane/OpenROAD/OpenSTA/PDK reports into a qualification report.

Expected report layout (all optional except the level you are claiming):

  reports/rtl_functional.json     {"checks": {"lint": true, "sim": true, "synth": true}}
  reports/pre_pnr_setup.rpt       text containing "setup WNS: 0.12"
  reports/pre_pnr_hold.rpt        text containing "hold WNS: 0.01"
  reports/post_route_setup.rpt    text containing "setup WNS: 0.04"
  reports/post_route_hold.rpt     text containing "hold WNS: 0.008"
  reports/drc.rpt                 text containing "0 violations" or "clean"
  reports/lvs.rpt                 text containing "match uniquely" or "clean"
  reports/antenna.rpt             text containing "0 violations" or "clean"
  reports/cdc.rpt                 text containing "0 violations" or "clean"
  reports/ber.json                {"ber": 1e-13, "jtol_pass": true, "eye_margin_ui": 0.2}
  reports/metrics.json            explicit {"checks": {...}} override

The collector is intentionally format-tolerant, but it never guesses a missing
check as passing.  A missing or ambiguous report leaves the check absent.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from qualify import evaluate  # noqa: E402

NUMBER = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def first_number(text: str, key: str) -> float | None:
    patterns = [
        rf"(?is)\b{key}\b[^\n0-9+-]*({NUMBER})",
        rf"(?is){key}\s*(?:wns|slack)?\s*[:=]\s*({NUMBER})",
        rf"(?is)worst\s+slack[^\n0-9+-]*({NUMBER})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
    return None


def parse_timing(reports: Path, stage: str, kind: str) -> float | None:
    candidates = [
        reports / f"{stage}_{kind}.rpt",
        reports / f"{stage}_{kind}.log",
    ]
    for path in candidates:
        if path.exists():
            return first_number(path.read_text(errors="ignore"), kind)
    # Fallback: one combined report with "setup WNS" and "hold WNS".
    for name in (f"{stage}_sta.rpt", f"{stage}_sta.log", f"{stage}.rpt"):
        path = reports / name
        if path.exists():
            return first_number(path.read_text(errors="ignore"), kind)
    return None


def clean_check(path: Path, kind: str) -> bool | None:
    if not path.exists():
        return None
    text = path.read_text(errors="ignore").lower()
    if kind == "lvs":
        if "match uniquely" in text or "circuits match" in text:
            return True
        if "do not match" in text or "failed" in text:
            return False
    if "no violations" in text or "0 violations" in text or "clean" in text:
        return True
    if "violation" in text or "error" in text or "fail" in text:
        return False
    return None


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def collect(package: Path, reports: Path) -> dict:
    checks: dict[str, object] = {}

    rtl = load_json(reports / "rtl_functional.json")
    for key in ("lint", "sim", "synth"):
        if key in rtl.get("checks", {}):
            checks[key] = rtl["checks"][key]

    for stage in ("pre_pnr", "post_route"):
        for kind in ("setup", "hold"):
            value = parse_timing(reports, stage, kind)
            if value is not None:
                checks[f"{stage}_{kind}_wns"] = value

    for key, filename in (("drc_clean", "drc.rpt"),
                          ("lvs_clean", "lvs.rpt"),
                          ("antenna_clean", "antenna.rpt"),
                          ("cdc_clean", "cdc.rpt")):
        value = clean_check(reports / filename, key.split("_")[0])
        if value is not None:
            checks[key] = value

    ber = load_json(reports / "ber.json")
    # Accept both the legacy flat form and the richer verify_link form, which
    # nests the qualification checks under "checks".
    ber_checks = ber.get("checks", {}) if isinstance(ber.get("checks"), dict) else {}
    for key in ("ber", "jtol_pass", "eye_margin_ui"):
        if key in ber:
            checks[key] = ber[key]
        elif key in ber_checks:
            checks[key] = ber_checks[key]

    metrics = load_json(reports / "metrics.json")
    checks.update(metrics.get("checks", {}))

    manifest = load_json(package / "manifest.json")
    return {
        "package": str(package),
        "design": manifest.get("design"),
        "spec": manifest.get("spec_name"),
        "ber_target": float(ber.get("ber_target", metrics.get("ber_target", 1e-12))),
        "checks": checks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect signoff reports into qualification.json")
    ap.add_argument("--package", required=True)
    ap.add_argument("--reports", default=None)
    ap.add_argument("--requested-level", default="POST_ROUTE_TIMING",
                    choices=["RTL_FUNCTIONAL", "PRE_PNR_TIMING", "POST_ROUTE_TIMING",
                             "PHYSICAL_SIGNOFF", "LINK_VALIDATED"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    package = Path(args.package).resolve()
    if not (package / "manifest.json").exists():
        print(f"error: {package} is not a generated package", file=sys.stderr)
        return 2
    reports = Path(args.reports).resolve() if args.reports else package / "reports"
    if not reports.exists():
        print(f"error: report directory {reports} does not exist", file=sys.stderr)
        return 2

    report = collect(package, reports)
    result = evaluate(report["checks"], report["ber_target"], args.requested_level)
    report["qualification"] = result
    report["requested_level"] = args.requested_level

    out = Path(args.out).resolve() if args.out else package / "signoff.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"package: {package}")
    print(f"reports: {reports}")
    print(f"achieved: {result['achieved']}")
    print(f"requested: {args.requested_level} -> "
          f"{'PASS' if result['requested_pass'] else 'FAIL'}")
    for level, details in result["details"].items():
        print(f"  {level}:")
        for name, detail in details.items():
            print(f"    {name}: {detail}")
    if result["missing_for_requested"]:
        print("missing for requested level: " + ", ".join(result["missing_for_requested"]))
    print(f"wrote {out}")
    return 0 if result["requested_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

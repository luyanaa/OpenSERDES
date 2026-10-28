#!/usr/bin/env python3
"""Collect LibreLane/OpenROAD/OpenSTA/PDK reports into a qualification report.

Expected report layout (all optional except the level you are claiming):

  reports/rtl_functional.json     {"checks": {"lint": true, "sim": true, "synth": true}}
  reports/pre_pnr_setup.rpt       text containing "setup WNS: 0.12"
  reports/pre_pnr_hold.rpt        text containing "hold WNS: 0.01"
  reports/post_route_setup.rpt    text containing "setup WNS: 0.04"
  reports/post_route_hold.rpt     text containing "hold WNS: 0.008"
  reports/drc.rpt                 exact integer violation count (must be 0)
  reports/lvs.rpt                 explicit "match uniquely"/"circuits match"
  reports/antenna.rpt             exact integer violation count (must be 0)
  reports/cdc.rpt                 exact integer violation count (must be 0)
  reports/ber.json                {"ber": 1e-13, "jtol_pass": true, "eye_margin_ui": 0.2}
  reports/metrics.json            supplementary metrics only; never overrides
                                  a tool-derived signoff check

The collector is fail-closed.  It never guesses a missing check as passing
and rejects report text such as "10 violations" or "not clean" even though it
contains the substring "0 violations"/"clean".
"""
from __future__ import annotations

import argparse
import hashlib
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


def _timing_report(reports: Path, stage: str, kind: str) -> Path | None:
    candidates = [reports / f"{stage}_{kind}.rpt", reports / f"{stage}_{kind}.log"]
    for path in candidates:
        if path.is_file():
            return path
    for name in (f"{stage}_sta.rpt", f"{stage}_sta.log", f"{stage}.rpt"):
        path = reports / name
        if path.is_file():
            return path
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


SIGNOFF_CHECKS = {
    "drc_clean", "lvs_clean", "antenna_clean", "cdc_clean",
    "pre_pnr_setup_wns", "pre_pnr_hold_wns",
    "post_route_setup_wns", "post_route_hold_wns",
    "ber", "jtol_pass", "eye_margin_ui",
}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _report_evidence(path: Path, text: str, value: bool | None) -> dict:
    digest = _sha256(path)
    evidence: dict = {
        "source": str(path),
        "source_sha256": digest,
        "input_sha256": digest,
        "status": ("pass" if value is True else "fail" if value is False
                   else "unparsed"),
        "run_status": ("pass" if value is True else "fail" if value is False
                       else "unparsed"),
        "tool_version": None,
        "corner": None,
        "gds_sha256": None,
        "netlist_sha256": None,
        "log": str(path),
    }
    for key, patterns in (
        ("tool_version", [r"(?im)^\s*(?:tool|version|openroad|opensta|magic|klayout|ngspice)\s*[:=]\s*(.+)$"]),
        ("corner", [r"(?im)\bcorner\s*[:=]\s*([^\s,;]+)"]),
        ("tool_exit_code", [r"(?im)\b(?:exit\s*code|return\s*code|status)\s*[:=]\s*(-?\d+)"]),
        ("gds_sha256", [r"(?im)\bgds_sha256\s*[:=]\s*([0-9a-fA-F]{8,})"]),
        ("netlist_sha256", [r"(?im)\bnetlist_sha256\s*[:=]\s*([0-9a-fA-F]{8,})"]),
    ):
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                evidence[key] = m.group(1).strip()
                break
    return evidence


def parse_clean_report(path: Path, kind: str) -> tuple[bool | None, dict]:
    """Parse an EDA report with exact counts, not substring "clean" logic."""
    if not path.exists():
        return None, {"source": str(path), "status": "missing"}
    text = path.read_text(errors="ignore")
    low = text.lower()
    value: bool | None = None
    if kind == "lvs":
        if re.search(r"\b(?:do not match|not match|mismatch|failed)\b", low):
            value = False
        elif re.search(r"\b(?:match uniquely|circuits match|match exactly)\b", low):
            value = True
    else:
        if re.search(r"\bnot\s+clean\b", low):
            value = False
        else:
            counts: list[int] = []
            for pattern in (
                r"\bviolations?\s*[:=]\s*(\d+)",
                r"(?<![\d.])(\d+)\s+violations?\b",
                r"\b(?:total\s+)?violations?\s+(?:count|found|total)?\s*[:=]?\s*(\d+)",
                r"\bviolation\s+count\s*[:=]?\s*(\d+)",
            ):
                counts.extend(int(m.group(1)) for m in re.finditer(pattern, low))
            if counts:
                value = max(counts) == 0
            elif re.search(r"\bclean\s*[:=]\s*(?:true|yes|pass)\b", low):
                value = True
            elif re.search(r"\bclean\s*[:=]\s*(?:false|no|fail)\b", low):
                value = False
            elif re.search(r"\b(?:failed|failure|error)\b", low):
                value = False
    evidence = _report_evidence(path, text, value)
    if evidence.get("tool_exit_code") not in (None, "0"):
        value = False
        evidence["status"] = "fail"
    # A report that declares an input hash must agree with the package artifact
    # if one is present.  This is the stale-GDS guard for signoff reports.
    return value, evidence


def clean_check(path: Path, kind: str) -> bool | None:
    """Compatibility wrapper for the stricter exact-count parser."""
    return parse_clean_report(path, kind)[0]


def _package_artifacts(package: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for pattern in ("*.gds", "*.gds.gz", "*.spice", "*.sp", "*.cir"):
        for path in package.rglob(pattern):
            if path.is_file():
                h = _sha256(path)
                if h:
                    out[path.name] = h
    return out


def _stale_check(evidence: dict, artifacts: dict[str, str]) -> bool:
    declared = evidence.get("gds_sha256") or evidence.get("netlist_sha256")
    if not declared:
        return False
    declared = declared.lower()
    return all(declared != digest.lower() for digest in artifacts.values())


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

    rtl_path = reports / "rtl_functional.json"
    rtl = load_json(rtl_path)
    for key in ("lint", "sim", "synth"):
        if key in rtl.get("checks", {}):
            checks[key] = rtl["checks"][key]
    evidence: dict[str, dict] = {}
    if rtl_path.is_file():
        evidence["rtl_functional"] = {
            "source": str(rtl_path), "source_sha256": _sha256(rtl_path),
            "input_sha256": _sha256(rtl_path), "status": "parsed",
            "run_status": "parsed", "tool_version": None, "corner": None,
            "gds_sha256": None, "netlist_sha256": None, "log": str(rtl_path),
        }

    for stage in ("pre_pnr", "post_route"):
        for kind in ("setup", "hold"):
            value = parse_timing(reports, stage, kind)
            name = f"{stage}_{kind}_wns"
            if value is not None:
                checks[name] = value
            path = _timing_report(reports, stage, kind)
            if path is not None:
                evidence[name] = _report_evidence(
                    path, path.read_text(errors="ignore"), value is not None
                )

    artifacts = _package_artifacts(package)
    for ev in evidence.values():
        ev.setdefault("artifact_hashes", artifacts)
    for key, filename in (("drc_clean", "drc.rpt"),
                          ("lvs_clean", "lvs.rpt"),
                          ("antenna_clean", "antenna.rpt"),
                          ("cdc_clean", "cdc.rpt")):
        path = reports / filename
        value, ev = parse_clean_report(path, key.split("_")[0])
        ev["artifact_hashes"] = artifacts
        ev["stale_input"] = _stale_check(ev, artifacts)
        evidence[key] = ev
        if value is not None and not ev["stale_input"]:
            checks[key] = value
        elif ev["stale_input"]:
            # Never promote checks from a report tied to another GDS/netlist.
            checks.pop(key, None)

    ber = load_json(reports / "ber.json")
    # Accept both the legacy flat form and the richer verify_link form, which
    # nests the qualification checks under "checks".
    ber_checks = ber.get("checks", {}) if isinstance(ber.get("checks"), dict) else {}
    for key in ("ber", "jtol_pass", "eye_margin_ui"):
        if key in ber:
            checks[key] = ber[key]
        elif key in ber_checks:
            checks[key] = ber_checks[key]

    ber_path = reports / "ber.json"
    if ber_path.is_file():
        evidence["ber"] = {
            "source": str(ber_path), "source_sha256": _sha256(ber_path),
            "input_sha256": _sha256(ber_path), "status": "parsed",
            "run_status": "parsed", "tool_version": None, "corner": None,
            "gds_sha256": None, "netlist_sha256": None, "log": str(ber_path),
        }

    metrics = load_json(reports / "metrics.json")
    manual_checks = metrics.get("checks", {}) if isinstance(metrics.get("checks"), dict) else {}
    manual_ignored: dict[str, str] = {}
    for key, value in manual_checks.items():
        if key in SIGNOFF_CHECKS:
            # Formal signoff checks must come from the tool reports.  A manual
            # metrics.json entry is recorded but never overrides them.
            manual_ignored[key] = "signoff check requires tool-derived evidence"
            continue
        if key in checks:
            manual_ignored[key] = "parsed report takes precedence"
            continue
        checks[key] = value
    evidence["metrics"] = {
        "source": str(reports / "metrics.json"),
        "source_sha256": _sha256(reports / "metrics.json"),
        "ignored": manual_ignored,
        "manual_override": bool(metrics.get("manual_override")),
    }

    manifest = load_json(package / "manifest.json")
    return {
        "package": str(package),
        "design": manifest.get("design"),
        "spec": manifest.get("spec_name"),
        "ber_target": float(ber.get("ber_target", metrics.get("ber_target", 1e-12))),
        "checks": checks,
        "evidence": evidence,
        "manual_overrides": manual_ignored,
        "input_hashes": {
            "manifest_sha256": _sha256(package / "manifest.json"),
            "artifacts": artifacts,
        },
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

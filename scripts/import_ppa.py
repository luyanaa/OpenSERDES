#!/usr/bin/env python3
"""Import measured per-tech PPA into tech/ppa_measured.yaml.

CSV columns:
  tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,confidence,source,area_um2,power_mw,
  provenance_report,provenance_sha256,corner,period_ns,git_commit,tool

- rel_* are normalized within the same tech/period/signoff setup.
- absolute columns are optional traceability data, not used for scoring.
- kind is tx or rx; arch must exist in tech/arch_db.yaml.

Provenance gate (item 3 of the PPA audit): a row claiming
"measured_post_route" or "measured_pre_pnr" must point at a power evidence
record produced by scripts/collect_power.py.  The record's own sha256 must
match provenance_sha256, and its corner/period must agree with the row.
An unproven measured claim is REJECTED unless --allow-unproven is passed, in
which case the confidence is downgraded to "unverified_claim" so it can never
be mistaken for measured data.  gen_ip.py re-checks this at load time.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

CONFIDENCE_VALUES = {
    "measured_post_route", "measured_pre_pnr", "rtl_validated",
    "estimated", "modeled_yosys_cells", "unverified_claim",
}
MEASURED = {"measured_post_route", "measured_pre_pnr"}
PROVENANCE_COLUMNS = ("source", "corner", "period_ns",
                      "provenance_report", "provenance_sha256")


def load_arch_db() -> dict:
    return yaml.safe_load((ROOT / "tech" / "arch_db.yaml").read_text())


def as_float(value: str, field: str, row: int) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SystemExit(f"row {row}: {field} must be numeric, got {value!r}")
    if number <= 0:
        raise SystemExit(f"row {row}: {field} must be positive, got {number}")
    return number


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_provenance(row: dict, index: int) -> dict:
    """Verify a measured claim against a collect_power.py evidence record."""
    missing = [c for c in PROVENANCE_COLUMNS if not row.get(c, "").strip()]
    if missing:
        raise SystemExit(
            f"row {index}: measured PPA claim is missing provenance columns "
            f"{missing}; run scripts/collect_power.py or pass --allow-unproven")
    report = Path(row["provenance_report"].strip()).resolve()
    if not report.is_file():
        raise SystemExit(f"row {index}: provenance report not found: {report}")
    actual = sha256_file(report)
    declared = row["provenance_sha256"].strip().lower()
    if actual != declared:
        raise SystemExit(
            f"row {index}: provenance sha256 mismatch: declared {declared} "
            f"but {report} hashes to {actual}")
    try:
        record = json.loads(report.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"row {index}: provenance report is not JSON: {exc}")
    if record.get("kind") != "librelane-power-evidence":
        raise SystemExit(f"row {index}: provenance report has wrong kind")
    if not record.get("metrics_sha256") or not record.get("power_w"):
        raise SystemExit(f"row {index}: provenance report has no power data")
    if record.get("corner") and row["corner"].strip() != str(record["corner"]):
        raise SystemExit(f"row {index}: corner disagrees with provenance report")
    if record.get("period_ns") is not None:
        if abs(float(row["period_ns"]) - float(record["period_ns"])) > 1e-6:
            raise SystemExit(f"row {index}: period_ns disagrees with provenance report")
    return {
        "verified": True,
        "report": str(report),
        "report_sha256": actual,
        "metrics_sha256": record["metrics_sha256"],
        "power_mw": record.get("power_mw"),
        "corner": row["corner"].strip(),
        "period_ns": float(row["period_ns"]),
        "git_commit": row.get("git_commit", "").strip(),
        "tool": row.get("tool", "").strip(),
        "activity_source": record.get("activity_source"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Import measured PPA CSV into YAML")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default=str(ROOT / "tech" / "ppa_measured.yaml"))
    ap.add_argument("--replace", action="store_true", help="discard existing entries")
    ap.add_argument("--allow-unproven", action="store_true",
                    help="downgrade unproven measured claims to unverified_claim")
    args = ap.parse_args()

    arch_db = load_arch_db()
    out = Path(args.out)
    existing: dict[tuple, dict] = {}
    if out.exists() and not args.replace:
        data = yaml.safe_load(out.read_text()) or {}
        for entry in data.get("entries", []):
            key = (entry["tech"], entry.get("scl", ""), entry["kind"], entry["arch"])
            existing[key] = entry

    rows = list(csv.DictReader(Path(args.csv).open()))
    added = 0
    for index, row in enumerate(rows, start=2):
        tech = row["tech"].strip()
        scl = row.get("scl", "").strip()
        kind = row["kind"].strip()
        arch = row["arch"].strip()
        if kind not in ("tx", "rx"):
            raise SystemExit(f"row {index}: kind must be tx or rx")
        if arch not in arch_db.get(kind, {}):
            raise SystemExit(f"row {index}: unknown {kind} arch {arch!r}")
        if not (ROOT / "tech" / tech / "tech.yaml").exists():
            raise SystemExit(f"row {index}: unknown tech {tech!r}")
        confidence = row.get("confidence", "measured_post_route").strip() or "measured_post_route"
        if confidence not in CONFIDENCE_VALUES:
            raise SystemExit(
                f"row {index}: unknown confidence {confidence!r}; "
                f"allowed: {sorted(CONFIDENCE_VALUES)}")
        provenance = None
        if confidence in MEASURED:
            try:
                provenance = check_provenance(row, index)
            except SystemExit:
                if not args.allow_unproven:
                    raise
                confidence = "unverified_claim"
                provenance = {
                    "verified": False,
                    "warning": "measured claim had no verified provenance; "
                               "downgraded by --allow-unproven",
                }
        entry = {
            "tech": tech,
            "scl": scl,
            "kind": kind,
            "arch": arch,
            "max_line_rate_mbps": as_float(row["max_line_rate_mbps"], "max_line_rate_mbps", index),
            "metrics": {
                "area": as_float(row["rel_area"], "rel_area", index),
                "power": as_float(row["rel_power"], "rel_power", index),
                "latency": as_float(row["rel_latency"], "rel_latency", index),
            },
            "confidence": confidence,
            "source": row.get("source", "").strip(),
        }
        if provenance is not None:
            entry["provenance"] = provenance
        absolute = {}
        for key in ("area_um2", "power_mw"):
            value = row.get(key, "").strip()
            if value:
                absolute[key] = as_float(value, key, index)
        if absolute:
            entry["absolute"] = absolute
        existing[(tech, scl, kind, arch)] = entry
        added += 1

    payload = {"schema": 1, "entries": list(existing.values())}
    out.write_text(yaml.safe_dump(payload, sort_keys=False))
    print(f"wrote {out} ({added} imported, {len(existing)} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

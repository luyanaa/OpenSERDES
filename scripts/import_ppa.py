#!/usr/bin/env python3
"""Import measured per-tech PPA into tech/ppa_measured.yaml.

CSV columns:
  tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,confidence,source,area_um2,power_mw

- rel_* are normalized within the same tech/period/signoff setup.
- absolute columns are optional traceability data, not used for scoring.
- kind is tx or rx; arch must exist in tech/arch_db.yaml.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


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


def main() -> int:
    ap = argparse.ArgumentParser(description="Import measured PPA CSV into YAML")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default=str(ROOT / "tech" / "ppa_measured.yaml"))
    ap.add_argument("--replace", action="store_true", help="discard existing entries")
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
            "confidence": row.get("confidence", "measured_post_route").strip() or "measured_post_route",
            "source": row.get("source", "").strip(),
        }
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

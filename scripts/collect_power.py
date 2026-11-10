#!/usr/bin/env python3
"""Turn a completed LibreLane run into a hash-verifiable power evidence record.

LibreLane / OpenROAD STAPostPNR writes power__* metrics into
final/metrics.json (see openroad/sta/corner.tcl report_power). This script
extracts them with the corner/period from resolved.json and emits a record
that import_ppa.py can hash-verify. It fails closed: a missing metric or an
unreadable report is an error, not a zero or a guess.

Activity caveat: without a SAIF/VCD annotation, OpenSTA report_power uses the
default switching activity. The record says so, so the number must not be
presented as workload-accurate power.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_POWER_KEYS = (
    "power__internal__total",
    "power__switching__total",
    "power__leakage__total",
    "power__total",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def collect(run: Path) -> dict:
    metrics_path = run / "final" / "metrics.json"
    if not metrics_path.is_file():
        raise SystemExit(f"error: no final/metrics.json under {run}")
    metrics = json.loads(metrics_path.read_text())
    missing = [k for k in REQUIRED_POWER_KEYS if k not in metrics]
    if missing:
        raise SystemExit("error: metrics.json is missing power keys: " + str(missing))
    power_w = {k: float(metrics[k]) for k in REQUIRED_POWER_KEYS}
    if power_w["power__total"] <= 0:
        raise SystemExit("error: power__total must be positive")
    resolved = {}
    rp = run / "resolved.json"
    if rp.is_file():
        try:
            resolved = json.loads(rp.read_text()) or {}
        except json.JSONDecodeError:
            pass
    corner = resolved.get("DEFAULT_CORNER") or resolved.get("default_corner")
    period = resolved.get("CLOCK_PERIOD") or resolved.get("clock_period")
    if period is not None:
        period = float(period)
    return {
        "schema": 1,
        "kind": "librelane-power-evidence",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run.resolve()),
        "metrics_path": str(metrics_path.resolve()),
        "metrics_sha256": _sha256(metrics_path),
        "power_w": power_w,
        "power_mw": round(power_w["power__total"] * 1000.0, 6),
        "corner": corner,
        "period_ns": period,
        "pdk": resolved.get("PDK"),
        "scl": resolved.get("STD_CELL_LIBRARY"),
        "git_commit": git_commit(),
        "tool": "LibreLane 3.0.14 / OpenSTA report_power",
        "activity_source": "default (no SAIF/VCD activity annotation)",
        "note": "Default-activity estimate, not workload-accurate or silicon power.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="LibreLane run directory")
    ap.add_argument("--out", required=True, help="output power evidence JSON")
    ap.add_argument("--csv-out", default=None,
                    help="also emit an import_ppa.py-ready CSV row")
    for field in ("tech", "scl", "kind", "arch", "rate", "source"):
        ap.add_argument("--" + field, default=None)
    args = ap.parse_args()
    record = collect(Path(args.run))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + chr(10))
    print(f"wrote {out}")
    if args.csv_out:
        needed = ("tech", "scl", "kind", "arch", "rate", "source")
        missing = [f for f in needed if getattr(args, f) is None]
        if missing:
            raise SystemExit("error: --csv-out requires " + ", ".join("--" + m for m in missing))
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        header = ("tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,"
                  "confidence,source,power_mw,provenance_report,provenance_sha256,"
                  "corner,period_ns,git_commit,tool\n")
        row = ("%s,%s,%s,%s,%s,1.0,1.0,1.0,measured_post_route,%s,%s,%s,%s,%s,%s,%s,%s\n"
               % (args.tech, args.scl, args.kind, args.arch, args.rate, args.source,
                  record["power_mw"], out.resolve(), digest, record["corner"],
                  record["period_ns"], record["git_commit"], record["tool"]))
        csv_path = Path(args.csv_out)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text(header + row)
        print(f"wrote {csv_path} (provenance sha256 {digest[:12]}...)")
    print("  power__total = %s mW  corner=%s  period=%s ns" % (
        record["power_mw"], record["corner"], record["period_ns"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

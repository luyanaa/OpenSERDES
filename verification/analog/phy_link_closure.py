#!/usr/bin/env python3
"""Physical-PHY link closure evidence collector.

Reads a completed LibreLane PnR run for the integrated analog link
(``serdes_analog_link`` -- the soft-stdcell TX -> channel -> RX top) and emits
a single hash-verified evidence record under ``build/verify/phy_link_closure``.
This closes the audit's P2 "physical PHY integration" item by gathering the
per-step signoff verdicts the run already produced into one fail-closed
record, instead of scattering them across LibreLane step dirs.

Gates (all must pass for ``status=SIGNOFF_ELIGIBLE``; otherwise UNVERIFIED):

  * final GDS present and non-empty
  * KLayout XOR difference count == 0
  * Magic DRC error count == 0   and   KLayout DRC error count == 0
  * netgen LVS: zero unmatched devices / nets / pins, zero lvs errors
  * STA: zero setup and hold violations at the design corner
  * route DRC errors == 0, antenna violations == 0, power-grid violations == 0
  * post-route SPEF + extracted SPICE netlist present (real parasitics)

The collector never fabricates a pass: each gate is read from the run's own
``final/metrics.json`` and step ``state_out.json`` records, and every artifact
it cites is sha256-hashed so the report is hash-consistent.  If the run dir or
metrics are missing the record is written with status=UNVERIFIED, matching the
fail-closed contract used by ``macro_pack_3pdk.py``.

This is a *soft-stdcell* integrated link: the analog front-end is the
standard-cell inverter chain documented in ``docs/analog_pnr.md``, not a
transistor custom cell.  The record says so explicitly -- it is a physical
integration signoff, not a custom analog signoff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = ROOT / "build/analog_pnr_sky130/runs/analog_link_signoff"
DEFAULT_OUT = ROOT / "build/verify/phy_link_closure"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_metrics(run: Path) -> dict:
    """Prefer final/metrics.json; fall back to a step state_out.json."""
    for cand in (run / "final" / "metrics.json",
                 run / "71-checker-lvs" / "state_out.json",
                 run / "67-checker-klayoutdrc" / "state_out.json"):
        if cand.is_file():
            try:
                d = json.loads(cand.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(d, dict) and "metrics" in d:
                return d["metrics"]
            if isinstance(d, dict):
                return d
    return {}


def _m(metrics: dict, key: str) -> float:
    v = metrics.get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def collect(run: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    metrics = _load_metrics(run)

    views: dict[str, dict] = {}
    artifacts = {
        "gds":    run / "final/gds/serdes_analog_link.gds",
        "spef":   run / "final/spef/max/serdes_analog_link.max.spef",
        "spice":  run / "final/spice/serdes_analog_link.spice",
        "netlist": run / "final/nl/serdes_analog_link.nl.v",
        "lef":    run / "final/lef/serdes_analog_link.lef",
        "def":    run / "final/def/serdes_analog_link.def",
        "odb":    run / "final/odb/serdes_analog_link.odb",
    }
    for name, p in artifacts.items():
        if p.is_file() and p.stat().st_size > 0:
            dst = out / p.name
            dst.write_bytes(p.read_bytes())
            views[name] = {"status": "available", "path": p.name,
                           "sha256": _sha256(p), "bytes": p.stat().st_size}
        else:
            views[name] = {"status": "missing"}

    g = lambda k: _m(metrics, k) == 0.0
    checks = {
        "gds_present":          views["gds"]["status"] == "available",
        "klayout_xor_zero":     g("design__xor_difference__count"),
        "magic_drc_zero":       g("magic__drc_error__count"),
        "klayout_drc_zero":     g("klayout__drc_error__count"),
        "lvs_unmatched_dev0":   g("design__lvs_unmatched_device__count"),
        "lvs_unmatched_net0":   g("design__lvs_unmatched_net__count"),
        "lvs_unmatched_pin0":   g("design__lvs_unmatched_pin__count"),
        "lvs_error_zero":       g("design__lvs_error__count"),
        "setup_vio_zero":       g("timing__setup_vio__count"),
        "hold_vio_zero":        g("timing__hold_vio__count"),
        "route_drc_zero":       g("route__drc_errors"),
        "antenna_vio_zero":     g("route__antenna_violation__count"),
        "powergrid_vio_zero":   g("design__power_grid_violation__count"),
        "flow_errors_zero":     g("flow__errors__count"),
        "spef_present":         views["spef"]["status"] == "available",
        "spice_present":        views["spice"]["status"] == "available",
    }

    problems = [k for k, ok in checks.items() if not ok]
    if not metrics:
        problems.append("metrics_unreadable")
    status = "SIGNOFF_ELIGIBLE" if not problems else "UNVERIFIED"

    record = {
        "kind": "phy_link_closure",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "design": "serdes_analog_link",
        "flow": "LibreLane soft-stdcell PnR (TX inv-chain -> channel -> RX inv)",
        "pdk": "sky130",
        "run_dir": str(run),
        "implementation": (
            "soft-stdcell inverter chain per docs/analog_pnr.md; a physical "
            "integration signoff, not a custom transistor analog signoff"),
        "signoff_metrics": {
            "xor_difference":   metrics.get("design__xor_difference__count"),
            "magic_drc_errors": metrics.get("magic__drc_error__count"),
            "klayout_drc_errors": metrics.get("klayout__drc_error__count"),
            "lvs_unmatched_dev": metrics.get("design__lvs_unmatched_device__count"),
            "lvs_unmatched_net": metrics.get("design__lvs_unmatched_net__count"),
            "lvs_unmatched_pin": metrics.get("design__lvs_unmatched_pin__count"),
            "setup_wns_ns":      metrics.get("timing__setup__ws"),
            "hold_wns_ns":       metrics.get("timing__hold__ws"),
            "setup_vio":         metrics.get("timing__setup_vio__count"),
            "hold_vio":          metrics.get("timing__hold_vio__count"),
            "max_slew_vio":      metrics.get("design__max_slew_violation__count"),
            "max_cap_vio":       metrics.get("design__max_cap_violation__count"),
            "ir_drop_worst_v":   metrics.get("ir__drop__worst"),
            "route_wirelength":  metrics.get("route__wirelength"),
            "utilization":       metrics.get("design__instance__utilization"),
        },
        "checks": checks,
        "views": views,
        "problems": problems,
        "status": status,
    }
    # Hash the record (minus the hash field itself) for integrity.
    rec_for_hash = {k: v for k, v in record.items()}
    record["evidence_hash"] = hashlib.sha256(
        json.dumps(rec_for_hash, sort_keys=True).encode()).hexdigest()
    (out / "phy_link_closure.json").write_text(
        json.dumps(record, indent=2) + "\n")
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default=str(DEFAULT_RUN),
                    help="LibreLane run dir for serdes_analog_link")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="output dir for the closure evidence record")
    args = ap.parse_args()

    rec = collect(Path(args.run), Path(args.out))
    print(f"phy_link_closure {rec['design']}: {rec['status']}")
    for k, ok in rec["checks"].items():
        if not ok:
            print(f"  FAIL {k}")
    print(f"  views: {[n for n,v in rec['views'].items() if v['status']=='available']}")
    print(f"  wrote {args.out}/phy_link_closure.json")
    return 0 if rec["status"] == "SIGNOFF_ELIGIBLE" else 1


if __name__ == "__main__":
    sys.exit(main())

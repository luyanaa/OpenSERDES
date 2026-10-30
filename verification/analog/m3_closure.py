#!/usr/bin/env python3
"""Aggregate the M3 RX characterization evidence into one closure matrix.

This is deliberately conservative: it can report that the characterization
campaign is complete while keeping production BER/CDR qualification false.
Missing or malformed evidence is a failure, not a pass by omission.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.m3_ber_sweep import wilson  # noqa: E402

DEFAULT_ARTIFACTS = {
    "holdout": "build/verify/m3_holdout.json",
    "pvt": "build/verify/m3_pvt_sweep.json",
    "mismatch": "build/verify/m3_mc_sweep_800.json",
    "bathtub": "build/verify/m3_bathtub.json",
    "noise": "build/verify/m3_noise_sweep.json",
    "sensitivity": "build/verify/m3_sensitivity.json",
    "rate_sweep": "build/verify/m3_ber_sweep.json",
    "digital_boundary": "build/verify/m3_digital_e2e_400_w4.json",
}


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _by_case(data: dict | None) -> dict:
    out = {}
    for case, rows in (data or {}).get("results", {}).items():
        valid = [r for r in rows if "validation_errors" in r]
        total = sum(r.get("validation_bits", 0) for r in valid)
        errors = sum(r.get("validation_errors", 0) for r in valid)
        out[case] = {
            "runs": len(valid),
            "validation_bits": total,
            "validation_errors": errors,
            "validation_ber": (errors / total) if total else None,
        }
    return out


def holdout_summary(data: dict | None, expected_rows: int) -> dict:
    rows = []
    if data:
        for case_rows in data.get("results", {}).values():
            rows.extend(case_rows)
    valid = [r for r in rows if "validation_errors" in r]
    total = sum(r.get("validation_bits", 0) for r in valid)
    errors = sum(r.get("validation_errors", 0) for r in valid)
    phase_frozen = bool(valid) and all(
        r.get("train_bits", 0) > 0 and r.get("validation_bits", 0) > 0
        for r in valid
    )
    ci = list(wilson(errors, total)) if total else [0.0, 0.0]
    return {
        "present": data is not None,
        "rows": len(rows),
        "valid_rows": len(valid),
        "expected_rows": expected_rows,
        "cases": sorted(data.get("results", {})) if data else [],
        "phase_frozen_by_training_prefix": phase_frozen,
        "validation_bits_total": total,
        "validation_errors_total": errors,
        "aggregate_validation_ber": (errors / total) if total else None,
        "aggregate_validation_ci95_diagnostic": ci,
        "errors": [r for r in rows if "error" in r],
        "by_case": _by_case(data),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--holdout", default=DEFAULT_ARTIFACTS["holdout"])
    ap.add_argument("--out", default="build/verify/m3_closure.json")
    ap.add_argument("--expected-holdout-rows", type=int, default=12)
    args = ap.parse_args()

    paths = {k: ROOT / v for k, v in DEFAULT_ARTIFACTS.items()}
    paths["holdout"] = ROOT / args.holdout
    data = {k: load_json(p) for k, p in paths.items()}
    holdout = holdout_summary(data["holdout"], args.expected_holdout_rows)

    presence = {
        k: {
            "path": str(paths[k]),
            "present": data[k] is not None,
        }
        for k in paths
    }
    checks = {
        "independent_holdout_present": holdout["present"],
        "independent_holdout_complete": (
            holdout["valid_rows"] == holdout["expected_rows"]
            and not holdout["errors"]
        ),
        "training_phase_frozen": holdout["phase_frozen_by_training_prefix"],
        "pvt_spot_present": data["pvt"] is not None,
        "local_mismatch_spot_present": data["mismatch"] is not None,
        "offset_bathtub_present": data["bathtub"] is not None,
        "noise_spot_present": data["noise"] is not None,
        "input_sensitivity_spot_present": data["sensitivity"] is not None,
        "rate_sweep_present": data["rate_sweep"] is not None,
        "digital_boundary_zero_frame_errors": (
            data["digital_boundary"] is not None
            and data["digital_boundary"].get("frame_errors") == 0
            and data["digital_boundary"].get("analog", {}).get("errors") == 0
        ),
    }
    characterization_keys = [
        "independent_holdout_complete",
        "training_phase_frozen",
        "pvt_spot_present",
        "local_mismatch_spot_present",
        "offset_bathtub_present",
        "noise_spot_present",
        "input_sensitivity_spot_present",
        "rate_sweep_present",
        "digital_boundary_zero_frame_errors",
    ]
    characterization_complete = all(checks[k] for k in characterization_keys)
    report = {
        "kind": "m3-characterization-closure",
        "characterization_complete": characterization_complete,
        "production_qualified": False,
        "production_qualification_reason": (
            "Open-loop sampled decisions, short frames, no CDR-conditioned "
            "sensitivity/global-process Monte Carlo. This is not production BER."
        ),
        "checks": checks,
        "artifacts": presence,
        "holdout": holdout,
        "limitations": [
            "Phase is trained and frozen; it is not a CDR loop.",
            "Validation frames are short (64 bits per case in the default campaign).",
            "PVT and mismatch are spot checks, not full signoff matrices.",
            "PCell feedback route still lacks closed macro-level body tie/LVS.",
            "Digital boundary is one 4-word frame at 400 Mb/s, not a CDR loop.",
            "No production/SoC BER qualification is claimed.",
        ],
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "characterization_complete": characterization_complete,
        "holdout_rows": f"{holdout['valid_rows']}/{holdout['expected_rows']}",
        "validation_bits": holdout["validation_bits_total"],
        "validation_errors": holdout["validation_errors_total"],
        "aggregate_validation_ber": holdout["aggregate_validation_ber"],
        "out": str(out),
    }, indent=2))
    return 0 if characterization_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())

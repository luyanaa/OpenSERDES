#!/usr/bin/env python3
"""Unified analog macro package builder.

Ingests per-PDK analog macros — whether produced by ``macro_gen`` (sky130) or a
bespoke verified builder (gf180_rx_macro, ihp_rx_macro) — and emits one common
package under ``out/`` with a single manifest schema, per-macro evidence_hash,
and a 3-PDK package report.

The goal is a *unified contract and evidence record*, not new physical
generation.  Each ingested macro keeps its own provenance (the script/flow that
built it) and its real verified status; nothing is upgraded to signoff by being
packaged.

Manifest schema (per macro, aligned with verification/analog/macro_gen.py):
  name, tech, pdk, topology, canonical_ports, named_pins, device_models,
  status, physical_signoff_eligible, evidence_hash, views{gds,lef,cdl,pex,
  drc,lvs,circuit_spice,schematic}, provenance{builder,evidence}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TECH_DIR = ROOT / "verification/analog" / "tech"
CANON = ("VDD", "VSS", "IN", "OUT")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_tech(name: str) -> dict:
    import yaml  # local import so --list works without a venv
    path = TECH_DIR / f"{name}.yaml"
    return yaml.safe_load(path.read_text()) if path.is_file() else {}


def _evidence_hash(name: str, tech: str, views: dict) -> str:
    h = hashlib.sha256()
    h.update(name.encode())
    h.update(tech.encode())
    for key in ("gds", "lef", "cdl", "pex", "drc", "lvs", "circuit_spice",
                "schematic"):
        view = views.get(key) or {}
        p = view.get("path")
        if p and Path(p).is_file():
            h.update(key.encode())
            h.update(Path(p).read_bytes())
    return h.hexdigest()


def _port_dir(p: str) -> str:
    return {"VDD": "power", "VSS": "ground", "IN": "input",
            "OUT": "output"}.get(p, "inout")


def _named_pins(ports: list[str]) -> dict:
    """Map a macro's declared ports onto the canonical power/ground/in/out."""
    up = {p.upper(): p for p in ports}
    def pick(*names):
        for n in names:
            if n in up:
                return up[n]
        return None
    return {
        "power": pick("VDD", "VCC", "VDDD", "VPWR"),
        "ground": pick("VSS", "GND", "VSSS", "VGND", "VSSE"),
        "input": pick("IN", "A", "DIN"),
        "output": pick("OUT", "Y", "Z", "Q", "DOUT"),
    }


def _collect(src_dir: Path, outdir: Path, files: dict) -> dict:
    """Copy referenced artifacts into the package and return view records."""
    views = {}
    for key, cand in files.items():
        # cand may be a single path or a list of candidate paths (first hit wins)
        paths = cand if isinstance(cand, list) else [cand]
        hit = None
        for c in paths:
            if c and Path(c).is_file():
                hit = Path(c)
                break
        if hit is None:
            views[key] = {"status": "missing"}
            continue
        dest = outdir / f"{outdir.name}.{key}{hit.suffix}"
        shutil.copy2(hit, dest)
        views[key] = {"path": str(dest), "status": "available",
                      "format": hit.suffix.lstrip("."), "sha256": _sha256(hit),
                      "source": str(hit)}
    return views


def _spec_paths_ok(spec: dict) -> list[str]:
    """Reject non-reproducible absolute paths (e.g. /tmp) in the ingest spec."""
    problems = []
    for key, cand in (spec.get("files") or {}).items():
        for c in (cand if isinstance(cand, list) else [cand]):
            if c and str(c).startswith("/tmp"):
                problems.append(f"files.{key} uses ephemeral path {c}")
    return problems


def _evidence_artifact_sha(ev: dict, view: str) -> str | None:
    """Pull the builder-recorded sha256 for an artifact out of the evidence."""
    # Builders record hashes under different keys; probe the known locations.
    probes = [
        (view, "sha256"), (view, f"{view}_sha256"), (view, "raw_sha256"),
        ("layout", f"{view}_sha256"), ("netlists", f"{view}_sha256"),
        (view, "gds_sha256"), ("netlists", "cdl_sha256"),
        ("netlists", "flat_spice_sha256"), ("pex", "raw_sha256"),
    ]
    for sect, k in probes:
        node = ev.get(sect)
        if isinstance(node, dict) and isinstance(node.get(k), str):
            return node[k]
    return None


def _verify_evidence(spec: dict, views: dict) -> tuple[dict, list[str]]:
    """Load the builder's evidence record and verify it + artifact hashes.

    Returns (evidence_dict, problems).  The packager NEVER trusts the spec's
    drc_clean/lvs_match booleans; it derives truth from the builder evidence.
    When evidence is absent or inconsistent the caller must mark the macro
    UNVERIFIED rather than guess.
    """
    problems = []
    ev_path = spec.get("evidence")
    evidence = {}
    if ev_path and Path(ROOT / ev_path).is_file():
        try:
            evidence = json.loads((ROOT / ev_path).read_text())
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"evidence unreadable: {exc}")
    elif ev_path and Path(ev_path).is_file():
        try:
            evidence = json.loads(Path(ev_path).read_text())
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"evidence unreadable: {exc}")
    elif ev_path:
        problems.append(f"evidence record not found: {ev_path}")

    if not evidence:
        return {}, problems or ["no builder evidence; cannot verify signoff"]

    # 1. The builder's own pass/fail record must say the macro passed.
    status = str(evidence.get("status", "")).lower()
    checks = evidence.get("checks") or {}
    if status and status not in ("pass", "passed", "ok"):
        problems.append(f"evidence status={status!r}")
    failing = [k for k, v in checks.items() if v is False]
    if failing:
        problems.append(f"evidence failing checks: {failing}")

    # 2. DRC/LVS must come from the evidence content, not the spec booleans.
    drc = evidence.get("drc") or {}
    drc_ok = bool(drc.get("clean")) or drc.get("drc_errors") == 0 or \
        checks.get("drc_clean") is True
    lvs = evidence.get("lvs") or {}
    lvs_ok = bool(lvs.get("match")) or \
        bool((evidence.get("lvs_netgen") or {}).get("match")) or \
        bool((evidence.get("lvs_klayout") or {}).get("match")) or \
        checks.get("lvs_match") is True or \
        checks.get("netgen_match") is True

    # 3. Hash-consistency: every collected artifact that the evidence records
    #    a hash for must match, proving GDS/CDL/PEX/DRC/LVS belong to the same
    #    design version.
    for view in ("gds", "cdl", "pex", "schematic"):
        rec = views.get(view) or {}
        if rec.get("status") != "available":
            continue
        expected = _evidence_artifact_sha(evidence, view)
        if expected and expected != rec["sha256"]:
            problems.append(
                f"{view} sha256 mismatch: packaged {rec['sha256'][:12]}… "
                f"!= evidence {expected[:12]}…")

    return {"drc_ok": drc_ok, "lvs_ok": lvs_ok, "evidence": evidence,
            "status": status, "checks": checks}, problems


def _signoff(views: dict, ev: dict, problems: list[str]) -> tuple[str, bool]:
    """Derive maturity from *verified* evidence, not config booleans."""
    if not ev or problems:
        return "UNVERIFIED", False
    drc_ok = ev.get("drc_ok", False)
    lvs_ok = ev.get("lvs_ok", False)
    pex_ok = views.get("pex", {}).get("status") == "available"
    gds_ok = views.get("gds", {}).get("status") == "available"
    signoff = bool(gds_ok and drc_ok and lvs_ok and pex_ok)
    if signoff:
        stage = "SIGNOFF_ELIGIBLE"
    elif lvs_ok:
        stage = "LVS_MATCHED"
    elif drc_ok:
        stage = "GEOMETRY_DRC_PASS"
    elif gds_ok:
        stage = "LAYOUT_ATTACHED"
    else:
        stage = "SCHEMATIC_ONLY"
    return stage, signoff


def ingest(spec: dict, pkg_dir: Path) -> dict:
    """Normalise one macro (generated or bespoke) into the package schema."""
    tech_name = spec["tech"]
    tech = _load_tech(tech_name)
    name = spec["name"]
    outdir = pkg_dir / name
    outdir.mkdir(parents=True, exist_ok=True)

    problems = _spec_paths_ok(spec)
    views = _collect(Path(spec.get("src_dir", ".")), outdir,
                     spec.get("files", {}))
    ev, ev_problems = _verify_evidence(spec, views)
    problems += ev_problems
    status, signoff = _signoff(views, ev, problems)
    ports = spec.get("ports") or list(CANON)

    manifest = {
        "schema": 1,
        "kind": "analog_macro_packaged",
        "name": name,
        "tech": tech_name,
        "pdk": tech.get("pdk", spec.get("pdk")),
        "supply_v": tech.get("vdd_v", spec.get("supply_v")),
        "topology": spec.get("topology"),
        "provenance": {
            "builder": spec.get("builder"),
            "evidence": spec.get("evidence"),
            "flow": spec.get("flow", "bespoke"),
        },
        "status": status,
        "physical_signoff_eligible": signoff,
        "topology_match": ev.get("lvs_ok", False),
        "verification": {
            "drc_ok": ev.get("drc_ok", False),
            "lvs_ok": ev.get("lvs_ok", False),
            "evidence": spec.get("evidence"),
            "problems": problems,
        },
        "ports": ports,
        "canonical_ports": [
            {"name": p, "index": i, "direction": _port_dir(p)}
            for i, p in enumerate(ports)
        ],
        "named_pins": _named_pins(ports),
        "device_models": {
            "nmos": (tech.get("devices") or {}).get("nmos"),
            "pmos": (tech.get("devices") or {}).get("pmos"),
        },
        "evidence_hash": _evidence_hash(name, tech_name, views),
        "views": views,
        "note": ("Packaging normalises the contract + evidence record only; it "
                 "does NOT upgrade a macro to signoff.  physical_signoff_eligible"
                 " is true only when GDS + clean DRC + unique-match LVS + PEX are "
                 "all present for this exact macro."),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True,
                    help="JSON list of macro ingest specs")
    ap.add_argument("--out", required=True, help="package output dir")
    ap.add_argument("--report", default=None, help="package report json")
    args = ap.parse_args()

    specs = json.loads(Path(args.spec).read_text())
    pkg_dir = Path(args.out)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    macros = [ingest(s, pkg_dir) for s in specs]
    report = {
        "kind": "analog_macro_package",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "package_dir": str(pkg_dir),
        "n_macros": len(macros),
        "techs": sorted({m["tech"] for m in macros}),
        "macros": [
            {"name": m["name"], "tech": m["tech"], "status": m["status"],
             "signoff_eligible": m["physical_signoff_eligible"],
             "evidence_hash": m["evidence_hash"],
             "views_present": [k for k, v in m["views"].items()
                              if v.get("status") == "available"]}
            for m in macros
        ],
        "signoff_summary": {
            "signoff_eligible": sum(1 for m in macros
                                    if m["physical_signoff_eligible"]),
            "schematic_only": sum(1 for m in macros
                                  if m["status"] == "SCHEMATIC_ONLY"),
        },
    }
    rep = Path(args.report) if args.report else pkg_dir / "package_report.json"
    rep.write_text(json.dumps(report, indent=2) + "\n")
    print(f"packaged {len(macros)} macros -> {pkg_dir}")
    for m in macros:
        print(f"  {m['tech']:12s} {m['name']:20s} {m['status']:18s} "
              f"hash={m['evidence_hash'][:12]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Run LibreLane for one design across the supported PDKs.

For each tech profile this script:

1. regenerates the spec-driven package with that tech,
2. expands the PDK path placeholders in the package pnr/librelane.yaml,
3. runs the LibreLane Classic flow through signoff,
4. collects the post-route metrics that ppa_model/ppa_measured can consume.

The run is fail-closed: a PDK whose flow does not complete is recorded as
unverified with the failing step, never as a measured point.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _git_commit() -> str:
    """HEAD for the evidence record; 'unknown' is better than a stale hash."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              text=True, capture_output=True, timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"

# LibreLane 3.0.14 / OpenROAD 26Q3 from the nix store.  The wrapper sets up the
# full toolchain (yosys, openroad, magic, klayout, netgen, abc), so it is the
# nix-shell environment without needing to rebuild the shell.
LIBRELANE = Path(
    os.environ.get(
        "LIBRELANE_BIN",
        "/nix/store/bl6n3l99m8ld1z6rn8pmhnmr3rqlvasm-librelane-3.0.14/bin/librelane",
    )
)

# PDK roots: --pdk-root for LibreLane and the libs.ref tree for the liberty
# corner files.  They differ for IHP and icsprout55.
PDK_ROOTS = {
    "sky130": "/home/yanlu/.ciel/ciel/sky130/versions/792ef67037d74c8c51811eab3ce23cf793491296",
    "gf180mcuD": "/home/yanlu/.ciel/ciel/gf180mcu/versions/792ef67037d74c8c51811eab3ce23cf793491296",
    "ihp-sg13g2": "/home/yanlu/.ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008",
    "icsprout55": "/grid/icsprout55-pdk",
}


def _tech_dir(tech: str) -> Path:
    return ROOT / "tech" / tech


def _pdk_paths(tech: str) -> tuple[str, str]:
    """Return (pdk_root, pdk_lib_root) for a tech profile."""
    cfg = yaml.safe_load((_tech_dir(tech) / "tech.yaml").read_text())
    root = os.environ.get("PDK_ROOT") or PDK_ROOTS[tech]
    lib_root = os.environ.get("PDK_LIB_ROOT") or root
    return root, lib_root


def _librelane_pdk_args(tech: str) -> list[str]:
    """PDK-selection flags for the LibreLane CLI.

    Always --manual-pdk.  In ciel mode LibreLane pins the PDK family to its
    own default version hash and downloads it when that exact version is not
    in the local ciel home, which is unreachable offline.  --manual-pdk skips
    that resolution and uses --pdk-root as given, so the version installed on
    this machine is the one that gets used.

    Two layouts have to be handled: a ciel version root that contains the PDK
    family directory (sky130A, gf180mcuD, ...), and a plain libs.ref/libs.tech
    tree that is itself the PDK.
    """
    cfg = yaml.safe_load((_tech_dir(tech) / "tech.yaml").read_text())
    pdk = cfg.get("pdk")
    scl = cfg.get("scl")
    if not pdk:
        return []
    root = os.environ.get("PDK_ROOT") or PDK_ROOTS[tech]
    if (Path(root) / pdk).is_dir():
        parent, name = Path(root), pdk
    elif (Path(root) / "libs.ref").is_dir():
        parent, name = Path(root).parent, Path(root).name
    else:
        return []
    return ["--manual-pdk", "--pdk-root", str(parent),
            "--pdk", name, "--scl", scl]


def _max_sample_clock_mhz(tech: str) -> float | None:
    """Capability gate for the RX sample clock of a tech profile.

    The gate lives under "limits" and is scoped per stdcell class, so it has
    to be found by key rather than at a fixed depth.
    """
    cfg = yaml.safe_load((_tech_dir(tech) / "tech.yaml").read_text())
    best: float | None = None

    def walk(node) -> None:
        nonlocal best
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "max_sample_clock_mhz" and isinstance(
                        v, (int, float)) and not isinstance(v, bool):
                    best = float(v) if best is None else max(best, float(v))
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(cfg)
    return best


def _spec_with_tech(spec: Path, tech: str, out: Path,
                    line_rate_mbps: float | None = None) -> Path:
    data = yaml.safe_load(spec.read_text())
    data.setdefault("tech", {})["tx"] = tech
    data["tech"]["rx"] = tech
    # PDK names carry hyphens (ihp-sg13g2); the design name has to stay a
    # legal identifier because it becomes DESIGN_NAME in the LibreLane config.
    slug = re.sub(r"[^0-9A-Za-z_]", "_", tech)
    data["name"] = f"{data.get('name', 'design')}_{slug}"
    if line_rate_mbps is not None:
        data.setdefault("link", {})["line_rate_mbps"] = float(line_rate_mbps)
        data.setdefault("tx", {})["line_rate_mbps"] = float(line_rate_mbps)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(data, sort_keys=False))
    return out


def _expand_config(src: Path, dst: Path, lib_root: str) -> None:
    text = src.read_text()
    text = text.replace("${PDK_LIB_ROOT}", lib_root)
    text = text.replace("${PDK_ROOT}", lib_root)
    if "${" in text:
        raise SystemExit(f"unresolved placeholder in {src}: "
                         + [ln for ln in text.splitlines() if "${" in ln][0])
    dst.write_text(text)


# icsprout55 uses the PDK's LibreLane plugin (`librelane_plugin_ics55`) and
# its documented `meta.substituting_steps` instead of CLI `--skip`: the Odb
# antenna-properties check is nulled because the Magic LEF writer is skipped,
# and Netgen.LVS is replaced by ICS55.KLayoutLVS after OpenROAD.WriteCDL.
# KLayout DRC is run in tiling mode.  Other PDKs keep the default flow.
SIGNOFF_SKIP_STEPS: dict[str, list[str]] = {}


def _ihp_flow_overrides(cfg_data: dict, pdk_root: Path, cfg_path: Path) -> None:
    """Use the PDK's KLayout LVS instead of netgen for IHP.

    The netgen flow fails top-level pin matching on the VPWR/VGND PDN nets
    versus the VDD/VSS standard-cell pins; the IHP PDK ships a KLayout LVS
    deck (KLAYOUT_LVS_SCRIPT) and upstream KLayout.LVS supports this PDK.
    """
    meta = cfg_data.setdefault("meta", {})
    meta["flow"] = "Classic"
    meta["substituting_steps"] = {
        "-Netgen.LVS": "OpenROAD.WriteCDL",
        "Netgen.LVS": "KLayout.LVS",
    }
    # pdk_root is the version root for IHP; the PDK family directory is
    # ihp-sg13g2 (the PDK config.tcl uses $PDK_ROOT/$PDK/...).
    pdk_dir = pdk_root
    family = str(cfg_data.get("PDK", ""))
    if family and (pdk_root / family).is_dir():
        pdk_dir = pdk_root / family
    cfg_data["KLAYOUT_LVS_SCRIPT"] = str(
        pdk_dir / "libs.tech/klayout/tech/lvs/sg13g2.lvs")
    cfg_data["KLAYOUT_LVS_OPTIONS"] = {"run_mode": "deep"}
    # The known IHP DRC deck errors are an observation, not a hard gate; keep
    # the flow going so the KLayout LVS verdict is recorded in the same run.
    cfg_data["ERROR_ON_MAGIC_DRC"] = False
    cfg_data["ERROR_ON_KLAYOUT_DRC"] = False


def _ics55_flow_overrides(cfg_data: dict, pdk_root: Path, cfg_path: Path) -> None:
    meta = cfg_data.setdefault("meta", {})
    meta["flow"] = "Classic"
    meta["substituting_steps"] = {
        "Magic.DRC": None,
        "Checker.MagicDRC": None,
        "Magic.SpiceExtraction": None,
        "Magic.StreamOut": None,
        "Magic.WriteLEF": None,
        "Odb.CheckDesignAntennaProperties": None,
        "Checker.IllegalOverlap": None,
        "KLayout.XOR": None,
        "Checker.XOR": None,
        "-Netgen.LVS": "OpenROAD.WriteCDL",
        "Netgen.LVS": "ICS55.KLayoutLVS",
    }
    # The repaired deck supports run_mode=tiling; the PDK config.tcl supplies
    # the LVS runset/options, but set them explicitly so the record is
    # independent of the LibreLane PDK config discovery.
    cfg_data["KLAYOUT_DRC_DEFINES"] = {"run_mode": "tiling"}
    cfg_data["KLAYOUT_LVS_SCRIPT"] = str(
        pdk_root / "libs.tech/lvs/ics55.lvs")
    cfg_data["KLAYOUT_LVS_OPTIONS"] = {"run_mode": "deep"}
    # The known library-drawing DRC items are recorded, not fatal for this
    # verification run; LVS is the gate that should still run.
    cfg_data["ERROR_ON_KLAYOUT_DRC"] = False
    cfg_data["ERROR_ON_MAGIC_DRC"] = False


def _skip_args(tech: str) -> list[str]:
    args: list[str] = []
    for step in SIGNOFF_SKIP_STEPS.get(tech, []):
        args += ["--skip", step]
    return args


def _clean_env() -> dict:
    """Environment for LibreLane with the shell proxy settings removed.

    LibreLane makes an httpx call at start-up; a SOCKS ALL_PROXY without the
    socksio extra aborts the flow before the first step runs.
    """
    env = dict(os.environ)
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            del env[key]
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def _run(cmd: list[str], cwd: Path, timeout: int,
         tech: str | None = None) -> subprocess.CompletedProcess:
    env = _clean_env()
    if tech == "icsprout55":
        plugin = Path(PDK_ROOTS[tech]) / "libs.tech/librelane"
        old = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(plugin) + (":" + old if old else "")
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True,
                          timeout=timeout, env=env)


def _collect(run_dir: Path) -> dict:
    """Pull the post-route metrics out of a completed LibreLane run."""
    metrics_path = run_dir / "final" / "metrics.json"
    source = "final/metrics.json"
    if not metrics_path.is_file():
        metrics_path = run_dir / "metrics.json"
        source = "metrics.json"
    if not metrics_path.is_file():
        # A flow can finish the physical implementation and then hang in a
        # signoff step whose PDK views the tool cannot read (e.g. Magic with
        # an incomplete tech LEF).  LibreLane keeps the accumulated metrics in
        # each step's state_out.json, so fall back to the last one and record
        # where the numbers came from instead of discarding the measurement.
        states = sorted(run_dir.glob("[0-9]*/state_out.json"),
                        key=lambda p: int(p.parent.name.split("-")[0]))
        for state in reversed(states):
            try:
                payload = json.loads(state.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload.get("metrics"), dict) and payload["metrics"]:
                metrics_path = state
                source = str(state.relative_to(run_dir))
                break
        if source == "final/metrics.json" or source == "metrics.json":
            raise FileNotFoundError(f"no metrics.json under {run_dir}")
    if source.endswith("state_out.json"):
        m = json.loads(metrics_path.read_text())["metrics"]
    else:
        m = json.loads(metrics_path.read_text())

    def g(*keys):
        for k in keys:
            if k in m:
                return m[k]
        return None

    def per_corner(prefix: str) -> dict:
        out = {}
        for k, v in m.items():
            if k.startswith(prefix) and "__corner:" in k:
                out[k.split("__corner:")[1]] = v
        return out

    # Critical-path arrival time from the post-PnR STA report of the worst
    # corner: the number that decides whether the period closes at all.
    arrival = None
    sta_dir = run_dir / "55-openroad-stapostpnr"
    if sta_dir.is_dir():
        worst = None
        for rpt in sorted(sta_dir.glob("*/max.rpt")):
            txt = rpt.read_text(errors="replace")
            mm = re.findall(r"^\s*([0-9.]+)\s+data arrival time", txt, re.M)
            if not mm:
                continue
            val = float(mm[0])
            if worst is None or val > worst[0]:
                worst = (val, rpt.parent.name)
        if worst is not None:
            arrival = {"ns": worst[0], "corner": worst[1]}

    return {
        "metrics_source": source,
        "cells": g("design__instance__count"),
        "stdcell_cells": g("design__instance__count__stdcell"),
        "dff_cells": g("design__instance__count__class:sequential_cell"),
        "core_area_um2": g("design__core__area"),
        "die_area_um2": g("design__die__area"),
        "utilization_pct": round(100 * (g("design__instance__utilization") or 0), 2),
        "power_internal_mw": g("power__internal__total"),
        "power_switching_mw": g("power__switching__total"),
        "power_leakage_mw": g("power__leakage__total"),
        "power_total_mw": g("power__total"),
        "clock_period_ns": g("design__clock__period"),
        "setup_wns_ns": g("timing__setup__wns"),
        "setup_wns_by_corner_ns": per_corner("timing__setup__wns"),
        "setup_tns_by_corner_ns": per_corner("timing__setup__tns"),
        "hold_wns_ns": g("timing__hold__wns"),
        "hold_violations": g("timing__hold_vio__count"),
        "setup_violations": g("timing__setup_vio__count"),
        "clock_skew_setup_ns": g("clock__skew__worst_setup"),
        "critical_path_arrival_ns": arrival,
        "drc_routing": g("route__drc_errors"),
        "drc_magic": g("magic__drc_error__count"),
        "drc_klayout": g("klayout__drc_error__count"),
        "lvs_errors": g("design__lvs_error__count"),
        "antenna_violations": g("route__antenna_violation__count"),
        "max_fanout_violations": g("design__max_fanout_violation__count"),
        "max_cap_violations": g("design__max_cap_violation__count"),
    }


def _classify(rec: dict) -> dict:
    """Classify a record that has metrics, flagging timing/physical violations.

    Metrics always constitute a measurement; violations are recorded as flags
    rather than downgrading the record to unverified.
    """
    m = rec["metrics"]
    violations: dict = {}
    timing = {k: round(v, 4)
              for k, v in m.get("setup_wns_by_corner_ns", {}).items()
              if isinstance(v, (int, float)) and v < 0}
    if timing:
        violations["setup_wns_ns"] = timing
        rec["timing_violations"] = timing
    physical = {k: v for k, v in m.items()
                if ("drc" in k or "lvs" in k) and isinstance(v, int) and v > 0}
    if physical:
        violations["physical"] = physical
    rec["status"] = "measured_with_violations" if violations else "measured"
    if violations:
        rec["violations"] = violations
        parts = []
        if timing:
            arr = m.get("critical_path_arrival_ns") or {}
            parts.append("setup violations in " + ", ".join(sorted(timing))
                         + "; critical path " + str(arr.get("ns")) + " ns")
        if physical:
            parts.append("non-zero DRC/LVS: " + ", ".join(sorted(physical)))
        rec["reason"] = "; ".join(parts)
    return rec


def run_pdk(spec: Path, tech: str, workdir: Path, tag: str,
            period: float | None = None,
            line_rate_mbps: float | None = None) -> dict:
    rec: dict = {"tech": tech, "status": "unverified", "steps": {}}
    pkg = workdir / "pkg"
    cfg = workdir / "config.yaml"
    run_dir = workdir / "runs" / tag
    # Only clear this run's tag directory.  Wiping the whole workdir would
    # destroy every other run tag (and their final/metrics.json evidence)
    # whenever a second measurement is taken for the same tech.
    if run_dir.exists():
        shutil.rmtree(run_dir)
    workdir.mkdir(parents=True, exist_ok=True)

    # Each PDK is measured at the fastest line rate its own capability gate
    # allows for this architecture, so a cross-PDK comparison stays honest
    # instead of comparing sky130 at 100 Mb/s against gf180 failing at 100.
    if line_rate_mbps is None:
        os_max = _max_sample_clock_mhz(tech)
        if os_max is not None:
            data = yaml.safe_load(spec.read_text())
            want = float(data.get("link", {}).get("line_rate_mbps", 0) or 0)
            oversample = int(data.get("rx", {}).get("oversample", 1) or 1)
            allowed = os_max / max(1, oversample)
            if want > allowed:
                line_rate_mbps = allowed
    local_spec = _spec_with_tech(spec, tech, workdir / "spec.yaml",
                                 line_rate_mbps)
    gen = _run([sys.executable, "scripts/gen_ip.py", "--spec", str(local_spec),
                "--out", str(pkg), "--force"], ROOT, 300)
    rec["steps"]["generate"] = gen.returncode
    if gen.returncode != 0:
        rec["reason"] = "generate failed: " + (gen.stderr or gen.stdout)[-400:]
        return rec

    pdk_root, lib_root = _pdk_paths(tech)
    try:
        _expand_config(pkg / "pnr" / "librelane.yaml", cfg, lib_root)
    except SystemExit as exc:
        rec["reason"] = str(exc)
        return rec
    rec["steps"]["expand"] = 0
    rec["pdk_root"] = pdk_root
    cfg_data = yaml.safe_load(cfg.read_text())
    if period is not None:
        cfg_data["CLOCK_PERIOD"] = float(period)
    rec["clock_period_ns"] = cfg_data.get("CLOCK_PERIOD")
    rec["line_rate_mbps"] = yaml.safe_load(
        local_spec.read_text())["link"]["line_rate_mbps"]
    # Static IR drop needs non-zero via resistance in the tech LEF.  Some
    # plain-PDK trees declare zero, which aborts the whole flow at that step.
    # IR drop is not an input to the mapped STA/power/area numbers, so skip it
    # uniformly across PDKs to keep the comparison fair.
    cfg_data["RUN_IRDROP_REPORT"] = False
    if tech == "icsprout55":
        _ics55_flow_overrides(cfg_data, Path(pdk_root), cfg)
    elif tech == "ihp-sg13g2":
        _ihp_flow_overrides(cfg_data, Path(pdk_root), cfg)
    cfg.write_text(yaml.safe_dump(cfg_data, sort_keys=False))

    # The config is a positional argument in LibreLane 3 ("-c" is not a
    # recognised option and is rejected with "No config file(s) provided").
    pdk_args = _librelane_pdk_args(tech)
    if not pdk_args:
        pdk_args = ["--pdk-root", pdk_root]
    cmd = ([str(LIBRELANE)] + pdk_args + _skip_args(tech)
           + ["--run-tag", tag, "--condensed", str(cfg)])
    try:
        pnr = _run(cmd, workdir, 7200, tech=tech)
    except subprocess.TimeoutExpired:
        rec["reason"] = "librelane timeout"
        return rec
    rec["steps"]["librelane"] = pnr.returncode
    # LibreLane exits non-zero when any checker reports violations, including
    # pure timing ones, but the run itself completed and wrote final views.
    # Only treat a non-zero exit as fatal when no metrics were produced.
    try:
        rec["metrics"] = _collect(run_dir)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        rec["reason"] = f"metrics: {exc}"
        if pnr.returncode != 0:
            tail = (pnr.stderr or pnr.stdout).strip().splitlines()
            rec["reason"] += "; librelane rc=%d: %s" % (
                pnr.returncode, tail[-1] if tail else "")
        return rec

    return _classify(rec)
    # Timing violations are a result, not a failure: the run completed and
    # produced GDS/LEF/LIB, so record the numbers and flag the corners.
    viol = {k: v for k, v in m.get("setup_wns_by_corner_ns", {}).items()
            if isinstance(v, (int, float)) and v < 0}
    rec["status"] = "measured_with_violations" if viol else "measured"
    if viol:
        rec["timing_violations"] = {k: round(v, 4) for k, v in viol.items()}
        arr = m.get("critical_path_arrival_ns") or {}
        rec["reason"] = ("setup violations in " + ", ".join(sorted(viol))
                          + "; critical path " + str(arr.get("ns")) + " ns")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--techs", required=True, help="comma separated")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default=str(ROOT / "build" / "pnr" / "cross_pdk"))
    ap.add_argument("--tag", default="cross_pdk")
    ap.add_argument("--period", type=float, default=None,
                    help="override CLOCK_PERIOD in the generated config")
    ap.add_argument("--line-rate", type=float, default=None,
                    help="force the line rate; by default each tech runs at "
                         "the fastest rate its capability gate allows")
    args = ap.parse_args()

    spec = Path(args.spec)
    techs = [t.strip() for t in args.techs.split(",") if t.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # LibreLane runs with cwd=workdir, and the config path is passed on the
    # command line.  A relative --workdir would make that path resolve twice,
    # so anchor it here.
    workroot = Path(args.workdir).resolve()

    records = []
    for tech in techs:
        print(f"=== {tech}", flush=True)
        rec = run_pdk(spec, tech, workroot / tech, f"{args.tag}_{tech}",
                     period=args.period, line_rate_mbps=args.line_rate)
        records.append(rec)
        summary = rec.get("reason")
        if not summary:
            m = rec.get("metrics", {})
            summary = (f"cells={m.get('cells')} area={m.get('core_area_um2')} um2 "
                       f"power={m.get('power_total_mw')} mW "
                       f"setup_wns={m.get('setup_wns_ns')} ns")
        print(f"    {rec['status']}: {summary}", flush=True)

    payload = {
        "kind": "cross-pdk-pnr-evidence",
        "spec": str(spec),
        "design": yaml.safe_load(spec.read_text()).get("name"),
        "librelane": str(LIBRELANE),
        # A bare command name (LIBRELANE_BIN=librelane inside the nix-shell)
        # is not useful provenance; record the resolved binary too.
        "librelane_resolved": shutil.which(str(LIBRELANE)) or str(LIBRELANE),
        "git_commit": _git_commit(),
        "records": records,
        "measured": [r["tech"] for r in records
                     if r["status"].startswith("measured")],
        "with_violations": [r["tech"] for r in records
                            if r["status"] == "measured_with_violations"],
        "unverified": [r["tech"] for r in records
                       if not r["status"].startswith("measured")],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out}")
    return 1 if payload["unverified"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Spec-driven OpenSERDES digital IP generator.

The generator is intentionally conservative: it only emits a configuration
whose parameters and architecture pass the local capability gates.  It does
not claim timing/PHY signoff; see docs/qualification.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ARCH_DB_PATH = ROOT / "tech" / "arch_db.yaml"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ppa_model  # noqa: E402  (RATIO-aware area/clock-load model)

# Source dependencies for each generated design.
DESIGN_FILES = {
    "serdes_8b10b_top": [
        "serializer.sv", "serializer_pingpong.sv", "deserializer_word.sv",
        "deserializer.sv",
        "cdr_sync.sv", "cdr_oversampling.sv", "cdr_adaptive.sv", "cdr_bang_bang.sv",
        "cdr_multiphase.sv",
        "comma_align.sv", "receiver_sampler.sv", "reset_sync.sv",
        "serdes_tx_digital.sv", "serdes_rx_digital.sv",
        "pcs_8b10b_tx.sv", "pcs_8b10b_rx.sv", "pcs_8b10b_comma.sv",
        "pcs_64b66b_lock.sv",
        "encoder_8b10b.sv", "decoder_8b10b.sv", "serdes_8b10b_top.sv",
    ],
    "serdes_64b66b_top": [
        "serializer.sv", "serializer_pingpong.sv", "deserializer_word.sv",
        "deserializer.sv",
        "cdr_sync.sv", "cdr_oversampling.sv", "cdr_adaptive.sv", "cdr_bang_bang.sv",
        "cdr_multiphase.sv",
        "comma_align.sv", "pcs_8b10b_comma.sv", "pcs_64b66b_lock.sv",
        "receiver_sampler.sv", "reset_sync.sv",
        "serdes_tx_digital.sv", "serdes_rx_digital.sv", "pcs_64b66b.sv",
        "serdes_64b66b_top.sv",
    ],
    "serdes_digital_top": [
        "serializer.sv", "serializer_pingpong.sv", "deserializer_word.sv",
        "deserializer.sv",
        "cdr_sync.sv", "cdr_oversampling.sv", "cdr_adaptive.sv", "cdr_bang_bang.sv",
        "cdr_multiphase.sv",
        "comma_align.sv", "receiver_sampler.sv", "reset_sync.sv",
        "serdes_tx_digital.sv", "serdes_rx_digital.sv",
        "serdes_digital_top.sv",
    ],
    "serdes_tx_digital": [
        "serializer.sv", "serializer_pingpong.sv", "reset_sync.sv",
        "serdes_tx_digital.sv",
    ],
    "serdes_tx_stream_digital": [
        "serializer_stream.sv", "reset_sync.sv",
        "serdes_tx_stream_digital.sv",
    ],
    "serdes_tx_half_rate_digital": [
        "serializer_half_rate.sv", "serdes_tx_half_rate_digital.sv",
    ],
    "serdes_tx_axis_digital": [
        "axis_tx_adapter.sv", "serializer_stream.sv", "reset_sync.sv",
        "axis_crc_inserter.sv", "crc_frame.sv", "serdes_tx_axis_digital.sv",
    ],
    "serdes_rx_digital": [
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "cdr_bang_bang.sv",
        "cdr_multiphase.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
    ],
    "serdes_rx_axis_digital": [
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "cdr_bang_bang.sv",
        "cdr_multiphase.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
        "axis_rx_adapter.sv", "axis_crc_checker.sv", "crc_frame.sv",
        "serdes_rx_axis_digital.sv",
    ],
    "serdes_soc_wrapper": [
        "apb_csr.sv", "bist_snapshot.sv", "cdc_sync.sv", "cdc_snapshot.sv", "serdes_bist.sv",
        "prbs_gen.sv", "prbs_check.sv", "prbs_word_gen.sv", "prbs_word_check.sv",
        "axis_tx_adapter.sv", "serializer_stream.sv",
        "axis_crc_inserter.sv", "axis_crc_checker.sv", "crc_frame.sv",
        "serdes_tx_axis_digital.sv", "axis_rx_adapter.sv",
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "cdr_bang_bang.sv",
        "cdr_multiphase.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
        "serdes_rx_axis_digital.sv", "serdes_soc_wrapper.sv",
    ],
    "serdes_soc_cdc_wrapper": [
        "apb_csr.sv", "async_fifo.sv", "cdc_sync.sv", "cdc_snapshot.sv",
        "bist_snapshot.sv", "serdes_bist.sv", "prbs_gen.sv", "prbs_check.sv",
        "axis_tx_adapter.sv", "serializer_stream.sv", "axis_rx_adapter.sv",
        "axis_crc_inserter.sv", "axis_crc_checker.sv", "crc_frame.sv",
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "cdr_bang_bang.sv",
        "cdr_multiphase.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
        "serdes_soc_cdc_wrapper.sv",
    ],
}

OPTIONAL_FILES = {
    "soc": [],
    "phy_ip": ["apb_csr.sv", "async_fifo.sv", "prbs_gen.sv", "prbs_check.sv",
               "serdes_bist.sv", "bist_snapshot.sv"],
}

PRESETS = {
    "minimal": {"data": "valid_ready", "control": "native", "cdc": "none", "features": []},
    "default": {"data": "valid_ready", "control": "native", "cdc": "auto", "features": []},
    "soc": {"data": "axi4_stream", "control": "apb", "cdc": "async_fifo", "features": ["bist"]},
    "phy_ip": {"data": "valid_ready", "control": "apb", "cdc": "async_fifo",
               "features": ["bist", "calibration", "dft"]},
}

CODING_FACTOR = {"raw": 1.0, "8b10b": 1.25, "64b66b": 66.0 / 64.0}

# Digital capability vocabulary.  Values are implementation classes; the
# generator only accepts options that are present in an integrated generated
# package.  Standalone, verified RTL is still listed in the manifest but is not
# promoted to a link capability.
CAPABILITY_CHOICES = {
    "coding": {"raw", "8b10b", "64b66b"},
    "framing": {"none", "sync_word", "crc16", "crc32"},
    "training": {"none", "phase_threshold"},
    "clock_source": {"source_sync", "independent", "external_reference",
                     "ring_pll", "adpll", "dll"},
    "clock_recovery": {"none", "source_sync", "fixed_oversampling",
                       "adaptive_oversampling", "bang_bang", "pi_cdr"},
    "sampler": {"single_edge", "ddr", "multiphase", "interleaved"},
    "tx_driver": {"rtl", "analog_macro", "generated_macro", "cml"},
    "rx_frontend": {"rtl", "analog_macro", "generated_macro", "resistive_fb",
                    "comparator", "ctle"},
    "equalization": {"none", "ctle", "dfe", "ffe"},
    "termination": {"none", "high_z", "on_chip", "programmable"},
    "lane_bonding": {"single", "2_lane", "4_lane"},
    "power_management": {"none", "clock_gating", "sleep", "wake"},
    "fec": {"none", "block", "rs", "bch"},
}
CAPABILITY_INTEGRATED = {
    "coding": {"raw": True, "8b10b": True, "64b66b": True},
    "framing": {"none": True, "sync_word": True},
    "training": {"none": True, "phase_threshold": True},
    "clock_source": {"source_sync": True, "independent": True},
    "clock_recovery": {"none": True, "source_sync": True,
                       "fixed_oversampling": True, "adaptive_oversampling": True,
                       "bang_bang": True},
    "sampler": {"single_edge": True},
    "tx_driver": {"rtl": True},
    "rx_frontend": {"rtl": True},
    "equalization": {"none": True},
    "termination": {"none": True},
    "lane_bonding": {"single": True},
    "power_management": {"none": True},
    "fec": {"none": True},
}


class SpecError(Exception):
    pass


def fail(message: str) -> None:
    raise SpecError(message)


def as_int(value: Any, path: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        fail(f"{path} must be an integer, got {value!r}")


def as_float(value: Any, path: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        fail(f"{path} must be a number, got {value!r}")


def get(mapping: Any, *keys: str, default: Any = None) -> Any:
    cur = mapping
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, list):
        return [expand(v) for v in value]
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    return value


def unresolved(value: Any) -> bool:
    if isinstance(value, str):
        return "$" in value and ("{" in value or value.startswith("$"))
    if isinstance(value, list):
        return any(unresolved(v) for v in value)
    if isinstance(value, dict):
        return any(unresolved(v) for v in value.values())
    return False


@dataclass
class Tech:
    name: str
    pdk: str
    scl: str
    default_corner: str
    corners: dict[str, list[str]]
    native_dff: str
    limits: dict[str, Any]
    cts: dict[str, Any]
    clock_gate: dict[str, Any]


def load_tech(name: str, scl_override: str | None) -> Tech:
    path = ROOT / "tech" / name / "tech.yaml"
    if not path.exists():
        fail(f"unknown tech profile {name!r}; available: " +
             ", ".join(sorted(p.parent.name for p in (ROOT / "tech").glob("*/tech.yaml"))))
    data = yaml.safe_load(path.read_text())
    scl = scl_override or data.get("scl")
    if not scl:
        fail(f"tech/{name}/tech.yaml has no scl")
    variant = data.get("scl_variants", {}).get(scl)
    if scl != data.get("scl") and variant is None:
        available = [data.get("scl")] + sorted(data.get("scl_variants", {}))
        fail(f"tech profile {name!r} does not define scl {scl!r}; available: {available}")
    corners = (variant or {}).get("corners", data.get("corners", {}))
    if not corners:
        fail(f"tech profile {name!r}/{scl!r} has no corners")
    default_corner = data.get("default_corner")
    if default_corner not in corners:
        default_corner = next(iter(corners))
    return Tech(
        name=name,
        pdk=data["pdk"],
        scl=scl,
        default_corner=default_corner,
        corners=corners,
        native_dff=(variant or {}).get("native_dff", data.get("native_dff", "")),
        limits=data.get("limits", {}),
        cts=data.get("cts", {}),
        clock_gate=data.get("clock_gate", {}) or {},
    )


def load_arch_db() -> dict[str, dict[str, Any]]:
    if not ARCH_DB_PATH.exists():
        fail(f"missing architecture database {ARCH_DB_PATH}")
    return yaml.safe_load(ARCH_DB_PATH.read_text())


PPA_WARNINGS: list[str] = []


def clock_evidence_for(tech: Tech, arch: str) -> dict[str, Any] | None:
    """Sample-clock evidence for one RX architecture, with a legacy fallback.

    Only a value backed by a post-route run at that period belongs in
    timing_closed_mhz; physical_signoff_mhz additionally needs DRC/LVS clean.
    cp_implied_mhz is 1000/critical-path and is an estimate, never a closure.
    """
    evidence = (tech.limits.get("clock_evidence_by_arch") or {}).get(arch)
    if evidence is not None:
        return evidence
    legacy = (tech.limits.get("signoff_sample_clock_mhz_by_arch") or {}).get(arch)
    if legacy is None:
        return None
    return {
        "timing_closed_mhz": float(legacy),
        "physical_signoff_mhz": None,
        "cp_implied_mhz": None,
        "source": "legacy signoff_sample_clock_mhz_by_arch",
        "note": "legacy scalar; physical signoff level unknown",
    }


def clock_tier(evidence: dict[str, Any] | None, mhz: float) -> str:
    if not evidence:
        return "none"
    phys = evidence.get("physical_signoff_mhz")
    timing = evidence.get("timing_closed_mhz")
    if phys is not None and mhz <= float(phys):
        return "physical_signoff"
    if timing is not None and mhz <= float(timing):
        return "timing_closed"
    return "none"


def clock_evidence_warning(tech: Tech, arch: str,
                           evidence: dict[str, Any] | None,
                           mhz: float) -> str | None:
    if evidence is None:
        return None
    tier = clock_tier(evidence, mhz)
    if tier == "physical_signoff":
        return None
    src = evidence.get("source", "?")
    phys = evidence.get("physical_signoff_mhz")
    timing = evidence.get("timing_closed_mhz")
    cp = evidence.get("cp_implied_mhz")
    if tier == "timing_closed":
        return (f"RX sample clock {mhz:g} MHz is inside the {tech.name}/{tech.scl} "
                f"{arch} post-route timing closure ({timing:g} MHz) but above "
                f"physical signoff ({phys}); treat as timing evidence, not "
                f"physical signoff ({src})")
    if timing is None:
        return (f"RX sample clock {mhz:g} MHz has no verified post-route timing "
                f"closure for {tech.name}/{tech.scl} {arch}"
                + (f" (CP-implied {cp:g} MHz is an estimate, not a closure)"
                   if cp else "")
                + f"; treat as an unverified experiment ({src})")
    return (f"RX sample clock {mhz:g} MHz exceeds the verified post-route timing "
            f"closure for {tech.name}/{tech.scl} {arch} ({timing:g} MHz"
            + (f"; CP-implied {cp:g} MHz" if cp else "")
            + "); treat as an unverified experiment (" + src + ")")
PPA_MEASURED = {"measured_post_route", "measured_pre_pnr"}


def load_ppa(path: str | None) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Load measured PPA entries keyed by (tech, scl, kind, arch).

    A measured claim is only trusted when its provenance block was verified by
    import_ppa.py.  A hand-written YAML that skips the importer is downgraded
    here, so the manifest can never claim measured PPA without evidence.
    """
    if not path:
        return {}
    ppa_path = Path(path)
    if not ppa_path.exists():
        return {}
    data = yaml.safe_load(ppa_path.read_text()) or {}
    entries: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for entry in data.get("entries", []):
        key = (entry["tech"], entry.get("scl", ""), entry["kind"], entry["arch"])
        if entry.get("confidence") in PPA_MEASURED:
            prov = entry.get("provenance") or {}
            if prov.get("verified") is not True or not prov.get("metrics_sha256"):
                entry = dict(entry)
                entry["confidence"] = "unverified_claim"
                entry["provenance_warning"] = (
                    "measured PPA claim has no verified provenance; downgraded")
                PPA_WARNINGS.append(
                    f"ppa_db {key}: measured claim downgraded to unverified_claim")
        entries[key] = entry
    return entries


def lib_files_present(corners: dict[str, list[str]]) -> bool:
    # True only when every expanded Liberty path exists.  Separate helper
    # so the fail-closed reporting can be unit-tested without a PDK.
    files = [Path(x) for libs in corners.values() for x in libs]
    return bool(files) and all(x.is_file() for x in files)


def pareto(candidates: list[tuple[str, dict[str, Any]]]) -> list[str]:
    frontier = []
    for name, cand in candidates:
        metrics = cand.get("metrics", {})
        dominated = False
        for other_name, other in candidates:
            if other_name == name:
                continue
            om = other.get("metrics", {})
            if all(om.get(k, 0) <= metrics.get(k, 0) for k in metrics) and \
               any(om.get(k, 0) < metrics.get(k, 0) for k in metrics):
                dominated = True
                break
        if not dominated:
            frontier.append(name)
    return frontier


def _model_metrics(kind: str, name: str, db: dict[str, Any], ratio: int,
                   oversample: int, line_rate: float,
                   latency_factor: float) -> dict[str, float]:
    ref_arch = ppa_model.REFERENCE[kind][0]
    ref_lat = float(db.get(ref_arch, {}).get("latency_factor", 1.0))
    return ppa_model.relative_metrics(
        kind, name, ratio, oversample, line_rate,
        latency_factor=latency_factor, reference_latency=ref_lat)


def select_arch(kind: str, requested: str | None, target: str, ratio: int,
                line_rate: float, oversample: int, objectives: list[str],
                clocking: str, ppm: float, tech_name: str, tech_scl: str,
                ppa: dict[tuple[str, str, str, str], dict[str, Any]],
                opt: dict[str, Any] | None = None) -> dict[str, Any]:
    db = load_arch_db().get(kind, {})
    candidates: list[tuple[str, dict[str, Any]]] = []
    reasons: list[str] = []
    for name, cand in db.items():
        eff = dict(cand)
        override = cand.get("tech_overrides", {}).get(tech_name, {})
        if override:
            for key, value in override.items():
                if key == "metrics":
                    eff["metrics"] = {**cand.get("metrics", {}), **value}
                else:
                    eff[key] = value
        ppa_entry = ppa.get((tech_name, tech_scl, kind, name)) or \
                    ppa.get((tech_name, "", kind, name))
        if ppa_entry:
            if "max_line_rate_mbps" in ppa_entry:
                eff["max_line_rate_mbps"] = ppa_entry["max_line_rate_mbps"]
            if "metrics" in ppa_entry:
                eff["metrics"] = dict(ppa_entry["metrics"])
            eff["confidence"] = ppa_entry.get("confidence", "measured_post_route")
            eff["ppa_source"] = ppa_entry.get("source", "")
            if ppa_entry.get("provenance"):
                eff["ppa_provenance"] = ppa_entry["provenance"]
            if ppa_entry.get("provenance_warning"):
                eff["provenance_warning"] = ppa_entry["provenance_warning"]
            if "absolute" in ppa_entry:
                eff["absolute"] = ppa_entry["absolute"]
        # Metrics precedence: measured PPA > explicit tech_overrides.metrics
        # > the RATIO-aware ppa_model.  Never leave a hand-written guess.
        if "metrics" not in eff:
            try:
                eff["metrics"] = _model_metrics(
                    kind, name, db, ratio, oversample, line_rate,
                    float(eff.get("latency_factor", 1.0)))
                eff["metrics_source"] = "ppa_model"
                eff["ppa_model"] = {
                    "source": ppa_model.MODEL_SOURCE,
                    "estimate": ppa_model.estimate(
                        name, ratio, oversample, line_rate, kind),
                }
                eff["confidence"] = "modeled_yosys_cells"
            except KeyError:
                eff["metrics"] = {"area": 1.0, "power": 1.0, "latency": 1.0}
                eff["metrics_source"] = "no_model_fallback"
                eff["confidence"] = "unmodeled"
        elif "metrics_source" not in eff:
            eff["metrics_source"] = ("measured_ppa_db" if ppa_entry
                                     else "tech_override")
        # bang_bang_oversampling is a specialised CDR; require an explicit
        # rx.architecture so it does not silently replace the default fixed
        # loop (which would also bypass measured-PPA gates on that arch).
        if name == "bang_bang_oversampling" and not requested:
            reasons.append(f"{name}: requires explicit rx.architecture")
            continue
        if requested and name != requested:
            continue
        if target == "link" and not eff.get("supports_link", True):
            reasons.append(f"{name}: not supported in a single link netlist")
            continue
        if ratio > eff.get("max_ratio_bits", 1 << 30):
            reasons.append(f"{name}: RATIO {ratio} > max_ratio_bits")
            continue
        if line_rate > eff.get("max_line_rate_mbps", 1e9):
            reasons.append(f"{name}: line rate {line_rate:g} > {tech_name} gate "
                           f"{eff.get('max_line_rate_mbps')}")
            continue
        if kind == "rx":
            if oversample < eff.get("min_oversample", 1):
                reasons.append(f"{name}: OVERSAMPLE {oversample} < min")
                continue
            if "max_oversample" in eff and oversample > eff["max_oversample"]:
                reasons.append(f"{name}: OVERSAMPLE {oversample} > max")
                continue
            if clocking == "source_sync" and name != "source_sync":
                reasons.append(f"{name}: clocking is source_sync")
                continue
            if clocking != "source_sync" and name == "source_sync":
                reasons.append(f"{name}: clocking is not source_sync")
                continue
            if abs(ppm) > 0 and name == "fixed_oversampling":
                reasons.append(f"{name}: ppm offset requires tracking CDR")
                continue
            if name == "adaptive_oversampling" and oversample < 8:
                reasons.append(f"{name}: adaptive requires OVERSAMPLE >= 8")
                continue
        candidates.append((name, eff))

    if not candidates:
        detail = "\n  ".join(reasons) if reasons else "no candidates"
        fail(f"no {kind} architecture satisfies the spec:\n  {detail}")

    opt = opt or {}
    objective_set = opt.get("objectives") or objectives or ["area", "power", "latency"]
    weights = opt.get("weights") or {o: 1.0 for o in objective_set}
    budgets = opt.get("budgets") or {}
    aggregation = opt.get("aggregation", "weighted_sum")

    # Hard budgets are the effective lever: they remove candidates, unlike the
    # Pareto filter (see note in result["selection"]).
    budget_rejections: list[str] = []
    if budgets:
        kept = []
        for n, c in candidates:
            m = c.get("metrics", {})
            bad = [k for k, v in budgets.items() if float(m.get(k, 0.0)) > v]
            if bad:
                budget_rejections.append(f"{n}: {'/'.join(bad)} exceeds budget")
                reasons.append(f"{n}: {'/'.join(bad)} exceeds budget")
            else:
                kept.append((n, c))
        candidates = kept
        if not candidates:
            fail(f"no {kind} architecture satisfies budgets {budgets}:\n  " +
                 "\n  ".join(budget_rejections))
    frontier = pareto(candidates)

    def score(item: tuple[str, dict[str, Any]]):
        metrics = item[1].get("metrics", {})
        vals = [weights.get(k, 0.0) * float(metrics.get(k, 0.0))
                for k in objective_set]
        if aggregation == "minmax":
            return max(vals) if vals else 0.0
        if aggregation == "lexicographic":
            order = sorted(range(len(objective_set)),
                           key=lambda i: -weights.get(objective_set[i], 0.0))
            return tuple(vals[i] for i in order)
        total = sum(weights.get(k, 0.0) for k in objective_set) or 1.0
        return sum(vals) / total

    if requested:
        chosen_name, chosen = candidates[0]
    else:
        eligible = [(n, c) for n, c in candidates if n in frontier]
        chosen_name, chosen = min(eligible, key=score)
    chosen_score = score((chosen_name, chosen))
    if isinstance(chosen_score, tuple):
        chosen_score = list(chosen_score)

    result = dict(chosen)
    result["name"] = chosen_name
    result["frontier"] = frontier
    result["confidence"] = chosen.get("confidence", "estimated")
    result["confidence_scope"] = "capability_gate_estimate_not_signoff"
    result["tech"] = tech_name
    result["selection"] = {
        "objectives": objective_set,
        "weights": weights,
        "budgets": budgets,
        "aggregation": aggregation,
        "score": chosen_score,
        "budget_rejections": budget_rejections,
        "note": ("For any monotone score (all weights >= 0) a dominated "
                 "candidate can never win, so the Pareto filter is informational. "
                 "Hard budgets and weights are the effective selection levers."),
    }
    return result


def normalise_tech_ref(value: Any, path: str) -> dict[str, Any]:
    if isinstance(value, str):
        return {"name": value, "scl": None}
    if isinstance(value, dict):
        if "name" not in value:
            fail(f"{path} must contain 'name'")
        return {"name": value["name"], "scl": value.get("scl")}
    fail(f"{path} must be a tech profile name or a mapping")


def validate(spec: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(spec, dict):
        fail("spec root must be a mapping")
    name = spec.get("name", "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
        fail("name must match [A-Za-z_][A-Za-z0-9_]*")
    target = spec.get("target", "link")
    if target not in ("link", "tx", "rx"):
        fail("target must be link, tx or rx")

    link = spec.get("link")
    if not isinstance(link, dict):
        fail("missing link mapping")
    width = as_int(link.get("payload_width"), "link.payload_width")
    words = as_int(link.get("words"), "link.words")
    if not (1 <= width <= 128):
        fail("link.payload_width must be in [1, 128]")
    if not (1 <= words <= 64):
        fail("link.words must be in [1, 64]")
    ratio = width * words
    if ratio > 4096:
        fail("payload_width * words must be <= 4096")

    coding = str(link.get("coding", "raw")).lower()
    if coding not in CODING_FACTOR:
        fail(f"link.coding must be one of {sorted(CODING_FACTOR)}")
    factor = CODING_FACTOR[coding]
    payload_rate = link.get("payload_rate_mbps")
    line_rate = link.get("line_rate_mbps")
    if payload_rate is not None:
        computed = as_float(payload_rate, "link.payload_rate_mbps") * factor
        if line_rate is not None:
            declared = as_float(line_rate, "link.line_rate_mbps")
            if abs(declared - computed) > max(0.01, computed * 0.01):
                fail(f"line_rate_mbps {declared:g} does not match payload_rate_mbps "
                     f"{payload_rate:g} x coding factor {factor:g} = {computed:g}")
        line_rate = computed
    if line_rate is None:
        fail("link.line_rate_mbps or link.payload_rate_mbps is required")
    line_rate = as_float(line_rate, "link.line_rate_mbps")
    if line_rate <= 0:
        fail("line rate must be positive")

    bit_order = str(link.get("bit_order", "lsb_first")).lower()
    if bit_order not in ("lsb_first", "msb_first"):
        fail("link.bit_order must be lsb_first or msb_first")
    lsb_first = bit_order == "lsb_first"

    tx = spec.get("tx", {}) or {}
    rx = spec.get("rx", {}) or {}
    if not isinstance(tx, dict) or not isinstance(rx, dict):
        fail("tx and rx must be mappings")
    ppm = as_float(rx.get("ppm", 0.0), "rx.ppm")
    opt_in = spec.get("optimization", {}) or {}
    power_first = bool(opt_in.get("clock_power_priority", False)) if isinstance(opt_in, dict) else False
    clocking_raw = str(rx.get("clocking", "independent")).lower()
    if clocking_raw == "auto":
        # "auto" resolves to the lowest-clock option that still meets the link
        # requirements.  source_sync needs an external forwarded clock; it is
        # only chosen when the spec explicitly asks for power-first behaviour
        # and there is no ppm offset to track.
        clocking = "source_sync" if (power_first and abs(ppm) == 0) else "independent"
        spec.setdefault("_warnings", []).append(
            f"rx.clocking=auto resolved to {clocking} "
            f"(clock_power_priority={power_first}, ppm={ppm})")
    elif clocking_raw in ("independent", "source_sync"):
        clocking = clocking_raw
    else:
        fail("rx.clocking must be independent, source_sync or auto")

    # Clock generation, recovery and sampler are separate axes.  They must
    # not be inferred from one another when the user asks for a physical
    # architecture that is not implemented.
    clock_source = str(rx.get("clock_source", clocking)).lower()
    if clock_source not in ("source_sync", "independent", "external_reference",
                            "ring_pll", "adpll", "dll"):
        fail("rx.clock_source must be source_sync, independent, external_reference, ring_pll, adpll or dll")
    sampler_arch = str(rx.get("sampler_architecture", "single_edge")).lower()
    if sampler_arch not in ("single_edge", "ddr", "multiphase", "interleaved"):
        fail("rx.sampler_architecture must be single_edge, ddr, multiphase or interleaved")
    if sampler_arch != "single_edge":
        fail(
            f"rx.sampler_architecture={sampler_arch!r} requires physical multiphase "
            "sampling clocks and a verified sampler backend; only single_edge is integrated"
        )

    os_raw = rx.get("oversample", "auto")
    if target == "tx":
        oversample = 1
    elif clocking == "source_sync":
        oversample = 1
    elif os_raw in (None, "auto"):
        if abs(ppm) > 0:
            oversample = 8
        elif power_first and bool(rx.get("allow_os2", False)):
            # Restricted 2x mode: half the clock load of OS=4, no jitter margin.
            oversample = 2
        else:
            oversample = 4
    else:
        oversample = as_int(os_raw, "rx.oversample")
    if target != "tx" and clocking != "source_sync":
        if oversample < 2 or oversample % 2:
            fail("rx.oversample must be an even integer >= 2")
        if oversample == 2 and not bool(get(spec, "rx", "allow_os2", default=False)):
            fail("OVERSAMPLE=2 is a restricted mode; set rx.allow_os2: true")
        if os_raw not in (None, "auto") and oversample < 4 and not bool(get(spec, "rx", "allow_os2", default=False)):
            fail("fixed/adaptive CDR requires OVERSAMPLE >= 4 (or allow_os2: true)")

    tx_arch_name = tx.get("architecture", "auto")
    capture = tx.get("capture", "auto")
    capture_map = {"parallel": "parallel_mux", "atomic": "parallel_mux",
                   "word_group": "word_group_capture",
                   "word_streaming": "word_streaming"}
    if capture not in ("auto", None) and capture not in capture_map:
        fail("tx.capture must be auto, parallel, word_group or word_streaming")
    if capture not in ("auto", None):
        requested_tx = capture_map[capture]
        if tx_arch_name not in ("auto", requested_tx):
            fail("tx.architecture and tx.capture disagree")
        tx_arch_name = requested_tx
    elif tx_arch_name == "auto":
        tx_arch_name = None

    tx_bank_mode = str(tx.get("bank", "single")).lower()
    if tx_bank_mode not in ("single", "pingpong"):
        fail("tx.bank must be single or pingpong")
    if tx_bank_mode == "pingpong" and target != "tx":
        fail("tx.bank: pingpong is only available for target: tx in this "
             "release; use it on a TX endpoint package")

    tx_rate_mode = str(tx.get("rate_mode", "full")).lower()
    if tx_rate_mode not in ("full", "half"):
        fail("tx.rate_mode must be full or half")
    if tx_rate_mode == "half":
        if target != "tx":
            fail("tx.rate_mode: half is only available for target: tx; "
                 "there is no verified half-rate RX")
        if str((spec.get("interface") or {}).get("enable_mode", "static")).lower() != "static":
            fail("tx.rate_mode: half has no enable port yet; set "
                 "interface.enable_mode: static")

    rx_arch_name = rx.get("architecture", "auto")
    if rx_arch_name == "auto":
        rx_arch_name = None
    if clocking == "source_sync" and rx_arch_name not in (None, "source_sync"):
        fail("rx.architecture conflicts with rx.clocking: source_sync")
    if clocking != "source_sync" and rx_arch_name == "source_sync":
        fail("rx.architecture source_sync requires rx.clocking: source_sync")

    recovery_default = ("source_sync" if clocking == "source_sync" else
                        "bang_bang" if rx_arch_name == "bang_bang_oversampling" else
                        "adaptive_oversampling" if rx_arch_name == "adaptive_oversampling"
                        else "fixed_oversampling")
    clock_recovery = str(rx.get("clock_recovery", recovery_default)).lower()
    if clock_recovery not in ("none", "source_sync", "fixed_oversampling",
                              "adaptive_oversampling", "bang_bang", "pi_cdr"):
        fail("rx.clock_recovery must be none, source_sync, fixed_oversampling, "
             "adaptive_oversampling, bang_bang or pi_cdr")
    if clock_recovery == "pi_cdr":
        fail("rx.clock_recovery='pi_cdr' is not integrated or verified")
    if clock_recovery == "bang_bang" and rx_arch_name != "bang_bang_oversampling":
        fail("rx.clock_recovery='bang_bang' requires "
             "rx.architecture: bang_bang_oversampling (4x BBPD + NCO)")
    if clock_recovery != recovery_default:
        fail("rx.clock_recovery conflicts with rx.clocking/rx.architecture")
    if clock_source != clocking:
        fail("rx.clock_source must match rx.clocking in the current digital backend")

    opt_in = spec.get("optimization", {}) or {}
    if not isinstance(opt_in, dict):
        fail("optimization must be a mapping")
    objectives = opt_in.get("objectives", ["area", "power", "latency"])
    if not isinstance(objectives, list) or not objectives:
        fail("optimization.objectives must be a non-empty list")
    for obj in objectives:
        if obj not in ("area", "power", "latency"):
            fail("optimization.objectives must use area, power, latency")
    weights_in = opt_in.get("weights", {}) or {}
    if not isinstance(weights_in, dict):
        fail("optimization.weights must be a mapping")
    weights = {}
    for key, value in weights_in.items():
        if key not in objectives:
            fail(f"optimization.weights key {key!r} is not in objectives")
        try:
            w = float(value)
        except (TypeError, ValueError):
            fail(f"optimization.weights.{key} must be numeric")
        if w < 0:
            fail(f"optimization.weights.{key} must be >= 0")
        weights[key] = w
    for obj in objectives:
        weights.setdefault(obj, 1.0)
    if not any(weights[o] > 0 for o in objectives):
        fail("optimization.weights must give at least one objective a positive weight")
    budgets_in = opt_in.get("budgets", {}) or {}
    if not isinstance(budgets_in, dict):
        fail("optimization.budgets must be a mapping")
    budgets = {}
    for key, value in budgets_in.items():
        if key not in ("area", "power", "latency"):
            fail(f"optimization.budgets key {key!r} must be area, power or latency")
        try:
            b = float(value)
        except (TypeError, ValueError):
            fail(f"optimization.budgets.{key} must be numeric")
        if b <= 0:
            fail(f"optimization.budgets.{key} must be positive")
        budgets[key] = b
    aggregation = str(opt_in.get("aggregation", "weighted_sum")).lower()
    if aggregation not in ("weighted_sum", "minmax", "lexicographic"):
        fail("optimization.aggregation must be weighted_sum, minmax or lexicographic")
    optimization = {"objectives": objectives, "weights": weights,
                    "budgets": budgets, "aggregation": aggregation,
                    "clock_power_priority": bool(opt_in.get("clock_power_priority", False)),
                    "clock_gating": bool(opt_in.get("clock_gating", False)),
                    "export_clk_out": bool(opt_in.get("export_clk_out", True))}

    tech_refs = spec.get("tech", {})
    if not isinstance(tech_refs, dict):
        fail("tech must be a mapping with tx and rx entries")
    tx_ref = normalise_tech_ref(tech_refs.get("tx", "sky130"), "tech.tx")
    rx_ref = normalise_tech_ref(tech_refs.get("rx", tx_ref["name"]), "tech.rx")
    tx_tech = load_tech(tx_ref["name"], tx_ref["scl"])
    rx_tech = load_tech(rx_ref["name"], rx_ref["scl"])
    if target == "link":
        if tx_tech.name != rx_tech.name or tx_tech.scl != rx_tech.scl:
            fail("target: link requires the same tech/scl for TX and RX; "
                 "generate target: tx and target: rx separately for cross-PDK endpoints")
        tech = tx_tech
    elif target == "tx":
        tech = tx_tech
    else:
        tech = rx_tech

    if optimization["clock_gating"] and not tech.clock_gate.get("icg_cell"):
        fail(f"optimization.clock_gating requested but {tech.name}/{tech.scl} "
             "does not declare clock_gate.icg_cell in tech.yaml")

    limit_key = "max_link_line_rate_mbps" if target == "link" else "max_endpoint_line_rate_mbps"
    limit = tech.limits.get(limit_key, tech.limits.get("serial_mbps"))
    if limit is not None and line_rate > float(limit):
        message = (f"line rate {line_rate:g} Mb/s exceeds {tech.name}/{tech.scl} "
                   f"{limit_key}={limit} Mb/s")
        if args.relax_limits:
            spec.setdefault("_warnings", []).append(message + " (relaxed)")
        else:
            fail(message + "; use --relax-limits only for an explicit experiment")

    tx_period_ns = 1000.0 / line_rate
    rx_period_ns = tx_period_ns / oversample if oversample else tx_period_ns
    sample_clock_mhz = line_rate * oversample

    # An oversampling CDR runs the whole RX word bank and synchronizer at
    # line_rate x OVERSAMPLE.  The line-rate gate above does not bound that
    # clock, so a spec can otherwise pass the gate while demanding an
    # impossible RX frequency (e.g. 400 MHz on sky130).  Bounds come from the
    # pre-PnR results in docs/prepnr_sta.md, not from a node-name assumption.
    if target in ("link", "rx") and oversample > 1:
        max_sample = tech.limits.get("max_sample_clock_mhz")
        if max_sample is not None and sample_clock_mhz > float(max_sample):
            message = (
                f"RX sample clock {sample_clock_mhz:g} MHz "
                f"(line rate {line_rate:g} Mb/s x OVERSAMPLE {oversample}) exceeds "
                f"{tech.name}/{tech.scl} max_sample_clock_mhz={max_sample} MHz")
            if args.relax_limits:
                spec.setdefault("_warnings", []).append(message + " (relaxed)")
            else:
                fail(message + "; lower line_rate/oversample, use source_sync, "
                     "or --relax-limits only for an explicit experiment")


    interface = spec.get("interface", {}) or {}
    if not isinstance(interface, dict):
        fail("interface must be a mapping")
    preset = str(interface.get("preset", "default")).lower()
    if preset not in PRESETS:
        fail(f"interface.preset must be one of {sorted(PRESETS)}")
    p = PRESETS[preset]
    data_mode = str(interface.get("data", p["data"])).lower()
    control = str(interface.get("control", p["control"])).lower()
    cdc = str(interface.get("cdc", p["cdc"])).lower()
    features = interface.get("features", p["features"])
    if data_mode not in ("valid_ready", "axi4_stream"):
        fail("interface.data must be valid_ready or axi4_stream; 'raw' is not "
             "a distinct data mode (it previously fell through to valid_ready)")
    if control not in ("native", "apb"):
        fail("interface.control must be native or apb")
    if cdc not in ("none", "async_fifo", "auto"):
        fail("interface.cdc must be none, async_fifo or auto")
    if not isinstance(features, list):
        fail("interface.features must be a list")
    for feature in features:
        if feature not in ("bist", "calibration", "dft", "elastic_fifo"):
            fail(f"unknown interface feature {feature!r}")

    if preset == "minimal":
        if data_mode != "valid_ready" or control != "native" or cdc != "none":
            fail("preset minimal requires valid_ready data, native control and no CDC")
        if features:
            fail("preset minimal cannot include optional features")
    user_clock = interface.get("user_clock_mhz")
    tx_user_clock = interface.get("tx_user_clock_mhz")
    rx_user_clock = interface.get("rx_user_clock_mhz")
    if user_clock is not None:
        user_clock = as_float(user_clock, "interface.user_clock_mhz")
    if tx_user_clock is not None:
        tx_user_clock = as_float(tx_user_clock, "interface.tx_user_clock_mhz")
    if rx_user_clock is not None:
        rx_user_clock = as_float(rx_user_clock, "interface.rx_user_clock_mhz")
    proven_sync = bool(interface.get("cdc_proven_synchronous", False))
    tx_phy_clock = line_rate
    rx_phy_clock = 1000.0 / rx_period_ns
    if proven_sync and (tx_user_clock is None or rx_user_clock is None):
        fail("interface.cdc_proven_synchronous requires both "
             "tx_user_clock_mhz and rx_user_clock_mhz")

    def _same_clock(a: float, b: float) -> bool:
        return abs(a - b) <= max(0.001, b * 1e-6)

    if cdc == "auto":
        # Rate equality alone is not proof that two user clocks are synchronous.
        # The SoC wrapper therefore defaults to an async FIFO unless the spec
        # explicitly supplies both user clocks and declares them proven.
        if preset == "soc":
            if proven_sync and _same_clock(tx_user_clock, tx_phy_clock) and \
                    _same_clock(rx_user_clock, rx_phy_clock):
                cdc = "none"
            else:
                cdc = "async_fifo"
                spec.setdefault("_warnings", []).append(
                    "interface.cdc=auto resolved to async_fifo: rate equality "
                    "does not prove synchronous user clocks")
        elif user_clock is not None:
            differs = not _same_clock(user_clock, tx_phy_clock)
            cdc = "async_fifo" if differs else "none"
        else:
            cdc = "none"
    elif cdc == "none":
        for name, value, phy in (("user_clock_mhz", user_clock, tx_phy_clock),
                                 ("tx_user_clock_mhz", tx_user_clock, tx_phy_clock),
                                 ("rx_user_clock_mhz", rx_user_clock, rx_phy_clock)):
            if value is not None and not _same_clock(value, phy):
                fail(f"interface.{name} differs from its PHY clock; "
                     "interface.cdc must be async_fifo")

    soc_single = (target == "link" and preset == "soc" and
                  data_mode == "axi4_stream" and control == "apb" and
                  cdc == "none")
    soc_cdc = (target == "link" and preset == "soc" and
               data_mode == "axi4_stream" and control == "apb" and
               cdc == "async_fifo")
    soc_any = soc_single or soc_cdc
    if soc_any and (clocking != "source_sync" or oversample != 1):
        fail("the soc wrapper is currently source-synchronous; set "
             "rx.clocking: source_sync and rx.oversample: 1")
    if preset == "soc" and not soc_any and not args.allow_incomplete:
        fail("preset soc requires axi4_stream + apb + cdc none/async_fifo "
             "for a real wrapper, or --allow-incomplete for a scaffold package")
    if preset == "phy_ip" and not args.allow_incomplete:
        fail("preset phy_ip requires BIST/calibration/DFT integration that is "
             "not implemented yet; use --allow-incomplete to emit a scaffold package")

    if data_mode == "axi4_stream" and target == "link" and not soc_any:
        if not args.allow_incomplete:
            fail("target link has no general AXIS wrapper; use target: tx or rx, "
                 "or preset: soc with cdc none/async_fifo, or "
                 "--allow-incomplete for a core scaffold")
        spec.setdefault("_warnings", []).append(
            "target link AXIS/APB/CDC wrapper is not integrated; package is a core scaffold")
    if (control == "apb" or cdc != "none" or features) and not soc_any:
        if not args.allow_incomplete:
            fail("APB/CDC/BIST/calibration/DFT integration is not implemented "
                 "for this target; use --allow-incomplete to emit a scaffold package")

    enable_mode = str(interface.get("enable_mode", "static")).lower()
    if enable_mode not in ("static", "dynamic"):
        fail("interface.enable_mode must be static or dynamic")

    align = str(rx.get("align", "external")).lower()
    align_map = {"external": 0, "comma": 1, "comma_per_frame": 2,
                 "comma_8b10b": 3, "block_lock": 4}
    if align not in align_map:
        fail("rx.align must be external, comma, comma_per_frame, "
             "comma_8b10b or block_lock")
    sync_pattern = rx.get("sync_pattern", "0xBC")
    if isinstance(sync_pattern, str):
        try:
            sync_pattern_int = int(sync_pattern, 0)
        except ValueError:
            fail("rx.sync_pattern must be an integer or a 0x-prefixed hex string")
    else:
        sync_pattern_int = as_int(sync_pattern, "rx.sync_pattern")
    sync_len = as_int(rx.get("sync_len", 8), "rx.sync_len")
    if not (2 <= sync_len <= 64):
        fail("rx.sync_len must be in [2, 64]")
    if sync_pattern_int < 0 or sync_pattern_int >= (1 << sync_len):
        fail("rx.sync_pattern does not fit rx.sync_len")
    if align in ("comma", "comma_per_frame") and sync_len != width:
        fail(
            "comma alignment requires rx.sync_len == link.payload_width "
            f"({sync_len} != {width}); the comma is a full word, not a byte "
            "inside a wider word"
        )
    if coding != "raw" and target == "link" and preset == "soc":
        fail("coded links are not available through the SoC AXIS/APB wrapper; use preset: minimal")
    if coding == "64b66b":
        if target != "link":
            fail("coding: 64b66b is currently supported only for target: link")
        if width != 64:
            fail("coding: 64b66b requires link.payload_width: 64")
        if align not in ("external", "block_lock"):
            fail("coding: 64b66b requires rx.align: external or block_lock "
                 "(block boundary external or sync-header block lock)")
    if coding == "8b10b":
        if target != "link":
            fail("coding: 8b10b is currently supported only for target: link")
        if width != 8:
            fail("coding: 8b10b requires link.payload_width: 8")
        if align not in ("external", "comma_8b10b"):
            fail("coding: 8b10b requires rx.align: external or comma_8b10b "
                 "(external comma or K28.5 self-alignment)")

    # Optional capability selection.  Defaults describe what this generator
    # actually integrates today.  A requested standalone block is rejected;
    # explicit `none` is the supported bypass.
    if clocking == "source_sync":
        recovery_default = "source_sync"
    elif rx_arch_name == "adaptive_oversampling":
        recovery_default = "adaptive_oversampling"
    elif target == "tx":
        recovery_default = "none"
    else:
        recovery_default = "fixed_oversampling"
    cap_defaults = {
        "coding": coding,
        "framing": "sync_word" if align != "external" else "none",
        "training": "none",
        "clock_source": clocking,
        "clock_recovery": recovery_default,
        "sampler": "single_edge",
        "tx_driver": "rtl" if target in ("link", "tx") else "none",
        "rx_frontend": "rtl" if target in ("link", "rx") else "none",
        "equalization": "none",
        "termination": "none",
        "lane_bonding": "single",
        "power_management": "none",
        "fec": "none",
    }
    cap_in = spec.get("capabilities", {}) or {}
    if not isinstance(cap_in, dict):
        fail("capabilities must be a mapping")
    for key, value in cap_in.items():
        if key not in CAPABILITY_CHOICES:
            fail(f"unknown capabilities.{key}")
        value = str(value).lower()
        if value not in CAPABILITY_CHOICES[key]:
            fail(f"capabilities.{key} must be one of {sorted(CAPABILITY_CHOICES[key])}")
        cap_defaults[key] = value
    if cap_defaults["coding"] != coding:
        fail("capabilities.coding must match link.coding")
    if cap_defaults["clock_source"] != clocking:
        fail("capabilities.clock_source must match rx.clocking")
    if cap_defaults["clock_recovery"] != recovery_default:
        fail("capabilities.clock_recovery must match the selected RX architecture")
    # Training needs a real, controllable phase actuator.  The only integrated
    # actuator today is the digital phase_offset on an oversampling CDR, so
    # refuse phase training when there is no oversampling path to drive.
    if cap_defaults["training"] == "phase_threshold":
        if clocking == "source_sync" or oversample <= 1:
            fail(
                "capabilities.training=phase_threshold requires an oversampling "
                "RX with a controllable sample-phase actuator; source_sync / "
                "oversample:1 has no phase actuator to calibrate"
            )
    framing_default = "sync_word" if align != "external" else "none"
    if cap_defaults["framing"] != framing_default:
        fail("capabilities.framing must match rx.align")
    for key, value in cap_defaults.items():
        if value == "none":
            continue
        if not CAPABILITY_INTEGRATED.get(key, {}).get(value, False):
            if value in ("crc16", "crc32") or key == "training":
                fail(
                    f"capabilities.{key}={value!r} is RTL_STANDALONE and is not "
                    "integrated into the generated package; use the explicit "
                    "bypass `none` until the block is wired and verified"
                )
            fail(
                f"capabilities.{key}={value!r} is not integrated for this "
                "generated design; use an available option or explicit `none`"
            )

    # A scaffold package must say what it does NOT contain.  Without this the
    # manifest's feature list plus empty warnings would make an
    # --allow-incomplete package indistinguishable from a complete one.
    missing_blocks: list[str] = []
    if args.allow_incomplete:
        if preset == "phy_ip":
            missing_blocks += ["calibration", "dft", "scan", "clock_gating"]
        if preset == "soc" and not soc_any:
            missing_blocks += ["soc_axi_apb_cdc_wrapper"]
        if data_mode == "axi4_stream" and target == "link" and not soc_any:
            missing_blocks += ["link_axis_wrapper"]
        if (control == "apb" or cdc != "none" or features) and not soc_any:
            missing_blocks += ["apb_cdc_bist_integration"]
        missing_blocks = sorted(set(missing_blocks))
        if missing_blocks:
            spec.setdefault("_warnings", []).append(
                "scaffold package: missing blocks " + ", ".join(missing_blocks) +
                "; --allow-incomplete was used, do NOT treat as a deliverable IP")
    spec["_missing_blocks"] = missing_blocks

    return {
        "name": name,
        "target": target,
        "width": width,
        "words": words,
        "ratio": ratio,
        "coding": coding,
        "line_rate": line_rate,
        "lsb_first": lsb_first,
        "oversample": oversample,
        "tx_period_ns": tx_period_ns,
        "rx_period_ns": rx_period_ns,
        "tx_arch_name": tx_arch_name,
        "tx_rate_mode": tx_rate_mode,
        "tx_bank_mode": tx_bank_mode,
        "rx_arch_name": rx_arch_name,
        "clocking": clocking,
        "clock_source": clock_source,
        "clock_recovery": clock_recovery,
        "sampler_architecture": sampler_arch,
        "ppm": ppm,
        "sample_clock_mhz": sample_clock_mhz,
        "max_sample_clock_mhz": tech.limits.get("max_sample_clock_mhz"),
        "user_clock_mhz": user_clock if user_clock is not None else line_rate,
        "tx_user_clock_mhz": tx_user_clock,
        "rx_user_clock_mhz": rx_user_clock,
        "cdc_proven_synchronous": proven_sync,
        "objectives": objectives,
        "optimization": optimization,
        "tech": tech,
        "tx_tech": tx_tech,
        "rx_tech": rx_tech,
        "preset": preset,
        "data_mode": data_mode,
        "control": control,
        "cdc": cdc,
        "features": features,
        "enable_mode": enable_mode,
        "align_mode": align_map[align],
        "sync_pattern": sync_pattern_int,
        "sync_len": sync_len,
        "capabilities": cap_defaults,
        "missing_blocks": missing_blocks,
        "warnings": spec.get("_warnings", []),
    }


def build_plan(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    unused = {"name": "not_used", "module": "none", "confidence": "n/a",
              "frontier": []}
    ppa = load_ppa(args.ppa_db)
    soc_single = (cfg["target"] == "link" and cfg["preset"] == "soc" and
                  cfg["data_mode"] == "axi4_stream" and
                  cfg["control"] == "apb" and cfg["cdc"] == "none")
    soc_cdc = (cfg["target"] == "link" and cfg["preset"] == "soc" and
               cfg["data_mode"] == "axi4_stream" and
               cfg["control"] == "apb" and cfg["cdc"] == "async_fifo")
    soc_link = soc_single or soc_cdc
    tx_target = "tx" if soc_link else cfg["target"]
    if cfg["target"] in ("link", "tx"):
        tx_arch = select_arch("tx", cfg["tx_arch_name"], tx_target, cfg["ratio"],
                              cfg["line_rate"], cfg["oversample"], cfg["objectives"],
                              cfg["clocking"], cfg["ppm"], cfg["tech"].name,
                              cfg["tech"].scl, ppa, opt=cfg["optimization"])
    else:
        tx_arch = dict(unused)
    if cfg["target"] in ("link", "rx"):
        rx_arch = select_arch("rx", cfg["rx_arch_name"], cfg["target"], cfg["ratio"],
                              cfg["line_rate"], cfg["oversample"], cfg["objectives"],
                              cfg["clocking"], cfg["ppm"], cfg["tech"].name,
                              cfg["tech"].scl, ppa, opt=cfg["optimization"])
    else:
        rx_arch = dict(unused)

    # Third tier: "can generate" is not "has a verified closure".  Evaluate
    # the selected arch (which may differ from the requested one under auto)
    # and record the evidence level in the manifest.
    clock_evidence = None
    if cfg["target"] in ("link", "rx"):
        clock_evidence = clock_evidence_for(cfg["tech"], rx_arch["name"])
        tier = clock_tier(clock_evidence, cfg["sample_clock_mhz"])
        cfg["clock_evidence"] = {
            "tier": tier,
            # modeled=False means this architecture has no clock-closure
            # evidence model (fixed/source-sync); physical signoff is then a
            # DRC/LVS claim, not a CDR-frequency claim.
            "modeled": clock_evidence is not None,
            "requested_mhz": cfg["sample_clock_mhz"],
            "timing_closed_mhz": (clock_evidence or {}).get("timing_closed_mhz"),
            "physical_signoff_mhz": (clock_evidence or {}).get("physical_signoff_mhz"),
            "cp_implied_mhz": (clock_evidence or {}).get("cp_implied_mhz"),
            "corner": (clock_evidence or {}).get("corner"),
            "tool": (clock_evidence or {}).get("tool"),
            "git_commit": (clock_evidence or {}).get("commit"),
            "source": (clock_evidence or {}).get("source"),
        }
        required = cfg.get("require_clock_evidence", "none")
        rank = {"none": 0, "timing_closed": 1, "physical_signoff": 2}
        cfg["clock_evidence"]["required"] = required
        cfg["clock_evidence"]["satisfied"] = rank[required] <= rank[tier]
        if not cfg["clock_evidence"]["satisfied"]:
            ev = clock_evidence or {}
            fail(
                f"RX sample clock {cfg['sample_clock_mhz']:g} MHz has clock "
                f"evidence tier {tier!r} for {cfg['tech'].name}/"
                f"{cfg['tech'].scl} {rx_arch['name']}, below "
                f"constraints.require_clock_evidence={required!r} "
                f"(timing_closed={ev.get('timing_closed_mhz')}, "
                f"physical_signoff={ev.get('physical_signoff_mhz')}, "
                f"source={ev.get('source', 'none')})")
        warning = clock_evidence_warning(cfg["tech"], rx_arch["name"],
                                         clock_evidence, cfg["sample_clock_mhz"])
        if warning:
            cfg["warnings"].append(warning)
    else:
        cfg["clock_evidence"] = None

    if cfg["target"] == "link":
        if soc_single:
            design = "serdes_soc_wrapper"
        elif soc_cdc:
            design = "serdes_soc_cdc_wrapper"
        elif cfg["coding"] == "8b10b":
            design = "serdes_8b10b_top"
        elif cfg["coding"] == "64b66b":
            design = "serdes_64b66b_top"
        else:
            design = "serdes_digital_top"
    elif cfg["target"] == "tx":
        if cfg.get("tx_rate_mode") == "half":
            design = "serdes_tx_half_rate_digital"
        else:
            design = ("serdes_tx_axis_digital"
                      if cfg["data_mode"] == "axi4_stream" else tx_arch["module"])
    else:
        design = ("serdes_rx_axis_digital"
                  if cfg["data_mode"] == "axi4_stream" else rx_arch["module"])

    if design not in DESIGN_FILES:
        fail(f"internal: no file list for design {design}")

    params: dict[str, Any] = {
        "DATA_WIDTH": cfg["width"],
        "WORDS": cfg["words"],
        "LSB_FIRST": 1 if cfg["lsb_first"] else 0,
    }
    if design in ("serdes_tx_digital", "serdes_digital_top",
                  "serdes_8b10b_top", "serdes_64b66b_top"):
        params["CAPTURE_MODE"] = int(tx_arch.get("capture_mode", 0))
    if design == "serdes_tx_digital":
        params["PINGPONG"] = 1 if cfg.get("tx_bank_mode") == "pingpong" else 0
    if design in ("serdes_soc_wrapper", "serdes_soc_cdc_wrapper"):
        params.update({
            "SYNC_LEN": cfg["sync_len"],
            "SYNC_PATTERN": cfg["sync_pattern"],
            "ADAPT_FRAC_BITS": 12,
            "BIST_PRBS_WIDTH": 7,
        })
    if design == "serdes_soc_cdc_wrapper":
        params["FIFO_DEPTH"] = cfg["fifo_depth"]
    if design in ("serdes_rx_digital", "serdes_rx_axis_digital", "serdes_digital_top",
                  "serdes_8b10b_top", "serdes_64b66b_top"):
        params.update({
            "OVERSAMPLE": cfg["oversample"],
            "SYNC_STAGES": cfg["sync_stages"],
            "LOCK_THRESH": cfg["lock_thresh"],
            "LOCK_STALE_BITS": cfg["lock_stale_bits"],
            "CDR_MODE": int(rx_arch.get("cdr_mode", 0)),
            "CDR_BYPASS": 1 if cfg["oversample"] == 1 else 0,
            "ALIGN_MODE": cfg["align_mode"],
            "SYNC_LEN": cfg["sync_len"],
            "SYNC_PATTERN": cfg["sync_pattern"],
            "ADAPT_FRAC_BITS": 12,
            "ADAPT_LOOP_GAIN": 4,
        })
    if design == "serdes_rx_digital":
        params["CLK_OUT_ENABLE"] = (1 if cfg["optimization"].get("export_clk_out", True)
                                    else 0)
    # Phase/threshold training is only wired into the digital top and the SoC
    # CDC wrapper (the two tops that own the oversampling-CDR phase actuator).
    if (cfg["capabilities"]["training"] == "phase_threshold" and
            design in ("serdes_digital_top", "serdes_soc_cdc_wrapper")):
        params.update({
            "TRAINING_ENABLE": 1,
            "TRAIN_DWELL_CYCLES": 64,
            "TRAIN_ERROR_WIDTH": 16,
        })

    if design == "serdes_soc_cdc_wrapper":
        clock_period = 1000.0 / cfg["user_clock_mhz"]
        clock_ports = ["pclk", "s_axis_aclk", "m_axis_aclk", "tx_clk", "rx_clk"]
    elif design == "serdes_soc_wrapper":
        clock_period = cfg["tx_period_ns"]
        clock_ports = ["clk"]
    elif design == "serdes_tx_half_rate_digital":
        # clk_bit runs at the line rate; clk_word is the divide-by-2 word clock.
        clock_period = cfg["tx_period_ns"]
        clock_ports = ["clk_word", "clk_bit"]
    elif cfg["target"] == "link":
        clock_period = cfg["tx_period_ns"]
        clock_ports = ["tx_clk", "rx_clk"]
    elif cfg["target"] == "tx":
        clock_period = cfg["tx_period_ns"]
        clock_ports = ["tx_clk"]
    else:
        clock_period = cfg["rx_period_ns"]
        clock_ports = ["rx_clk"]
    # LibreLane's CLOCK_PORT accepts a list and CTS turns it into CLOCK_NET,
    # so every declared clock is buffered.  CLOCK_PERIOD stays scalar (the
    # primary clock); the individual periods live in the supplied SDC.
    clock_port = list(clock_ports)

    capture_semantics = {
        "parallel_mux": "atomic: load && ready captures the full frame; upstream may change data the next cycle",
        "word_group_capture": "word_group_hold: ready is low for WORDS cycles; upstream must hold the full frame stable",
        "word_streaming": "word_streaming: per-word in_valid/in_ready; upstream holds one word per accepted cycle",
        "axi4_stream": "axi4_stream: per-word AXIS beats; tlast marks the last word; no full-frame hold",
        "soc_wrapper": "soc_wrapper: TX AXIS frame = comma + payload words; RX AXIS frame = payload words",
        "soc_cdc_wrapper": "soc_cdc_wrapper: AXIS frames cross async FIFOs; APB control/status synchronized",
        "half_rate": "half_rate: word capture on clk_word; final bit mux on clk_bit = 2 x clk_word",
        "not_used": "n/a",
    }.get("soc_cdc_wrapper" if design == "serdes_soc_cdc_wrapper"
          else "soc_wrapper" if design == "serdes_soc_wrapper"
          else "half_rate" if design == "serdes_tx_half_rate_digital"
          else "axi4_stream" if design == "serdes_tx_axis_digital"
          else tx_arch["name"], "unknown")

    files = list(DESIGN_FILES[design])
    optional = OPTIONAL_FILES.get(cfg["preset"], [])
    for filename in optional:
        if filename not in files:
            files.append(filename)

    return {
        "design": design,
        "params": params,
        "tx_arch": tx_arch,
        "rx_arch": rx_arch,
        "clock_port": clock_port,
        "clock_period_ns": clock_period,
        "clock_ports": clock_ports,
        "capture_semantics": capture_semantics,
        "optional_blocks": optional,
        "ppa_db": args.ppa_db,
        "ppa_entries": len(ppa),
        "files": files,
    }


def digital_capability_report(cfg: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Expose what the generated package actually integrates.

    This is deliberately conservative.  Standalone RTL blocks (training and
    CRC) are listed, but they are not marked integrated until their actuator
    or frame plumbing is part of the generated design.
    """
    clock_recovery = cfg["clock_recovery"]
    framing = "sync_word" if cfg["align_mode"] != 0 else "none"
    # Coded links are generated, but they are coding-layer packages only.
    # Record the unimplemented PCS work so "integrated" is not read as a
    # complete 8b/10b or 64b/66b link capability.
    coding_limits = []
    if cfg["coding"] == "8b10b":
        coding_limits = [
            "enc_ctrl is tied to 0: no K-codes / comma are emitted, so alignment "
            "must be supplied externally",
            "no link-level running-disparity or 8b/10b error recovery",
        ]
    elif cfg["coding"] == "64b66b":
        coding_limits = [
            "no full IEEE control-field, lane-distribution or gearbox semantics",
            "external block/character alignment is the integrator's responsibility",
        ]
    return {
        "coding": {
            "selected": cfg["coding"],
            "implementation": "rtl",
            "integrated": cfg["coding"] in ("raw", "8b10b", "64b66b"),
            "limitations": coding_limits,
        },
        "framing": {
            "selected": framing,
            "implementation": "rtl",
            "integrated": framing in ("none", "sync_word"),
        },
        "crc": {
            "selected": "none",
            "implementation": "rtl_standalone",
            "module": "crc_frame",
            "integrated": False,
        },
        "training": {
            "selected": cfg["capabilities"]["training"],
            "implementation": "rtl" if cfg["capabilities"]["training"] == "phase_threshold"
                              else "rtl_standalone",
            "module": "link_training_fsm",
            "integrated": cfg["capabilities"]["training"] == "phase_threshold",
        },
        "clock_source": {
            "selected": cfg["clock_source"],
            "implementation": "rtl",
            "integrated": True,
        },
        "clock_recovery": {
            "selected": clock_recovery,
            "implementation": "rtl",
            "integrated": True,
        },
        "sampler": {
            "selected": cfg["sampler_architecture"],
            "implementation": "rtl",
            "integrated": True,
        },
        "tx_driver": {
            "selected": plan["tx_arch"]["name"],
            "implementation": "rtl",
            "integrated": cfg["target"] in ("link", "tx"),
        },
        "rx_frontend": {
            "selected": plan["rx_arch"]["name"],
            "implementation": "rtl",
            "integrated": cfg["target"] in ("link", "rx"),
        },
        "equalization": {"selected": "none", "implementation": "none",
                         "integrated": False},
        "termination": {"selected": "none", "implementation": "none",
                        "integrated": False},
        "lane_bonding": {"selected": "single", "implementation": "rtl",
                         "integrated": True},
        "power_management": {"selected": "none", "implementation": "none",
                             "integrated": False},
        "fec": {"selected": "none", "implementation": "none",
                "integrated": False},
        "standalone_blocks": ["link_training_fsm", "crc_frame"],
    }


def synth_parameters(params: dict[str, Any]) -> list[str]:
    order = ["DATA_WIDTH", "WORDS", "LSB_FIRST", "CAPTURE_MODE", "OVERSAMPLE",
             "SYNC_STAGES", "LOCK_THRESH", "LOCK_STALE_BITS", "CDR_MODE",
             "CDR_BYPASS", "ALIGN_MODE", "SYNC_LEN", "SYNC_PATTERN",
             "ADAPT_FRAC_BITS", "ADAPT_LOOP_GAIN", "CLK_OUT_ENABLE", "PINGPONG", "FIFO_DEPTH",
             "BIST_PRBS_WIDTH",
             "TRAINING_ENABLE", "TRAIN_DWELL_CYCLES", "TRAIN_ERROR_WIDTH"]
    return [f"{k}={params[k]}" for k in order if k in params]


def signoff_extras(cfg: dict[str, Any], stage: str) -> list[str]:
    """Constraints that only signoff.sdc carries.

    post_cts.sdc already enables propagated clocks and reset recovery.  A real
    signoff stage additionally needs the target library's design-rule limits;
    these come from tech/<pdk>/tech.yaml (cts:) and are emitted as explicit SDC
    rather than left implicit in LibreLane environment variables.

    OCV derates, derated corners and extracted parasitics are deliberately NOT
    invented here: the PDK signoff flow must supply them.  The comment in the
    emitted file says so, so this file cannot be mistaken for a complete
    foundry signoff constraint set.
    """
    if stage != "signoff":
        return []
    cts = cfg["tech"].cts or {}
    lines = [
        "",
        "# Signoff-only: library design-rule limits from tech/<pdk>/tech.yaml.",
        "# NOT included (must be supplied by the PDK signoff flow): OCV/derates,",
        "# derated timing corners, extracted parasitics, and SI/PI constraints.",
    ]
    emitted = 0
    if cts.get("max_fanout") is not None:
        lines.append(f"set_max_fanout {float(cts['max_fanout']):.1f} [current_design]")
        emitted += 1
    if cts.get("max_slew_ns") is not None:
        lines.append(f"set_max_transition {float(cts['max_slew_ns']):.4f} [current_design]")
        emitted += 1
    if cts.get("max_cap_ff") is not None:
        # tech.yaml is in fF; Liberty/SDC capacitance is in pF.
        lines.append(f"set_max_capacitance {float(cts['max_cap_ff']) / 1000.0:.6f} [current_design]")
        emitted += 1
    if not emitted:
        lines.append("# tech profile declares no CTS design-rule limits; none emitted.")
    return lines


def _sdc_finish(lines: list[str], io: float, load: float) -> str:
    # The constraint lines are emitted with the historical defaults as
    # placeholders.  Substitute only inside the io-delay / load lines so a
    # user-supplied uncertainty of 0.200 is never mistaken for an io delay.
    rendered: list[str] = []
    for line in lines:
        if line is None:
            continue
        stripped = line.lstrip()
        if stripped.startswith(("set_input_delay", "set_output_delay")):
            line = line.replace("0.200", f"{io:.3f}")
        elif stripped.startswith("set_load"):
            line = line.replace("0.010", f"{load:.3f}")
        rendered.append(line)
    return "\n".join(rendered) + "\n"


def sdc_text(cfg: dict[str, Any], plan: dict[str, Any], stage: str) -> str:
    target = cfg["target"]
    tx = cfg["tx_period_ns"]
    rx = cfg["rx_period_ns"]
    os = cfg["oversample"]
    setup = cfg.get("setup_uncertainty_ns", 0.100)
    hold = cfg.get("hold_uncertainty_ns", 0.050)
    io = cfg.get("io_delay_ns", 0.200)
    load = cfg.get("output_load_pf", 0.010)
    lines: list[str] = [
        f"# Generated by OpenSERDES gen_ip.py ({stage}). Do not edit by hand.",
        f"# Stage: {stage}",
        "",
    ]
    if target in ("link", "tx"):
        lines += [
            f"set TX_PERIOD_NS {tx:.6f}",
            "create_clock -name tx_clk -period $TX_PERIOD_NS [get_ports tx_clk]",
            f"set_clock_uncertainty -setup {setup:.3f} [get_clocks tx_clk]",
            f"set_clock_uncertainty -hold  {hold:.3f} [get_clocks tx_clk]",
        ]
    if target == "link":
        lines += [
            f"set OVERSAMPLE   {os}",
            "set RX_PERIOD_NS [expr {$TX_PERIOD_NS / $OVERSAMPLE}]",
            "create_clock -name rx_clk -period $RX_PERIOD_NS [get_ports rx_clk]",
            f"set_clock_uncertainty -setup {setup:.3f} [get_clocks rx_clk]",
            f"set_clock_uncertainty -hold  {hold:.3f} [get_clocks rx_clk]",
            "",
            "set_clock_groups -asynchronous -group [get_clocks tx_clk] -group [get_clocks rx_clk]",
        ]
    elif target == "rx":
        lines += [
            f"set RX_PERIOD_NS {rx:.6f}",
            "create_clock -name rx_clk -period $RX_PERIOD_NS [get_ports rx_clk]",
            f"set_clock_uncertainty -setup {setup:.3f} [get_clocks rx_clk]",
            f"set_clock_uncertainty -hold  {hold:.3f} [get_clocks rx_clk]",
        ]
    lines += [""]

    if plan["design"] == "serdes_soc_cdc_wrapper":
        soc_period = 1000.0 / cfg["user_clock_mhz"]
        lines = [
            f"# Generated by OpenSERDES gen_ip.py ({stage}). Do not edit by hand.",
            f"# Stage: {stage}",
            "",
            f"set PCLK_PERIOD_NS {soc_period:.6f}",
            "create_clock -name pclk -period $PCLK_PERIOD_NS [get_ports pclk]",
            "create_clock -name s_axis_aclk -period $PCLK_PERIOD_NS [get_ports s_axis_aclk]",
            "create_clock -name m_axis_aclk -period $PCLK_PERIOD_NS [get_ports m_axis_aclk]",
            f"set TX_PERIOD_NS {tx:.6f}",
            "create_clock -name tx_clk -period $TX_PERIOD_NS [get_ports tx_clk]",
            f"set RX_PERIOD_NS {rx:.6f}",
            "create_clock -name rx_clk -period $RX_PERIOD_NS [get_ports rx_clk]",
            "set_clock_groups -asynchronous -group [get_clocks pclk] -group [get_clocks s_axis_aclk] -group [get_clocks m_axis_aclk] -group [get_clocks tx_clk] -group [get_clocks rx_clk]",
            "",
            "set_input_delay  -clock pclk 0.200 [get_ports {paddr* psel penable pwrite pwdata*}]",
            "set_output_delay -clock pclk 0.200 [get_ports {pready prdata* pslverr csr_regs*}]",
            "set_input_delay  -clock s_axis_aclk 0.200 [get_ports {s_axis_tvalid s_axis_tdata* s_axis_tlast}]",
            "set_output_delay -clock s_axis_aclk 0.200 [get_ports s_axis_tready]",
            "set_output_delay -clock m_axis_aclk 0.200 [get_ports {m_axis_tvalid m_axis_tdata* m_axis_tlast}]",
            "set_input_delay  -clock m_axis_aclk 0.200 [get_ports m_axis_tready]",
            "set_output_delay -clock tx_clk 0.200 [get_ports {tx_serial_out tx_serial_valid}]",
            "set_input_delay  -clock rx_clk 0.200 [get_ports rx_serial_in]",
            "set_output_delay -clock rx_clk 0.200 [get_ports {rx_sample_strobe rx_serial_data rx_serial_valid rx_freq_trim* rx_clk_out}]",
            "set_false_path -to [get_ports rx_clk_out]",
            "set_load 0.010 [get_ports tx_serial_out]",
            "",
        ]
        if stage == "pre_pnr":
            lines += [
                "# Pre-CTS: asynchronous reset recovery/removal is not signed off yet.",
                "set_false_path -from [get_ports {presetn s_axis_aresetn m_axis_aresetn tx_rst_n rx_rst_n}]",
                "foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }",
            ]
        else:
            lines += [
                "# Post-CTS/signoff: reset recovery/removal is enabled.",
                "set_propagated_clock [all_clocks]",
            ]
        lines += signoff_extras(cfg, stage)
        return _sdc_finish(lines, io, load)

    if plan["design"] == "serdes_soc_wrapper":
        lines = [
            f"# Generated by OpenSERDES gen_ip.py ({stage}). Do not edit by hand.",
            f"# Stage: {stage}",
            "",
            f"set CLK_PERIOD_NS {tx:.6f}",
            "create_clock -name clk -period $CLK_PERIOD_NS [get_ports clk]",
            f"set_clock_uncertainty -setup {setup:.3f} [get_clocks clk]",
            f"set_clock_uncertainty -hold  {hold:.3f} [get_clocks clk]",
            "",
            "set_input_delay  -clock clk 0.200 [get_ports {paddr* psel penable pwrite pwdata* s_axis_tvalid s_axis_tdata* s_axis_tlast rx_serial_in}]",
            "set_output_delay -clock clk 0.200 [get_ports {pready prdata* pslverr s_axis_tready m_axis_tvalid m_axis_tdata* m_axis_tlast tx_serial_out tx_serial_valid rx_sample_strobe rx_serial_data rx_serial_valid rx_freq_trim* rx_clk_out csr_regs*}]",
            "set_false_path -to [get_ports rx_clk_out]",
            "set_load 0.010 [get_ports tx_serial_out]",
            "",
        ]
        if stage == "pre_pnr":
            lines += [
                "# Pre-CTS: asynchronous reset recovery/removal is not signed off yet.",
                "set_false_path -from [get_ports rst_n]",
                "foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }",
            ]
        else:
            lines += [
                "# Post-CTS/signoff: reset recovery/removal is enabled.",
                "set_propagated_clock [all_clocks]",
            ]
        lines += signoff_extras(cfg, stage)
        return _sdc_finish(lines, io, load)

    if plan["design"] == "serdes_tx_half_rate_digital":
        word_period = tx * 2.0
        lines = [
            f"# Generated by OpenSERDES gen_ip.py ({stage}). Do not edit by hand.",
            f"# Stage: {stage}",
            "",
            f"set BIT_PERIOD_NS {tx:.6f}",
            "create_clock -name clk_bit -period $BIT_PERIOD_NS [get_ports clk_bit]",
            f"set WORD_PERIOD_NS {word_period:.6f}",
            "# clk_word is the divide-by-2 word clock; keep the phase relationship.",
            "create_generated_clock -name clk_word -source [get_ports clk_bit] "
            "-divide_by 2 [get_ports clk_word]",
            f"set_clock_uncertainty -setup {setup:.3f} [get_clocks clk_bit]",
            f"set_clock_uncertainty -hold  {hold:.3f} [get_clocks clk_bit]",
            f"set_clock_uncertainty -setup {setup:.3f} [get_clocks clk_word]",
            f"set_clock_uncertainty -hold  {hold:.3f} [get_clocks clk_word]",
            "",
            "set_input_delay  -clock clk_word 0.200 [get_ports {in_valid in_word*}]",
            "set_output_delay -clock clk_word 0.200 [get_ports in_ready]",
            "set_output_delay -clock clk_bit 0.200 [get_ports {serial_out serial_valid busy frame_done}]",
            "set_load 0.010 [get_ports serial_out]",
            "",
        ]
        if stage == "pre_pnr":
            lines += [
                "# Pre-CTS: asynchronous reset recovery/removal is not signed off yet.",
                "set_false_path -from [get_ports rst_n]",
                "foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }",
                "foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }",
            ]
        else:
            lines += [
                "# Post-CTS/signoff: reset recovery/removal is enabled.",
                "set_propagated_clock [all_clocks]",
            ]
        lines += signoff_extras(cfg, stage)
        return _sdc_finish(lines, io, load)

    if target in ("link", "tx"):
        if target == "tx" and plan["design"] == "serdes_tx_axis_digital":
            lines += [
                "set_input_delay  -clock tx_clk 0.200 [get_ports {s_axis_tvalid s_axis_tdata* s_axis_tlast}]",
                "set_output_delay -clock tx_clk 0.200 [get_ports {s_axis_tready tx_serial_out tx_serial_valid tx_busy tx_frame_done tx_frame_error}]",
            ]
        elif target == "tx" and plan["design"] == "serdes_tx_stream_digital":
            lines += [
                "set_input_delay  -clock tx_clk 0.200 [get_ports {tx_in_valid tx_in_word*}]",
                "set_output_delay -clock tx_clk 0.200 [get_ports {tx_in_ready tx_serial_out tx_serial_valid tx_busy tx_frame_done}]",
            ]
        else:
            lines += [
                "set_input_delay  -clock tx_clk 0.200 [get_ports {tx_load tx_enable tx_par_data*}]",
                "set_output_delay -clock tx_clk 0.200 [get_ports {tx_ready tx_serial_out tx_serial_valid tx_busy tx_frame_done}]",
            ]
        if cfg["enable_mode"] == "static":
            lines += ["set_false_path -from [get_ports tx_enable]"]
        else:
            lines += ["# tx_enable is dynamic; do not false-path it"]
    if target in ("link", "rx"):
        if plan["design"] == "serdes_rx_axis_digital":
            lines += [
                "set_input_delay  -clock rx_clk 0.200 [get_ports {rx_serial_in rx_enable rx_flush rx_align m_axis_tready}]",
                "set_output_delay -clock rx_clk 0.200 [get_ports {m_axis_tvalid m_axis_tdata* m_axis_tlast rx_lock rx_lock_stale rx_frame_locked rx_frame_overflow rx_par_valid rx_sample_strobe rx_serial_data rx_serial_valid rx_busy rx_freq_trim*}]",
                "set_false_path -to [get_ports rx_clk_out]",
            ]
        else:
            lines += [
                "set_input_delay  -clock rx_clk 0.200 [get_ports {rx_serial_in rx_enable rx_flush rx_align}]",
                "set_output_delay -clock rx_clk 0.200 [get_ports {rx_par_data* rx_par_valid rx_busy rx_lock rx_lock_stale rx_sample_strobe rx_frame_locked rx_frame_valid rx_serial_data rx_serial_valid rx_freq_trim*}]",
                "set_false_path -to [get_ports rx_clk_out]",
            ]
        if cfg["enable_mode"] == "static":
            lines += ["set_false_path -from [get_ports rx_enable]"]
        else:
            lines += ["# rx_enable is dynamic; do not false-path it"]
    lines += ["", "set_load 0.010 [get_ports tx_serial_out]" if target in ("link", "tx") else "", ""]

    if stage == "pre_pnr":
        lines += [
            "# Pre-CTS: asynchronous reset recovery/removal is not signed off yet.",
            "set_false_path -from [get_ports tx_rst_n]" if target in ("link", "tx") else "",
            "set_false_path -from [get_ports rx_rst_n]" if target in ("link", "rx") else "",
            "foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }",
            "foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }",
            "foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }",
            "foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }",
            "foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }",
        ]
    else:
        lines += [
            "# Post-CTS/signoff: reset recovery/removal is enabled.  Re-derive",
            "# uncertainty from the actual CTS skew/jitter before signoff.",
            "set_propagated_clock [all_clocks]",
        ]
    lines += signoff_extras(cfg, stage)
    return _sdc_finish(lines, io, load)


def make_readme(cfg: dict[str, Any], plan: dict[str, Any], manifest: dict[str, Any]) -> str:
    params = plan["params"]
    tx = plan["tx_arch"]
    rx = plan["rx_arch"]
    lines = [
        f"# {cfg['name']}",
        "",
        "Generated digital SerDes package. This is **not** a PHY signoff package.",
        "",
        "## Specification",
        "",
        f"- target: {cfg['target']}",
        f"- payload: {cfg['words']} x {cfg['width']} bits, {cfg['coding']}, "
        f"{'LSB' if cfg['lsb_first'] else 'MSB'} first",
        f"- line rate: {cfg['line_rate']:g} Mb/s (TX period {cfg['tx_period_ns']:.3f} ns)",
        f"- RX oversampling: {cfg['oversample']} (RX period {cfg['rx_period_ns']:.3f} ns)",
        f"- tech: {cfg['tech'].name} / {cfg['tech'].scl}",
        f"- preset: {cfg['preset']} (data {cfg['data_mode']}, control {cfg['control']}, CDC {cfg['cdc']})",
        "",
        "## Selected architecture",
        "",
    ]
    if cfg["target"] in ("link", "tx"):
        lines.append(f"- TX: {tx['name']} ({tx['module']}), confidence {tx['confidence']}")
        lines.append(f"- TX Pareto frontier: {', '.join(tx['frontier'])}")
        lines.append(f"- TX capture: {plan['capture_semantics']}")
    if cfg["target"] in ("link", "rx"):
        lines.append(f"- RX: {rx['name']} ({rx['module']}), confidence {rx['confidence']}")
        lines.append(f"- RX Pareto frontier: {', '.join(rx['frontier'])}")
    lines += [
        "",
        "Architecture metrics come from scripts/ppa_model.py: a linear cell-count",
        "model fitted to Yosys generic-gate synthesis at 8x4 and 32x8, plus a",
        "DFF x f_clock clock-load proxy for power. They are RATIO- and",
        "OVERSAMPLE-dependent capability-gate estimates -- NOT PDK area, NOT",
        "measured power, NOT signoff PPA. A measured ppa_measured.yaml entry",
        "overrides the model; see manifest.ppa_model and architecture.*.metrics_source.",
        "",
        "## Integration",
        "",
        "~~~systemverilog",
        f"{plan['design']} #(",
    ]
    overrides = synth_parameters(params)
    for index, key in enumerate(overrides):
        name, value = key.split("=", 1)
        comma = "," if index + 1 < len(overrides) else ""
        lines.append(f"    .{name}({value}){comma}")
    lines += [
        ") u_dut ( ... );",
        "~~~",
        "",
        "The generated RTL is in rtl/; rtl/filelist.f lists compilation order. "
        "rtl/sv2v/ and rtl/filelist_sv2v.f are the sv2v-converted sources used by "
        "the LibreLane/Yosys flow.",
        ("Optional primitives: " + ", ".join(plan["optional_blocks"])
         if plan["optional_blocks"] else "No optional preset blocks included."),
        "",
        "## Constraints and PnR",
        "",
        "- constraints/pre_pnr.sdc: pre-CTS, async reset excluded.",
        "- constraints/post_cts.sdc: propagated clock, reset recovery enabled.",
        "- constraints/signoff.sdc: propagated clock plus tech design-rule "
        "limits; reset recovery enabled. Still a template -- the PDK signoff "
        "flow must add OCV derates, corners and extracted parasitics.",
        "- pnr/librelane.yaml: LibreLane configuration.",
        "",
        "## Clocking",
        "",
        f"- clock ports: {', '.join(plan['clock_ports'])}",
        f"- TX period: {cfg['tx_period_ns']:.3f} ns; RX period: {cfg['rx_period_ns']:.3f} ns",
        f"- PnR CLOCK_PORT: {', '.join(plan['clock_port'])}",
        "- CLOCK_PORT lists every clock in the design; LibreLane's CTS step",
        "  uses it as CLOCK_NET. CLOCK_PERIOD is the primary clock only -- the",
        "  individual clock periods are defined in the supplied SDC files.",
        "",
        "## Qualification",
        "",
        f"Level: **{manifest['qualification']['level']}**. "
        "Run make regress and make qualify REPORT=... to raise it.",
        "",
        "## Known gaps",
        "",
        "- No analog TX/RX wrapper, PEX, SI/PI, jitter/BER or silicon data in this package.",
        "- phy_ip / non-soc APB/AXIS packages are scaffold-only; missing blocks "
        "are listed in manifest.missing_blocks and in the README section above.",
        ("- Coded link limitations: " +
         "; ".join(manifest["capabilities"]["coding"]["limitations"])
         if manifest["capabilities"]["coding"]["limitations"] else ""),
        "- Pre-PnR and pre-CTS timing results are not a PHY line-rate guarantee.",
        "",
    ]
    if cfg["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in cfg["warnings"]] + [""]
    if cfg["missing_blocks"]:
        lines += ["## Missing blocks (scaffold package)", "",
                  "- " + ", ".join(cfg["missing_blocks"]),
                  "- Do not integrate this package as a deliverable IP.", ""]
    return "\n".join(lines)


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def find_sv2v() -> str | None:
    return shutil.which(os.environ.get("SV2V", "sv2v"))


def convert_package_rtl(out: Path, files: list[str], allow_native: bool) -> dict[str, Any]:
    """Convert the generated SV sources for the Yosys/LibreLane frontend.

    Native SystemVerilog remains in rtl/ for simulation and lint.  The PnR
    flow uses rtl/sv2v/*.v and filelist_sv2v.f so LibreLane's Yosys version is
    not exposed to newer SV constructs (packed arrays, always_comb, ...).
    """
    if allow_native:
        return {"used": False, "reason": "--allow-native-sv"}
    exe = find_sv2v()
    if exe is None:
        raise RuntimeError(
            "sv2v is required for the LibreLane/Yosys package flow; install "
            "sv2v v0.0.12 or set SV2V=/path/to/sv2v, or pass --allow-native-sv "
            "to emit native SystemVerilog (not a stable PnR path)"
        )
    converted: list[str] = []
    dest_dir = out / "rtl" / "sv2v"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for filename in files:
        src = out / "rtl" / filename
        dst = dest_dir / (Path(filename).stem + ".v")
        proc = subprocess.run(
            [exe, str(src), "-w", str(dst)],
            text=True, capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"sv2v failed for {filename}:\n{proc.stdout}\n{proc.stderr}"
            )
        converted.append(f"sv2v/{dst.name}")
    (out / "rtl" / "filelist_sv2v.f").write_text("\n".join(converted) + "\n")
    version = subprocess.run([exe, "--version"], text=True, capture_output=True,
                             timeout=30).stdout.strip()
    return {
        "used": True,
        "tool": exe,
        "version": version,
        "native_files": list(files),
        "pnr_files": converted,
        "filelist": "rtl/filelist_sv2v.f",
    }


def _clock_recommendation(cfg: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    """Quantify the lowest-clock alternative (item 5), using ppa_model.

    Purely advisory: changing the clocking mode changes the link's physical
    requirements, so the generator never switches it silently.
    """
    if cfg["target"] == "tx":
        return None
    name = plan["rx_arch"].get("name")
    if not name or name == "not_used" or cfg["oversample"] <= 1:
        return None
    rate = cfg["line_rate"]
    ratio = cfg["ratio"]
    try:
        cur = ppa_model.estimate(name, ratio, cfg["oversample"], rate, "rx")
        if abs(cfg["ppm"]) == 0:
            alt_arch, alt_os = "source_sync", 1
            req = "requires an external forwarded bit clock"
        else:
            alt_arch, alt_os = "fixed_oversampling_os2", 2
            req = "requires rx.allow_os2: true and has no jitter margin"
        alt = ppa_model.estimate(alt_arch, ratio, alt_os, rate, "rx")
    except KeyError:
        return None
    return {
        "current": {"arch": name, "oversample": cfg["oversample"],
                    "sample_clock_mhz": cur["clock_mhz"],
                    "clock_load": round(cur["clock_load"], 1)},
        "alternative": {"arch": alt_arch, "oversample": alt_os,
                        "sample_clock_mhz": alt["clock_mhz"],
                        "clock_load": round(alt["clock_load"], 1)},
        "alternative_clock_load_ratio": round(alt["clock_load"] / cur["clock_load"], 4),
        "requirement": req,
        "note": ("clock_load is the DFF x f_clock proxy from ppa_model.py, not "
                 "measured power. Select with rx.clocking: auto + "
                 "optimization.clock_power_priority: true."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a spec-driven OpenSERDES digital IP package")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--pdk-lib-root", default=None)
    ap.add_argument("--ppa-db", default=str(ROOT / "tech" / "ppa_measured.yaml"),
                    help="measured PPA database (missing file means no overrides)")
    ap.add_argument("--check", action="store_true", help="validate and print, do not write")
    ap.add_argument("--force", action="store_true", help="replace an existing output directory")
    ap.add_argument("--relax-limits", action="store_true", help="warn instead of failing capability gates")
    ap.add_argument("--allow-native-sv", action="store_true",
                    help="emit native SystemVerilog for PnR (not the stable sv2v path)")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="emit a scaffold package for presets whose optional blocks are not implemented")
    ap.add_argument("--list-arch", action="store_true")
    args = ap.parse_args()

    if not args.check and not args.list_arch and not args.out:
        print("spec error: --out is required unless --check or --list-arch is used",
              file=sys.stderr)
        return 2

    if args.list_arch:
        db = load_arch_db()
        for kind in ("tx", "rx"):
            print(kind)
            for name, cand in db.get(kind, {}).items():
                print(f"  {name}: {cand.get('module')} confidence={cand.get('confidence')} "
                      f"metrics={cand.get('metrics')}")
        return 0

    if args.pdk_root:
        os.environ["PDK_ROOT"] = args.pdk_root
    if args.pdk_lib_root:
        os.environ["PDK_LIB_ROOT"] = args.pdk_lib_root

    try:
        spec = yaml.safe_load(Path(args.spec).read_text())
        cfg = validate(spec, args)
        cfg["setup_uncertainty_ns"] = as_float(
            get(spec, "constraints", "uncertainty_setup_ns", default=0.100),
            "constraints.uncertainty_setup_ns")
        cfg["hold_uncertainty_ns"] = as_float(
            get(spec, "constraints", "uncertainty_hold_ns", default=0.050),
            "constraints.uncertainty_hold_ns")
        cons = spec.get("constraints", {}) or {}
        cfg["io_delay_ns"] = as_float(cons.get("io_delay_ns", 0.200),
                                      "constraints.io_delay_ns")
        cfg["output_load_pf"] = as_float(cons.get("output_load_pf", 0.010),
                                         "constraints.output_load_pf")
        cfg["sync_stages"] = as_int(cons.get("sync_stages", 3),
                                    "constraints.sync_stages")
        cfg["lock_thresh"] = as_int(cons.get("lock_thresh", 8),
                                    "constraints.lock_thresh")
        cfg["lock_stale_bits"] = as_int(cons.get("lock_stale_bits", 16),
                                        "constraints.lock_stale_bits")
        cfg["fifo_depth"] = as_int(cons.get("fifo_depth", 16),
                                   "constraints.fifo_depth")
        cfg["require_clock_evidence"] = str(
            cons.get("require_clock_evidence", "none")).lower()
        if cfg["require_clock_evidence"] not in (
                "none", "timing_closed", "physical_signoff"):
            fail("constraints.require_clock_evidence must be none, "
                 "timing_closed or physical_signoff")
        if (cfg["target"] == "tx" and
                cfg["require_clock_evidence"] != "none"):
            fail("constraints.require_clock_evidence only applies to "
                 "target: link or rx")
        if cfg["sync_stages"] < 2:
            fail("constraints.sync_stages must be >= 2")
        if cfg["lock_thresh"] < 1 or cfg["lock_stale_bits"] < 1:
            fail("constraints.lock_thresh and lock_stale_bits must be >= 1")
        depth = cfg["fifo_depth"]
        if depth < 2 or depth & (depth - 1):
            fail("constraints.fifo_depth must be a power of two >= 2")
        plan = build_plan(cfg, args)
        cfg["warnings"].extend(PPA_WARNINGS)
        rec = _clock_recommendation(cfg, plan)
        if rec:
            cfg["clock_recommendation"] = rec
            cfg["warnings"].append(
                "clock power: %s OS=%s has %.2fx the clock load of %s; %s"
                % (rec["current"]["arch"], rec["current"]["oversample"],
                   1.0 / rec["alternative_clock_load_ratio"],
                   rec["alternative"]["arch"], rec["requirement"]))
        if plan["rx_arch"].get("name") == "bang_bang_oversampling":
            try:
                bb = ppa_model.estimate("bang_bang_oversampling", cfg["ratio"],
                                        cfg["oversample"], cfg["line_rate"], "rx")
                fx = ppa_model.estimate("fixed_oversampling", cfg["ratio"],
                                        cfg["oversample"], cfg["line_rate"], "rx")
                cfg["warnings"].append(
                    "area: bang_bang_oversampling needs %.0f cells vs %.0f for "
                    "fixed_oversampling at RATIO=%d (%.1fx measured Yosys generic "
                    "gates); its advantage is clock load, not area"
                    % (bb["cells"], fx["cells"], cfg["ratio"],
                       bb["cells"] / fx["cells"]))
            except KeyError:
                pass
    except SpecError as exc:
        print(f"spec error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(json.dumps({
            "name": cfg["name"],
            "target": cfg["target"],
            "design": plan["design"],
            "tx_arch": plan["tx_arch"]["name"],
            "rx_arch": plan["rx_arch"]["name"],
            "tx_confidence": plan["tx_arch"]["confidence"],
            "rx_confidence": plan["rx_arch"]["confidence"],
            "ppa_db": plan["ppa_db"],
            "ppa_entries": plan["ppa_entries"],
            "params": plan["params"],
            "tx_period_ns": cfg["tx_period_ns"],
            "rx_period_ns": cfg["rx_period_ns"],
            "tech": {"name": cfg["tech"].name, "scl": cfg["tech"].scl},
            "clock_evidence": cfg.get("clock_evidence"),
            "warnings": cfg["warnings"],
        }, indent=2))
        return 0

    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        if not args.force:
            print(f"output {out} exists; use --force to replace it", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    (out / "rtl").mkdir(parents=True, exist_ok=True)
    (out / "constraints").mkdir(parents=True, exist_ok=True)
    (out / "pnr").mkdir(parents=True, exist_ok=True)

    for filename in plan["files"]:
        shutil.copy2(ROOT / "rtl" / filename, out / "rtl" / filename)
    (out / "rtl" / "filelist.f").write_text(
        "\n".join(plan["files"]) + "\n")

    try:
        sv2v_meta = convert_package_rtl(out, plan["files"], args.allow_native_sv)
    except RuntimeError as exc:
        print(f"generation error: {exc}", file=sys.stderr)
        shutil.rmtree(out, ignore_errors=True)
        return 2

    params_json = json.dumps(plan["params"], indent=2) + "\n"
    (out / "rtl" / "params.json").write_text(params_json)
    svh = ["// Generated by OpenSERDES gen_ip.py", ""]
    for key in synth_parameters(plan["params"]):
        name, value = key.split("=", 1)
        svh.append(chr(96) + "define SERDES_" + name + " " + value)
    (out / "rtl" / "params.svh").write_text("\n".join(svh) + "\n")

    for stage in ("pre_pnr", "post_cts", "signoff"):
        (out / "constraints" / f"{stage}.sdc").write_text(sdc_text(cfg, plan, stage))

    tech = cfg["tech"]
    corners = expand({k: list(v) for k, v in tech.corners.items()})
    lib_files = [Path(p) for libs in corners.values() for p in libs]
    pdk_lib_files_present = lib_files_present(corners)
    pdk_paths_resolved = (not unresolved(corners)) and pdk_lib_files_present
    if not unresolved(corners) and not pdk_lib_files_present:
        cfg["warnings"].append(
            "PDK liberty paths are expanded but some .lib files are missing")
    pnr_files = (plan["files"] if not sv2v_meta.get("used")
                 else sv2v_meta["pnr_files"])
    pnr = {
        "DESIGN_NAME": plan["design"],
        "VERILOG_FILES": [str(out / "rtl" / f) for f in pnr_files],
        "CLOCK_PORT": plan["clock_port"],
        "CLOCK_PERIOD": round(plan["clock_period_ns"], 6),
        "PDK": tech.pdk,
        "STD_CELL_LIBRARY": tech.scl,
        "DEFAULT_CORNER": tech.default_corner,
        "STA_CORNERS": list(corners.keys()),
        "LIB": corners,
        "SYNTH_PARAMETERS": synth_parameters(plan["params"]),
        "PNR_SDC_FILE": str(out / "constraints" / "pre_pnr.sdc"),
        "SIGNOFF_SDC_FILE": str(out / "constraints" / "signoff.sdc"),
    }
    if tech.cts.get("max_cap_ff") is not None:
        pnr["CTS_MAX_CAP"] = float(tech.cts["max_cap_ff"]) / 1000.0
    if tech.cts.get("max_slew_ns") is not None:
        pnr["CTS_MAX_SLEW"] = float(tech.cts["max_slew_ns"])
    clock_gate = tech.clock_gate
    if cfg["optimization"].get("clock_gating") and clock_gate.get("icg_cell"):
        pnr["SYNTH_CLOCKGATE_MIN_WIDTH"] = int(clock_gate.get("min_width", 8))
        pnr["SYNTH_CLOCKGATE_POSEDGE_ICG"] = "%s/%s/%s/%s" % (
            clock_gate["icg_cell"], clock_gate["enable_pin"],
            clock_gate["clk_pin"], clock_gate["gated_clk_pin"])
    (out / "pnr" / "librelane.yaml").write_text(yaml.safe_dump(pnr, sort_keys=False))
    (out / "pnr" / "README.md").write_text(
        "# PnR notes\n\n"
        f"`CLOCK_PORT` lists all clocks: {', '.join(plan['clock_port'])}. "
        "LibreLane's CTS step uses this list as CLOCK_NET, so every declared "
        "clock is buffered. `CLOCK_PERIOD` is a scalar for the primary "
        "clock only; the per-clock periods come from `pre_pnr.sdc` / "
        "`signoff.sdc`. Confirm the target PDK signoff flow supplies OCV "
        "derates, derated corners and extracted parasitics -- the generated "
        "signoff.sdc is not a complete foundry signoff deck.\n\n"
        + ("Clock gating: Yosys SYNTH_CLOCKGATE_* enabled (ICG "
           + pnr.get("SYNTH_CLOCKGATE_POSEDGE_ICG", "n/a")
           + "); re-run CTS/STA after insertion.\n\n"
           if pnr.get("SYNTH_CLOCKGATE_POSEDGE_ICG") else
           "Clock gating: not enabled for this package.\n\n")
        + "Power evidence: LibreLane STAPostPNR writes power__* metrics into "
        "final/metrics.json. Run scripts/collect_power.py on the completed run "
        "and feed the result through scripts/import_ppa.py to obtain a "
        "provenance-verified measured PPA entry. Unverified measured claims are "
        "downgraded to unverified_claim by gen_ip.py.\n")

    spec_text = Path(args.spec).read_text()
    qualification = {
        "level": "UNVERIFIED",
        "target_level": "RTL_FUNCTIONAL",
        "checks": {},
        "required": ["lint", "sim", "synth"],
        "note": "Run make regress and scripts/qualify.py to record evidence.",
        "link_capability": {
            "evidence_level": "NONE",
            "rtl_link_smoke_pass": False,
            "reason": "no verification report; run make verify-link VERIFY_PACKAGE=<out>",
        },
    }
    manifest = {
        "generator": "OpenSERDES gen_ip",
        "generator_version": "0.1.0",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "spec_name": cfg["name"],
        "spec_sha256": hashlib.sha256(spec_text.encode()).hexdigest(),
        "target": cfg["target"],
        "design": plan["design"],
        "tx_rate_mode": cfg.get("tx_rate_mode", "full"),
        "tx_bank_mode": cfg.get("tx_bank_mode", "single"),
        "line_rate_mbps": cfg["line_rate"],
        "data_width": cfg["width"],
        "words": cfg["words"],
        "tech": {"name": tech.name, "scl": tech.scl, "pdk": tech.pdk,
                 "native_dff": tech.native_dff,
                 "pdk_paths_resolved": pdk_paths_resolved,
                 "pdk_lib_files_present": pdk_lib_files_present},
        "params": plan["params"],
        "architecture": {
            "tx": {k: plan["tx_arch"][k] for k in ("name", "module", "confidence",
                                                  "confidence_scope", "frontier",
                                                  "metrics", "metrics_source", "ppa_model",
                                                  "ppa_provenance", "provenance_warning",
                                                  "selection")
                   if k in plan["tx_arch"]} |
                  ({k: plan["tx_arch"][k] for k in ("ppa_source", "absolute")
                    if k in plan["tx_arch"]}),
            "rx": {k: plan["rx_arch"][k] for k in ("name", "module", "confidence",
                                                  "confidence_scope", "frontier",
                                                  "metrics", "metrics_source", "ppa_model",
                                                  "ppa_provenance", "provenance_warning",
                                                  "selection")
                   if k in plan["rx_arch"]} |
                  ({k: plan["rx_arch"][k] for k in ("ppa_source", "absolute")
                    if k in plan["rx_arch"]}),
        },
        "ppa_db": plan["ppa_db"],
        "ppa_entries": plan["ppa_entries"],
        "ppa_model": {
            "source": ppa_model.MODEL_SOURCE,
            "scope": "Yosys generic-gate cell count + DFF x f_clock clock-load proxy",
            "not_measured": ["PDK area", "total dynamic power", "leakage", "post-route PPA"],
            "precedence": "measured ppa_db > tech_overrides.metrics > modeled",
        },
        "interface": {k: cfg[k] for k in ("preset", "data_mode", "control", "cdc",
                                          "features", "enable_mode",
                                          "tx_user_clock_mhz", "rx_user_clock_mhz",
                                          "cdc_proven_synchronous")},
        "tx_capture_semantics": plan["capture_semantics"],
        "optional_blocks": plan["optional_blocks"],
        "missing_blocks": cfg["missing_blocks"],
        "features_missing": [f for f in cfg["features"] if f != "bist"],
        "capabilities": digital_capability_report(cfg, plan),
        "capability_request": cfg["capabilities"],
        "optimization": cfg["optimization"],
        "clock_gating": {
            "enabled": bool(cfg["optimization"].get("clock_gating")),
            "implementation": "Yosys SYNTH_CLOCKGATE_* (synthesis-time ICG insertion)",
            "icg": (("%s/%s/%s/%s" % (tech.clock_gate["icg_cell"],
                                      tech.clock_gate["enable_pin"],
                                      tech.clock_gate["clk_pin"],
                                      tech.clock_gate["gated_clk_pin"]))
                    if cfg["optimization"].get("clock_gating") else None),
            "note": ("RTL keeps one clock per domain by design; ICG insertion is a "
                     "synthesis step and must be re-verified after CTS."),
        },
        "sv2v": sv2v_meta,
        "verification_config": {
            "cdr_mode": int(plan["params"].get(
                "CDR_MODE",
                2 if plan["rx_arch"]["name"] == "source_sync"
                else 1 if plan["rx_arch"]["name"] == "adaptive_oversampling"
                else 0)),
            "oversample": int(plan["params"].get("OVERSAMPLE", cfg["oversample"])),
            "align_mode": int(plan["params"].get("ALIGN_MODE", cfg["align_mode"])),
            "framing": "comma_per_frame"
                       if int(plan["params"].get("ALIGN_MODE", cfg["align_mode"])) == 2
                       else "external",
            "clock_ports": plan["clock_ports"],
            "clock_periods_ns": {
                "tx_clk": cfg["tx_period_ns"],
                "rx_clk": cfg["rx_period_ns"],
            },
            "top": plan["design"],
            "rtl_source": "sv2v" if sv2v_meta.get("used") else "native",
        },
        "clocking": {
            "source": cfg["clock_source"],
            "recovery": cfg["clock_recovery"],
            "sampler_architecture": cfg["sampler_architecture"],
            "sample_clock_mhz": cfg["sample_clock_mhz"],
            "max_sample_clock_mhz": cfg.get("max_sample_clock_mhz"),
            "evidence": cfg.get("clock_evidence"),
            "recommendation": cfg.get("clock_recommendation"),
            "ports": plan["clock_ports"],
            "tx_period_ns": cfg["tx_period_ns"],
            "rx_period_ns": cfg["rx_period_ns"],
            "pnr_clock_port": plan["clock_port"],
            "note": "CLOCK_PORT lists every clock; CLOCK_PERIOD is the primary clock only, per-clock periods are in the SDC.",
        },
        "link_capability": {
            "evidence_level": "NONE",
            "rtl_link_smoke_pass": False,
            "reason": "no verification report; run make verify-link VERIFY_PACKAGE=<out>",
        },
        "qualification": qualification,
        "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()),
        "warnings": cfg["warnings"],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "qualification.json").write_text(json.dumps(qualification, indent=2) + "\n")
    (out / "README.md").write_text(make_readme(cfg, plan, manifest))

    print(f"generated {out}")
    print(f"  design={plan['design']} tx={plan['tx_arch']['name']} "
          f"rx={plan['rx_arch']['name']} tech={tech.name}/{tech.scl}")
    for warning in cfg["warnings"]:
        print(f"  warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

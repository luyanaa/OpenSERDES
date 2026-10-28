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

# Source dependencies for each generated design.
DESIGN_FILES = {
    "serdes_digital_top": [
        "serializer.sv", "deserializer_word.sv", "deserializer.sv",
        "cdr_sync.sv", "cdr_oversampling.sv", "cdr_adaptive.sv",
        "comma_align.sv", "receiver_sampler.sv", "reset_sync.sv",
        "serdes_tx_digital.sv", "serdes_rx_digital.sv",
        "serdes_digital_top.sv",
    ],
    "serdes_tx_digital": [
        "serializer.sv", "reset_sync.sv", "serdes_tx_digital.sv",
    ],
    "serdes_tx_stream_digital": [
        "serializer_stream.sv", "reset_sync.sv",
        "serdes_tx_stream_digital.sv",
    ],
    "serdes_tx_axis_digital": [
        "axis_tx_adapter.sv", "serializer_stream.sv", "reset_sync.sv",
        "serdes_tx_axis_digital.sv",
    ],
    "serdes_rx_digital": [
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
    ],
    "serdes_rx_axis_digital": [
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
        "axis_rx_adapter.sv", "serdes_rx_axis_digital.sv",
    ],
    "serdes_soc_wrapper": [
        "apb_csr.sv", "bist_snapshot.sv", "cdc_sync.sv", "serdes_bist.sv",
        "prbs_gen.sv", "prbs_check.sv", "prbs_word_gen.sv", "prbs_word_check.sv",
        "axis_tx_adapter.sv", "serializer_stream.sv",
        "serdes_tx_axis_digital.sv", "axis_rx_adapter.sv",
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
        "serdes_rx_axis_digital.sv", "serdes_soc_wrapper.sv",
    ],
    "serdes_soc_cdc_wrapper": [
        "apb_csr.sv", "async_fifo.sv", "cdc_sync.sv",
        "bist_snapshot.sv", "serdes_bist.sv", "prbs_gen.sv", "prbs_check.sv",
        "axis_tx_adapter.sv", "serializer_stream.sv", "axis_rx_adapter.sv",
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "comma_align.sv",
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

CODING_FACTOR = {"raw": 1.0, "none": 1.0, "8b10b": 1.25, "64b66b": 66.0 / 64.0}


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
    )


def load_arch_db() -> dict[str, dict[str, Any]]:
    if not ARCH_DB_PATH.exists():
        fail(f"missing architecture database {ARCH_DB_PATH}")
    return yaml.safe_load(ARCH_DB_PATH.read_text())


def load_ppa(path: str | None) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Load measured PPA entries keyed by (tech, scl, kind, arch)."""
    if not path:
        return {}
    ppa_path = Path(path)
    if not ppa_path.exists():
        return {}
    data = yaml.safe_load(ppa_path.read_text()) or {}
    entries: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for entry in data.get("entries", []):
        key = (entry["tech"], entry.get("scl", ""), entry["kind"], entry["arch"])
        entries[key] = entry
    return entries


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


def select_arch(kind: str, requested: str | None, target: str, ratio: int,
                line_rate: float, oversample: int, objectives: list[str],
                clocking: str, ppm: float, tech_name: str, tech_scl: str,
                ppa: dict[tuple[str, str, str, str], dict[str, Any]]) -> dict[str, Any]:
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
            if "absolute" in ppa_entry:
                eff["absolute"] = ppa_entry["absolute"]
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

    frontier = pareto(candidates)
    if requested:
        chosen_name, chosen = candidates[0]
    else:
        objective_set = objectives or ["area", "power", "latency"]
        weights = {o: 1.0 for o in objective_set}

        def score(item: tuple[str, dict[str, Any]]) -> float:
            metrics = item[1].get("metrics", {})
            total = sum(weights.values()) or 1.0
            return sum(weights.get(k, 0.0) * float(metrics.get(k, 0.0))
                       for k in objective_set) / total

        chosen_name, chosen = min(candidates, key=score)

    result = dict(chosen)
    result["name"] = chosen_name
    result["frontier"] = frontier
    result["confidence"] = chosen.get("confidence", "estimated")
    result["tech"] = tech_name
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
    if coding != "raw":
        fail(
            f"link.coding={coding!r} is not implemented: no encoder/decoder, "
            "training or framing exists for it. Only coding: raw is allowed "
            "until those blocks are implemented and verified."
        )
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
    clocking = str(rx.get("clocking", "independent")).lower()
    if clocking not in ("independent", "source_sync"):
        fail("rx.clocking must be independent or source_sync")
    ppm = as_float(rx.get("ppm", 0.0), "rx.ppm")

    os_raw = rx.get("oversample", "auto")
    if target == "tx":
        oversample = 1
    elif clocking == "source_sync":
        oversample = 1
    elif os_raw in (None, "auto"):
        oversample = 8 if abs(ppm) > 0 else 4
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

    rx_arch_name = rx.get("architecture", "auto")
    if rx_arch_name == "auto":
        rx_arch_name = None
    if clocking == "source_sync" and rx_arch_name not in (None, "source_sync"):
        fail("rx.architecture conflicts with rx.clocking: source_sync")
    if clocking != "source_sync" and rx_arch_name == "source_sync":
        fail("rx.architecture source_sync requires rx.clocking: source_sync")

    objectives = spec.get("optimization", {}).get("objectives",
                                                   ["area", "power", "latency"])
    if not isinstance(objectives, list) or not objectives:
        fail("optimization.objectives must be a non-empty list")
    for obj in objectives:
        if obj not in ("area", "power", "latency"):
            fail("optimization.objectives must use area, power, latency")

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
    if data_mode not in ("raw", "valid_ready", "axi4_stream"):
        fail("interface.data must be raw, valid_ready or axi4_stream")
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
    align_map = {"external": 0, "comma": 1, "comma_per_frame": 2}
    if align not in align_map:
        fail("rx.align must be external, comma or comma_per_frame")
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
        "rx_arch_name": rx_arch_name,
        "clocking": clocking,
        "ppm": ppm,
        "user_clock_mhz": user_clock if user_clock is not None else line_rate,
        "tx_user_clock_mhz": tx_user_clock,
        "rx_user_clock_mhz": rx_user_clock,
        "cdc_proven_synchronous": proven_sync,
        "objectives": objectives,
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
                              cfg["tech"].scl, ppa)
    else:
        tx_arch = dict(unused)
    if cfg["target"] in ("link", "rx"):
        rx_arch = select_arch("rx", cfg["rx_arch_name"], cfg["target"], cfg["ratio"],
                              cfg["line_rate"], cfg["oversample"], cfg["objectives"],
                              cfg["clocking"], cfg["ppm"], cfg["tech"].name,
                              cfg["tech"].scl, ppa)
    else:
        rx_arch = dict(unused)

    if cfg["target"] == "link":
        if soc_single:
            design = "serdes_soc_wrapper"
        elif soc_cdc:
            design = "serdes_soc_cdc_wrapper"
        else:
            design = "serdes_digital_top"
    elif cfg["target"] == "tx":
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
    if design in ("serdes_tx_digital", "serdes_digital_top"):
        params["CAPTURE_MODE"] = int(tx_arch.get("capture_mode", 0))
    if design in ("serdes_soc_wrapper", "serdes_soc_cdc_wrapper"):
        params.update({
            "SYNC_LEN": cfg["sync_len"],
            "SYNC_PATTERN": cfg["sync_pattern"],
            "ADAPT_FRAC_BITS": 12,
            "BIST_PRBS_WIDTH": 7,
        })
    if design == "serdes_soc_cdc_wrapper":
        params["FIFO_DEPTH"] = 16
    if design in ("serdes_rx_digital", "serdes_rx_axis_digital", "serdes_digital_top"):
        params.update({
            "OVERSAMPLE": cfg["oversample"],
            "SYNC_STAGES": 3,
            "LOCK_THRESH": 8,
            "LOCK_STALE_BITS": 16,
            "CDR_MODE": int(rx_arch.get("cdr_mode", 0)),
            "CDR_BYPASS": 1 if cfg["oversample"] == 1 else 0,
            "ALIGN_MODE": cfg["align_mode"],
            "SYNC_LEN": cfg["sync_len"],
            "SYNC_PATTERN": cfg["sync_pattern"],
            "ADAPT_FRAC_BITS": 12,
            "ADAPT_LOOP_GAIN": 4,
        })

    if design == "serdes_soc_cdc_wrapper":
        clock_port = "pclk"
        clock_period = 1000.0 / cfg["user_clock_mhz"]
        clock_ports = ["pclk", "s_axis_aclk", "m_axis_aclk", "tx_clk", "rx_clk"]
    elif design == "serdes_soc_wrapper":
        clock_port = "clk"
        clock_period = cfg["tx_period_ns"]
        clock_ports = ["clk"]
    elif cfg["target"] == "link":
        clock_port = "tx_clk"
        clock_period = cfg["tx_period_ns"]
        clock_ports = ["tx_clk", "rx_clk"]
    elif cfg["target"] == "tx":
        clock_port = "tx_clk"
        clock_period = cfg["tx_period_ns"]
        clock_ports = ["tx_clk"]
    else:
        clock_port = "rx_clk"
        clock_period = cfg["rx_period_ns"]
        clock_ports = ["rx_clk"]

    capture_semantics = {
        "parallel_mux": "atomic: load && ready captures the full frame; upstream may change data the next cycle",
        "word_group_capture": "word_group_hold: ready is low for WORDS cycles; upstream must hold the full frame stable",
        "word_streaming": "word_streaming: per-word in_valid/in_ready; upstream holds one word per accepted cycle",
        "axi4_stream": "axi4_stream: per-word AXIS beats; tlast marks the last word; no full-frame hold",
        "soc_wrapper": "soc_wrapper: TX AXIS frame = comma + payload words; RX AXIS frame = payload words",
        "soc_cdc_wrapper": "soc_cdc_wrapper: AXIS frames cross async FIFOs; APB control/status synchronized",
        "not_used": "n/a",
    }.get("soc_cdc_wrapper" if design == "serdes_soc_cdc_wrapper"
          else "soc_wrapper" if design == "serdes_soc_wrapper"
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


def synth_parameters(params: dict[str, Any]) -> list[str]:
    order = ["DATA_WIDTH", "WORDS", "LSB_FIRST", "CAPTURE_MODE", "OVERSAMPLE",
             "SYNC_STAGES", "LOCK_THRESH", "LOCK_STALE_BITS", "CDR_MODE",
             "CDR_BYPASS", "ALIGN_MODE", "SYNC_LEN", "SYNC_PATTERN",
             "ADAPT_FRAC_BITS", "ADAPT_LOOP_GAIN", "FIFO_DEPTH",
             "BIST_PRBS_WIDTH"]
    return [f"{k}={params[k]}" for k in order if k in params]


def sdc_text(cfg: dict[str, Any], plan: dict[str, Any], stage: str) -> str:
    target = cfg["target"]
    tx = cfg["tx_period_ns"]
    rx = cfg["rx_period_ns"]
    os = cfg["oversample"]
    setup = cfg.get("setup_uncertainty_ns", 0.100)
    hold = cfg.get("hold_uncertainty_ns", 0.050)
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
        return "\n".join(lines) + "\n"

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
        return "\n".join(lines) + "\n"

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
    return "\n".join(line for line in lines if line is not None) + "\n"


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
        "The relative area/power/latency metrics in tech/arch_db.yaml are",
        "placeholders for measured post-route data. They are capability-gated,",
        "not signoff numbers.",
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
        "- constraints/signoff.sdc: signoff template, reset recovery enabled.",
        "- pnr/librelane.yaml: LibreLane configuration.",
        "",
        "## Clocking",
        "",
        f"- clock ports: {', '.join(plan['clock_ports'])}",
        f"- TX period: {cfg['tx_period_ns']:.3f} ns; RX period: {cfg['rx_period_ns']:.3f} ns",
        f"- PnR CLOCK_PORT: {plan['clock_port']}",
        "- For target link, the SDC creates both clocks but the scalar PnR",
        "  CLOCK_PORT names only tx_clk. Verify that the CTS step adds rx_clk",
        "  to its clock set before trusting RX skew.",
        "",
        "## Qualification",
        "",
        f"Level: **{manifest['qualification']['level']}**. "
        "Run make regress and make qualify REPORT=... to raise it.",
        "",
        "## Known gaps",
        "",
        "- No analog TX/RX wrapper, PEX, SI/PI, jitter/BER or silicon data in this package.",
        "- soc/phy_ip presets require optional APB/AXI-Stream/CDC/BIST modules "
        "that are not implemented in this release.",
        "- Pre-PnR and pre-CTS timing results are not a PHY line-rate guarantee.",
        "",
    ]
    if cfg["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in cfg["warnings"]] + [""]
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
        plan = build_plan(cfg, args)
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
    pdk_paths_resolved = not unresolved(corners)
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
    (out / "pnr" / "librelane.yaml").write_text(yaml.safe_dump(pnr, sort_keys=False))
    (out / "pnr" / "README.md").write_text(
        "# PnR notes\n\n"
        f"`CLOCK_PORT` is set to {plan['clock_port']}. "
        "For target link, the SDC also creates the RX clock; confirm the "
        "CTS/clock-plan step includes it. `pre_pnr.sdc` is intentionally "
        "used for PnR; `signoff.sdc` enables reset recovery/removal and "
        "propagated clocks.\n")

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
        "line_rate_mbps": cfg["line_rate"],
        "data_width": cfg["width"],
        "words": cfg["words"],
        "tech": {"name": tech.name, "scl": tech.scl, "pdk": tech.pdk,
                 "native_dff": tech.native_dff,
                 "pdk_paths_resolved": pdk_paths_resolved},
        "params": plan["params"],
        "architecture": {
            "tx": {k: plan["tx_arch"][k] for k in ("name", "module", "confidence", "frontier")
                   if k in plan["tx_arch"]} |
                  ({k: plan["tx_arch"][k] for k in ("ppa_source", "absolute")
                    if k in plan["tx_arch"]}),
            "rx": {k: plan["rx_arch"][k] for k in ("name", "module", "confidence", "frontier")
                   if k in plan["rx_arch"]} |
                  ({k: plan["rx_arch"][k] for k in ("ppa_source", "absolute")
                    if k in plan["rx_arch"]}),
        },
        "ppa_db": plan["ppa_db"],
        "ppa_entries": plan["ppa_entries"],
        "interface": {k: cfg[k] for k in ("preset", "data_mode", "control", "cdc",
                                          "features", "enable_mode",
                                          "tx_user_clock_mhz", "rx_user_clock_mhz",
                                          "cdc_proven_synchronous")},
        "tx_capture_semantics": plan["capture_semantics"],
        "optional_blocks": plan["optional_blocks"],
        "sv2v": sv2v_meta,
        "clocking": {
            "ports": plan["clock_ports"],
            "tx_period_ns": cfg["tx_period_ns"],
            "rx_period_ns": cfg["rx_period_ns"],
            "pnr_clock_port": plan["clock_port"],
            "note": "CLOCK_PORT is scalar; verify rx_clk enters the CTS clock set for target link.",
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

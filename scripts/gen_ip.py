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
    "serdes_rx_digital": [
        "deserializer_word.sv", "deserializer.sv", "cdr_sync.sv",
        "cdr_oversampling.sv", "cdr_adaptive.sv", "comma_align.sv",
        "receiver_sampler.sv", "reset_sync.sv", "serdes_rx_digital.sv",
    ],
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
                clocking: str, ppm: float, tech_name: str) -> dict[str, Any]:
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
    if preset in ("soc", "phy_ip") and not args.allow_incomplete:
        fail(f"preset {preset} requires APB/AXI-Stream/CDC modules that are not "
             "implemented yet; use --allow-incomplete to emit a scaffold package")

    user_clock = interface.get("user_clock_mhz")
    if user_clock is not None:
        user_clock = as_float(user_clock, "interface.user_clock_mhz")
        phy_clock = line_rate
        differs = abs(user_clock - phy_clock) > max(0.001, phy_clock * 1e-6)
        if cdc == "auto":
            cdc = "async_fifo" if differs else "none"
        elif cdc == "none" and differs:
            fail("interface.user_clock_mhz differs from the PHY clock; "
                 "interface.cdc must be async_fifo")
    elif cdc == "auto":
        cdc = "none"

    if (data_mode == "axi4_stream" or control == "apb" or
            cdc != "none" or features):
        if not args.allow_incomplete:
            fail("AXI4-Stream/APB/CDC/BIST/calibration/DFT blocks are not "
                 "implemented yet; use --allow-incomplete to emit a scaffold package")

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
    if cfg["target"] in ("link", "tx"):
        tx_arch = select_arch("tx", cfg["tx_arch_name"], cfg["target"], cfg["ratio"],
                              cfg["line_rate"], cfg["oversample"], cfg["objectives"],
                              cfg["clocking"], cfg["ppm"], cfg["tech"].name)
    else:
        tx_arch = dict(unused)
    if cfg["target"] in ("link", "rx"):
        rx_arch = select_arch("rx", cfg["rx_arch_name"], cfg["target"], cfg["ratio"],
                              cfg["line_rate"], cfg["oversample"], cfg["objectives"],
                              cfg["clocking"], cfg["ppm"], cfg["tech"].name)
    else:
        rx_arch = dict(unused)

    if cfg["target"] == "link":
        design = "serdes_digital_top"
    elif cfg["target"] == "tx":
        design = tx_arch["module"]
    else:
        design = rx_arch["module"]

    if design not in DESIGN_FILES:
        fail(f"internal: no file list for design {design}")

    params: dict[str, Any] = {
        "DATA_WIDTH": cfg["width"],
        "WORDS": cfg["words"],
        "LSB_FIRST": 1 if cfg["lsb_first"] else 0,
    }
    if design in ("serdes_tx_digital", "serdes_digital_top"):
        params["CAPTURE_MODE"] = int(tx_arch.get("capture_mode", 0))
    if design in ("serdes_rx_digital", "serdes_digital_top"):
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
            "ADAPT_FRAC_BITS": 8,
            "ADAPT_LOOP_GAIN": 4,
        })

    if cfg["target"] == "link":
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
        "not_used": "n/a",
    }.get(tx_arch["name"], "unknown")

    return {
        "design": design,
        "params": params,
        "tx_arch": tx_arch,
        "rx_arch": rx_arch,
        "clock_port": clock_port,
        "clock_period_ns": clock_period,
        "clock_ports": clock_ports,
        "capture_semantics": capture_semantics,
        "files": DESIGN_FILES[design],
    }


def synth_parameters(params: dict[str, Any]) -> list[str]:
    order = ["DATA_WIDTH", "WORDS", "LSB_FIRST", "CAPTURE_MODE", "OVERSAMPLE",
             "SYNC_STAGES", "LOCK_THRESH", "LOCK_STALE_BITS", "CDR_MODE",
             "CDR_BYPASS", "ALIGN_MODE", "SYNC_LEN", "SYNC_PATTERN",
             "ADAPT_FRAC_BITS", "ADAPT_LOOP_GAIN"]
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

    if target in ("link", "tx"):
        if target == "tx" and plan["design"] == "serdes_tx_stream_digital":
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
        "The generated RTL is in rtl/; rtl/filelist.f lists compile order.",
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a spec-driven OpenSERDES digital IP package")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--pdk-lib-root", default=None)
    ap.add_argument("--check", action="store_true", help="validate and print, do not write")
    ap.add_argument("--force", action="store_true", help="replace an existing output directory")
    ap.add_argument("--relax-limits", action="store_true", help="warn instead of failing capability gates")
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
    pnr = {
        "DESIGN_NAME": plan["design"],
        "VERILOG_FILES": [str(out / "rtl" / f) for f in plan["files"]],
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
        "tech": {"name": tech.name, "scl": tech.scl, "pdk": tech.pdk,
                 "native_dff": tech.native_dff,
                 "pdk_paths_resolved": pdk_paths_resolved},
        "params": plan["params"],
        "architecture": {
            "tx": {k: plan["tx_arch"][k] for k in ("name", "module", "confidence", "frontier")},
            "rx": {k: plan["rx_arch"][k] for k in ("name", "module", "confidence", "frontier")},
        },
        "interface": {k: cfg[k] for k in ("preset", "data_mode", "control", "cdc",
                                          "features", "enable_mode")},
        "tx_capture_semantics": plan["capture_semantics"],
        "clocking": {
            "ports": plan["clock_ports"],
            "tx_period_ns": cfg["tx_period_ns"],
            "rx_period_ns": cfg["rx_period_ns"],
            "pnr_clock_port": plan["clock_port"],
            "note": "CLOCK_PORT is scalar; verify rx_clk enters the CTS clock set for target link.",
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

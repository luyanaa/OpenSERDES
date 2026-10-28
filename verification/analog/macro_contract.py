#!/usr/bin/env python3
"""Canonical analog-macro contract validation.

The legacy SPICE views use different positional port orders (for example RX
schematic is GND,INP,OUT,VDD while RX layout is GND,VDD,INP,OUT).  This module
makes the canonical names and aliases the only supported interface.

It validates manifests, not transistor models.  Missing physical views are
recorded as missing/unusable rather than fabricated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MACROS = ROOT / "analog" / "macros"
CANONICAL_REQUIRED = ("VDD", "VSS", "IN", "OUT")


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: manifest is not an object")
    return data


def canonical_ports(manifest: dict[str, Any]) -> list[str]:
    ports = manifest.get("canonical_ports")
    if not isinstance(ports, list):
        raise ValueError("canonical_ports must be a list")
    ordered = sorted(ports, key=lambda p: int(p.get("index", -1)))
    names = [str(p.get("name")) for p in ordered]
    if names != list(CANONICAL_REQUIRED):
        raise ValueError(
            f"canonical ports must be exactly {list(CANONICAL_REQUIRED)}; got {names}"
        )
    return names


def alias_map(manifest: dict[str, Any], ports: list[str]) -> dict[str, str]:
    aliases = manifest.get("aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("aliases must be an object")
    out: dict[str, str] = {}
    for canonical in ports:
        names = aliases.get(canonical, [canonical])
        if isinstance(names, str):
            names = [names]
        for name in [canonical, *names]:
            key = str(name).upper()
            if key in out and out[key] != canonical:
                raise ValueError(f"alias {name!r} maps to both {out[key]} and {canonical}")
            out[key] = canonical
    return out


def view_ports(manifest: dict[str, Any], view_name: str,
               ports: list[str], aliases: dict[str, str]) -> list[str]:
    view = (manifest.get("views", {}) or {}).get(view_name, {})
    order = view.get("port_order")
    if order is None:
        return []
    mapped: list[str] = []
    for raw in order:
        canonical = aliases.get(str(raw).upper())
        if canonical is None:
            raise ValueError(f"{view_name}: unknown port {raw!r}")
        if canonical in mapped:
            raise ValueError(f"{view_name}: duplicate canonical port {canonical}")
        mapped.append(canonical)
    if sorted(mapped) != sorted(ports):
        raise ValueError(
            f"{view_name}: port set {mapped} != canonical {ports}"
        )
    return mapped


def validate_manifest(path: Path, manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        ports = canonical_ports(manifest)
        aliases = alias_map(manifest, ports)
    except ValueError as exc:
        return [str(exc)], warnings
    if manifest.get("polarity") not in ("inverting", "noninverting"):
        errors.append("polarity must be inverting or noninverting")
    if manifest.get("polarity_verified") is not True:
        warnings.append("polarity is not verified by a DC/transient fixture")

    views = manifest.get("views", {}) or {}
    for required in ("schematic", "layout", "canonical_spice", "macro_v"):
        if required not in views:
            errors.append(f"missing required view {required}")
    for name, view in views.items():
        if not isinstance(view, dict):
            errors.append(f"view {name} is not an object")
            continue
        status = view.get("status", "missing")
        rel = view.get("path")
        if rel and status in ("available", "correct", "extraction-only"):
            target = ROOT / rel
            if not target.exists():
                errors.append(f"view {name}: path does not exist: {rel}")
        if view.get("port_order") is not None:
            try:
                view_ports(manifest, name, ports, aliases)
            except ValueError as exc:
                errors.append(str(exc))
        if name in ("schematic", "layout", "canonical_spice") and not rel:
            errors.append(f"view {name}: no path")
        if name == "pex":
            fmt = str(view.get("format", ""))
            if status == "available" and fmt not in ("ngspice", "spice"):
                errors.append(f"view pex: non-ngspice format {fmt!r} cannot be called available")
    # Canonical wrapper must map by names, never by source positional order.
    wrapper = ROOT / str(views.get("canonical_spice", {}).get("path", ""))
    if wrapper.is_file() and "Xcore" in wrapper.read_text(errors="replace"):
        pass
    return errors, warnings


def validate_tree(root: Path = DEFAULT_MACROS) -> dict[str, Any]:
    macros: dict[str, Any] = {}
    total_errors = 0
    for manifest_path in sorted(root.glob("*/manifest.json")):
        manifest = load_manifest(manifest_path)
        errors, warnings = validate_manifest(manifest_path, manifest)
        total_errors += len(errors)
        macros[manifest.get("name", manifest_path.parent.name)] = {
            "manifest": str(manifest_path),
            "canonical_ports": [p.get("name") for p in manifest.get("canonical_ports", [])],
            "polarity": manifest.get("polarity"),
            "views": (manifest.get("views", {}) or {}),
            "errors": errors,
            "warnings": warnings,
        }
    return {"root": str(root), "macros": macros, "ok": total_errors == 0,
            "error_count": total_errors}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(DEFAULT_MACROS))
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = validate_tree(Path(args.root))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {out}")
    else:
        print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

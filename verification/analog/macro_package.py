#!/usr/bin/env python3
"""Stage validated analog macro views into a generated package.

The package gets real GDS/LEF/black-box Liberty/Verilog/SPICE paths and a
LibreLane MACROS fragment.  The default digital PnR config is not silently
rewritten; the caller must merge the fragment after the target LibreLane
version is checked.  This is a macro-integration preparation step, not a
placed/routed/STA signoff.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.macro_contract import DEFAULT_MACROS, load_manifest, validate_manifest


def stage(package: Path, out_dir: Path) -> dict:
    package = package.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = {}
    for mpath in sorted(DEFAULT_MACROS.glob("*/manifest.json")):
        manifest = load_manifest(mpath)
        errors, warnings = validate_manifest(mpath, manifest)
        if errors:
            raise RuntimeError(f"{mpath}: {errors}")
        name = manifest["name"]
        dst = out_dir / name
        dst.mkdir(parents=True, exist_ok=True)
        views = manifest.get("views", {})
        copied = {}
        # Copy the source schematic and replace the canonical wrapper so its
        # include is package-local (the repo-relative include would break).
        sch = views.get("schematic", {})
        if sch.get("path"):
            src = ROOT / sch["path"]
            if src.is_file():
                shutil.copy2(src, dst / "schematic.sp")
                copied["schematic"] = str(dst / "schematic.sp")
        for view_name in ("macro_v", "lef", "liberty", "views_json"):
            view = views.get(view_name, {})
            rel = view.get("path")
            if not rel:
                continue
            src = ROOT / rel
            if not src.is_file():
                raise FileNotFoundError(f"{name}: missing {view_name} {src}")
            target = dst / src.name
            shutil.copy2(src, target)
            copied[view_name] = str(target)
        # Canonical wrapper is package-local and maps by name.
        canonical = manifest["canonical_ports"]
        aliases = manifest.get("aliases", {})
        alias_to_canon = {}
        for canon, vals in aliases.items():
            for val in [canon, *vals]:
                alias_to_canon[str(val).upper()] = canon
        source_order = sch.get("port_order", [])
        source_values = []
        for pin in source_order:
            c = alias_to_canon.get(str(pin).upper())
            if c:
                source_values.append(c)
        if not source_values:
            source_values = [p["name"] for p in canonical]
        wrapper = (f"* package-local canonical wrapper for {name}\n"
                   f'.include "schematic.sp"\n'
                   f".subckt {name} " + " ".join(p["name"] for p in canonical) + "\n"
                   "Xcore " + " ".join(source_values)
                   + f" {sch.get('top', name)}\n.ends\n")
        (dst / "canonical.spice").write_text(wrapper)
        copied["canonical_spice"] = str(dst / "canonical.spice")
        # The layout source is named layout.gds in the staged macro.
        layout = views.get("layout", {})
        if layout.get("path"):
            src = ROOT / layout["path"]
            if not src.is_file():
                raise FileNotFoundError(src)
            target = dst / "layout.gds"
            shutil.copy2(src, target)
            copied["layout"] = str(target)
        # PEX is optional (Magic build artifact).
        pex = views.get("magic_pex", {}).get("path")
        if pex and (ROOT / pex).is_file():
            target = dst / "pex.spice"
            shutil.copy2(ROOT / pex, target)
            copied["magic_pex"] = str(target)
        (dst / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staged[name] = copied
    combined = ROOT / "analog/macros/sky130_analog_macros.lef"
    if combined.is_file():
        shutil.copy2(combined, out_dir / "sky130_analog_macros.lef")
    top = ROOT / "analog/rtl/serdes_analog_link.sv"
    if top.is_file():
        shutil.copy2(top, out_dir / "serdes_analog_link.sv")
    index = {
        "schema": 1,
        "package": str(package),
        "combined_lef": str(out_dir / "sky130_analog_macros.lef") if combined.is_file() else None,
        "mixed_signal_top": str(out_dir / "serdes_analog_link.sv") if top.is_file() else None,
        "macros": staged,
        "note": "abstract LEF + black-box Liberty; no timing arcs, no PnR result.",
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    lib = {
        "MACROS": {
            name: {
                "gds": [f"analog/{name}/layout.gds"],
                "lef": [("analog/sky130_analog_macros.lef" if combined.is_file()
                         else f"analog/{name}/abstract.lef")],
                "nl": [f"analog/{name}/macro.v"],
                "lib": {"*": [f"analog/{name}/macro.lib"]},
            }
            for name in staged
        }
    }
    # Write a package-relative fragment; do not overwrite pnr/librelane.yaml.
    import yaml
    (out_dir / "librelane_macros.yaml").write_text(
        yaml.safe_dump(lib, sort_keys=False)
    )
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", required=True)
    ap.add_argument("--out", default=None,
                    help="default: <package>/analog")
    args = ap.parse_args()
    package = Path(args.package)
    out = Path(args.out) if args.out else package / "analog"
    try:
        index = stage(package, out)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"staged {len(index['macros'])} analog macros into {out}")
    print(f"wrote {out / 'librelane_macros.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

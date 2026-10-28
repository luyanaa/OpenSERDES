#!/usr/bin/env python3
"""Inventory the stdcell libraries available under /grid.

The catalog is factual: it records Liberty/LEF/GDS/Verilog/Magic artifacts,
cell counts, track/site information, DFF/NAND presence, and any delivered
DRC/LVS evidence.  It does not promote a library to signoff; use
docs/library_selection.md for the measured flow status.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SPECS = [
    # GF180MCU 3.3 V candidates.
    {"name": "gf180_3v3_as_7t", "path": "/grid/gf180mcu/gf180mcu_as_sc_mcu7t3v3",
     "family": "gf180mcu", "voltage_v": 3.3, "tracks": "7T",
     "note": "AS WiP; DRC/LVS reported in docs/library_selection.md"},
    {"name": "gf180_3v3_ocd_12t", "path": "/grid/gf180mcu/gf180mcu_ocd_sc_rt12t3v3",
     "family": "gf180mcu", "voltage_v": 3.3, "tracks": "12T",
     "note": "OCD; 18-cell library, per-cell Liberty"},
    {"name": "gf180_3v3_osu_12t", "path": "/grid/gf180mcu/gf180mcu_osu_sc_gp12t3v3",
     "family": "gf180mcu", "voltage_v": 3.3, "tracks": "12T",
     "note": "OSU; DRC clean, LVS blocked in docs/library_selection.md"},
    {"name": "gf180_3v3_osu_9t", "path": "/grid/gf180mcu/gf180mcu_osu_sc_gp9t3v3",
     "family": "gf180mcu", "voltage_v": 3.3, "tracks": "9T",
     "note": "OSU 9T 3.3 V; no foundry mcu9t3v3 directory found"},
    # Sky130 official low-voltage libraries available under /grid.
    {"name": "sky130_fd_sc_hdll", "path": "/grid/sky130/sky130_fd_sc_hdll",
     "family": "sky130", "voltage_v": 1.8, "tracks": "9T", "note": "official HD low-leakage"},
    {"name": "sky130_fd_sc_lp", "path": "/grid/sky130/sky130_fd_sc_lp",
     "family": "sky130", "voltage_v": 1.8, "tracks": "9T", "note": "official low power"},
    {"name": "sky130_fd_sc_ls", "path": "/grid/sky130/sky130_fd_sc_ls",
     "family": "sky130", "voltage_v": 1.8, "tracks": "9T", "note": "official low speed"},
    {"name": "sky130_fd_sc_ms", "path": "/grid/sky130/sky130_fd_sc_ms",
     "family": "sky130", "voltage_v": 1.8, "tracks": "11T", "note": "official medium speed"},
    {"name": "sky130_as_sc_hs", "path": "/grid/sky130/sky130_as_sc_hs",
     "family": "sky130", "voltage_v": 1.8, "tracks": "9T", "note": "AS WiP; no async-reset DFF in core set"},
    {"name": "sky130_osu_sc_t12", "path": "/grid/sky130/sky130_osu_sc_t12",
     "family": "sky130", "voltage_v": 1.8, "tracks": "12T", "note": "OSU 12T"},
    {"name": "sky130_osu_sc_t15", "path": "/grid/sky130/sky130_osu_sc_t15",
     "family": "sky130", "voltage_v": 1.8, "tracks": "15T", "note": "OSU 15T"},
    {"name": "sky130_osu_sc_t18", "path": "/grid/sky130/sky130_osu_sc_t18",
     "family": "sky130", "voltage_v": 1.8, "tracks": "18T", "note": "OSU 18T"},
    # IHP.
    {"name": "ihp_ez130_8t", "path": "/grid/ihp130/ez130_8t_v1p1",
     "family": "ihp-sg13g2", "voltage_v": 1.2, "tracks": "8T",
     "note": "EZ130 8T; no delivered DRC/LVS report in package"},
]


def _files(root: Path, pattern: str, limit: int = 20000) -> list[Path]:
    out = []
    for path in root.rglob(pattern):
        if ".git" in path.parts:
            continue
        out.append(path)
        if len(out) >= limit:
            break
    return out


def _track_info(root: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for pattern in ("*.tlef", "*.lef"):
        for path in _files(root, pattern, limit=50):
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            sites = re.findall(
                r"(?ims)^\s*SITE\s+(\S+)\s*;?\s*.*?SIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)\s*;",
                text,
            )
            if sites:
                info["site"] = [{"name": s[0], "width_um": float(s[1]),
                                 "height_um": float(s[2])} for s in sites[:8]]
                break
        if info:
            break
    return info


def _cell_names(root: Path) -> set[str]:
    names = set()
    for path in _files(root, "*.v", limit=5000):
        names.add(path.stem)
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r"(?im)^\s*module\s+([A-Za-z_]\w*)", text):
            names.add(match.group(1))
    return names


def inspect(spec: dict[str, Any]) -> dict[str, Any]:
    root = Path(spec["path"])
    if not root.is_dir():
        return {**spec, "available": False, "reason": "directory missing"}
    libs = _files(root, "*.lib")
    lefs = _files(root, "*.lef") + _files(root, "*.tlef")
    gds = _files(root, "*.gds")
    vlog = _files(root, "*.v")
    magic = _files(root, "*.mag")
    spice = _files(root, "*.spice") + _files(root, "*.sp")
    drc = [p for p in _files(root, "*.rpt") + _files(root, "*.log")
           if "drc" in p.name.lower()]
    lvs = [p for p in _files(root, "*.rpt") + _files(root, "*.log")
           if "lvs" in p.name.lower()]
    cells = _cell_names(root)
    dff = sorted(n for n in cells if re.search(r"dff|dfxt|dfrt|dfsr|dfrb", n, re.I))
    nand = sorted(n for n in cells if re.search(r"nand", n, re.I))
    corners = sorted({p.stem for p in libs})
    available = bool(libs and lefs and gds and vlog)
    assessment = "usable-candidate" if available else "incomplete"
    if drc and lvs:
        assessment = "drc-lvs-evidence-present"
    return {
        **spec,
        "available": available,
        "assessment": assessment,
        "artifacts": {
            "liberty": len(libs),
            "lef_tlef": len(lefs),
            "gds": len(gds),
            "verilog": len(vlog),
            "magic": len(magic),
            "spice": len(spice),
            "drc_reports": [str(p.relative_to(root)) for p in drc[:5]],
            "lvs_reports": [str(p.relative_to(root)) for p in lvs[:5]],
        },
        "liberty_names": corners[:12],
        "cell_count": len(cells),
        "dff_cells": dff[:12],
        "nand_cells": nand[:12],
        "track": _track_info(root),
    }


def markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Library catalog",
        "",
        "Generated from /grid by `scripts/library_catalog.py`.  Artifact presence",
        "is not a signoff claim; see `docs/library_selection.md`.",
        "",
        "| Library | Tracks | Available | Cells | DFF | NAND | DRC/LVS evidence | Note |",
        "|---|---:|---|---:|---|---|---|---|",
    ]
    for r in rows:
        if not r.get("available"):
            lines.append(
                f"| `{r['name']}` | {r.get('tracks','')} | no | - | - | - | - | {r.get('reason','')} |"
            )
            continue
        a = r["artifacts"]
        ev = "yes" if a["drc_reports"] and a["lvs_reports"] else "no"
        lines.append(
            f"| `{r['name']}` | {r.get('tracks','')} | yes | {r['cell_count']} | "
            f"{len(r['dff_cells'])} | {len(r['nand_cells'])} | {ev} | {r.get('note','')} |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Official Sky130 `hdll/lp/ls/ms` repos under /grid contain tech/model data",
        "  but no complete Liberty/LEF/GDS/Verilog cell set; they are not directly",
        "  usable here.",
        "- No foundry GF180 3.3 V 9T library was found; `gf180mcu_osu_sc_gp9t3v3`",
        "  is the only 9T 3.3 V candidate.",
        "- GF180 3.3 V 7T/12T candidates are evaluation libraries; OSU LVS is",
        "  blocked by the model/process mismatch documented in library_selection.",
        "- Sky130 OSU 12T/15T/18T and IHP EZ130 8T need library-specific DRC/LVS",
        "  and reset-cell checks before use.",
        "- DFF/NAND presence counts modules, not characterized cells.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="build/library_catalog.json")
    ap.add_argument("--markdown", default="build/library_catalog.md")
    ap.add_argument("--only", nargs="*", default=None,
                    help="subset of catalog names")
    args = ap.parse_args()
    specs = [s for s in SPECS if not args.only or s["name"] in args.only]
    rows = [inspect(s) for s in specs]
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "libraries": rows,
        "notes": [
            "Artifact presence is not signoff; see docs/library_selection.md.",
            "GF180 3.3 V 9T foundry library was not found under /grid; OSU gp9t3v3 is present.",
            "Sky130 official hs is not present under /grid; hdll/lp/ls/ms are.",
            "Sky130 OSU 12T/15T/18T and IHP EZ130 are present but need DRC/LVS validation.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    md = Path(args.markdown)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(markdown(rows))
    print(markdown(rows))
    print(f"wrote {out} and {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

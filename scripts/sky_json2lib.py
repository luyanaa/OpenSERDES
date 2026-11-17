#!/usr/bin/env python3
"""Convert skywater-pdk ``*.lib.json`` timing data into consolidated Liberty.

The skywater-pdk source libraries ship timing as JSON (one file per corner
header, one per cell per corner, plus a shared ``*__common.lib.json``) and no
``.lib``.  The installed PDK that LibreLane/OpenSTA consumes needs a single
``<scl>__<corner>.lib`` per corner, the same shape open_pdks installs for
sky130A.

Usage:
    python3 scripts/sky_json2lib.py \
        --family sky130_fd_sc_hdll \
        --corner tt_025C_1v80 --corner ss_100C_1v60 --corner ff_n40C_1v65 \
        --out build/sky130_lib/...

    # or discover every complete corner the family ships
    python3 scripts/sky_json2lib.py --family sky130_fd_sc_hdll --all-corners

Output goes to ``--out-dir <dir>`` as ``<family>__<corner>.lib``.

Variant files (``*_ccsnoise``, ``*_pwrlkg``, ``*_ka*``, ``dest*``) are skipped:
they are not interchangeable with the plain NLDM corner a PDK flow loads.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PDK = Path("/grid/sky130")
SKIP_VARIANT = re.compile(r"(ccsnoise|pwrlkg|ka\d|dest|power_?grid)", re.I)


def _num(v: float) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    # repr() gives the shortest round-trip form; Liberty needs a decimal point.
    s = repr(float(v))
    if "e" not in s and "." not in s:
        s += ".0"
    return s


def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _num(v)
    s = str(v)
    if s in ("true", "false"):
        return s
    return '"' + s.replace('"', r"\"") + '"'


def _complex_args(v) -> str:
    if isinstance(v, list):
        return ", ".join(_val(x) if not isinstance(x, list)
                         else ", ".join(_val(y) for y in x) for x in v)
    return _val(v)


def emit(entries: dict, indent: int, out: list[str], header: str | None) -> None:
    pad = "    " * indent
    for key, value in entries.items():
        if key == "table":
            # open_pdks renders the statetable body as one quoted
            # comma-joined row string; that is the form OpenSTA accepts.
            rows = (value.split(",") if isinstance(value, str)
                    else [str(x) for x in value])
            out.append(f"{pad}table : "
                       + _val(",".join(r.strip() for r in rows)) + ";")
        elif key in ("values",):
            if value and isinstance(value[0], list):
                rows = ", ".join('"' + ", ".join(_num(x) for x in row) + '"'
                                 for row in value)
            else:
                rows = '"' + ", ".join(_num(x) for x in value) + '"'
            out.append(f"{pad}values({rows});")
        elif key in ("index_1", "index_2", "index_3"):
            vals = ", ".join(_num(x) for x in value)
            out.append(f'{pad}{key}("{vals}");')
        elif key.startswith("comp_attribute,"):
            name = key.split(",", 1)[1]
            if isinstance(value, dict):
                for k, v in value.items():
                    out.append(f'{pad}{name}("{k}", {_val(v)});')
            elif isinstance(value, list) and value and isinstance(value[0], list):
                for pair in value:
                    out.append(f"{pad}{name}({', '.join(_val(x) for x in pair)});")
            elif isinstance(value, list):
                out.append(f"{pad}{name}({_complex_args(value)});")
            else:
                out.append(f"{pad}{name}({_val(value)});")
        elif key == "define":
            for d in value:
                out.append(f"{pad}define({d['attribute_name']},"
                           f"{d['group_name']},{d['attribute_type']});")
        elif "," in key:
            # "group,arg1,arg2" is a group with several (quoted) name args,
            # e.g. ff ("IQ","IQ_N").  A single arg is the common case.
            parts = key.split(",")
            gtype, gargs = parts[0], parts[1:]
            args = ", ".join(f'"{a}"' for a in gargs)
            if isinstance(value, dict):
                out.append(f"{pad}{gtype}({args}) {{")
                emit(value, indent + 1, out, None)
                out.append(f"{pad}}}")
            else:
                out.append(f"{pad}{gtype}({args}, {_complex_args(value)});")
        elif isinstance(value, dict):
            out.append(f"{pad}{key}() {{")
            emit(value, indent + 1, out, key)
            out.append(f"{pad}}}")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for item in value:
                out.append(f"{pad}{key}() {{")
                emit(item, indent + 1, out, key)
                out.append(f"{pad}}}")
        else:
            if isinstance(value, list):
                out.append(f"{pad}{key}({_complex_args(value)});")
            else:
                out.append(f"{pad}{key} : {_val(value)};")


def family_root(family: str, root_opt: str | None = None) -> Path:
    root = Path(root_opt) if root_opt else PDK / family
    if not root.is_dir():
        raise SystemExit(f"missing {root}")
    return root


def corners(root: Path, family: str) -> dict[str, dict]:
    """corner -> {"header": path, "cells": {cell: path}} for plain corners."""
    res: dict[str, dict] = {}
    for f in sorted(root.glob("timing/*.lib.json")):
        m = re.match(rf"{family}__(\S+)\.lib\.json$", f.name)
        if not m or m.group(1) == "common" or SKIP_VARIANT.search(f.name):
            continue
        res.setdefault(m.group(1), {"header": f, "cells": {}})
    for f in sorted(root.rglob(f"{family}__*.lib.json")):
        if "cells" not in f.parts or SKIP_VARIANT.search(f.name):
            continue
        for corner in res:
            m = re.match(rf"{family}__(\S+)__{re.escape(corner)}\.lib\.json$",
                         f.name)
            if m:
                res[corner]["cells"][m.group(1)] = f
    return {c: v for c, v in res.items() if v["cells"]}


def convert(root: Path, family: str, corner: str, info: dict) -> tuple[str, int]:
    common = root / "timing" / f"{family}__common.lib.json"
    lines: list[str] = [f'library ("{family}__{corner}") {{']
    if common.is_file():
        emit(json.loads(common.read_text()), 1, lines, None)
    emit(json.loads(info["header"].read_text()), 1, lines, None)
    for cell, path in sorted(info["cells"].items()):
        body = json.loads(path.read_text())
        lines.append(f'    cell ("{family}__{cell}") {{')
        emit(body, 2, lines, cell)
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n", len(info["cells"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True)
    ap.add_argument("--corner", action="append", default=[])
    ap.add_argument("--all-corners", action="store_true")
    ap.add_argument("--root", default=None,
                    help="library tree (default /grid/sky130/<family>)")
    ap.add_argument("--out-dir", default="build/sky130_lib")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    root = family_root(args.family, args.root)
    avail = corners(root, args.family)
    if args.list:
        for c, info in sorted(avail.items()):
            print(f"{c}: {len(info['cells'])} cells")
        return 0
    todo = sorted(avail) if args.all_corners else args.corner
    if not todo:
        raise SystemExit("pass --corner ... or --all-corners")
    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    for corner in todo:
        if corner not in avail:
            print(f"skip {corner}: no per-cell JSON for this corner",
                  file=sys.stderr)
            continue
        text, n = convert(root, args.family, corner, avail[corner])
        dest = outdir / f"{args.family}__{corner}.lib"
        dest.write_text(text)
        print(f"{dest} : {n} cells, {len(text.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())

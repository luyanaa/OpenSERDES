#!/usr/bin/env python3
"""PEX characterization / comparison harness.

Two explicit modes:

1. ``--pex-netlist`` + ``--top tx|rx``: wrap an externally extracted SPICE
   netlist (subcircuit with ports ``in out vdd vss`` by default) in the same
   driver/slicer testbench used by ``extract_lut.py`` and produce a LUT with
   ``model_kind: pex``.  The netlist must already be extracted, DRC/LVS-clean
   and include its own models; this script does not run layout extraction.
2. ``--schematic-lut`` + ``--pex-lut``: compare two LUT JSON files and record
   deltas.  This is the recommended first PEX step: extract one TX/RX macro,
   compare against the schematic LUT, then decide whether more PEX is worth it.

No PEX netlist is generated from a GDS here.  Magic/KLayout paths are recorded
by ``extract_lut.py --check-tools``; layout/DRC/LVS/extraction remains an
external, explicit step.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.extract_lut import (  # noqa: E402
    _rx_netlist,
    _tx_netlist,
    run_ngspice,
)

VDD = 1.8


def _subckt_instance(top: str, name: str, ports: dict[str, str]) -> str:
    return f"X{name} {ports['in']} {ports['out']} {ports['vdd']} {ports['vss']} {top}"


def _wrap_netlist(pex: Path, top: str, ports: dict[str, str],
                  mode: str, load_ff: float, edge_ps: float,
                  vdd: float) -> str:
    include = f'.include "{pex}"'
    if mode == "tx":
        return f"""* OpenSERDES PEX TX LUT
.param VDD={vdd}
{include}
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {edge_ps}p {edge_ps}p 2n 4n)
{_subckt_instance(top, 'dut', ports)}
Cload out 0 {load_ff}f
.tran 1p 4n
.meas tran vmax max v(out)
.meas tran vmin min v(out)
.meas tran trise trig v(out) val={vdd*0.2} rise=1 targ v(out) val={vdd*0.8} rise=1
.meas tran tfall trig v(out) val={vdd*0.8} fall=1 targ v(out) val={vdd*0.2} fall=1
.end
"""
    return f"""* OpenSERDES PEX RX LUT
.param VDD={vdd}
{include}
Vdd vdd 0 {{VDD}}
Vin in 0 PULSE(0 {{VDD}} 0.2n {edge_ps}p {edge_ps}p 2n 4n)
{_subckt_instance(top, 'dut', ports)}
Cload out 0 {load_ff}f
.tran 1p 4n
.meas tran tphl trig v(in) val={vdd*0.5} rise=1 targ v(out) val={vdd*0.5} fall=1
.meas tran tplh trig v(in) val={vdd*0.5} fall=1 targ v(out) val={vdd*0.5} rise=1
.end
"""


def _parse_ports(text: str) -> dict[str, str]:
    defaults = {"in": "in", "out": "out", "vdd": "vdd", "vss": "vss"}
    if not text:
        return defaults
    for item in text.split(","):
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"bad port map entry {item!r}; use name=port")
        defaults[key.strip()] = value.strip()
    return defaults


def _load_lut(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or "tx" not in data or "rx" not in data:
        raise ValueError(f"{path} is not a LUT")
    return data


def _compare(schematic: dict[str, Any], pex: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for block, key in (("tx", "rise_ns"), ("tx", "fall_ns"), ("rx", "tphl_ns"), ("rx", "tplh_ns")):
        s_rows = {(float(r.get("load_ff", r.get("slew_ps", 0))),): r
                  for r in schematic.get(block, {}).get("rows", [])}
        p_rows = {(float(r.get("load_ff", r.get("slew_ps", 0))),): r
                  for r in pex.get(block, {}).get("rows", [])}
        for key_tuple, srow in s_rows.items():
            prow = p_rows.get(key_tuple)
            if prow is None:
                continue
            sval = srow.get(key)
            pval = prow.get(key)
            if sval is None or pval is None:
                continue
            rows.append({
                "block": block,
                "metric": key,
                "coordinate": key_tuple[0],
                "schematic": float(sval),
                "pex": float(pval),
                "delta": float(pval) - float(sval),
                "relative": (float(pval) - float(sval)) / float(sval) if sval else None,
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pex-netlist", default=None,
                    help="externally extracted SPICE netlist")
    ap.add_argument("--top", default="tx_driver",
                    help="subcircuit name in the PEX netlist")
    ap.add_argument("--mode", choices=("tx", "rx", "both"), default="both")
    ap.add_argument("--ports", default=None,
                    help="comma list, e.g. in=in,out=out,vdd=vdd,vss=vss")
    ap.add_argument("--loads-ff", type=float, nargs="*", default=[50.0])
    ap.add_argument("--slews-ps", type=float, nargs="*", default=[20.0])
    ap.add_argument("--schematic-lut", default=None)
    ap.add_argument("--pex-lut", default=None)
    ap.add_argument("--out", default=str(ROOT / "build" / "verify" / "pex_lut.json"))
    ap.add_argument("--workdir", default=str(ROOT / "build" / "verify" / "pex"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.schematic_lut and args.pex_lut:
        report = {
            "kind": "pex-vs-schematic",
            "schematic_lut": args.schematic_lut,
            "pex_lut": args.pex_lut,
            "rows": _compare(_load_lut(Path(args.schematic_lut)),
                             _load_lut(Path(args.pex_lut))),
            "generated_utc": datetime.now(timezone.utc).isoformat(),
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {out} ({len(report['rows'])} comparison rows)")
        return 0

    if not args.pex_netlist:
        print("error: pass --pex-netlist (extracted) or --schematic-lut/--pex-lut",
              file=sys.stderr)
        return 2
    if shutil.which("ngspice") is None:
        print("error: ngspice is required", file=sys.stderr)
        return 2
    ports = _parse_ports(args.ports)
    netlist = Path(args.pex_netlist)
    workdir = Path(args.workdir)
    if args.dry_run:
        print(f"pex netlist: {netlist}")
        print(f"top: {args.top} ports={ports}")
        print(f"modes: {args.mode}")
        return 0

    rows: dict[str, Any] = {"tx": [], "rx": []}
    if args.mode in ("tx", "both"):
        for load in args.loads_ff:
            values = run_ngspice(
                _wrap_netlist(netlist, args.top, ports, "tx", load, 50.0, VDD),
                workdir, f"tx_{int(load)}ff",
            )
            rows["tx"].append({
                "load_ff": load,
                "rise_ns": (values.get("trise") or 0) * 1e9 if values.get("trise") is not None else None,
                "fall_ns": (values.get("tfall") or 0) * 1e9 if values.get("tfall") is not None else None,
                "swing_v": (values.get("vmax", 0) - values.get("vmin", 0)),
            })
    if args.mode in ("rx", "both"):
        for slew in args.slews_ps:
            values = run_ngspice(
                _wrap_netlist(netlist, args.top, ports, "rx", 20.0, slew, VDD),
                workdir, f"rx_{int(slew)}ps",
            )
            rows["rx"].append({
                "slew_ps": slew,
                "tphl_ns": (values.get("tphl") or 0) * 1e9 if values.get("tphl") is not None else None,
                "tplh_ns": (values.get("tplh") or 0) * 1e9 if values.get("tplh") is not None else None,
            })
    report = {
        "kind": "pex-lut",
        "pdk": "from-netlist",
        "model_kind": "pex",
        "pex_netlist": str(netlist),
        "top": args.top,
        "ports": ports,
        "tx": {"loads_ff": args.loads_ff, "rows": rows["tx"]},
        "rx": {"slews_ps": args.slews_ps, "rows": rows["rx"]},
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": "External layout extraction input; no layout/PEX was generated here.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

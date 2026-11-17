#!/usr/bin/env python3
"""Half-rate TX power after a real clock tree, not an ideal clock.

OpenROAD floorplans the mapped netlist, places it, then runs
``clock_tree_synthesis``.  Without a placement the clock nets have no sinks
and the tool inserts nothing.  Power is OpenSTA default activity on that
netlist: the inserted buffers are not in any pre-CTS VCD, so this is not an
annotated workload and it does not emit E_bit.  Analog power is not in this
netlist.  Estimated CTS wire is not routed SPEF.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OR = "/nix/store/2y36a4sr078y15ick388dncqjdzmlxga-openroad-2026-02-17/bin/openroad"
STA = "/nix/store/6qbn5zjnnnyna6md4rsmiqw4y26y8556-openroad-26Q3/bin/sta"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"error: {' '.join(cmd[:4])} failed\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--liberty", required=True)
    ap.add_argument("--lef", required=True)
    ap.add_argument("--out", default=str(ROOT / "build/cts/half_rate.json"))
    ap.add_argument("--work", default=str(ROOT / "build/cts"))
    args = ap.parse_args()
    for tool in ("yosys", OR, STA):
        if not Path(tool).is_file() and shutil.which(tool) is None:
            raise SystemExit(f"error: missing {tool}")
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    net = work / "half_rate.v"
    _run(["yosys", "-q", "-p",
          "read_verilog -sv rtl/serializer_half_rate.sv; "
          "hierarchy -top serializer_half_rate -chparam DATA_WIDTH 8 -chparam WORDS 4; "
          "synth -top serializer_half_rate -flatten; "
          f"dfflibmap -liberty {args.liberty}; abc -liberty {args.liberty} "
          "-dont_use sky130_fd_sc_hd__lpflow*; "
          f"opt_clean; write_verilog -noattr {net}"])
    # CTS.  Two clocks, the word clock at twice the bit period.
    cts_tcl = work / "cts.tcl"
    cts_v = work / "half_rate_cts.v"
    lef = args.lef
    tech = str(Path(lef).parent.parent / "techlef/sky130_fd_sc_hd__nom.tlef")
    cts_tcl.write_text(
        f"read_lef {tech}\n"
        f"read_liberty {args.liberty}\n"
        f"read_lef {lef}\n"
        f"read_verilog {net}\n"
        "link_design serializer_half_rate\n"
        "initialize_floorplan -utilization 40 -aspect_ratio 1 "
        "-core_space 2 -site unithd\n"
        # Same pitches as the sky130 HD LibreLane track setup.  place_pins
        # refuses a layer that has no routing tracks.
        "make_tracks li1 -x_offset 0.23 -x_pitch 0.46 -y_offset 0.17 -y_pitch 0.34\n"
        "make_tracks met1 -x_offset 0.17 -x_pitch 0.34 -y_offset 0.17 -y_pitch 0.34\n"
        "make_tracks met2 -x_offset 0.23 -x_pitch 0.46 -y_offset 0.23 -y_pitch 0.46\n"
        "make_tracks met3 -x_offset 0.34 -x_pitch 0.68 -y_offset 0.34 -y_pitch 0.68\n"
        "make_tracks met4 -x_offset 0.46 -x_pitch 0.92 -y_offset 0.46 -y_pitch 0.92\n"
        "make_tracks met5 -x_offset 1.70 -x_pitch 3.40 -y_offset 1.70 -y_pitch 3.40\n"
        "place_pins -hor_layers {met3} -ver_layers {met2}\n"
        "global_placement\n"
        # Without this, TritonCTS reports zero clock-wire RC and the tree
        # power is only the buffer cells.
        "set_wire_rc -signal -layer met2\n"
        "set_wire_rc -clock -layer met3\n"
        "create_clock -name clk_bit -period 5 [get_ports clk_bit]\n"
        "create_clock -name clk_word -period 10 [get_ports clk_word]\n"
        "set_clock_groups -asynchronous -group clk_bit -group clk_word\n"
        "clock_tree_synthesis -buf_list {sky130_fd_sc_hd__clkbuf_1 "
        "sky130_fd_sc_hd__clkbuf_2 sky130_fd_sc_hd__clkbuf_4} "
        "-root_buf sky130_fd_sc_hd__clkbuf_4 -sink_clustering_enable\n"
        "set_propagated_clock [all_clocks]\n"
        # Placement estimate, not OpenRCX.  write_spef has nothing to write
        # until a real extraction, so the power report stays in this session.
        "estimate_parasitics -placement\n"
        "report_power -digits 6\n"
        f"write_verilog {cts_v}\n"
        "report_cts\n"
    )
    cts = subprocess.run([OR, "-exit", str(cts_tcl)], cwd=ROOT, text=True,
                         capture_output=True)
    log = cts.stdout + "\n" + cts.stderr
    (work / "cts.log").write_text(log)
    if cts.returncode != 0:
        raise SystemExit(
            f"error: openroad cts failed ({cts.returncode})\n{log[-2000:]}")
    inserted = re.search(r"Total number of Buffers Inserted:\s*(\d+)", log)
    nbuf = int(inserted.group(1)) if inserted else 0
    if nbuf <= 0 or "CTS-0041" in log or "No valid clock nets" in log:
        raise SystemExit(
            "error: clock_tree_synthesis inserted no buffers; "
            "refusing to report this run as CTS power")
    if "resistance/capacitance values are zero" in log:
        raise SystemExit(
            "error: clock wire RC is still zero; refusing to report CTS power")
    # Power on the CTS netlist needs a VCD of that netlist.  The tree adds
    # buffers, so the pre-CTS VCD does not name them.  Report both: ideal
    # clock on the pre-CTS netlist, and the CTS netlist with default activity
    # labelled as such.  A VCD that does not include the new buffers cannot
    # be called annotated.
    def parse_power(out: str) -> dict:
        groups = {}
        for name in ("Sequential", "Combinational", "Clock", "Total"):
            m = re.search(
                rf"^{name}\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)",
                out, re.M)
            if m:
                groups[name.lower()] = {"total_w": float(m.group(4))}
        if "total" not in groups:
            raise SystemExit("error: power report did not parse\n" + out[-1500:])
        return groups

    def power(netlist: Path, propagated: bool) -> dict:
        tcl = work / f"{netlist.stem}.sta.tcl"
        tcl.write_text(
            f"read_liberty {args.liberty}\n"
            f"read_verilog {netlist}\n"
            "link_design serializer_half_rate\n"
            "create_clock -name clk_bit -period 5 [get_ports clk_bit]\n"
            "create_clock -name clk_word -period 10 [get_ports clk_word]\n"
            + ("set_propagated_clock [all_clocks]\n" if propagated else "")
            + "report_power -digits 6\n"
        )
        return parse_power(_run([STA, "-no_init", "-exit", str(tcl)]).stdout)

    ideal = power(net, False)
    # The CTS report is the in-session placement estimate.  A second STA read
    # of the Verilog drops the estimated wire capacitance.
    tree = parse_power(log)
    record = {
        "schema": 1,
        "kind": "half-rate-cts-power",
        "pre_route": True,
        "activity": "default",
        "annotation_coverage": None,
        "ideal_clock_w": ideal,
        "after_cts_w": tree,
        "buffers_inserted": nbuf,
        "clock_tree_w": tree.get("clock", {}).get("total_w"),
        "analog_w": None,
        "energy_j_per_bit": None,
        "parasitics": "placement-estimate",
        "note": ("Default activity, no VCD: the inserted buffers are not in a "
                 "pre-CTS simulation.  The CTS number is report_power in the "
                 "same OpenROAD session as estimate_parasitics -placement "
                 "(met3 clock, met2 signal).  That is not routed SPEF and not "
                 "an energy-per-bit.  The ideal number has no wire capacitance."),
    }
    clock_w = record["clock_tree_w"]
    if clock_w is None or clock_w <= 0:
        raise SystemExit(
            "error: CTS clock power is 0; refusing to report this run as CTS power")
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(record, indent=2) + "\n")
    print(f"buffers {nbuf}")
    print(f"ideal total {ideal['total']['total_w']*1e3:.4f} mW")
    print(f"cts   total {tree['total']['total_w']*1e3:.4f} mW  "
          f"clock {record['clock_tree_w']*1e3:.4f} mW")
    print(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

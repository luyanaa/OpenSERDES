#!/usr/bin/env python3
"""Annotate a mapped netlist with a workload VCD and report OpenSTA power.

This is the step ``make sim`` does not do.  It synthesizes one RTL top to a
Liberty library, simulates that gate netlist with a workload testbench, then
feeds the VCD to OpenSTA ``read_vcd``.  Coverage comes from
``report_activity_annotation``; it is not inferred from the file existing.

The five named workloads are:

  prbs         15-bit PRBS payload, one new word per frame
  constant     every payload bit held at 0
  alternating  0xAA / 0x55 words
  throughput   back-to-back frames, no idle gap between them
  idle         clock and reset only; no load, enable stays low

``R_useful`` is payload bits per second after the capture overhead.  For the
parallel serializer that overhead is the load cycle, so a frame of RATIO bits
takes RATIO+1 clocks.  ``throughput`` is the same payload with the gap removed
only when the RTL can accept the next frame on the cycle ``frame_done``
returns; this script measures the cycle count from the simulation rather than
assuming it.  ``idle`` has no payload, so its useful rate is zero and E_bit
stays null.  Clock-tree power is the OpenSTA Clock group when the report has
one; analog power is not in a standard-cell run and stays null.

Numbers from this script are pre-route: ideal clocks, no SPEF, no CTS.  They
are workload-annotated gate power, not post-route or silicon power.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ("prbs", "constant", "alternating", "throughput", "idle")
DEFAULT_STA = "/nix/store/6qbn5zjnnnyna6md4rsmiqw4y26y8556-openroad-26Q3/bin/sta"


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd or ROOT, text=True, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(
            f"error: {' '.join(cmd)} failed ({proc.returncode})\n"
            f"{proc.stdout}\n{proc.stderr}")
    return proc


def synthesize(top: str, rtl: list[Path], liberty: Path, netlist: Path,
               chparams: list[tuple[str, str]]) -> None:
    params = " ".join(f"-chparam {name} {value}" for name, value in chparams)
    reads = " ".join(str(p) for p in rtl)
    script = (
        f"read_verilog -sv {reads}; "
        f"hierarchy -top {top} {params}; "
        f"synth -top {top} -flatten; "
        f"dfflibmap -liberty {liberty}; "
        # lpflow isolation cells are not a functional match for ready/enable
        # here: abc maps `ready = ~active & rst_n` onto one and the gate
        # simulation then never leaves reset-looking ready.
        f"abc -liberty {liberty} -dont_use sky130_fd_sc_hd__lpflow*; "

        f"opt_clean; write_verilog -noattr {netlist}"
    )
    _run(["yosys", "-q", "-p", script])


def _scalar_ports(netlist: Path) -> dict[str, list[str]]:
    """Yosys writes a packed port as ``name[i]`` scalars, LSB first in the list."""
    text = netlist.read_text()
    match = re.search(r"module\s+\w+\s*\(([^)]*)\)", text)
    if not match:
        raise SystemExit(f"error: no port list in {netlist}")
    groups: dict[str, list[str]] = {}
    for part in match.group(1).split(","):
        token = part.strip()
        if not token:
            continue
        base = token.split("[", 1)[0]
        groups.setdefault(base, []).append(token)
    return groups


def _tb_text(top: str, workload: str, words: int, data_width: int) -> str:
    """One clock, one frame contract, five activity patterns.

    The testbench counts payload bits and elapsed clocks after reset so the
    useful rate is measured, not assumed.  ``idle`` never loads.
    """
    return f"""`timescale 1ns/1ps
module tb_activity;
    localparam int WORDS = {words};
    localparam int DATA_WIDTH = {data_width};
    localparam int RATIO = WORDS * DATA_WIDTH;
    localparam int FRAMES = {1 if workload == 'idle' else 8};

    logic clk, rst_n, enable, load;
    logic [RATIO-1:0] flat;
    logic ready, serial_out, serial_valid, busy, frame_done;
    int frames, bits, clocks;
    logic [14:0] lfsr;

    // The mapped netlist keeps the packed par_data port.  Drive it as a
    // flat vector; the word loop below fills one word at a time.
    {top} dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .load(load),
        .par_data(flat), .ready(ready), .serial_out(serial_out),
        .serial_valid(serial_valid), .busy(busy), .frame_done(frame_done)
    );

    always #5 clk = ~clk;

    // Icarus rejects a variable index of a packed array inside a task, so the
    // payload is filled here with constant indices.
    function automatic logic [DATA_WIDTH-1:0] next_bits;
        int b;
        begin
            next_bits = '0;
            for (b = 0; b < DATA_WIDTH; b = b + 1) begin
                if ("{workload}" == "constant")
                    next_bits[b] = 1'b0;
                else if ("{workload}" == "alternating")
                    next_bits[b] = b[0];
                else if ("{workload}" == "throughput")
                    next_bits[b] = ~b[0];
                else begin
                    next_bits[b] = lfsr[14];
                    lfsr = {{lfsr[13:0], lfsr[14] ^ lfsr[13]}};
                end
            end
        end
    endfunction

    initial begin
        clk = 1'b0; rst_n = 1'b0; enable = 1'b0; load = 1'b0;
        flat = '0; frames = 0; bits = 0; clocks = 0; lfsr = 15'h7fff;
        $dumpfile("__VCD_PATH__");
        // Depth 0 includes cell pins.  Depth 1 leaves every gate pin
        // unannotated, so report_activity_annotation stays near the port
        // count and the internal power is still the default activity.
        $dumpvars(0, dut);
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        if ("{workload}" == "idle") begin
            repeat (64) @(posedge clk);
        end else begin
            while (frames < FRAMES) begin
                @(negedge clk);
                if (!ready) begin
                    $display("FAIL: ready low");
                    $fatal(1);
                end
                flat = {{next_bits(), next_bits(), next_bits(), next_bits()}};
                load = 1'b1;
                @(posedge clk);
                clocks = clocks + 1;
                #1;
                load = 1'b0;
                enable = 1'b1;
                while (!frame_done) begin
                    @(posedge clk);
                    clocks = clocks + 1;
                    #1;
                    if (serial_valid) bits = bits + 1;
                end
                frames = frames + 1;
                if ("{workload}" != "throughput") begin
                    enable = 1'b0;
                    repeat (4) begin
                        @(posedge clk);
                        clocks = clocks + 1;
                    end
                end
            end
        end
        $display("ACTIVITY bits=%0d clocks=%0d frames=%0d period_ns=10",
                 bits, clocks, frames);
        $finish;
    end
endmodule
"""


def simulate(top: str, netlist: Path, workload: str, words: int,
             data_width: int, work: Path, primitives: list[Path]) -> dict:
    """Simulate the mapped netlist so the VCD names match OpenSTA instances."""
    vcd = work / f"{workload}.vcd"
    tb = work / f"tb_{workload}.sv"
    text = _tb_text(top, workload, words, data_width)
    text = text.replace("__VCD_PATH__", str(vcd))
    tb.write_text(text)
    elf = work / f"sim_{workload}"
    # FUNCTIONAL drops the power-pin check.  Yosys does not tie VPWR/VGND,
    # so the timing models drive every output X and the frame never starts.
    _run(["iverilog", "-g2012", "-DFUNCTIONAL", "-o", str(elf), str(tb),
          str(netlist), *[str(p) for p in primitives]], cwd=work)
    proc = _run(["vvp", str(elf)])
    match = re.search(
        r"ACTIVITY bits=(\d+) clocks=(\d+) frames=(\d+) period_ns=(\d+)",
        proc.stdout)
    if not match:
        raise SystemExit(f"error: activity summary missing for {workload}\n{proc.stdout}")
    bits, clocks, frames, period_ns = (int(g) for g in match.groups())
    if workload != "idle" and (bits <= 0 or clocks <= 0):
        raise SystemExit(f"error: {workload} produced no payload activity")
    seconds = clocks * period_ns * 1e-9
    useful = 0.0 if workload == "idle" else (bits / seconds) / 1e6
    return {"vcd": vcd, "bits": bits, "clocks": clocks, "frames": frames,
            "period_ns": period_ns, "useful_rate_mbps": useful}


def _parse_annotation(text: str) -> dict:
    annotated = unannotated = None
    for line in text.splitlines():
        a = re.search(r"Annotated\s+(\d+)\s+pin activities", line)
        if a:
            annotated = int(a.group(1))
        u = re.match(r"\s*unannotated\s+(\d+)", line)
        if u:
            unannotated = int(u.group(1))
    if annotated is None or unannotated is None:
        raise SystemExit("error: report_activity_annotation did not parse\n" + text)
    total = annotated + unannotated
    return {"annotated_pins": annotated, "unannotated_pins": unannotated,
            "annotation_coverage": (annotated / total) if total else 0.0}


def _parse_power(text: str) -> dict:
    groups = {}
    for name in ("Sequential", "Combinational", "Clock", "Macro", "Pad", "Total"):
        match = re.search(
            rf"^{name}\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)",
            text, re.M)
        if match:
            internal, switching, leakage, total = (float(v) for v in match.groups())
            groups[name.lower()] = {
                "internal_w": internal, "switching_w": switching,
                "leakage_w": leakage, "total_w": total,
            }
    if "total" not in groups:
        raise SystemExit("error: report_power did not parse\n" + text)
    return groups


def sta_power(liberty: Path, netlist: Path, vcd: Path, scope: str,
              period_ns: float, sta: str) -> tuple[dict, dict, str]:
    tcl = vcd.with_suffix(".tcl")
    tcl.write_text(
        f"read_liberty {liberty}\n"
        f"read_verilog {netlist}\n"
        f"link_design serializer\n"
        f"create_clock -name clk -period {period_ns} [get_ports clk]\n"
        f"read_vcd -scope {scope} {vcd}\n"
        "report_activity_annotation\n"
        "report_power -digits 6\n"
    )
    proc = _run([sta, "-no_init", "-exit", str(tcl)])
    text = proc.stdout
    return _parse_annotation(text), _parse_power(text), text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--liberty", required=True)
    ap.add_argument("--primitives", nargs="+", required=True,
                    help="gate Verilog models for the mapped netlist")
    ap.add_argument("--sta", default=DEFAULT_STA)
    ap.add_argument("--workloads", nargs="*", default=list(WORKLOADS),
                    choices=WORKLOADS)
    ap.add_argument("--words", type=int, default=4)
    ap.add_argument("--data-width", type=int, default=8)
    ap.add_argument("--period-ns", type=float, default=10.0)
    ap.add_argument("--out", default=str(ROOT / "build/activity/serializer.json"))
    ap.add_argument("--work", default=str(ROOT / "build/activity"))
    args = ap.parse_args()
    for tool in ("yosys", "iverilog", "vvp", args.sta):
        if shutil.which(tool) is None and not Path(tool).is_file():
            raise SystemExit(f"error: missing tool {tool}")
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    netlist = work / "serializer_mapped.v"
    synthesize("serializer", [ROOT / "rtl/serializer.sv"], Path(args.liberty),
               netlist, [("DATA_WIDTH", str(args.data_width)),
                         ("WORDS", str(args.words)),
                         ("LSB_FIRST", "1"), ("CAPTURE_MODE", "0")])
    records = []
    primitives = [Path(p) for p in args.primitives]
    for name in args.workloads:
        sim = simulate("serializer", netlist, name, args.words,
                       args.data_width, work, primitives)
        coverage, power, raw = sta_power(
            Path(args.liberty), netlist, sim["vcd"], "tb_activity/dut",
            args.period_ns, args.sta)
        (work / f"{name}.sta").write_text(raw)
        total = power["total"]["total_w"]
        useful = sim["useful_rate_mbps"]
        energy = None if useful <= 0 else total / (useful * 1e6)
        clock = power.get("clock", {}).get("total_w")
        records.append({
            "workload": name,
            "activity_source": "vcd",
            "vcd": str(sim["vcd"]),
            "annotation_coverage": coverage["annotation_coverage"],
            "annotated_pins": coverage["annotated_pins"],
            "unannotated_pins": coverage["unannotated_pins"],
            "payload_bits": sim["bits"],
            "clocks": sim["clocks"],
            "frames": sim["frames"],
            "useful_rate_mbps": useful,
            "power_w": {"internal": power["total"]["internal_w"],
                        "switching": power["total"]["switching_w"],
                        "leakage": power["total"]["leakage_w"],
                        "total": total,
                        "clock_tree": clock,
                        "analog": None},
            "energy_j_per_bit": energy,
            "pre_route": True,
            "note": "Ideal clock, no SPEF and no CTS.  Analog power is not in this netlist.",
        })
        print(f"{name}: coverage={coverage['annotation_coverage']:.4f} "
              f"P={total*1e3:.6f} mW R={useful:.4f} Mb/s E={energy}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": 1, "kind": "workload-activity",
                                "netlist": str(netlist), "records": records},
                              indent=2) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

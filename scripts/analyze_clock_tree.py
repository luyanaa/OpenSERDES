#!/usr/bin/env python3
"""Report clock-tree depth/fanout from a gate-level .lvs.v netlist.

This is intentionally limited to the qflow-style netlists in OpenSERDES:
  sky130_fd_sc_hd__clkbuf_*  with .A()/.X()
  sky130_fd_sc_hd__dfrtp_* / dfstp_* with .CLK()
It is a topology audit, not a timing/skew signoff tool.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict, deque


def parse(path: str):
    text = open(path).read()
    root_match = re.search(r"module\s+\w+\s*\((.*?)\);", text, re.S)
    if not root_match:
        raise SystemExit(f"no module header in {path}")
    header = root_match.group(1)
    roots = [n for n in ("CLK_IN", "CLK") if re.search(rf"\b{n}\b", header)]
    if not roots:
        raise SystemExit(f"no CLK/CLK_IN port in {path}")

    buffers = {}
    for m in re.finditer(
        r"sky130_fd_sc_hd__clkbuf_(\d+)\s+(\w+)\s*\((.*?)\);", text, re.S
    ):
        drive, name, body = int(m.group(1)), m.group(2), m.group(3)
        ports = dict(re.findall(r"\.(\w+)\(([^)]*)\)", body))
        if "A" in ports and "X" in ports:
            buffers[name] = {"a": ports["A"].strip(), "x": ports["X"].strip(), "drive": drive}

    flops = []
    missing_clk = []
    for m in re.finditer(
        r"sky130_fd_sc_hd__df(?:r|s)tp_\d+\s+(\w+)\s*\((.*?)\);", text, re.S
    ):
        name, body = m.group(1), m.group(2)
        ports = dict(re.findall(r"\.(\w+)\(([^)]*)\)", body))
        if "CLK" in ports:
            flops.append((name, ports["CLK"].strip()))
        else:
            missing_clk.append(name)

    return roots[0], buffers, flops, missing_clk


def analyze(path: str):
    root, buffers, flops, missing_clk = parse(path)
    by_a = defaultdict(list)
    for name, b in buffers.items():
        by_a[b["a"]].append(name)

    by_x = {b["x"]: name for name, b in buffers.items()}
    depth = {}
    q = deque()
    # The root port feeds zero or more buffers.  A buffer directly fed by the
    # root is depth 1.
    for name in by_a.get(root, []):
        depth[name] = 1
        q.append(name)

    while q:
        name = q.popleft()
        for child in by_a.get(buffers[name]["x"], []):
            if child not in depth:
                depth[child] = depth[name] + 1
                q.append(child)

    # Attach flops to the net that drives their CLK pin.
    flops_by_net = defaultdict(list)
    for name, net in flops:
        flops_by_net[net].append(name)

    reachable = []
    unreachable = []
    for name, net in flops:
        if net in by_x or net == root:
            reachable.append((name, net))
        else:
            unreachable.append((name, net))

    depth_rows = []
    for d in sorted(set(depth.values())):
        bufs = [n for n, v in depth.items() if v == d]
        # Flops are counted at the depth of the buffer that drives them.
        fcount = 0
        max_fan = 0
        for b in bufs:
            fcount += len(flops_by_net.get(buffers[b]["x"], []))
            fan = len(by_a.get(buffers[b]["x"], [])) + len(flops_by_net.get(buffers[b]["x"], []))
            max_fan = max(max_fan, fan)
        depth_rows.append({
            "depth": d,
            "buffers": len(bufs),
            "drives": sorted({buffers[b]["drive"] for b in bufs}),
            "flops": fcount,
            "max_fanout": max_fan,
        })

    return {
        "path": path,
        "root": root,
        "buffers": len(buffers),
        "flops": len(flops),
        "reachable_flops": len(reachable),
        "unreachable_flops": len(unreachable),
        "missing_clk_flops": len(missing_clk),
        "missing_clk_examples": missing_clk[:5],
        "unreachable_nets": sorted({n for _, n in unreachable}),
        "max_depth": max(depth.values()) if depth else 0,
        "depth_rows": depth_rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("netlists", nargs="+")
    args = ap.parse_args()
    for path in args.netlists:
        r = analyze(path)
        print(f"## {path}")
        print(f"root={r['root']} buffers={r['buffers']} flops={r['flops']} "
              f"reachable={r['reachable_flops']} unreachable={r['unreachable_flops']} "
              f"missing_clk={r['missing_clk_flops']} max_depth={r['max_depth']}")
        if r["missing_clk_flops"]:
            print(f"missing CLK examples: {', '.join(r['missing_clk_examples'])}")
        if r["unreachable_nets"]:
            print(f"unreachable clock nets: {', '.join(r['unreachable_nets'])}")
        print("| depth | buffers | drives | flops | max fanout |")
        print("|---:|---:|---|---:|---:|")
        for row in r["depth_rows"]:
            print(f"| {row['depth']} | {row['buffers']} | {row['drives']} | "
                  f"{row['flops']} | {row['max_fanout']} |")
        print()


if __name__ == "__main__":
    main()

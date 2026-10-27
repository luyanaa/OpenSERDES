#!/usr/bin/env python3
"""Parameter and configuration regression for the portable SerDes RTL."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "regress"
IVERILOG = shutil.which("iverilog")
VVP = shutil.which("vvp")

# (name, top, rtl, params)
# params are iverilog -P overrides on the testbench top module.
CASES = [
    ("serializer_8x4_lsb", "tb_serializer", ["rtl/serializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1, "CAPTURE_MODE": 0}),
    ("serializer_8x4_msb", "tb_serializer", ["rtl/serializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 0, "CAPTURE_MODE": 0}),
    ("serializer_8x4_capture", "tb_serializer", ["rtl/serializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1, "CAPTURE_MODE": 1}),
    ("serializer_16x4", "tb_serializer", ["rtl/serializer.sv"],
     {"DATA_WIDTH": 16, "WORDS": 4, "LSB_FIRST": 1, "CAPTURE_MODE": 1}),
    ("serializer_32x8", "tb_serializer", ["rtl/serializer.sv"],
     {"DATA_WIDTH": 32, "WORDS": 8, "LSB_FIRST": 1, "CAPTURE_MODE": 1}),
    ("serializer_8x1_capture", "tb_serializer", ["rtl/serializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 1, "LSB_FIRST": 1, "CAPTURE_MODE": 1}),
    ("serializer_1x4", "tb_serializer", ["rtl/serializer.sv"],
     {"DATA_WIDTH": 1, "WORDS": 4, "LSB_FIRST": 1, "CAPTURE_MODE": 1}),

    ("deserializer_8x4_lsb", "tb_deserializer", ["rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1}),
    ("deserializer_8x4_msb", "tb_deserializer", ["rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 0}),
    ("deserializer_16x4", "tb_deserializer", ["rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 16, "WORDS": 4, "LSB_FIRST": 1}),
    ("deserializer_32x8", "tb_deserializer", ["rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 32, "WORDS": 8, "LSB_FIRST": 1}),
    ("deserializer_8x1", "tb_deserializer", ["rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 1, "LSB_FIRST": 1}),
    ("deserializer_1x4", "tb_deserializer", ["rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 1, "WORDS": 4, "LSB_FIRST": 1}),

    ("loopback_8x4_lsb", "tb_serdes_loopback",
     ["rtl/serializer.sv", "rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1}),
    ("loopback_8x4_msb", "tb_serdes_loopback",
     ["rtl/serializer.sv", "rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 0}),
    ("loopback_16x4", "tb_serdes_loopback",
     ["rtl/serializer.sv", "rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 16, "WORDS": 4, "LSB_FIRST": 1}),
    ("loopback_32x8", "tb_serdes_loopback",
     ["rtl/serializer.sv", "rtl/deserializer_word.sv", "rtl/deserializer.sv"],
     {"DATA_WIDTH": 32, "WORDS": 8, "LSB_FIRST": 1}),

    ("stream_8x4", "tb_serializer_stream", ["rtl/serializer_stream.sv"], {}),
    ("cdr_fixed", "tb_cdr", ["rtl/cdr_sync.sv", "rtl/cdr_oversampling.sv"], {}),
    ("cdr_fixed_os2", "tb_cdr", ["rtl/cdr_sync.sv", "rtl/cdr_oversampling.sv"],
     {"OVERSAMPLE": 2, "N_CHECK": 200}),
    ("cdr_adaptive_0ppm", "tb_cdr_adaptive", ["rtl/cdr_sync.sv", "rtl/cdr_adaptive.sv"],
     {"PPM": 0.0}),
    ("cdr_adaptive_p700ppm", "tb_cdr_adaptive", ["rtl/cdr_sync.sv", "rtl/cdr_adaptive.sv"],
     {"PPM": 700.0}),
    ("cdr_adaptive_n700ppm", "tb_cdr_adaptive", ["rtl/cdr_sync.sv", "rtl/cdr_adaptive.sv"],
     {"PPM": -700.0}),
    ("frame_sync", "tb_frame_sync",
     ["rtl/comma_align.sv", "rtl/deserializer.sv", "rtl/deserializer_word.sv"], {}),
    ("link_scoreboard", "tb_serdes_channel", ["tb/channel_model.sv"] + [str(p) for p in sorted((ROOT / "rtl").glob("*.sv"))], {}),
    ("cdr_bypass", "tb_cdr_bypass", [str(p) for p in sorted((ROOT / "rtl").glob("*.sv"))], {}),
    ("compat_legacy", "tb_compat_legacy",
     ["rtl/serializer.sv", "rtl/deserializer_word.sv", "rtl/deserializer.sv",
      "rtl/compat/serializer_unit_cell_1.v", "rtl/compat/deserialiser_unit_cell_1.v"], {}),
    ("compat_port_order", "tb_compat_port_order",
     ["rtl/serializer.sv", "rtl/deserializer_word.sv", "rtl/deserializer.sv",
      "rtl/compat/serializer_unit_cell_1.v", "rtl/compat/deserialiser_unit_cell_1.v"], {}),
]


def run_case(name: str, top: str, sources: list[str], params: dict) -> tuple[bool, str]:
    BUILD.mkdir(parents=True, exist_ok=True)
    exe = BUILD / name
    cmd = [IVERILOG, "-g2012", "-Wall", "-o", str(exe)]
    for key, value in params.items():
        if isinstance(value, float):
            cmd.append(f"-P{top}.{key}={value}")
        elif isinstance(value, bool):
            cmd.append(f"-P{top}.{key}={1 if value else 0}")
        else:
            cmd.append(f"-P{top}.{key}={value}")
    cmd += [f"tb/{top}.sv", *sources]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=180)
    if proc.returncode != 0:
        return False, f"compile failed\n{proc.stdout}\n{proc.stderr}"
    run = subprocess.run([VVP, str(exe)], cwd=ROOT, text=True, capture_output=True, timeout=300)
    out = run.stdout + run.stderr
    ok = run.returncode == 0 and "PASS" in out
    return ok, out.strip().splitlines()[-1] if out.strip() else "(no output)"


def run_generator_checks() -> tuple[int, int]:
    """Validate specs and generate the two supported reference packages."""
    py = sys.executable
    total = 0
    failed = 0
    spec_cases = [
        ("default", [], 0),
        ("minimal", [], 0),
        ("tx_stream", [], 0),
        ("soc", [], 2),
        ("soc", ["--allow-incomplete"], 0),
        ("phy_ip", ["--allow-incomplete"], 0),
    ]
    for spec_name, extra, expected in spec_cases:
        total += 1
        cmd = [py, "scripts/gen_ip.py", "--spec", f"spec/{spec_name}.yaml", "--check", *extra]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60)
        ok = proc.returncode == expected
        detail = (proc.stdout + proc.stderr).strip().splitlines()
        print(f"{'PASS' if ok else 'FAIL'} generator_{spec_name}_{'allow' if extra else 'strict'}: "
              f"{detail[-1] if detail else proc.returncode}")
        if not ok:
            failed += 1

    for spec_name, expected_design in (("default", "serdes_digital_top"),
                                       ("tx_stream", "serdes_tx_stream_digital"),
                                       ("minimal", "serdes_digital_top")):
        total += 1
        out = ROOT / "build" / "gen" / spec_name
        cmd = [py, "scripts/gen_ip.py", "--spec", f"spec/{spec_name}.yaml",
               "--out", str(out), "--force"]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60)
        ok = proc.returncode == 0
        detail = "generation failed"
        if ok:
            manifest = json.loads((out / "manifest.json").read_text())
            required = [out / "rtl" / "filelist.f",
                        out / "constraints" / "pre_pnr.sdc",
                        out / "constraints" / "post_cts.sdc",
                        out / "constraints" / "signoff.sdc",
                        out / "pnr" / "librelane.yaml",
                        out / "qualification.json"]
            ok = manifest.get("design") == expected_design and all(p.exists() for p in required)
            detail = f"design={manifest.get('design')} files={'ok' if ok else 'missing'}"
        print(f"{'PASS' if ok else 'FAIL'} generator_package_{spec_name}: {detail}")
        if not ok:
            failed += 1
    return total, failed


def main() -> int:
    if not IVERILOG or not VVP:
        print("iverilog/vvp not found", file=sys.stderr)
        return 2
    failures = 0
    for name, top, sources, params in CASES:
        try:
            ok, detail = run_case(name, top, sources, params)
        except subprocess.TimeoutExpired:
            ok, detail = False, "timeout"
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            failures += 1
    gen_total, gen_failed = run_generator_checks()
    total = len(CASES) + gen_total
    failures += gen_failed
    print(f"\n{total - failures}/{total} regression cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

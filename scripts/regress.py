#!/usr/bin/env python3
"""Parameter and configuration regression for the portable SerDes RTL."""
from __future__ import annotations

import hashlib
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
YOSYS = shutil.which("yosys")

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
    ("serializer_pingpong_8x4_lsb", "tb_serializer_pingpong",
     ["rtl/serializer_pingpong.sv"], {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1}),
    ("serializer_pingpong_8x4_msb", "tb_serializer_pingpong",
     ["rtl/serializer_pingpong.sv"], {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 0}),
    ("serializer_pingpong_32x8", "tb_serializer_pingpong",
     ["rtl/serializer_pingpong.sv"], {"DATA_WIDTH": 32, "WORDS": 8, "LSB_FIRST": 1}),
    ("serializer_half_8x4", "tb_serializer_half_rate",
     ["rtl/serializer_stream.sv", "rtl/serializer_half_rate.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1}),
    ("serializer_half_32x8", "tb_serializer_half_rate",
     ["rtl/serializer_stream.sv", "rtl/serializer_half_rate.sv"],
     {"DATA_WIDTH": 32, "WORDS": 8, "LSB_FIRST": 0}),
    ("serializer_half_5x3", "tb_serializer_half_rate",
     ["rtl/serializer_stream.sv", "rtl/serializer_half_rate.sv"],
     {"DATA_WIDTH": 5, "WORDS": 3, "LSB_FIRST": 1}),
    ("deserializer_half_8x4", "tb_deserializer_half_rate",
     ["rtl/deserializer_half_rate.sv"], {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1}),
    ("deserializer_half_32x8", "tb_deserializer_half_rate",
     ["rtl/deserializer_half_rate.sv"], {"DATA_WIDTH": 32, "WORDS": 8, "LSB_FIRST": 0}),
    ("receiver_pair_sampler", "tb_receiver_pair_sampler",
     ["rtl/receiver_pair_sampler.sv"], {}),
    ("serializer_hier_8x4_lsb", "tb_serializer_hier_2to1",
     ["rtl/serializer_stream.sv", "rtl/serializer_hier_2to1.sv"],
     {"DATA_WIDTH": 8, "WORDS": 4, "LSB_FIRST": 1}),
    ("serializer_hier_32x8_msb", "tb_serializer_hier_2to1",
     ["rtl/serializer_stream.sv", "rtl/serializer_hier_2to1.sv"],
     {"DATA_WIDTH": 32, "WORDS": 8, "LSB_FIRST": 0}),
    ("serializer_hier_5x3", "tb_serializer_hier_2to1",
     ["rtl/serializer_stream.sv", "rtl/serializer_hier_2to1.sv"],
     {"DATA_WIDTH": 5, "WORDS": 3, "LSB_FIRST": 1}),
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
    ("apb_csr", "tb_apb_csr", ["rtl/apb_csr.sv"], {}),
    ("async_fifo", "tb_async_fifo", ["rtl/async_fifo.sv"], {}),
    ("axis_tx_adapter", "tb_axis_tx_adapter", ["rtl/axis_tx_adapter.sv"], {}),
    ("axis_rx_adapter", "tb_axis_rx_adapter", ["rtl/axis_rx_adapter.sv"], {}),
    ("serdes_tx_axis", "tb_serdes_tx_axis",
     ["rtl/serdes_tx_axis_digital.sv", "rtl/axis_tx_adapter.sv",
      "rtl/serializer_stream.sv", "rtl/reset_sync.sv"], {}),
    ("serdes_rx_axis", "tb_serdes_rx_axis",
     ["rtl/serdes_rx_axis_digital.sv", "rtl/deserializer_word.sv", "rtl/deserializer.sv",
      "rtl/cdr_sync.sv", "rtl/cdr_oversampling.sv", "rtl/cdr_adaptive.sv",
      "rtl/comma_align.sv", "rtl/receiver_sampler.sv", "rtl/reset_sync.sv",
      "rtl/serdes_rx_digital.sv", "rtl/axis_rx_adapter.sv"], {}),
    ("prbs7", "tb_prbs", ["rtl/prbs_gen.sv", "rtl/prbs_check.sv"], {"WIDTH": 7}),
    ("prbs15", "tb_prbs", ["rtl/prbs_gen.sv", "rtl/prbs_check.sv"], {"WIDTH": 15}),
    ("prbs31", "tb_prbs", ["rtl/prbs_gen.sv", "rtl/prbs_check.sv"], {"WIDTH": 31}),
    ("serdes_soc_wrapper", "tb_serdes_soc_wrapper", [str(p) for p in sorted((ROOT / "rtl").glob("*.sv"))], {}),
    ("serdes_soc_cdc_wrapper", "tb_serdes_soc_cdc_wrapper", [str(p) for p in sorted((ROOT / "rtl").glob("*.sv"))], {}),
    ("serdes_bist7", "tb_serdes_bist", ["rtl/serdes_bist.sv", "rtl/prbs_gen.sv", "rtl/prbs_check.sv"], {"WIDTH": 7}),
    ("serdes_bist15", "tb_serdes_bist", ["rtl/serdes_bist.sv", "rtl/prbs_gen.sv", "rtl/prbs_check.sv"], {"WIDTH": 15}),
    ("serdes_bist31", "tb_serdes_bist", ["rtl/serdes_bist.sv", "rtl/prbs_gen.sv", "rtl/prbs_check.sv"], {"WIDTH": 31}),
    ("serdes_bist_word", "tb_serdes_bist_word", ["rtl/prbs_word_gen.sv", "rtl/prbs_word_check.sv", "rtl/serializer_stream.sv", "rtl/deserializer.sv", "rtl/deserializer_word.sv"], {}),
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
        ("tx_axis", [], 0),
        ("rx_axis", [], 0),
        ("soc_single_clock", [], 0),
        ("soc", [], 0),
        ("soc", ["--allow-incomplete"], 0),
        ("phy_ip", ["--allow-incomplete"], 0),
        ("power_tradeoff", [], 0),
        ("low_power", [], 0),
        ("tx_half_rate", [], 0),
        ("ihp_ez130", [], 0),
        ("tx_pingpong", [], 0),
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
                                       ("minimal", "serdes_digital_top"),
                                       ("tx_axis", "serdes_tx_axis_digital"),
                                       ("rx_axis", "serdes_rx_axis_digital"),
                                       ("soc_single_clock", "serdes_soc_wrapper"),
                                       ("soc", "serdes_soc_cdc_wrapper"),
                                       ("tx_half_rate", "serdes_tx_half_rate_digital"),
                                       ("tx_pingpong", "serdes_tx_digital")):
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
                        out / "rtl" / "filelist_sv2v.f",
                        out / "rtl" / "sv2v",
                        out / "constraints" / "pre_pnr.sdc",
                        out / "constraints" / "post_cts.sdc",
                        out / "constraints" / "signoff.sdc",
                        out / "pnr" / "librelane.yaml",
                        out / "qualification.json"]
            ok = manifest.get("design") == expected_design and all(p.exists() for p in required)
            detail = f"design={manifest.get('design')} files={'ok' if ok else 'missing'}"
            if ok:
                # P1 regressions: signoff.sdc must be a real third stage,
                # CLOCK_PORT must list every clock, and confidence must be
                # labelled as a capability-gate estimate.
                post = (out / "constraints" / "post_cts.sdc").read_text()
                sign = (out / "constraints" / "signoff.sdc").read_text()
                pnr_text = (out / "pnr" / "librelane.yaml").read_text()
                cp_block = pnr_text.split("CLOCK_PORT:", 1)[1].split("\n\n", 1)[0]
                scope_ok = any(
                    a.get("confidence_scope") == "capability_gate_estimate_not_signoff"
                    for a in manifest["architecture"].values())
                ok = ("set_max_fanout" in sign and "set_max_fanout" not in post
                      and cp_block.lstrip().startswith("-") and scope_ok)
                detail += f" p1_contracts={'ok' if ok else 'fail'}"
            if ok:
                files = (out / "rtl" / "filelist.f").read_text().split()
                compile_cmd = [IVERILOG, "-g2012", "-Wall", "-s", expected_design,
                               "-o", str(out / "compile_smoke"), *files]
                compile_proc = subprocess.run(compile_cmd, cwd=out / "rtl", text=True,
                                              capture_output=True, timeout=120)
                ok = compile_proc.returncode == 0
                detail += f" compile={'ok' if ok else 'fail'}"
            if ok and YOSYS is not None:
                sv_files = (out / "rtl" / "filelist_sv2v.f").read_text().split()
                yosys_cmd = [
                    YOSYS, "-q", "-p",
                    "read_verilog " + " ".join(sv_files) +
                    f"; synth -top {expected_design}; stat",
                ]
                yosys_proc = subprocess.run(
                    yosys_cmd, cwd=out / "rtl", text=True,
                    capture_output=True, timeout=300,
                )
                ok = yosys_proc.returncode == 0
                detail += f" sv2v_yosys={'ok' if ok else 'fail'}"
        print(f"{'PASS' if ok else 'FAIL'} generator_package_{spec_name}: {detail}")
        if not ok:
            failed += 1
    return total, failed


def run_signoff_checks() -> tuple[int, int]:
    """Synthetic report collection and measured-PPA import checks."""
    py = sys.executable
    total = 0
    failed = 0
    package = ROOT / "build" / "gen" / "default"
    if not (package / "manifest.json").exists():
        return 0, 0

    reports = ROOT / "build" / "signoff" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "rtl_functional.json").write_text(
        json.dumps({"checks": {"lint": True, "sim": True, "synth": True}}))
    for name, key, value in (("pre_pnr_setup.rpt", "setup", "0.12"),
                             ("pre_pnr_hold.rpt", "hold", "0.01"),
                             ("post_route_setup.rpt", "setup", "0.04"),
                             ("post_route_hold.rpt", "hold", "0.008")):
        (reports / name).write_text(f"{key} WNS: {value}\n")
    (reports / "drc.rpt").write_text("DRC clean: 0 violations\n")
    (reports / "lvs.rpt").write_text("Circuits match uniquely\n")
    (reports / "antenna.rpt").write_text("Antenna clean: 0 violations\n")
    (reports / "cdc.rpt").write_text("CDC clean: 0 violations\n")
    (reports / "ber.json").write_text(json.dumps({
        "ber_target": 1e-12, "ber": 1e-13,
        "jtol_pass": True, "eye_margin_ui": 0.2,
    }))

    total += 1
    proc = subprocess.run(
        [py, "scripts/collect_signoff.py", "--package", str(package),
         "--reports", str(reports), "--requested-level", "LINK_VALIDATED"],
        cwd=ROOT, text=True, capture_output=True, timeout=60)
    ok = proc.returncode == 0 and "LINK_VALIDATED" in proc.stdout
    print(f"{'PASS' if ok else 'FAIL'} signoff_full_report: "
          f"{(proc.stdout + proc.stderr).strip().splitlines()[-1] if proc.stdout else proc.returncode}")
    if not ok:
        failed += 1

    partial = ROOT / "build" / "signoff" / "partial"
    partial.mkdir(parents=True, exist_ok=True)
    for path in reports.glob("*"):
        if path.name != "post_route_hold.rpt":
            (partial / path.name).write_text(path.read_text())
    total += 1
    proc = subprocess.run(
        [py, "scripts/collect_signoff.py", "--package", str(package),
         "--reports", str(partial), "--requested-level", "POST_ROUTE_TIMING"],
        cwd=ROOT, text=True, capture_output=True, timeout=60)
    ok = proc.returncode == 1 and "post_route_hold_wns" in proc.stdout
    print(f"{'PASS' if ok else 'FAIL'} signoff_missing_hold_rejected: "
          f"exit={proc.returncode}")
    if not ok:
        failed += 1

    ppa_dir = ROOT / "build" / "ppa"
    ppa_dir.mkdir(parents=True, exist_ok=True)

    # Fixture: a synthetic but structurally real LibreLane power report, fed
    # through collect_power.py so the measured claim has verifiable provenance.
    fixture_run = ppa_dir / "fixture_run"
    (fixture_run / "final").mkdir(parents=True, exist_ok=True)
    (fixture_run / "final" / "metrics.json").write_text(json.dumps({
        "power__internal__total": 0.0034,
        "power__switching__total": 0.0012,
        "power__leakage__total": 5e-8,
        "power__total": 0.0046,
    }) + "\n")
    (fixture_run / "resolved.json").write_text(json.dumps({
        "DEFAULT_CORNER": "nom_tt_025C_1v80", "CLOCK_PERIOD": 20.0,
        "PDK": "sky130A", "STD_CELL_LIBRARY": "sky130_fd_sc_hd",
    }) + "\n")
    record = ppa_dir / "power_record.json"
    subprocess.run([py, "scripts/collect_power.py", "--run", str(fixture_run),
                    "--out", str(record)], cwd=ROOT, text=True,
                   capture_output=True, timeout=60, check=True)
    record_hash = hashlib.sha256(record.read_bytes()).hexdigest()

    header = ("tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,"
              "confidence,source,power_mw,provenance_report,provenance_sha256,"
              "corner,period_ns,git_commit,tool\n")
    def ppa_row(rate, source):
        return ("icsprout55,ics55_LLSC_H7CL,rx,fixed_oversampling,%s,1.0,1.0,1.0,"
                "measured_post_route,%s,4.6,%s,%s,nom_tt_025C_1v80,20.0,fixture,OpenSTA\n"
                % (rate, source, record.resolve(), record_hash))

    csv_path = ppa_dir / "measured.csv"
    csv_path.write_text(header + ppa_row(200, "run-001"))
    ppa_path = ppa_dir / "measured.yaml"
    subprocess.run([py, "scripts/import_ppa.py", "--csv", str(csv_path),
                    "--out", str(ppa_path), "--replace"], cwd=ROOT,
                   text=True, capture_output=True, timeout=60, check=True)
    total += 1
    proc = subprocess.run(
        [py, "scripts/gen_ip.py", "--spec", "spec/default.yaml", "--check",
         "--ppa-db", str(ppa_path)], cwd=ROOT, text=True, capture_output=True, timeout=60)
    ok = False
    detail = proc.returncode
    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout)
            ok = payload.get("rx_confidence") == "measured_post_route"
            detail = payload.get("rx_confidence")
        except json.JSONDecodeError:
            pass
    print(f"{'PASS' if ok else 'FAIL'} ppa_measured_used: {detail}")
    if not ok:
        failed += 1

    low_csv = ppa_dir / "low.csv"
    low_csv.write_text(header + ppa_row(80, "run-low"))
    low_yaml = ppa_dir / "low.yaml"
    subprocess.run([py, "scripts/import_ppa.py", "--csv", str(low_csv),
                    "--out", str(low_yaml), "--replace"], cwd=ROOT,
                   text=True, capture_output=True, timeout=60, check=True)
    total += 1
    proc = subprocess.run(
        [py, "scripts/gen_ip.py", "--spec", "spec/default.yaml", "--check",
         "--ppa-db", str(low_yaml)], cwd=ROOT, text=True, capture_output=True, timeout=60)
    ok = proc.returncode == 2 and "gate 80" in (proc.stdout + proc.stderr)
    print(f"{'PASS' if ok else 'FAIL'} ppa_capability_gate_rejected: exit={proc.returncode}")
    if not ok:
        failed += 1

    # An unproven measured claim must be rejected by the importer...
    unproven_csv = ppa_dir / "unproven.csv"
    unproven_csv.write_text(
        "tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,confidence,source\n"
        "icsprout55,ics55_LLSC_H7CL,rx,fixed_oversampling,200,1.0,1.0,1.0,measured_post_route,run-x\n")
    total += 1
    strict = subprocess.run([py, "scripts/import_ppa.py", "--csv", str(unproven_csv),
                             "--out", str(ppa_dir / "unproven.yaml"), "--replace"],
                            cwd=ROOT, text=True, capture_output=True, timeout=60)
    ok = strict.returncode != 0 and "provenance" in (strict.stdout + strict.stderr)
    print(f"{'PASS' if ok else 'FAIL'} ppa_unproven_rejected: exit={strict.returncode}")
    if not ok:
        failed += 1

    # ...and only allowed with --allow-unproven, which downgrades the claim.
    downgraded_yaml = ppa_dir / "unproven_allow.yaml"
    subprocess.run([py, "scripts/import_ppa.py", "--csv", str(unproven_csv),
                    "--out", str(downgraded_yaml), "--replace", "--allow-unproven"],
                   cwd=ROOT, text=True, capture_output=True, timeout=60, check=True)
    total += 1
    proc = subprocess.run(
        [py, "scripts/gen_ip.py", "--spec", "spec/default.yaml", "--check",
         "--ppa-db", str(downgraded_yaml)], cwd=ROOT, text=True,
        capture_output=True, timeout=60)
    ok = False
    detail = proc.returncode
    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout)
            ok = payload.get("rx_confidence") == "unverified_claim"
            detail = payload.get("rx_confidence")
        except json.JSONDecodeError:
            pass
    print(f"{'PASS' if ok else 'FAIL'} ppa_unproven_downgraded: {detail}")
    if not ok:
        failed += 1

    # A hand-written YAML that bypasses the importer must also be downgraded.
    hand_yaml = ppa_dir / "handwritten.yaml"
    hand_yaml.write_text(
        "schema: 1\nentries:\n- tech: icsprout55\n  scl: ics55_LLSC_H7CL\n"
        "  kind: rx\n  arch: fixed_oversampling\n  max_line_rate_mbps: 200\n"
        "  metrics: {area: 1.0, power: 1.0, latency: 1.0}\n"
        "  confidence: measured_post_route\n  source: handwritten\n")
    total += 1
    proc = subprocess.run(
        [py, "scripts/gen_ip.py", "--spec", "spec/default.yaml", "--check",
         "--ppa-db", str(hand_yaml)], cwd=ROOT, text=True,
        capture_output=True, timeout=60)
    ok = False
    detail = proc.returncode
    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout)
            ok = payload.get("rx_confidence") == "unverified_claim"
            detail = payload.get("rx_confidence")
        except json.JSONDecodeError:
            pass
    print(f"{'PASS' if ok else 'FAIL'} ppa_handwritten_downgraded: {detail}")
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
    signoff_total, signoff_failed = run_signoff_checks()
    total = len(CASES) + gen_total + signoff_total
    failures += gen_failed + signoff_failed
    print(f"\n{total - failures}/{total} regression cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generated-top SoC end-to-end smoke.

This compiles the generated package's own RTL filelist with
``tb_soc_e2e.sv`` and instantiates ``serdes_soc_cdc_wrapper`` (not the
standalone RX core).  It covers:

* four independent clocks and reset release;
* APB scratch write/read and CONTROL programming;
* TX AXI4-Stream -> FIFO -> serializer -> source-synchronous loopback ->
  RX core -> RX FIFO -> RX AXI4-Stream, with intermittent m_axis backpressure;
* BIST PRBS loopback and coherent snapshot registers;
* a deliberate RX-FIFO overflow phase.

It is a generated-RTL integration smoke, not post-route timing, silicon,
channel/jitter, or full SoC software validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TB = Path(__file__).resolve().parent / "tb_soc_e2e.sv"
BUILD = ROOT / "build" / "verify"


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sources(rtl_dir: Path) -> list[Path]:
    fl = rtl_dir / "filelist_sv2v.f"
    if not fl.is_file():
        fl = rtl_dir / "filelist.f"
    if not fl.is_file():
        raise FileNotFoundError(f"no package filelist under {rtl_dir}")
    return [rtl_dir / line.strip() for line in fl.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _compile(rtl_dir: Path, data_width: int, words: int, sync_len: int,
             sync_pattern: int, n_frames: int, rx_delay_ps: int,
             rx_freq_offset_ppm: int, tx_jitter_ps: int, rx_jitter_ps: int,
             jitter_freq_hz: int, keep: bool) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    sources = [TB, *_sources(rtl_dir)]
    binary = BUILD / (f"tb_soc_e2e_dw{data_width}_w{words}_s{sync_len}"
                      f"_n{n_frames}_p{rx_delay_ps}_f{rx_freq_offset_ppm}"
                      f"_tj{tx_jitter_ps}_rj{rx_jitter_ps}.vvp")
    if binary.is_file() and all(
        s.stat().st_mtime <= binary.stat().st_mtime for s in sources
    ):
        return binary
    if shutil.which("iverilog") is None:
        raise RuntimeError("iverilog is required for SoC E2E")
    cmd = [
        "iverilog", "-g2012", "-Wall", "-o", str(binary),
        f"-Ptb_soc_e2e.DATA_WIDTH={data_width}",
        f"-Ptb_soc_e2e.WORDS={words}",
        f"-Ptb_soc_e2e.SYNC_LEN={sync_len}",
        f"-Ptb_soc_e2e.SYNC_PATTERN={sync_len}'h{sync_pattern:x}",
        f"-Ptb_soc_e2e.N_FRAMES={n_frames}",
        f"-Ptb_soc_e2e.RX_DELAY_PS={rx_delay_ps}",
        f"-Ptb_soc_e2e.RX_FREQ_OFFSET_PPM={rx_freq_offset_ppm}",
        f"-Ptb_soc_e2e.TX_JITTER_PS={tx_jitter_ps}",
        f"-Ptb_soc_e2e.RX_JITTER_PS={rx_jitter_ps}",
        f"-Ptb_soc_e2e.JITTER_FREQ_HZ={jitter_freq_hz}",
        *map(str, sources),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"iverilog failed:\n{proc.stdout}\n{proc.stderr}")
    return binary


def run_soc_e2e(package: str | Path, *, data_width: int | None = None,
                words: int | None = None, sync_len: int | None = None,
                sync_pattern: int | None = None, n_frames: int = 4,
                rx_delay_ps: int = 5000, rx_freq_offset_ppm: int = 0,
                tx_jitter_ps: int = 0, rx_jitter_ps: int = 0,
                jitter_freq_hz: int = 1_000_000,
                rtl_dir: str | Path | None = None,
                keep: bool = False) -> dict[str, Any]:
    pkg = Path(package).resolve()
    manifest_path = pkg / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    params = manifest.get("params", {}) if isinstance(manifest.get("params"), dict) else {}
    dw = int(data_width if data_width is not None else params.get("DATA_WIDTH", 8))
    w = int(words if words is not None else params.get("WORDS", 4))
    sl = int(sync_len if sync_len is not None else params.get("SYNC_LEN", dw))
    sp = int(sync_pattern if sync_pattern is not None else params.get("SYNC_PATTERN", 0xBC))
    rdir = Path(rtl_dir).resolve() if rtl_dir else pkg / "rtl"
    binary = _compile(rdir, dw, w, sl, sp, n_frames, int(rx_delay_ps),
                      int(rx_freq_offset_ppm), int(tx_jitter_ps),
                      int(rx_jitter_ps), int(jitter_freq_hz), keep)
    proc = subprocess.run(["vvp", str(binary)], text=True, capture_output=True,
                          timeout=120, cwd=ROOT)
    text = proc.stdout + proc.stderr
    m = re.search(
        r"SOC_E2E frames=(\d+) expected=(\d+) errors=(\d+) apb_errors=(\d+) "
        r"status=([0-9a-fA-F]+) bist_status=([0-9a-fA-F]+) "
        r"bist_errors=(\d+) bist_bits=(\d+)", text)
    parsed: dict[str, Any] = {"result_line": m.group(0) if m else None}
    if m:
        keys = ("frames", "expected", "errors", "apb_errors", "status",
                "bist_status", "bist_errors", "bist_bits")
        parsed.update({k: int(v, 16) if k in ("status", "bist_status") else int(v)
                       for k, v in zip(keys, m.groups())})
    passed = m is not None and "SOC_E2E PASS" in text and proc.returncode == 0
    if m:
        passed = passed and parsed["errors"] == 0 and parsed["apb_errors"] == 0
        passed = passed and parsed["frames"] == parsed["expected"]
    return {
        "kind": "generated-top-soc-e2e",
        "package": str(pkg),
        "rtl_dir": str(rdir),
        "manifest_sha256": _sha(manifest_path),
        "filelist_sha256": _sha(rdir / "filelist_sv2v.f") or _sha(rdir / "filelist.f"),
        "parameters": {"DATA_WIDTH": dw, "WORDS": w, "SYNC_LEN": sl,
                       "SYNC_PATTERN": sp, "N_FRAMES": n_frames,
                       "RX_DELAY_PS": int(rx_delay_ps),
                       "RX_FREQ_OFFSET_PPM": int(rx_freq_offset_ppm),
                       "TX_JITTER_PS": int(tx_jitter_ps),
                       "RX_JITTER_PS": int(rx_jitter_ps),
                       "JITTER_FREQ_HZ": int(jitter_freq_hz)},
        "compile": {"binary": str(binary)},
        "returncode": proc.returncode,
        "pass": bool(passed),
        **parsed,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-2000:],
        "note": ("serdes_soc_cdc_wrapper top with APB, AXIS backpressure, BIST "
                 "loopback and RX-FIFO overflow; no channel/jitter/post-route."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", required=True)
    ap.add_argument("--data-width", type=int, default=None)
    ap.add_argument("--words", type=int, default=None)
    ap.add_argument("--sync-len", type=int, default=None)
    ap.add_argument("--sync-pattern", type=lambda x: int(x, 0), default=None)
    ap.add_argument("--n-frames", type=int, default=4)
    ap.add_argument("--rx-delay-ps", type=int, default=5000)
    ap.add_argument("--rx-freq-offset-ppm", type=int, default=0)
    ap.add_argument("--tx-jitter-ps", type=int, default=0)
    ap.add_argument("--rx-jitter-ps", type=int, default=0)
    ap.add_argument("--jitter-freq-hz", type=int, default=1000000)
    ap.add_argument("--rtl-dir", default=None)
    ap.add_argument("--out", default=str(BUILD / "soc_e2e.json"))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    try:
        report = run_soc_e2e(
            args.package, data_width=args.data_width, words=args.words,
            sync_len=args.sync_len, sync_pattern=args.sync_pattern,
            n_frames=args.n_frames, rx_delay_ps=args.rx_delay_ps,
            rx_freq_offset_ppm=args.rx_freq_offset_ppm,
            tx_jitter_ps=args.tx_jitter_ps, rx_jitter_ps=args.rx_jitter_ps,
            jitter_freq_hz=args.jitter_freq_hz,
            rtl_dir=args.rtl_dir, keep=args.keep,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"SOC_E2E {'PASS' if report['pass'] else 'FAIL'}: "
          f"frames={report.get('frames')} expected={report.get('expected')} "
          f"errors={report.get('errors')} apb_errors={report.get('apb_errors')}")
    print(f"wrote {out}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

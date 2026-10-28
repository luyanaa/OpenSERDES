"""iverilog-driven jitter-tolerance harness for the real RTL CDR.

This is the "time-domain" path of the verification framework: the Python side
generates a PRBS bit stream with per-boundary TIE (RJ/SJ/DCD/ppm), the RTL
``cdr_adaptive`` recovers bits, and Python computes BER and bit slips from the
recovered sequence.  It is not a transistor-level or analog model; it is the
real digital CDR state machine driven by a jittered edge schedule.

Requirements: iverilog on PATH.  No cocotb dependency.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

from verification.channel.jitter import build_tie
from verification.patterns.prbs import prbs_bits

ROOT = Path(__file__).resolve().parents[2]
TB = Path(__file__).resolve().parent / "tb_cdr_jtol.sv"
CDR_SOURCES = [ROOT / "rtl" / "cdr_adaptive.sv", ROOT / "rtl" / "cdr_sync.sv"]
BUILD_DIR = ROOT / "build" / "verify"

__all__ = ["JtolResult", "run_rtl_jtol", "build_stimulus"]


@dataclass
class JtolResult:
    ber: float
    errors: int
    compared: int
    bit_slips: int
    lock_losses: int
    lock_wait_cycles: int
    recovered: int
    offset: int
    locked: bool
    rj_rms_ui: float
    sj_peak_ui: float
    sj_freq_hz: float
    dcd_peak_ui: float
    ppm: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compile_tb() -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    binary = BUILD_DIR / "tb_cdr_jtol"
    sources = [TB, *CDR_SOURCES]
    if binary.exists() and all(
        src.stat().st_mtime <= binary.stat().st_mtime for src in sources
    ):
        return binary
    if shutil.which("iverilog") is None:
        raise RuntimeError("iverilog is required for the RTL jitter harness")
    cmd = ["iverilog", "-g2012", "-Wall", "-o", str(binary), *map(str, sources)]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"iverilog failed:\n{proc.stdout}\n{proc.stderr}")
    return binary


def build_stimulus(
    bits: np.ndarray,
    ui_s: float,
    *,
    rj_rms_ui: float = 0.0,
    sj_peak_ui: float = 0.0,
    sj_freq_hz: float = 0.0,
    dcd_peak_ui: float = 0.0,
    ppm: float = 0.0,
    seed: int = 2026,
) -> np.ndarray:
    """Return absolute edge times in seconds for each bit.

    ``t_n = n * UI * (1 + ppm/1e6) + tie_n``.  The CDR clock is independent of
    the TX timing, so ppm is the TX/RX frequency offset seen by the receiver.
    """
    bits = np.asarray(bits, dtype=np.int8)
    rng = np.random.default_rng(seed)
    tie_s, _meta = build_tie(
        bits,
        ui_s,
        rj_rms_ui=rj_rms_ui,
        sj_peak_ui=sj_peak_ui,
        sj_freq_hz=sj_freq_hz,
        dcd_peak_ui=dcd_peak_ui,
        rng=rng,
    )
    n = bits.size
    nominal = np.arange(n, dtype=np.float64) * ui_s * (1.0 + ppm * 1e-6)
    times = nominal + tie_s[:n]
    if np.any(np.diff(times) <= 0):
        bad = int(np.argmax(np.diff(times) <= 0))
        raise ValueError(
            f"jitter caused nonmonotonic edge times at bit {bad}; "
            "reduce stress or model lost bits explicitly"
        )
    return times


def _write_stimulus(path: Path, bits: np.ndarray, times_s: np.ndarray) -> None:
    # 1 ps resolution is far below one UI for the rates used here.
    lines = [f"{int(round(t * 1e12))} {int(b)}" for t, b in zip(times_s, bits)]
    path.write_text("\n".join(lines) + "\n")


def _read_recovered(path: Path) -> np.ndarray:
    if not path.exists():
        return np.zeros(0, dtype=np.int8)
    text = path.read_text().split()
    return np.array([int(v) for v in text], dtype=np.int8)


def _best_offset(
    recovered: np.ndarray, expected: np.ndarray, start: int, width: int, span: int
) -> tuple[int, int]:
    """Best alignment in [start-span, start+span]; returns (offset, errors)."""
    best_off = start
    best_err = width + 1
    lo = max(0, start - span)
    hi = min(start + span, expected.size - width)
    for off in range(lo, hi + 1):
        err = int(np.count_nonzero(recovered[:width] != expected[off : off + width]))
        if err < best_err or (err == best_err and abs(off - start) < abs(best_off - start)):
            best_err = err
            best_off = off
    return best_off, best_err


def analyse(
    recovered: np.ndarray,
    expected: np.ndarray,
    *,
    lock_losses: int,
    lock_wait: int,
    params: dict[str, float],
) -> JtolResult:
    if recovered.size == 0:
        return JtolResult(
            ber=1.0,
            errors=0,
            compared=0,
            bit_slips=0,
            lock_losses=lock_losses,
            lock_wait_cycles=lock_wait,
            recovered=0,
            offset=0,
            locked=False,
            **params,
        )

    probe = min(1024, recovered.size, expected.size)
    offset, _ = _best_offset(recovered, expected, 0, probe, 255)

    usable = min(recovered.size, expected.size - offset)
    recovered = recovered[:usable]
    errors = int(np.count_nonzero(recovered != expected[offset : offset + usable]))
    ber = errors / usable if usable else 1.0

    # Bit-slip estimate: track the best local alignment in consecutive windows.
    slips = 0
    window = 512
    prev = offset
    for pos in range(0, max(0, usable - window + 1), window):
        local_rec = recovered[pos : pos + window]
        # Compare against the expected window at the previous alignment.
        cand, _ = _best_offset(local_rec, expected, prev, window, 32)
        if cand != prev:
            slips += abs(cand - prev)
            prev = cand

    return JtolResult(
        ber=ber,
        errors=errors,
        compared=usable,
        bit_slips=slips,
        lock_losses=lock_losses,
        lock_wait_cycles=lock_wait,
        recovered=int(recovered.size),
        offset=offset,
        locked=True,
        **params,
    )


def run_rtl_jtol(
    *,
    rate_mbps: float = 12.5,
    nbits: int = 2000,
    rj_rms_ui: float = 0.0,
    sj_peak_ui: float = 0.0,
    sj_freq_hz: float = 0.0,
    dcd_peak_ui: float = 0.0,
    ppm: float = 0.0,
    prbs_width: int = 15,
    seed: int = 2026,
    keep: bool = False,
) -> JtolResult:
    """Drive the RTL CDR with a jittered PRBS stream and analyse its output."""
    if nbits < 256:
        raise ValueError("nbits must be >= 256")
    ui_s = 1.0 / (rate_mbps * 1e6)
    expected = prbs_bits(prbs_width, nbits + 4096, seed=None)
    times = build_stimulus(
        expected,
        ui_s,
        rj_rms_ui=rj_rms_ui,
        sj_peak_ui=sj_peak_ui,
        sj_freq_hz=sj_freq_hz,
        dcd_peak_ui=dcd_peak_ui,
        ppm=ppm,
        seed=seed,
    )
    binary = _compile_tb()

    tmp = Path(tempfile.mkdtemp(prefix="serdes_jtol_", dir=str(ROOT / "build" / "verify")))
    stim = tmp / "stim.txt"
    out = tmp / "rx_bits.txt"
    _write_stimulus(stim, expected, times)

    cmd = [
        "vvp",
        str(binary),
        f"+STIM={stim}",
        f"+OUT={out}",
        f"+NBITS={nbits}",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"vvp failed:\n{proc.stdout}\n{proc.stderr}")

    lock_losses = 0
    lock_wait = 0
    locked = False
    for line in proc.stdout.splitlines():
        if line.startswith("NO_LOCK"):
            locked = False
            m = re.search(r"lock_wait=(\d+)", line)
            if m:
                lock_wait = int(m.group(1))
        elif line.startswith("META"):
            locked = True
            for key in ("recovered", "lock_wait", "lock_losses", "freq_trim"):
                m = re.search(rf"{key}=(-?\d+)", line)
                if m:
                    if key == "lock_wait":
                        lock_wait = int(m.group(1))
                    elif key == "lock_losses":
                        lock_losses = int(m.group(1))

    recovered = _read_recovered(out)
    result = analyse(
        recovered,
        expected,
        lock_losses=lock_losses,
        lock_wait=lock_wait,
        params={
            "rj_rms_ui": float(rj_rms_ui),
            "sj_peak_ui": float(sj_peak_ui),
            "sj_freq_hz": float(sj_freq_hz),
            "dcd_peak_ui": float(dcd_peak_ui),
            "ppm": float(ppm),
        },
    )
    result.locked = result.locked and locked
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        result_dict = result.to_dict()
        result_dict["workdir"] = str(tmp)
    return result


def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rate-mbps", type=float, default=12.5)
    ap.add_argument("--nbits", type=int, default=2000)
    ap.add_argument("--rj-rms-ui", type=float, default=0.0)
    ap.add_argument("--sj-peak-ui", type=float, default=0.0)
    ap.add_argument("--sj-freq-hz", type=float, default=0.0)
    ap.add_argument("--dcd-peak-ui", type=float, default=0.0)
    ap.add_argument("--ppm", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    result = run_rtl_jtol(
        rate_mbps=args.rate_mbps,
        nbits=args.nbits,
        rj_rms_ui=args.rj_rms_ui,
        sj_peak_ui=args.sj_peak_ui,
        sj_freq_hz=args.sj_freq_hz,
        dcd_peak_ui=args.dcd_peak_ui,
        ppm=args.ppm,
        seed=args.seed,
        keep=args.keep,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

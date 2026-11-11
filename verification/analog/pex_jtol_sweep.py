#!/usr/bin/env python3
"""Closed-loop jitter tolerance through the TX/RX PEX chain.

The analog half is the same TX-PEX -> RC-channel -> RX-PEX transient used by
``mixed_signal_e2e``, now with deterministic sinusoidal jitter on the TX edges.
The sliced decisions are replayed into the generated package's digital
``serdes_rx_digital`` frame scoreboard, so the reported jitter tolerance is a
property of the whole chain and not of either half alone.

Each point sweeps one jitter amplitude; the record keeps the BER and the frame
errors per point plus the eye metrics, and fails closed on a simulation error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.mixed_signal_e2e import run as run_pex  # noqa: E402
from verification.rtl.runner import (  # noqa: E402
    _compile_tb, _read_frames, build_framed_bits,
)
from verification.analog.magic_pex import find_pdk_root  # noqa: E402


def _digital_frames(package: Path, decisions: np.ndarray, ui_ns: float,
                    workdir: Path, data_width: int, words: int) -> dict:
    """Replay the analog decisions through the package's RX scoreboard."""
    rtl = package / "rtl"
    fl = rtl / "filelist_sv2v.f"
    if not fl.is_file():
        fl = rtl / "filelist.f"
    sources = [rtl / name for name in fl.read_text().split()]
    times_s = (100.0 + np.arange(decisions.size, dtype=float) * ui_ns) * 1e-9
    stim = workdir / "serial.txt"
    stim.write_text("\n".join(
        f"{int(round(t * 1e12))} {int(b)}"
        for t, b in zip(times_s, decisions)
    ) + "\n")
    binary = _compile_tb("frame", data_width, words, data_width,
                        oversample=1, cdr_mode=2, align_mode=2,
                        rtl_sources=sources)
    out = workdir / "frames.txt"
    proc = subprocess.run(
        ["vvp", str(binary), f"+STIM={stim}", f"+OUT={out}"],
        text=True, capture_output=True, timeout=600,
    )
    frames = _read_frames(out) if out.is_file() else []
    return {
        "returncode": proc.returncode,
        "frames": frames,
        "stdout_tail": proc.stdout[-500:],
        "stderr_tail": proc.stderr[-500:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", required=True)
    ap.add_argument("--tx-pex", default=str(ROOT / "build/verify/magic_pex/Inv_Transmitter.pex.spice"))
    ap.add_argument("--rx-pex", default=str(ROOT / "build/verify/magic_pex/Resist_FB_INV.pex.spice"))
    ap.add_argument("--rate-mbps", type=float, default=100.0)
    ap.add_argument("--words", type=int, default=4)
    ap.add_argument("--data-width", type=int, default=8)
    ap.add_argument("--r-ohm", type=float, default=100.0)
    ap.add_argument("--c-pf", type=float, default=16.0)
    ap.add_argument("--frames", type=int, default=4,
                    help="frames per jitter point (PRBS-7 payload each)")
    ap.add_argument("--sj-ui", type=float, action="append", default=None,
                    help="jitter amplitude in UI; repeat the flag to sweep")
    ap.add_argument("--sj-freq-mhz", type=float, default=2.0)
    ap.add_argument("--out", default=str(ROOT / "build/verify/pex_jtol.json"))
    args = ap.parse_args()

    for p in (args.tx_pex, args.rx_pex):
        if not Path(p).is_file():
            print(f"error: missing PEX {p}; run make magic-pex / analog-macro-pex",
                  file=sys.stderr)
            return 2
    pkg = Path(args.package).resolve()
    ui_ns = 1000.0 / args.rate_mbps
    amps = args.sj_ui or [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    pdk = find_pdk_root()

    bits, expected = build_framed_bits(7, args.frames,
                                        word_bits=args.data_width,
                                        words=args.words, comma=0xBC)
    tmp = Path(tempfile.mkdtemp(prefix="pex_jtol_",
                                dir=str(ROOT / "build" / "verify")))

    points = []
    worst_ok = None
    for amp in amps:
        work = tmp / f"sj{amp:.3f}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            mixed = run_pex(Path(args.tx_pex), Path(args.rx_pex),
                            [int(x) for x in bits], ui_ns, pdk,
                            work / "analog", "tt", args.r_ohm, args.c_pf,
                            sj_ui=amp, sj_freq_mhz=args.sj_freq_mhz)
        except RuntimeError as exc:
            points.append({"sj_ui": amp, "sim_error": str(exc)[-300:]})
            break
        decisions = np.asarray(mixed["samples"], dtype=np.int8)
        dig = _digital_frames(pkg, decisions, ui_ns, work,
                              args.data_width, args.words)
        got = [tuple(f) for f in dig["frames"]]
        want = [tuple(int(x) for x in e) for e in expected]
        frame_errors = sum(1 for g, w in zip(got, want) if g != w)
        analog_errors = int(mixed.get("errors", 0))
        rec = {
            "sj_ui": amp,
            "sj_freq_mhz": args.sj_freq_mhz,
            "analog_errors": analog_errors,
            "analog_ber": mixed.get("ber"),
            # BER-gated eye openings of the analog half: clean is a zero-error
            # mask, the looser one allows 5% bit errors.
            "eye_open_clean_ns": mixed.get("eye_open_clean_ns"),
            "eye_open_ber_ns": mixed.get("eye_open_ber_ns"),
            "eye_height_v": mixed.get("eye_height_v"),
            "sample_offset_ns": mixed.get("sample_offset_ns"),
            "frames_expected": len(want),
            "frames_recovered": len(got),
            "frame_errors": frame_errors,
            "digital_returncode": dig["returncode"],
            "status": "pass" if frame_errors == 0 and dig["returncode"] == 0 else "fail",
        }
        points.append(rec)
        if rec["status"] == "pass":
            worst_ok = amp
        print(f"  sj={amp:.3f} UI -> {rec['status']} "
              f"(frame_errors={frame_errors}, analog_errors={analog_errors}, "
              f"eye_clean={rec['eye_open_clean_ns']} ns)", flush=True)

    payload = {
        "kind": "pex-jtol-sweep",
        "package": str(pkg),
        "tx_pex": str(args.tx_pex),
        "rx_pex": str(args.rx_pex),
        "rate_mbps": args.rate_mbps,
        "ui_ns": ui_ns,
        "sj_freq_mhz": args.sj_freq_mhz,
        "channel": {"r_ohm": args.r_ohm, "c_pf": args.c_pf},
        "points": points,
        "tolerated_sj_ui": worst_ok,
        "note": ("Closed loop: TX PEX -> RC -> RX PEX decisions replayed into "
                 "the generated serdes_rx_digital frame scoreboard.  "
                 "tolerated_sj_ui is the largest amplitude with zero frame "
                 "errors, not a measured silicon jitter tolerance."),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out}")
    return 0 if worst_ok is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())

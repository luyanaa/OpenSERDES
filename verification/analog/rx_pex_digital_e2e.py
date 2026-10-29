#!/usr/bin/env python3
"""RX PEX -> digital serdes_rx_digital frame recovery.

The analog portion is the same TX PEX -> RC -> RX PEX chain used by
``mixed_signal_e2e``.  Its sliced decisions are written as a source-synchronous
edge file and fed to the package's ``tb_rx_frame_jtol`` (digital CDR/
deserializer/comma alignment).  This closes the analog-to-digital boundary for
one frame; it is not full SoC/channel/PVT signoff.
"""

from __future__ import annotations

import argparse
import json
import shutil
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


def package_sources(package: Path) -> list[Path]:
    rtl = package / "rtl"
    fl = rtl / "filelist_sv2v.f"
    if not fl.is_file():
        fl = rtl / "filelist.f"
    if not fl.is_file():
        raise FileNotFoundError(f"no package filelist under {rtl}")
    return [rtl / line.strip() for line in fl.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


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
    ap.add_argument("--out", default=str(ROOT / "build/verify/rx_pex_digital_e2e.json"))
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()
    for p in (args.tx_pex, args.rx_pex):
        if not Path(p).is_file():
            print(f"error: missing PEX {p}; run make magic-pex / analog-macro-pex",
                  file=sys.stderr)
            return 2
    pkg = Path(args.package).resolve()
    sources = package_sources(pkg)
    ui_ns = 1000.0 / args.rate_mbps
    bits, expected = build_framed_bits(
        7, 1, word_bits=args.data_width, words=args.words, comma=0xBC
    )
    tmp = Path(tempfile.mkdtemp(prefix="rx_pex_e2e_",
                                dir=str(ROOT / "build" / "verify")))
    try:
        mixed = run_pex(
            Path(args.tx_pex), Path(args.rx_pex), [int(x) for x in bits],
            ui_ns, find_pdk_root(), tmp / "analog", "tt", args.r_ohm, args.c_pf,
        )
        # Use the recovered decisions as the serial stream.  A source-sync
        # receiver samples at the bit centers, so put transitions on the
        # nominal bit boundaries.
        decisions = np.asarray(mixed["samples"], dtype=np.int8)
        # Leave the TB reset/read phase before the first edge; otherwise the
        # stimulus reader catches up past the comma at t=0 while reset is high.
        times_s = (100.0 + np.arange(decisions.size, dtype=float) * ui_ns) * 1e-9
        stim = tmp / "serial.txt"
        stim.write_text("\n".join(
            f"{int(round(t * 1e12))} {int(b)}"
            for t, b in zip(times_s, decisions)
        ) + "\n")
        binary = _compile_tb(
            "frame", args.data_width, args.words, args.data_width,
            oversample=1, cdr_mode=2, align_mode=2, rtl_sources=sources,
        )
        out = tmp / "frames.txt"
        proc = subprocess.run(
            ["vvp", str(binary), f"+STIM={stim}", f"+OUT={out}",
             "+NFRAMES=1", f"+HALF_NS={ui_ns/2:.6f}"],
            text=True, capture_output=True, timeout=180, cwd=ROOT,
        )
        recovered = _read_frames(out)
        exp = np.asarray(expected, dtype=np.int64)
        if recovered.size == 0:
            errors = int(exp.size)
            got = []
        else:
            # Compare the first frame; tolerate the analog decision stream's
            # leading idle sample by considering one frame offset.
            got = recovered[0].tolist()
            errors = int(np.count_nonzero(recovered[0] != exp[0]))
        report = {
            "kind": "rx-pex-digital-e2e",
            "package": str(pkg),
            "tx_pex": args.tx_pex,
            "rx_pex": args.rx_pex,
            "rate_mbps": args.rate_mbps,
            "ui_ns": ui_ns,
            "analog": {k: mixed[k] for k in
                       ("errors", "ber", "sample_offset_ns",
                        "txout_min_v", "txout_max_v",
                        "rxin_min_v", "rxin_max_v", "out_min_v", "out_max_v")},
            "serial_bits": [int(x) for x in bits],
            "decisions": decisions.tolist(),
            "expected_frame": exp[0].tolist(),
            "recovered_frame": got,
            "frame_errors": errors,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-1000:],
            "note": ("One analog PEX frame -> digital RX frame scoreboard; "
                     "not PVT/MC/full SoC."),
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"analog errors={mixed['errors']} bit_errors/decisions; "
              f"digital frame_errors={errors} recovered={got}")
        print(f"wrote {out_path}")
        return 0 if mixed["errors"] == 0 and errors == 0 else 1
    finally:
        if not args.workdir:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"kept analog/digital workdir {tmp}")


if __name__ == "__main__":
    raise SystemExit(main())

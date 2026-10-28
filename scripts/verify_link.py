#!/usr/bin/env python3
"""Run the link-verification gate and emit a qualification ``ber.json``.

The gate combines the behavioral stressed-eye/bathtub layer with the RTL
CDR/RX JTOL harness:

* ``eye_margin_ui`` is the 0.5/99.5 threshold-crossing eye width from the
  behavioral 2-UI fold.  It is an **eye metric**, not a BER contour.
* ``ber`` is the RTL-measured BER at the profile's nominal stress.  When zero
  errors are observed the report uses the 95 % rule-of-three upper bound
  ``3/N`` -- zero errors is never reported as BER=0.
* ``jtol_pass`` is true when every swept SJ frequency reaches at least
  ``--jtol-min-amp-ui`` of peak SJ at the requested BER target with no lock
  losses.  It is a conservative, explicitly-defined acceptance rule, not an
  interpolated standards mask.

The output is directly consumable by ``scripts/collect_signoff.py`` (as
``reports/ber.json``) and therefore by ``scripts/qualify.py``.  With
``--package`` the script also records a ``link_capability`` block in the
package manifest; a package without a passing report stays unvalidated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.lut import load_lut, tx_tau_ns  # noqa: E402
from verification.analysis.ber import confidence_interval  # noqa: E402
from verification.analysis.eye import eye_metrics, fold_eye  # noqa: E402
from verification.channel.channel_impulse import RCReflectionChannel, nrz_waveform  # noqa: E402
from verification.channel.jitter import build_tie  # noqa: E402
from verification.cli import load_profile  # noqa: E402
from verification.patterns.prbs import prbs_bits  # noqa: E402
from verification.rtl.runner import run_rtl_jtol  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def behavioral_eye(profile: dict[str, Any], n_bits: int,
                   channel_file: str | None = None,
                   channel_port_map: dict[str, int] | None = None) -> dict[str, float]:
    bits = prbs_bits(profile["prbs_width"], n_bits)
    tie, _ = build_tie(
        bits,
        profile["ui_s"],
        profile["rj_rms_ui"],
        profile["sj_peak_ui"],
        profile["sj_freq_hz"],
        profile["dcd_peak_ui"],
        rng=np.random.default_rng(profile["seed"]),
    )
    wave = nrz_waveform(bits, profile["sps"], tie_ui=tie / profile["ui_s"])
    if channel_file:
        from verification.channel.touchstone import load_touchstone_channel

        channel = load_touchstone_channel(
            channel_file, profile["sps"], profile["ui_s"],
            port_map=channel_port_map,
        )
        wave = channel.apply(wave)
    else:
        channel = RCReflectionChannel(
            tau_ui=profile["channel"]["tau_ui"],
            reflections=profile["channel"]["reflections"],
        )
        wave = channel.apply(wave, profile["sps"])
    if profile["noise_rms_v"] > 0.0:
        rng = np.random.default_rng(profile["seed"] + 1)
        wave = wave + rng.normal(0.0, profile["noise_rms_v"], size=wave.size)
    folded = fold_eye(wave, profile["sps"])
    metrics = eye_metrics(folded, threshold_v=profile["threshold_v"], sps=profile["sps"])
    return {
        "eye_height_v": float(metrics["eye_height_v"]),
        "eye_width_ui": float(metrics["eye_width_ui"]),
    }


def rtl_point(
    profile: dict[str, Any],
    *,
    n_bits: int,
    chain: str,
    sj_peak_ui: float,
    sj_freq_hz: float,
    ppm: float,
    data_width: int = 8,
    words: int = 4,
    sync_len: int | None = None,
    oversample: int = 8,
    cdr_mode: int | None = None,
    align_mode: int | None = None,
    package_rtl_dir: str | None = None,
    channel_file: str | None = None,
    channel_port_map: dict[str, int] | None = None,
    allow_long: bool = True,
) -> dict[str, Any]:
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        raise RuntimeError("iverilog and vvp are required for the RTL gate")
    result = run_rtl_jtol(
        rate_mbps=float(profile["rate_mbps"]),
        nbits=n_bits,
        rj_rms_ui=profile["rj_rms_ui"],
        sj_peak_ui=sj_peak_ui,
        sj_freq_hz=sj_freq_hz,
        dcd_peak_ui=profile["dcd_peak_ui"],
        ppm=ppm,
        prbs_width=profile["prbs_width"],
        seed=profile["seed"],
        chain=chain,
        data_width=data_width,
        words=words,
        sync_len=sync_len,
        oversample=oversample,
        cdr_mode=cdr_mode,
        align_mode=align_mode,
        package_rtl_dir=package_rtl_dir,
        noise_rms_v=profile["noise_rms_v"],
        channel_tau_ui=profile["channel"]["tau_ui"],
        channel_file=channel_file,
        channel_port_map=channel_port_map,
        threshold_v=profile["threshold_v"],
    )
    return result.to_dict()


def jtol_sweep(
    profile: dict[str, Any],
    *,
    n_bits: int,
    chain: str,
    freqs: list[float],
    amps: list[float],
    ppm: float,
    ber_target: float,
    min_amp_ui: float,
    data_width: int = 8,
    words: int = 4,
    sync_len: int | None = None,
    oversample: int = 8,
    cdr_mode: int | None = None,
    align_mode: int | None = None,
    package_rtl_dir: str | None = None,
    channel_file: str | None = None,
    channel_port_map: dict[str, int] | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    per_freq: dict[str, Any] = {}
    for freq in freqs:
        passing: list[dict[str, Any]] = []
        for amp in amps:
            point = rtl_point(
                profile,
                n_bits=n_bits,
                chain=chain,
                sj_peak_ui=amp,
                sj_freq_hz=freq,
                ppm=ppm,
                data_width=data_width,
                words=words,
                sync_len=sync_len,
                oversample=oversample,
                cdr_mode=cdr_mode,
                align_mode=align_mode,
                package_rtl_dir=package_rtl_dir,
                channel_file=channel_file,
                channel_port_map=channel_port_map,
            )
            ber_upper = _upper95(int(point["errors"]), int(point["compared"]))
            row = {
                "sj_freq_hz": float(freq),
                "sj_amplitude_ui": float(amp),
                "sj_peak_to_peak_ui": 2.0 * float(amp),
                "ber": float(point["ber"]),
                "ber_upper95": ber_upper,
                "errors": int(point["errors"]),
                "compared": int(point["compared"]),
                "lock_losses": int(point["lock_losses"]),
                "bit_slips": int(point["bit_slips"]),
                "locked": bool(point["locked"]),
                "confidence": 0.95,
                "pass": bool(
                    ber_upper <= ber_target
                    and int(point["lock_losses"]) == 0
                    and int(point["bit_slips"]) == 0
                    and bool(point["locked"])
                ),
            }
            rows.append(row)
            if row["pass"]:
                passing.append(row)
        best = max(passing, key=lambda r: r["sj_amplitude_ui"]) if passing else None
        per_freq[str(freq)] = {
            "max_passing_amplitude_ui": (
                None if best is None else float(best["sj_amplitude_ui"])
            ),
            "passes_min_amplitude": bool(
                best is not None and best["sj_amplitude_ui"] >= min_amp_ui
            ),
            "best_ber": None if best is None else float(best["ber"]),
            "best_ber_upper95": None if best is None else float(best["ber_upper95"]),
        }
    ok = all(item["passes_min_amplitude"] for item in per_freq.values())
    return ok, rows, per_freq


def _upper95(errors: int, n_bits: int) -> float:
    """95 % binomial upper bound; zero errors use the 3/N rule."""
    if n_bits <= 0:
        return 1.0
    _, upper = confidence_interval(int(errors), int(n_bits), level=0.95)
    return float(upper)


def load_package_binding(package: str | None, profile: dict[str, Any]) -> dict[str, Any]:
    if not package:
        return {
            "bound": False,
            "errors": ["no --package given; capability is unbound"],
        }
    pkg = Path(package).resolve()
    manifest_path = pkg / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"package manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    params = manifest.get("params", {}) or {}
    data_width = int(params.get("DATA_WIDTH", 8))
    words = int(params.get("WORDS", 4))
    sync_len = int(params.get("SYNC_LEN", data_width))
    line_rate = manifest.get("line_rate_mbps")
    design = manifest.get("design")
    vc = manifest.get("verification_config", {}) or {}
    errors: list[str] = []
    if line_rate is None:
        errors.append("manifest has no line_rate_mbps")
    elif abs(float(line_rate) - float(profile["rate_mbps"])) > max(
        1e-6, 1e-6 * float(line_rate)
    ):
        errors.append(
            f"profile rate {profile['rate_mbps']:g} Mb/s != manifest "
            f"{float(line_rate):g} Mb/s"
        )
    if design not in ("serdes_soc_wrapper", "serdes_soc_cdc_wrapper"):
        errors.append(f"design {design!r} is not a link wrapper")
    for key in ("cdr_mode", "oversample", "align_mode", "framing", "top",
                "rtl_source", "clock_ports"):
        if key not in vc:
            errors.append(f"manifest verification_config missing {key}")
    if vc.get("top") not in (None, design):
        errors.append(f"verification_config.top {vc.get('top')!r} != design {design!r}")
    if vc.get("align_mode") == 2 and vc.get("framing") != "comma_per_frame":
        errors.append("verification_config align_mode=2 requires comma_per_frame")
    if vc.get("rtl_source") not in (None, "sv2v", "native"):
        errors.append(f"unknown rtl_source {vc.get('rtl_source')!r}")
    periods = vc.get("clock_periods_ns", {}) or {}
    for clock in ("tx_clk", "rx_clk"):
        period = periods.get(clock)
        if period is None:
            errors.append(f"verification_config.clock_periods_ns missing {clock}")
        elif abs(float(period) - 1e3 / float(profile["rate_mbps"])) > max(
            1e-6, 1e-6 * float(period)
        ):
            errors.append(
                f"{clock} period {period:g} ns != profile UI "
                f"{1e9 / float(profile['rate_mbps']):g} ns"
            )
    return {
        "bound": not errors,
        "errors": errors,
        "package": str(pkg),
        "manifest": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "spec_sha256": manifest.get("spec_sha256"),
        "design": design,
        "data_width": data_width,
        "words": words,
        "sync_len": sync_len,
        "line_rate_mbps": line_rate,
        "verification_config": vc,
        "package_rtl_dir": str(pkg / "rtl"),
        "interface": manifest.get("interface"),
        "params": params,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    profile = load_profile(args.profile)
    if args.channel_port_map:
        args.channel_port_map = {
            part.split("=")[0].strip(): int(part.split("=")[1])
            for part in args.channel_port_map.split(",") if part.strip()
        }
    binding = load_package_binding(args.package, profile)
    data_width = int(binding.get("data_width", 8))
    words = int(binding.get("words", 4))
    sync_len = int(binding.get("sync_len", data_width))
    vc = binding.get("verification_config", {}) or {}
    oversample = int(vc.get("oversample", 8))
    cdr_mode = int(vc.get("cdr_mode", 2))
    align_mode = int(vc.get("align_mode", 2))
    package_rtl_dir = binding.get("package_rtl_dir")
    if args.chain == "frame" and align_mode != 2:
        # The frame scoreboard needs per-frame comma re-alignment.
        binding["bound"] = False
        binding.setdefault("errors", []).append(
            f"chain=frame requires align_mode=2; manifest has {align_mode}")
    lut_info = None
    if args.analog_lut:
        lut = load_lut(args.analog_lut)
        tau_ns = tx_tau_ns(lut, args.tx_load_ff)
        if tau_ns is None:
            raise RuntimeError("analog LUT has no TX rise time for the requested load")
        tau_ui = tau_ns / (profile["ui_s"] * 1e9)
        profile["channel"]["tau_ui"] = tau_ui
        lut_info = {
            "path": str(Path(args.analog_lut).resolve()),
            "model_kind": lut.get("model_kind"),
            "pdk": lut.get("pdk"),
            "tech": lut.get("tech"),
            "tech_sha256": lut.get("tech_sha256"),
            "load_ff": args.tx_load_ff,
            "tau_ns": tau_ns,
            "tau_ui": tau_ui,
        }
    if not args.skip_rtl and shutil.which("iverilog") is None:
        raise RuntimeError(
            "iverilog/vvp are required for the RTL gate; install them or pass "
            "--skip-rtl for a behavioral-only report (which cannot declare "
            "link capability)"
        )

    eye = behavioral_eye(
        profile, args.eye_bits,
        channel_file=args.channel_file,
        channel_port_map=args.channel_port_map,
    )
    eye_ok = eye["eye_width_ui"] >= args.min_eye_ui

    capability_reasons: list[str] = []
    if args.skip_rtl:
        ber_value = 1.0
        nominal = {"ber": 1.0, "compared": 0, "errors": 0,
                   "lock_losses": 0, "bit_slips": 0, "locked": False}
        jtol_ok = False
        jtol_rows: list[dict[str, Any]] = []
        per_freq: dict[str, Any] = {}
        capability_reasons.append("RTL path skipped")
    else:
        nominal = rtl_point(
            profile,
            n_bits=args.bits,
            chain=args.chain,
            sj_peak_ui=profile["sj_peak_ui"],
            sj_freq_hz=profile["sj_freq_hz"],
            ppm=args.ppm,
            data_width=data_width,
            words=words,
            sync_len=sync_len,
            oversample=oversample,
            cdr_mode=cdr_mode,
            align_mode=align_mode,
            package_rtl_dir=package_rtl_dir,
            channel_file=args.channel_file,
            channel_port_map=args.channel_port_map,
        )
        if nominal["compared"] == 0:
            ber_value = 1.0
            capability_reasons.append("no recovered bits")
        else:
            ber_value = _upper95(int(nominal["errors"]), int(nominal["compared"]))

        jtol_ok, jtol_rows, per_freq = jtol_sweep(
            profile,
            n_bits=args.jtol_bits,
            chain=args.chain,
            freqs=args.jtol_freqs,
            amps=args.jtol_amps,
            ppm=args.ppm,
            ber_target=args.ber_target,
            min_amp_ui=args.jtol_min_amp_ui,
            data_width=data_width,
            words=words,
            sync_len=sync_len,
            oversample=oversample,
            cdr_mode=cdr_mode,
            align_mode=align_mode,
            package_rtl_dir=package_rtl_dir,
            channel_file=args.channel_file,
            channel_port_map=args.channel_port_map,
        )

    ber_ok = ber_value <= args.ber_target
    nom_lock_ok = int(nominal["lock_losses"]) == 0
    binding_ok = bool(binding.get("bound"))
    smoke_pass = bool(ber_ok and jtol_ok and eye_ok and nom_lock_ok and binding_ok)
    if not ber_ok:
        capability_reasons.append(f"BER {ber_value:.3e} > {args.ber_target:.1e}")
    if not jtol_ok:
        capability_reasons.append("SJ sweep below mask")
    if not eye_ok:
        capability_reasons.append(f"eye width {eye['eye_width_ui']:.4f} UI < {args.min_eye_ui}")
    if not nom_lock_ok:
        capability_reasons.append("nominal run lost lock")
    if not binding_ok:
        capability_reasons += list(binding.get("errors", []))

    profile_path = Path(args.profile).resolve()
    checked_utc = datetime.now(timezone.utc).isoformat()
    capability = {
        "evidence_level": "RTL_LINK_SMOKE" if smoke_pass else "NONE",
        "rtl_link_smoke_pass": smoke_pass,
        "validated_rate_mbps": float(profile["rate_mbps"]),
        "profile": str(profile_path),
        "profile_sha256": _sha256(profile_path),
        "chain": args.chain,
        "binding": binding,
        "ber_target": args.ber_target,
        "ppm": args.ppm,
        "pvt_validated": False,
        "pvt_note": "No PVT or transistor-level model is bundled; PVT is not claimed.",
        "analog_model_validated": bool(
            lut_info is not None
            and str(lut_info.get("model_kind", "")).startswith("sky130-schematic")
        ),
        "analog_lut": lut_info,
        "rtl_link_smoke": {
            "available": True,
            "pass": smoke_pass,
            "n_bits": int(nominal.get("compared", 0)),
            "ber_upper95": ber_value,
            "ber_target": args.ber_target,
            "confidence": 0.95,
            "bit_slips": int(nominal.get("bit_slips", 0)),
            "lock_losses": int(nominal.get("lock_losses", 0)),
            "note": "Short RTL run; the reported BER is a 3/N or Wilson upper bound, not a low-BER guarantee.",
        },
        "statistical_link_model": {
            "available": False,
            "note": "Not run by this gate; the behavioral Gaussian-tail model is a separate evidence level.",
        },
        "measured_link": {
            "available": False,
            "note": "No silicon/measured-link evidence is bundled.",
        },
        "reasons": capability_reasons,
        "checked_utc": checked_utc,
        "git_commit": _git_commit(),
    }
    return {
        "ber_target": args.ber_target,
        # Flat keys match the legacy reports/ber.json contract; the nested
        # checks block is the canonical form used by verify_link itself.
        "ber": ber_value,
        "jtol_pass": jtol_ok,
        "eye_margin_ui": eye["eye_width_ui"],
        "checks": {
            "ber": ber_value,
            "jtol_pass": jtol_ok,
            "eye_margin_ui": eye["eye_width_ui"],
        },
        "capability": capability,
        "profile": str(profile_path),
        "profile_sha256": capability["profile_sha256"],
        "channel_file": args.channel_file,
        "channel_port_map": args.channel_port_map,
        "verification_config": vc,
        "binding": binding,
        "evidence": {
            "eye": {**eye, "min_eye_ui": args.min_eye_ui},
            "nominal": nominal,
            "jtol": {
                "frequencies_hz": [float(f) for f in args.jtol_freqs],
                "amplitudes_ui": [float(a) for a in args.jtol_amps],
                "min_amplitude_ui": args.jtol_min_amp_ui,
                "per_frequency": per_freq,
                "rows": jtol_rows,
            },
        },
        "checked_utc": checked_utc,
        "git_commit": capability["git_commit"],
    }


def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default=str(ROOT / "verification/profiles/stressed.yaml"))
    ap.add_argument("--out", default=str(ROOT / "build/verify/ber.json"))
    ap.add_argument("--package", default=None,
                    help="optional generated package; writes reports/ber.json and updates manifest")
    ap.add_argument("--chain", choices=("rx", "cdr", "frame"), default="frame")
    ap.add_argument("--bits", type=int, default=8000, help="RTL bits for the nominal BER point")
    ap.add_argument("--jtol-bits", type=int, default=4000, help="RTL bits per JTOL point")
    ap.add_argument("--eye-bits", type=int, default=8000)
    ap.add_argument("--ber-target", type=float, default=1e-3)
    ap.add_argument("--min-eye-ui", type=float, default=0.10)
    ap.add_argument("--jtol-min-amp-ui", type=float, default=0.10)
    ap.add_argument("--jtol-freqs", type=float, nargs="*",
                    default=[100_000.0, 500_000.0, 2_000_000.0])
    ap.add_argument("--jtol-amps", type=float, nargs="*",
                    default=[0.05, 0.10, 0.20, 0.40])
    ap.add_argument("--ppm", type=float, default=0.0)
    ap.add_argument("--channel-file", default=None,
                    help="Touchstone .s2p/.s4p channel override")
    ap.add_argument("--channel-port-map", default=None,
                    help="comma list, e.g. tx_p=0,tx_n=1,rx_p=2,rx_n=3")
    ap.add_argument("--analog-lut", default=None,
                    help="optional LUT from verification/analog/extract_lut.py; "
                         "replaces the profile channel tau with the TX rise-time tau")
    ap.add_argument("--tx-load-ff", type=float, default=50.0,
                    help="load used to interpolate the analog LUT (default 50 fF)")
    ap.add_argument("--skip-rtl", action="store_true",
                    help="behavioral-only report; capability is always false")
    args = ap.parse_args()

    report = build_report(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    capability = report["capability"]
    print(f"profile: {report['profile']}")
    print(f"checks: {json.dumps(report['checks'], sort_keys=True)}")
    print(f"validated rate: {capability['validated_rate_mbps']:.3f} Mb/s")
    print(f"RTL_LINK_SMOKE: {'PASS' if capability['rtl_link_smoke_pass'] else 'FAIL'}")
    for reason in capability["reasons"]:
        print(f"  - {reason}")
    print(f"wrote {out}")

    if args.package:
        package = Path(args.package)
        reports = package / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "ber.json").write_text(json.dumps(report, indent=2) + "\n")
        manifest_path = package / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            manifest["link_capability"] = capability
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            print(f"updated {manifest_path}")
        print(f"wrote {reports / 'ber.json'}")

    return 0 if capability["rtl_link_smoke_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

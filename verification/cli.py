"""Command-line entry points for the phase-1 SI/BER framework.

Run from the repository root, for example::

    /usr/bin/python3 -m verification.cli eye \
        --profile verification/profiles/stressed.yaml --out /tmp/opencode/eye.png

All commands use the non-interactive Agg matplotlib backend.  The ``jtol``
command's analytic mode is explicitly a Gaussian-tail / stressed-eye demo and
does **not** run RTL or a CDR.  ``--model rtl`` is a separate real-RTL path
(iverilog + ``cdr_adaptive``); the analytic results never claim to include that
CDR or its lock behavior.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # must precede pyplot imports anywhere in the process

import numpy as np
import yaml

from verification.analysis.bathtub import phase_scan
from verification.analysis.ber import gaussian_ber, required_bits_for_ber
from verification.analysis.eye import fold_eye, plot_eye
from verification.analysis.jtol import jtol_curve, run_jtol
from verification.channel.channel_impulse import RCReflectionChannel, nrz_waveform
from verification.channel.jitter import build_tie
from verification.patterns.prbs import prbs_bits

DEFAULT_PROFILE = Path(__file__).resolve().parent / "profiles" / "stressed.yaml"

#: Default analytic JTOL demo sweep.  These are deliberately modest and are
#: explicitly not a standards compliance grid.
ANALYTIC_FREQS_HZ = (100e3, 200e3, 400e3, 800e3, 1.6e6)
ANALYTIC_AMPS_UI = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35)


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ValueError(f"profile {where} is missing required key {key!r}")
    return mapping[key]


def _finite(name: str, value: Any, *, minimum: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"profile field {name} must be numeric; got {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"profile field {name} must be finite; got {out!r}")
    if minimum is not None and out < minimum:
        raise ValueError(f"profile field {name} must be >= {minimum}; got {out}")
    return out


def load_profile(path: str | Path) -> dict[str, Any]:
    """Load and validate one of the YAML profiles into a normalized dict."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"profile not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"profile {path} must contain a YAML mapping")
    where = str(path)

    rate_mbps = _finite("rate_mbps", _require(raw, "rate_mbps", where), minimum=0.0)
    if rate_mbps <= 0.0:
        raise ValueError("profile field rate_mbps must be > 0")
    rate_hz = rate_mbps * 1e6

    prbs_width = int(_require(raw, "prbs_width", where))
    if prbs_width not in (7, 15, 31):
        raise ValueError("profile field prbs_width must be 7, 15 or 31")
    n_bits = int(_require(raw, "n_bits", where))
    if n_bits <= 0:
        raise ValueError("profile field n_bits must be > 0")
    sps = int(_require(raw, "sps", where))
    if sps <= 0:
        raise ValueError("profile field sps must be > 0")

    rj = _finite("rj_rms_ui", _require(raw, "rj_rms_ui", where), minimum=0.0)
    sj_peak = _finite("sj_peak_ui", _require(raw, "sj_peak_ui", where), minimum=0.0)
    dcd = _finite("dcd_peak_ui", _require(raw, "dcd_peak_ui", where), minimum=0.0)
    noise = _finite("noise_rms_v", _require(raw, "noise_rms_v", where), minimum=0.0)
    threshold = _finite("threshold_v", raw.get("threshold_v", 0.0))

    if "sj_freq_per_ui" in raw:
        if "sj_freq_hz" in raw:
            raise ValueError("profile must not contain both sj_freq_hz and sj_freq_per_ui")
        sj_freq = _finite(
            "sj_freq_per_ui", raw["sj_freq_per_ui"], minimum=0.0
        ) * rate_hz
    elif "sj_freq_hz" in raw:
        sj_freq = _finite("sj_freq_hz", raw["sj_freq_hz"], minimum=0.0)
    else:
        raise ValueError("profile is missing required key 'sj_freq_hz' (or 'sj_freq_per_ui')")
    if sj_peak > 0.0 and sj_freq <= 0.0:
        raise ValueError("profile sj_freq_hz must be > 0 when sj_peak_ui is non-zero")

    channel_raw = _require(raw, "channel", where)
    if not isinstance(channel_raw, dict):
        raise ValueError("profile channel must be a mapping")
    tau_ui = _finite("channel.tau_ui", _require(channel_raw, "tau_ui", where), minimum=0.0)
    if tau_ui <= 0.0:
        raise ValueError("profile channel.tau_ui must be > 0")
    reflections_raw = _require(channel_raw, "reflections", where)
    reflections: list[tuple[int, float]] = []
    for item in reflections_raw:
        try:
            delay, coeff = item
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "each channel.reflections entry must be [delay_ui, coefficient]"
            ) from exc
        delay = int(delay)
        coeff = _finite("channel.reflections coefficient", coeff)
        if delay <= 0:
            raise ValueError("channel.reflections delay_ui must be > 0")
        reflections.append((delay, coeff))

    phases_raw = _require(raw, "phases", where)
    if not isinstance(phases_raw, dict):
        raise ValueError("profile phases must be a mapping")
    phase_start = _finite("phases.start", _require(phases_raw, "start", where))
    phase_stop = _finite("phases.stop", _require(phases_raw, "stop", where))
    phase_count = int(_require(phases_raw, "count", where))
    if phase_count < 1:
        raise ValueError("profile phases.count must be >= 1")
    if not (0.0 <= phase_start < 1.0 and 0.0 <= phase_stop < 1.0):
        raise ValueError("profile phases.start/stop must satisfy 0 <= value < 1")

    discard = int(raw.get("discard", 100))
    if discard < 0:
        raise ValueError("profile discard must be >= 0")
    if discard >= n_bits:
        raise ValueError("profile discard must be smaller than n_bits")
    seed = int(raw.get("seed", 0))

    return {
        "path": str(path),
        "rate_mbps": rate_mbps,
        "rate_hz": rate_hz,
        "ui_s": 1.0 / rate_hz,
        "prbs_width": prbs_width,
        "n_bits": n_bits,
        "sps": sps,
        "rj_rms_ui": rj,
        "sj_peak_ui": sj_peak,
        "sj_freq_hz": sj_freq,
        "dcd_peak_ui": dcd,
        "noise_rms_v": noise,
        "channel": {"tau_ui": tau_ui, "reflections": tuple(reflections)},
        "phases": {"start": phase_start, "stop": phase_stop, "count": phase_count},
        "threshold_v": threshold,
        "discard": discard,
        "seed": seed,
    }


def _profile_phases(profile: dict[str, Any]) -> np.ndarray:
    p = profile["phases"]
    return np.linspace(float(p["start"]), float(p["stop"]), int(p["count"]))


def _build_bits_and_channel(
    profile: dict[str, Any], n_bits: int
) -> tuple[np.ndarray, RCReflectionChannel]:
    bits = prbs_bits(profile["prbs_width"], n_bits)  # RTL reset seed: all ones
    channel = RCReflectionChannel(
        tau_ui=profile["channel"]["tau_ui"],
        reflections=profile["channel"]["reflections"],
        normalize=True,
    )
    return bits, channel


def _parse_port_map(text: str | None) -> dict[str, int] | None:
    if not text:
        return None
    out: dict[str, int] = {}
    for item in text.split(","):
        if not item.strip():
            continue
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"bad channel port map entry {item!r}; use name=index")
        out[key.strip()] = int(value)
    return out


def _build_record(
    profile: dict[str, Any],
    n_bits: int,
    *,
    add_noise: bool,
    channel_file: str | None = None,
    channel_port_map: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build bits -> TIE -> NRZ waveform -> channel for one profile."""
    bits, channel = _build_bits_and_channel(profile, n_bits)
    rng = np.random.default_rng(profile["seed"])
    tie, tie_meta = build_tie(
        bits,
        profile["ui_s"],
        profile["rj_rms_ui"],
        profile["sj_peak_ui"],
        profile["sj_freq_hz"],
        profile["dcd_peak_ui"],
        rng=rng,
    )
    waveform = nrz_waveform(bits, profile["sps"], tie_ui=tie / profile["ui_s"])
    if channel_file:
        from verification.channel.touchstone import load_touchstone_channel

        channel = load_touchstone_channel(
            channel_file,
            profile["sps"],
            profile["ui_s"],
            port_map=channel_port_map,
        )
        waveform = channel.apply(waveform)
    else:
        waveform = channel.apply(waveform, profile["sps"])
    if add_noise and profile["noise_rms_v"] > 0.0:
        waveform = waveform + rng.normal(
            0.0, profile["noise_rms_v"], size=waveform.shape
        )
    return {
        "bits": bits,
        "waveform": waveform,
        "tie": tie,
        "tie_meta": tie_meta,
        "channel": channel,
        "n_bits": n_bits,
    }


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    _ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False, allow_nan=False)
        handle.write("\n")


def _nan_to_none(values: Any) -> list[Any]:
    arr = np.asarray(values, dtype=np.float64)
    return [None if not np.isfinite(x) else float(x) for x in arr.reshape(-1)]


def _cmd_eye(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    n_bits = int(args.bits) if args.bits is not None else profile["n_bits"]
    if n_bits <= profile["discard"] + 1:
        raise ValueError("--bits must be greater than profile discard + 1")
    record = _build_record(
        profile, n_bits, add_noise=True,
        channel_file=args.channel_file,
        channel_port_map=_parse_port_map(args.channel_port_map),
    )
    sps = profile["sps"]
    folded = fold_eye(record["waveform"], sps, ui_window=2)

    out = Path(args.out)
    _ensure_parent(out)
    metrics_plot = plot_eye(
        folded,
        out,
        threshold_v=profile["threshold_v"],
        sps=sps,
        ui_s=profile["ui_s"],
        title=(
            f"{profile['prbs_width']}-bit PRBS, {profile['rate_mbps']:.3f} Mb/s, "
            f"2-UI eye"
        ),
    )

    ui_s = profile["ui_s"]
    sj_peak = profile["sj_peak_ui"]
    print(f"Profile: {profile['path']}")
    if args.channel_file:
        print(f"Channel: {args.channel_file} (Touchstone)")
    print(
        f"Rate: {profile['rate_mbps']:.3f} Mb/s; UI = {ui_s * 1e12:.4f} ps; "
        f"bits = {n_bits}; sps = {sps}; discard = {profile['discard']}"
    )
    print(
        f"SJ: peak = {sj_peak:.4f} UI ({sj_peak * ui_s * 1e12:.4f} ps); "
        f"p-p = {2.0 * sj_peak:.4f} UI ({2.0 * sj_peak * ui_s * 1e12:.4f} ps); "
        f"frequency = {profile['sj_freq_hz'] / 1e3:.3f} kHz"
    )
    print(
        f"Eye height: {metrics_plot['eye_height_v']:.6f} V at center; "
        f"eye width: {metrics_plot['eye_width_ui']:.6f} UI at "
        f"threshold {profile['threshold_v']:.6f} V"
    )
    print(
        "Metric definition: height = 0.5/99.5 inner-rail opening at the center; "
        "width = 0.5/99.5 threshold-crossing span (not a BER contour)."
    )
    print(f"Saved eye plot: {out}")
    return 0


def _cmd_bathtub(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    n_bits = int(args.bits) if args.bits is not None else profile["n_bits"]
    if n_bits <= profile["discard"] + 1:
        raise ValueError("--bits must be greater than profile discard + 1")
    record = _build_record(
        profile, n_bits, add_noise=True,
        channel_file=args.channel_file,
        channel_port_map=_parse_port_map(args.channel_port_map),
    )
    sps = profile["sps"]
    phases = _profile_phases(profile)
    scan = phase_scan(
        record["bits"],
        record["waveform"],
        sps,
        phases=phases,
        threshold_v=profile["threshold_v"],
        discard=profile["discard"],
    )

    out = Path(args.out)
    _ensure_parent(out)
    import matplotlib.pyplot as plt

    y = np.where(scan["zero_error"], scan["upper95"], scan["ber"])
    y = np.maximum(y, np.finfo(float).tiny)
    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    ax.semilogy(scan["phases"], y, color="#1f4e79", linewidth=1.2, marker=".", markersize=3)
    if np.any(scan["zero_error"]):
        ax.semilogy(
            scan["phases"][scan["zero_error"]],
            y[scan["zero_error"]],
            linestyle="none",
            marker="^",
            markersize=4,
            color="#548235",
            label="zero errors (plotted at 3/N upper bound)",
        )
    ax.axvline(scan["best_phase"], color="#c00000", linestyle="--", linewidth=1.0, label="best phase")
    ax.set_xlabel("Sampling phase (UI)")
    ax.set_ylabel("BER")
    ax.set_title(
        f"Empirical bathtub: {profile['rate_mbps']:.3f} Mb/s, "
        f"N = {scan['n_bits']} bits/phase"
    )
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)

    print(f"Profile: {profile['path']}")
    if args.channel_file:
        print(f"Channel: {args.channel_file} (Touchstone)")
    print(
        f"Rate: {profile['rate_mbps']:.3f} Mb/s; UI = {profile['ui_s'] * 1e12:.4f} ps; "
        f"bits = {n_bits}; sps = {sps}; discard = {profile['discard']}; "
        f"N = {scan['n_bits']} bits/phase"
    )
    print(
        f"Best phase: {scan['best_phase']:.6f} UI; errors = {scan['best_errors']}/"
        f"{scan['n_bits']}; empirical BER = {scan['best_ber']:.6e}"
    )
    if scan["best_errors"] == 0:
        print(
            "Zero-error 95% upper bound (rule of three, 3/N): "
            f"{scan['best_upper95']:.6e}"
        )
        zero_phases = scan["phases"][scan["zero_error"]]
        print(
            f"Zero-error phase span: {zero_phases.min():.6f} .. "
            f"{zero_phases.max():.6f} UI"
        )
    else:
        print(
            "Zero-error upper bound: not applicable (best phase has errors > 0); "
            "report empirical BER only."
        )
    print(f"Saved bathtub plot: {out}")

    if args.json:
        json_path = Path(args.json)
        payload = {
            "profile": profile["path"],
            "rate_mbps": profile["rate_mbps"],
            "ui_s": profile["ui_s"],
            "prbs_width": profile["prbs_width"],
            "n_bits_total": n_bits,
            "discard": profile["discard"],
            "n_bits": scan["n_bits"],
            "sps": sps,
            "threshold_v": profile["threshold_v"],
            "phases": scan["phases"].tolist(),
            "errors": scan["errors"].tolist(),
            "ber": scan["ber"].tolist(),
            "upper95": _nan_to_none(scan["upper95"]),
            "upper95_applicable": scan["upper95_applicable"].tolist(),
            "zero_error": scan["zero_error"].tolist(),
            "best_phase": scan["best_phase"],
            "best_errors": scan["best_errors"],
            "best_ber": scan["best_ber"],
            "best_upper95": (
                None if not np.isfinite(scan["best_upper95"]) else scan["best_upper95"]
            ),
            "rule_of_three_factor": 3.0,
            "zero_error_note": (
                "upper95 is 3/N only where errors == 0; it is an upper bound, "
                "not a measured BER. NaN/null where empirical errors > 0."
            ),
        }
        _write_json(json_path, payload)
        print(f"Saved bathtub JSON: {json_path}")
    return 0


def _cmd_jtol_rtl(args: argparse.Namespace) -> int:
    """Real RTL path: iverilog + cdr_adaptive + jittered edge schedule."""
    from verification.rtl.runner import run_rtl_jtol

    profile = load_profile(args.profile)
    # The RTL harness derives its clock period from the requested rate with
    # OVERSAMPLE=8, so the profile's line rate can be used directly.
    rtl_rate_mbps = float(profile["rate_mbps"])
    chain = getattr(args, "chain", "cdr")
    n_bits = int(args.bits) if args.bits is not None else 800
    if n_bits < 256:
        raise ValueError("--bits must be >= 256 for the RTL harness")
    freqs = tuple(float(x) for x in args.freqs) if args.freqs else (
        50_000.0, 100_000.0, 200_000.0, 500_000.0, 1_000_000.0, 2_000_000.0,
    )
    amps = tuple(float(x) for x in args.amps) if args.amps else (
        0.05, 0.10, 0.20, 0.40,
    )
    if any(a < 0.0 or not math.isfinite(a) for a in amps):
        raise ValueError("--amps must be finite and >= 0")

    channel_cfg = profile.get("channel", {}) or {}
    reflections = tuple(
        (float(pair[0]), float(pair[1])) for pair in channel_cfg.get("reflections", ())
    ) or ((1.0, 0.20), (2.0, -0.08))

    def simulate(stimulus: dict[str, Any]) -> dict[str, Any]:
        result = run_rtl_jtol(
            rate_mbps=rtl_rate_mbps,
            nbits=n_bits,
            rj_rms_ui=profile["rj_rms_ui"],
            sj_peak_ui=float(stimulus["sj_peak_ui"]),
            sj_freq_hz=float(stimulus["sj_freq_hz"]),
            dcd_peak_ui=profile["dcd_peak_ui"],
            ppm=args.ppm,
            prbs_width=profile["prbs_width"],
            seed=profile["seed"],
            chain=chain,
            noise_rms_v=float(profile.get("noise_rms_v", 0.0)) if chain == "rx" else 0.0,
            channel_tau_ui=float(channel_cfg.get("tau_ui", 0.16)),
            threshold_v=float(profile.get("threshold_v", 0.0)),
        )
        return {
            "ber": result.ber,
            "lock_losses": result.lock_losses,
            "bit_slips": result.bit_slips,
        }

    base_stress = {
        "model": "rtl-iverilog-cdr-adaptive",
        "cdr": {
            "cdr": "cdr_adaptive OVERSAMPLE=8",
            "rx": "serdes_rx_digital (CDR+sampler+deserializer)",
            "frame": "serdes_rx_digital ALIGN_MODE=2 (framed payload scoreboard)",
        }[chain],
        "chain": chain,
        "rate_mbps": rtl_rate_mbps,
        "n_bits": n_bits,
        "rj_rms_ui": profile["rj_rms_ui"],
        "dcd_peak_ui": profile["dcd_peak_ui"],
        "ppm": args.ppm,
    }
    rows = run_jtol(simulate, freqs, amps, base_stress)
    curve = jtol_curve(rows, ber_limit=args.ber_limit, lock_loss_limit=0)

    print(f"MODEL: real RTL {base_stress['cdr']} driven by a jittered edge schedule.")
    print(
        "This is the digital CDR state machine, not an analog/PEX model. "
        f"The harness clock is derived from {rtl_rate_mbps:.3f} Mb/s at "
        "OVERSAMPLE=8."
    )
    if chain == "rx":
        print(
            "RX chain mode: the edge schedule is the sliced analog waveform "
            f"(channel tau={channel_cfg.get('tau_ui', 0.16)} UI, "
            f"noise={profile.get('noise_rms_v', 0.0)} V rms, "
            f"threshold={profile.get('threshold_v', 0.0)} V)."
        )
    print(
        f"Profile: {profile['path']}; RJ={profile['rj_rms_ui']:.4f} UI rms; "
        f"DCD={profile['dcd_peak_ui']:.4f} UI; ppm={args.ppm:.1f}; "
        f"bits/point={n_bits}; BER limit={args.ber_limit:.3e}"
    )
    print(
        f"Sweep: {len(freqs)} frequencies x {len(amps)} peak amplitudes, "
        f"{len(rows)} points."
    )
    for point in curve:
        amp = point["max_passing_amplitude_ui"]
        if amp is None:
            print(
                f"  {point['sj_freq_hz']:>10.1f} Hz: no tested amplitude passed "
                f"({point['status']})"
            )
        else:
            print(
                f"  {point['sj_freq_hz']:>10.1f} Hz: {amp:.4f} UI peak "
                f"({point['max_passing_peak_to_peak_ui']:.4f} UI p-p), "
                f"status={point['status']}, BER={point['max_passing_ber']:.3e}"
            )

    out = Path(args.out)
    stem = out.with_suffix("") if out.suffix else out
    json_path = out if out.suffix.lower() == ".json" else stem.with_suffix(".json")
    csv_path = stem.with_suffix(".csv")
    png_path = stem.with_suffix(".png")
    payload = {
        "model": "rtl-iverilog-cdr-adaptive",
        "cdr": {
            "cdr": "cdr_adaptive OVERSAMPLE=8",
            "rx": "serdes_rx_digital (CDR+sampler+deserializer)",
            "frame": "serdes_rx_digital ALIGN_MODE=2 (framed payload scoreboard)",
        }[chain],
        "chain": chain,
        "disclaimer": (
            "Real digital CDR, but no analog TX/RX, no PEX and no channel "
            "S-parameters. The edge schedule carries RJ/SJ/DCD/ppm only."
        ),
        "profile": profile["path"],
        "rate_mbps": rtl_rate_mbps,
        "n_bits_per_point": n_bits,
        "rj_rms_ui": profile["rj_rms_ui"],
        "dcd_peak_ui": profile["dcd_peak_ui"],
        "ppm": args.ppm,
        "ber_limit": args.ber_limit,
        "lock_loss_limit": 0,
        "sweep": {
            "sj_frequencies_hz": [float(f) for f in freqs],
            "sj_amplitudes_ui": [float(a) for a in amps],
        },
        "rows": rows,
        "curve": curve,
        "curve_method": "last_passing_tested_amplitude",
    }
    _write_json(json_path, payload)
    _ensure_parent(csv_path)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sj_freq_hz", "sj_amplitude_ui", "sj_peak_to_peak_ui",
                "ber", "lock_losses", "bit_slips",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    import matplotlib.pyplot as plt

    curve_freqs = [p["sj_freq_hz"] for p in curve if p["max_passing_amplitude_ui"] is not None]
    curve_amps = [p["max_passing_amplitude_ui"] for p in curve if p["max_passing_amplitude_ui"] is not None]
    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    if curve_freqs:
        ax.semilogx(curve_freqs, curve_amps, marker="o", color="#1f4e79", linewidth=1.3)
    ax.set_xlabel("SJ frequency (Hz)")
    ax.set_ylabel("Max passing SJ peak amplitude (UI)")
    ax.set_title(f"RTL {base_stress['cdr']} JTOL ({rtl_rate_mbps:.1f} Mb/s)")
    ax.grid(True, which="both", alpha=0.25)
    fig.tight_layout()
    _ensure_parent(png_path)
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"Saved JTOL JSON: {json_path}")
    print(f"Saved JTOL CSV:  {csv_path}")
    print(f"Saved JTOL plot: {png_path}")
    return 0


def _cmd_jtol(args: argparse.Namespace) -> int:
    if args.model == "rtl":
        return _cmd_jtol_rtl(args)

    profile = load_profile(args.profile)
    n_bits = int(args.bits) if args.bits is not None else profile["n_bits"]
    if n_bits <= profile["discard"] + 1:
        raise ValueError("--bits must be greater than profile discard + 1")
    bits, channel = _build_bits_and_channel(profile, n_bits)
    sps = profile["sps"]
    phase = float(args.phase)
    if not 0.0 <= phase < 1.0:
        raise ValueError("--phase must satisfy 0 <= phase < 1")

    freqs = tuple(float(x) for x in args.freqs) if args.freqs else ANALYTIC_FREQS_HZ
    amps = tuple(float(x) for x in args.amps) if args.amps else ANALYTIC_AMPS_UI
    if any(f < 0.0 or not math.isfinite(f) for f in freqs):
        raise ValueError("--freqs must be finite and >= 0")
    if any(a < 0.0 or not math.isfinite(a) for a in amps):
        raise ValueError("--amps must be finite and >= 0")

    def simulate(stimulus: dict[str, Any]) -> dict[str, Any]:
        # Fresh seeded RNG per point makes the RJ/noise realization repeatable
        # across the sweep; only the SJ amplitude/frequency changes.
        rng = np.random.default_rng(profile["seed"])
        tie, _ = build_tie(
            bits,
            profile["ui_s"],
            profile["rj_rms_ui"],
            float(stimulus["sj_peak_ui"]),
            float(stimulus["sj_freq_hz"]),
            profile["dcd_peak_ui"],
            rng=rng,
        )
        waveform = nrz_waveform(bits, sps, tie_ui=tie / profile["ui_s"])
        waveform = channel.apply(waveform, sps)
        stats = gaussian_ber(
            waveform,
            bits,
            sps,
            phases=[phase],
            threshold_v=profile["threshold_v"],
            noise_rms_v=profile["noise_rms_v"],
            discard=profile["discard"],
        )
        # No CDR is present in phase 1: lock_losses/bit_slips are not
        # observable and are reported as zero placeholders, never as a lock
        # result.  The analytic model cannot produce a lock loss.
        return {
            "ber": float(stats["ber"][0]),
            "lock_losses": 0,
            "bit_slips": 0,
        }

    base_stress = {
        "rate_mbps": profile["rate_mbps"],
        "ui_s": profile["ui_s"],
        "prbs_width": profile["prbs_width"],
        "n_bits": n_bits,
        "sps": sps,
        "rj_rms_ui": profile["rj_rms_ui"],
        "dcd_peak_ui": profile["dcd_peak_ui"],
        "noise_rms_v": profile["noise_rms_v"],
        "threshold_v": profile["threshold_v"],
        "sample_phase_ui": phase,
        "model": "analytic-gaussian-tail",
        "cdr": "none",
    }
    rows = run_jtol(simulate, freqs, amps, base_stress)
    curve = jtol_curve(rows, ber_limit=args.ber_limit, lock_loss_limit=0)

    print("MODEL: analytic Gaussian-tail stressed-eye demo.")
    print(
        "WARNING: no RTL, no CDR, no phase tracking and no lock model are used. "
        "lock_losses and bit_slips are zero placeholders; this is not an RTL "
        "JTOL compliance result. A separate RTL path is available via --model rtl."
    )
    print(
        f"Profile: {profile['path']}; rate = {profile['rate_mbps']:.3f} Mb/s; "
        f"bits/point = {n_bits}; sps = {sps}; sample phase = {phase:.4f} UI; "
        f"BER limit = {args.ber_limit:.3e}"
    )
    print(
        f"Sweep: {len(freqs)} frequencies x {len(amps)} peak amplitudes "
        f"({amps[0]:.3f} .. {amps[-1]:.3f} UI), {len(rows)} points."
    )
    print(
        "JTOL curve (max passing SJ peak amplitude per frequency; demo BER limit "
        f"{args.ber_limit:.3e}):"
    )
    for point in curve:
        amp = point["max_passing_amplitude_ui"]
        pp = point["max_passing_peak_to_peak_ui"]
        if amp is None:
            print(
                f"  {point['sj_freq_hz']:>10.1f} Hz: no tested amplitude passed "
                f"({point['status']})"
            )
        else:
            print(
                f"  {point['sj_freq_hz']:>10.1f} Hz: {amp:.4f} UI peak "
                f"({pp:.4f} UI p-p), status={point['status']}, "
                f"BER={point['max_passing_ber']:.3e}"
            )
    print(
        "Note: 'last passing' uses the largest tested amplitude that met the "
        "BER/lock limits; no BER-vs-amplitude interpolation is performed."
    )
    print(
        "Note: with a fixed-phase sampler there is no CDR loop bandwidth or "
        "jitter peaking, so the frequency dependence of this demo is weak by "
        "construction. It is not a substitute for a measured CDR JTOL curve."
    )

    out = Path(args.out)
    if out.suffix.lower() == ".csv":
        csv_path = out
        stem = out.with_suffix("")
    elif out.suffix.lower() == ".json":
        json_path = out
        stem = out.with_suffix("")
        csv_path = stem.with_suffix(".csv")
    else:
        stem = out
        json_path = out.with_suffix(".json")
        csv_path = out.with_suffix(".csv")
    if out.suffix.lower() != ".json":
        json_path = stem.with_suffix(".json")
    png_path = stem.with_suffix(".png")

    payload = {
        "model": "analytic-gaussian-tail-stressed-eye",
        "cdr": "none",
        "disclaimer": (
            "No RTL and no CDR are simulated. The fixed-phase slicer cannot "
            "track SJ; lock_losses/bit_slips are zero placeholders. Not a "
            "standards-compliant JTOL result. Phase 2: cocotb + RTL CDR."
        ),
        "profile": profile["path"],
        "rate_mbps": profile["rate_mbps"],
        "ui_s": profile["ui_s"],
        "prbs_width": profile["prbs_width"],
        "n_bits_per_point": n_bits,
        "sps": sps,
        "sample_phase_ui": phase,
        "ber_limit": args.ber_limit,
        "lock_loss_limit": 0,
        "sweep": {
            "sj_frequencies_hz": [float(f) for f in freqs],
            "sj_amplitudes_ui": [float(a) for a in amps],
        },
        "rows": rows,
        "curve": curve,
        "curve_method": "last_passing_tested_amplitude",
    }
    _write_json(json_path, payload)
    _ensure_parent(csv_path)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sj_freq_hz",
                "sj_amplitude_ui",
                "sj_peak_to_peak_ui",
                "ber",
                "lock_losses",
                "bit_slips",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    import matplotlib.pyplot as plt

    curve_freqs = [p["sj_freq_hz"] for p in curve if p["max_passing_amplitude_ui"] is not None]
    curve_amps = [
        p["max_passing_amplitude_ui"]
        for p in curve
        if p["max_passing_amplitude_ui"] is not None
    ]
    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    if curve_freqs:
        ax.semilogx(curve_freqs, curve_amps, marker="o", color="#1f4e79", linewidth=1.3)
    ax.set_xlabel("SJ frequency (Hz)")
    ax.set_ylabel("Max passing SJ peak amplitude (UI)")
    ax.set_title("ANALYTIC Gaussian-tail stressed-eye demo; no RTL/CDR")
    ax.grid(True, which="both", alpha=0.25)
    ax.text(
        0.01,
        0.01,
        "Fixed-phase sampler, no CDR lock/tracking. Not a compliance JTOL curve.",
        transform=ax.transAxes,
        fontsize=7.5,
        va="bottom",
        ha="left",
        bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
    )
    fig.tight_layout()
    fig.savefig(png_path, dpi=130)
    plt.close(fig)

    print(f"Saved JTOL JSON: {json_path}")
    print(f"Saved JTOL CSV:  {csv_path}")
    print(f"Saved JTOL plot: {png_path}")
    print(
        f"For a 1e-12 BER target the zero-error rule of three needs "
        f"{required_bits_for_ber(1e-12):.3e} bits; this demo uses {n_bits} "
        "bits per point, statistical (not empirical) BER, and a demo limit of "
        f"{args.ber_limit:.3e}."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m verification.cli",
        description=(
            "Phase-1 behavioral NRZ SI/BER tools. Fixed-phase slicer only: "
            "this is not a CDR."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    eye = sub.add_parser("eye", help="fold and plot a 2-UI stressed eye")
    eye.add_argument("--profile", default=str(DEFAULT_PROFILE), help="YAML profile path")
    eye.add_argument("--out", required=True, help="output PNG path")
    eye.add_argument("--bits", type=int, default=None, help="override profile n_bits")
    eye.add_argument("--channel-file", default=None,
                     help="Touchstone .s2p/.s4p channel override")
    eye.add_argument("--channel-port-map", default=None,
                     help="comma list, e.g. tx_p=0,tx_n=1,rx_p=2,rx_n=3")
    eye.set_defaults(func=_cmd_eye)

    tub = sub.add_parser("bathtub", help="empirical BER vs fixed sampling phase")
    tub.add_argument("--profile", default=str(DEFAULT_PROFILE), help="YAML profile path")
    tub.add_argument("--out", required=True, help="output PNG path")
    tub.add_argument("--json", default=None, help="optional JSON results path")
    tub.add_argument("--bits", type=int, default=None, help="override profile n_bits")
    tub.add_argument("--channel-file", default=None,
                     help="Touchstone .s2p/.s4p channel override")
    tub.add_argument("--channel-port-map", default=None,
                     help="comma list, e.g. tx_p=0,tx_n=1,rx_p=2,rx_n=3")
    tub.set_defaults(func=_cmd_bathtub)

    jtol = sub.add_parser("jtol", help="JTOL sweep harness / analytic demo")
    jtol.add_argument("--profile", default=str(DEFAULT_PROFILE), help="YAML profile path")
    jtol.add_argument(
        "--out",
        required=True,
        help="output JSON (or CSV) path; the sibling CSV and PNG are also written",
    )
    jtol.add_argument(
        "--model",
        choices=("analytic", "rtl"),
        default="analytic",
        help="analytic Gaussian-tail demo (phase 1) or the phase-2 RTL hook",
    )
    jtol.add_argument(
        "--chain",
        choices=("cdr", "rx", "frame"),
        default="cdr",
        help=(
            "RTL path only: cdr = drive cdr_adaptive directly; "
            "rx = full serdes_rx_digital with the sliced analog waveform; "
            "frame = comma-framed RX, scoreboard on rx_par_data/frame_valid"
        ),
    )
    jtol.add_argument("--bits", type=int, default=None, help="override profile n_bits")
    jtol.add_argument(
        "--ber-limit",
        type=float,
        default=1e-3,
        help=(
            "passing BER limit for the analytic demo (default 1e-3). The "
            "jtol_curve() API default remains 1e-12; a 1e-12 limit is not "
            "informative for this simplified fixed-phase channel's "
            "deterministic ISI and is not a standards target."
        ),
    )
    jtol.add_argument(
        "--phase",
        type=float,
        default=0.5,
        help="fixed sampling phase in UI for the analytic demo (default 0.5)",
    )
    jtol.add_argument(
        "--ppm",
        type=float,
        default=0.0,
        help="TX/RX frequency offset in ppm for --model rtl (default 0)",
    )
    jtol.add_argument(
        "--freqs",
        type=float,
        nargs="*",
        default=None,
        help=f"SJ frequencies in Hz (default {list(ANALYTIC_FREQS_HZ)})",
    )
    jtol.add_argument(
        "--amps",
        type=float,
        nargs="*",
        default=None,
        help=f"SJ peak amplitudes in UI (default {list(ANALYTIC_AMPS_UI)})",
    )
    jtol.set_defaults(func=_cmd_jtol)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, TypeError, KeyError, FileNotFoundError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

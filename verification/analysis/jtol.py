"""Generic JTOL (jitter tolerance) sweep driver.

This module is a **harness interface**, not a CDR.  Phase 1 contains no RTL and
no CDR loop.  The caller supplies ``simulate(stimulus) -> result``; that callable
is the hook where a cocotb/RTL testbench (or a higher-fidelity model) can be
plugged in later.  The driver only enumerates SJ frequency/amplitude points,
records the returned BER/lock-loss/bit-slip metrics and reduces them to a
maximum passing amplitude per frequency.

A passing point is defined here as ``ber <= ber_limit`` **and**
``lock_losses <= lock_loss_limit``.  Bit slips are recorded but are not part of
that default pass criterion; callers that need a slip-free requirement should
filter or post-process the returned rows accordingly.  ``jtol_curve`` uses the
"last passing" amplitude (the largest tested amplitude that passed), not an
interpolated BER crossing.  That is conservative relative to the tested grid and
avoids inventing a BER-vs-amplitude interpolation law.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

__all__ = ["run_jtol", "jtol_curve"]


def run_jtol(
    simulate: Callable[[dict[str, Any]], Mapping[str, Any]],
    sj_frequencies_hz: Sequence[float],
    sj_amplitudes_ui: Sequence[float],
    base_stress: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a full SJ frequency x amplitude sweep through ``simulate``.

    Parameters
    ----------
    simulate:
        Callable taking one stimulus ``dict`` and returning a mapping with at
        least ``ber``, ``lock_losses`` and ``bit_slips``.  This is where an RTL
        CDR testbench or an analytic model is attached.
    sj_frequencies_hz:
        Sinusoidal-jitter frequencies in Hz.
    sj_amplitudes_ui:
        Sinusoidal-jitter **peak** amplitudes in UI.  The p-p amplitude
        (``2 * amplitude``) is also placed in the stimulus and the row.
    base_stress:
        Optional constant settings merged into every stimulus (for example
        rate, PRBS width, RJ, DCD, noise, threshold, channel).

    Returns
    -------
    list of dict
        One JSON-serializable row per (frequency, amplitude), in frequency-major,
        amplitude-minor order.  Each stimulus contains ``sj_freq_hz``,
        ``sj_peak_ui``, ``sj_peak_to_peak_ui`` and ``sj_amplitude_ui`` in
        addition to the base keys.
    """
    if not callable(simulate):
        raise TypeError("simulate must be callable")
    freqs = np.asarray(sj_frequencies_hz, dtype=np.float64).reshape(-1)
    amps = np.asarray(sj_amplitudes_ui, dtype=np.float64).reshape(-1)
    if freqs.size == 0:
        raise ValueError("sj_frequencies_hz must be non-empty")
    if amps.size == 0:
        raise ValueError("sj_amplitudes_ui must be non-empty")
    if not np.all(np.isfinite(freqs)) or np.any(freqs < 0.0):
        raise ValueError("sj_frequencies_hz must be finite and >= 0")
    if not np.all(np.isfinite(amps)) or np.any(amps < 0.0):
        raise ValueError("sj_amplitudes_ui must be finite and >= 0")
    base = dict(base_stress) if base_stress is not None else {}

    rows: list[dict[str, Any]] = []
    for freq in freqs:
        for amp in amps:
            stimulus = dict(base)
            stimulus["sj_freq_hz"] = float(freq)
            stimulus["sj_peak_ui"] = float(amp)
            stimulus["sj_peak_to_peak_ui"] = float(2.0 * amp)
            stimulus["sj_amplitude_ui"] = float(amp)
            result = simulate(stimulus)
            if not isinstance(result, Mapping):
                raise TypeError(
                    "simulate() must return a mapping with keys "
                    f"'ber', 'lock_losses', 'bit_slips'; got {type(result).__name__}"
                )
            missing = {"ber", "lock_losses", "bit_slips"} - set(result.keys())
            if missing:
                raise KeyError(f"simulate() result is missing keys: {sorted(missing)}")
            try:
                ber = float(result["ber"])
                lock_losses = int(result["lock_losses"])
                bit_slips = int(result["bit_slips"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "simulate() returned non-numeric ber/lock_losses/bit_slips"
                ) from exc
            if not np.isfinite(ber) or ber < 0.0 or ber > 1.0:
                raise ValueError(f"simulate() returned invalid ber: {ber!r}")
            if lock_losses < 0 or bit_slips < 0:
                raise ValueError(
                    "simulate() returned negative lock_losses or bit_slips: "
                    f"{lock_losses}, {bit_slips}"
                )
            rows.append(
                {
                    "sj_freq_hz": float(freq),
                    "sj_amplitude_ui": float(amp),
                    "sj_peak_to_peak_ui": float(2.0 * amp),
                    "ber": ber,
                    "lock_losses": lock_losses,
                    "bit_slips": bit_slips,
                }
            )
    return rows


def jtol_curve(
    rows: Sequence[Mapping[str, Any]],
    ber_limit: float = 1e-12,
    lock_loss_limit: int = 0,
) -> list[dict[str, Any]]:
    """Reduce sweep rows to the maximum passing SJ amplitude per frequency.

    For each frequency the tested amplitudes are sorted and the largest one
    satisfying both limits is reported.  If no tested point passes,
    ``max_passing_amplitude_ui`` is ``None`` and ``status`` is
    ``"no_pass"``.  If every tested point passes, ``status`` is
    ``"all_tested_pass"``.  No amplitude interpolation is performed.
    """
    if not rows:
        return []
    ber_limit = float(ber_limit)
    lock_loss_limit = int(lock_loss_limit)
    if not np.isfinite(ber_limit) or ber_limit < 0.0:
        raise ValueError(f"ber_limit must be finite and >= 0; got {ber_limit}")
    if lock_loss_limit < 0:
        raise ValueError(f"lock_loss_limit must be >= 0; got {lock_loss_limit}")

    grouped: dict[float, list[tuple[float, bool, float, int]]] = {}
    for idx, row in enumerate(rows):
        try:
            freq = float(row["sj_freq_hz"])
            amp_raw = row["sj_amplitude_ui"] if "sj_amplitude_ui" in row else row["sj_peak_ui"]
            amp = float(amp_raw)
            ber = float(row["ber"])
            lock_losses = int(row["lock_losses"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"row {idx} is missing/invalid JTOL fields") from exc
        if not np.isfinite(freq) or not np.isfinite(amp) or not np.isfinite(ber):
            raise ValueError(f"row {idx} contains non-finite values")
        passed = (ber <= ber_limit) and (lock_losses <= lock_loss_limit)
        grouped.setdefault(freq, []).append((amp, passed, ber, lock_losses))

    curve: list[dict[str, Any]] = []
    for freq in sorted(grouped):
        points = sorted(grouped[freq], key=lambda item: item[0])
        passing = [(amp, ber, lock) for amp, ok, ber, lock in points if ok]
        failing_amps = [amp for amp, ok, _ber, _lock in points if not ok]
        tested_max = points[-1][0]
        if passing:
            max_amp, max_ber, max_lock = max(passing, key=lambda item: item[0])
            if len(passing) == len(points):
                status = "all_tested_pass"
            else:
                status = "last_passing"
            first_failing = min(
                (amp for amp in failing_amps if amp > max_amp), default=None
            )
        else:
            max_amp = None
            max_ber = None
            max_lock = None
            status = "no_pass"
            first_failing = min(failing_amps) if failing_amps else None
        curve.append(
            {
                "sj_freq_hz": float(freq),
                "max_passing_amplitude_ui": None if max_amp is None else float(max_amp),
                "max_passing_peak_to_peak_ui": (
                    None if max_amp is None else float(2.0 * max_amp)
                ),
                # Alias for consumers that use the shorter field name.
                "max_amplitude_ui": None if max_amp is None else float(max_amp),
                "max_passing_ber": None if max_ber is None else float(max_ber),
                "max_passing_lock_losses": None if max_lock is None else int(max_lock),
                "tested_max_amplitude_ui": float(tested_max),
                "first_failing_amplitude_ui": (
                    None if first_failing is None else float(first_failing)
                ),
                "status": status,
                "n_points": len(points),
            }
        )
    return curve

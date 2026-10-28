"""Empirical bathtub scans over sampling phase and threshold.

Zero-error interpretation
-------------------------
An empirical scan can only say "no errors were observed in N bits".  When the
observed error count is zero, this module reports the 95 % rule-of-three upper
bound ``3 / N``.  That is **not** a measured BER of zero and it is not a claim
that the true BER is below a tighter limit; it is the conventional approximate
95 % upper confidence bound for a zero-count Poisson/binomial observation.  For
rows with one or more errors the empirical ``errors / N`` is reported, the
upper-bound field is ``NaN`` and ``upper95_applicable`` is ``False``; a proper
binomial/Wilson interval should be used instead (see
:func:`verification.analysis.ber.confidence_interval`).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..channel.rx_slicer import sample_and_decide

__all__ = ["phase_scan", "threshold_scan"]

_DEFAULT_PHASES = np.linspace(0.01, 0.99, 81)


def _validate_bits_waveform(bits: Any, waveform: Any, sps: int, discard: int) -> tuple[np.ndarray, np.ndarray, int, int]:
    bits_arr = np.asarray(bits).reshape(-1)
    if bits_arr.size == 0:
        raise ValueError("bits must be non-empty")
    if not np.all((bits_arr == 0) | (bits_arr == 1)):
        raise ValueError("bits must contain only 0/1 values")
    wave = np.asarray(waveform, dtype=np.float64).reshape(-1)
    if wave.size == 0:
        raise ValueError("waveform must be non-empty")
    sps = int(sps)
    if sps <= 0:
        raise ValueError(f"sps must be >= 1; got {sps}")
    discard = int(discard)
    if discard < 0:
        raise ValueError(f"discard must be >= 0; got {discard}")
    if discard >= bits_arr.size:
        raise ValueError(
            f"discard={discard} leaves no bits (bits length {bits_arr.size})"
        )
    return bits_arr.astype(np.int8, copy=False), wave, sps, discard


def _validate_phases(phases: Any) -> np.ndarray:
    arr = np.asarray(phases, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        raise ValueError("phases must be non-empty")
    if not np.all(np.isfinite(arr)) or np.any(arr < 0.0) or np.any(arr >= 1.0):
        raise ValueError("all phases must be finite and satisfy 0 <= phase < 1")
    return arr


def phase_scan(
    bits: Any,
    waveform: Any,
    sps: int,
    phases: Any = _DEFAULT_PHASES,
    threshold_v: float = 0.0,
    discard: int = 100,
) -> dict[str, Any]:
    """Scan empirical BER versus fixed sampling phase.

    Sampling positions are ``floor((n + phase) * sps)`` as implemented by
    :func:`verification.channel.rx_slicer.sample_and_decide`.  The first
    ``discard`` bits are omitted from every phase.

    Returns
    -------
    dict
        ``phases``, ``errors``, ``ber``, scalar ``n_bits``, ``upper95``
        (``3/N`` where errors == 0 else ``NaN``), ``zero_error`` mask,
        ``upper95_applicable`` mask, plus best-phase summary fields.  All arrays
        are numpy arrays and therefore not directly JSON-serializable; the CLI
        converts them for output.
    """
    bits_arr, wave, sps, discard = _validate_bits_waveform(bits, waveform, sps, discard)
    phases_arr = _validate_phases(phases)
    threshold_v = float(threshold_v)
    if not np.isfinite(threshold_v):
        raise ValueError("threshold_v must be finite")

    n_bits = bits_arr.size - discard
    errors = np.zeros(phases_arr.size, dtype=np.int64)
    truth = bits_arr[discard:]
    for i, phase in enumerate(phases_arr):
        decisions, _ = sample_and_decide(
            wave,
            bits_arr,
            sps,
            float(phase),
            threshold_v=threshold_v,
            discard=discard,
        )
        errors[i] = int(np.count_nonzero(decisions != truth))

    ber = errors.astype(np.float64) / float(n_bits)
    zero_error = errors == 0
    upper95 = np.full(phases_arr.size, np.nan, dtype=np.float64)
    upper95[zero_error] = 3.0 / float(n_bits)

    best_idx = int(np.argmin(errors))
    return {
        "phases": phases_arr,
        "errors": errors,
        "ber": ber,
        "n_bits": int(n_bits),
        "upper95": upper95,
        "upper95_applicable": zero_error.copy(),
        "zero_error": zero_error,
        "best_index": best_idx,
        "best_phase": float(phases_arr[best_idx]),
        "best_errors": int(errors[best_idx]),
        "best_ber": float(ber[best_idx]),
        "best_upper95": float(upper95[best_idx]),
        "threshold_v": threshold_v,
        "discard": discard,
        "rule_of_three_factor": 3.0,
    }


def threshold_scan(
    bits: Any,
    waveform: Any,
    sps: int,
    phases: Any = _DEFAULT_PHASES,
    thresholds: Any | None = None,
    discard: int = 100,
) -> dict[str, Any]:
    """Scan empirical BER on a phase x threshold grid.

    Parameters
    ----------
    thresholds:
        Decision thresholds in volts.  Defaults to 41 points spanning
        ``-0.5 .. +0.5 V`` (the framework's default NRZ rails are -0.5/+0.5 V).

    Returns
    -------
    dict
        ``ber``, ``errors``, ``upper95`` and ``zero_error`` have shape
        ``(n_phases, n_thresholds)``.  ``upper95`` is ``3/N`` only where no
        errors were observed and ``NaN`` elsewhere.  Scalar ``n_bits`` is
        common to every cell.
    """
    bits_arr, wave, sps, discard = _validate_bits_waveform(bits, waveform, sps, discard)
    phases_arr = _validate_phases(phases)
    if thresholds is None:
        thresholds_arr = np.linspace(-0.5, 0.5, 41, dtype=np.float64)
    else:
        thresholds_arr = np.asarray(thresholds, dtype=np.float64).reshape(-1)
        if thresholds_arr.size == 0:
            raise ValueError("thresholds must be non-empty")
        if not np.all(np.isfinite(thresholds_arr)):
            raise ValueError("thresholds must be finite")

    n_bits = bits_arr.size - discard
    truth = bits_arr[discard:]
    errors = np.zeros((phases_arr.size, thresholds_arr.size), dtype=np.int64)
    for i, phase in enumerate(phases_arr):
        for j, threshold in enumerate(thresholds_arr):
            decisions, _ = sample_and_decide(
                wave,
                bits_arr,
                sps,
                float(phase),
                threshold_v=float(threshold),
                discard=discard,
            )
            errors[i, j] = int(np.count_nonzero(decisions != truth))

    ber = errors.astype(np.float64) / float(n_bits)
    zero_error = errors == 0
    upper95 = np.full(errors.shape, np.nan, dtype=np.float64)
    upper95[zero_error] = 3.0 / float(n_bits)
    return {
        "phases": phases_arr,
        "thresholds": thresholds_arr,
        "errors": errors,
        "ber": ber,
        "n_bits": int(n_bits),
        "upper95": upper95,
        "upper95_applicable": zero_error.copy(),
        "zero_error": zero_error,
        "discard": discard,
        "rule_of_three_factor": 3.0,
    }

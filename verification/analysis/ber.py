"""Statistical Gaussian-tail BER prediction and binomial confidence helpers.

Model and explicit assumptions
-----------------------------
For a fixed sampling phase and threshold, sampled signal values are partitioned
by the **known** transmitted bit (this is a diagnostic, not a blind receiver).
For each rail the mean ``mu`` and standard deviation ``sigma`` are estimated
from those samples.  An independent Gaussian noise voltage ``noise_rms_v`` is
added in quadrature to the sampled-rail standard deviation, then::

    p(error | transmitted 1) = Q((mu1 - threshold) / sigma1)
    p(error | transmitted 0) = Q((threshold - mu0) / sigma0)
    BER = 0.5 * p(error | 1) + 0.5 * p(error | 0)

The equal-prior factor ``0.5`` is a convention, not an estimate of the pattern's
actual ones density.  The per-rail standard deviation is estimated **across the
record** from the sampled waveform slices, so it includes deterministic
ISI/jitter-induced amplitude spread as well as the independent
``noise_rms_v`` term added in quadrature.  A rail that is strongly bimodal or
data-dependent will therefore not be Gaussian even if the thermal noise is; that
is one reason this prediction is a model, not a measurement.

This is an **extrapolation** from a finite, deterministic set of sampled values
plus an assumed Gaussian noise.  It is valid only while (a) the residual
amplitude distribution around each rail is approximately Gaussian and
independent sample-to-sample, and (b) the decision is a fixed threshold.  It
must not be extrapolated through a nonlinear CDR, DFE, MLSE, adaptive
equalizer, AGC or any other decision-directed loop: those blocks change the
noise/enhancement and error propagation in ways this formula cannot represent.
It also says nothing about lock loss or bit slips.  Treat predictions far below
the empirical floor as an assumption-driven model output, never as measured
silicon data.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.stats import norm

from ..channel.rx_slicer import q_function

__all__ = [
    "confidence_interval",
    "gaussian_ber",
    "required_bits_for_ber",
    "statistical_ber",
]

_DEFAULT_PHASES = np.linspace(0.01, 0.99, 81)


def _sample_values(
    waveform: np.ndarray,
    bits: np.ndarray,
    sps: int,
    phase_ui: float,
    discard: int,
) -> tuple[np.ndarray, np.ndarray]:
    n = np.arange(discard, bits.size, dtype=np.int64)
    indices = np.floor((n.astype(np.float64) + phase_ui) * sps).astype(np.int64)
    np.clip(indices, 0, waveform.size - 1, out=indices)
    return waveform[indices], bits[discard:]


def _rail_stats(
    values: np.ndarray, truth: np.ndarray, noise_rms_v: float
) -> tuple[float, float, float, float, int, int]:
    ones = values[truth == 1]
    zeros = values[truth == 0]
    if ones.size == 0 or zeros.size == 0:
        raise ValueError(
            "gaussian_ber requires both 0 and 1 bits after discard; got "
            f"{zeros.size} zeros and {ones.size} ones"
        )
    mu1 = float(np.mean(ones))
    mu0 = float(np.mean(zeros))
    # ddof=1 when possible; a single sample has no observable spread.
    sigma1_ch = float(np.std(ones, ddof=1)) if ones.size > 1 else 0.0
    sigma0_ch = float(np.std(zeros, ddof=1)) if zeros.size > 1 else 0.0
    sigma1 = float(np.sqrt(sigma1_ch**2 + noise_rms_v**2))
    sigma0 = float(np.sqrt(sigma0_ch**2 + noise_rms_v**2))
    return mu0, sigma0, mu1, sigma1, int(zeros.size), int(ones.size)


def _tail_probability(mu: float, sigma: float, threshold: float, side: str) -> float:
    """Q-tail for one rail; handles the degenerate sigma == 0 case."""
    if sigma > 0.0:
        if side == "1":
            return float(q_function((mu - threshold) / sigma))
        return float(q_function((threshold - mu) / sigma))
    # No spread: the result is deterministic except exactly at the threshold,
    # where the zero-noise limit is convention-dependent.  Use 0.5 there.
    if mu == threshold:
        return 0.5
    if side == "1":
        return 1.0 if mu < threshold else 0.0
    return 1.0 if mu > threshold else 0.0


def gaussian_ber(
    waveform: Any,
    bits_truth: Any,
    sps: int,
    phases: Any = _DEFAULT_PHASES,
    threshold_v: float = 0.0,
    noise_rms_v: float = 0.0,
    discard: int = 100,
    thresholds: Any | None = None,
) -> dict[str, Any]:
    """Gaussian-tail BER over sampling phases (and optionally thresholds).

    Parameters
    ----------
    waveform:
        Sampled waveform (noise-free, ideally; pass ``noise_rms_v`` instead of
        pre-adding noise so the quadrature model is meaningful).
    bits_truth:
        Serial 0/1 reference bits.  Used to classify the sampled rails.
    sps:
        Samples per UI.
    phases:
        Sampling phases in UI.  Default is 81 points over ``0.01 .. 0.99``.
    threshold_v:
        Single decision threshold (volts).  Ignored when ``thresholds`` is
        supplied.
    noise_rms_v:
        Independent Gaussian noise RMS (volts) added in quadrature to each
        rail's sampled standard deviation.
    discard:
        Number of leading bits omitted to remove channel startup.
    thresholds:
        Optional array of thresholds (volts) for a phase x threshold grid.

    Returns
    -------
    dict
        ``ber`` has shape ``(n_phases,)`` for a scalar threshold and
        ``(n_phases, n_thresholds)`` when ``thresholds`` is supplied.
        ``mu0/mu1`` and ``sigma0/sigma1`` are per phase; the ``*_channel_v``
        sigmas are before adding the independent noise.  ``n0/n1`` are the
        counts of classified samples per phase (constant across thresholds).
    """
    waveform = np.asarray(waveform, dtype=np.float64).reshape(-1)
    bits = np.asarray(bits_truth).reshape(-1)
    if bits.size == 0:
        raise ValueError("bits_truth must be non-empty")
    if not np.all((bits == 0) | (bits == 1)):
        raise ValueError("bits_truth must contain only 0/1 values")
    bits = bits.astype(np.int8, copy=False)
    sps = int(sps)
    if sps <= 0:
        raise ValueError(f"sps must be an integer >= 1; got {sps}")
    if waveform.size == 0:
        raise ValueError("waveform must be non-empty")
    phases_arr = np.asarray(phases, dtype=np.float64).reshape(-1)
    if phases_arr.size == 0:
        raise ValueError("phases must be non-empty")
    if not np.all(np.isfinite(phases_arr)) or np.any(phases_arr < 0.0) or np.any(phases_arr >= 1.0):
        raise ValueError("all phases must be finite and satisfy 0 <= phase < 1")
    noise_rms_v = float(noise_rms_v)
    if not np.isfinite(noise_rms_v) or noise_rms_v < 0.0:
        raise ValueError(f"noise_rms_v must be finite and >= 0; got {noise_rms_v}")
    discard = int(discard)
    if discard < 0:
        raise ValueError(f"discard must be >= 0; got {discard}")
    if discard >= bits.size:
        raise ValueError(
            f"discard={discard} leaves no bits (bits length {bits.size})"
        )

    if thresholds is None:
        thr_arr = np.array([float(threshold_v)], dtype=np.float64)
        single_threshold = True
    else:
        thr_arr = np.asarray(thresholds, dtype=np.float64).reshape(-1)
        if thr_arr.size == 0:
            raise ValueError("thresholds must be non-empty")
        single_threshold = False
    if not np.all(np.isfinite(thr_arr)):
        raise ValueError("thresholds must be finite")

    n_phases = phases_arr.size
    mu0 = np.empty(n_phases, dtype=np.float64)
    mu1 = np.empty(n_phases, dtype=np.float64)
    sigma0 = np.empty(n_phases, dtype=np.float64)
    sigma1 = np.empty(n_phases, dtype=np.float64)
    sigma0_ch = np.empty(n_phases, dtype=np.float64)
    sigma1_ch = np.empty(n_phases, dtype=np.float64)
    n0 = np.empty(n_phases, dtype=np.int64)
    n1 = np.empty(n_phases, dtype=np.int64)
    p0 = np.empty((n_phases, thr_arr.size), dtype=np.float64)
    p1 = np.empty((n_phases, thr_arr.size), dtype=np.float64)

    for i, phase in enumerate(phases_arr):
        values, truth = _sample_values(waveform, bits, sps, float(phase), discard)
        m0, s0, m1, s1, c0, c1 = _rail_stats(values, truth, noise_rms_v)
        mu0[i], sigma0[i], mu1[i], sigma1[i] = m0, s0, m1, s1
        sigma0_ch[i] = math.sqrt(max(0.0, s0**2 - noise_rms_v**2))
        sigma1_ch[i] = math.sqrt(max(0.0, s1**2 - noise_rms_v**2))
        n0[i], n1[i] = c0, c1
        for j, thr in enumerate(thr_arr):
            p0[i, j] = _tail_probability(m0, s0, float(thr), "0")
            p1[i, j] = _tail_probability(m1, s1, float(thr), "1")

    ber = 0.5 * (p0 + p1)
    if single_threshold:
        ber_out = ber[:, 0]
    else:
        ber_out = ber

    return {
        "phases": phases_arr,
        "thresholds": thr_arr,
        "ber": ber_out,
        "mu0": mu0,
        "mu1": mu1,
        "sigma0": sigma0,
        "sigma1": sigma1,
        "sigma0_channel_v": sigma0_ch,
        "sigma1_channel_v": sigma1_ch,
        "n0": n0,
        "n1": n1,
        "n_bits": int(bits.size - discard),
        "noise_rms_v": noise_rms_v,
        "threshold_v": float(thr_arr[0]) if single_threshold else None,
        "discard": discard,
        "assumptions": (
            "independent Gaussian residual per rail; equal priors; fixed threshold; "
            "truth-aided rail classification; no CDR/DFE/MLSE/AGC extrapolation"
        ),
    }


# Descriptive alias used in the README and by callers who prefer the layer name.
statistical_ber = gaussian_ber


def confidence_interval(
    errors: int, n: int, level: float = 0.95
) -> tuple[float, float]:
    """Two-sided binomial confidence interval for an error count.

    For ``errors == 0`` the 95 % rule-of-three upper bound is used:
    ``3 / n`` (more generally ``-ln(1-level)/n``).  For ``errors > 0`` the
    Wilson score interval is returned.  ``level`` is the two-sided confidence
    level, e.g. 0.95.

    Returns
    -------
    (lower, upper):
        Floats in ``[0, 1]``.
    """
    errors = int(errors)
    n = int(n)
    level = float(level)
    if n <= 0:
        raise ValueError(f"n must be > 0; got {n}")
    if errors < 0 or errors > n:
        raise ValueError(f"errors must satisfy 0 <= errors <= n; got {errors}/{n}")
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be in (0, 1); got {level}")

    if errors == 0:
        # The exact Clopper-Pearson upper limit is 1-(alpha)^(1/n); the
        # rule-of-three approximation 3/n is the specified 95 % convention.
        if level == 0.95:
            upper = 3.0 / n
        else:
            upper = -math.log1p(-level) / n
        return 0.0, float(min(1.0, upper))

    z = float(norm.ppf(0.5 + level / 2.0))
    p = errors / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    lower = max(0.0, center - half)
    upper = min(1.0, center + half)
    return float(lower), float(upper)


def required_bits_for_ber(ber: float, confidence: float = 0.95) -> int:
    """Bits needed for a zero-error observation at BER ``ber``.

    For the default 95 % confidence the rule of three gives
    ``ceil(3 / ber)``.  For other confidence levels the factor is
    ``-ln(1 - confidence)``.  ``ber == 0`` returns ``inf``.
    """
    ber = float(ber)
    confidence = float(confidence)
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1); got {confidence}")
    if not np.isfinite(ber) or ber < 0.0 or ber > 1.0:
        raise ValueError(f"ber must be finite and in [0, 1]; got {ber}")
    if ber == 0.0:
        return math.inf  # type: ignore[return-value]
    factor = 3.0 if confidence == 0.95 else -math.log1p(-confidence)
    return int(math.ceil(factor / ber))

"""Ideal fixed-phase NRZ sampler -- explicitly **not** a CDR.

The sampler observes a waveform at a programmable phase within each nominal UI.
It has no phase detector, no loop filter, no frequency acquisition, no lock
state and no jitter tracking.  Every result produced by this module therefore
answers a source-synchronous / fixed-clock question ("how much margin at this
phase?"), not a CDR question ("does the loop stay locked?").
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.stats import norm

__all__ = ["q_function", "sample_and_decide"]


def q_function(x: Any) -> Any:
    """Gaussian Q-function ``Q(x) = P(N(0,1) > x)`` via ``scipy.stats.norm.sf``."""
    return norm.sf(x)


def sample_and_decide(
    waveform: Any,
    bits_truth: Any,
    sps: int,
    phase_ui: float,
    threshold_v: float = 0.0,
    noise_rms_v: float = 0.0,
    rng: np.random.Generator | None = None,
    discard: int = 100,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample at ``floor((n + phase_ui) * sps)`` and slice against a threshold.

    Parameters
    ----------
    waveform:
        Sampled waveform (1-D).
    bits_truth:
        Serial 0/1 reference bits.  Only its length is used here; the caller
        compares the returned decisions with ``bits_truth[discard:]``.
    sps:
        Samples per UI.
    phase_ui:
        Fixed sampling phase in UI, ``0 <= phase_ui < 1``.  Phase 0.5 is the
        nominal center of a bit, assuming the waveform starts at a bit boundary.
    threshold_v:
        Decision threshold in volts.
    noise_rms_v:
        Optional independent Gaussian noise added **at the sampling instants**
        (volts RMS).  It is not filtered by the channel.
    rng:
        ``numpy.random.Generator`` used only when ``noise_rms_v > 0``.  If
        ``None``, a fresh default generator is used (non-deterministic).
    discard:
        Number of leading bits to skip.  The first ``discard`` decisions are not
        returned, which removes channel/phase-scan startup transients.

    Returns
    -------
    (decisions, indices):
        ``decisions`` is an ``int8`` array (1 = above threshold) and ``indices``
        contains the (clipped) waveform sample indices.  Both cover bits
        ``discard .. len(bits_truth)-1``.  Sampling positions are clipped to the
        waveform bounds, so a phase/bit combination at the record edge repeats
        the last sample rather than reading out of bounds.

    Notes
    -----
    This is an ideal fixed-phase sampler, **not a CDR**.  It does not adjust the
    phase, track jitter, recover a clock or model a phase interpolator.
    """
    waveform = np.asarray(waveform, dtype=np.float64).reshape(-1)
    bits_truth = np.asarray(bits_truth).reshape(-1)
    sps = int(sps)
    if sps <= 0:
        raise ValueError(f"sps must be an integer >= 1; got {sps}")
    if waveform.size == 0:
        raise ValueError("waveform must be non-empty")
    if bits_truth.size == 0:
        raise ValueError("bits_truth must be non-empty")
    phase_ui = float(phase_ui)
    if not np.isfinite(phase_ui) or not 0.0 <= phase_ui < 1.0:
        raise ValueError(f"phase_ui must satisfy 0 <= phase_ui < 1; got {phase_ui}")
    threshold_v = float(threshold_v)
    noise_rms_v = float(noise_rms_v)
    if not np.isfinite(threshold_v):
        raise ValueError("threshold_v must be finite")
    if not np.isfinite(noise_rms_v) or noise_rms_v < 0.0:
        raise ValueError(f"noise_rms_v must be finite and >= 0; got {noise_rms_v}")
    discard = int(discard)
    if discard < 0:
        raise ValueError(f"discard must be >= 0; got {discard}")
    if discard >= bits_truth.size:
        raise ValueError(
            f"discard={discard} leaves no bits to sample "
            f"(bits_truth length {bits_truth.size})"
        )

    n = np.arange(discard, bits_truth.size, dtype=np.int64)
    indices = np.floor((n.astype(np.float64) + phase_ui) * sps).astype(np.int64)
    np.clip(indices, 0, waveform.size - 1, out=indices)

    samples = waveform[indices]
    if noise_rms_v > 0.0:
        if rng is None:
            rng = np.random.default_rng()
        samples = samples + rng.normal(0.0, noise_rms_v, size=samples.size)
    decisions = (samples > threshold_v).astype(np.int8)
    return decisions, indices

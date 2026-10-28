"""Behavioral NRZ channel and waveform helper.

The channel here is a deliberately small, analytical ISI toy -- **not** a
Touchstone, PEX, IBIS-AMI, package or crosstalk model.  It is useful for
exercising the SI/BER plumbing and for producing a repeatable stressed eye; it
must not be presented as analog signoff.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np
from scipy.signal import lfilter

__all__ = [
    "RCReflectionChannel",
    "load_touchstone_sdd21",
    "nrz_waveform",
]


class RCReflectionChannel:
    """First-order RC loss plus two (or more) delayed reflection taps.

    The reference-demo model implemented here is::

        alpha = exp(-1 / (sps * tau_ui))
        r[n]  = alpha * r[n-1] + (1 - alpha) * x[n]       # RC low-pass
        y[n]  = r[n]
                + 0.20 * r[n - 1*spa]                     # first reflection
                - 0.08 * r[n - 2*spa]                     # second reflection
        y    /= 1.12                                      # DC normalization

    More generally, ``reflections`` is a sequence of ``(delay_ui, coefficient)``
    pairs and the normalization divisor is ``1 + sum(coefficients)``.  The
    delayed taps use the *pre-reflection* RC output, so the taps are parallel
    echoes rather than a recursive IIR ladder.  With a long constant input the
    output settles to exactly 1.0 when ``normalize=True``.

    This is a simplified behavioral ISI model.  It has no frequency-dependent
    loss, no skin effect, no package, no crosstalk and no Touchstone/PEX data.
    """

    def __init__(
        self,
        tau_ui: float = 0.16,
        reflections: Iterable[Sequence[float]] = ((1, 0.20), (2, -0.08)),
        normalize: bool = True,
    ) -> None:
        tau_ui = float(tau_ui)
        if not np.isfinite(tau_ui) or tau_ui <= 0.0:
            raise ValueError(f"tau_ui must be a finite value > 0; got {tau_ui}")
        self.tau_ui = tau_ui
        self.normalize = bool(normalize)

        parsed: list[tuple[int, float]] = []
        for item in reflections:
            try:
                delay_ui, coeff = item
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "each reflection must be a (delay_ui, coefficient) pair; "
                    f"got {item!r}"
                ) from exc
            delay_ui = int(delay_ui)
            coeff = float(coeff)
            if delay_ui <= 0:
                raise ValueError(f"reflection delay_ui must be > 0; got {delay_ui}")
            if not np.isfinite(coeff):
                raise ValueError(f"reflection coefficient must be finite; got {coeff}")
            parsed.append((delay_ui, coeff))
        # Keep deterministic ordering by delay, while allowing duplicate
        # delays to accumulate naturally in apply().
        self.reflections: tuple[tuple[int, float], ...] = tuple(
            sorted(parsed, key=lambda p: p[0])
        )
        self.dc_gain = 1.0 + sum(coeff for _, coeff in self.reflections)

    def apply(self, waveform: Any, sps: int) -> np.ndarray:
        """Filter a sampled waveform.  ``sps`` samples per UI is required."""
        x = np.asarray(waveform, dtype=np.float64).reshape(-1)
        sps = int(sps)
        if sps <= 0:
            raise ValueError(f"sps must be an integer >= 1; got {sps}")
        if x.size == 0:
            return x.copy()

        alpha = float(np.exp(-1.0 / (sps * self.tau_ui)))
        rc = lfilter([1.0 - alpha], [1.0, -alpha], x)
        y = rc.copy()
        for delay_ui, coeff in self.reflections:
            delay = delay_ui * sps
            if delay < rc.size:
                y[delay:] += coeff * rc[:-delay]

        if self.normalize and self.dc_gain != 0.0:
            y /= self.dc_gain
        return y

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"RCReflectionChannel(tau_ui={self.tau_ui!r}, "
            f"reflections={self.reflections!r}, normalize={self.normalize!r})"
        )


def load_touchstone_sdd21(path: Any, **kwargs: Any) -> Any:
    """Load and validate a 2-port/4-port Touchstone response.

    This is a compatibility wrapper around
    :func:`verification.channel.touchstone.load_touchstone_sdd21`; it returns a
    ``TouchstoneResponse`` with Sdd21/Sdd11 and diagnostics.  A time-domain
    channel is obtained with ``touchstone.load_touchstone_channel()``.
    """
    from .touchstone import load_touchstone_sdd21 as _load

    return _load(path, **kwargs)


def nrz_waveform(
    bits: Any,
    sps: int,
    tie_ui: Any | None = None,
    levels: Sequence[float] = (-0.5, 0.5),
) -> np.ndarray:
    """Build a sampled NRZ waveform from serial bits.

    Parameters
    ----------
    bits:
        Serial 0/1 bits, shape ``(N,)``.
    sps:
        Samples per unit interval (UI).
    tie_ui:
        Optional per-boundary TIE (in **UI**), length ``N+1``.  Boundary ``n``
        is placed at ``n + tie_ui[n]`` UI.  If ``None`` all boundaries are
        nominal.  Boundaries must be strictly increasing; this is checked.
    levels:
        ``(level_for_0, level_for_1)``.  The framework default is -0.5 V / +0.5 V
        (1 V nominal peak-to-peak), so the default decision threshold is 0 V.

    Returns
    -------
    numpy.ndarray
        Float64 waveform of length ``N*sps``.  The sample grid is
        ``t = k / sps`` UI.  Samples before the first (possibly jittered)
        boundary are associated with bit 0; this is a finite-record edge effect
        and is ignored by the analysis ``discard`` window.
    """
    bits = np.asarray(bits).reshape(-1)
    if bits.size == 0:
        raise ValueError("bits must contain at least one bit")
    if not np.all((bits == 0) | (bits == 1)):
        raise ValueError("bits must contain only 0/1 values")
    bits = bits.astype(np.int8, copy=False)
    sps = int(sps)
    if sps <= 0:
        raise ValueError(f"sps must be an integer >= 1; got {sps}")
    levels = np.asarray(levels, dtype=np.float64).reshape(-1)
    if levels.size != 2 or not np.all(np.isfinite(levels)):
        raise ValueError("levels must be two finite values (level0, level1)")
    if levels[0] == levels[1]:
        raise ValueError("levels must be distinct")

    n_bits = bits.size
    values = levels[bits]
    if tie_ui is None:
        return np.repeat(values, sps)

    tie_ui = np.asarray(tie_ui, dtype=np.float64).reshape(-1)
    if tie_ui.size != n_bits + 1:
        raise ValueError(
            f"tie_ui must have one value per boundary ({n_bits + 1}); "
            f"got {tie_ui.size}"
        )
    if not np.all(np.isfinite(tie_ui)):
        raise ValueError("tie_ui contains non-finite values")
    boundaries = np.arange(n_bits + 1, dtype=np.float64) + tie_ui
    diffs = np.diff(boundaries)
    bad = np.flatnonzero(diffs <= 0.0)
    if bad.size:
        idx = int(bad[0]) + 1
        raise ValueError(
            f"tie_ui causes nonmonotonic boundaries at index {idx} "
            f"(t[{idx - 1}]={boundaries[idx - 1]:.6g}, "
            f"t[{idx}]={boundaries[idx]:.6g} UI)"
        )

    n_samples = n_bits * sps
    t = np.arange(n_samples, dtype=np.float64) / sps
    # side='right' makes a sample exactly on boundary n belong to bit n.
    bit_index = np.searchsorted(boundaries, t, side="right") - 1
    np.clip(bit_index, 0, n_bits - 1, out=bit_index)
    return values[bit_index]

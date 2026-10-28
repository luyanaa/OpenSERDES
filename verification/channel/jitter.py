"""Time-interval-error (TIE) jitter model.

Conventions and explicit limitations
------------------------------------
The model places one time error on every **nominal bit boundary** ``n``::

    t_n = n * UI + tie_n          (tie_n is in seconds)

Nominal boundary ``n`` is the boundary immediately before bit ``n`` (and
boundary ``N`` follows the last bit), so an ``N``-bit stream has ``N + 1``
boundaries.  Positive TIE moves a boundary later in time.  The returned ``tie``
array is in **seconds**; divide by ``ui_s`` to obtain unit intervals (UI).

Supported components
~~~~~~~~~~~~~~~~~~~~
* **RJ** -- independent Gaussian samples per boundary, with standard deviation
  ``rj_rms_ui``.  This is *not* a phase-noise process: there is no 1/f noise, no
  cycle-to-cycle correlation and no oscillator spectral model.  It is a
  convenient white-TIE surrogate only.
* **SJ/PJ** -- a sinusoid ``sj_peak_ui * sin(2*pi*f*t_n)`` sampled at the nominal
  boundaries.  ``sj_peak_ui`` is the **peak** (0-to-peak) amplitude; the
  peak-to-peak amplitude is therefore ``2 * sj_peak_ui``.  Both numbers are
  recorded in the metadata.  The sinusoid is sampled at nominal times, not at
  the already-jittered times, which keeps the model linear and reproducible.
* **DCD** -- a deterministic offset with the sign of the data transition:
  ``+dcd_peak_ui`` for 0 -> 1 and ``-dcd_peak_ui`` for 1 -> 0.  At a nominal
  boundary where the data does not transition there is no physical edge, so the
  DCD term is zero there.  This sign convention is a model choice; a real
  driver/receiver may have the opposite polarity and/or asymmetry between rise
  and fall.
* **DDJ/ISI** -- an optional caller-supplied ``ddj_ui`` array of per-boundary
  deterministic shifts.  This is an injection point, not a pattern-dependent
  channel simulation; build it from a channel step/impulse response or from a
  measured table if needed.
* **Wander/SSC** -- an optional ``wander_ui`` array of per-boundary shifts.
  Low-frequency sinusoidal frequency modulation (SSC) can be converted to TIE
  externally (or with :func:`sinusoidal_tie`) and passed through this array.
  No SSC loop model, spectrum or spread-spectrum profile is implied.

All amplitude arguments are in UI except ``ui_s`` (seconds) and
``sj_freq_hz`` (Hz).  After adding all components the absolute boundary times
must be strictly increasing.  If jitter makes two boundaries coincide or cross,
``ValueError`` is raised with the offending boundary index.  That is a real
model limit: once edges cross, a simple NRZ/TIE description is no longer valid.
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["build_tie", "sinusoidal_tie"]


def _as_finite_float(name: str, value: Any, *, minimum: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number; got {value!r}") from exc
    if not np.isfinite(out):
        raise ValueError(f"{name} must be finite; got {value!r}")
    if minimum is not None and out < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {out}")
    return out


def sinusoidal_tie(
    n_boundaries: int,
    ui_s: float,
    amplitude_ui: float,
    frequency_hz: float,
    phase_rad: float = 0.0,
) -> np.ndarray:
    """Sample a sinusoidal TIE at ``n_boundaries`` nominal boundary times.

    Returns an array in **UI** (not seconds); multiply by ``ui_s`` if a
    seconds-valued TIE is needed.  ``amplitude_ui`` is a peak amplitude.  This
    helper is also convenient for generating an SSC/wander array to pass to
    :func:`build_tie` as ``wander_ui``.
    """
    n_boundaries = int(n_boundaries)
    if n_boundaries < 1:
        raise ValueError(f"n_boundaries must be >= 1; got {n_boundaries}")
    ui_s = _as_finite_float("ui_s", ui_s, minimum=0.0)
    if ui_s <= 0:
        raise ValueError(f"ui_s must be > 0; got {ui_s}")
    amplitude_ui = _as_finite_float("amplitude_ui", amplitude_ui, minimum=0.0)
    frequency_hz = _as_finite_float("frequency_hz", frequency_hz, minimum=0.0)
    phase_rad = _as_finite_float("phase_rad", phase_rad)
    t = np.arange(n_boundaries, dtype=np.float64) * ui_s
    if frequency_hz == 0.0:
        return np.zeros(n_boundaries, dtype=np.float64)
    return amplitude_ui * np.sin(2.0 * np.pi * frequency_hz * t + phase_rad)


def _injected_ui(name: str, values: Any, n_boundaries: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if arr.size != n_boundaries:
        raise ValueError(
            f"{name} must contain one value per boundary ({n_boundaries}); "
            f"got {arr.size} values"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite values")
    return arr


def build_tie(
    bits: Any,
    ui_s: float,
    rj_rms_ui: float,
    sj_peak_ui: float,
    sj_freq_hz: float,
    dcd_peak_ui: float,
    rng: np.random.Generator | None = None,
    ddj_ui: Any | None = None,
    wander_ui: Any | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build per-boundary TIE in seconds and a JSON-serializable metadata dict.

    Parameters
    ----------
    bits:
        Serial 0/1 data.  An ``N``-bit stream produces ``N+1`` boundaries.
    ui_s:
        Unit interval in seconds (``1 / rate_hz``).
    rj_rms_ui:
        Gaussian random-jitter RMS, in UI.  Independent per boundary.
    sj_peak_ui:
        Sinusoidal jitter **peak** amplitude, in UI (p-p is twice this).
    sj_freq_hz:
        Sinusoidal jitter frequency in Hz.  Must be > 0 when ``sj_peak_ui`` is
        non-zero.
    dcd_peak_ui:
        Duty-cycle-distortion magnitude in UI, signed by the data transition.
    rng:
        ``numpy.random.Generator``.  If ``None`` a fresh default generator is
        created (non-deterministic); pass a seeded generator for repeatability.
    ddj_ui:
        Optional array with one deterministic TIE value (UI) per boundary.
        This is an injection point for an externally computed DDJ/ISI table.
    wander_ui:
        Optional array with one low-frequency wander/SSC TIE value (UI) per
        boundary.

    Returns
    -------
    (tie, meta):
        ``tie`` is a float64 array of length ``N+1`` in seconds.  ``meta``
        records the component settings, including both SJ peak and peak-to-peak
        values, and the observed sampled peak for transparency.
    """
    bits = np.asarray(bits).reshape(-1)
    if bits.size == 0:
        raise ValueError("bits must contain at least one bit")
    if not np.all((bits == 0) | (bits == 1)):
        raise ValueError("bits must contain only 0/1 values")
    bits = bits.astype(np.int8, copy=False)

    ui_s = _as_finite_float("ui_s", ui_s, minimum=0.0)
    if ui_s <= 0.0:
        raise ValueError(f"ui_s must be > 0; got {ui_s}")
    rj_rms_ui = _as_finite_float("rj_rms_ui", rj_rms_ui, minimum=0.0)
    sj_peak_ui = _as_finite_float("sj_peak_ui", sj_peak_ui, minimum=0.0)
    sj_freq_hz = _as_finite_float("sj_freq_hz", sj_freq_hz, minimum=0.0)
    dcd_peak_ui = _as_finite_float("dcd_peak_ui", dcd_peak_ui, minimum=0.0)
    if sj_peak_ui > 0.0 and sj_freq_hz <= 0.0:
        raise ValueError("sj_freq_hz must be > 0 when sj_peak_ui is non-zero")

    n_bits = bits.size
    n_boundaries = n_bits + 1
    tie_ui = np.zeros(n_boundaries, dtype=np.float64)

    # Random jitter: independent per boundary, in UI.  Only draw when needed so
    # that a zero-RJ run does not consume RNG state.
    if rj_rms_ui > 0.0:
        if rng is None:
            rng = np.random.default_rng()
        tie_ui += rng.normal(0.0, rj_rms_ui, size=n_boundaries)

    # Sinusoidal jitter (peak amplitude), sampled at nominal boundary times.
    sj_ui = np.zeros(n_boundaries, dtype=np.float64)
    if sj_peak_ui > 0.0:
        t_nominal = np.arange(n_boundaries, dtype=np.float64) * ui_s
        sj_ui = sj_peak_ui * np.sin(2.0 * np.pi * sj_freq_hz * t_nominal)
        tie_ui += sj_ui

    # DCD: sign follows the data transition; no transition -> no observable
    # edge, so the term is zero at that nominal boundary.
    dcd_ui = np.zeros(n_boundaries, dtype=np.float64)
    if dcd_peak_ui > 0.0 and n_bits > 1:
        transitions = bits[1:].astype(np.int8) - bits[:-1].astype(np.int8)
        # transitions[i] is at boundary i+1 (between bit i and bit i+1).
        # Boundary N follows the last bit, so it has no data transition.
        dcd_ui[1:n_bits] = dcd_peak_ui * np.sign(transitions)
        tie_ui += dcd_ui

    ddj_applied = ddj_ui is not None
    if ddj_applied:
        tie_ui += _injected_ui("ddj_ui", ddj_ui, n_boundaries)

    wander_applied = wander_ui is not None
    if wander_applied:
        tie_ui += _injected_ui("wander_ui", wander_ui, n_boundaries)

    # All components above are in UI; the public TIE is in seconds.
    tie = tie_ui * ui_s
    if not np.all(np.isfinite(tie)):
        raise ValueError("TIE contains non-finite values")

    # Strict monotonicity of absolute boundary times.  Report the boundary at
    # which the ordering first fails.
    boundary_times = np.arange(n_boundaries, dtype=np.float64) * ui_s + tie
    diffs = np.diff(boundary_times)
    bad = np.flatnonzero(diffs <= 0.0)
    if bad.size:
        idx = int(bad[0]) + 1
        raise ValueError(
            "Jitter caused nonmonotonic boundaries at index "
            f"{idx}: t[{idx - 1}]={boundary_times[idx - 1]:.6e} s, "
            f"t[{idx}]={boundary_times[idx]:.6e} s (need strictly increasing)"
        )

    observed_peak = float(np.max(np.abs(sj_ui))) if sj_peak_ui > 0.0 else 0.0
    observed_pp = float(np.ptp(sj_ui)) if sj_peak_ui > 0.0 else 0.0
    meta: dict[str, Any] = {
        "ui_s": ui_s,
        "n_bits": int(n_bits),
        "n_boundaries": int(n_boundaries),
        "tie_unit": "s",
        "tie_ui_unit": "UI = tie / ui_s",
        "rj_rms_ui": rj_rms_ui,
        "sj_peak_ui": sj_peak_ui,
        "sj_peak_to_peak_ui": 2.0 * sj_peak_ui,
        "sj_definition": "peak (0-to-peak); peak-to-peak = 2 * peak",
        "sj_freq_hz": sj_freq_hz,
        "sj_observed_peak_ui": observed_peak,
        "sj_observed_peak_to_peak_ui": observed_pp,
        "dcd_peak_ui": dcd_peak_ui,
        "dcd_sign": "plus on 0->1, minus on 1->0, zero on no-transition",
        "ddj_applied": bool(ddj_applied),
        "wander_applied": bool(wander_applied),
        "rj_model": "independent Gaussian TIE per boundary; not phase-noise correlated",
    }
    return tie, meta

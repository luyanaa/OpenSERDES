"""Touchstone S-parameter import and time-domain channel response.

This module is deliberately dependency-light: it includes a native Touchstone
``.s2p``/``.s4p`` parser so CI does not require scikit-rf.  If scikit-rf is
installed it can be used as a cross-check, but it is not required.

What is implemented
-------------------
* Touchstone v1 ``S`` data in ``RI``, ``MA`` or ``DB`` format with Hz/kHz/MHz/GHz.
* S2P: single-ended insertion/return loss (``S21``/``S11``) for the original
  single-ended CMOS link.
* S4P: explicit port map -> mixed-mode ``Sdd21``/``Sdd11`` with the standard
  1/sqrt(2) differential transformation.
* Frequency-grid checks, DC handling, passivity/reciprocity diagnostics, an
  impulse response, deterministic propagation-delay removal, and an aligned
  convolution channel for the existing eye/BER code.

What is not implemented
-----------------------
* Full Touchstone v2, N-port (other than 2/4), Y/Z parameter formats, and
  automatic port-order discovery.  Port order must come from the user or the
  connector metadata; guessing is refused.
* Constrained rational fitting/causality enforcement, IBIS-AMI, crosstalk
  superposition, and S-parameter renormalization beyond the stated reference
  impedance.  Warnings are recorded instead of silently correcting the data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from scipy.signal import fftconvolve

__all__ = [
    "TouchstoneError",
    "TouchstoneResponse",
    "TouchstoneChannel",
    "load_touchstone",
    "load_touchstone_channel",
    "load_touchstone_sdd21",
]


class TouchstoneError(ValueError):
    """Raised when a Touchstone file is malformed or unsupported."""


@dataclass
class TouchstoneResponse:
    """Validated frequency response and diagnostics."""

    path: str
    nports: int
    freq_hz: np.ndarray
    sdd21: np.ndarray
    sdd11: np.ndarray | None
    reference_ohm: float
    port_map: dict[str, int]
    warnings: list[str] = field(default_factory=list)
    dc_extrapolated: bool = False
    format: str = "RI"

    def insertion_loss_db(self) -> np.ndarray:
        return 20.0 * np.log10(np.maximum(np.abs(self.sdd21), 1e-30))

    def group_delay_s(self) -> np.ndarray:
        phase = np.unwrap(np.angle(self.sdd21))
        if self.freq_hz.size < 2:
            return np.zeros_like(phase)
        return -np.gradient(phase, self.freq_hz)


def _parse_header(line: str) -> tuple[str, str, float]:
    tokens = line[1:].upper().split()
    if not tokens:
        return "S", "RI", 50.0
    kind = next((t for t in tokens if t in ("S", "Y", "Z")), "S")
    fmt = next((t for t in tokens if t in ("RI", "MA", "DB")), "RI")
    ref = 50.0
    if "R" in tokens:
        index = tokens.index("R")
        if index + 1 < len(tokens):
            try:
                ref = float(tokens[index + 1])
            except ValueError as exc:
                raise TouchstoneError(
                    f"bad reference impedance in header: {line!r}"
                ) from exc
    return kind, fmt, ref


def _freq_scale(header: str) -> float:
    tokens = header[1:].upper().split()
    unit = "GHZ"
    # Unit is the first token that is not S/Y/Z, RI/MA/DB or a number.
    for token in tokens:
        if token in ("HZ", "KHZ", "MHZ", "GHZ"):
            unit = token
            break
    return {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[unit]


def _default_port_map(nports: int) -> dict[str, int]:
    if nports == 2:
        return {"tx": 0, "rx": 1}
    if nports == 4:
        return {"tx_p": 0, "tx_n": 1, "rx_p": 2, "rx_n": 3}
    raise TouchstoneError(
        f"only 2-port and 4-port files are supported; got {nports} ports"
    )


def _reorder(s: np.ndarray, port_map: dict[str, int], nports: int) -> np.ndarray:
    required = ["tx", "rx"] if nports == 2 else ["tx_p", "tx_n", "rx_p", "rx_n"]
    if sorted(port_map) != sorted(required):
        raise TouchstoneError(
            f"port_map must define exactly {required}; got {sorted(port_map)}"
        )
    indices = [port_map[name] for name in required]
    if sorted(indices) != list(range(nports)):
        raise TouchstoneError(
            f"port_map indices must be a permutation of 0..{nports-1}: {indices}"
        )
    return s[:, indices, :][:, :, indices]


def _network_from_file(path: Path) -> tuple[np.ndarray, np.ndarray, int, float, str, list[str]]:
    text = path.read_text(errors="ignore")
    lines = []
    header = ""
    for raw in text.splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            header = line
            continue
        if line.startswith("["):
            raise TouchstoneError("Touchstone v2 keywords are not supported")
        lines.append(line)
    if not lines:
        raise TouchstoneError(f"{path} contains no data")
    kind, fmt, ref = _parse_header(header) if header else ("S", "RI", 50.0)
    if kind != "S":
        raise TouchstoneError(f"only S-parameter data is supported; got {kind!r}")
    if fmt not in ("RI", "MA", "DB"):
        raise TouchstoneError(f"unsupported data format {fmt!r}")
    fs = _freq_scale(header) if header else 1e9

    values = np.fromstring(" ".join(lines), sep=" ")
    # Touchstone allows 2/4-port rows.  Determine nports from a data row:
    # frequency + 2*N values.  Use the file suffix as the primary hint.
    suffix = path.suffix.lower()
    if suffix in (".s2p", ".ts2"):
        nports = 2
    elif suffix in (".s4p", ".ts4"):
        nports = 4
    else:
        raise TouchstoneError("file suffix must be .s2p or .s4p (or .ts2/.ts4)")
    stride = 1 + 2 * nports * nports
    if values.size % stride != 0:
        raise TouchstoneError(
            f"data length {values.size} is not a multiple of {stride} for {nports} ports"
        )
    rows = values.reshape(-1, stride)
    freq = rows[:, 0] * fs
    data = rows[:, 1:]
    if np.any(np.diff(freq) <= 0):
        raise TouchstoneError("frequencies must be strictly increasing")
    if fmt == "RI":
        s = data[:, 0::2] + 1j * data[:, 1::2]
    elif fmt == "MA":
        s = data[:, 0::2] * np.exp(1j * np.deg2rad(data[:, 1::2]))
    else:  # DB
        mag = 10.0 ** (data[:, 0::2] / 20.0)
        s = mag * np.exp(1j * np.deg2rad(data[:, 1::2]))
    # Touchstone v1 orders each row as S11,S21,...,SN1,S12,...,SNN, i.e.
    # input-port-major.  Transpose to [output, input] for the mixed-mode math.
    s = s.reshape(-1, nports, nports).transpose(0, 2, 1)
    warnings: list[str] = []
    if ref <= 0:
        raise TouchstoneError(f"reference impedance must be > 0; got {ref}")
    if abs(ref - 50.0) > 1e-9:
        warnings.append(
            f"reference impedance is {ref:g} ohm, not 50 ohm; results are not "
            "renormalized and are reported against this reference"
        )
    return freq, s, nports, ref, fmt, warnings


def load_touchstone(
    path: str | Path,
    *,
    port_map: dict[str, int] | None = None,
    require_dc: bool = False,
) -> TouchstoneResponse:
    """Load a 2-port or 4-port Touchstone file.

    For 4-port data the default physical order is
    ``tx_p, tx_n, rx_p, rx_n``.  Pass ``port_map`` when the file uses a
    different order; automatic guessing is intentionally not attempted.
    """
    p = Path(path)
    freq, s_raw, nports, ref, fmt, warnings = _network_from_file(p)
    pm = dict(port_map) if port_map is not None else _default_port_map(nports)
    s = _reorder(s_raw, pm, nports)

    # Basic sanity diagnostics on the *single-ended* network before mixed-mode.
    max_mag = float(np.max(np.abs(s))) if s.size else 0.0
    if max_mag > 1.0 + 1e-6:
        warnings.append(f"passivity check: max |S| = {max_mag:.4f} > 1")
    reciprocity = float(np.max(np.abs(s - np.transpose(s, (0, 2, 1)))))
    if reciprocity > 1e-6:
        warnings.append(f"reciprocity check: max |S - S^T| = {reciprocity:.3e}")

    if nports == 2:
        sdd21 = s[:, 1, 0]
        sdd11 = s[:, 0, 0]
    else:
        # Logical order: tx_p(0), tx_n(1), rx_p(2), rx_n(3).
        sdd21 = 0.5 * (s[:, 2, 0] - s[:, 2, 1] - s[:, 3, 0] + s[:, 3, 1])
        sdd11 = 0.5 * (s[:, 0, 0] - s[:, 0, 1] - s[:, 1, 0] + s[:, 1, 1])

    dc_extrapolated = False
    if freq[0] > 0.0:
        if require_dc:
            raise TouchstoneError(
                f"{p} has no DC point; supply DC or allow extrapolation explicitly"
            )
        # Keep the low-frequency real level and zero imaginary part.  This is
        # recorded in the returned object; it is not a hidden correction.
        freq = np.concatenate(([0.0], freq))
        sdd21 = np.concatenate(([sdd21[0].real + 0j], sdd21))
        sdd11 = np.concatenate(([sdd11[0].real + 0j], sdd11))
        dc_extrapolated = True
        warnings.append("no DC point: extrapolated from the lowest frequency")

    return TouchstoneResponse(
        path=str(p),
        nports=nports,
        freq_hz=freq,
        sdd21=sdd21,
        sdd11=sdd11,
        reference_ohm=ref,
        port_map=pm,
        warnings=warnings,
        dc_extrapolated=dc_extrapolated,
        format=fmt,
    )


def _uniform_response(
    response: TouchstoneResponse,
    sps: int,
    ui_s: float,
    n_fft: int | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Resample Sdd21 onto a uniform positive-frequency grid and build h(t)."""
    if sps <= 0 or ui_s <= 0:
        raise ValueError("sps and ui_s must be positive")
    dt = ui_s / sps
    fmax = float(response.freq_hz[-1])
    if fmax <= 0:
        raise TouchstoneError("Touchstone response has no positive frequency point")
    if n_fft is None:
        # Cover the measured bandwidth with a grid no coarser than ~fmax/4096,
        # and keep the time record practical.
        n_fft = 1
        while 1.0 / (dt * n_fft) > fmax / 4096.0 or n_fft < 8192:
            n_fft *= 2
    df = 1.0 / (dt * n_fft)
    f_grid = np.arange(n_fft // 2 + 1) * df
    # Linear interpolation preserves the measured magnitude/phase; this is
    # adequate for the eye/BER plumbing, not a substitute for a vector-fit.
    real = np.interp(f_grid, response.freq_hz, response.sdd21.real,
                     left=response.sdd21[0].real, right=0.0)
    imag = np.interp(f_grid, response.freq_hz, response.sdd21.imag,
                     left=response.sdd21[0].imag, right=0.0)
    h_grid = real + 1j * imag
    h_grid[f_grid > fmax] = 0.0
    h_grid[0] = h_grid[0].real
    h = np.fft.irfft(h_grid, n=n_fft)
    return h, f_grid, dt


@dataclass
class TouchstoneChannel:
    """Aligned time-domain channel derived from a Touchstone response.

    ``apply(waveform)`` convolves and removes the deterministic propagation
    delay (by the impulse-response peak).  The delay is recorded in
    ``delay_s``; no claim is made that the residual phase is dispersion-free.
    """

    response: TouchstoneResponse
    sps: int
    ui_s: float
    n_fft: int | None = None

    def __post_init__(self) -> None:
        self.h, self.f_grid, self.dt = _uniform_response(
            self.response, self.sps, self.ui_s, self.n_fft
        )
        self.delay_samples = int(np.argmax(np.abs(self.h)))
        self.delay_s = self.delay_samples * self.dt

    def apply(self, waveform: np.ndarray) -> np.ndarray:
        x = np.asarray(waveform, dtype=np.float64).reshape(-1)
        if x.size == 0:
            return x.copy()
        y = fftconvolve(x, self.h, mode="full")[: x.size + self.h.size - 1]
        d = self.delay_samples
        if d >= y.size:
            return np.zeros_like(x)
        out = y[d:d + x.size]
        if out.size < x.size:
            out = np.pad(out, (0, x.size - out.size))
        return out


def load_touchstone_channel(
    path: str | Path,
    sps: int,
    ui_s: float,
    *,
    port_map: dict[str, int] | None = None,
    n_fft: int | None = None,
) -> TouchstoneChannel:
    response = load_touchstone(path, port_map=port_map)
    return TouchstoneChannel(response=response, sps=sps, ui_s=ui_s, n_fft=n_fft)


def load_touchstone_sdd21(path: str | Path, **kwargs: Any) -> TouchstoneResponse:
    """Backwards-compatible alias returning the validated response."""
    return load_touchstone(path, **kwargs)

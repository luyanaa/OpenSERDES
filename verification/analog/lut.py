"""Helpers for consuming the sky130 schematic-level LUT.

The LUT is produced by :mod:`verification.analog.extract_lut`.  It is a
schematic-level table (ngspice + PDK device models, no PEX).  These helpers map
the table into the behavioral framework:

* TX 20-80 % rise time -> first-order RC tau.  For an RC step,
  ``t_20_80 = tau * ln(4)``, so ``tau_ns = rise_ns / ln(4)``.
* RX inverter delay -> a sampling-delay budget term.  The current behavioral
  slicer does not yet inject this as a phase offset; the helper returns the
  value so a caller/profile can account for it explicitly.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

__all__ = [
    "load_lut",
    "tx_rise_ns",
    "tx_tau_ns",
    "rx_delay_ns",
]


def load_lut(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or "tx" not in data or "rx" not in data:
        raise ValueError(f"{path} is not a LUT produced by extract_lut.py")
    return data


def _interp(xs: list[float], ys: list[float | None], x: float) -> float | None:
    pairs = [(a, b) for a, b in zip(xs, ys) if b is not None]
    if not pairs:
        return None
    if len(pairs) == 1:
        return float(pairs[0][1])
    pairs.sort()
    if x <= pairs[0][0]:
        return float(pairs[0][1])
    if x >= pairs[-1][0]:
        return float(pairs[-1][1])
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y0)
            return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return float(pairs[-1][1])


def tx_rise_ns(lut: dict[str, Any], load_ff: float) -> float | None:
    rows = lut["tx"]["rows"]
    return _interp([float(r["load_ff"]) for r in rows],
                   [r.get("rise_ns") for r in rows], float(load_ff))


def tx_tau_ns(lut: dict[str, Any], load_ff: float) -> float | None:
    """Map the 20-80 % rise time to an equivalent first-order RC tau."""
    rise = tx_rise_ns(lut, load_ff)
    if rise is None or rise <= 0.0:
        return None
    return rise / math.log(4.0)


def rx_delay_ns(lut: dict[str, Any], slew_ps: float) -> float | None:
    rows = lut["rx"]["rows"]
    delays = []
    for row in rows:
        tphl = row.get("tphl_ns")
        tplh = row.get("tplh_ns")
        if tphl is None and tplh is None:
            delays.append(None)
        elif tphl is None:
            delays.append(tplh)
        elif tplh is None:
            delays.append(tphl)
        else:
            delays.append(0.5 * (tphl + tplh))
    return _interp([float(r["slew_ps"]) for r in rows], delays, float(slew_ps))

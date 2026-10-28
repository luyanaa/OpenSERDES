"""Eye-diagram folding and explicit contour-based eye metrics.

Definitions used here (all are estimator choices, not standards):

* A fold window is ``ui_window`` UI long and is **centered on a nominal bit
  center** (``n + 0.5 UI``).  For the default two-UI window the folded matrix
  therefore contains one complete eye opening in its middle and the transitions
  near its left/right edges.  This avoids the common confusion where a 2-UI
  window aligned to bit boundaries places a crossing at the matrix midpoint.
* For each time column the samples are split into an upper group (> threshold)
  and a lower group (<= threshold).  The vertical opening is
  ``percentile(upper, 0.5) - percentile(lower, 99.5)`` by default: a 99 %
  *inner-rail* estimate.  Negative opening means the inner rails overlap.
* ``eye_height_v`` is that opening at the matrix-center column (the nominal eye
  center).
* ``eye_width_ui`` is a threshold-crossing percentile width: for every trace
  the threshold crossing times on the left and right of the center are
  interpolated.  The width is the interval between the late-left crossing
  percentile and the early-right crossing percentile (0.5/99.5 by default),
  divided by ``sps``.  This is the horizontal opening at the decision threshold
  after accounting for jitter/ISI; traces that do not transition at a boundary
  simply do not contribute a crossing there.  A non-positive raw width is
  reported as 0.

These percentiles are simple, transparent estimators.  They are not a formal
BER contour, not a Gaussian tail fit and not a substitute for a specified
mask/contour standard.
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["fold_eye", "eye_metrics", "plot_eye"]


def fold_eye(waveform: Any, sps: int, ui_window: int = 2) -> np.ndarray:
    """Fold a sampled waveform into eye traces of ``ui_window`` UI.

    Parameters
    ----------
    waveform:
        Sampled waveform.
    sps:
        Samples per UI.
    ui_window:
        Window length in UI.  The default is 2 UI.

    Returns
    -------
    numpy.ndarray
        Shape ``(n_traces, ui_window*sps)``.  Each row is one window centered on
        a bit center.  Windows that would fall outside the record are omitted,
        so a short record can produce an empty matrix with the correct number
        of columns.
    """
    wave = np.asarray(waveform, dtype=np.float64).reshape(-1)
    sps = int(sps)
    ui_window = int(ui_window)
    if sps <= 0:
        raise ValueError(f"sps must be an integer >= 1; got {sps}")
    if ui_window <= 0:
        raise ValueError(f"ui_window must be an integer >= 1; got {ui_window}")

    seg_len = ui_window * sps
    if wave.size < seg_len:
        return np.empty((0, seg_len), dtype=np.float64)

    starts: list[int] = []
    n = 0
    while True:
        start = int(round((n + 0.5 - 0.5 * ui_window) * sps))
        if start < 0:
            n += 1
            continue
        if start + seg_len > wave.size:
            break
        starts.append(start)
        n += 1

    if not starts:
        return np.empty((0, seg_len), dtype=np.float64)
    return np.stack([wave[s : s + seg_len] for s in starts], axis=0)


def eye_metrics(
    folded: Any,
    threshold_v: float = 0.0,
    rail_percentile: float = 0.5,
    sps: int | None = None,
) -> dict[str, Any]:
    """Compute inner-rail eye height and threshold eye width.

    Parameters
    ----------
    folded:
        Output of :func:`fold_eye`, shape ``(n_traces, n_columns)``.
    threshold_v:
        Decision threshold used both to split rails and as the horizontal
        reference for eye width.
    rail_percentile:
        Percentage in ``[0, 50)``.  Upper-rail lower percentile and lower-rail
        upper percentile are used; 0.5 gives a 99 % inner opening.
    sps:
        Optional samples per UI.  Supply it for a non-2-UI fold; for the default
        2-UI fold it is inferred from the column count.

    Returns
    -------
    dict
        Contains ``eye_height_v`` and ``eye_width_ui`` (plus ``height_v`` /
        ``width_ui`` aliases), the per-column ``opening_v`` array, the center
        index, threshold and percentile.  It is JSON-friendly except for the
        ``opening_v`` array.
    """
    arr = np.asarray(folded, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"folded must be 2-D; got shape {arr.shape}")
    threshold_v = float(threshold_v)
    if not np.isfinite(threshold_v):
        raise ValueError("threshold_v must be finite")
    rail_percentile = float(rail_percentile)
    if not 0.0 <= rail_percentile < 50.0:
        raise ValueError(f"rail_percentile must be in [0, 50); got {rail_percentile}")
    if sps is not None:
        sps = int(sps)
        if sps <= 0:
            raise ValueError(f"sps must be positive; got {sps}")

    n_traces, n_cols = arr.shape
    if n_cols == 0:
        return {
            "eye_height_v": float("nan"),
            "eye_width_ui": 0.0,
            "height_v": float("nan"),
            "width_ui": 0.0,
            "height": float("nan"),
            "width": 0.0,
            "opening_v": np.empty(0, dtype=np.float64),
            "upper_contour_v": np.empty(0, dtype=np.float64),
            "lower_contour_v": np.empty(0, dtype=np.float64),
            "threshold_margin_v": np.empty(0, dtype=np.float64),
            "center_index": 0,
            "threshold_v": threshold_v,
            "rail_percentile": rail_percentile,
        }

    opening = np.full(n_cols, np.nan, dtype=np.float64)
    upper_contour = np.full(n_cols, np.nan, dtype=np.float64)
    lower_contour = np.full(n_cols, np.nan, dtype=np.float64)
    margin = np.full(n_cols, np.nan, dtype=np.float64)
    for col in range(n_cols):
        values = arr[:, col]
        values = values[np.isfinite(values)]
        if values.size == 0:
            continue
        upper = values[values > threshold_v]
        lower = values[values <= threshold_v]
        if upper.size == 0 or lower.size == 0:
            continue
        upper_low = np.percentile(upper, rail_percentile)
        lower_high = np.percentile(lower, 100.0 - rail_percentile)
        upper_contour[col] = upper_low
        lower_contour[col] = lower_high
        opening[col] = upper_low - lower_high
        margin[col] = min(upper_low - threshold_v, threshold_v - lower_high)

    center = n_cols // 2
    height = float(opening[center]) if np.isfinite(opening[center]) else float("nan")

    # Horizontal opening at the threshold from interpolated trace crossings.
    # Each 2-UI window centered on a bit contains two nominal boundaries (the
    # left and right edges of that bit).  Traces that do not transition there
    # contribute no crossing.  The late-left / early-right percentiles give the
    # inner horizontal opening.
    sps_est = float(sps) if sps is not None else _infer_sps_from_shape(n_cols)
    left_crossings: list[float] = []
    right_crossings: list[float] = []
    for row in arr:
        y = row - threshold_v
        finite = np.isfinite(y)
        for k in range(n_cols - 1):
            if not (finite[k] and finite[k + 1]):
                continue
            y0 = y[k]
            y1 = y[k + 1]
            if y0 == 0.0:
                x = float(k)
            elif y0 * y1 < 0.0:
                x = k + (-y0) / (y1 - y0)
            else:
                continue
            if x < center:
                left_crossings.append(x)
            else:
                right_crossings.append(x)

    width_ui = 0.0
    width_raw_ui = 0.0
    if len(left_crossings) >= 2 and len(right_crossings) >= 2:
        left_arr = np.asarray(left_crossings, dtype=np.float64)
        right_arr = np.asarray(right_crossings, dtype=np.float64)
        late_left = float(np.percentile(left_arr, 100.0 - rail_percentile))
        early_right = float(np.percentile(right_arr, rail_percentile))
        width_raw_ui = (early_right - late_left) / sps_est
        width_ui = max(0.0, width_raw_ui)

    return {
        "eye_height_v": height,
        "eye_width_ui": float(width_ui),
        "eye_width_raw_ui": float(width_raw_ui),
        "height_v": height,
        "width_ui": float(width_ui),
        "height": height,
        "width": float(width_ui),
        "opening_v": opening,
        "upper_contour_v": upper_contour,
        "lower_contour_v": lower_contour,
        "threshold_margin_v": margin,
        "left_crossings_ui": np.asarray(left_crossings, dtype=np.float64) / sps_est,
        "right_crossings_ui": np.asarray(right_crossings, dtype=np.float64) / sps_est,
        "center_index": int(center),
        "threshold_v": threshold_v,
        "rail_percentile": rail_percentile,
    }


def _infer_sps_from_shape(n_cols: int) -> float:
    """Best-effort samples-per-UI inference for a 2-UI fold.

    ``eye_metrics`` is normally called with the output of :func:`fold_eye`
    (2 UI), so half the columns is the sample rate.  For other integer window
    lengths the common factors are ambiguous; this helper only affects the UI
    label, never the vertical estimator.
    """
    if n_cols % 2 == 0:
        return float(n_cols // 2)
    return float(n_cols) / 2.0


def plot_eye(
    folded: Any,
    path: Any | None = None,
    threshold_v: float = 0.0,
    sps: int | None = None,
    ui_s: float | None = None,
    title: str | None = None,
    max_traces: int = 2000,
) -> dict[str, Any]:
    """Plot a folded eye and optionally save it.  Requires an Agg backend in CLI/tests.

    Only ``max_traces`` rows are drawn (evenly sampled) to keep the figure
    responsive; metrics are always computed from **all** rows.
    """
    import matplotlib.pyplot as plt

    arr = np.asarray(folded, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"folded must be 2-D; got shape {arr.shape}")
    n_cols = arr.shape[1]
    if sps is None:
        sps = _infer_sps_from_shape(n_cols) if n_cols else 1
    sps = int(sps)
    if sps <= 0:
        raise ValueError("sps must be positive")
    metrics = eye_metrics(arr, threshold_v=threshold_v, sps=sps)

    x = (np.arange(n_cols, dtype=np.float64) - 0.5 * (n_cols - 1)) / sps
    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    if arr.shape[0] > 0:
        if arr.shape[0] > max_traces:
            rows = np.linspace(0, arr.shape[0] - 1, max_traces).astype(int)
            draw = arr[rows]
        else:
            draw = arr
        ax.plot(x, draw.T, color="#1f4e79", alpha=0.10, linewidth=0.45)
    ax.axhline(threshold_v, color="#c00000", linewidth=0.9, linestyle="--", label="threshold")
    ax.set_xlabel(
        "Time relative to eye center (UI)"
        if ui_s is None
        else "Time relative to eye center (UI; 1 UI = %.4g s)" % ui_s
    )
    ax.set_ylabel("Amplitude (V)")
    if title:
        ax.set_title(title)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper right", fontsize=8)

    h = metrics["eye_height_v"]
    w = metrics["eye_width_ui"]
    text = f"height={h:.3f} V, width={w:.3f} UI (percentile estimates)"
    ax.text(
        0.01,
        0.01,
        text,
        transform=ax.transAxes,
        fontsize=8,
        va="bottom",
        ha="left",
        bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "none"},
    )
    fig.tight_layout()
    if path is not None:
        fig.savefig(path, dpi=130)
    plt.close(fig)
    return metrics

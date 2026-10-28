"""Analysis layer: eye, empirical bathtub, statistical BER and JTOL driving."""

from .eye import eye_metrics, fold_eye, plot_eye
from .bathtub import phase_scan, threshold_scan
from .ber import (
    confidence_interval,
    gaussian_ber,
    required_bits_for_ber,
    statistical_ber,
)
from .jtol import jtol_curve, run_jtol

__all__ = [
    "eye_metrics",
    "fold_eye",
    "plot_eye",
    "phase_scan",
    "threshold_scan",
    "confidence_interval",
    "gaussian_ber",
    "required_bits_for_ber",
    "statistical_ber",
    "jtol_curve",
    "run_jtol",
]

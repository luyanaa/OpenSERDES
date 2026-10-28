"""Channel layer: jitter, behavioral channel and the ideal fixed-phase slicer."""

from .jitter import build_tie, sinusoidal_tie
from .channel_impulse import RCReflectionChannel, load_touchstone_sdd21, nrz_waveform
from .rx_slicer import q_function, sample_and_decide

__all__ = [
    "build_tie",
    "sinusoidal_tie",
    "RCReflectionChannel",
    "load_touchstone_sdd21",
    "nrz_waveform",
    "q_function",
    "sample_and_decide",
]

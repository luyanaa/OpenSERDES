"""OpenSERDES phase-1 behavioral Signal-Integrity / BER framework.

This package is intentionally self-contained and uses only numpy, scipy,
matplotlib and PyYAML.  It does **not** run RTL and it does **not** contain a
CDR.  See ``verification/README.md`` for the conventions and non-claims.

Public layers:

* :mod:`verification.patterns` -- PRBS generators matching ``rtl/prbs_gen.sv``
* :mod:`verification.channel` -- TIE jitter, behavioral RC/reflection channel,
  ideal fixed-phase slicer
* :mod:`verification.analysis` -- eye folding/metrics, empirical bathtub,
  Gaussian-tail BER prediction, JTOL sweep driver
* :mod:`verification.cli` -- command-line entry points
"""

__version__ = "0.1.0"

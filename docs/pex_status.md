# PEX status

The repository contains real sky130 Calibre xRC PEX exports for the legacy
TX/RX macros:

- `Inverter_Based_Tx/Full_TX/Inv_Transmitter.pex.netlist`
- `Receiver_Bypassing_CDR/Receiver.pex.netlist`

They are **Calibre RVE** netlists (`mgc_rve_*`, `mr_pi`, `mr_pp` constructs),
not ngspice-compatible SPICE; the schematic sources use legacy custom
`nshort`/`pshort` models that are not present in the installed sky130_fd_pr
model tree.  Therefore `verification/analog/pex_characterize.py` can consume a
real PEX netlist only after an explicit conversion/model mapping; this
repository does not fabricate or silently translate those devices.

What is implemented:

- `pex_characterize.py --pex-netlist ... --top ... --ports ...` wraps an
  ngspice-compatible extracted subcircuit in the same TX/RX testbench.
- `pex_characterize.py --schematic-lut ... --pex-lut ...` computes delta rows.
- `make pex-compare` and the contract tests are in place.

Next required step: convert the Calibre RVE netlist to a standard SPICE netlist
with the PDK device models (or provide a ngspice-readable post-layout netlist),
then run the schematic/PEX comparison.  No PEX timing/signoff result is claimed
yet.

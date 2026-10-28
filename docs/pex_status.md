# PEX status: Magic extraction flow

The supported flow is now **Magic RC extraction from the existing GDS**, not
translation of the legacy Calibre RVE files.

## Commands

```bash
make magic-pex
make magic-pex-compare
```

`make magic-pex` runs Magic headlessly with the installed `sky130A` tech, reads
the top GDS cell, extracts devices/capacitance, runs `extresist all`, and wraps
the extracted SPICE in a port-ordered subcircuit.  `make magic-pex-compare`
drives the PEX and the converted schematic with the same ngspice testbench and
records delay and waveform deltas.

## Results (this checkout)

| Macro | GDS | Magic counts | PEX result |
|---|---|---|---|
| TX | `Inverter_Based_Tx/Inv_Transmitter.gds` | 39 devices, 317 R, 6 C | rise 58.7 ps, fall 53.3 ps at 50 fF |
| RX | `Receiver_Bypassing_CDR/Receiver.gds` | 76 devices, 5170 R, 16 C | simulation completes; waveform compared |

The TX schematic (legacy `nshort`/`pshort` converted to sky130 devices) gives
12.9 ps / 11.6 ps rise/fall in the same testbench, so the extracted RC network
is the dominant difference.  The comparison also reports a delay-aware
cross-correlation lag (TX: 81.8 ps) and the residual after shifting.  The
PEX/schematic comparison JSON is generated at
`build/verify/magic_pex/tx_compare.json` and `rx_compare.json`.

Magic `extresist` may emit slightly different internal node numbering and a
few different series segments between runs; the extracted TX delay metrics
were identical across two runs (58.680 ps rise, 53.316 ps fall).  Comparisons
are therefore defined on measured delay/waveform metrics, not byte-identical
netlists.

The generated PEX files are ignored build artifacts.  They contain no model
library; `magic_pex_compare.py` adds the PDK ngspice library
(`sky130.lib.spice <corner>`) so nominal and MC runs share models.

## Limits

- The Magic extraction is **RC extraction**, not a DRC/LVS signoff.  The
  original GDS has not been re-DRC/re-LVS checked in this project.
- Floating-cap annotations (`**FLOATING`) from Magic are stripped because
  ngspice treats `**` as a continuation.
- The RX comparison is a repeated waveform/delay comparison, not a timing
  signoff; its clocked testbench is intentionally simple.
- Calibre RVE files remain in the repository as historical inputs and are not
  silently translated.

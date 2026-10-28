# Mixed-signal PEX chain (D start)

`verification/analog/mixed_signal_e2e.py` runs a transistor/RC transient:

```text
PWL bit stream
  -> TX Magic RC PEX (Inv_Transmitter)
  -> external R/C channel (100 ohm, 16 pF)
  -> RX Magic RC PEX (Resist_FB_INV)
  -> thresholded output samples
```

This is a first mixed-signal integration step, not the digital CDR/top-level
SoC E2E.  It checks:

- TX PEX polarity (inverting)
- RX feedback macro polarity (inverting; the two inversions restore the bit)
- signal swing through the extracted RC networks
- bit decisions after a simple channel

Current 8-bit, 100 Mb/s result:

```text
01011001 -> 0/8 errors, BER=0, best sample offset 0 ns
```

Run:

```bash
make mixed-signal-e2e
```

Limitations: no package/channel S-parameter, no CTLE/DFE, no CDR/digital
recovery, no PVT/MC sweep in this chain, and no post-route/silicon claim.  The
next step is to feed the sliced RX PEX output into the digital receive bench and
add PVT/MC samples.

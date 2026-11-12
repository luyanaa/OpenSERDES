# Legacy analog and physical evidence

These directories were moved from the repository root to keep the canonical
tree clean.  They are **read-only provenance**: the original GDS, SPICE, PEX,
LVS, PDF and screenshot artifacts for the analog blocks that predate the
current `rtl/` and `analog/macros/` flows.

| directory | original content |
|---|---|
| `Inverter_Based_Tx/` | inverter-based TX chain, full-TX schematic and PEX |
| `OverSampling_CDR/` | 4x oversampling clock recovery GDS/SPICE/PEX/LVS |
| `Resistive_FB_inverter/` | resistive-feedback RX inverter GDS/SPICE/LVS |
| `Receiver_Bypassing_CDR/` | receiver-bypassing CDR GDS/SPICE/PEX/LVS |

Use rules:

- Do not add these files to a new synthesis, lint or simulation run.
- The canonical digital implementation is `rtl/`; the current analog macros
  are under `analog/macros/` (verified PCell macros with their own manifests).
- The PEX/LVS/DRC reports here are historical evidence, not a signoff claim
  for any current PDK flow.

# Library catalog

Generated from /grid by `scripts/library_catalog.py`.  Artifact presence
is not a signoff claim; see `docs/library_selection.md`.

| Library | Tracks | Available | Cells | DFF | NAND | DRC/LVS evidence | Note |
|---|---:|---|---:|---|---|---|---|
| `gf180_3v3_as_7t` | 7T | yes | 117 | 5 | 10 | no | AS WiP; DRC/LVS reported in docs/library_selection.md |
| `gf180_3v3_ocd_12t` | 12T | yes | 44 | 3 | 1 | no | OCD; 18-cell library, per-cell Liberty |
| `gf180_3v3_osu_12t` | 12T | yes | 46 | 3 | 1 | no | OSU; DRC clean, LVS blocked in docs/library_selection.md |
| `gf180_3v3_osu_9t` | 9T | yes | 45 | 3 | 1 | no | OSU 9T 3.3 V; no foundry mcu9t3v3 directory found |
| `sky130_fd_sc_hdll` | 9T | no | - | - | - | - |  |
| `sky130_fd_sc_lp` | 9T | no | - | - | - | - |  |
| `sky130_fd_sc_ls` | 9T | no | - | - | - | - |  |
| `sky130_fd_sc_ms` | 11T | no | - | - | - | - |  |
| `sky130_as_sc_hs` | 9T | yes | 81 | 7 | 5 | no | AS WiP; no async-reset DFF in core set |
| `sky130_osu_sc_t12` | 12T | yes | 174 | 12 | 6 | no | OSU 12T |
| `sky130_osu_sc_t15` | 15T | yes | 174 | 12 | 6 | no | OSU 15T |
| `sky130_osu_sc_t18` | 18T | yes | 174 | 12 | 6 | no | OSU 18T |
| `ihp_ez130_8t` | 8T | yes | 216 | 12 | 12 | no | EZ130 8T; no delivered DRC/LVS report in package |

## Notes

- Official Sky130 `hdll/lp/ls/ms` repos under /grid contain tech/model data
  but no complete Liberty/LEF/GDS/Verilog cell set; they are not directly
  usable here.
- No foundry GF180 3.3 V 9T library was found; `gf180mcu_osu_sc_gp9t3v3`
  is the only 9T 3.3 V candidate.
- GF180 3.3 V 7T/12T candidates are evaluation libraries; OSU LVS is
  blocked by the model/process mismatch documented in library_selection.
- Sky130 OSU 12T/15T/18T and IHP EZ130 8T need library-specific DRC/LVS
  and reset-cell checks before use.
- DFF/NAND presence counts modules, not characterized cells.

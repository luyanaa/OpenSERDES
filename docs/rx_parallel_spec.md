# Quarter-rate parallel RX — structure contract

Status: pre-RTL contract, 2026-11-25.  Companion to `docs/pma_arch.md` P2.
The parallel RX is a **rewrite**, not an extension of `cdr_bang_bang` /
`cdr_multiphase` — see that doc for the recon detail.  This file fixes the
interfaces before RTL so the lanes (sampler bank / BBPD / decimated loop /
gearbox) can be written independently.

## Block structure (2 Gb/s, quarter-rate, 8-phase)

```
                        multiphase clock (8 phases @ 500 MHz)
                                   |
        DATA in --> [ sampler_bank ] --> 8 samples/cycle
                                   |        (4 data D[3:0] + 4 edge E[3:0])
                    +--------------+--------------+
                    |                             |
            [ cdr_parallel_bbpd ]         [ rx_parallel_gearbox ]
            vote-merge 4 early/late         reorder 4 bits/UI,
            -> vote[2:0]                    align to word boundary
                    |                             |
            [ cdr_decimated_loop ]          [ rx_parallel_align ]
            KP/KI at 1/16 or 1/32 UI        deskew, elastic buffer
                    |
             phase_ctrl -> PI / tap walk (back to clock/PI)
```

## Interfaces

### `sampler_bank` (PMA leaf, per-PDK swappable)
```
input  wire        data_in;          // serial NRZ
input  wire [7:0]  clk_ph;           // 8 phases, 45° apart @ 500 MHz
output reg  [3:0]  data_q;           // samples on the 4 "data" phases
output reg  [3:0]  edge_q;           // samples on the 4 "edge" phases
// data phases = clk_ph[0,2,4,6]; edge = clk_ph[1,3,5,7] (UI/2 offset)
```
Each leaf is one sampler (catalog entry: stdcell DFF v1, TG-latch /
StrongARM later).  `*_q` registered in the 500 MHz domain, valid for one
quarter-rate cycle (4 UI).

### `cdr_parallel_bbpd`
```
input  wire [3:0]  data_q, edge_q;   // from sampler_bank
input  wire        clk_q;            // quarter-rate (500 MHz) loop clock
input  wire        rst_n, enable;
output reg  signed [2:0] vote;       // sum of 4 early/late votes, -4..+4
output reg         vote_valid;       // this quarter-rate cycle had a vote
```
Per-UI vote: `vote_ui[i] = early_i ? +1 : -1` where `early_i` compares
`edge_q[i]` against `data_q` neighbours (Alexander: edge==prev_data → early).
`vote = Σ vote_ui[i]` for the UI inside this cycle that had a transition.
`vote_valid` only when ≥1 transition in the 4-UI group (drought → no update).
**Sign convention**: OpenSERDES `early=+1` → *delay* phase (do NOT copy adpll
`alexander_pd` — its sign is opposite).

### `cdr_decimated_loop`
```
input  wire        clk_q;            // quarter-rate loop clock
input  wire        rst_n, enable;
input  wire signed [2:0] vote;
input  wire        vote_valid;
input  wire [PH_W-1:0] phase_init;
output reg  [PH_W-1:0] pi_code;      // phase-interpolator code to clock gen
output reg         lock, lock_stale;
output reg  [FRAC:0] freq_trim;
```
Reuses `cdr_bang_bang`'s integral/proportional math + sign, `KI=1` step,
`KP` scaling, FMAX clamp, leak/lock/stale counters — but re-derives
KP/KI/latency for decimation: the loop updates once per quarter-rate cycle
= once per 4 UI (or per `DECIM` UI), so effective loop bandwidth is the
single-sample BW divided by the decimation factor.  `DECIM` is a param;
16-UI update → 125 MHz, 32-UI → 62.5 MHz.

The phase state is a fractional word like `cdr_multiphase`'s
`ph_q[FREQ_FRAC+PH_W-1:0]`; `pi_code` = top `PH_W` bits walked by the loop.
Elastic correction to the gearbox handles PI wrap (code rollover = ±UI
boundary shift) and independent-ref ppm.

### `rx_parallel_gearbox` + `rx_parallel_align`
```
in:  data_q[3:0] (4 bits/cycle), clk_q, rst_n, a phase-slip/align flag
out: rx_word[DATA_W-1:0] on the word clock + word_valid
```
Collects the 4-UI group into a word, orders by which phase/UI arrived
first, slips when the PI wraps (boundary moves ±1 UI).  Elastic buffer
absorbs the independent-ref ppm drift the decimated loop leaves residual.

## Open items for the RTL pass
- Exact Alexander mapping inside the 4-UI group (which `data_q`/`edge_q`
  index pairs form each early/late decision) — derive from the 45° phase
  layout so data phases sit mid-UI and edge phases on boundaries.
- `DECIM` default (start 16 → 125 MHz) and the KP/KI re-derivation table.
- Whether the PI code walks the *sampling phases* (re-time `clk_ph`) or an
  interpolator between two adjacent DCO phases — pick when the
  multiphase_clock block lands; the interface above is agnostic.
- Pattern-3 acceptance: the decimated loop must still lock + 0 errors on
  `tb_cdr_multiphase` pattern 3 (6% transition density).  Vote-valid gating
  must not stall the loop during droughts (leak/lock counters already exist).

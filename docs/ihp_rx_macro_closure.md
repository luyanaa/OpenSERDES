# IHP SG13G2 RX nonlinear-feedback macro closure

Status: **PASS for the bounded IHP SG13G2 macro task**, using the installed
IHP SG13G2 PDK only.

This lane builds and verifies a hierarchical macro with the same electrical
topology as the sky130 `rx_fb_pcell`:

* 3 CMOS inverters (3 NMOS + 3 PMOS)
* 3 extra parallel PMOS (source VDD, drain OUT, gate IN)
* 2 series feedback PMOS from OUT to IN
  (`OUT -> fb1 S=G=OUT/D=net06 -> fb0 S=G=net06/D=IN`)
* ports: `VSS VDD IN OUT`
* NMOS bodies tied to VSS (PDK p-substrate guard ring/tap)
* PMOS bodies tied to VDD (PDK n-well guard ring/tie)

It is a macro DRC/LVS/PEX/model-binding closure, not full-chip, foundry, or
tape-out signoff.  No sky130, gf180, M3, Makefile, or `pya_pcell*` file was
changed.

Reproduce with:

```sh
cd /opt/OpenSERDES
IHP_ROOT=/home/yanlu/.ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008 \
  python3 verification/analog/ihp_rx_macro.py --install-osdi
```

## Results

| Gate | Result |
|---|---|
| GDS built from `SG13_dev` PCells, no sky130 geometry | pass |
| Magic DRC with `ihp-sg13g2.magicrc` | **0 errors** |
| Netgen LVS vs independent schematic | **`Circuits match uniquely.`** |
| Top-level instances / nets | **8 / 5** |
| Body ports present on all device classes | **yes** (`S D G B`) |
| Magic PEX `extract; extresist all; ext2spice extresist on` | ran, 11 device instances (3 NMOS + 8 PMOS), R/C emitted |
| Negative control (remove extra-PMOS VDD body strap) | **LVS fails** (rejected) |
| ngspice OSDI PSP103 DC operating point | **pass** |

The JSON `checks` block is all true:

```json
{
  "drc_clean": true,
  "lvs_match": true,
  "lvs_8_instances": true,
  "lvs_5_nets": true,
  "body_ports_present": true,
  "pex_ran": true,
  "pex_has_r_and_c": true,
  "negative_rejected": true,
  "model_dc": true
}
```

## Layout construction

The GDS is hierarchical, which is what makes the acceptance count of 8
instances possible:

```
rx_macro (top, ports VSS VDD IN OUT)
  ├─ rx_inv          x3   (each: 1 NMOS + 1 PMOS)
  ├─ rx_pmos_extra   x3   (1 PMOS each)
  └─ rx_pmos_fb      x2   (1 PMOS each)
```

Each leaf device is an `SG13_dev` PCell.  The sizes are:

| Device | W | L | Body tie |
|---|---|---|---|
| `rx_inv` NMOS | 5.0 µm | 0.13 µm | p-substrate guard ring to VSS |
| `rx_inv` PMOS | 5.0 µm | 0.13 µm | n-well guard ring to VDD |
| `rx_pmos_extra` PMOS | 5.0 µm | 0.13 µm | n-well guard ring to VDD |
| `rx_pmos_fb` PMOS | 0.55 µm | 2.0 µm | n-well guard ring to VDD |

Every leaf gets the PDK guard ring (`guardRingType=psub` for NMOS,
`guardRingType=nwell` for PMOS, `guardRingDistance=1u`) and a contacted body
tie.  The PCell gate has no contact, so the builder adds a local GatPoly pad,
`Cont`, Metal1 pad, `Via1`, Metal2 pad and port label for `G`.  S/D and body
terminals get Metal1 pads, `Via1`, Metal2 pads and `S`/`D`/`B` labels.  The
body is never `bulk=None`.

Routing is a regular tile scheme:

* Device terminals are brought out on Metal2 vertical stubs.
* Tile-local Metal3 buses collect `VSS`, `VDD`, `IN`, `OUT` (and `SG`/`D` for
  the feedback tile).
* Metal4 trunks bring each tile net to labelled Metal4 pin pads at the tile
  top edge.
* The top cell places the 8 tile instances and connects their Metal4 pin pads
  to five Metal5 buses (`VSS`, `VDD`, `IN`, `OUT`, internal `net06`) through
  `Via4`.
* Top-level labels are on Metal5.pin for `VSS`, `VDD`, `IN`, `OUT`; `net06`
  is intentionally left unlabeled so it stays an internal net.

GDS bbox: `(-8000,-7500;112000,24500)` nm, i.e. about **120 µm × 32 µm**.
Generated view: `build/verify/ihp_rx_macro/rx_macro.gds`.

## Exact verifier flow

The Python driver performs, per run:

1. **KLayout** (`klayout -zz -r ...`) imports `sg13g2_pycell_lib`, sets
   `layout.technology_name='sg13g2'`, builds the hierarchy and writes the GDS.
2. **Magic DRC + extraction**:
   ```tcl
   gds read <gds>
   load rx_macro -dereference
   select top cell
   drc on
   drc check
   drc count
   extract
   ext2spice lvs
   ext2spice extresist off
   ext2spice -o rx_macro_lvs.spice
   ```
   with
   `PDK_ROOT=$IHP_ROOT magic -dnull -noconsole -rcfile $IHP_PDK/libs.tech/magic/ihp-sg13g2.magicrc`.
3. **Netgen LVS** via the Netgen 1.5.3xx `netgen::lvs` form:
   ```tcl
   netgen::lvs [list rx_macro_lvs.spice rx_macro] \
               [list rx_macro_schematic.cir rx_macro] \
               $IHP_PDK/libs.tech/netgen/ihp-sg13g2_setup.tcl
   ```
   The schematic is independent of the extracted netlist
   (`build/verify/ihp_rx_macro/rx_macro.cdl` and `work/rx_macro_schematic.cir`).
4. **Magic PEX** in a fresh process:
   ```tcl
   extract
   extresist all
   ext2spice extresist on
   ext2spice rthresh 0
   ext2spice cthresh 0
   ext2spice -o rx_macro_pex.spice
   ```
5. **Negative control**: rebuild with the VDD body strap removed from
   `rx_pmos_extra`; extract and LVS with the same schematic.
6. **ngspice DC**: concatenate the PDK model lib, the extracted LVS netlist,
   `VDD`/`VSS`/`VIN` sources and `Xdut`, then run `.op`.

## LVS detail

Extracted top-level summary:

```
Circuit rx_macro contains 8 device instances.
  Class: rx_inv          instances: 3
  Class: rx_pmos_extra   instances: 3
  Class: rx_pmos_fb      instances: 2
Circuit contains 5 nets.
```

Netgen final result: `Circuits match uniquely.`

Device leaf subcircuits all expose body ports:

```
rx_nmos_w5l013   S D G B
rx_pmos_w5l013   S D G B
rx_pmos_w055l20  S D G B
```

This is the required body-port evidence.  The flattened device count is 11
(3 NMOS + 8 PMOS); the hierarchical top-level instance count is 8.

## PEX detail (with an explicit caveat)

Magic `extresist` reports 11 device instances (3 NMOS + 8 PMOS) and actual
R/C counts.  On the recorded run:

* run 1: **55 resistors, 26 capacitors**, 16 caps marked `**FLOATING`
* run 2: **25 resistors, 24 capacitors**, 14 caps marked `**FLOATING`

The two runs use the **same GDS** and the same command sequence.  The exact
node/R/C count varies widely because Magic `extresist` net/node ordering is
not stable across invocations (repeated trial runs on this GDS ranged from
about 14 to 76 resistors).  The delivered `rx_macro.pex.spice` is run 1; both
runs are recorded in the JSON.  This is PEX command/device/body evidence, not
a claim of deterministic full-parasitic signoff.

Five of the 11 PEX device lines have `ad=0`/`as=0` after Magic's splitting;
that is recorded exactly and not hidden.  PEX is a flat netlist, so parasitic
nodes such as `VSS.n0`…`VSS.n13` appear in addition to the five functional
nets.

## Negative control

The negative build removes the `VDD` body-tie Metal2 strap in the
`rx_pmos_extra` tile.  The three extra PMOS n-wells then float.  Magic extracts
an internal `rx_pmos_w5l013_0/B` node and the tile subcircuit has more nets
than the reference; Netgen reports:

```
Circuit 1 contains 8 devices, Circuit 2 contains 8 devices.
Circuit 1 contains 8 nets,    Circuit 2 contains 5 nets. *** MISMATCH ***
Final result: Netlists do not match.
```

So the negative control is rejected, as required.  Evidence:
`work/rx_macro_negative_*`.

## Model DC

OSDI PSP103 models were already generated in the installed PDK
(`libs.tech/ngspice/osdi/{psp103,psp103_nqs,r3_cmc,mosvar}.osdi`, plus
`~/.spiceinit`) using OpenVAF 23.5.0; the driver can regenerate them with
`--install-osdi`.

DC result:

* `V(OUT) = 0.620552 V`
* `I(VDD) = -3.90074e-4 A` (≈ -390 µA)
* `I(VIN) = 2.320842e-11 A`

The two optional MOM-cap OSDI libraries (`cap_cmomi`, `cap_cmomf`) are absent;
ngspice prints non-fatal start-up warnings, retained in
`work/rx_macro_model_dc.log` and the JSON `osdi_warnings`.  They do not block
the PSP103 MOS DC result.

## Limitations / non-claims

1. **Not full-chip or tape-out signoff.** This is one macro at one operating
   point and one schematic topology.
2. **PEX R/C counts are not deterministic** across Magic invocations; both
   observed runs are reported.
3. **PEX contains zero-area split devices** and floating caps; these are
   reported, not cleaned up or interpreted as physical devices.
4. **No LEF view was generated.** The deliverable uses GDS + independent
   CDL/schematic (the requested "LEF/CDL or schematic" option).
5. **The macro is not area-optimized.** Tiles use generous 15 µm pitch and
   guard rings; bbox is about 120 µm × 32 µm.
6. **PDK body-tie structures are explicit layout additions** (guard rings and
   gate/S/D pads), not hidden simulator settings.

## Evidence

Everything is under `build/verify/ihp_rx_macro/`:

* `ihp_rx_macro.json` — commands, return codes, DRC count, LVS match/instances/
  nets/classes, PEX runs, negative control, model DC, hashes
* `rx_macro.gds` — macro GDS
* `rx_macro.cdl` — independent schematic/CDL
* `rx_macro.pex.spice` — PEX run 1
* `work/` — KLayout build script/log, Magic TCL/logs/extracts, Netgen
  scripts/logs, PEX run 2, negative-control GDS/netlists, ngspice deck/log

## Files changed by this lane

* `verification/analog/ihp_rx_macro.py` — new headless macro builder/verifier
* `verification/analog/tech/ihp_sg13g2.yaml` — added `macro.rx_fb_pcell`
  status/results; `analog_lut_status: scaffold` retained
* `docs/ihp_rx_macro_closure.md` — this document
* `build/verify/ihp_rx_macro/` — generated evidence

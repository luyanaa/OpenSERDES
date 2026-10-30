# IHP SG13G2 device-level closure (LV NMOS / PMOS)

Status: **device-level closure passed for `sg13_lv_nmos` and `sg13_lv_pmos`**
in the installed IHP SG13G2 PDK at

```
IHP_ROOT=/home/yanlu/.ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008
IHP_PDK=$IHP_ROOT/ihp-sg13g2
```

This is device-level DRC/LVS/PEX/model-binding evidence, **not** a full-chip,
macro, or tape-out signoff. It is bounded to the two LV 1.2 V MOS devices; it
does not touch sky130, gf180MCU, the M3 lane, the Makefile, or any
`pya_pcell*` module.

Reproduce with:

```sh
cd /opt/OpenSERDES
IHP_ROOT=/home/yanlu/.ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008 \
  python3 verification/analog/ihp_device_verify.py --install-osdi
```

Evidence is written to `build/verify/ihp_device/`:

* `ihp_device_verify.json` — machine-readable record (commands, return codes,
  counts, hashes, logs, model results)
* `logs/` — KLayout PCell-generation logs
* `work/` — GDS, Magic TCL, extracted SPICE, netgen scripts/logs, model decks

## Results

| Check | NMOS | PMOS |
|---|---|---|
| Device / model class | `sg13_lv_nmos` | `sg13_lv_pmos` |
| Magic DRC errors | 0 | 0 |
| Magic extraction | `sg13_lv_nmos`, 4 ports | `sg13_lv_pmos`, 4 ports |
| Netgen LVS | `Circuits match uniquely.` | `Circuits match uniquely.` |
| Extracted ports | `S D G sub` | `S D b G` |
| PEX resistors | 0 | 2 |
| PEX capacitors | 6 | 11 (1 zero-valued, marked `**FLOATING`) |
| Model DC (OSDI PSP103) | pass, I(VDD) = -24.022 µA | pass, I(VDD) = -16.836 µA |
| Negative control (drop port label) | rejected (LVS fails) | rejected (LVS fails) |

The JSON `checks` block is all true and `status` is `pass`:

```json
{
  "drc_clean": true,
  "lvs_match": true,
  "negative_controls_rejected": true,
  "pex_ran": true,
  "device_classes_ok": true,
  "model_dc": true
}
```

## What was actually run

Tool versions recorded in the JSON:

* KLayout 0.30.0
* Magic 8.3 revision 685
* Netgen 1.5.325
* ngspice-44.2

For each device the script performs these real commands.

### 1. Layout generation (KLayout + PDK PCells)

```sh
KLAYOUT_PATH=$IHP_PDK/libs.tech/klayout \
  klayout -zz -r build/verify/ihp_device/work/<dev>_make.py
```

The generated script imports `sg13g2_pycell_lib`, sets
`layout.technology_name='sg13g2'`, and instantiates `SG13_dev` `nmos` / `pmos`
with unit-string parameters `{'l':'0.13u','w':'1u','ng':1}`. Text labels are
placed on `Metal1.pin` (8/2) and `GatPoly.pin` (5/2); the device cell itself is
labelled, then wrapped in a `TOP` cell.

### 2. DRC and extraction (Magic, SG13G2 deck)

```sh
PDK_ROOT=$IHP_ROOT magic -dnull -noconsole \
  -rcfile $IHP_PDK/libs.tech/magic/ihp-sg13g2.magicrc \
  build/verify/ihp_device/work/<dev>_magic_lvs.tcl
```

The TCL script does:

```tcl
gds read <dev>
load TOP -dereference
select top cell
drc on
drc check
drc count
extract
ext2spice lvs
ext2spice extresist off
ext2spice -o <dev>_lvs.spice
```

`TOP` is extracted (not only the device leaf) because that is what promotes the
NMOS substrate node to the `sub` port. The first `.subckt` block is then
trimmed into `<dev>_device_lvs.spice` so Netgen compares the device only and
does not warn about the TOP wrapper instantiating the device.

### 3. LVS (Netgen, SG13G2 setup)

```sh
netgen -batch source build/verify/ihp_device/work/<dev>_netgen_lvs.tcl
```

where the TCL calls the Netgen 1.5.3xx `netgen::lvs` form:

```tcl
netgen::lvs [list <dev>_device_lvs.spice <dev>] \
            [list <dev>_schematic.cir <dev>] \
            $IHP_PDK/libs.tech/netgen/ihp-sg13g2_setup.tcl
```

Schematics compared:

```spice
.subckt nmos S D G sub
X0 D G S sub sg13_lv_nmos w=1u l=0.13u
.ends
```

```spice
.subckt pmos S D G b
X0 D G S b sg13_lv_pmos w=1u l=0.13u
.ends
```

The PDK subckt port order is `d g s b`; the top-level LVS port names above
match the extracted layout names. Both runs end with
`Final result: Circuits match uniquely.`

### 4. PEX (Magic `extresist`)

A **separate** Magic process avoids `extresist` state leaking into the LVS
netlist:

```tcl
gds read <dev>
load TOP -dereference
select top cell
extract
extresist all
ext2spice extresist on
ext2spice rthresh 0
ext2spice cthresh 0
ext2spice -o <dev>_pex.spice
```

The IHP deck `ihp-sg13g2-extract.tech` supplies the `resist` and
`defaultareacap`/`defaultsidewall` rules (for example Metal1 110 Ω/sq and
35.015 aF/µm² area capacitance); no separate rcfile is required beyond
`ihp-sg13g2.magicrc`.

Exact PEX results:

* NMOS: **0 resistors, 6 capacitors**, flat top-level netlist.
  Magic reported `Total Nets: 4; Nets extracted: 3; Nets output: 0`.
* PMOS: **2 resistors, 11 capacitors** (one zero-valued floating cap),
  flat top-level netlist. Magic reported `Total Nets: 5; Nets extracted: 4;
  Nets output: 1`.

The PMOS resistors come from the short Metal1 source strap:

```spice
R0 pmos_0/S pmos_0/S.t0 17.0005
R1 pmos_0/S.t0 pmos_0/S 8.56123
```

The NMOS strap did **not** produce `extresist` resistors on this
substrate-based device; only overlap/substrate capacitors were emitted. That
is reported as zero, not hidden. The NMOS result is therefore device
extraction plus PEX command/capacitance evidence, **not** full resistive
parasitic closure.

### 5. Model DC (OSDI PSP103)

The PDK OSDI models were missing. They were generated with the PDK installer
using the available OpenVAF binary:

```sh
mkdir -p /tmp/opencode/bin
ln -sf /opt/openvaf /tmp/opencode/bin/openvaf
PATH=/tmp/opencode/bin:$PATH PDK_ROOT=$IHP_ROOT \
  python3 $IHP_PDK/libs.tech/ngspice/install.py
```

Recorded side effects (outside this repository):

* created `$IHP_PDK/libs.tech/ngspice/osdi/{psp103,psp103_nqs,r3_cmc,mosvar}.osdi`
* created `~/.spiceinit` → `$IHP_PDK/libs.tech/ngspice/.spiceinit`

The verification script can perform this automatically with `--install-osdi`;
on the recorded run the models were already present, so the installer was not
re-run.

DC checks:

```spice
.lib $IHP_PDK/libs.tech/ngspice/models/cornerMOSlv.lib mos_tt
VDD d 0 1.2
VG g 0 0.6
VB b 0 0
X1 d g 0 b sg13_lv_nmos w=1u l=0.13u
.op
```

and the PMOS analogue with source at 1.2 V and bulk tied to source. Both
ngspice runs return a finite operating point:

* NMOS `v(d)=1.2 V`, `i(vdd)=-2.40222e-05 A`
* PMOS `v(s)=1.2 V`, `i(vdd)=-1.68357e-05 A`

Note: `~/.spiceinit` also references `cap_cmomi.osdi` and `cap_cmomf.osdi`,
which `install.py` does not build. ngspice prints start-up errors for those two
optional MOM-cap libraries. They do not affect the PSP103 MOS DC result; the
warnings are retained in `work/<dev>_model.log` and in the JSON
`osdi_warnings` field.

## Device-specific engineering notes

### NMOS body

The SG13G2 NMOS body is the global substrate. Magic exposes it as the `sub`
port when `TOP` is extracted. No body label is required in the GDS.

### PMOS body / n-well

The bare `SG13_dev` `pmos` PCell leaves the n-well as an internal extracted
node (`w_...`/`well`), so a 3-port netlist would not bind the body. This lane
therefore instantiates the PDK PCell with `guardRingType='nwell'` and
`guardRingDistance='1u'`, then labels the contacted Metal1 guard-ring segment
`b`. The resulting extracted 4-port device matches `sg13_lv_pmos` with the
`b` port. This is real layout, not a simulated or asserted body connection.

When the `b` label is removed, the negative control extracts
`pmos S D G` and Netgen fails pin matching, as expected.

### Labels and straps

* `Metal1.pin` 8/2: `S`, `D`, `b` (PMOS body ring tie)
* `GatPoly.pin` 5/2: `G`
* A 0.4 µm × 0.16 µm Metal1 source strap is added below the source contact
  stack (same source net, no connectivity change) to give `extresist` a wire
  segment and extra node.

### Negative controls

The script regenerates each device without one port label and reruns
extraction/LVS:

* NMOS: drop `S` → extracted ports `D G sub`; LVS fails.
* PMOS: drop `b` → extracted ports `S D G`; LVS fails.

Both are recorded as `rejected: true` in the JSON.

## Blockers and limits (honest scope)

1. **Not full parasitic closure for NMOS.** PEX emitted 6 capacitors and 0
   resistors. The source strap did not create a resistive segment on this
   substrate-based device. Do not claim resistive PEX signoff for NMOS.
2. **PMOS PEX has a zero-valued floating capacitor** (`C6 ... 0 **FLOATING`)
   and is a flat top-level netlist; counts include it.
3. **PMOS body requires the guard ring.** A minimal bare PCell does not expose
   the n-well as a port. This is an additive body-tie structure, not the raw
   PCell geometry.
4. **OSDI warnings for absent MOM-cap models.** The PSP103 MOS models load and
   simulate; the two optional cap OSDI libraries are missing and warn.
5. **PCell generation is technology-name dependent.** The script sets
   `layout.technology_name='sg13g2'` and `KLAYOUT_PATH`; cloning the flow
   without those settings is not equivalent.
6. **No full-chip / macro / tape-out claim.** Only one `nmos` and one `pmos`
   at `w=1u`, `l=0.13u`, `ng=1` were verified.

## Files changed by this lane

* `verification/analog/ihp_device_verify.py` — new bounded verifier
* `verification/analog/tech/ihp_sg13g2.yaml` — real device/model/PEX metadata
* `docs/ihp_device_closure.md` — this document
* `build/verify/ihp_device/` — generated evidence

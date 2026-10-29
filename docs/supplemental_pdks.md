# Supplemental PDK probes: FreePDK45 and ETRI050

These PDKs supplement the Sky130/gf180mcuD/IHP/ICsprout55 PCell work.  Neither
probe is a signoff claim.

## FreePDK45 / Nangate45

Installed under:

```text
~/.ciel/ciel/freepdk45/versions/784b1a48.../freepdk45
```

`libs.ref/nangate45` contains the NangateOpenCellLibrary GDS/CDL/Liberty/LEF.
FreePDK45 is explicitly a **research, non-manufacturable** PDK.  The installed
tree has no KLayout device PCell; it is a standard-cell/model supplement, not a
new PCell backend.

Model smoke:

```sh
python3 verification/analog/freepdk45_probe.py
```

Result:

```text
ngspice INV DC transfer
Vin=0.0 -> Vout=0.9999977 V
Vin=1.0 -> Vout=9.568614e-06 V
inverting: true
```

The KLayout FreePDK45 DRC deck was also exercised on the shipped `INV.gds`
example.  It reported four `ACTIVE.1` items, so that example is **not** called
clean.  This is retained as a DRC diagnostic, not a signoff result.

Config:

```text
verification/analog/tech/freepdk45.yaml   status=scaffold
```

## ETRI050

Installed under:

```text
/grid/ETRI050
```

ETRI050 is a 0.5 um analog CMOS, 2P3M, 5 V NSPL process.  Its installed model
set provides `nmos4`, `pmos4`, diodes, resistors, capacitors, and selected
BJTs.  The repository probe uses only `nmos4`/`pmos4` for a model smoke:

```sh
python3 verification/analog/etri050_probe.py
```

Result:

```text
ngspice inverter DC transfer, VDD=5 V
Vin=0 V -> Vout=5 V
Vin=5 V -> Vout=2.762712e-09 V
inverting: true
```

This establishes model usability only.  ETRI050's KLayout DRC/LVS decks and its
0.5 um PCell/standard-cell work are separate; no ETRI050 PCell is installed in
this repository.  The 5 V process is not interchangeable with the 1.0/1.2/1.8 V
high-speed links.

Config:

```text
verification/analog/tech/etri050.yaml   status=scaffold
```

## Status

| PDK | Role | PCell | Model smoke | DRC/LVS signoff |
|---|---|---|---|---|
| FreePDK45 | Nangate45 standard-cell/model supplement | no | pass | not claimed |
| ETRI050 | 0.5 um 5 V analog model supplement | no | pass | not claimed |

The previously tested ICsprout55 LVS item is intentionally ignored per the
current scope.  ICsprout55 remains a geometry-probed PCell PDK, not a signoff
PDK.

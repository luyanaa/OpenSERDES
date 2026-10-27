# PDK environment variables

The tech profiles are portable: no PDK install path is stored in the YAML.
They use two environment variables, expanded by
`scripts/gen_librelane_config.py`:

| Variable | Meaning |
|---|---|
| `PDK_ROOT` | Directory passed to LibreLane as `--pdk-root`. |
| `PDK_LIB_ROOT` | Directory containing the PDK's `libs.ref` tree. |

For sky130 and gf180mcuD the two are usually the same:

```bash
export PDK_ROOT=/path/to/sky130/versions/<version>
export PDK_LIB_ROOT=$PDK_ROOT

export PDK_ROOT=/path/to/gf180mcu/versions/<version>
export PDK_LIB_ROOT=$PDK_ROOT
```

For IHP and icsprout55 the LibreLane overlay and the library tree can differ:

```bash
# IHP: PDK_ROOT is the overlay/PDK root, PDK_LIB_ROOT is the version tree
# containing ihp-sg13g2/libs.ref.
export PDK_ROOT=/path/to/ihp-pdk-root
export PDK_LIB_ROOT=/path/to/ihp-sg13/versions/<version>

# icsprout55: PDK_ROOT is the LibreLane overlay, PDK_LIB_ROOT is the
# icsprout55-pdk tree containing libs.ref.
export PDK_ROOT=/path/to/ics-pdk-overlay
export PDK_LIB_ROOT=/path/to/icsprout55-pdk
```

`PDK_LIB_ROOT` defaults to `PDK_ROOT` when unset.  The generator fails with a
clear error if a `${...}` placeholder remains unresolved.  Command-line
overrides are available:

```bash
python3 scripts/gen_librelane_config.py \
  --tech sky130 --design serializer --period 10 \
  --pdk-root "$PDK_ROOT" --pdk-lib-root "$PDK_LIB_ROOT" \
  --out /tmp/serializer.yaml
```

The generated YAML contains expanded absolute paths; it is a run artifact, not
a portable profile.  The portable inputs are the `tech/*/tech.yaml` files plus
the environment above.

## Architecture database

`tech/arch_db.yaml` is the candidate database used by `scripts/gen_ip.py`.  It
records, per TX/RX architecture:

- module and parameter mapping (`capture_mode`, `cdr_mode`);
- capability gates (`max_ratio_bits`, `max_line_rate_mbps`, `min_oversample`,
  `supports_link`);
- optional `tech_overrides` that replace the max line rate (and optionally
  metrics) for a specific PDK before selection;
- relative area/power/latency metrics and a confidence label.

The metrics are not signoff data.  Replace them with measured post-route PPA as
the qualification database grows.  Measured overrides live in
`tech/ppa_measured.yaml` (example: `tech/ppa_measured.example.yaml`) and are
imported with `scripts/import_ppa.py`; the generator records the selected
candidate, the Pareto frontier, the PPA source and the confidence in
`manifest.json`.

## Spec-driven generator

The product entry point is `scripts/gen_ip.py`, not the legacy
`gen_librelane_config.py`.  It reads `spec/*.yaml`, applies the capability gates,
selects an architecture and emits a self-contained package:

```bash
python3 scripts/gen_ip.py --spec spec/default.yaml --out out/default --force
python3 scripts/gen_ip.py --spec spec/default.yaml --check
```

When `PDK_ROOT`/`PDK_LIB_ROOT` are not set, the package keeps `${...}` paths and
records `tech.pdk_paths_resolved: false` in `manifest.json`.  This is deliberate:
the package can be generated in CI without a PDK install, but PnR must resolve
the paths before running.

`--scl` is validated against `scl_variants`; unsupported library names are an
error, not a silent fallback to the default liberty files.

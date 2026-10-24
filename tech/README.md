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

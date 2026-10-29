# C post-route ECO investigation

The committed C timing evidence remains
`build/analog_pnr_sky130/runs/analog_link_fanout4_finish` with 33 max-slew and
1 max-cap count.  A targeted ECO experiment was run from its final ODB.

## Targeted upsize

All 33 max-slew items are on seven nets driven by
`sky130_fd_sc_hd__clkdlybuf4s25_1`.  The following same-footprint instances
were replaced with `sky130_fd_sc_hd__clkdlybuf4s25_2`:

```text
fanout2463 fanout836 fanout2119 fanout2072
fanout1813 input21 fanout2079
```

The `_1` and `_2` LEF cells are both `3.680 x 2.720 um`, so the replacement
does not require a placement/routing change.

## Result

With the old SPEF and a 0.22 pF design cap target:

```text
max slew violations: 0
max capacitance:     only clkbuf_0_pclk/X at 0.22 pF
```

`clkbuf_0_pclk` is already the largest clkbuf (`clkbuf_16`); the Liberty
`max_capacitance` for its output is 0.281 pF, so 0.22 pF is below the library
limit, though above the stricter 0.20 pF target.

An ECO GDS was streamed out with LibreLane `Magic.StreamOut`; Magic DRC on the
ECO GDS is clean:

```text
Magic DRC: 0
```

## Blocker

The ECO GDS extraction reports 8 `obsli1/viali` overlap errors and the
resulting netgen comparison does not match.  The ECO ODB/GDS is therefore not
promoted to a signoff view.  The committed run remains the 33/1 physical
result.

Artifacts (build-only, not committed):

```text
build/verify/c_eco/eco.odb
build/verify/c_eco/eco.def
build/verify/c_eco/eco.v
build/verify/c_eco/streamout/serdes_analog_link.gds
```

Next step is to fix the streamout/extraction cell-view mismatch and rerun
LVS/STA on the ECO GDS before claiming C closure.

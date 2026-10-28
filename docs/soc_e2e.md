# Generated-top SoC E2E smoke

`make soc-e2e` compiles the generated package's own filelist and runs
`verification/rtl/tb_soc_e2e.sv` with `serdes_soc_cdc_wrapper` as the simulation
top.

```bash
make soc-e2e PYTHON=/usr/bin/python3 SOC_E2E_PACKAGE=out/ci-soc
```

The runner reads `manifest.json` for `DATA_WIDTH`, `WORDS`, `SYNC_LEN` and
`SYNC_PATTERN`, resolves `rtl/filelist_sv2v.f` (or `filelist.f`), and records
the manifest/filelist hashes and parameters in `build/verify/soc_e2e.json`.

## Coverage

- Four clocks: `pclk`, `s_axis_aclk`, `tx_clk`, `m_axis_aclk`.
- Initial reset of all domains.
- APB scratch write/read and CONTROL enable programming.
- TX AXI4-Stream frame: comma + `WORDS` payload words, through the TX async
  FIFO and serializer.
- Fixed-skew forwarded RX clock (`RX_DELAY_PS=5000`, 5 ns at 100 Mb/s) and
  source-synchronous serial loopback.
- RX core -> RX FIFO -> RX AXI4-Stream, with intermittent `m_axis_tready`
  backpressure.
- Frame scoreboard against the transmitted payload words.
- BIST PRBS loopback and coherent snapshot counters through APB.
- Deliberate RX-FIFO overflow, checked through `STATUS[7]`.
- `STATUS[4]` RX lock and `STATUS[6]` frame lock.

Current result on the 32-bit x 8-word generated package:

```text
SOC_E2E PASS frames=4 expected=4 errors=0 apb_errors=0
status=0x000007d7 bist_errors=0 bist_bits=1006
```

## Limits

- The serial path is an ideal loopback plus fixed forwarded-clock skew; there
  is no channel loss, TX/RX correlated jitter, asynchronous clock drift, or
  clock-data recovery.
- The source-synchronous RX core emits idle frames after the transmitted
  frames; the scoreboard stops at the expected frame count rather than treating
  those idle frames as payload.
- This is generated-RTL integration smoke, not post-route timing, CDC signoff,
  silicon, or full software validation.

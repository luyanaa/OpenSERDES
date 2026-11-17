# PIPE-Lite digital shell (`preset: pipe_lite`)

`preset: pipe_lite` generates `serdes_pipe_lite_top` on top of the Raw PCS.
The manifest carries `interface_class: PIPE_DIGITAL_COMPAT`.

This is **not** a PCIe/USB/SATA PHY.  It is a 16-bit PIPE-style digital
interface for open MAC cores, FPGA bring-up and low-speed ASIC prototypes.
The line rate stays in the digital range (tens to low hundreds of Mb/s), and
the electrical PIPE signals are digital stubs.

## What is implemented

- 16-bit `tx_data` / `rx_data` plus `tx_datak[1:0]` / `rx_datak[1:0]`.
- 8b/10b encode/decode (two symbols per 20-bit Raw PCS word).  The decoder is
  held in reset until the Raw PCS comma locks, so its running disparity starts
  at the first aligned word.
- K-code passthrough (e.g. K28.5 = `16'hBCBC`, `datak=2'b11`).
- `rx_status[2:0]`: `000` normal, `001` SKP, `010` receiver detected,
  `011` 8b10b decode error, `100` elastic overflow, `101` elastic underflow,
  `110` disparity error.
- Elastic buffer with SKP insert/remove (`pipe_elastic_buffer.sv`): insert at
  the low watermark, drop at the high watermark, overflow/underflow status.
- `power_down[1:0]` gates `tx_ready` and the RX word output.
- `tx_detect_rx` returns a 4-cycle `phy_status` pulse and `rx_status=010`.
- Analog hooks: `analog_tx_elec_idle`, `analog_rx_elec_idle`,
  `analog_tx_det_rx` are driven by digital stubs
  (`elec_idle | power_down`, `tx_detect_rx`).  A real PMA can drive the same
  nets later.

`tx_usr_clk = rx_rec_clk = clk / 20` (two 8b10b symbols per word); the
generated SDC declares both with `create_generated_clock -divide_by 20`.

## Example

```bash
python3 scripts/gen_ip.py --spec spec/pipe_lite.yaml --out build/gen/pipe_lite --force
make build/tb_pipe_lite build/tb_pipe_elastic_buffer
./build/tb_pipe_elastic_buffer
./build/tb_pipe_lite
```

`tb_pipe_lite` loops the serial output back, sends a COM (K28.5/K28.5) then
data and K words, checks the recovered data/K and RxStatus, pulses
`tx_detect_rx` and checks PhyStatus/RxStatus=010, gates with `power_down`, and
measures the 200 ns word clock.  `tb_pipe_elastic_buffer` covers overflow,
underflow, high-watermark SKP drop and low-watermark SKP insert.

## Limits

- 16-bit only in this release; 32-bit needs a four-symbol datapath.
- The single-clock top runs the elastic buffer in pass-through (one read per
  decoded word).  The SKP insert/remove path is verified by the dedicated
  unit test with an independent read rate.
- No electrical idle detect, no receiver termination detect, no rate change,
  no P0s/P1/P2 power sequencing beyond the `power_down` gate.
- The qualification label is digital compatibility only.  Do not claim
  PCIe/USB/SATA compliance.

## See also

- `docs/pcs_coding.md` — 8b/10b control symbols, 10-bit comma alignment and the
  64b/66b block-lock path that PIPE-style framers reuse.

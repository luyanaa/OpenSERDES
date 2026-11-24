`timescale 1ns/1ps
`default_nettype none

// -----------------------------------------------------------------------------
// PMA TX driver wrapper.
//
// Abstraction layer in front of the analog driver cell so no digital RTL
// hardcodes a PDK-specific PCell.  The IHP driver is `tx_macro` (a PCell with
// ports VSS VDD IN OUT); the real binding happens at PnR time by defining
// PMA_TX_DRIVER_CELL to that cell name -- this wrapper never instantiates the
// PCell directly.
//
// Sim default: a behavioral zero-delay buffer so digital TBs work without the
// analog model.  Compile-time options:
//   PMA_TX_DRIVER_CELL : macro name of the real driver cell to instantiate
//   PMA_POWER_PINS     : add VDD/VSS ports (matches the tx_macro port list)
// -----------------------------------------------------------------------------
module tx_driver (
    input  wire IN,
    output wire OUT
`ifdef PMA_POWER_PINS
    , input wire VDD
    , input wire VSS
`endif
);

`ifdef PMA_TX_DRIVER_CELL
    // Real driver cell (e.g. the IHP tx_macro PCell: ports VSS VDD IN OUT).
    PMA_TX_DRIVER_CELL u_tx_driver_cell (
        .IN (IN),
        .OUT(OUT)
`ifdef PMA_POWER_PINS
        , .VDD(VDD)
        , .VSS(VSS)
`endif
    );
`else
    // Behavioral stand-in: ideal unity-gain buffer, zero propagation delay.
    // Good enough for functional/BER verification of the digital tree; the
    // analog team's model (or tx_macro) takes over at PnR / mixed-signal sim.
    assign OUT = IN;
`endif

endmodule

`default_nettype wire

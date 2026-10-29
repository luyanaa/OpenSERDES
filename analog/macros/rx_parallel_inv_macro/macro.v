// Canonical black-box view.  The canonical pin order is VDD,VSS,IN,OUT.
// The feedback path is an analog PMOS network, not a synthesized resistor.
(* blackbox *)
module rx_parallel_inv_macro (
    input  wire VDD,
    input  wire VSS,
    input  wire IN,
    output wire OUT
);
endmodule

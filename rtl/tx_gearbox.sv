`timescale 1ns/1ps
`default_nettype none
/* verilator lint_off DECLFILENAME */
// File name kept as-is; the module inside is the generic gearbox stage cell.

// -----------------------------------------------------------------------------
// TX gearbox: optional width ladder feeding the quarter-rate parallel MUX.
//
//   tx_gearbox_stage : one parametric 2N -> N registered halving stage on a
//                      clk / clk2 (2x) clock pair.
//   tx_gearbox       : WIDTH-parameterized chain of stages (32 -> 16 -> 8 -> 4)
//                      using the clk_word / clk_word2 / clk_word4 / clk_word8
//                      ladder.  The must-have in front of tx_parallel_mux is
//                      only the last stage; this ladder is a convenience.
//
// Clock contract (per stage): the slow clock's posedge occurs halfway between
// two fast-clock posedges (clk = clk2 / 2, delayed a quarter clk2 period).
// The 2N-bit word is captured on the slow clock; the two halves are emitted
// on alternate clk2 edges, LSB_FIRST=1 -> lower half first.
//
// The mux tree halves at each stage keep the same LSB-first bit order as the
// final mux, so the serial stream is in_word[0], in_word[1], ... (MSB_FIRST
// swaps the halves per stage; treat v1 as LSB-first only).
// -----------------------------------------------------------------------------
module tx_gearbox_stage #(
    parameter int N = 16,               // output width; input is 2N
    parameter bit LSB_FIRST = 1'b1
) (
    input  wire           clk,          // slow clock: 2N-bit word capture
    input  wire           clk2,         // fast clock (2x clk): half-rate emit
    input  wire           rst_n,
    input  wire [2*N-1:0] in_word,
    output logic [N-1:0]  out_word
);

    // Slow-domain capture registers (one per stage; explicit retiming so the
    // capture edge and the emit edges are separate pipeline stages).
    logic [N-1:0] lo_q;
    logic [N-1:0] hi_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            lo_q <= '0;
            hi_q <= '0;
        end else begin
            lo_q <= in_word[N-1:0];
            hi_q <= in_word[2*N-1:N];
        end
    end

    // Half-select toggle on the fast clock.  Reset value is chosen so the
    // first clk2 edge after a slow-clock capture presents the lower half
    // (LSB_FIRST) / upper half (MSB_FIRST) -- see the header contract.
    logic sel_q;
    always_ff @(posedge clk2 or negedge rst_n) begin
        if (!rst_n)     sel_q <= LSB_FIRST ? 1'b1 : 1'b0;
        else            sel_q <= ~sel_q;
    end

    assign out_word = LSB_FIRST ? (sel_q ? lo_q : hi_q)
                                : (sel_q ? hi_q : lo_q);

endmodule

// WIDTH-parameterized ladder.  WIDTH in {8, 16, 32}; unused ladder clocks may
// be left unconnected by the instantiating generator.
module tx_gearbox #(
    parameter int WIDTH = 32,           // 8, 16 or 32
    parameter bit LSB_FIRST = 1'b1
) (
    input  wire             clk_word,   // slowest capture clock
    input  wire             clk_word2,
    input  wire             clk_word4,
    input  wire             clk_word8,
    input  wire             rst_n,
    input  wire [WIDTH-1:0] in_word,
    output logic [3:0]      out_nibble
);
    localparam int STAGES = (WIDTH <= 4) ? 0 : $clog2(WIDTH / 4);

    generate
        if (STAGES == 0) begin : g_direct
            assign out_nibble = in_word[3:0];
        end else begin : g_stage0
            wire [WIDTH/2-1:0] w1;
            tx_gearbox_stage #(.N(WIDTH/2), .LSB_FIRST(LSB_FIRST)) u_s0 (
                .clk(clk_word), .clk2(clk_word2), .rst_n(rst_n),
                .in_word(in_word), .out_word(w1));
            if (STAGES == 1) begin : g_last1
                assign out_nibble = w1[3:0];
            end else begin : g_stage1
                wire [WIDTH/4-1:0] w2;
                tx_gearbox_stage #(.N(WIDTH/4), .LSB_FIRST(LSB_FIRST)) u_s1 (
                    .clk(clk_word2), .clk2(clk_word4), .rst_n(rst_n),
                    .in_word(w1), .out_word(w2));
                if (STAGES == 2) begin : g_last2
                    assign out_nibble = w2[3:0];
                end else begin : g_stage2
                    wire [3:0] w3;
                    tx_gearbox_stage #(.N(4), .LSB_FIRST(LSB_FIRST)) u_s2 (
                        .clk(clk_word4), .clk2(clk_word8), .rst_n(rst_n),
                        .in_word(w2), .out_word(w3));
                    assign out_nibble = w3;
                end
            end
        end
    endgenerate

endmodule

`default_nettype wire

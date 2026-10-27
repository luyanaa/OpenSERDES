`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Minimal APB-compatible control/status register file.
//
// Register map (word aligned, ADDR_WIDTH >= 3):
//   0 .. NUM_REGS-2 : read/write control/status registers
//   NUM_REGS-1     : read-only status_i
//
// APB is a sampled interface: pready is high whenever psel is high; writes are
// committed on the rising edge at the end of the access phase.  Out-of-range
// addresses return pslverr and do not write.
// -----------------------------------------------------------------------------
module apb_csr #(
    parameter int NUM_REGS   = 8,
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 16
) (
    input  logic                            pclk,
    input  logic                            presetn,
    input  logic [ADDR_WIDTH-1:0]           paddr,
    input  logic                            psel,
    input  logic                            penable,
    input  logic                            pwrite,
    input  logic [DATA_WIDTH-1:0]           pwdata,
    output logic                            pready,
    output logic [DATA_WIDTH-1:0]           prdata,
    output logic                            pslverr,
    output logic [NUM_REGS-1:0][DATA_WIDTH-1:0] regs_o,
    input  logic [DATA_WIDTH-1:0]           status_i
);

    localparam int IDX_W      = (NUM_REGS <= 1) ? 1 : $clog2(NUM_REGS);
    localparam int STATUS_REG = NUM_REGS - 1;

    localparam logic [ADDR_WIDTH-3:0] NUM_REGS_C = (ADDR_WIDTH-2)'(NUM_REGS);
    localparam logic [IDX_W-1:0]      STATUS_C   = IDX_W'(STATUS_REG);

    logic [ADDR_WIDTH-3:0] word_addr;
    logic [IDX_W-1:0]      idx;
    logic                  in_range;
    logic [NUM_REGS-1:0][DATA_WIDTH-1:0] regs_q;

    assign word_addr = paddr[ADDR_WIDTH-1:2];
    assign idx       = word_addr[IDX_W-1:0];
    assign in_range  = (word_addr < NUM_REGS_C);

    assign pready  = psel;
    // This CSR supports word-aligned accesses only.
    assign pslverr = psel && (!in_range || (paddr[1:0] != 2'b00));
    assign prdata  = !in_range ? '0 :
                     (idx == STATUS_C) ? status_i : regs_q[idx];
    assign regs_o  = regs_q;

    always_ff @(posedge pclk or negedge presetn) begin
        if (!presetn) begin
            regs_q <= '0;
        end else if (psel && penable && pwrite && in_range &&
                     (idx != STATUS_C)) begin
            regs_q[idx] <= pwdata;
        end
    end

endmodule

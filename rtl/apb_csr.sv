`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Minimal APB-compatible control/status register file.
//
// Register map (word aligned, ADDR_WIDTH >= 3):
//   0 .. NUM_REGS-1 : registers; STATUS_IDX is read-only via status_i,
//                     RO_MASK[i] selects a read-only register fed by ro_i[i],
//                     all other registers are read/write.
//
// APB is a sampled interface: pready is high whenever psel is high; writes are
// committed on the rising edge at the end of the access phase.  Out-of-range
// addresses return pslverr and do not write.  Read-only registers ignore
// writes and always return their external value.
// -----------------------------------------------------------------------------
module apb_csr #(
    parameter int NUM_REGS   = 8,
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 16,
    // Index of the scalar status register (defaults to the last register).
    parameter int STATUS_IDX = NUM_REGS - 1,
    // Extra read-only registers: bit i returns ro_i[i] and ignores writes.
    parameter logic [NUM_REGS-1:0] RO_MASK = '0
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
    input  logic [DATA_WIDTH-1:0]           status_i,
    input  logic [NUM_REGS-1:0][DATA_WIDTH-1:0] ro_i
);

    localparam int IDX_W      = (NUM_REGS <= 1) ? 1 : $clog2(NUM_REGS);
    localparam int STATUS_REG = (STATUS_IDX < 0) ? 0 :
                                (STATUS_IDX >= NUM_REGS) ? NUM_REGS - 1 : STATUS_IDX;

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
                     (idx == STATUS_C) ? status_i :
                     RO_MASK[idx]      ? ro_i[idx] : regs_q[idx];
    assign regs_o  = regs_q;

    always_ff @(posedge pclk or negedge presetn) begin
        if (!presetn) begin
            regs_q <= '0;
        end else if (psel && penable && pwrite && in_range &&
                     (idx != STATUS_C) && !RO_MASK[idx]) begin
            regs_q[idx] <= pwdata;
        end
    end

endmodule

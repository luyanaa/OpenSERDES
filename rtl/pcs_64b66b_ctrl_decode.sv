`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// 64b/66b control-block decode (IEEE 802.3 clause 49 subset).
//
// A block is classified from its 2-bit sync header and, for control blocks,
// from the block-type byte (payload bits [7:0], i.e. the first byte after the
// sync header):
//
//   header 01                -> DATA          (64 payload bits)
//   header 10, type 0x00     -> ALL_CONTROL   (idle / C field block)
//   header 10, type 0x78     -> MIXED_START   (start of packet in this lane)
//   header 10, type 0x99     -> MIXED_START   (start of data in following lanes)
//   header 10, type 0x87     -> MIXED_TERMINATE
//   header 10, type 0xFF     -> MIXED_DATA    (all bytes carry data)
//   header 10, other type    -> ERROR
//   header 00/11             -> ERROR
//
// byte_ctrl marks which of the 8 payload bytes are control (1) rather than
// data (0).  For a control block the type byte is always control; the
// remaining bytes are taken from the block type: idle blocks are all control,
// 0xFF is all data, the mixed types carry data from byte 1 onwards.
//
// ctrl_field[i] exposes the 7-bit control field starting at payload bit
// 8 + 7*i for control blocks, which is the layout the idle/start/terminate
// ordered sets use.  field_valid[i] is set when byte i+1 is a control byte.
// -----------------------------------------------------------------------------
module pcs_64b66b_ctrl_decode (
    input  logic [65:0]      block_in,
    output logic [2:0]       kind,
    output logic [7:0]       byte_ctrl,
    output logic [63:0]      data,
    output logic [7:0]       block_type,
    output logic [6:0][6:0]  ctrl_field,
    output logic [6:0]       field_valid,
    output logic             type_valid
);

    localparam logic [2:0] K_DATA      = 3'd0;
    localparam logic [2:0] K_ALL_CTRL  = 3'd1;
    localparam logic [2:0] K_MIX_START = 3'd2;
    localparam logic [2:0] K_MIX_TERM  = 3'd3;
    localparam logic [2:0] K_MIX_DATA  = 3'd4;
    localparam logic [2:0] K_ERROR     = 3'd5;

    logic [1:0] hdr;
    assign hdr        = block_in[65:64];
    assign data       = block_in[63:0];
    assign block_type = block_in[7:0];

    always_comb begin
        kind        = K_ERROR;
        byte_ctrl   = 8'h00;
        type_valid  = 1'b0;
        field_valid = '0;
        ctrl_field  = '0;

        if (hdr == 2'b01) begin
            kind       = K_DATA;
            byte_ctrl  = 8'h00;
            type_valid = 1'b1;
        end else if (hdr == 2'b10) begin
            case (block_in[7:0])
                8'h00: begin
                    kind        = K_ALL_CTRL;
                    byte_ctrl   = 8'hFF;
                    field_valid = 7'h7F;
                    type_valid  = 1'b1;
                end
                8'h78, 8'h99: begin
                    kind        = K_MIX_START;
                    byte_ctrl   = 8'hFF;
                    field_valid = 7'h7F;
                    type_valid  = 1'b1;
                end
                8'h87: begin
                    kind        = K_MIX_TERM;
                    byte_ctrl   = 8'hFF;
                    field_valid = 7'h7F;
                    type_valid  = 1'b1;
                end
                8'hFF: begin
                    kind        = K_MIX_DATA;
                    byte_ctrl   = 8'h01;   // only the type byte is control
                    field_valid = 7'h00;
                    type_valid  = 1'b1;
                end
                default: begin
                    kind       = K_ERROR;
                    byte_ctrl  = 8'h01;
                    type_valid = 1'b0;
                end
            endcase
            // Byte 0 is always the block type in a control block.
            byte_ctrl[0] = 1'b1;
            for (int i = 0; i < 7; i++)
                ctrl_field[i] = block_in[8 + 7*i +: 7];
        end
    end

endmodule

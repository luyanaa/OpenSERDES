`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// One word of the deserializer.
//
// Each word owns exactly one DATA_WIDTH register bank.  There is no shadow
// INT_PAR_IN register and no second PAR_IN latch: par_data is the word_data
// array itself.
//
// The bit select is one-hot, so this module has no binary comparator in the
// capture path.  The parent already qualified `capture` with the word select.
// -----------------------------------------------------------------------------
module deserializer_word #(
    parameter int DATA_WIDTH = 32,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  capture,
    input  logic                  serial_in,
    input  logic [DATA_WIDTH-1:0] bit_sel_onehot,
    output logic [DATA_WIDTH-1:0] data
);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data <= '0;
        end else if (capture) begin
            for (int b = 0; b < DATA_WIDTH; b++) begin
                if (bit_sel_onehot[b])
                    data[LSB_FIRST ? b : (DATA_WIDTH - 1 - b)] <= serial_in;
            end
        end
    end

endmodule

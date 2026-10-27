`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Legacy-compatible deserializer adapter.
//
// Keeps the original `deserialiser_unit_cell_1` port list and maps it onto the
// new one-hot deserializer.  The new core captures one bit whenever READY is
// high (serial_valid is tied high), matching the original one-bit-per-CLK
// assumption.
//
// PAR_IN1..8 are the migrated output bank.  OUT is the last word for
// compatibility; it is not the old shift-register scratch value.  COMPLETE is
// the core par_valid pulse.  COUNT/SAMPLE_COUNT are compatibility counters.
//
// Do not compile this file together with legacy/rtl/deserialiser_unit_cell.v:
// both define the same module name.
// -----------------------------------------------------------------------------
module deserialiser_unit_cell_1 (
    input  logic        CLK,
    input  logic        RESET,
    input  logic        READY,
    input  logic        SERIAL_IN,
    output logic [31:0] PAR_IN1,
    output logic [31:0] PAR_IN2,
    output logic [31:0] PAR_IN3,
    output logic [31:0] PAR_IN4,
    output logic [31:0] PAR_IN5,
    output logic [31:0] PAR_IN6,
    output logic [31:0] PAR_IN7,
    output logic [31:0] PAR_IN8,
    output logic        COMPLETE,
    output logic        INTERNAL_FINISH,
    output logic [5:0]  COUNT,
    output logic [3:0]  SAMPLE_COUNT,
    output logic [31:0] OUT
);

    localparam int DATA_WIDTH = 32;
    localparam int WORDS      = 8;

    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic par_valid;
    logic busy;

    deserializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1)
    ) u_core (
        .clk(CLK),
        .rst_n(RESET),
        .enable(READY),
        .align(1'b0),
        .flush(1'b0),
        .serial_in(SERIAL_IN),
        .serial_valid(1'b1),
        .par_data(par_data),
        .par_valid(par_valid),
        .busy(busy)
    );

    assign PAR_IN1 = par_data[0];
    assign PAR_IN2 = par_data[1];
    assign PAR_IN3 = par_data[2];
    assign PAR_IN4 = par_data[3];
    assign PAR_IN5 = par_data[4];
    assign PAR_IN6 = par_data[5];
    assign PAR_IN7 = par_data[6];
    assign PAR_IN8 = par_data[7];
    assign OUT     = par_data[WORDS-1];

    assign COMPLETE        = par_valid;
    assign INTERNAL_FINISH = busy;

    always_ff @(posedge CLK or negedge RESET) begin
        if (!RESET) begin
            COUNT        <= '0;
            SAMPLE_COUNT <= '0;
        end else if (par_valid) begin
            COUNT        <= '0;
            SAMPLE_COUNT <= '0;
        end else if (busy && READY) begin
            if (COUNT == 6'd31) begin
                COUNT <= '0;
                if (SAMPLE_COUNT < 4'd7)
                    SAMPLE_COUNT <= SAMPLE_COUNT + 1'b1;
            end else begin
                COUNT <= COUNT + 1'b1;
            end
        end
    end

endmodule

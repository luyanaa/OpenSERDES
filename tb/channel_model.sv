`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Digital behavioral channel model.
//
// This models latency, optional inversion and deterministic bit-error
// injection.  It does NOT model analog loss, ISI, reflections or jitter; those
// require a Verilog-A / S-parameter / IBIS-AMI channel.  It is intended for
// digital integration and protocol tests, not for analog signoff.
// -----------------------------------------------------------------------------
module channel_model #(
    parameter int DELAY_CYCLES    = 0,
    parameter int BIT_ERROR_PERIOD = 0, // 0 = no errors; N = one error every N bits
    parameter bit INVERT          = 1'b0
) (
    input  logic clk,
    input  logic rst_n,
    input  logic serial_in,
    input  logic serial_valid,
    // Runtime controls: pulse corrupt_pulse to flip one serial bit, or
    // slip_pulse to hold the current serial bit for one extra bit period --
    // the downstream free-running sampler then sees one bit duplicated, which
    // is a single-bit phase slip.  Both are optional; leave them low for the
    // parameter-only behaviour.
    input  logic corrupt_pulse = 1'b0,
    input  logic slip_pulse    = 1'b0,
    output logic serial_out,
    output logic serial_valid_out
);

    logic corrupted;
    logic inject_error;
    logic slip_hold_q;
    logic slipped_bit;
    int   bit_count;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_count  <= 0;
            slip_hold_q <= 1'b0;
            slipped_bit <= 1'b0;
        end else begin
            if (serial_valid)
                bit_count <= bit_count + 1;
            // Latch the bit being held so the next cycle repeats it.
            if (slip_pulse)
                slipped_bit <= serial_in;
            slip_hold_q <= slip_pulse;
        end
    end

    always @(bit_count or serial_valid) begin
        inject_error = 1'b0;
        if (BIT_ERROR_PERIOD > 0 && serial_valid) begin
            if ((bit_count % BIT_ERROR_PERIOD) == 0)
                inject_error = 1'b1;
        end
    end

    // During a slip the output replays the held bit instead of the live one.
    assign corrupted = (slip_hold_q ? slipped_bit : serial_in)
                       ^ INVERT ^ inject_error ^ corrupt_pulse;

    generate
        if (DELAY_CYCLES == 0) begin : g_no_delay
            assign serial_out       = corrupted;
            assign serial_valid_out = serial_valid;
        end else begin : g_delay
            logic [0:DELAY_CYCLES-1] data_pipe;
            logic [0:DELAY_CYCLES-1] valid_pipe;

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    data_pipe  <= '0;
                    valid_pipe <= '0;
                end else begin
                    for (int i = DELAY_CYCLES-1; i > 0; i--) begin
                        data_pipe[i]  <= data_pipe[i-1];
                        valid_pipe[i] <= valid_pipe[i-1];
                    end
                    data_pipe[0]  <= corrupted;
                    valid_pipe[0] <= serial_valid;
                end
            end

            assign serial_out       = data_pipe[DELAY_CYCLES-1];
            assign serial_valid_out = valid_pipe[DELAY_CYCLES-1];
        end
    endgenerate

endmodule

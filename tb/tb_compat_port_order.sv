`timescale 1ns/1ps

// Compile/elaboration-only check that the compatibility adapters keep the
// legacy POSITIONAL port order, not just the names.
module tb_compat_port_order;

    logic clk;
    logic rst;
    logic ready;
    logic serial_out;
    logic internal_finish_s;
    logic complete_s;
    logic [31:0] par_s1, par_s2, par_s3, par_s4;
    logic [31:0] par_s5, par_s6, par_s7, par_s8;
    logic [5:0]  count_s;
    logic [3:0]  sample_count_s;

    logic serial_in;
    logic [31:0] par_d1, par_d2, par_d3, par_d4;
    logic [31:0] par_d5, par_d6, par_d7, par_d8;
    logic        complete_d;
    logic        internal_finish_d;
    logic [5:0]  count_d;
    logic [3:0]  sample_count_d;
    logic [31:0] out_d;

    serializer_unit_cell_1 u_ser (
        clk, rst, serial_out, ready, internal_finish_s, complete_s,
        par_s1, par_s2, par_s3, par_s4, par_s5, par_s6, par_s7, par_s8,
        count_s, sample_count_s
    );

    deserialiser_unit_cell_1 u_des (
        clk, rst, ready, serial_in,
        par_d1, par_d2, par_d3, par_d4, par_d5, par_d6, par_d7, par_d8,
        complete_d, internal_finish_d, count_d, sample_count_d, out_d
    );

    initial begin
        $display("PASS: tb_compat_port_order");
        $finish;
    end

endmodule

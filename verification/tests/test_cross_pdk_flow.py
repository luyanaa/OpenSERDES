"""icsprout55 cross-PDK flow overrides: Odb skip, KLayout LVS, tiled DRC."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_cross_pdk_pnr.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_cross_pdk_pnr_mod", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["run_cross_pdk_pnr_mod"] = module
    spec.loader.exec_module(module)
    return module


class TestIcs55FlowOverrides(unittest.TestCase):
    def test_odB_antenna_is_removed_and_klayout_lvs_replaces_netgen(self) -> None:
        module = _load()
        cfg: dict = {}
        module._ics55_flow_overrides(
            cfg, Path("/grid/icsprout55-pdk"), Path("librelane.yaml"))
        substitutions = cfg["meta"]["substituting_steps"]
        self.assertIsNone(substitutions["Odb.CheckDesignAntennaProperties"])
        self.assertEqual(substitutions["Netgen.LVS"], "ICS55.KLayoutLVS")
        self.assertEqual(substitutions["-Netgen.LVS"], "OpenROAD.WriteCDL")
        self.assertEqual(
            substitutions["Odb.ReportDisconnectedPins"],
            "ICS55.ReportDisconnectedPins",
        )
        self.assertIsNone(substitutions["Magic.WriteLEF"])
        self.assertEqual(cfg["meta"]["flow"], "Classic")

    def test_tiled_drc_and_lvs_runset_are_set(self) -> None:
        module = _load()
        cfg: dict = {
            "DESIGN_NAME": "serdes_rx_digital",
        }
        module._ics55_flow_overrides(
            cfg, Path("/grid/icsprout55-pdk"), Path("librelane.yaml"))
        self.assertEqual(cfg["KLAYOUT_DRC_DEFINES"]["run_mode"], "tiling")
        self.assertEqual(cfg["KLAYOUT_DRC_DEFINES"]["cell_exclusion"], "true")
        self.assertEqual(cfg["KLAYOUT_DRC_DEFINES"]["top"], "serdes_rx_digital")
        self.assertTrue(cfg["KLAYOUT_LVS_SCRIPT"].endswith("libs.tech/lvs/ics55.lvs"))
        self.assertTrue(cfg["KLAYOUT_DEF_LAYER_MAP"].endswith(
            "tech/icsprout55/def_layer.map"))
        self.assertEqual(cfg["KLAYOUT_LVS_OPTIONS"], {
            "run_mode": "deep",
            "top": "serdes_rx_digital",
            "strict_topology": "true",
            "strict_hierarchy": "true",
            "blackbox": "true",
            "flow_mode": "true",
        })
        self.assertFalse(cfg["ERROR_ON_KLAYOUT_DRC"])

    def test_clock_tree_uses_smaller_root_to_meet_fanout(self) -> None:
        module = _load()
        cfg: dict = {"DESIGN_NAME": "serdes_rx_digital"}
        module._ics55_flow_overrides(
            cfg, Path("/grid/icsprout55-pdk"), Path("librelane.yaml"))
        self.assertEqual(cfg["CTS_ROOT_BUFFER"], "BUFX8H7L")
        self.assertEqual(cfg["CTS_CLK_BUFFERS"], ["BUFX4H7L", "BUFX8H7L"])

    def test_ihp_uses_klayout_lvs_instead_of_netgen(self) -> None:
        module = _load()
        cfg: dict = {}
        module._ihp_flow_overrides(
            cfg, Path("/pdk/ihp-sg13g2"), Path("librelane.yaml"))
        substitutions = cfg["meta"]["substituting_steps"]
        self.assertEqual(substitutions["Netgen.LVS"], "KLayout.LVS")
        self.assertEqual(substitutions["-Netgen.LVS"], "OpenROAD.WriteCDL")
        self.assertTrue(cfg["KLAYOUT_LVS_SCRIPT"].endswith(
            "libs.tech/klayout/tech/lvs/sg13g2.lvs"))
        # IHP keeps its original deck options; the ics55 strict/blackbox
        # switches are not valid for the IHP runset.
        self.assertEqual(cfg["KLAYOUT_LVS_OPTIONS"], {"run_mode": "deep"})

    def test_ez130_cdl_drops_well_pins_only(self) -> None:
        module = _load()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.cdl"
            src.write_text(
                ".SUBCKT AND2X1 A B VDD VNW VPW VSS Y\n"
                "M0 Y A VSS VPW nfet\n"
                ".ENDS\n")
            dst = Path(tmp) / "out.cdl"
            module._ez130_lvs_cdl(src, dst)
            text = dst.read_text().splitlines()
            self.assertEqual(text[0], ".SUBCKT AND2X1 A B VDD VSS Y")
            self.assertIn("VPW", text[1])
            self.assertIn(".SUBCKT FILLER1 VDD VSS", text)
            self.assertEqual(text.count(".SUBCKT FILLER1 VDD VSS"), 1)

    def test_ihp_lvs_path_uses_the_pdk_family_directory(self) -> None:
        module = _load()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ihp-sg13g2").mkdir()
            cfg = {"PDK": "ihp-sg13g2"}
            module._ihp_flow_overrides(cfg, root, Path("librelane.yaml"))
            self.assertEqual(
                cfg["KLAYOUT_LVS_SCRIPT"],
                str(root / "ihp-sg13g2/libs.tech/klayout/tech/lvs/sg13g2.lvs"))

    def test_no_cli_skip_list_for_ics55(self) -> None:
        module = _load()
        self.assertEqual(module._skip_args("icsprout55"), [])
        self.assertEqual(module.SIGNOFF_SKIP_STEPS, {})

    def test_scl_override_reaches_the_librelane_cli(self) -> None:
        module = _load()
        args = module._librelane_pdk_args("gf180mcuD", scl="gf180mcu_fd_sc_mcu9t5v0")
        self.assertIn("gf180mcu_fd_sc_mcu9t5v0", args)
        self.assertIn("--manual-pdk", args)

    def test_unknown_scl_is_rejected(self) -> None:
        module = _load()
        with self.assertRaises(SystemExit):
            module._librelane_pdk_args("gf180mcuD", scl="not_a_library")

    def test_spec_scl_is_a_mapping(self) -> None:
        module = _load()
        import tempfile
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.yaml"
            src.write_text("name: demo\ntech: {}\n")
            out = module._spec_with_tech(
                src, "gf180mcuD", Path(tmp) / "out.yaml",
                scl="gf180mcu_fd_sc_mcu9t5v0")
            data = yaml.safe_load(out.read_text())
            self.assertEqual(data["tech"]["rx"], {
                "name": "gf180mcuD", "scl": "gf180mcu_fd_sc_mcu9t5v0"})
            plain = module._spec_with_tech(
                src, "gf180mcuD", Path(tmp) / "plain.yaml")
            plain_data = yaml.safe_load(plain.read_text())
            self.assertEqual(plain_data["tech"]["rx"], "gf180mcuD")

    def test_osu_magic_gds_is_the_merged_library_plus_fill1(self) -> None:
        module = _load()
        paths = module._osu_cell_gds(
            Path("/pdk"), "gf180mcuD", Path("/work/fill_1.gds"))
        self.assertEqual(paths, [
            "/pdk/gf180mcuD/libs.ref/gf180mcu_osu_sc_gp9t3v3/gds/"
            "gf180mcu_osu_sc_gp9t3v3.gds",
            "/work/fill_1.gds",
        ])


class TestAcceptanceGates(unittest.TestCase):
    def _rec(self, **metrics):
        module = _load()
        return module._classify({"metrics": metrics})

    def test_positive_setup_with_slew_is_not_timing_closure(self) -> None:
        rec = self._rec(
            setup_wns_ns=1.5, setup_wns_by_corner_ns={"tt": 1.5},
            hold_wns_ns=0.1, max_slew_violations=235,
            max_cap_violations=0, max_fanout_violations=0,
            drc_routing=0, drc_magic=0, drc_klayout=0,
            lvs_errors=0, antenna_violations=0)
        self.assertEqual(rec["timing_closure"]["status"], "fail")
        self.assertEqual(rec["physical_signoff"]["status"], "pass")
        self.assertEqual(rec["status"], "measured_with_violations")
        self.assertIn("max_slew_violations", rec["timing_closure"]["failed"])

    def test_setup_fail_does_not_fail_a_clean_physical_gate(self) -> None:
        rec = self._rec(
            setup_wns_ns=-1.2, setup_wns_by_corner_ns={"ss": -1.2},
            hold_wns_ns=0.0, max_slew_violations=0,
            max_cap_violations=0, max_fanout_violations=0,
            drc_routing=0, drc_magic=0, drc_klayout=0,
            lvs_errors=0, antenna_violations=0,
            critical_path_arrival_ns={"ns": 21.2, "corner": "ss"})
        self.assertEqual(rec["timing_closure"]["status"], "fail")
        self.assertEqual(rec["physical_signoff"]["status"], "pass")
        self.assertIn("ss", rec["timing_violations"])

    def test_missing_signoff_is_not_a_pass(self) -> None:
        rec = self._rec(
            setup_wns_ns=0.2, setup_wns_by_corner_ns={"tt": 0.2},
            hold_wns_ns=0.0, max_slew_violations=0,
            max_cap_violations=0, max_fanout_violations=0,
            drc_routing=0)
        self.assertEqual(rec["timing_closure"]["status"], "pass")
        self.assertEqual(rec["physical_signoff"]["status"], "not_reached")
        self.assertEqual(rec["status"], "measured_incomplete")
        self.assertIn("drc_magic", rec["physical_signoff"]["missing"])

    def test_both_gates_passing_is_measured(self) -> None:
        rec = self._rec(
            setup_wns_ns=0.2, setup_wns_by_corner_ns={"tt": 0.2},
            hold_wns_ns=0.0, max_slew_violations=0,
            max_cap_violations=0, max_fanout_violations=0,
            drc_routing=0, drc_magic=0, drc_klayout=0,
            lvs_errors=0, antenna_violations=0)
        self.assertEqual(rec["status"], "measured")
        self.assertNotIn("reason", rec)


class TestCollectFallback(unittest.TestCase):
    def test_midflow_state_without_route_drc_is_rejected(self) -> None:
        module = _load()
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            mid = run / "43-openroad-stamidpnr-3"
            mid.mkdir()
            (mid / "state_out.json").write_text(json.dumps({
                "metrics": {"design__instance__count": 1215,
                            "timing__setup__wns": 0}}))
            with self.assertRaises(FileNotFoundError):
                module._collect(run)

    def test_postroute_state_with_route_drc_is_accepted(self) -> None:
        module = _load()
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            post = run / "55-openroad-stapostpnr"
            post.mkdir()
            (post / "state_out.json").write_text(json.dumps({
                "metrics": {"route__drc_errors": 0,
                            "design__instance__count": 10}}))
            rec = module._collect(run)
            self.assertEqual(
                rec["metrics_source"],
                "55-openroad-stapostpnr/state_out.json")
            self.assertEqual(rec["drc_routing"], 0)


class TestSignoffRulesReachPnR(unittest.TestCase):
    def test_pnr_sdc_gets_signoff_limits_and_clock_cluster_cap(self) -> None:
        module = _load()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pnr = root / "pre_pnr.sdc"
            sign = root / "signoff.sdc"
            pnr.write_text("create_clock -name rx_clk -period 20\n")
            sign.write_text(
                "set_max_fanout 10.0 [current_design]\n"
                "set_max_transition 0.5000 [current_design]\n"
                "set_max_capacitance 0.100000 [current_design]\n")
            cfg = {"PNR_SDC_FILE": str(pnr), "SIGNOFF_SDC_FILE": str(sign)}
            module._apply_signoff_rules_to_pnr(cfg, root)
            text = Path(cfg["PNR_SDC_FILE"]).read_text()
            self.assertIn("set_max_transition 0.5000", text)
            self.assertIn("set_max_capacitance 0.100000", text)
            self.assertIn("set_max_fanout 10.0", text)
            self.assertEqual(cfg["CTS_SINK_CLUSTERING_SIZE"], 9)
            self.assertTrue(cfg["CTS_SINK_CLUSTERING_ENABLE"])
            self.assertEqual(Path(cfg["SIGNOFF_SDC_FILE"]), sign)
            # A second pass must not duplicate the rules or tighten further.
            module._apply_signoff_rules_to_pnr(cfg, root)
            self.assertEqual(Path(cfg["PNR_SDC_FILE"]).read_text().count(
                "set_max_transition"), 1)
            self.assertEqual(cfg["CTS_SINK_CLUSTERING_SIZE"], 9)


class TestIhpPdnRail(unittest.TestCase):
    def test_rail_width_matches_stdcell_rail(self) -> None:
        module = _load()
        cfg: dict = {"PDK": "ihp-sg13g2", "STD_CELL_LIBRARY": "sg13g2_stdcell"}
        module._ihp_flow_overrides(
            cfg, Path("/pdk/ihp-sg13g2"), Path("librelane.yaml"))
        self.assertEqual(cfg["PDN_RAIL_LAYER"], "Metal1")
        self.assertEqual(cfg["PDN_RAIL_WIDTH"], 0.44)


class TestSclFlowPatches(unittest.TestCase):
    def test_slow_corner_selection(self) -> None:
        module = _load()
        corners = ["nom_tt_025C_1v20", "nom_ss_125C_1v08",
                   "nom_ff_n40C_1v32"]
        slow = [c for c in corners
                if "_ss_" in c or "slow" in c.lower() or c.endswith("_ss")]
        self.assertEqual(slow, ["nom_ss_125C_1v08"])
        ihp = ["nom_typ_1p20V_25C", "nom_slow_1p08V_125C",
               "nom_fast_1p32V_m40C"]
        slow = [c for c in ihp
                if "_ss_" in c or "slow" in c.lower() or c.endswith("_ss")]
        self.assertEqual(slow, ["nom_slow_1p08V_125C"])

    def test_sky130_clock_tree_uses_smaller_root(self) -> None:
        module = _load()
        patch = module._SCL_FLOW_PATCHES["sky130_fd_sc_hd"]
        self.assertEqual(patch["CTS_ROOT_BUFFER"], "sky130_fd_sc_hd__clkbuf_8")
        self.assertEqual(patch["CTS_CLK_BUFFERS"],
                         ["sky130_fd_sc_hd__clkbuf_4",
                          "sky130_fd_sc_hd__clkbuf_8"])


class TestFanoutSplit(unittest.TestCase):
    def test_clock_tree_fanout_is_recorded_separately(self) -> None:
        module = _load()
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            final = run / "final"
            final.mkdir()
            (final / "metrics.json").write_text(json.dumps({
                "route__drc_errors": 0,
                "design__max_fanout_violation__count": 3,
                "design__max_slew_violation__count": 0,
                "design__max_cap_violation__count": 0,
                "timing__setup__wns": 0.1,
                "timing__hold__wns": 0.1,
            }))
            sta = run / "56-openroad-stapostpnr" / "nom_ss"
            sta.mkdir(parents=True)
            (sta / "checks.rpt").write_text(
                "\nmax fanout\n\n"
                "Pin    Limit    Fanout    Slack\n"
                "clkbuf_0_rx_clk/Y    10    16    -6 (VIOLATED)\n"
                "_1234_/Y             10    12    -2 (VIOLATED)\n"
                "clkload2/A           10    11    -1 (VIOLATED)\n"
                "\nmax capacitance\n\n"
                "Pin    Limit    Cap    Slack\n"
                "clkbuf_0_rx_clk/Y    0.1    0.111    -0.011 (VIOLATED)\n")
            rec = module._collect(run)
            self.assertEqual(rec["max_fanout_violations"], 1)
            self.assertEqual(rec["clock_fanout_violations"], 2)
            self.assertEqual(rec["max_cap_violations"], 0)
            self.assertEqual(rec["clock_cap_violations"], 1)


if __name__ == "__main__":
    unittest.main()

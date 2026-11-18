"""Clock gating and operand isolation (item 8 of the PPA audit)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "gen_ip.py"


def _spec(tech: str = "sky130", cg: bool = True) -> dict:
    return {
        "name": "cg", "target": "tx",
        "link": {"payload_width": 8, "words": 4, "line_rate_mbps": 25,
                 "coding": "raw", "bit_order": "lsb_first"},
        "tx": {"architecture": "word_group_capture"},
        "rx": {"architecture": "fixed_oversampling", "oversample": 4,
               "align": "external", "clocking": "independent", "ppm": 0,
               "sync_len": 8},
        "tech": {"tx": tech, "rx": tech},
        "interface": {"preset": "minimal", "data": "valid_ready",
                      "control": "native", "cdc": "none",
                      "enable_mode": "static"},
        "constraints": {"uncertainty_setup_ns": 0.1, "uncertainty_hold_ns": 0.05},
        "optimization": {"objectives": ["power"], "clock_gating": cg},
    }


class TestClockGating(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, spec: dict):
        p = self.d / "s.yaml"
        p.write_text(yaml.safe_dump(spec))
        out = self.d / ("o%d" % len(list(self.d.glob("o*"))))
        r = subprocess.run([sys.executable, str(GEN), "--spec", str(p),
                            "--out", str(out), "--force"], cwd=ROOT,
                           text=True, capture_output=True, timeout=120)
        return r, out

    def test_sky130_emits_yosys_clock_gate_config(self) -> None:
        r, out = self._run(_spec("sky130", True))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pnr = yaml.safe_load((out / "pnr" / "librelane.yaml").read_text())
        self.assertEqual(pnr["SYNTH_CLOCKGATE_MIN_WIDTH"], 8)
        self.assertEqual(pnr["SYNTH_CLOCKGATE_POSEDGE_ICG"],
                         "sky130_fd_sc_hd__dlclkp_1/GATE/CLK/GCLK")
        m = json.loads((out / "manifest.json").read_text())
        self.assertTrue(m["clock_gating"]["enabled"])
        self.assertIn("dlclkp_1", m["clock_gating"]["icg"])
        self.assertIn("SYNTH_CLOCKGATE", (out / "pnr" / "README.md").read_text())

    def test_tech_without_icg_fails_closed(self) -> None:
        # Every shipped tech profile now declares an ICG cell, so the
        # fail-closed path is exercised on the check itself rather than on a
        # real profile.  Asking for clock gating on a library without an
        # integrated gate must stop the flow, not silently emit a Yosys
        # clock-gate config the library cannot implement.
        sys.path.insert(0, str(ROOT))
        from scripts import gen_ip

        no_icg = gen_ip.Tech(
            name="fake", pdk="fake", scl="fake_sc", default_corner="tt",
            corners={"tt": []}, native_dff="dff", limits={}, cts={},
            clock_gate={})
        # SpecError is gen_ip's own failure type (raised by `fail`); the CLI
        # turns it into exit code 2.
        with self.assertRaises(gen_ip.SpecError) as ctx:
            gen_ip.check_clock_gating(no_icg)
        self.assertIn("clock_gate.icg_cell", str(ctx.exception))

    def test_gf180_emits_icg_config(self) -> None:
        """gf180mcuD gained clock_gate (icgtp_1) in this round.

        The pins were verified in the gf180mcu tt liberty: CLK is the clock,
        E the enable, and Q is the `clock_gate_out_pin`, so the gated clock is
        the cell output rather than a separately named GCLK pin.
        """
        r, out = self._run(_spec("gf180mcuD", True))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pnr = yaml.safe_load((out / "pnr" / "librelane.yaml").read_text())
        self.assertEqual(pnr["SYNTH_CLOCKGATE_POSEDGE_ICG"],
                         "gf180mcu_fd_sc_mcu7t5v0__icgtp_1/E/CLK/Q")
        m = json.loads((out / "manifest.json").read_text())
        self.assertTrue(m["clock_gating"]["enabled"])
        self.assertIn("icgtp_1", m["clock_gating"]["icg"])

    def test_ihp_emits_icg_config(self) -> None:
        r, out = self._run(_spec("ihp-sg13g2", True))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pnr = yaml.safe_load((out / "pnr" / "librelane.yaml").read_text())
        self.assertEqual(pnr["SYNTH_CLOCKGATE_POSEDGE_ICG"],
                         "sg13g2_lgcp_1/GATE/CLK/GCLK")
        m = json.loads((out / "manifest.json").read_text())
        self.assertTrue(m["clock_gating"]["enabled"])
        self.assertIn("lgcp_1", m["clock_gating"]["icg"])

    def test_disabled_by_default(self) -> None:
        r, out = self._run(_spec("sky130", False))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pnr = yaml.safe_load((out / "pnr" / "librelane.yaml").read_text())
        self.assertNotIn("SYNTH_CLOCKGATE_POSEDGE_ICG", pnr)
        m = json.loads((out / "manifest.json").read_text())
        self.assertFalse(m["clock_gating"]["enabled"])

    def test_export_clk_out_can_be_disabled(self) -> None:
        spec = _spec("sky130", False)
        spec["target"] = "rx"
        spec["tx"] = {"architecture": "parallel_mux"}
        spec["rx"] = {"architecture": "fixed_oversampling", "oversample": 4,
                      "align": "external", "clocking": "independent", "ppm": 0,
                      "sync_len": 8}
        spec["optimization"] = {"objectives": ["power"], "export_clk_out": False}
        r, out = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pnr = yaml.safe_load((out / "pnr" / "librelane.yaml").read_text())
        self.assertIn("CLK_OUT_ENABLE=0", pnr["SYNTH_PARAMETERS"])
        m = json.loads((out / "manifest.json").read_text())
        self.assertFalse(m["optimization"]["export_clk_out"])

    def test_operand_isolation_is_present_in_serializers(self) -> None:
        for name in ("serializer.sv", "serializer_stream.sv", "serializer_hier_2to1.sv"):
            text = (ROOT / "rtl" / name).read_text()
            self.assertIn("active_q &", text, name)


if __name__ == "__main__":
    unittest.main()

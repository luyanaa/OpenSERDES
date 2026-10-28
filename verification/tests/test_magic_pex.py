import tempfile
import unittest
from pathlib import Path

from verification.analog.magic_pex import _clean_body, gds_read_path
from verification.analog.magic_pex_compare import convert_schematic
from verification.analog.pdk_mc import MODES, _netlist, parse_statistics


class TestMagicPexHelpers(unittest.TestCase):
    def test_gds_stem_for_magic(self) -> None:
        self.assertEqual(gds_read_path(Path("/x/y.gds")), Path("/x/y"))
        self.assertEqual(gds_read_path(Path("/x/y")), Path("/x/y"))

    def test_floating_annotation_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "raw.spice"
            p.write_text("* SPICE3 file\nC1 a 0 1f **FLOATING\nR1 a b 2\n.end\n")
            body = _clean_body(p)
            self.assertIn("C1 a 0 1f", body)
            self.assertNotIn("**FLOATING", "\n".join(body))
            self.assertNotIn(".end", body)

    def test_schematic_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "macro.sp"
            src.write_text(
                ".SUBCKT Macro GND VDD IN OUT\n"
                "M1 OUT IN GND GND nshort L=0.15 W=5 m=1 $X=1\n"
                "M2 OUT IN VDD VDD pshort L=0.15 W=5 m=1\n"
                "X3 GND VDD IN OUT Inverter2\n"
                "X4 GND VDD nwdiode\n"
                ".ENDS\n"
            )
            out = Path(tmp) / "out.spice"
            info = convert_schematic(src, out)
            text = out.read_text()
            self.assertEqual(info["mos"], 2)
            self.assertIn("sky130_fd_pr__nfet_01v8", text)
            self.assertIn("sky130_fd_pr__pfet_01v8", text)
            self.assertIn("Inverter2", text)
            self.assertNotIn("nwdiode", text)

    def test_statistics_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "critical.spice"
            p.write_text(
                "* statistics {\n"
                "*   process {\n"
                "*     vary cnwvc dist=gauss std=1.0\n"
                "*   }\n"
                "*   mismatch {\n"
                "*     vary vth0 dist=gauss std=1.5\n"
                "*   }\n"
                "* }\n"
            )
            parsed = parse_statistics(p)
            self.assertEqual(parsed["process"][0]["name"], "cnwvc")
            self.assertEqual(parsed["mismatch"][0]["std"], 1.5)

    def test_pdk_mc_switch_netlist(self) -> None:
        self.assertTrue(MODES["mismatch"][1] == 1)
        # Do not require an installed PDK in the unit-test environment.
        text = _netlist(Path("/opt/fake-pdk"), *MODES["mismatch"], seed=7)
        self.assertIn("mc_mm_switch=1", text)
        self.assertIn("mc_pr_switch=0", text)
        self.assertIn("set seed=7", text)


if __name__ == "__main__":
    unittest.main()

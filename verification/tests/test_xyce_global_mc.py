import tempfile
import unittest
from pathlib import Path

from verification.analog.xyce_global_mc import _netlist, parse_prn, process_vars


class TestXyceGlobalMc(unittest.TestCase):
    def test_process_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "critical.spice"
            p.write_text(
                ".param a = 0.0\n"
                "* statistics {\n"
                "*  process {\n"
                "*     vary cnwvc dist=gauss std=1.0\n"
                "*     vary diff_cd dist=gauss std=1.5\n"
                "*  }\n"
                "*  mismatch {\n"
                "*     vary ignored dist=gauss std=9\n"
                "*  }\n"
                "* }\n"
            )
            vars_ = process_vars(p)
            self.assertEqual([v["name"] for v in vars_], ["cnwvc", "diff_cd"])
            self.assertEqual(vars_[1]["std"], 1.5)

    def test_global_params_precede_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdk = Path(tmp)
            lib = pdk / "sky130A/libs.tech/ngspice/sky130.lib.spice"
            lib.parent.mkdir(parents=True)
            lib.write_text("* fake\n")
            text = _netlist(pdk, [
                {"name": "cnwvc", "dist": "gauss", "std": 1.0},
                {"name": "diff_cd", "dist": "gauss", "std": 1.5},
            ], "global", 4, 123, 1)
            self.assertLess(text.index(".global_param cnwvc=0.0"),
                            text.index('.lib "'))
            self.assertIn(".SAMPLING", text)
            self.assertIn("param=cnwvc,diff_cd", text)
            self.assertIn("std_deviations=1.0,1.5", text)

    def test_prn_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.prn"
            p.write_text(
                "Index      I(VD1)            I(VD2)\n"
                "0       -3.0e-05   -3.1e-05\n"
                "0       -3.2e-05   -3.2e-05\n"
                "End of Xyce(TM) Parameter Sweep\n"
            )
            rows = parse_prn(p)
            self.assertEqual(len(rows), 2)
            self.assertAlmostEqual(rows[0]["differential_a"], 1e-6)
            self.assertAlmostEqual(rows[1]["differential_a"], 0.0)


if __name__ == "__main__":
    unittest.main()

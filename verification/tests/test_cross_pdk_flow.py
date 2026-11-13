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
        self.assertIsNone(substitutions["Magic.WriteLEF"])
        self.assertEqual(cfg["meta"]["flow"], "Classic")

    def test_tiled_drc_and_lvs_runset_are_set(self) -> None:
        module = _load()
        cfg: dict = {}
        module._ics55_flow_overrides(
            cfg, Path("/grid/icsprout55-pdk"), Path("librelane.yaml"))
        self.assertEqual(cfg["KLAYOUT_DRC_DEFINES"], {"run_mode": "tiling"})
        self.assertTrue(cfg["KLAYOUT_LVS_SCRIPT"].endswith("libs.tech/lvs/ics55.lvs"))
        self.assertFalse(cfg["ERROR_ON_KLAYOUT_DRC"])

    def test_no_cli_skip_list_for_ics55(self) -> None:
        module = _load()
        self.assertEqual(module._skip_args("icsprout55"), [])
        self.assertEqual(module.SIGNOFF_SKIP_STEPS, {})


if __name__ == "__main__":
    unittest.main()

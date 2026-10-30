#!/usr/bin/env python3
"""GF180MCU OpenRCX (OpenROAD) parasitic extraction path.

This is the credible R-emitting RCX route for gf180mcuD.  The PDK ships
OpenRCX rule sets under ``libs.tech/librelane/rules.openrcx.gf180mcuD.*``
and a tech LEF with ``Metal1 RESISTANCE RPERSQ 0.090``; OpenROAD's
``extract_parasitics`` consumes a routed DEF + the tech/cell LEF and emits a
SPEF whose ``*RES`` section carries real wire resistors.

This module provides a minimal, verifiable calibration: it synthesises a
1 um x 100 um MET1 wire in DEF, runs OpenRCX, and checks the extracted
resistance against the expected 9.0 ohm (0.090 ohm/sq x 100 sq).  It can also
extract an arbitrary routed DEF supplied via ``--def``.

This is a bounded calibration / macro-PEX step, not full-chip or foundry
signoff.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.gf180_rx_macro import (  # noqa: E402
    _find_gf_root,
    _gf_paths,
    _env,
    _sha256,
)

# 1 um wide, 100 um long on Metal1 -> 100 squares x 0.090 ohm/sq
KNOWN_LINE_LEN_UM = 100.0
KNOWN_LINE_WID_UM = 1.0
M1_SHEET_OHM_PER_SQ = 0.090
EXPECTED_R_OHM = M1_SHEET_OHM_PER_SQ * (KNOWN_LINE_LEN_UM / KNOWN_LINE_WID_UM)

_OPENROAD_NIX = (
    "/nix/store/2y36a4sr078y15ick388dncqjdzmlxga-openroad-2026-02-17"
    "/bin/openroad"
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _openroad(explicit: str | None = None) -> str | None:
    for cand in (explicit, os.environ.get("OPENROAD"),
                 shutil.which("openroad"), _OPENROAD_NIX):
        if cand and Path(cand).is_file():
            return str(cand)
    return None


def _run_or(cmd: list[str], workdir: Path, env: dict[str, str],
            log: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, cwd=str(workdir), env=env, text=True,
                          capture_output=True, timeout=1200)
    text = proc.stdout + proc.stderr
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text)
    return {"command": cmd, "returncode": proc.returncode,
            "log": str(log), "tail": text[-8000:]}


def _tech_lef(pdk: Path) -> Path:
    base = (pdk / "libs.ref/gf180mcu_fd_sc_mcu7t5v0/techlef")
    for cand in sorted(base.glob("*__nom.tlef")) or sorted(base.glob("*.tlef")):
        return cand
    raise FileNotFoundError(f"no tech LEF under {base}")


def _cell_lefs(pdk: Path, extra: list[str]) -> list[Path]:
    lefs: list[Path] = []
    default = (pdk / "libs.ref/gf180mcu_fd_sc_mcu7t5v0/lef/"
               "gf180mcu_fd_sc_mcu7t5v0.lef")
    if default.is_file():
        lefs.append(default)
    for e in extra:
        p = Path(e).expanduser()
        if not p.is_absolute():
            p = pdk / p
        if p.is_file():
            lefs.append(p)
    return lefs


def _openrcx_rules(pdk: Path, corner: str) -> Path:
    librelane = pdk / "libs.tech/librelane"
    cand = librelane / f"rules.openrcx.gf180mcuD.{corner}"
    if not cand.is_file():
        raise FileNotFoundError(f"missing OpenRCX rules: {cand}")
    return cand


def _write_known_def(path: Path) -> None:
    """1 um x 100 um MET1 wire between pins A and B.

    Uses a NONDEFAULTRULE so the routed wire is 1 um wide (default route
    width is the 0.23 um minimum, which would give ~39 ohm, not 9 ohm).
    Coordinates are in microns * UNITS(1000).
    """
    die = 104000  # nm, a little margin around the 100 um body + 1 um pads
    path.write_text(f"""VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN known_m1 ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( {die} 3000 ) ;
TRACKS X 0 DO 1 STEP 1000 LAYER Metal1 ;
TRACKS Y 0 DO 1 STEP 1000 LAYER Metal1 ;
NONDEFAULTRULES 1 ;
- w1u + HARDSPACING
  + LAYER Metal1 WIDTH 1000 SPACING 230
  + LAYER Metal2 WIDTH 1000 SPACING 230
  + LAYER Metal3 WIDTH 1000 SPACING 230
  + LAYER Metal4 WIDTH 1000 SPACING 230
  + LAYER Metal5 WIDTH 1000 SPACING 230 ;
END NONDEFAULTRULES
PINS 2 ;
- A + NET N + DIRECTION INPUT + USE SIGNAL
  + PORT + LAYER Metal1 ( -500 -500 ) ( 500 500 )
  + PLACED ( 500 1500 ) N ;
- B + NET N + DIRECTION OUTPUT + USE SIGNAL
  + PORT + LAYER Metal1 ( -500 -500 ) ( 500 500 )
  + PLACED ( 100500 1500 ) N ;
END PINS
NETS 1 ;
- N ( PIN A ) ( PIN B ) + USE SIGNAL + NONDEFAULTRULE w1u
  + ROUTED Metal1 ( 500 1500 0 ) ( 100500 1500 0 ) ;
END NETS
END DESIGN
""")


def _spef_net_resistance(spef: Path) -> dict:
    """Return {net_name: total_res_ohm} by summing each *D_NET *RES section."""
    out: dict[str, float] = {}
    if not spef.is_file():
        return out
    name_map: dict[str, str] = {}
    cur_net: str | None = None
    in_res = False
    for raw in spef.read_text(errors="replace").splitlines():
        s = raw.strip()
        nm = re.match(r"\*(\d+)\s+(\S+)", s)
        if nm and cur_net is None and "*NAME_MAP" not in out:
            name_map["*" + nm.group(1)] = nm.group(2)
        m = re.match(r"\*D_NET\s+(\S+)", s)
        if m:
            cur_net = name_map.get(m.group(1), m.group(1))
            out.setdefault(cur_net, 0.0)
            in_res = False
            continue
        if cur_net is None:
            continue
        if s.startswith("*RES"):
            in_res = True
            continue
        if s.startswith("*END"):
            cur_net = None
            in_res = False
            continue
        if in_res:
            tok = s.split()
            if len(tok) >= 4:
                try:
                    out[cur_net] += float(tok[3])
                except ValueError:
                    pass
    return out


def _extract(techlef: Path, cellefs: list[Path], defn: Path,
             rules: Path, spef: Path, workdir: Path, env: dict[str, str],
             openroad: str) -> dict:
    tcl = workdir / "extract.tcl"
    lines = [f'read_lef "{techlef}"']
    lines += [f'read_lef "{c}"' for c in cellefs]
    lines += [
        f'read_def "{defn}"',
        f'extract_parasitics -ext_model_file "{rules}" -lef_res',
        f'write_spef "{spef}"',
        "exit",
    ]
    tcl.write_text("\n".join(lines) + "\n")
    rep = _run_or([openroad, "-no_init", "-exit", str(tcl)],
                  workdir, env, workdir / "openrcx.log")
    rep["tcl"] = str(tcl)
    rep["spef"] = str(spef)
    rep["net_resistance_ohm"] = _spef_net_resistance(spef)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gf-root", default=None)
    ap.add_argument("--openroad", default=None)
    ap.add_argument("--def", dest="defn", default=None,
                    help="routed DEF to extract (default: synthesised known line)")
    ap.add_argument("--cell-lef", action="append", default=[],
                    help="extra cell LEF (repeatable; resolved vs PDK root)")
    ap.add_argument("--rules", default="nom", choices=("nom", "min", "max"))
    ap.add_argument("--known-resistor", action="store_true",
                    help="force the synthesised 1x100um MET1 calibration")
    ap.add_argument("--out", default=str(
        ROOT / "build/verify/gf180_rx_macro/openrcx.json"))
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    workdir = (Path(args.workdir).resolve() if args.workdir else
               ROOT / "build/verify/gf180_rx_macro/openrcx")
    workdir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "kind": "gf180-openrcx-pex",
        "generated_utc": _utc(),
        "scope": (
            "OpenRCX (OpenROAD extract_parasitics) RC extraction for "
            "gf180mcuD. Known-line calibration or a caller-supplied routed "
            "DEF. Bounded macro-PEX evidence, not full-chip/foundry signoff."
        ),
        "status": "OPENRCX_PARTIAL",
    }
    try:
        gf_root = _find_gf_root(args.gf_root)
        paths = _gf_paths(gf_root)
        pdk = paths["pdk"]
        env = _env(gf_root)
        openroad = _openroad(args.openroad)
        if not openroad:
            raise FileNotFoundError("no openroad binary (set OPENROAD)")
        rules = _openrcx_rules(pdk, args.rules)
        techlef = _tech_lef(pdk)
        cellefs = _cell_lefs(pdk, args.cell_lef)
        report.update({
            "pdk": str(pdk), "openroad": openroad,
            "openrcx_rules": str(rules), "techlef": str(techlef),
            "cell_lefs": [str(c) for c in cellefs],
        })

        known = args.known_resistor or not args.defn
        if known:
            defn = workdir / "known_m1.def"
            _write_known_def(defn)
        else:
            defn = Path(args.defn).resolve()
            if not defn.is_file():
                raise FileNotFoundError(f"missing DEF: {defn}")
        report["def"] = str(defn)
        report["known_line"] = bool(known)

        spef = workdir / (defn.stem + ".spef")
        rep = _extract(techlef, cellefs, defn, rules, spef, workdir, env,
                       openroad)
        report["extraction"] = rep
        report["spef_sha256"] = _sha256(spef) if spef.is_file() else None
        net_r = rep["net_resistance_ohm"]
        report["net_resistance_ohm"] = net_r
        report["total_resistance_ohm"] = sum(net_r.values())

        if rep["returncode"] != 0 or not spef.is_file():
            report["reason"] = "openroad extract_parasitics failed"
        elif known:
            report["expected_r_ohm"] = EXPECTED_R_OHM
            got = report["total_resistance_ohm"]
            ok = abs(got - EXPECTED_R_OHM) / EXPECTED_R_OHM <= 0.10
            report["r_matches_expected"] = bool(ok)
            report["status"] = "OPENRCX_CALIBRATED" if ok else "OPENRCX_PARTIAL"
            report["reason"] = None if ok else (
                f"extracted {got} ohm, expected {EXPECTED_R_OHM}")
        else:
            report["status"] = "OPENRCX_EXTRACTED" if (
                report["total_resistance_ohm"] > 0) else "OPENRCX_PARTIAL"
            report["reason"] = None if report["status"] == "OPENRCX_EXTRACTED" \
                else "no resistance extracted"
    except Exception as exc:  # noqa: BLE001
        report["reason"] = str(exc)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "status": report["status"],
        "reason": report.get("reason"),
        "net_resistance_ohm": report.get("net_resistance_ohm"),
        "total_resistance_ohm": report.get("total_resistance_ohm"),
        "expected_r_ohm": report.get("expected_r_ohm"),
        "spef": report.get("extraction", {}).get("spef"),
        "openrcx_rules": report.get("openrcx_rules"),
    }, indent=2))
    return 0 if report["status"] in ("OPENRCX_CALIBRATED",
                                     "OPENRCX_EXTRACTED") else 1


if __name__ == "__main__":
    raise SystemExit(main())

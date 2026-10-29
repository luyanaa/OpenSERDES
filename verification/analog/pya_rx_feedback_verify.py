#!/usr/bin/env python3
"""Build and verify the pure-pya PCell RX nonlinear-feedback inverter.

Evidence: Magic DRC, LEF/CDL generation, Magic extraction with explicit ports,
and netgen LVS against the same 3N/6P + two-PMOS-feedback topology.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.magic_drc import run_drc  # noqa: E402
from verification.analog.magic_pex import find_pdk_root  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_gds(gds: Path, work: Path) -> dict[str, Any]:
    gds.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["OUT"] = str(gds)
    proc = subprocess.run(
        ["klayout", "-b", "-r", str(ROOT / "verification/analog/pya_rx_feedback.py")],
        cwd=work, env=env, text=True, capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    line = proc.stdout.strip().splitlines()[-1]
    m = re.search(r"bbox=\((.*)\)", line)
    return {"bbox": m.group(1) if m else None}


def build_views(gds: Path, work: Path) -> tuple[Path, Path]:
    lef = work / "rx_fb_pcell.lef"
    cdl = work / "rx_fb_pcell.cdl"
    env = dict(os.environ)
    env.update({"IN": str(gds), "LEF_OUT": str(lef), "CDL_OUT": str(cdl)})
    proc = subprocess.run(
        ["klayout", "-b", "-r", str(ROOT / "verification/analog/pya_rx_feedback_views.py")],
        cwd=work, env=env, text=True, capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout + proc.stderr)
    return lef, cdl


def extract(gds: Path, pdk: Path, work: Path) -> Path:
    tcl = work / "extract.tcl"
    spice = work / "extracted.spice"
    tcl.write_text(
        f"gds read {gds}\n"
        "load rx_fb_pcell -dereference\n"
        "select top cell\n"
        "port makeall\n"
        "extract\n"
        "ext2spice lvs\n"
        f"ext2spice -o {spice}\n"
        "quit\n"
    )
    env = dict(os.environ)
    env["PDK_ROOT"] = str(pdk)
    proc = subprocess.run(
        ["magic", "-dnull", "-noconsole", "-rcfile",
         str(pdk / "sky130A/libs.tech/magic/sky130A.magicrc"), str(tcl)],
        cwd=work, env=env, text=True, capture_output=True, timeout=300,
    )
    (work / "magic.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0 or not spice.exists():
        raise RuntimeError((proc.stdout + proc.stderr)[-4000:])
    return spice


def lvs(extracted: Path, cdl: Path, pdk: Path, work: Path) -> dict[str, Any]:
    setup = pdk / "sky130A/libs.tech/netgen/sky130A_setup.tcl"
    proc = subprocess.run(
        ["netgen", "-batch", "lvs",
         f"{extracted} rx_fb_pcell", f"{cdl} rx_fb_pcell", str(setup)],
        cwd=work, text=True, capture_output=True, timeout=300,
    )
    text = proc.stdout + proc.stderr
    (work / "netgen.log").write_text(text)
    return {
        "match": "Circuits match uniquely." in text,
        "returncode": proc.returncode,
        "tail": text[-2500:],
    }


def pex(gds: Path, pdk: Path, work: Path) -> dict[str, Any]:
    out = work / "rx_fb_pcell.pex.spice"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "verification/analog/magic_pex.py"),
         "--gds", str(gds), "--cell", "rx_fb_pcell",
         "--ports", "VDD,VSS,IN,OUT", "--out", str(out),
         "--workdir", str(work / "pex"), "--pdk-root", str(pdk)],
        cwd=work, text=True, capture_output=True, timeout=600,
    )
    return {
        "path": str(out) if out.exists() else None,
        "status": "available" if out.exists() else "missing",
        "returncode": proc.returncode,
        "tail": (proc.stdout + proc.stderr)[-2000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="build/verify/pcell_rx_fb.json")
    ap.add_argument("--workdir", default="build/verify/pcell_rx_fb")
    ap.add_argument("--pdk-root", default=None)
    args = ap.parse_args()

    pdk = find_pdk_root(args.pdk_root)
    work = ROOT / args.workdir
    if work.exists():
        for child in work.iterdir():
            if child.is_file():
                child.unlink()
    work.mkdir(parents=True, exist_ok=True)
    gds = work / "rx_fb_pcell.gds"
    build = build_gds(gds, work)
    drc = run_drc(gds, "rx_fb_pcell", work / "drc.json", work / "drc", str(pdk))
    lef, cdl = build_views(gds, work)
    extracted = extract(gds, pdk, work)
    lvs_result = lvs(extracted, cdl, pdk, work)
    pex_result = pex(gds, pdk, work)
    result = {
        "kind": "pure-pya-pcell-rx-feedback",
        "gds": str(gds),
        "gds_sha256": sha256(gds),
        "lef": str(lef),
        "lef_sha256": sha256(lef),
        "cdl": str(cdl),
        "cdl_sha256": sha256(cdl),
        "bbox_dbu": build["bbox"],
        "topology": "3N/6P inverter plus two series W=0.55/L=8 PMOS feedback devices",
        "drc": {
            "clean": drc["clean"],
            "errors": drc["drc_errors"],
            "evidence": str(work / "drc.json"),
        },
        "lvs": lvs_result,
        "pex": pex_result,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "note": "DRC, LEF/CDL, hierarchical LVS, and Magic RC PEX diagnostic evidence only; no STA/signoff claim. PMOS well/body ties are not generated by the pure-pya device cells.",
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "drc_errors": result["drc"]["errors"],
        "drc_clean": result["drc"]["clean"],
        "lvs_match": result["lvs"]["match"],
        "gds": str(gds),
    }))
    return 0 if result["drc"]["clean"] and result["lvs"]["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

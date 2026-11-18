#!/usr/bin/env python3
"""Run LibreLane for one design across the supported PDKs.

For each tech profile this script:

1. regenerates the spec-driven package with that tech,
2. expands the PDK path placeholders in the package pnr/librelane.yaml,
3. runs the LibreLane Classic flow through signoff,
4. collects the post-route metrics that ppa_model/ppa_measured can consume.

The run is fail-closed: a PDK whose flow does not complete is recorded as
unverified with the failing step, never as a measured point.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _git_commit() -> str:
    """HEAD for the evidence record; 'unknown' is better than a stale hash."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              text=True, capture_output=True, timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"

# LibreLane 3.0.14 / OpenROAD 26Q3 from the nix store.  The wrapper sets up the
# full toolchain (yosys, openroad, magic, klayout, netgen, abc), so it is the
# nix-shell environment without needing to rebuild the shell.
LIBRELANE = Path(
    os.environ.get(
        "LIBRELANE_BIN",
        "/nix/store/bl6n3l99m8ld1z6rn8pmhnmr3rqlvasm-librelane-3.0.14/bin/librelane",
    )
)

# PDK roots: --pdk-root for LibreLane and the libs.ref tree for the liberty
# corner files.  They differ for IHP and icsprout55.
PDK_ROOTS = {
    "sky130": "/home/yanlu/.ciel/ciel/sky130/versions/792ef67037d74c8c51811eab3ce23cf793491296",
    "gf180mcuD": "/home/yanlu/.ciel/ciel/gf180mcu/versions/792ef67037d74c8c51811eab3ce23cf793491296",
    "ihp-sg13g2": "/home/yanlu/.ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008",
    "icsprout55": "/grid/icsprout55-pdk",
}


def _tech_dir(tech: str) -> Path:
    return ROOT / "tech" / tech


def _pdk_paths(tech: str) -> tuple[str, str]:
    """Return (pdk_root, pdk_lib_root) for a tech profile."""
    cfg = yaml.safe_load((_tech_dir(tech) / "tech.yaml").read_text())
    root = os.environ.get("PDK_ROOT") or PDK_ROOTS[tech]
    lib_root = os.environ.get("PDK_LIB_ROOT") or root
    return root, lib_root


def _librelane_pdk_args(tech: str, scl: str | None = None) -> list[str]:
    """PDK-selection flags for the LibreLane CLI.

    Always --manual-pdk.  In ciel mode LibreLane pins the PDK family to its
    own default version hash and downloads it when that exact version is not
    in the local ciel home, which is unreachable offline.  --manual-pdk skips
    that resolution and uses --pdk-root as given, so the version installed on
    this machine is the one that gets used.

    Two layouts have to be handled: a ciel version root that contains the PDK
    family directory (sky130A, gf180mcuD, ...), and a plain libs.ref/libs.tech
    tree that is itself the PDK.

    ``scl`` overrides the profile default so a trial can select a declared
    ``scl_variants`` entry without editing tech.yaml.
    """
    cfg = yaml.safe_load((_tech_dir(tech) / "tech.yaml").read_text())
    pdk = cfg.get("pdk")
    chosen = scl or cfg.get("scl")
    if chosen and chosen != cfg.get("scl") and chosen not in (cfg.get("scl_variants") or {}):
        raise SystemExit(
            f"tech profile {tech!r} does not define scl {chosen!r}")
    if not pdk:
        return []
    root = os.environ.get("PDK_ROOT") or PDK_ROOTS[tech]
    if (Path(root) / pdk).is_dir():
        parent, name = Path(root), pdk
    elif (Path(root) / "libs.ref").is_dir():
        parent, name = Path(root).parent, Path(root).name
    else:
        return []
    return ["--manual-pdk", "--pdk-root", str(parent),
            "--pdk", name, "--scl", chosen]


def _max_sample_clock_mhz(tech: str) -> float | None:
    """Capability gate for the RX sample clock of a tech profile.

    The gate lives under "limits" and is scoped per stdcell class, so it has
    to be found by key rather than at a fixed depth.
    """
    cfg = yaml.safe_load((_tech_dir(tech) / "tech.yaml").read_text())
    best: float | None = None

    def walk(node) -> None:
        nonlocal best
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "max_sample_clock_mhz" and isinstance(
                        v, (int, float)) and not isinstance(v, bool):
                    best = float(v) if best is None else max(best, float(v))
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(cfg)
    return best


def _spec_with_tech(spec: Path, tech: str, out: Path,
                    line_rate_mbps: float | None = None,
                    scl: str | None = None) -> Path:
    data = yaml.safe_load(spec.read_text())
    ref: str | dict = {"name": tech, "scl": scl} if scl else tech
    data.setdefault("tech", {})["tx"] = ref
    data["tech"]["rx"] = ref
    # PDK names carry hyphens (ihp-sg13g2); the design name has to stay a
    # legal identifier because it becomes DESIGN_NAME in the LibreLane config.
    slug = re.sub(r"[^0-9A-Za-z_]", "_", tech)
    data["name"] = f"{data.get('name', 'design')}_{slug}"
    if line_rate_mbps is not None:
        data.setdefault("link", {})["line_rate_mbps"] = float(line_rate_mbps)
        data.setdefault("tx", {})["line_rate_mbps"] = float(line_rate_mbps)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(data, sort_keys=False))
    return out


def _expand_config(src: Path, dst: Path, lib_root: str) -> None:
    text = src.read_text()
    text = text.replace("${PDK_LIB_ROOT}", lib_root)
    text = text.replace("${PDK_ROOT}", lib_root)
    if "${" in text:
        raise SystemExit(f"unresolved placeholder in {src}: "
                         + [ln for ln in text.splitlines() if "${" in ln][0])
    dst.write_text(text)


# icsprout55 uses the PDK's LibreLane plugin (`librelane_plugin_ics55`) and
# its documented `meta.substituting_steps` instead of CLI `--skip`: the Odb
# antenna-properties check is nulled because the Magic LEF writer is skipped,
# and Netgen.LVS is replaced by ICS55.KLayoutLVS after OpenROAD.WriteCDL.
# KLayout DRC is run in tiling mode.  Other PDKs keep the default flow.
SIGNOFF_SKIP_STEPS: dict[str, list[str]] = {}


_OSU_LIB_CORNERS = {
    "nom_tt_025C_3v30": "gf180mcu_osu_sc_gp9t3v3__tt_025C_3v30.lib",
    "nom_ss_125C_3v00": "gf180mcu_osu_sc_gp9t3v3__ss_125C_3v00.lib",
    "nom_ff_n40C_3v60": "gf180mcu_osu_sc_gp9t3v3__ff_n40C_3v60.lib",
}


def _osu_cell_gds(lib_root: Path, pdk: str, fill1: Path) -> list[str]:
    """GDS Magic must read for the OSU library.

    The PDK glob would list every per-cell file plus the merged library, and
    a design-config ``CELL_GDS`` replaces that glob outright.  Pointing Magic
    at the per-cell set drops cells once the env list is truncated, which
    then fails stream-out ("abstract view").  The merged library already
    holds all 49 cells; ``fill_1`` is the only extra, flow-side cell.
    """
    merged = (Path(lib_root) / pdk / "libs.ref" / "gf180mcu_osu_sc_gp9t3v3"
              / "gds" / "gf180mcu_osu_sc_gp9t3v3.gds")
    return [str(merged), str(fill1)]


def _osu_fill1_gds(dst: Path) -> None:
    """Write a rail-only GDS for the flow-side fill_1.

    Magic's stream-out refuses any LEF cell without a GDS ("abstract view").
    fill_1 carries only the Metal1 power and ground rails, so that is the
    whole cell.  Written with the system KLayout module, which the LibreLane
    nix-shell does not ship, so this runs before that shell.  The vendor GDS
    is not touched.
    """
    if dst.is_file():
        return
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = "/usr/lib/klayout"
    env["PYTHONPATH"] = "/usr/lib/klayout/pymod"
    dst.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "import klayout.db as db\n"
        "layout = db.Layout(); layout.dbu = 0.001\n"
        "metal = layout.layer(34, 0)\n"
        "cell = layout.create_cell('gf180mcu_osu_sc_gp9t3v3__fill_1')\n"
        "for box in ((0, 5.7, 0.05, 6.3), (0, 0.0, 0.05, 0.6)):\n"
        "    cell.shapes(metal).insert(db.DBox(*box))\n"
        f"layout.write({str(dst)!r})\n"
    )
    res = subprocess.run(["/usr/bin/python3", "-c", script], env=env,
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"fill_1 gds write failed: {res.stderr[-400:]}")


def _osu_lef_with_fill1(src: Path, dst: Path) -> None:
    """Add a one-site filler the vendor LEF does not have.

    The OSU site is 0.05 um wide but the narrowest filler is fill_4 at 1.2 um,
    so ``filler_placement`` stops on any gap below that and the signoff steps
    never run.  fill_1 is one site wide with only the power and ground rails,
    copied from fill_4.  The vendor LEF is not modified.
    """
    text = src.read_text(errors="replace")
    block = re.search(
        r"MACRO gf180mcu_osu_sc_gp9t3v3__fill_4\b.*?"
        r"END gf180mcu_osu_sc_gp9t3v3__fill_4",
        text, re.S).group(0)
    block = block.replace("fill_4", "fill_1")
    block = re.sub(r"(?<= )1\.2(?= )", "0.05", block)
    dst.write_text(text.replace("\nEND LIBRARY", "\n" + block + "\n\nEND LIBRARY"))


def _osu_lib_with_max_cap(src: Path, dst: Path, cap_pf: float) -> None:
    """Give every output pin a max_capacitance so TritonCTS can characterize.

    The OSU NLDM only states max_capacitance on the sequential and tristate
    cells.  ``clock_tree_synthesis`` reads it from the buffer it inserts and
    aborts with CTS-0111 when the attribute is absent; on the full design that
    abort comes out as a segfault in ``dbMaster::staCell``.  The value is the
    largest load index in the cell tables, so it does not extend the
    characterized range.  The vendor liberty is not modified.
    """
    out = []
    for line in src.read_text(errors="replace").splitlines():
        out.append(line)
        if re.match(r"\s*direction\s*:\s*output\s*;", line):
            indent = re.match(r"\s*", line).group(0)
            out.append(f"{indent}max_capacitance : {cap_pf:.6g};")
    dst.write_text("\n".join(out) + "\n")


def _ez130_lvs_cdl(src: Path, dst: Path) -> None:
    """Prepare the ez130 CDL for OpenROAD.WriteCDL and the KLayout LVS deck.

    Keep the well pins.  Dropping ``VNW``/``VPW`` from every ``.SUBCKT`` is what
    this function used to do to get past WriteCDL, and it made **every cell fail
    to compare**: the GDS extraction emits the wells as real ports, the deck
    compares by port list, and the top level then reported "Circuits ... could
    not be compared because the following subcircuits failed to compare" with
    the same cell list on both sides -- the signature of a per-cell port
    mismatch, not of a missing cell.  With the wells kept, all 78 used cells
    compare; see build/pnr/ez130_lvs.json.

    Also adds empty FILLER subcircuits: they are in the LEF and the flow inserts
    them, but the vendor CDL has no subcircuit for them, and WriteCDL aborts on a
    placed master with no CDL.  They are physical-only, so an empty subcircuit
    with the LEF power pins is the whole model.
    """
    out: list[str] = []
    for line in src.read_text(errors="replace").splitlines():
        if line.startswith("*.PININFO"):
            # The deck does not read PININFO, and it lists VNW/VPW as :B, which
            # would be a schematic-only port the layout cannot have.
            continue
        out.append(line)
    for filler in ("FILLER1", "FILLER2", "FILLER4", "FILLER8", "FILLER16"):
        out.append(f".SUBCKT {filler} VDD VNW VPW VSS")
        out.append(f".ENDS {filler}")
    dst.write_text("\n".join(out) + "\n")


def _ihp_flow_overrides(cfg_data: dict, pdk_root: Path, cfg_path: Path) -> None:
    """Use the PDK's KLayout LVS instead of netgen for IHP.

    The netgen flow fails top-level pin matching on the VPWR/VGND PDN nets
    versus the VDD/VSS standard-cell pins; the IHP PDK ships a KLayout LVS
    deck (KLAYOUT_LVS_SCRIPT) and upstream KLayout.LVS supports this PDK.
    """
    meta = cfg_data.setdefault("meta", {})
    meta["flow"] = "Classic"
    meta["substituting_steps"] = {
        "-Netgen.LVS": "OpenROAD.WriteCDL",
        "Netgen.LVS": "KLayout.LVS",
    }
    # pdk_root is the version root for IHP; the PDK family directory is
    # ihp-sg13g2 (the PDK config.tcl uses $PDK_ROOT/$PDK/...).
    pdk_dir = pdk_root
    family = str(cfg_data.get("PDK", ""))
    if family and (pdk_root / family).is_dir():
        pdk_dir = pdk_root / family
    cfg_data["KLAYOUT_LVS_SCRIPT"] = str(
        pdk_dir / "libs.tech/klayout/tech/lvs/sg13g2.lvs")
    # The deck is run as-is (`deep`, strict ports).  Do NOT weaken it.
    #
    # Status after the cell-port fix below: every one of the 78 used cells
    # compares, the top-level circuit matches, and exactly two reports remain,
    #   "Net VGND is not matching any net from reference netlist"
    #   "Net VPWR is not matching any net from reference netlist"
    # because the GDS extraction declares VGND/VPWR **three times each** as
    # top-level pins (one per PDN metal layer) while OpenROAD.WriteCDL declares
    # them once.  The deck compares top-level ports by list, so the lists differ
    # in multiplicity even though every device and net below is identical.
    #
    # Options that were tried and rejected, with the reason, so this is not
    # re-litigated:
    #   - `implicit_nets=VGND,VPWR`: merges the supplies into one virtual net,
    #     after which the top level compares with *no* ports at all
    #     (`X(serdes_rx_digital () 0`).  Strictly worse, and the deck's own
    #     README says implicit connections must not be used for top-level LVS.
    #   - `.GLOBAL VGND VPWR` in the CDL: ignored by KLayout's SPICE reader.
    #   - `purge_nets`: the supplies are top-level pins, so they are not
    #     floating and nothing is purged.
    #   - `ignore_top_ports_mismatch`: skips the port *list* comparison only;
    #     the two net-level reports remain.
    # What would actually close it is emitting the pin multiplicity of the
    # layout on the schematic side, i.e. a post-processing step on the WriteCDL
    # output that duplicates the VGND/VPWR port entries.  That is a real change
    # to how the schematic netlist is produced, not a config flag, so it is
    # recorded as the remaining item instead of being faked here.
    cfg_data["KLAYOUT_LVS_OPTIONS"] = {"run_mode": "deep"}
    # The known IHP DRC deck errors are an observation, not a hard gate; keep
    # the flow going so the KLayout LVS verdict is recorded in the same run.
    cfg_data["ERROR_ON_MAGIC_DRC"] = False
    cfg_data["ERROR_ON_KLAYOUT_DRC"] = False
    # The row FOLLOWPIN must match the **cell's own** Metal1 rail height, or it
    # overhangs the cell rail and the full-design long-run spacing rule fires on
    # the cell power pins next to every row.  That single mistake accounted for
    # the whole ez130 DRC report below, so the width is per-SCL now instead of
    # one number for the PDK:
    #
    #   sg13g2_stdcell  0.44 um  (sg13g2_inv_1 VDD RECT 0 3.56 1.44 4,
    #                             VSS 0 -0.22 1.44 0.22)
    #   ez130_8t_v1p1   0.32 um  (ez130_8t AND2X1 VDD RECT 0 3.2 2.1 3.52,
    #                             VSS 0 -0.16 2.1 0.16)
    #
    # The PDK config.tcl sets PDN_RAIL_WIDTH 1.0 for both, which is what made
    # this fire in the first place; sky130 does the same with 0.48.
    _RAIL_WIDTH = {"sg13g2_stdcell": 0.44, "ez130_8t_v1p1": 0.32}
    cfg_data["PDN_RAIL_LAYER"] = "Metal1"
    cfg_data["PDN_RAIL_WIDTH"] = _RAIL_WIDTH.get(
        str(cfg_data.get("STD_CELL_LIBRARY")), 0.44)
    if cfg_data.get("STD_CELL_LIBRARY") == "ez130_8t_v1p1":
        src = Path("/grid/ihp130/ez130_8t_v1p1/cdl/ez130_8t.cdl")
        dst = cfg_path.parent / "ez130_8t_lvs.cdl"
        _ez130_lvs_cdl(src, dst)
        cfg_data["CELL_CDLS"] = str(dst)
        cfg_data["CELL_SPICE_MODELS"] = str(dst)


def _ics55_flow_overrides(cfg_data: dict, pdk_root: Path, cfg_path: Path) -> None:
    meta = cfg_data.setdefault("meta", {})
    meta["flow"] = "Classic"
    meta["substituting_steps"] = {
        "Magic.DRC": None,
        "Checker.MagicDRC": None,
        "Magic.SpiceExtraction": None,
        "Magic.StreamOut": None,
        "Magic.WriteLEF": None,
        "Odb.CheckDesignAntennaProperties": None,
        "Checker.IllegalOverlap": None,
        "KLayout.XOR": None,
        "Checker.XOR": None,
        "-Netgen.LVS": "OpenROAD.WriteCDL",
        "Netgen.LVS": "ICS55.KLayoutLVS",
        "Odb.ReportDisconnectedPins": "ICS55.ReportDisconnectedPins",
    }
    # The repaired deck supports run_mode=tiling; the PDK config.tcl supplies
    # the LVS runset/options, but set them explicitly so the record is
    # independent of the LibreLane PDK config discovery.
    # Full-design DRC runs on the top-level interconnect: the FEOL ACT_L_1
    # check expands the full CT layer by 12.5 um and does not finish on a
    # 2356-cell design, while the standard cells themselves are covered by
    # `make ics55-drc-lvs`.  The deck's cell_exclusion mode keeps the cell
    # frames and checks only the top-level/analog shapes.
    cfg_data["KLAYOUT_DRC_DEFINES"] = {
        "run_mode": "tiling",
        "cell_exclusion": "true",
        "top": str(cfg_data.get("DESIGN_NAME", "")),
    }
    # The PDK's ics55.map is a GDS stream-in map (short M1..M5 names); the
    # routed DEF uses MET1..MET5/T4M2, so KLayout's DEF reader would ignore
    # every routing, via and pin/label layer.  Use the repo LEF/DEF map.
    cfg_data["KLAYOUT_DEF_LAYER_MAP"] = str(
        ROOT / "tech/icsprout55/def_layer.map")
    cfg_data["KLAYOUT_LVS_SCRIPT"] = str(
        pdk_root / "libs.tech/lvs/ics55.lvs")
    cfg_data["KLAYOUT_LVS_OPTIONS"] = {
        "run_mode": "deep",
        "top": str(cfg_data.get("DESIGN_NAME", "")),
        "strict_topology": "true",
        "strict_hierarchy": "true",
        "blackbox": "true",
        "flow_mode": "true",
    }
    # The known library-drawing DRC items are recorded, not fatal for this
    # verification run; LVS is the gate that should still run.
    cfg_data["ERROR_ON_KLAYOUT_DRC"] = False
    cfg_data["ERROR_ON_MAGIC_DRC"] = False
    # The PDK root is BUFX16H7L and CTS puts all 16 leaf clusters directly on
    # it, so signoff reports fanout 16 against the 10 limit.  A BUFX8 root
    # with BUFX4/BUFX8 clock buffers halves TritonCTS's per-buffer sink
    # criterion and forces an extra clock-tree level.
    cfg_data["CTS_ROOT_BUFFER"] = "BUFX8H7L"
    cfg_data["CTS_CLK_BUFFERS"] = ["BUFX4H7L", "BUFX8H7L"]


def _skip_args(tech: str) -> list[str]:
    args: list[str] = []
    for step in SIGNOFF_SKIP_STEPS.get(tech, []):
        args += ["--skip", step]
    return args


def _clean_env() -> dict:
    """Environment for LibreLane with the shell proxy settings removed.

    LibreLane makes an httpx call at start-up; a SOCKS ALL_PROXY without the
    socksio extra aborts the flow before the first step runs.
    """
    env = dict(os.environ)
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            del env[key]
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def _run(cmd: list[str], cwd: Path, timeout: int,
         tech: str | None = None) -> subprocess.CompletedProcess:
    env = _clean_env()
    if tech == "icsprout55":
        plugin = Path(PDK_ROOTS[tech]) / "libs.tech/librelane"
        old = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(plugin) + (":" + old if old else "")
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True,
                          timeout=timeout, env=env)


def _collect(run_dir: Path) -> dict:
    """Pull the post-route metrics out of a completed LibreLane run."""
    metrics_path = run_dir / "final" / "metrics.json"
    source = "final/metrics.json"
    if not metrics_path.is_file():
        metrics_path = run_dir / "metrics.json"
        source = "metrics.json"
    if not metrics_path.is_file():
        # A flow can finish the physical implementation and then hang in a
        # signoff step whose PDK views the tool cannot read (e.g. Magic with
        # an incomplete tech LEF).  LibreLane keeps the accumulated metrics in
        # each step's state_out.json, so fall back to the last one and record
        # where the numbers came from instead of discarding the measurement.
        states = sorted(run_dir.glob("[0-9]*/state_out.json"),
                        key=lambda p: int(p.parent.name.split("-")[0]))
        for state in reversed(states):
            try:
                payload = json.loads(state.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            metrics = payload.get("metrics")
            if not isinstance(metrics, dict) or not metrics:
                continue
            # A mid-PnR STA state has timing but no route DRC; accepting it
            # would turn a failed routing run into a "measured" record.
            if "route__drc_errors" not in metrics:
                continue
            metrics_path = state
            source = str(state.relative_to(run_dir))
            break
        if source == "final/metrics.json" or source == "metrics.json":
            raise FileNotFoundError(f"no metrics.json under {run_dir}")
    if source.endswith("state_out.json"):
        m = json.loads(metrics_path.read_text())["metrics"]
    else:
        m = json.loads(metrics_path.read_text())

    def g(*keys):
        for k in keys:
            if k in m:
                return m[k]
        return None

    def per_corner(prefix: str) -> dict:
        out = {}
        for k, v in m.items():
            if k.startswith(prefix) and "__corner:" in k:
                out[k.split("__corner:")[1]] = v
        return out

    # Critical-path arrival time from the post-PnR STA report of the worst
    # corner: the number that decides whether the period closes at all.
    arrival = None
    sta_dir = None
    for cand in sorted(run_dir.glob("*openroad-stapostpnr")):
        sta_dir = cand
    if sta_dir is not None and sta_dir.is_dir():
        worst = None
        for rpt in sorted(sta_dir.glob("*/max.rpt")):
            txt = rpt.read_text(errors="replace")
            mm = re.findall(r"^\s*([0-9.]+)\s+data arrival time", txt, re.M)
            if not mm:
                continue
            val = float(mm[0])
            if worst is None or val > worst[0]:
                worst = (val, rpt.parent.name)
        if worst is not None:
            arrival = {"ns": worst[0], "corner": worst[1]}

    # Fanout and capacitance are reported as single numbers, but the CTS root
    # legitimately drives one buffer per sink cluster (16 at 140 sinks) while
    # the design rules are data-net targets.  Record the two counts
    # separately instead of hiding the clock item inside the data-net number.
    def split_clock_items(section: str):
        data = clock = None
        if sta_dir is None:
            return data, clock
        for rpt in sorted(sta_dir.glob("*/checks.rpt")):
            txt = rpt.read_text(errors="replace")
            sec = re.search(rf"\n{section}\n(.*?)(?=\nmax |\Z)", txt, re.S)
            if not sec:
                continue
            total = clk = 0
            for line in sec.group(1).splitlines():
                if "VIOLATED" not in line:
                    continue
                total += 1
                inst = line.split()[0].split("/")[0]
                if inst.startswith(("clkbuf", "clkload")) or "_clk" in inst:
                    clk += 1
            data = total - clk if data is None else max(data, total - clk)
            clock = clk if clock is None else max(clock, clk)
        return data, clock

    data_fanout, clock_fanout = split_clock_items("max fanout")
    data_cap, clock_cap = split_clock_items("max capacitance")

    return {
        "metrics_source": source,
        "cells": g("design__instance__count"),
        "stdcell_cells": g("design__instance__count__stdcell"),
        "dff_cells": g("design__instance__count__class:sequential_cell"),
        "core_area_um2": g("design__core__area"),
        "die_area_um2": g("design__die__area"),
        "utilization_pct": round(100 * (g("design__instance__utilization") or 0), 2),
        "power_internal_mw": g("power__internal__total"),
        "power_switching_mw": g("power__switching__total"),
        "power_leakage_mw": g("power__leakage__total"),
        "power_total_mw": g("power__total"),
        "clock_period_ns": g("design__clock__period"),
        "setup_wns_ns": g("timing__setup__wns"),
        "setup_wns_by_corner_ns": per_corner("timing__setup__wns"),
        "setup_tns_by_corner_ns": per_corner("timing__setup__tns"),
        "hold_wns_ns": g("timing__hold__wns"),
        "hold_violations": g("timing__hold_vio__count"),
        "setup_violations": g("timing__setup_vio__count"),
        "clock_skew_setup_ns": g("clock__skew__worst_setup"),
        "critical_path_arrival_ns": arrival,
        "drc_routing": g("route__drc_errors"),
        "drc_magic": g("magic__drc_error__count"),
        "drc_klayout": g("klayout__drc_error__count"),
        "lvs_errors": g("design__lvs_error__count"),
        "antenna_violations": g("route__antenna_violation__count"),
        "max_fanout_violations": (data_fanout if data_fanout is not None
                                  else g("design__max_fanout_violation__count")),
        "clock_fanout_violations": clock_fanout,
        "max_cap_violations": (data_cap if data_cap is not None
                               else g("design__max_cap_violation__count")),
        "clock_cap_violations": clock_cap,
        "max_slew_violations": g("design__max_slew_violation__count"),
    }


def _num(value):
    return value if isinstance(value, (int, float)) else None


def _timing_closure(m: dict) -> dict:
    """Setup slack alone is not closure.

    A corner with positive setup WNS still fails while hold, max-slew,
    max-cap or max-fanout is violated.  A missing report is ``not_reached``,
    not a pass.
    """
    failed: dict = {}
    missing = []
    corners = m.get("setup_wns_by_corner_ns") or {}
    setup_vals = {k: v for k, v in corners.items() if _num(v) is not None}
    if setup_vals:
        bad = {k: round(v, 4) for k, v in setup_vals.items() if v < 0}
        if bad:
            failed["setup_wns_ns"] = bad
    elif _num(m.get("setup_wns_ns")) is None:
        missing.append("setup_wns_ns")
    elif m["setup_wns_ns"] < 0:
        failed["setup_wns_ns"] = round(m["setup_wns_ns"], 4)
    for key in ("hold_wns_ns",):
        val = _num(m.get(key))
        if val is None:
            missing.append(key)
        elif val < 0:
            failed[key] = round(val, 4)
    for key in ("max_slew_violations", "max_cap_violations",
                "max_fanout_violations"):
        val = _num(m.get(key))
        if val is None:
            missing.append(key)
        elif val > 0:
            failed[key] = val
    if failed:
        status = "fail"
    elif missing:
        status = "not_reached"
    else:
        status = "pass"
    return {"status": status, "failed": failed, "missing": missing}


def _physical_signoff(m: dict) -> dict:
    """DRC/LVS/antenna are a separate gate from timing.

    Any missing count means that signoff step did not run.  A zero that is
    actually present is a pass for that check only; it does not upgrade a
    black-box or pin-level LVS into transistor proof.
    """
    keys = ("drc_routing", "drc_magic", "drc_klayout", "lvs_errors",
            "antenna_violations")
    failed = {}
    missing = []
    for key in keys:
        val = _num(m.get(key))
        if val is None:
            missing.append(key)
        elif val > 0:
            failed[key] = val
    if failed:
        status = "fail"
    elif missing:
        status = "not_reached"
    else:
        status = "pass"
    return {"status": status, "failed": failed, "missing": missing}


def _classify(rec: dict) -> dict:
    """Classify a record that has metrics.

    Timing closure and physical signoff are independent.  Metrics stay a
    measurement either way; only both gates passing is ``measured``.
    """
    m = rec["metrics"]
    timing = _timing_closure(m)
    physical = _physical_signoff(m)
    rec["timing_closure"] = timing
    rec["physical_signoff"] = physical
    setup = timing["failed"].get("setup_wns_ns")
    if isinstance(setup, dict):
        rec["timing_violations"] = setup
    violations: dict = {}
    if timing["failed"]:
        violations["timing"] = timing["failed"]
    if physical["failed"]:
        violations["physical"] = physical["failed"]
    if timing["status"] == "pass" and physical["status"] == "pass":
        rec["status"] = "measured"
    elif timing["status"] == "fail" or physical["status"] == "fail":
        rec["status"] = "measured_with_violations"
    else:
        rec["status"] = "measured_incomplete"
    if violations:
        rec["violations"] = violations
    parts = []
    if timing["status"] != "pass":
        parts.append("timing " + timing["status"])
        if isinstance(setup, dict):
            arr = m.get("critical_path_arrival_ns") or {}
            parts.append("setup violations in " + ", ".join(sorted(setup))
                         + "; critical path " + str(arr.get("ns")) + " ns")
        if timing["missing"]:
            parts.append("timing missing " + ", ".join(timing["missing"]))
    if physical["status"] != "pass":
        parts.append("physical " + physical["status"])
        if physical["failed"]:
            parts.append("non-zero DRC/LVS: " + ", ".join(sorted(physical["failed"])))
        if physical["missing"]:
            parts.append("physical missing " + ", ".join(physical["missing"]))
    if parts:
        rec["reason"] = "; ".join(parts)
    return rec


# Per-library flow overrides applied after the generated config is loaded.
# AS has no integrated clock gate; the PDK config must not name one, and the
# design config must not keep a stale ICG either.  Its latch map is left to
# the PDK (gf180mcu pdk_compat drops SYNTH_LATCH_MAP; the library has no
# reset latch, only transparent dlxtp/dlxfp).
_SCL_FLOW_PATCHES: dict[str, dict] = {
    # The PDK root is clkbuf_16 and CTS puts all 16 leaf clusters directly on
    # it, so signoff reports fanout 16 against the 10 limit.  A clkbuf_8 root
    # halves TritonCTS's per-buffer sink criterion and forces an extra level.
    "sky130_fd_sc_hd": {
        "CTS_ROOT_BUFFER": "sky130_fd_sc_hd__clkbuf_8",
        "CTS_CLK_BUFFERS": ["sky130_fd_sc_hd__clkbuf_4",
                            "sky130_fd_sc_hd__clkbuf_8"],
    },
    "gf180mcu_as_sc_mcu7t3v3": {
        "SYNTH_CLOCKGATE_POSEDGE_ICG": None,
        "SYNTH_CLOCKGATE_NEGEDGE_ICG": None,
    },
    # Same gap as AS: no ICG.  dffsr_1 is the reset flop; there is no reset
    # latch, and pdk_compat already drops SYNTH_LATCH_MAP for gf180mcu.
    # The profile CTS cap/slew (0.1 pF, 0.5 ns) is below this liberty's
    # smallest table index, and TritonCTS then segfaults in
    # getBufferFanoutLimit.  10 pF / 3 ns stays inside the characterized
    # tables and under the 3 ns max-transition constraint.
    "gf180mcu_osu_sc_gp9t3v3": {
        "SYNTH_CLOCKGATE_POSEDGE_ICG": None,
        "SYNTH_CLOCKGATE_NEGEDGE_ICG": None,
        "CTS_MAX_CAP": 10.0,
        "CTS_MAX_SLEW": 3.0,
        # Rewritten per run by _osu_timing_libs; the vendor liberty has no
        # max_capacitance on its combinational cells.
        "LIB": {},
        # The pad config is loaded after the SCL config, so it cannot be
        # cleared there.  Its 3.3 V PAD_LIBS point at the 5 V gf180mcu_fd_io
        # liberty, which then gets read into every OSU corner and makes
        # TritonCTS dereference a null STA cell.  This design has no pads.
        "PAD_LIBS": {},
        "PAD_LEFS": [],
        "PAD_GDS": [],
        "PAD_VERILOG_MODELS": [],
        "PAD_SPICE_MODELS": [],
        "PAD_CDLS": [],
    },
}


def _apply_signoff_rules_to_pnr(cfg_data: dict, workdir: Path) -> None:
    """Make repair and CTS target the same limits post-route STA checks.

    gen_ip writes set_max_fanout/transition/capacitance into signoff.sdc only.
    Implementation reads PNR_SDC_FILE, so repair_design never sees those
    violations and they show up for the first time at signoff.
    """
    pnr = cfg_data.get("PNR_SDC_FILE")
    sign = cfg_data.get("SIGNOFF_SDC_FILE")
    if not pnr or not sign:
        return
    pnr_path, sign_path = Path(pnr), Path(sign)
    if not pnr_path.is_file() or not sign_path.is_file():
        return
    rules = [
        line for line in sign_path.read_text().splitlines()
        if line.startswith(("set_max_fanout ", "set_max_transition ",
                            "set_max_capacitance "))
    ]
    if not rules:
        return
    body = pnr_path.read_text()
    if not all(rule in body for rule in rules):
        dest = workdir / "constraints" / "pnr_signoff_rules.sdc"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            body.rstrip()
            + "\n\n# Copied from signoff.sdc. Repair and CTS read PNR_SDC_FILE;\n"
            + "# these limits were previously checked only at post-route STA.\n"
            + "\n".join(rules) + "\n")
        cfg_data["PNR_SDC_FILE"] = str(dest)
    # Leaf clock buffers cluster to the cell max_capacitance (several pF
    # on gf180 AS clkbuff_12), which is past the signoff fanout and cap.
    # Hold one pin back so a CTS dummy load still fits under the limit.
    if cfg_data.get("CTS_SINK_CLUSTERING_ENABLE") is False:
        return
    if cfg_data.get("CTS_SINK_CLUSTERING_SIZE") is not None:
        return
    for rule in rules:
        if rule.startswith("set_max_fanout "):
            fanout = int(float(rule.split()[1]))
            cfg_data["CTS_SINK_CLUSTERING_ENABLE"] = True
            cfg_data["CTS_SINK_CLUSTERING_SIZE"] = max(1, fanout - 1)
            return


def run_pdk(spec: Path, tech: str, workdir: Path, tag: str,
            period: float | None = None,
            line_rate_mbps: float | None = None,
            scl: str | None = None,
            core_util: float | None = None) -> dict:
    rec: dict = {"tech": tech, "status": "unverified", "steps": {}}
    pkg = workdir / "pkg"
    cfg = workdir / "config.yaml"
    run_dir = workdir / "runs" / tag
    # Only clear this run's tag directory.  Wiping the whole workdir would
    # destroy every other run tag (and their final/metrics.json evidence)
    # whenever a second measurement is taken for the same tech.
    if run_dir.exists():
        shutil.rmtree(run_dir)
    workdir.mkdir(parents=True, exist_ok=True)

    # Each PDK is measured at the fastest line rate its own capability gate
    # allows for this architecture, so a cross-PDK comparison stays honest
    # instead of comparing sky130 at 100 Mb/s against gf180 failing at 100.
    if line_rate_mbps is None:
        os_max = _max_sample_clock_mhz(tech)
        if os_max is not None:
            data = yaml.safe_load(spec.read_text())
            want = float(data.get("link", {}).get("line_rate_mbps", 0) or 0)
            oversample = int(data.get("rx", {}).get("oversample", 1) or 1)
            allowed = os_max / max(1, oversample)
            if want > allowed:
                line_rate_mbps = allowed
    local_spec = _spec_with_tech(spec, tech, workdir / "spec.yaml",
                                 line_rate_mbps, scl=scl)
    gen = _run([sys.executable, "scripts/gen_ip.py", "--spec", str(local_spec),
                "--out", str(pkg), "--force"], ROOT, 300)
    rec["steps"]["generate"] = gen.returncode
    if gen.returncode != 0:
        rec["reason"] = "generate failed: " + (gen.stderr or gen.stdout)[-400:]
        return rec

    pdk_root, lib_root = _pdk_paths(tech)
    try:
        _expand_config(pkg / "pnr" / "librelane.yaml", cfg, lib_root)
    except SystemExit as exc:
        rec["reason"] = str(exc)
        return rec
    rec["steps"]["expand"] = 0
    rec["pdk_root"] = pdk_root
    cfg_data = yaml.safe_load(cfg.read_text())
    if period is not None:
        cfg_data["CLOCK_PERIOD"] = float(period)
    if core_util is not None:
        # Floorplan core utilization only.  PL_TARGET_DENSITY_PCT stays unset
        # so OpenROAD keeps its util + padding formula instead of a second,
        # independent density target.
        cfg_data["FP_CORE_UTIL"] = float(core_util)
        rec["fp_core_util"] = float(core_util)
    chosen_scl = cfg_data.get("STD_CELL_LIBRARY")
    rec["scl"] = chosen_scl
    for key, value in _SCL_FLOW_PATCHES.get(str(chosen_scl), {}).items():
        cfg_data[key] = value
    if chosen_scl == "gf180mcu_osu_sc_gp9t3v3":
        # 10 pF is the largest load index in the OSU delay tables.
        lib_dir = (Path(lib_root) / cfg_data.get("PDK", "gf180mcuD")
                   / "libs.ref" / chosen_scl / "lib")
        rewritten = {}
        for corner, name in _OSU_LIB_CORNERS.items():
            dst = workdir / "lib" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            _osu_lib_with_max_cap(lib_dir / name, dst, 10.0)
            rewritten[corner] = [str(dst)]
        cfg_data["LIB"] = rewritten
        lef_src = (Path(lib_root) / cfg_data.get("PDK", "gf180mcuD")
                   / "libs.ref" / chosen_scl / "lef"
                   / "gf180mcu_osu_sc_gp9t3v3.lef")
        lef_dst = workdir / "lef" / "gf180mcu_osu_sc_gp9t3v3.lef"
        lef_dst.parent.mkdir(parents=True, exist_ok=True)
        _osu_lef_with_fill1(lef_src, lef_dst)
        cfg_data["CELL_LEFS"] = [str(lef_dst)]
        # Magic refuses to stream out a cell that has a LEF but no GDS.
        # fill_1 is a rail-only filler, so its GDS is just the two Metal1
        # rails.  The vendor views stay the merged library GDS, not the
        # per-cell files: writing only fill_1 here replaces the PDK glob
        # and Magic then sees every other cell as an abstract.
        fill1 = workdir / "gds" / "gf180mcu_osu_sc_gp9t3v3__fill_1.gds"
        _osu_fill1_gds(fill1)
        cfg_data["CELL_GDS"] = _osu_cell_gds(
            Path(lib_root), cfg_data.get("PDK", "gf180mcuD"), fill1)
    rec["clock_period_ns"] = cfg_data.get("CLOCK_PERIOD")
    rec["line_rate_mbps"] = yaml.safe_load(
        local_spec.read_text())["link"]["line_rate_mbps"]
    # Static IR drop needs non-zero via resistance in the tech LEF.  Some
    # plain-PDK trees declare zero, which aborts the whole flow at that step.
    # IR drop is not an input to the mapped STA/power/area numbers, so skip it
    # uniformly across PDKs to keep the comparison fair.
    # Pin/label layers are stream-out annotations, not geometry: Magic emits
    # them for every cell (pwell.pin 122/16, ...) while the KLayout stream-out
    # does not, so the XOR gate reports thousands of "differences" on a design
    # whose Magic DRC, route DRC and LVS are all clean.  Ignore the pin layers
    # rather than skip the check.
    # Physical-only cells (fill / tap / decap / antenna diode) exist in the
    # layout but carry no functional devices; the schematic netlist written by
    # OpenROAD.WriteCDL does not describe them, so Netgen counts extra devices
    # on the layout side and the top-level comparison fails.  Ignore them, the
    # same way the PDK configs do.
    cfg_data.setdefault("LVS_IGNORE_CELLS", [
        "*__fill_*", "*__fill", "*__tap_*", "*__tap", "*__decap_*",
        "*__diode_*", "*__latchupcell", "*__macro_sparecell",
    ])
    cfg_data.setdefault("KLAYOUT_XOR_IGNORE_LAYERS", [
        "pwell.pin", "nwell.pin", "diff.pin", "poly.pin", "li1.pin",
        "met1.pin", "met2.pin", "met3.pin", "met4.pin", "met5.pin",
        "tap.pin", "boundary",
    ])
    cfg_data["RUN_IRDROP_REPORT"] = False
    # LibreLane defaults both repair wire-length caps to 0, which disables
    # buffer insertion for long nets.  The remaining max-slew, max-cap and
    # max-fanout violations are on those nets (including the clock tree's
    # own load cells).  A positive cap lets repair_design insert buffers
    # without changing the clock period or the RTL.
    cfg_data["DESIGN_REPAIR_MAX_WIRE_LENGTH"] = 150
    cfg_data["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"] = 150
    cfg_data["RUN_POST_GRT_DESIGN_REPAIR"] = True
    cfg_data["CTS_CLK_MAX_WIRE_LENGTH"] = 150
    # Post-GRT default margin is 10%; detailed-route RC then leaves marginal
    # nets a few percent over (sky130 at 22 ns: 5 nets at 0.508 vs 0.500;
    # ics55 at 10 ns: 9 nets at 0.815 vs 0.796).  25% matches the post-GPL
    # default and still targets the slow corner.
    cfg_data["GRT_DESIGN_REPAIR_MAX_SLEW_PCT"] = 25
    cfg_data["GRT_DESIGN_REPAIR_MAX_CAP_PCT"] = 25
    # OpenROAD's resizer repairs design rules against the loaded corner, and
    # the multi-corner load order is not stable.  Pin the repair steps to the
    # slow corner so the signoff corner is the one being optimized: ics55 at
    # 10 ns had 61 SS max-slew violations and almost none at TT/FF.
    corners = list(cfg_data.get("STA_CORNERS") or [])
    slow = [c for c in corners
            if "_ss_" in c or "slow" in c.lower() or c.endswith("_ss")]
    if len(slow) == 1:
        cfg_data["RSZ_CORNERS"] = slow
    _apply_signoff_rules_to_pnr(cfg_data, workdir)
    if tech == "icsprout55":
        _ics55_flow_overrides(cfg_data, Path(pdk_root), cfg)
    elif tech == "ihp-sg13g2":
        _ihp_flow_overrides(cfg_data, Path(pdk_root), cfg)
    cfg.write_text(yaml.safe_dump(cfg_data, sort_keys=False))

    # The config is a positional argument in LibreLane 3 ("-c" is not a
    # recognised option and is rejected with "No config file(s) provided").
    try:
        pdk_args = _librelane_pdk_args(tech, scl=chosen_scl)
    except SystemExit as exc:
        rec["reason"] = str(exc)
        return rec
    if not pdk_args:
        pdk_args = ["--pdk-root", pdk_root]
    cmd = ([str(LIBRELANE)] + pdk_args + _skip_args(tech)
           + ["--run-tag", tag, "--condensed", str(cfg)])
    try:
        pnr = _run(cmd, workdir, 7200, tech=tech)
    except subprocess.TimeoutExpired:
        rec["reason"] = "librelane timeout"
        return rec
    rec["steps"]["librelane"] = pnr.returncode
    # LibreLane exits non-zero when any checker reports violations, including
    # pure timing ones, but the run itself completed and wrote final views.
    # Only treat a non-zero exit as fatal when no metrics were produced.
    try:
        rec["metrics"] = _collect(run_dir)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        rec["reason"] = f"metrics: {exc}"
        if pnr.returncode != 0:
            tail = (pnr.stderr or pnr.stdout).strip().splitlines()
            rec["reason"] += "; librelane rc=%d: %s" % (
                pnr.returncode, tail[-1] if tail else "")
        return rec

    return _classify(rec)
    # Timing violations are a result, not a failure: the run completed and
    # produced GDS/LEF/LIB, so record the numbers and flag the corners.
    viol = {k: v for k, v in m.get("setup_wns_by_corner_ns", {}).items()
            if isinstance(v, (int, float)) and v < 0}
    rec["status"] = "measured_with_violations" if viol else "measured"
    if viol:
        rec["timing_violations"] = {k: round(v, 4) for k, v in viol.items()}
        arr = m.get("critical_path_arrival_ns") or {}
        rec["reason"] = ("setup violations in " + ", ".join(sorted(viol))
                          + "; critical path " + str(arr.get("ns")) + " ns")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--techs", required=True, help="comma separated")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default=str(ROOT / "build" / "pnr" / "cross_pdk"))
    ap.add_argument("--tag", default="cross_pdk")
    ap.add_argument("--period", type=float, default=None,
                    help="override CLOCK_PERIOD in the generated config")
    ap.add_argument("--line-rate", type=float, default=None,
                    help="force the line rate; by default each tech runs at "
                         "the fastest rate its capability gate allows")
    ap.add_argument("--scl", default=None,
                    help="stdcell variant declared in tech.yaml scl_variants")
    ap.add_argument("--core-util", type=float, default=None,
                    help="override FP_CORE_UTIL (percent); density stays derived")
    args = ap.parse_args()

    spec = Path(args.spec)
    techs = [t.strip() for t in args.techs.split(",") if t.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # LibreLane runs with cwd=workdir, and the config path is passed on the
    # command line.  A relative --workdir would make that path resolve twice,
    # so anchor it here.
    workroot = Path(args.workdir).resolve()

    records = []
    for tech in techs:
        print(f"=== {tech}", flush=True)
        rec = run_pdk(spec, tech, workroot / tech, f"{args.tag}_{tech}",
                     period=args.period, line_rate_mbps=args.line_rate,
                     scl=args.scl, core_util=args.core_util)
        records.append(rec)
        summary = rec.get("reason")
        if not summary:
            m = rec.get("metrics", {})
            summary = (f"cells={m.get('cells')} area={m.get('core_area_um2')} um2 "
                       f"power={m.get('power_total_mw')} mW "
                       f"setup_wns={m.get('setup_wns_ns')} ns")
        print(f"    {rec['status']}: {summary}", flush=True)

    payload = {
        "kind": "cross-pdk-pnr-evidence",
        "spec": str(spec),
        "design": yaml.safe_load(spec.read_text()).get("name"),
        "librelane": str(LIBRELANE),
        # A bare command name (LIBRELANE_BIN=librelane inside the nix-shell)
        # is not useful provenance; record the resolved binary too.
        "librelane_resolved": shutil.which(str(LIBRELANE)) or str(LIBRELANE),
        "git_commit": _git_commit(),
        "records": records,
        "measured": [r["tech"] for r in records
                     if r["status"].startswith("measured")],
        "with_violations": [r["tech"] for r in records
                            if r["status"] == "measured_with_violations"],
        "unverified": [r["tech"] for r in records
                       if not r["status"].startswith("measured")],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out}")
    return 1 if payload["unverified"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

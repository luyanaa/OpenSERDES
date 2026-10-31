#!/usr/bin/env python3
"""Generate a LibreLane config for the mixed digital+analog top.

This consumes a generated package filelist and the validated macro views.  It
does not run PnR; it only creates a reproducible config/SDC for the real
LibreLane environment.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# OpenSERDES hard-macro power hookup.
#
# Verified in OpenROAD pdngen: a `-macro` grid's add_pdn_connect only bridges
# strap shapes that grid itself draws -- it cannot reach the stdcell_grid
# straps, so any macro-local strap is a floating island (PSM-0025).  The two
# custom cells are too small (~10-20 um) for a ring (PDN-0351) or a 153 um
# pitch strap (PDN-0185), and rx_res_fb_pmos carries power only on met1 stubs
# which no -macro grid can wire to the met4/met5 global grid.
#
# The honest fix (the same technique real LibreLane designs use, e.g. the
# ankhdjet pdn_patch) is to let pdngen build the stdcell grid normally, then
# wrap `pdngen` so that, right after it runs, pdn_patch.tcl draws the binding
# geometry in the ODB: a via stack from each macro power pin up to the strap
# layer plus the metal run that overlaps the nearest generated strap on the
# same net.  That is real layout -- via stacks onto the pin geometry the GDS
# actually carries -- not a fabricated strap.
_PDN_MACRO_GRID = """

# OpenSERDES hard-macro power hookup.  A -macro grid's add_pdn_connect only
# bridges shapes that grid itself draws, so any macro-local strap is a
# floating island (PSM-0038) that never reaches the global grid -- and an
# empty macro grid aborts pdngen (PDN-0233).  Neither is needed: the iterms
# are already bound to VPWR/VGND by PDN_MACRO_CONNECTIONS (global connect),
# and the wrapped pdngen below sources pdn_patch.tcl to draw the real
# binding.  So we deliberately declare NO -macro grids at all, then patch.
#
# (Left intentionally blank: no -macro grid definitions.)

# Wrap pdngen: run the real one, then source pdn_patch.tcl (next to this
# config in DESIGN_DIR) to draw via-stack binding from each macro power pin
# to the nearest generated strap on the same net.  Build the command list
# explicitly -- `uplevel ... {*}$args` would not re-expand {*} inside uplevel.
if { [info commands ::_oserdes_real_pdngen] eq "" } {
    rename ::pdngen ::_oserdes_real_pdngen
    proc ::pdngen {args} {
        set r [uplevel 1 [linsert $args 0 ::_oserdes_real_pdngen]]
        set patch [file join $::env(DESIGN_DIR) pdn_patch.tcl]
        if { [file exists $patch] } {
            uplevel #0 [list source $patch]
        }
        return $r
    }
}
"""

# pdn_patch.tcl is written next to the config and sourced by the wrapped
# pdngen.  For every power/ground pin on each named macro instance it:
#   1. reads the iterm's die-absolute bbox and the pin's layer,
#   2. draws a via stack straight up from the pin layer to met4
#      (met1 pins: via/via2/via3; met4 pins: nothing needed, already on
#      the strap layer),
#   3. draws a horizontal met4 stub from that stack to the nearest met4
#      strap shape already generated on the same net, so the pin is now
#      electrically bound to the global grid.
# Every box is drawn with odb::dbSBox_create on the net's dbSWire, so the
# shapes are real routed geometry that DRC/LVS see.
_PDN_PATCH = r'''
# Sourced by the wrapped pdngen after the stdcell grid is generated.  Binds
# the hard-macro power pins to the global met4 straps.  All coords in DBU.
set block [ord::get_db_block]
set tech  [ord::get_db_tech]

proc _layer {name} {
    return [[ord::get_db_tech] findLayer $name]
}

# find a dbTechVia (prefab) connecting layer a -> b by name.  getVias returns
# generate-rule objects too whose layer accessors differ, so match on the
# canonical sky130 prefab via names via findVia (returns a real dbTechVia).
proc _via_between {a b} {
    set tech [ord::get_db_tech]
    set key "${a} ${b}"
    set vname ""
    switch -- $key {
        "li1 met1"  { set vname L1M1_PR }
        "met1 met2" { set vname M1M2_PR }
        "met2 met3" { set vname M2M3_PR }
        "met3 met4" { set vname M3M4_PR }
        "met4 met5" { set vname M4M5_PR }
    }
    if { $vname eq "" } { return "" }
    set v [$tech findVia $vname]
    if { $v eq "NULL" } { return "" }
    return $v
}

# Collect every strap on $net on a given routing layer as {x0 x1 y0 y1}
# quads, skipping the NULL placeholders getWires leaves for pruned boxes.
proc _straps {net layname} {
    set out {}
    foreach sw [$net getSWires] {
        if { $sw eq "NULL" || $sw eq "" } { continue }
        foreach sb [$sw getWires] {
            if { $sb eq "NULL" || $sb eq "" } { continue }
            set lay ""
            catch { set lay [[$sb getTechLayer] getName] }
            if { $lay ne $layname } { continue }
            lappend out [list [$sb xMin] [$sb xMax] [$sb yMin] [$sb yMax]]
        }
    }
    return $out
}

# Nearest *global* met5 (horizontal) strap on $net to (px,py), by y-distance.
# Macro-grids also draw small local met5 straps inside the instance bbox --
# those are floating islands, not the global grid, so bind only to straps
# that span (nearly) the whole die width.
proc _nearest_m5 {net px py} {
    set best "" ; set bd 1e30
    foreach s [_straps $net met5] {
        lassign $s x0 x1 y0 y1
        # skip macro-local stubs (width < 100 um): only global straps span the die
        if { [expr {$x1-$x0}] < 100000 } { continue }
        set cy [expr {($y0+$y1)/2.0}]
        set d [expr {abs($cy-$py)}]
        if { $d < $bd } { set bd $d ; set best $s }
    }
    return $best
}

# Bind a macro power pin to the global grid.  Route strategy (verified
# short-free): via-stack the pin up to met4, run a VERTICAL met4 column in
# the pin's own x to the nearest same-net met5 horizontal strap, then a via
# up to met5.  A vertical met4 column cannot cross an opposite-net vertical
# met4 strap (different x), and crossing a horizontal met5 strap on a
# different layer is harmless -- vias only form where we place them.
proc _bind_pin {inst pinname netname} {
    set block [ord::get_db_block]
    set tech  [ord::get_db_tech]
    set i [$block findInst $inst]
    if { $i eq "NULL" || $i eq "" } { return }
    set it [$i findITerm $pinname]
    if { $it eq "NULL" || $it eq "" } { return }
    set bb [$it getBBox]
    set py [expr {int(([$bb yMin]+[$bb yMax])/2)}]
    set net [$block findNet $netname]
    if { $net eq "NULL" || $net eq "" } { return }
    set sw [odb::dbSWire_create $net ROUTED]
    # For a wide pin (TX's VDD/VSS rails span the macro width), choose a
    # column x that does not collide with an opposite-net vertical met4
    # column already drawn for the sibling pin.  The two TX power columns
    # must be kept apart on met4 or they short VPWR<->VGND.  Scan candidate
    # x across the pin, pick the first whose vertical met4 run is free of the
    # OTHER net's met4 shapes.
    set onet [expr {$netname eq "VPWR" ? "VGND" : "VPWR"}]
    set on [$block findNet $onet]
    set px [expr {int(([$bb xMin]+[$bb xMax])/2)}]
    set m5 [_nearest_m5 $net $px $py]
    if { $m5 eq "" } { puts "pdn_patch: no met5 strap for $inst/$pinname"; return }
    lassign $m5 mx0 mx1 my0 my1
    set mcy [expr {int(($my0+$my1)/2.0)}]
    # pin layer
    set player ""
    foreach mp [[$it getMTerm] getMPins] {
        foreach g [$mp getGeometry] { set player [[$g getTechLayer] getName] }
    }
    # Column half-width.  Keep it narrow so the vertical run can sit in the
    # gap between adjacent opposite-net met4 straps (which are only ~1.7 um
    # apart) without touching them.  A 0.5 um-wide column still lands a via.
    set halfw 500
    # candidate x columns across the pin width, centre first then spread out
    set mid [expr {int(([$bb xMin]+[$bb xMax])/2)}]
    set cands [list $mid]
    for {set dx 2000} {$dx <= [expr {[$bb xMax]-[$bb xMin]}]} {incr dx 2000} {
        lappend cands [expr {$mid + $dx}]
        lappend cands [expr {$mid - $dx}]
    }
    set chosenx ""
    foreach cx $cands {
        # does a met4 column at cx (y=py..mcy) overlap any onet met4 shape?
        set hit 0
        set yl [expr {min($py,$mcy)-$halfw}]; set yh [expr {max($py,$mcy)+$halfw}]
        foreach os [_straps $on met4] {
            lassign $os ox0 ox1 oy0 oy1
            if {[expr {$cx-$halfw}]<$ox1 && [expr {$cx+$halfw}]>$ox0 &&
                $yl<$oy1 && $yh>$oy0} { set hit 1; break }
        }
        if { !$hit } {
            set chosenx $cx
            break
        }
    }
    if { $chosenx eq "" } { set chosenx $mid }
    set px $chosenx
    # 1. metal on the pin's own layer so the stack lands on the pin shape.
    # The met1 pin is a tiny (~175x195 DBU) stub -- too small for a via plus
    # its enclosure.  Draw a generous met1 pad centred on the pin (overlapping
    # it) plus same-size met2/met3/met4 pads, then the via stack through them.
    # A pad overlapping the pin on the same layer is the connect.
    if { $player eq "met1" } {
        set pad 500
        odb::dbSBox_create $sw [_layer met1] \
            [expr {int($px-$pad)}] [expr {int($py-$pad)}] \
            [expr {int($px+$pad)}] [expr {int($py+$pad)}] STRIPE
        # keep the tiny pin shape too so the net definitely owns it
        odb::dbSBox_create $sw [_layer met1] \
            [$bb xMin] [$bb yMin] [$bb xMax] [$bb yMax] STRIPE
        odb::dbSBox_create $sw [_layer met2] \
            [expr {int($px-$pad)}] [expr {int($py-$pad)}] \
            [expr {int($px+$pad)}] [expr {int($py+$pad)}] STRIPE
        odb::dbSBox_create $sw [_layer met3] \
            [expr {int($px-$pad)}] [expr {int($py-$pad)}] \
            [expr {int($px+$pad)}] [expr {int($py+$pad)}] STRIPE
        foreach {a b} {met1 met2 met2 met3 met3 met4} {
            set v [_via_between $a $b]
            if { $v ne "" } { odb::dbSBox_create $sw $v $px $py STRIPE }
        }
    }
    # 2. vertical met4 column from pin-y to the met5 strap centre-y
    set y_lo [expr {int(min($py,$mcy)-$halfw)}]
    set y_hi [expr {int(max($py,$mcy)+$halfw)}]
    odb::dbSBox_create $sw [_layer met4] \
        [expr {int($px-$halfw)}] $y_lo [expr {int($px+$halfw)}] $y_hi STRIPE
    # 3. via met4->met5 at the column top, landing on the met5 strap
    set v45 [_via_between met4 met5]
    if { $v45 ne "" } { odb::dbSBox_create $sw $v45 $px $mcy STRIPE }
    puts "pdn_patch: bound $inst/$pinname ($player @$px,$py) -> met5 strap y=$my0..$my1"
}

foreach inst {u_tx_driver u_rx_frontend} {
    _bind_pin $inst VDD VPWR
    _bind_pin $inst VSS VGND
}
'''

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", default=str(ROOT / "out/ci-soc"))
    ap.add_argument("--out", default=str(ROOT / "build/analog_pnr"))
    ap.add_argument("--pdk-root", default=None)
    ap.add_argument("--soft-stdcell", action="store_true",
                    help="use the stdcell soft macros; omit hard-macro PDN")
    args = ap.parse_args()
    pkg = Path(args.package).resolve()
    out = Path(args.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    rtl = pkg / "rtl"
    fl = rtl / "filelist_sv2v.f"
    if not fl.is_file():
        fl = rtl / "filelist.f"
    files = [str(rtl / line.strip()) for line in fl.read_text().splitlines()
             if line.strip() and not line.strip().startswith("#")]
    files += [str(ROOT / "analog/rtl/serdes_analog_link.sv")]
    if not args.soft_stdcell:
        for m in ("tx_inv_chain_1_3_9", "rx_res_fb_pmos"):
            files.append(str(ROOT / "analog/macros" / m / "macro.v"))
    manifest = json.loads((pkg / "manifest.json").read_text()) if (pkg / "manifest.json").is_file() else {}
    params = manifest.get("params", {})
    spec_path = ROOT / "spec/soc_sky130.yaml"
    spec = {}
    if spec_path.is_file():
        import yaml as _yaml
        spec = _yaml.safe_load(spec_path.read_text()) or {}
    line_rate = float(spec.get("link", {}).get("line_rate_mbps", 25) or 25)
    user_mhz = float(spec.get("interface", {}).get("user_clock_mhz", 50) or 50)
    ui_ns = 1000.0 / line_rate
    user_ns = 1000.0 / user_mhz
    sdc = out / "serdes_analog_link.sdc"
    sdc.write_text("""# Mixed-signal top; generated by scripts/gen_analog_pnr_config.py
set PCLK_PERIOD_NS __USER_NS__
set TX_PERIOD_NS __TX_NS__
create_clock -name pclk -period $PCLK_PERIOD_NS [get_ports pclk]
create_clock -name s_axis_aclk -period $PCLK_PERIOD_NS [get_ports s_axis_aclk]
create_clock -name m_axis_aclk -period $PCLK_PERIOD_NS [get_ports m_axis_aclk]
create_clock -name tx_clk -period $TX_PERIOD_NS [get_ports tx_clk]
# rx_clk is a forwarded, top-level source-synchronous clock.  It is a real
# input port, so it must be a primary clock; a generated clock sourced from
# tx_clk would require an internal tx_clk -> rx_clk path that does not exist.
create_clock -name rx_clk -period $TX_PERIOD_NS [get_ports rx_clk]
set_clock_groups -asynchronous -group [get_clocks pclk] -group [get_clocks s_axis_aclk] -group [get_clocks m_axis_aclk]
set_clock_uncertainty -setup 0.050 [get_clocks {pclk s_axis_aclk m_axis_aclk tx_clk rx_clk}]
set_clock_uncertainty -hold 0.020 [get_clocks {pclk s_axis_aclk m_axis_aclk tx_clk rx_clk}]
set_input_delay -clock s_axis_aclk 0.1 [get_ports {s_axis_tvalid s_axis_tdata* s_axis_tlast}]
set_output_delay -clock s_axis_aclk 0.2 [get_ports s_axis_tready]
set_output_delay -clock m_axis_aclk 0.2 [get_ports {m_axis_tvalid m_axis_tdata* m_axis_tlast}]
set_input_delay -clock m_axis_aclk 0.1 [get_ports m_axis_tready]
set_input_delay -clock pclk 0.1 [get_ports {paddr* psel penable pwrite pwdata*}]
set_output_delay -clock pclk 0.2 [get_ports {pready prdata* pslverr csr_regs*}]
set_input_delay -clock rx_clk 0.1 [get_ports rx_pad]
set_output_delay -clock tx_clk 0.2 [get_ports tx_pad]
# Explicit port transition/load so max-slew/max-cap checks do not inherit
# tool defaults at the package boundary.
set_input_transition 0.10 [all_inputs]
set_load 0.05 [all_outputs]
# Enforce the package-level electrical limits on every net, including
# non-timing control fans such as mux selects.
set_max_fanout 4 [current_design]
set_max_transition 0.90 [current_design]
set_max_capacitance 0.22 [current_design]
set_false_path -from [get_ports {presetn s_axis_aresetn m_axis_aresetn tx_rst_n rx_rst_n}]
set_false_path -to [get_ports rx_clk_out]
""".replace("__USER_NS__", f"{user_ns:.6f}").replace("__TX_NS__", f"{ui_ns:.6f}"))
    # Place each macro so a top-level PDN strap lands directly over its power
    # pin -- the patch only has to drop a vertical via stack, not route a
    # stub that would cross the neighbouring opposite-net strap.  The sky130
    # met4 straps are vertical at x = 16.32 + n*153.6 (n=0,1,2 -> ~21.6,
    # ~175.4, ~329.0 um); the n=1 VPWR strap at x=174.64..176.24 has a y-gap
    # 46.48..78.64 (a macro keepout) so we place both pins inside its solid
    # spans.  Pin offsets in the LEF (macro-local): tx VDD/VSS met4 rails sit
    # at the macro's right edge ~+10 um past origin; rx VDD/VSS met1 stubs are
    # ~+5.5 um.  Place origins so the pin centre lands at x~175.4 (strap
    # centre) on the same net.
    macros = {}
    for name, inst, loc in (("tx_inv_chain_1_3_9", "u_tx_driver", (165.0, 80.0)),
                            ("rx_res_fb_pmos", "u_rx_frontend", (169.0, 300.0))):
        base = ROOT / "analog/macros" / name
        macros[name] = {
            "gds": [str(base / "layout.gds")],
            "lef": [str(base / "abstract.lef")],
            "nl": [str(base / "macro.v")],
            "lib": {"*": [str(base / "macro.lib")]},
            "instances": {inst: {"location": list(loc), "orientation": "N"}},
        }
    # Custom PDN cfg for the hard-macro link: the stock sky130 pdn_cfg.tcl
    # with its no-strap macro grid replaced by one that draws met4/met5 straps
    # over the macros and connects met4<->met5 and met1<->met4.  Written next
    # to the YAML; referenced only on the hard-macro path.
    stock_pdn = Path(
        "/opt/librelane/librelane/scripts/openroad/common/pdn_cfg.tcl")
    stock = stock_pdn.read_text()
    # Splice out the stock trailing `define_pdn_grid -macro` block (from its
    # last `define_pdn_grid` to EOF) and substitute the strap-bearing grid.
    marker = stock.rfind("define_pdn_grid")
    if marker < 0:
        raise SystemExit("stock pdn_cfg.tcl: no define_pdn_grid found")
    pdn_cfg = out / "pdn_cfg_hard_macros.tcl"
    pdn_cfg.write_text(stock[:marker] + _PDN_MACRO_GRID.lstrip("\n"))
    # Post-pdngen binding patch, sourced by the pdngen wrapper above.
    (out / "pdn_patch.tcl").write_text(_PDN_PATCH.lstrip("\n"))

    base_path = pkg / "pnr" / "librelane.yaml"
    config = {}
    if args.pdk_root:
        pdk_root = Path(args.pdk_root).resolve()
    else:
        import os
        env = os.environ.get("PDK_ROOT")
        if env:
            pdk_root = Path(env).resolve()
            if pdk_root.name == "sky130A":
                pdk_root = pdk_root.parent
        else:
            candidates = sorted(Path.home().glob(
                ".ciel/ciel/sky130/versions/*/sky130A/libs.tech/librelane"))
            if not candidates:
                raise SystemExit("set --pdk-root or PDK_ROOT (sky130 version root)")
            pdk_root = candidates[-1].parents[2]
    import yaml as _yaml
    if base_path.is_file():
        config.update(_yaml.safe_load(base_path.read_text()) or {})
        if "LIB" in config:
            libs = {
                corner: [str(p).replace("${PDK_LIB_ROOT}", str(pdk_root))
                         for p in paths]
                for corner, paths in config["LIB"].items()
            }
            default = str(config.get("DEFAULT_CORNER", ""))
            chosen = libs.get(default) or next(iter(libs.values()))
            config["LIB"] = {default or "default": chosen}
            config["SYNTH_CORNER"] = default or "default"
    if args.soft_stdcell:
        macros = {}
    config.update({
        "VERILOG_DEFINES": ([] if args.soft_stdcell
                            else ["SERDES_LINK_HARD_MACROS"]),
        "DESIGN_NAME": "serdes_analog_link",
        "VERILOG_FILES": files,
        "CLOCK_PORT": "pclk",
        "CLOCK_PERIOD": user_ns,
        "PDK": "sky130A",
        "STD_CELL_LIBRARY": "sky130_fd_sc_hd",
        "DEFAULT_CORNER": "nom_tt_025C_1V80",
        "PNR_SDC_FILE": str(sdc),
        "SIGNOFF_SDC_FILE": str(sdc),
        "DIE_AREA": [0, 0, 700, 700],
        "FP_CORE_UTIL": 30,
        "PL_TARGET_DENSITY": 0.4,
        "MACROS": macros,
        "VDD_NETS": ["VPWR"],
        "GND_NETS": ["VGND"],
        "PDN_MACRO_CONNECTIONS": ([] if args.soft_stdcell else [
            "u_tx_driver VPWR VGND VDD VSS",
            "u_rx_frontend VPWR VGND VDD VSS",
        ]),
        # Hard-macro link only: custom pdn cfg adds the met1<->met4 connect so
        # rx_res_fb_pmos (met1 power stubs) is reachable.  None keeps the stock
        # cfg on the soft-stdcell path.
        "PDN_CFG": (None if args.soft_stdcell else str(pdn_cfg)),
        # Zero macro PDN halo: the 10 um placement halo is only for floorplan
        # keep-out.  A nonzero PDN halo carves a keep-out around each tiny
        # (10-20 um) macro that swallows it whole, so the macro grid can never
        # place a shape -> PDN-0233.  Zero lets straps/vias reach the pins.
        **({} if args.soft_stdcell else {
            "PDN_HORIZONTAL_HALO": 0.0,
            "PDN_VERTICAL_HALO": 0.0,
        }),
        "RUN_CTS": True,
        "KLAYOUT_XOR_IGNORE_LAYERS": ["235/4"],
        "PL_RESIZER_SETUP_SLACK_MARGIN": 0.0,
        "PL_RESIZER_HOLD_SLACK_MARGIN": 0.10,
        "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.0,
        "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.10,
    })
    # OpenROAD treats separate LEF files as one 'abstract' library; use the
    # combined file to avoid dropping the second hard macro.
    combined = str(ROOT / "analog/macros/sky130_analog_macros.lef")
    for macro in config["MACROS"].values():
        macro["lef"] = [combined]
    # The base config's LIB key may use a lowercase corner spelling; make it
    # exactly match DEFAULT_CORNER for filter_views.
    if config.get("LIB"):
        corner_key = next(iter(config["LIB"]))
        config["LIB"] = {corner_key: next(iter(config["LIB"].values()))}
        config["DEFAULT_CORNER"] = corner_key
        config["SYNTH_CORNER"] = corner_key
        config["STA_CORNERS"] = [corner_key]
    path = out / "librelane.yaml"
    import yaml
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    print(f"wrote {path} and {sdc}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

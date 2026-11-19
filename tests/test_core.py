"""Run: python tests/test_core.py   (or: python -m pytest tests)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
from PIL import Image, ImageDraw, ImageFont
import core

SIGS = [
    {"rock": "Laranite (Raw)", "method": "ship", "signature": 3825},
    {"rock": "Tungsten (Ore)", "method": "ship", "signature": 3870},
    {"rock": "Copper (Ore)", "method": "ship", "signature": 4700},
    {"rock": "Corundum (Raw)", "method": "ship", "signature": 4700},
]

def test_matches_like_asop_scanner():  # same cases as packages/shared/test/mining.test.mjs
    assert [(h["count"], h["rock"]) for h in core.match_signature(7650, SIGS)] == [(2, "Laranite (Raw)")]
    assert [(h["count"], h["rock"]) for h in core.match_signature(3870, SIGS)] == [(1, "Tungsten (Ore)")]
    assert core.match_signature(1234, SIGS) == []
    assert core.match_signature(3825 * 9, SIGS) == []  # above max cluster

def test_format():
    assert core.format_hits(core.match_signature(7650, SIGS)) == "2× Laranite"
    assert core.format_hits(core.match_signature(9400, SIGS)) == "2× Copper / Corundum"
    assert core.format_hits(core.match_signature(3870, SIGS), show_single_count=False) == "Tungsten"
    assert core.format_hits(core.match_signature(7650, SIGS), short_names=False) == "2× Laranite (Raw)"

def test_parse():
    P = core.parse_number
    assert P("7650") == 7650
    assert P("7,650") == 7650
    assert P("RS 7 650") == 7650
    assert P("765O") == 7650           # O misread for 0
    assert P("RS 3825") == 3825         # 'RS' label untouched
    assert P("12.300") == 12300
    assert P("") is None and P("abc") is None
    assert P("42", min_value=100) is None

def test_pick_value():
    sigs = SIGS + [{"rock": "Raw Ice", "method": "ship", "signature": 4300}]
    known = lambda v: bool(core.match_signature(v, sigs))
    # pin icon read as a leading digit / a fringe misread as a trailing one
    assert core.pick_value(["917200", ".17200"], known) == 17200
    assert core.pick_value(["217200", "17260"], known) == 17200
    assert core.pick_value(["17200", "17206"], known) == 17200
    assert core.pick_value(["12345"], known) is None          # nothing adds up: show nothing
    assert core.pick_value(["12345"], None) == 12345           # no table yet: plain reading

def test_stabilizer():
    st = core.Stabilizer(confirm=2, switch=3, hold=4.0, empty_hold=0.8)
    assert st.update(17200, False, 0.0) is None            # needs 2 in a row
    assert st.update(17200, False, 0.25) == 17200
    assert st.update(None, False, 0.5) == 17200             # unreadable frame: lingers
    assert st.update(17206, False, 0.75) == 17200           # one-off misread: ignored
    assert st.update(17200, False, 1.0) == 17200
    assert st.update(None, False, 4.9) == 17200             # still within hold of t=1.0
    assert st.update(None, False, 5.1) is None              # hold ran out
    st.update(16960, False, 6.0); st.update(16960, False, 6.25)
    assert st.update(4300, False, 6.5) == 16960             # new rock needs 3 in a row
    st.update(4300, False, 6.75)
    assert st.update(4300, False, 7.0) == 4300
    assert st.update(None, True, 7.25) == 4300              # box went empty…
    assert st.update(None, True, 8.1) is None               # …cleared after empty_hold

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from asop_4_10_3 import SIGS as LIVE  # the real list from ASOP, build 4.10.3


def test_new_rules_4_10_3():
    F = lambda v: core.format_hits(core.match_signature(v, LIVE))
    assert F(7650) == "2× Laranite"
    assert F(16960) == "4× Copper"
    assert F(17200) == "4× Raw Ice"
    assert F(19200) == "5× Aslarite"            # not "6× Savrilium": it clusters at most 2
    assert F(6340) == "2× Quantainium"
    assert F(9510) == ""                        # 3× Quantainium can't happen (max 2)
    assert F(3000) == "Hand · 1× Wreckage"      # a hand gem, or the Origin 890 Jump hull
    assert F(6000) == "Hand · 3× Wreckage"
    assert F(4000) == "ROC · 2× Wreckage"
    assert F(12000) == "Hand · ROC · 6× Wreckage"
    assert F(36000) == "Hand · ROC"
    assert F(2000) == "1× Wreckage"             # a panel or the Aegis Redeemer hull: both wreckage
    assert F(75000) == "Hand Minable"           # 25 gems (Hadanite, Janalite cluster that big)
    assert F(32000) == "ROC Minable"            # 8 gems: 16 panels is past Wreckage's 15
    assert F(2400) == "1× Wreckage"             # C2 hull


def test_place_filter():
    sigs = [dict(s) for s in LIVE]
    for s in sigs:
        s["places"] = {"Aslarite (Raw)": ["Ita"], "Raw Ice": ["Ita", "Hurston"], "Aphorite": ["Daymar"],
                       "Dolivine": ["Daymar"], "Hadanite": ["Daymar"], "Janalite": ["Daymar"]}.get(s["rock"], ["Elsewhere"])
    F = lambda v, place: core.format_hits(core.match_signature(v, sigs, place=place))
    assert F(19200, "Ita") == "5× Aslarite"
    assert F(19200, "Hurston") == ""                       # no Aslarite on Hurston
    assert F(6000, "Daymar") == "Hand · 3× Wreckage"
    assert F(6000, "Ita") == "3× Wreckage"                 # no hand gems at Ita; wreckage is anywhere
    # ranking: rocks sharing a signature, most likely first
    two = [{"rock": "B rock", "method": "ship", "signature": 5000, "maxCluster": 2, "places": ["X"]},
           {"rock": "A rock", "method": "ship", "signature": 5000, "maxCluster": 2, "places": ["X"]}]
    ch = {("X", "B rock", "ship"): 0.7, ("X", "A rock", "ship"): 0.2}
    assert core.format_hits(core.match_signature(5000, two, place="X", chances=ch)) == "1× B rock / A rock"
    assert core.format_hits(core.match_signature(5000, two)) == "1× A rock / B rock"
    table = {"spots": [{"place": "Ita", "system": "Stanton", "rock": "Raw Ice", "method": "ship", "chance": 0.3}],
             "signatures": [{"places": ["Daymar", "Ita"]}]}
    assert core.places_of(table) == [("Ita", "Stanton"), ("Daymar", None)]


def test_unknown_number():
    known = lambda v: bool(core.match_signature(v, LIVE))
    assert core.pick_value(["5555", "5555", ""], known) == 5555   # clear, agreed, not a rock: "???"
    assert core.pick_value(["5555", "5585", "x"], known) is None  # passes disagree: unreadable
    assert core.pick_value(["9 17200", "17200"], known) == 17200   # known still wins


def test_corrections():
    sigs = [dict(s) for s in LIVE]
    n = core.apply_corrections(sigs, [
        {"kind": "rock-signature", "recordKey": "Savrilium (Ore)|ship", "field": "maxCluster", "value": 6},
        {"kind": "mining-laser", "recordKey": "x", "field": "power", "value": 1},
    ])
    assert n == 1
    assert core.format_hits(core.match_signature(19200, sigs)) == "5× Aslarite · 6× Savrilium"


def test_hotkey():
    assert core.parse_hotkey("ctrl+shift+f8") == (0x6, 0x77)
    assert core.parse_hotkey("alt+k") == (0x1, ord("K"))

def test_color_and_preprocess():
    img = Image.new("RGB", (160, 40), (10, 14, 20))
    d = ImageDraw.Draw(img)
    d.text((8, 6), "7,650", fill=(80, 200, 255), font=ImageFont.load_default(size=26))
    c = core.hud_color(img)
    assert c[2] > c[0] and c[2] > 150, c        # picks up the cyan text, not the dark bg
    bw = core.preprocess(img)
    assert bw.mode == "L" and bw.width > 160 * 2
    assert core.preprocess(Image.new("RGB", (160, 40), (30, 30, 30))) is None
    assert core.apply_brightness((100, 100, 100), 2.0) == (255, 255, 255)
    assert core.apply_brightness((100, 200, 50), 0.5) == (50, 100, 25)

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)

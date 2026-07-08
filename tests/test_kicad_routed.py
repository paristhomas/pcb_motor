"""Regression: pcb_motor.kicad.routed must reproduce the SHIPPED gimbal90 artwork.

The module is a port of the studio scripts ``build_routed_stator.py`` /
``build_routed_project.py``; these tests rebuild both footprint variants from
gimbal90's ``motor.json`` and compare them against the reference deliverables
the user's scripts produced (geometry, not bytes -- header/metadata may
legitimately differ):

- identical thru-hole pad sets (name, position, size, drill, layers, 1e-6 mm);
- identical custom-pad sets (name, layers, anchor) and per-layer copper
  (shapely union symmetric-difference area < 1e-6 of the union area);
- the port's own re-parse verification must end in RESULT: PASS.

If geometry differs, FIX THE PORT -- do not loosen these tolerances.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pcb_motor.design import MotorDesign
from pcb_motor.kicad.routed import build_routed_project, build_routed_stator

REF_DIR = Path("/home/paris/random_projects/pcb_motor_studio/design_sessions/gimbal90_odrive")
REF = {False: REF_DIR / "stator_routed_2side.kicad_mod",
       True: REF_DIR / "stator_routed_2side_tabs.kicad_mod"}

pytestmark = pytest.mark.skipif(
    not REF_DIR.is_dir(), reason="gimbal90 reference deliverables not present"
)


def _load_design() -> MotorDesign:
    data = json.loads((REF_DIR / "motor.json").read_text(encoding="utf-8"))
    valid = {f.name for f in dataclasses.fields(MotorDesign)}
    return MotorDesign(**{k: v for k, v in data.items() if k in valid})


# --------------------------------------------------------------------------- #
# Independent parser (does not share code with the writer under test)
# --------------------------------------------------------------------------- #
def _parse(path: Path):
    txt = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    smd = []   # (name, layers tuple, anchor (x,y) mm, absolute Polygon mm)
    for m in re.finditer(
        r'\(pad "(\w+)" smd custom \(at ([-\d.]+) ([-\d.]+)\) '
        r"\(size ([\d.]+) ([\d.]+)\) \(layers ([^)]*)\).*?"
        r"\(gr_poly \(pts (.*?)\) \(width",
        txt,
        re.S,
    ):
        nm, cx, cy = m.group(1), float(m.group(2)), float(m.group(3))
        layers = tuple(re.findall(r'"([^"]+)"', m.group(6)))
        pts = [(float(a) + cx, float(b) + cy)
               for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", m.group(7))]
        smd.append((nm, layers, (cx, cy), Polygon(pts).buffer(0)))
    th = []    # (name, x, y, od, drill, layers tuple)
    for m in re.finditer(
        r'\(pad "(\w+)" thru_hole circle \(at ([-\d.]+) ([-\d.]+)\) '
        r"\(size ([\d.]+) ([\d.]+)\) \(drill ([\d.]+)\) \(layers ([^)]*)\)\)",
        txt,
    ):
        th.append((m.group(1), float(m.group(2)), float(m.group(3)),
                   float(m.group(4)), float(m.group(6)),
                   tuple(re.findall(r'"([^"]+)"', m.group(7)))))
    return smd, th


def _layer_union(smd, layer):
    return unary_union([p for nm, lays, _a, p in smd if lays[0] == layer])


# --------------------------------------------------------------------------- #
# Build both variants ONCE (the coil cache is shared between them)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    design = _load_design()
    root = tmp_path_factory.mktemp("routed")
    out = {}
    for tabs in (False, True):
        path = root / REF[tabs].name
        report = build_routed_stator(design, str(path), tabs=tabs)
        out[tabs] = (path, report)
    return design, root, out


@pytest.mark.parametrize("tabs", [False, True], ids=["circular", "tabs"])
def test_routed_stator_matches_reference(built, tabs):
    _design, _root, out = built
    path, report = out[tabs]
    assert report.passed, report.lines
    assert any(l.startswith("RESULT: PASS") for l in report.lines)

    got_smd, got_th = _parse(path)
    ref_smd, ref_th = _parse(REF[tabs])

    # ---- thru-hole pad set: name/position/size/drill/layers within 1e-6 mm ----
    key = lambda t: (t[0], t[5], round(t[3], 6), round(t[4], 6),
                     round(t[1], 6), round(t[2], 6))
    assert sorted(map(key, got_th)) == sorted(map(key, ref_th))
    # max positional delta between matched holes (exact-match sets -> 0)
    gs, rs = sorted(got_th), sorted(ref_th)
    max_th_delta = max(
        max(abs(g[1] - r[1]), abs(g[2] - r[2])) for g, r in zip(gs, rs)
    )
    assert max_th_delta < 1e-6

    # ---- custom pad set: names, layers, anchors within 1e-6 mm ----
    assert sorted((nm, lays) for nm, lays, _a, _p in got_smd) == \
           sorted((nm, lays) for nm, lays, _a, _p in ref_smd)
    akey = lambda t: (t[0], t[1], round(t[2][0], 6), round(t[2][1], 6))
    assert sorted(akey(t) for t in got_smd) == sorted(akey(t) for t in ref_smd)
    ga = sorted((akey(t) for t in got_smd))
    ra = sorted((akey(t) for t in ref_smd))
    max_anchor_delta = max(
        max(abs(g[2] - r[2]), abs(g[3] - r[3])) for g, r in zip(ga, ra)
    )

    # ---- per-layer copper geometric equality ----
    sym_areas = {}
    for layer in ("F.Cu", "B.Cu"):
        gu = _layer_union(got_smd, layer)
        ru = _layer_union(ref_smd, layer)
        union_area = unary_union([gu, ru]).area
        sym = gu.symmetric_difference(ru).area
        sym_areas[layer] = (sym, union_area)
        assert sym < 1e-6 * union_area, (
            f"{'tabs' if tabs else 'circular'} {layer}: symmetric difference "
            f"{sym*1e6:.6f} mm^2 vs union {union_area*1e6:.3f} mm^2"
        )

    print(f"\n[{'tabs' if tabs else 'circular'}] th={len(got_th)} smd={len(got_smd)} "
          f"max_th_delta={max_th_delta:.3e} mm max_anchor_delta={max_anchor_delta:.3e} mm "
          + " ".join(f"{lay}: symdiff={s*1e6:.6f}/union={u*1e6:.1f} mm^2"
                     for lay, (s, u) in sym_areas.items()))


@pytest.mark.parametrize("tabs", [False, True], ids=["circular", "tabs"])
def test_routed_project_builds_and_passes(built, tabs):
    design, root, out = built
    mod_path, _ = out[tabs]
    out_dir = root / ("kicad_routed_tabs" if tabs else "kicad_routed")
    report = build_routed_project(design, str(out_dir), tabs=tabs,
                                  mod_path=str(mod_path))
    assert report.passed
    assert any(l == "SUMMARY: PASS" for l in report.lines)
    proj = report.project
    for fn in (f"{proj}.kicad_sym", f"{proj}.kicad_sch", f"{proj}.kicad_pcb",
               f"{proj}.kicad_pro", "sym-lib-table", "fp-lib-table",
               f"{proj}.pretty/{report.fp_name}.kicad_mod"):
        assert (out_dir / fn).is_file(), fn
    # CRLF everywhere
    raw = (out_dir / f"{proj}.kicad_pcb").read_bytes()
    assert b"\r\n" in raw and b"\r\r\n" not in raw
    # every net-bearing pad in the footprint got its net bound in the board
    assert all(v > 0 for v in report.net_pad_counts.values())


def test_rejects_non_12_slot():
    design = dataclasses.replace(_load_design(), n_slots=36, pole_pairs=21)
    with pytest.raises(NotImplementedError):
        build_routed_stator(design, "/tmp/never_written.kicad_mod")

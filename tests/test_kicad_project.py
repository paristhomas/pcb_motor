"""Smoke tests for pcb_motor.kicad.project -- the stator KiCad project.

The load-bearing contract: pin NUMBER on the stator symbol == pad NAME in the
full footprint, the WYE is pre-wired with the in-footprint bridges expressed
as stacked pins (no wires), the chain ends stay separate nets (no on-board
star), and every emitted file is CRLF.
"""

from __future__ import annotations

import json
import re

import pytest

from pcb_motor.design import MotorDesign
from pcb_motor.kicad.project import ProjectError, build_kicad_project
from pcb_motor.kicad.footprint import stator_plan


def _assert_crlf(raw: bytes):
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"").find(b"\n") == -1, "found a bare LF"


def _pins(sym_txt: str) -> dict[str, str]:
    """pin NUMBER -> connection point, from the symbol lib text."""
    return {
        num: at
        for at, num in re.findall(
            r'\(pin passive line \(at ([-\d.]+ [-\d.]+) 0\).*?\(number "([^"]+)"',
            sym_txt,
        )
    }


def test_project_12_slots(tmp_path):
    d = MotorDesign()
    rep = build_kicad_project(d, str(tmp_path))
    assert rep.passed
    assert all(rep.checks.values()), rep.checks

    # every emitted file is CRLF
    names = {p.rsplit("/", 1)[-1] for p in rep.files}
    assert names == {
        "pcb_motor_coil.kicad_sym",
        "pcb_motor_stator.kicad_sch",
        "sym-lib-table",
        "fp-lib-table",
        "pcb_motor_stator.kicad_pro",
    }
    for p in rep.files:
        _assert_crlf(open(p, "rb").read())

    sym = (tmp_path / "pcb_motor_coil.kicad_sym").read_text(encoding="utf-8")
    sch = (tmp_path / "pcb_motor_stator.kicad_sch").read_text(encoding="utf-8")

    # THE contract: one symbol pin per footprint pad, pin NUMBER == pad NAME
    pins = _pins(sym)
    pads = {f"{k}{l}" for k in range(d.n_slots) for l in "AB"}
    assert pads <= set(pins), sorted(pads - set(pins))

    # bridged pairs stack (identical connection point = one node, the copper
    # join lives inside the footprint); everything else has its own slot
    _, _, _, bridges = stator_plan(d.n_slots)
    pairs = [(f"{ka}{la}", f"{kb}{lb}") for (ka, la), (kb, lb) in bridges]
    for a, b in pairs:
        assert pins[a] == pins[b], (a, b)
    stacked = {p for pr in pairs for p in pr}
    solo_pts = [pins[p] for p in sorted(pads - stacked)]
    assert len(solo_pts) == len(set(solo_pts)), "un-bridged pins must not stack"

    # schematic: 1 stator, 6 solder-wire pads, all pins instantiated, and the
    # expected wire count (cross-ring joins only; bridges draw no wires)
    assert sch.count('(lib_id "pcb_motor_coil:stator")') == 1
    assert sch.count('(lib_id "Connector:Conn_01x01_Pin")') == 6
    assert all(f'(pin "{p}" (uuid' in sch for p in pads)
    assert sch.count("(wire ") == 21   # 3 joins x3 + 3 leads + 3 ends + 6 J stubs

    # no on-board star: three separate end nets, no NEUTRAL anywhere
    for net in ("A_END", "B_END", "C_END"):
        assert f'(label "{net}"' in sch
    assert "NEUTRAL" not in sch

    # the project file is valid JSON and only seeded when missing
    pro = tmp_path / "pcb_motor_stator.kicad_pro"
    json.loads(pro.read_text(encoding="utf-8"))
    marker = '{"user": "edited"}'
    pro.write_text(marker, encoding="utf-8")
    build_kicad_project(d, str(tmp_path))
    assert pro.read_text(encoding="utf-8") == marker, ".kicad_pro was clobbered"


def test_project_36_slots(tmp_path):
    d = MotorDesign(n_slots=36, pole_pairs=21, r_inner_m=16e-3,
                    r_outer_m=39.5e-3, tapered_traces=True)
    rep = build_kicad_project(d, str(tmp_path))
    assert rep.passed

    sym = (tmp_path / "pcb_motor_coil.kicad_sym").read_text(encoding="utf-8")
    pins = _pins(sym)
    pads = {f"{k}{l}" for k in range(36) for l in "AB"}
    assert pads <= set(pins)

    sch = (tmp_path / "pcb_motor_stator.kicad_sch").read_text(encoding="utf-8")
    # 12 coils/phase: 11 joins - 6 bridged = 5 drawn joins x 3 wires x 3 phases
    assert sch.count("(wire ") == 3 * 15 + 3 + 3 + 6


def test_project_vendors_and_cross_checks_footprint(tmp_path):
    """When given the built footprint, the project must verify pad names and
    that net_tie_pad_groups equals the symbol's stacked pairs, then vendor it."""
    from pcb_motor.kicad.footprint import build_footprint

    d = MotorDesign()
    fp = tmp_path / "coil_full_2side.kicad_mod"
    build_footprint(d, str(fp), resolution_m=5e-4)

    out = tmp_path / "kicad"
    rep = build_kicad_project(d, str(out), footprint_full=str(fp))
    assert rep.passed
    assert rep.checks["footprint_pads_complete"]
    assert rep.checks["net_tie_groups_match"]
    vendored = out / "pcb_motor.pretty" / "coil_full_2side.kicad_mod"
    assert vendored.exists()
    _assert_crlf(vendored.read_bytes())

    # a WRONG footprint (different slot count) must abort before writing
    with pytest.raises(ProjectError):
        build_kicad_project(
            MotorDesign(n_slots=36, pole_pairs=21, r_inner_m=16e-3,
                        r_outer_m=39.5e-3, tapered_traces=True),
            str(tmp_path / "kicad_bad"),
            footprint_full=str(fp),
        )
    assert not (tmp_path / "kicad_bad").exists()

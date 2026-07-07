"""Tests for pcb_motor.kicad.footprint -- the production two-sided footprint.

The source-of-truth safety property is the builder's own refusal to emit on a
failed clearance/stitch/bridge check; these tests exercise a real build (at a
coarsened artwork resolution for speed) and re-assert the externally visible
contract: PASS with worst clearance >= trace_space, parseable s-expression,
pad names derived from n_slots, CRLF output.
"""

from __future__ import annotations

import re

import pytest

from pcb_motor.design import MotorDesign
from pcb_motor.kicad.footprint import (
    FootprintError,
    build_footprint,
    stator_plan,
)

# Coarser artwork resolution than the 0.2 mm production default: ~4x fewer
# buffered segments, same shapes to within fab tolerance -- fast tests.
RES = 5e-4


def _read(path):
    raw = path.read_bytes()
    return raw, raw.replace(b"\r\n", b"\n").decode("utf-8")


def _assert_crlf(raw: bytes):
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"").find(b"\n") == -1, "found a bare LF"


def test_full_stator_12_slots(tmp_path):
    d = MotorDesign()   # 12N14P default; coerced to the tapered-wedge artwork
    out = tmp_path / "coil_full_2side.kicad_mod"
    rep = build_footprint(d, str(out), resolution_m=RES)

    # RESULT PASS and every gated clearance at/above trace_space (2 um emit
    # rounding tolerance, same as the original generator used).
    assert rep.passed
    assert all(rep.checks.values()), rep.checks
    ts_mm = d.trace_space_m * 1e3
    assert rep.worst_clearance_mm >= ts_mm - 2e-3

    raw, text = _read(out)
    _assert_crlf(raw)
    assert text.count("(") == text.count(")")
    assert text.startswith('(footprint "')

    # pad names derive from n_slots: 0A..11B custom pads + one stitch-via pad
    # name per coil, and nothing beyond n_slots-1
    for k in range(d.n_slots):
        assert f'(pad "{k}A" smd custom' in text
        assert f'(pad "{k}B" smd custom' in text
        assert f'(pad "{k}S" thru_hole circle' in text
    assert f'(pad "{d.n_slots}A"' not in text
    assert rep.pad_names == sorted(f"{k}{l}" for k in range(12) for l in "AB")

    # two-sided: filled copper on BOTH layers, netless graphic by design
    assert '(layer "F.Cu"))' in text and '(layer "B.Cu"))' in text
    assert text.count("(fp_poly ") >= 2 * d.n_slots

    # the in-footprint series bridges are declared intentional
    m = re.search(r"\(net_tie_pad_groups ([^)]*)\)", text)
    assert m is not None
    groups = {frozenset(g.split(",")) for g in re.findall(r'"([^"]+)"', m.group(1))}
    _, _, _, bridges = stator_plan(d.n_slots)
    assert groups == {frozenset((f"{ka}{la}", f"{kb}{lb}")) for (ka, la), (kb, lb) in bridges}
    assert rep.n_bridges == 6                     # 2 adjacent joins per phase


def test_single_tooth(tmp_path):
    d = MotorDesign()
    out = tmp_path / "coil_single_2side.kicad_mod"
    rep = build_footprint(d, str(out), single_tooth=True, resolution_m=RES)

    assert rep.passed and rep.n_coils == 1
    assert rep.checks["holes_noncopper"]
    assert rep.checks["stitch_on_both_layers"]

    raw, text = _read(out)
    _assert_crlf(raw)
    assert text.count("(") == text.count(")")
    assert '(pad "0A" smd custom' in text and '(pad "0B" smd custom' in text
    # every stitch via is a thru-hole pad on *.Cu
    assert text.count('(pad "0S" thru_hole') == rep.n_vias_per_coil
    assert "(net_tie_pad_groups" not in text     # bridges are full-stator only


def test_full_stator_36_slots(tmp_path):
    """The 36N42P demo-motor slot count: pads 0A..35B, 18 bridges (6 per
    phase), flips from the tiled star-of-slots layout."""
    d = MotorDesign(
        n_slots=36, pole_pairs=21,
        r_inner_m=16e-3, r_outer_m=39.5e-3,   # ~80 mm OD board
        tapered_traces=True,
    )
    out = tmp_path / "coil_full_36.kicad_mod"
    rep = build_footprint(d, str(out), resolution_m=RES)

    assert rep.passed
    assert rep.n_coils == 36
    assert rep.worst_clearance_mm >= d.trace_space_m * 1e3 - 2e-3

    raw, text = _read(out)
    _assert_crlf(raw)
    assert text.count("(") == text.count(")")
    for k in (0, 17, 35):
        assert f'(pad "{k}A" smd custom' in text
        assert f'(pad "{k}B" smd custom' in text
    assert f'(pad "36A"' not in text
    assert rep.n_bridges == 18


def test_refuses_to_write_on_fail(tmp_path):
    """The clearance gate must refuse to emit: a huge width shrink floor
    violation is simulated by an absurd disk radius that decapitates the
    terminal pads' rim band; easier: demand an impossible stitch."""
    d = MotorDesign()
    out = tmp_path / "bad.kicad_mod"
    # trace pitch so coarse that no turn fits the sector at all
    bad = MotorDesign(trace_width_m=5e-3, trace_space_m=5e-3)
    with pytest.raises(FootprintError):
        build_footprint(bad, str(out), resolution_m=RES)
    assert not out.exists(), "FAIL must not leave a file behind"


def test_stator_plan_follows_layout_polarity():
    """Flips mirror exactly the reverse-wound coils, and every bridge joins a
    ring-adjacent same-layer pad pair."""
    from pcb_motor.coils import _coil_layout

    for n in (12, 36):
        layout = _coil_layout(n, 3)
        _, flips, clip, bridges = stator_plan(n)
        assert flips == [s < 0 for _, s in layout]
        for (ta, la), (tb, lb) in bridges:
            assert abs(ta - tb) == 1              # ring-adjacent teeth
            assert la == lb                       # same copper layer
            # end of coil a and start of coil b, per the winding direction
            sa = dict(enumerate(layout))[ta][1]
            assert la == ("B" if sa > 0 else "A")
        assert clip == {f"{k}{l}" for pr in bridges for k, l in pr}


def test_untapered_design_coercion_is_loud(tmp_path, capsys):
    """tapered_traces=false is coerced to the tapered artwork; the builder must
    say so in the report notes AND on stderr, telling the user to re-evaluate
    (the emitted copper differs from what was simulated otherwise)."""
    d = MotorDesign(tapered_traces=False)
    out = tmp_path / "tooth.kicad_mod"
    rep = build_footprint(d, str(out), single_tooth=True, resolution_m=RES)
    assert any(n.startswith("tapered_traces coerced to true") for n in rep.notes)
    assert any("re-evaluate with tapered_traces=true" in n for n in rep.notes)
    err = capsys.readouterr().err
    assert "WARNING" in err and "tapered_traces=false" in err


def test_tapered_design_has_no_coercion_note(tmp_path, capsys):
    d = MotorDesign(tapered_traces=True)
    rep = build_footprint(d, str(tmp_path / "t.kicad_mod"), single_tooth=True,
                          resolution_m=RES)
    assert not any("coerced" in n for n in rep.notes)
    assert "tapered_traces" not in capsys.readouterr().err

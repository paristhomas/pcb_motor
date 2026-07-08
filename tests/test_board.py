"""General board path (board.py): wrap a filled-copper footprint into a
complete, manufacturable .kicad_pcb with the WYE terminals net-bound and the
cross-ring interconnect left as ratsnest. Uses odrive80's committed 36-slot
footprint via footprint_path, so the test skips the slow footprint build."""

from pathlib import Path

import pytest

from pcb_motor import session as sessions
from pcb_motor.kicad import build_board
from pcb_motor.kicad.board import BoardError
from pcb_motor.kicad.project import _Plan

REPO = Path(__file__).resolve().parent.parent
EX = REPO / "examples" / "odrive80"
FP = EX / "stator_full_2side.kicad_mod"


@pytest.mark.skipif(not FP.exists(), reason="odrive80 committed footprint absent")
def test_general_board_36slot(tmp_path):
    design = sessions.Session("odrive80", root=str(REPO / "examples")).load_motor()
    rep = build_board(design, str(tmp_path), footprint_path=str(FP))

    assert rep.passed
    pcb = Path(rep.pcb_path)
    assert pcb.exists()
    txt = pcb.read_text(encoding="utf-8")

    # balanced, references the vendored footprint, has a net table
    assert txt.count("(") == txt.count(")")
    assert '(footprint "pcb_motor:coil_full_2side"' in txt
    assert '(net 0 "")' in txt

    # every declared net binds at least one pad on the board
    assert rep.net_pad_counts and all(v > 0 for v in rep.net_pad_counts.values())

    # board outline: outer edge + bore, both on Edge.Cuts
    assert txt.count('(layer "Edge.Cuts")') >= 2

    # cross-ring joins are left for a human (ratsnest), matching the plan
    plan = _Plan(int(design.n_slots), int(design.n_phases))
    assert rep.ratsnest_joins == len(plan.drawn_joins()) == 15

    # CRLF line endings like the other writers
    raw = pcb.read_bytes()
    assert b"\r\n" in raw and b"\r\r\n" not in raw


@pytest.mark.skipif(not FP.exists(), reason="odrive80 committed footprint absent")
def test_terminal_pads_carry_nets_stitch_vias_do_not(tmp_path):
    from pcb_motor.kicad.board import _pad_spans

    design = sessions.Session("odrive80", root=str(REPO / "examples")).load_motor()
    rep = build_board(design, str(tmp_path), footprint_path=str(FP))
    txt = Path(rep.pcb_path).read_text(encoding="utf-8")

    spans = {nm: txt[i:j] for i, j, nm in _pad_spans(txt)}
    # a terminal pad (…A/…B) is net-bound; a stitch via (…S) is not
    assert "(net " in spans["0A"] and "(net " in spans["0B"]
    if "0S" in spans:
        assert "(net " not in spans["0S"]

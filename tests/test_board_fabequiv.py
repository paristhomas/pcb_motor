"""gimbal90 fab-equivalence: `build_routed_project` from the committed routed
footprint must reproduce the board that was actually fabricated
(`examples/gimbal90/fabricated/`), coordinate-for-coordinate.

This is the deterministic half of the check (no KiCad needed). The Gerber half
-- that kicad-cli plots byte-identical layers from the two boards -- is verified
manually / in CI where kicad-cli exists; see the fabricated/README.md.
"""

import re
from pathlib import Path

import pytest

from pcb_motor import session as sessions
from pcb_motor.kicad import build_routed_project
from pcb_motor.kicad.routed import RoutedError

REPO = Path(__file__).resolve().parent.parent
EX = REPO / "examples" / "gimbal90"
GOLDEN = EX / "fabricated" / "gimbal90_routed_tabs.kicad_pcb"
MOD = EX / "stator_routed_2side_tabs.kicad_mod"


def _xy(text: str):
    return re.findall(r"\(xy (-?[0-9.]+) (-?[0-9.]+)\)", text)


@pytest.mark.skipif(not (GOLDEN.exists() and MOD.exists()),
                    reason="gimbal90 golden reference / routed footprint absent")
def test_regenerated_board_matches_fabricated(tmp_path):
    design = sessions.Session("gimbal90", root=str(REPO / "examples")).load_motor()
    try:
        rep = build_routed_project(design, str(tmp_path), tabs=True,
                                   mod_path=str(MOD),
                                   project="gimbal90_routed_tabs")
    except RoutedError as exc:                       # pragma: no cover
        pytest.fail(f"routed board build failed: {exc}")

    assert rep.passed
    # every declared net binds at least one pad (fully-wired board)
    assert rep.net_pad_counts and all(v > 0 for v in rep.net_pad_counts.values())

    built = (tmp_path / "gimbal90_routed_tabs.kicad_pcb").read_text(encoding="utf-8")
    golden = GOLDEN.read_text(encoding="utf-8")
    # the copper + edge geometry (what the Gerbers plot) must match exactly
    assert _xy(built) == _xy(golden)
    assert len(_xy(built)) > 10000            # sanity: it's the full routed board

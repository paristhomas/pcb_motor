# gimbal90 — the board that was fabricated

These are the **exact production files for the gimbal90 routed-tabs stator that
was physically manufactured** (the "main event"). They are checked in verbatim
as the golden reference:

- `gimbal90_routed_tabs.kicad_pcb` / `.kicad_pro` — the KiCad board sent to fab.
- `gimbal90_routed_TABS_gerbers.zip` — the Gerber set that was ordered.

**What this proves.** `pcb-motor board --session gimbal90 --gerbers` regenerates
this board from the committed routed footprint and exports its Gerbers. The
regenerated `.kicad_pcb` is **coordinate-for-coordinate identical** to this one
(same 17,665 copper/edge points, same bore, same placement), and Gerbers plotted
from it by the same `kicad-cli` are **byte-identical modulo timestamps** on every
copper / mask / silk / edge layer. See `tests/test_board_fabequiv.py`.

The ordered Gerber set omits the paste layers and the drill map (not needed for
this board); the tool's default set includes them, which is a harmless superset
— the copper, soldermask, silkscreen, edge cuts and drills are the same board.

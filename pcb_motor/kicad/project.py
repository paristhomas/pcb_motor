"""Generate a KiCad project (symbol lib + schematic + project file) for the
full PCB-motor stator.

Everything is derived from the SAME star-of-slots layout
(:func:`pcb_motor.coils._coil_layout` via
:func:`pcb_motor.kicad.footprint.stator_plan`) that the footprint generator
uses, so the schematic stays in sync with the board by construction.

The schematic uses ONE "stator" symbol for the whole motor -- ``2*n_slots``
pins (``0A``, ``0B`` ... named to match the FULL footprint's pad names) bound
to the single full-stator footprint. The 3-phase WYE is pre-connected: the
ADJACENT-coil series joins per phase are copper arcs INSIDE the footprint
(``net_tie_pad_groups``) and appear as STACKED pins (one slot, one combined
name, an arc glyph in the body) -- not wires; only the cross-ring joins are
real drawn wires to route. There is NO on-board neutral star: the three chain
ends come out as separate nets (``A_END``/``B_END``/``C_END``) to six
individual solder-wire pads (J1-J3 leads in, J4-J6 ends out), so two identical
stator boards can be SERIES-connected externally (top board's ends jumper to
the bottom board's leads; the star = bridge a,b,c on ONE board only). An
on-board star would make series wiring impossible.

Pin NUMBERS on the stator symbol == pad NAMES in the full footprint
(``0A`` .. ``{n_slots-1}B``); the ``S`` stitch vias are internal per coil and
are NOT schematic pins. A built-in self-check asserts every required pad-pair
shares a net and refuses to write on failure.

All files are written with CRLF line endings (KiCad saves CRLF).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field

from ..design import MotorDesign
from .footprint import stator_plan

PH = "ABC"
PITCH = 2.54                          # pin slot pitch [mm] (KiCad grid)
BODY_X0, BODY_X1 = 2.54, 58.78        # symbol rectangle x-span (pins at x=0)
GEN = '(generator "pcb_motor")'
VERSION_SYM = "(version 20231120)"    # KiCad 8 symbol-lib version
VERSION_SCH = "(version 20231120)"    # KiCad 8 schematic version
SOLDERWIRE_FP = "Connector_Wire:SolderWire-0.25sqmm_1x01_D0.65mm_OD2mm"


class ProjectError(RuntimeError):
    """A netlist / structural self-check failed; nothing was written."""


@dataclass
class ProjectReport:
    """What :func:`build_kicad_project` generated and verified."""

    out_dir: str
    passed: bool
    files: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)


def _uuid() -> str:
    return str(uuid.uuid4())


def _balanced(txt: str) -> bool:
    return txt.count("(") == txt.count(")")


# --------------------------------------------------------------------------- #
# Netlist plan: chains, nets, stacked pairs, pin slots -- all from the layout
# --------------------------------------------------------------------------- #
class _Plan:
    """WYE netlist + symbol pin layout for ``n_slots`` concentrated coils."""

    def __init__(self, n_slots: int, n_phases: int = 3):
        if n_phases != 3:
            raise ProjectError("the WYE project generator is 3-phase only")
        self.n_slots = int(n_slots)
        layout, flips, clip_pads, bridges = stator_plan(self.n_slots, n_phases)
        self.layout = layout
        # series chains per phase: (tooth, sign) in winding order
        self.chains = {
            p: [(k, s) for k, (ph, s) in enumerate(layout) if ph == p]
            for p in range(3)
        }
        self.cpp = len(self.chains[0])            # coils per phase
        if any(len(self.chains[p]) != self.cpp for p in range(3)):
            raise ProjectError("layout does not split evenly into 3 phases")
        self.all_pads = [f"{k}{t}" for k in range(self.n_slots) for t in "AB"]

        # pad -> net name. Each phase chain END is its OWN net (A_END ...),
        # NOT a shared neutral: the star is made off-board so two identical
        # boards can be series-connected.
        self.pad_net: dict[str, str] = {}
        for p in range(3):
            nodes = (
                [f"{PH[p]}_LEAD"]
                + [f"{PH[p]}_N{i}" for i in range(1, self.cpp)]
                + [f"{PH[p]}_END"]
            )
            for i, (t, s) in enumerate(self.chains[p]):
                start = f"{t}A" if s > 0 else f"{t}B"  # +: A(front,start)->B(back,end)
                end = f"{t}B" if s > 0 else f"{t}A"
                self.pad_net[start] = nodes[i]
                self.pad_net[end] = nodes[i + 1]

        # In-footprint bridged pairs (SAME source as the footprint generator,
        # so symbol stacking and net_tie_pad_groups can never drift apart).
        self.bridged_pairs = [
            (f"{ka}{la}", f"{kb}{lb}") for (ka, la), (kb, lb) in bridges
        ]
        self.pairset = set(self.bridged_pairs)
        # BOTH pins of a pair carry the IDENTICAL combined name: per-pin text
        # hiding is not honored by KiCad 8, so the only overlap-proof rendering
        # is two identical strings at the identical position (paint as one).
        self.combined = {
            p: f"{a}+{b}" for a, b in self.bridged_pairs for p in (a, b)
        }

        # Pin slots: one tall left-side bank, pins ordered by phase chain; each
        # bridged pair STACKS in one slot; one blank slot between phase blocks.
        self.slot_base: dict[int, int] = {}
        base = 0
        for p in range(3):
            self.slot_base[p] = base
            bridged_p = sum(
                1 for a, b in self.bridged_pairs if self.pad_net[a][0] == PH[p]
            )
            base += (2 * self.cpp - bridged_p) + 1
        self.total_slots = base - 1               # drop the trailing blank
        # slot 0 at the top; multiple of 1.27 so pins stay on the KiCad grid
        self.slot0_y = (self.total_slots - 1) / 2.0 * PITCH

        self.pin_local = {
            pad: (0.0, self.slot_y(self.pin_slot(pad))) for pad in self.all_pads
        }
        self.body_top = self.slot_y(0) + PITCH
        self.body_bot = self.slot_y(self.total_slots - 1) - PITCH

    def chain_order(self, p: int) -> list[str]:
        """Pad names along phase p's series winding, start->end, per coil:
        ``[c0.start, c0.end, c1.start, c1.end, ...]`` (2*cpp pads)."""
        order = []
        for t, s in self.chains[p]:
            a = f"{t}A" if s > 0 else f"{t}B"
            b = f"{t}B" if s > 0 else f"{t}A"
            order += [a, b]
        return order

    def pin_slot(self, pad: str) -> int:
        for p in range(3):
            o = self.chain_order(p)
            if pad not in o:
                continue
            slot = 0
            for i, q in enumerate(o):
                # a coil START (even i>0) shares its slot with the previous
                # coil's END when the pair is bridged in the footprint;
                # otherwise it opens a new slot
                if i and not (i % 2 == 0 and (o[i - 1], q) in self.pairset):
                    slot += 1
                if q == pad:
                    return self.slot_base[p] + slot
        raise KeyError(pad)

    def slot_y(self, slot: int) -> float:
        return self.slot0_y - slot * PITCH

    def drawn_joins(self) -> list[tuple[str, str]]:
        """Series joins that are real schematic wires (not in-footprint ties)."""
        out = []
        for p in range(3):
            o = self.chain_order(p)
            for i in range(1, 2 * self.cpp - 1, 2):
                a, b = o[i], o[i + 1]
                if (a, b) not in self.pairset:
                    out.append((a, b))
        return out


# --------------------------------------------------------------------------- #
# Symbol s-expression builders
# --------------------------------------------------------------------------- #
def _stator_symbol(plan: _Plan, symid: str, fp_link: str) -> list[str]:
    """The single full-stator symbol: one pin per footprint pad + the footprint.

    ``symid`` is the bare name ("stator") for the lib file, or "<lib>:stator"
    for the schematic's embedded lib_symbols copy.
    """
    L = []
    L.append(f'    (symbol "{symid}"')
    L.append("      (pin_names (offset 0.508))")
    # numbers == pad names == pin names; showing both doubles every row's text
    L.append("      (pin_numbers hide)")
    L.append("      (exclude_from_sim no) (in_bom yes) (on_board yes)")
    L.append(
        f'      (property "Reference" "M" (at 0 {plan.body_top + 2.54:.2f} 0)'
        " (effects (font (size 1.27 1.27))))"
    )
    L.append(
        f'      (property "Value" "stator" (at 0 {plan.body_bot - 2.54:.2f} 0)'
        " (effects (font (size 1.27 1.27))))"
    )
    L.append(
        f'      (property "Footprint" "{fp_link}" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    L.append(
        '      (property "Datasheet" "" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    L.append(
        f'      (property "Description" "{plan.n_slots}-coil PCB-motor stator'
        ' (full, 2-sided)" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    # graphic body: rectangle, pin-bank divider, bold phase letters, and a
    # drawn motor glyph (rotor ring + magnet wedges + shaft) so the part reads
    # as a motor at a glance. Glyph geometry preserved from the hand-tuned
    # original symbol (purely decorative -- 12 wedges regardless of n_slots).
    L.append('      (symbol "stator_0_1"')
    L.append(
        f"        (rectangle (start {BODY_X0:.2f} {plan.body_top:.2f})"
        f" (end {BODY_X1:.2f} {plan.body_bot:.2f})"
        "         (stroke (width 0.254) (type default)) (fill (type background)))"
    )
    L.append(
        f"        (polyline (pts (xy 17.78 {plan.body_top:.2f})"
        f" (xy 17.78 {plan.body_bot:.2f}))"
        " (stroke (width 0.127) (type default)) (fill (type none)))"
    )
    for p in range(3):
        yc = plan.slot_y(plan.slot_base[p] + 2)
        L.append(
            f'        (text "{PH[p]}" (at 12.16 {yc:.2f} 0)'
            " (effects (font (size 2.0 2.0) (bold yes))))"
        )
    # internal-bridge glyphs: a small arc ("jumper") at each stacked-pair slot,
    # inside the body next to the pin name -- these joins are copper arcs in
    # the footprint (net_tie_pad_groups), not schematic wires.
    for a, b in plan.bridged_pairs:
        y = plan.pin_local[a][1]
        L.append(
            f"        (arc (start 14.20 {y:.2f}) (mid 15.20 {y + 1.0:.2f})"
            f" (end 16.20 {y:.2f})"
            " (stroke (width 0.2) (type default)) (fill (type none)))"
        )
        for cxg in (14.20, 16.20):
            L.append(
                f"        (circle (center {cxg:.2f} {y:.2f}) (radius 0.22)"
                " (stroke (width 0.1) (type default)) (fill (type outline)))"
            )
    L += [
        "        (circle (center 39.78 0.00) (radius 16.00) (stroke (width 0.254) (type default)) (fill (type none)))",
        "        (circle (center 39.78 0.00) (radius 5.50) (stroke (width 0.254) (type default)) (fill (type background)))",
        "        (polyline (pts (xy 41.06 6.58) (xy 42.60 14.53) (xy 36.96 14.53) (xy 38.50 6.58) (xy 41.06 6.58)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 44.18 5.06) (xy 49.49 11.17) (xy 44.60 13.99) (xy 41.96 6.33) (xy 44.18 5.06)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 46.11 2.18) (xy 53.77 4.82) (xy 50.95 9.71) (xy 44.84 4.40) (xy 46.11 2.18)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 46.36 -1.28) (xy 54.31 -2.82) (xy 54.31 2.82) (xy 46.36 1.28) (xy 46.36 -1.28)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 44.84 -4.40) (xy 50.95 -9.71) (xy 53.77 -4.82) (xy 46.11 -2.18) (xy 44.84 -4.40)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 41.96 -6.33) (xy 44.60 -13.99) (xy 49.49 -11.17) (xy 44.18 -5.06) (xy 41.96 -6.33)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 38.50 -6.58) (xy 36.96 -14.53) (xy 42.60 -14.53) (xy 41.06 -6.58) (xy 38.50 -6.58)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 35.38 -5.06) (xy 30.07 -11.17) (xy 34.96 -13.99) (xy 37.60 -6.33) (xy 35.38 -5.06)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 33.45 -2.18) (xy 25.79 -4.82) (xy 28.61 -9.71) (xy 34.72 -4.40) (xy 33.45 -2.18)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 33.20 1.28) (xy 25.25 2.82) (xy 25.25 -2.82) (xy 33.20 -1.28) (xy 33.20 1.28)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 34.72 4.40) (xy 28.61 9.71) (xy 25.79 4.82) (xy 33.45 2.18) (xy 34.72 4.40)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 37.60 6.33) (xy 34.96 13.99) (xy 30.07 11.17) (xy 35.38 5.06) (xy 37.60 6.33)) (stroke (width 0.127) (type default)) (fill (type outline)))",
        "        (polyline (pts (xy 39.78 0.00) (xy 39.78 4.70)) (stroke (width 0.254) (type default)) (fill (type none)))",
        "        (polyline (pts (xy 39.78 0.00) (xy 43.85 -2.35)) (stroke (width 0.254) (type default)) (fill (type none)))",
        "        (polyline (pts (xy 39.78 0.00) (xy 35.71 -2.35)) (stroke (width 0.254) (type default)) (fill (type none)))",
        f'        (text "{plan.n_slots}-coil 3-phase WYE"'
        f" (at 39.78 {plan.body_bot + 1.6:.2f} 0) (effects (font (size 1.27 1.27))))",
    ]
    L.append("      )")
    # pins (one bank on the left edge, ordered by phase chain). A bridged pair
    # is TWO STACKED PINS: coincident connection points = one electrical node
    # (the join is copper inside the footprint). Both pins of a pair show the
    # IDENTICAL combined name so the coincident texts paint as one. Pin NUMBERS
    # stay the footprint pad names (hidden symbol-wide above).
    L.append('      (symbol "stator_1_1"')
    for pad in plan.all_pads:
        lx, ly = plan.pin_local[pad]
        name = plan.combined.get(pad, pad)
        L.append(
            f"        (pin passive line (at {lx:.2f} {ly:.2f} 0) (length 2.54)"
            f' (name "{name}" (effects (font (size 1.0 1.0))))'
            f' (number "{pad}" (effects (font (size 1.0 1.0)))))'
        )
    L.append("      )")
    L.append("    )")
    return L


def _coil_symbol(symid: str, fp_link_single: str) -> list[str]:
    """Legacy one-tooth 2-pin coil symbol (bound to the single-coil footprint)."""
    L = []
    L.append(f'    (symbol "{symid}"')
    L.append("      (pin_names (offset 0.254))")
    L.append("      (exclude_from_sim no) (in_bom yes) (on_board yes)")
    L.append('      (property "Reference" "C" (at 0 2.54 0) (effects (font (size 1.27 1.27))))')
    L.append('      (property "Value" "coil" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))')
    L.append(
        f'      (property "Footprint" "{fp_link_single}" (at 0 -5.08 0)'
        " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    L.append('      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')
    L.append(
        '      (property "Description" "PCB-motor coil (one tooth, 2-sided)"'
        " (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    L.append('      (symbol "coil_0_1"')
    L.append("        (polyline (pts (xy 0 3.81) (xy 0 2.54)) (stroke (width 0)) (fill (type none)))")
    L.append("        (polyline (pts (xy 0 -3.81) (xy 0 -2.54)) (stroke (width 0)) (fill (type none)))")
    for yc in (1.27, -0.0, -1.27):
        L.append(
            f"        (arc (start 0 {yc + 1.27:.3f}) (mid 1.0 {yc:.3f})"
            f" (end 0 {yc - 1.27:.3f}) (stroke (width 0)) (fill (type none)))"
        )
    L.append("      )")
    L.append('      (symbol "coil_1_1"')
    L.append(
        '        (pin passive line (at 0 6.35 270) (length 2.54)'
        ' (name "A" (effects (font (size 1.27 1.27))))'
        ' (number "A" (effects (font (size 1.27 1.27)))))'
    )
    L.append(
        '        (pin passive line (at 0 -6.35 90) (length 2.54)'
        ' (name "B" (effects (font (size 1.27 1.27))))'
        ' (number "B" (effects (font (size 1.27 1.27)))))'
    )
    L.append("      )")
    L.append("    )")
    return L


def _connector_symbol() -> list[str]:
    """Single solder-wire pad (Conn_01x01_Pin), placed six times: J1-J3 = phase
    leads A,B,C (in), J4-J6 = chain ends a,b,c (out). Series-link two boards by
    wiring top.a/b/c -> bottom.A/B/C; bridge a,b,c on one board only for the
    star. Pin at (5.08, 0) angle 180, i.e. the connection point lands 5.08 LEFT
    of the origin when the symbol is placed at rotation 180."""
    L = []
    L.append('    (symbol "Connector:Conn_01x01_Pin"')
    L.append("      (pin_names (offset 1.016) (hide yes))")
    L.append("      (exclude_from_sim no) (in_bom yes) (on_board yes)")
    L.append('      (property "Reference" "J" (at 0 2.54 0) (effects (font (size 1.27 1.27))))')
    L.append('      (property "Value" "Lead" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))')
    L.append(
        f'      (property "Footprint" "{SOLDERWIRE_FP}" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    L.append('      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')
    L.append(
        '      (property "Description" "Solder-wire pad, single terminal"'
        " (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    L.append('      (symbol "Conn_01x01_Pin_1_1"')
    L.append(
        "        (rectangle (start 0.8636 0.127) (end 0 -0.127)"
        " (stroke (width 0.1524) (type default)) (fill (type outline)))"
    )
    L.append(
        "        (polyline (pts (xy 1.27 0) (xy 0.8636 0))"
        " (stroke (width 0.1524) (type default)) (fill (type none)))"
    )
    L.append(
        "        (pin passive line (at 5.08 0 180) (length 3.81)"
        ' (name "Pin_1" (effects (font (size 1.27 1.27))))'
        ' (number "1" (effects (font (size 1.27 1.27)))))'
    )
    L.append("      )")
    L.append("    )")
    return L


# --------------------------------------------------------------------------- #
# Schematic
# --------------------------------------------------------------------------- #
def _wire(x1, y1, x2, y2):
    return (
        f"  (wire (pts (xy {x1:.2f} {y1:.2f}) (xy {x2:.2f} {y2:.2f}))"
        f' (stroke (width 0) (type default)) (uuid "{_uuid()}"))'
    )


def _label(name, x, y, rot, justify):
    return (
        f'  (label "{name}" (at {x:.2f} {y:.2f} {rot})'
        f' (effects (font (size 1.27 1.27)) (justify {justify})) (uuid "{_uuid()}"))'
    )


def _build_schematic(plan: _Plan, sym_lib: str, fp_link_full: str,
                     project: str) -> str:
    root_uuid = _uuid()   # root sheet uuid; symbol (instances ...) reference it
    L = [
        "(kicad_sch",
        f"  {VERSION_SCH}",
        f"  {GEN}",
        f'  (uuid "{root_uuid}")',
        '  (paper "A4")',
    ]

    # --- embedded lib symbols ---
    L.append("  (lib_symbols")
    L += _stator_symbol(plan, f"{sym_lib}:stator", fp_link_full)
    L += _connector_symbol()
    L.append("  )")

    # --- place the single stator symbol (vertically centred, on-grid) ---
    PX = 165.1
    PY = round((105.0 + (plan.body_top + plan.body_bot) / 2.0) / 1.27) * 1.27
    ax = lambda pad: PX + plan.pin_local[pad][0]   # abs x of a pin's connection
    ay = lambda pad: PY - plan.pin_local[pad][1]   # abs y (schematic Y flipped)
    L.append(f'  (symbol (lib_id "{sym_lib}:stator") (at {PX:.2f} {PY:.2f} 0) (unit 1)')
    L.append("    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)")
    L.append(f'    (uuid "{_uuid()}")')
    L.append(
        f'    (property "Reference" "M1" (at {PX + BODY_X1:.2f}'
        f' {PY - plan.body_top:.2f} 0) (effects (font (size 1.27 1.27))))'
    )
    L.append(
        f'    (property "Value" "stator" (at {PX + BODY_X1:.2f}'
        f' {PY - plan.body_bot:.2f} 0) (effects (font (size 1.27 1.27))))'
    )
    L.append(
        f'    (property "Footprint" "{fp_link_full}" (at {PX:.2f} {PY:.2f} 0)'
        " (effects (font (size 1.27 1.27)) (hide yes)))"
    )
    for pad in plan.all_pads:
        L.append(f'    (pin "{pad}" (uuid "{_uuid()}"))')
    L.append(
        f'    (instances (project "{project}" (path "/{root_uuid}"'
        ' (reference "M1") (unit 1))))'
    )
    L.append("  )")

    # --- WYE series joins. The adjacent-coil joins are copper arcs inside the
    # footprint, expressed as stacked pins -> NO wires. Only the cross-ring
    # joins are drawn. ---
    JOIN_X = PX - 5.08
    for a, b in plan.drawn_joins():
        ya, yb = ay(a), ay(b)
        L.append(_wire(ax(a), ya, JOIN_X, ya))
        L.append(_wire(JOIN_X, ya, JOIN_X, yb))
        L.append(_wire(JOIN_X, yb, ax(b), yb))

    # --- phase leads: stub + net label out to the connector ---
    for p in range(3):
        lead = plan.chain_order(p)[0]
        y = ay(lead)
        L.append(_wire(ax(lead), y, JOIN_X, y))
        L.append(_label(f"{PH[p]}_LEAD", JOIN_X, y, 0, "right"))

    # --- chain ends: NO on-board star. Each end gets its own stub + net label
    # (A_END/B_END/C_END) out to the connector, so the star is made externally
    # (bridge a,b,c on ONE board) and two boards can be series-linked. ---
    for p in range(3):
        end = plan.chain_order(p)[2 * plan.cpp - 1]
        y = ay(end)
        L.append(_wire(ax(end), y, JOIN_X, y))
        L.append(_label(f"{PH[p]}_END", JOIN_X, y, 0, "right"))

    # --- six solder-wire pads: leads in (J1-J3) + chain ends out (J4-J6), one
    # Conn_01x01_Pin per row, placed rot 180 so its pin (local (5.08, 0) angle
    # 180) lands 5.08 LEFT of the symbol origin -- exactly on the labeled stub.
    connector = [(f"J{p + 1}", f"{PH[p]}_LEAD") for p in range(3)] + [
        (f"J{p + 4}", f"{PH[p]}_END") for p in range(3)
    ]
    cx, cy = PX - 50.8, PY - 17.78
    for i, (ref, net) in enumerate(connector):
        y = cy - (2.5 - i) * 2.54
        L.append(
            f'  (symbol (lib_id "Connector:Conn_01x01_Pin")'
            f" (at {cx:.2f} {y:.2f} 180) (unit 1)"
        )
        L.append("    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)")
        L.append(f'    (uuid "{_uuid()}")')
        L.append(
            f'    (property "Reference" "{ref}" (at {cx + 1.27:.2f} {y - 1.27:.2f} 0)'
            " (effects (font (size 1.27 1.27)) (justify left)))"
        )
        L.append(
            f'    (property "Value" "Lead" (at {cx + 1.27:.2f} {y + 1.27:.2f} 0)'
            " (effects (font (size 1.27 1.27)) (justify left) (hide yes)))"
        )
        L.append(
            f'    (property "Footprint" "{SOLDERWIRE_FP}" (at {cx:.2f} {y:.2f} 0)'
            " (effects (font (size 1.27 1.27)) (hide yes)))"
        )
        L.append(f'    (pin "1" (uuid "{_uuid()}"))')
        L.append(
            f'    (instances (project "{project}" (path "/{root_uuid}"'
            f' (reference "{ref}") (unit 1))))'
        )
        L.append("  )")
        # pin connection point: origin + rot180(local (5.08, 0)) = 5.08 left
        px = cx - 5.08
        L.append(_wire(px, y, px - 2.54, y))
        L.append(_label(net, px - 2.54, y, 180, "right"))

    # --- title / note ---
    joins = ", ".join(f"{a}-{b}" for a, b in plan.drawn_joins())
    L.append(
        f'  (text "{plan.n_slots}-coil PCB-motor stator: ONE symbol (M1) -> full'
        " footprint.\\n"
        f"3-phase WYE ({plan.cpp} coils/phase, series). Pins == footprint pads.\\n"
        "Combined pins (arc glyph) = pad pairs BRIDGED by copper arcs inside\\n"
        "the footprint (net_tie_pad_groups) -- stacked pins, nothing to route.\\n"
        f"Cross-ring joins to route: {joins}.\\n"
        "NO on-board star: solder-wire pads J1-J3 = leads A,B,C; J4-J6 = ends"
        " a,b,c.\\n"
        "Dual-stator series: top.a/b/c -> bottom.A/B/C; bridge a,b,c on ONE"
        " board = floating star.\\n"
        'Single-board use: bridge a,b,c (J4-J6). S vias are internal per coil."'
        f' (at 30.48 25.4 0) (effects (font (size 2 2)) (justify left))'
        f' (uuid "{_uuid()}"))'
    )

    L.append('  (sheet_instances (path "/" (page "1")))')
    L.append(")")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# Project file + library tables
# --------------------------------------------------------------------------- #
def _build_project(project: str) -> str:
    pro = {
        "board": {"design_settings": {}, "layer_presets": [], "viewports": []},
        "boards": [],
        "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{project}.kicad_pro", "version": 1},
        "net_settings": {"classes": [{"name": "Default", "clearance": 0.2}]},
        "pcbnew": {"page_layout_descr_file": ""},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [],
        "text_variables": {},
    }
    return json.dumps(pro, indent=2) + "\n"


def _build_sym_lib_table(sym_lib: str) -> str:
    return (
        "(sym_lib_table\n"
        "  (version 7)\n"
        f'  (lib (name "{sym_lib}")(type "KiCad")(uri "${{KIPRJMOD}}/{sym_lib}'
        '.kicad_sym")(options "")(descr "PCB-motor stator + coil"))\n'
        ")\n"
    )


def _build_fp_lib_table(fp_nick: str, pretty: str) -> str:
    return (
        "(fp_lib_table\n"
        "  (version 7)\n"
        f'  (lib (name "{fp_nick}")(type "KiCad")(uri "${{KIPRJMOD}}/{pretty}")'
        '(options "")(descr "pcb_motor coil footprints (project-local)"))\n'
        ")\n"
    )


# --------------------------------------------------------------------------- #
# Self-check: the netlist against an independent reference
# --------------------------------------------------------------------------- #
# Reference WYE table for the tabulated 12N14P layout (hand-derived; kept
# verbatim from the original project as an independent cross-check).
_REFERENCE_12 = [
    ("A_LEAD", ["0A"]),
    ("A_N1", ["0B", "1B"]),
    ("A_N2", ["1A", "6B"]),
    ("A_N3", ["6A", "7A"]),
    ("B_LEAD", ["4A"]),
    ("B_N1", ["4B", "5B"]),
    ("B_N2", ["5A", "10B"]),
    ("B_N3", ["10A", "11A"]),
    ("C_LEAD", ["2B"]),
    ("C_N1", ["2A", "3A"]),
    ("C_N2", ["3B", "8A"]),
    ("C_N3", ["8B", "9B"]),
    ("A_END", ["7B"]),
    ("B_END", ["11B"]),
    ("C_END", ["9A"]),
]


def _self_check(plan: _Plan):
    """Return (ok, lines). Validates pad_net structurally, plus against the
    hand-derived reference table for the 12-slot layout."""
    lines = []
    ok = True
    # every pad assigned to exactly one net
    for pad in plan.all_pads:
        if pad not in plan.pad_net:
            ok = False
            lines.append(f"  FAIL pad {pad} unassigned")
    # per phase: cpp+1 distinct nets, shared only at series joins
    for p in range(3):
        o = plan.chain_order(p)
        nets = [plan.pad_net[q] for q in o]
        # coil interiors: start/end of one coil are different nets
        for i in range(0, len(o), 2):
            if nets[i] == nets[i + 1]:
                ok = False
                lines.append(f"  FAIL coil {o[i]}/{o[i+1]} shorted to one net")
        # series joins: end of coil i == start of coil i+1
        for i in range(1, len(o) - 1, 2):
            if nets[i] != nets[i + 1]:
                ok = False
                lines.append(f"  FAIL join {o[i]}-{o[i+1]}: {nets[i]} != {nets[i+1]}")
        if nets[0] != f"{PH[p]}_LEAD" or nets[-1] != f"{PH[p]}_END":
            ok = False
            lines.append(f"  FAIL phase {PH[p]} lead/end nets: {nets[0]}/{nets[-1]}")
    end_pads = sorted(p for p, n in plan.pad_net.items() if n.endswith("_END"))
    if len(end_pads) != 3:
        ok = False
        lines.append(f"  FAIL expected 3 chain-end pads, got {end_pads}")
    if any(n == "NEUTRAL" for n in plan.pad_net.values()):
        ok = False
        lines.append("  FAIL: a NEUTRAL net exists (star must be off-board)")
    lines.append(f"  chain-end pads (separate nets, no star) = {end_pads}")
    if plan.n_slots == 12:
        for net, pads in _REFERENCE_12:
            actual = {plan.pad_net.get(p) for p in pads}
            if actual != {net}:
                ok = False
                lines.append(
                    f"  FAIL net {net}: pads {pads} ->"
                    f" {sorted(str(a) for a in actual)} (expected all '{net}')"
                )
            else:
                lines.append(f"  ok   net {net}: {pads} all on '{net}'")
    return ok, lines


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def build_kicad_project(
    design: MotorDesign,
    out_dir: str,
    *,
    project_name: str = "pcb_motor_stator",
    sym_lib: str = "pcb_motor_coil",
    fp_nick: str = "pcb_motor",
    pretty: str = "pcb_motor.pretty",
    fp_full_name: str = "coil_full_2side",
    fp_single_name: str = "coil_single_2side",
    footprint_full: str | None = None,
    footprint_single: str | None = None,
    symbol_only: bool = False,
) -> ProjectReport:
    """Generate the KiCad project for ``design``'s full stator into ``out_dir``.

    Writes ``<sym_lib>.kicad_sym`` (stator + legacy coil symbols),
    ``<project_name>.kicad_sch`` (WYE pre-wired), ``sym-lib-table``,
    ``fp-lib-table`` and seeds ``<project_name>.kicad_pro`` (only if missing --
    an existing project file accumulates the user's DRC/board settings and is
    never clobbered). ``footprint_full``/``footprint_single`` are optional
    paths to ``.kicad_mod`` files (from
    :func:`pcb_motor.kicad.footprint.build_footprint`) to vendor into the
    project-local ``<pretty>`` library; the full footprint, when given, is
    cross-checked against the symbol (pad names, ``net_tie_pad_groups``).

    ``symbol_only=True`` writes ONLY the symbol lib (leave the schematic /
    tables / footprints on disk untouched, e.g. while the user is editing the
    schematic in KiCad); everything is still built in memory and all
    consistency checks still run.

    All checks run BEFORE any file is written; on failure a
    :class:`ProjectError` is raised and nothing is touched. Files are CRLF.
    """
    plan = _Plan(int(design.n_slots), int(design.n_phases))
    fp_link_full = f"{fp_nick}:{fp_full_name}"
    fp_link_single = f"{fp_nick}:{fp_single_name}"

    sym_txt = "\n".join(
        ["(kicad_symbol_lib", f"  {VERSION_SYM}", f"  {GEN}"]
        + _stator_symbol(plan, "stator", fp_link_full)
        + _coil_symbol("coil", fp_link_single)
        + [")"]
    ) + "\n"
    sch_txt = _build_schematic(plan, sym_lib, fp_link_full, project_name)

    checks: dict[str, bool] = {}
    lines: list[str] = []

    ok, chk_lines = _self_check(plan)
    checks["netlist_self_check"] = ok
    lines += chk_lines

    checks["parens_balanced"] = _balanced(sym_txt) and _balanced(sch_txt)
    n_pads = 2 * plan.n_slots
    checks["stator_instances"] = sch_txt.count(f'(lib_id "{sym_lib}:stator")') == 1
    checks["solder_wire_pads"] = (
        sch_txt.count('(lib_id "Connector:Conn_01x01_Pin")') == 6
    )
    checks["stator_pins_placed"] = (
        sum(sch_txt.count(f'(pin "{pad}" (uuid') for pad in plan.all_pads) == n_pads
    )
    n_wires_expect = 3 * len(plan.drawn_joins()) + 3 + 3 + 6
    checks["wires_drawn"] = sch_txt.count("(wire ") == n_wires_expect
    lines.append(
        f"  wires drawn: {sch_txt.count('(wire ')} (expect {n_wires_expect}:"
        f" {len(plan.drawn_joins())} cross-ring joins x3 + 3 leads + 3 ends + 6 J)"
    )
    # every footprint pad name must exist as a symbol pin NUMBER (the contract)
    checks["pin_number_eq_pad_name"] = all(
        f'(number "{pad}"' in sym_txt for pad in plan.all_pads
    )
    checks["symbol_fp_binding"] = (
        f'(property "Footprint" "{fp_link_full}"' in sym_txt
    )
    # each bridged pair's two pins must STACK (identical connection point)
    pin_at = {
        num: at
        for at, num in re.findall(
            r'\(pin passive line \(at ([-\d.]+ [-\d.]+) 0\).*?\(number "([^"]+)"',
            sym_txt,
        )
    }
    checks["bridged_pins_stacked"] = all(
        pin_at[a] == pin_at[b] for a, b in plan.bridged_pairs
    )

    # optional vendored footprint cross-checks (read the SOURCE files now, so
    # a mismatch aborts before anything is written)
    fp_texts: dict[str, str] = {}
    for nm, src in ((fp_full_name, footprint_full), (fp_single_name, footprint_single)):
        if src is None:
            continue
        if not os.path.exists(src):
            raise ProjectError(f"footprint source missing: {src}")
        with open(src, encoding="utf-8") as fh:
            fp_texts[nm] = fh.read()
    if fp_full_name in fp_texts:
        fp_txt = fp_texts[fp_full_name]
        n_a = sum(f'(pad "{k}A"' in fp_txt for k in range(plan.n_slots))
        n_b = sum(f'(pad "{k}B"' in fp_txt for k in range(plan.n_slots))
        checks["footprint_pads_complete"] = (
            n_a == plan.n_slots and n_b == plan.n_slots and _balanced(fp_txt)
        )
        m = re.search(r"\(net_tie_pad_groups ([^)]*)\)", fp_txt)
        fp_groups = (
            {frozenset(g.split(",")) for g in re.findall(r'"([^"]+)"', m.group(1))}
            if m
            else set()
        )
        checks["net_tie_groups_match"] = fp_groups == {
            frozenset(pr) for pr in plan.bridged_pairs
        }
        lines.append(
            f"  full footprint pads: A={n_a}/{plan.n_slots} B={n_b}/{plan.n_slots};"
            f" net-tie groups match symbol pairs: {checks['net_tie_groups_match']}"
        )

    passed = all(checks.values())
    if not passed:
        failed = [k for k, v in checks.items() if not v]
        raise ProjectError(
            f"project self-check FAILED ({', '.join(failed)}); nothing written"
        )

    # ---- all checks green: write (CRLF everywhere) ----
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []

    def _write(name: str, txt: str):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(txt)
        written.append(path)

    _write(f"{sym_lib}.kicad_sym", sym_txt)
    if not symbol_only:
        _write(f"{project_name}.kicad_sch", sch_txt)
        _write("sym-lib-table", _build_sym_lib_table(sym_lib))
        _write("fp-lib-table", _build_fp_lib_table(fp_nick, pretty))
        # the .kicad_pro accumulates the user's settings once KiCad opens the
        # project -- only seed it if missing, never clobber an existing one
        pro_path = os.path.join(out_dir, f"{project_name}.kicad_pro")
        if not os.path.exists(pro_path):
            with open(pro_path, "w", encoding="utf-8", newline="\r\n") as fh:
                fh.write(_build_project(project_name))
            written.append(pro_path)
        else:
            lines.append(f"  kept existing {pro_path} (preserving settings)")
        # vendor footprints into the project-local library (binary copy: the
        # builder already wrote them CRLF)
        pretty_dir = os.path.join(out_dir, pretty)
        for nm, src in (
            (fp_full_name, footprint_full),
            (fp_single_name, footprint_single),
        ):
            if src is None:
                continue
            os.makedirs(pretty_dir, exist_ok=True)
            dest = os.path.join(pretty_dir, f"{nm}.kicad_mod")
            shutil.copyfile(src, dest)
            written.append(dest)

    return ProjectReport(
        out_dir=out_dir, passed=passed, files=written, checks=checks, lines=lines
    )

"""General KiCad board (``.kicad_pcb``) for any concentrated-winding stator.

Wraps the filled-copper footprint from :func:`pcb_motor.kicad.build_footprint`
in a complete board -- circular ``Edge.Cuts`` + bore (+ optional mounting
holes), the terminal pads bound to the WYE netlist derived by
:class:`pcb_motor.kicad.project._Plan` -- and reuses
:func:`pcb_motor.kicad.build_kicad_project` for the schematic / symbol /
project / library-table scaffolding around it.

Honest limits, stated on the page and in docs:

* The coil copper is emitted as *netless graphic* polygons (that is how
  ``footprint.py`` builds it), so KiCad's connectivity engine can't trace a
  coil from one terminal to the other. The board is fully **manufacturable**
  (every polygon, pad and via plots to Gerbers) but is **not** connectivity-
  DRC-clean.
* Only the ADJACENT-coil series joins are copper (baked into the footprint as
  ``net_tie_pad_groups``). The cross-ring phase joins are declared as shared
  nets but carry **no track copper** -- KiCad shows them as a ratsnest for a
  human to route. This is the honest autonomous endpoint; there is no
  auto-router here.

gimbal90 keeps its verbatim fully-routed board (:mod:`pcb_motor.kicad.routed`);
every other design takes this path. The two reach a ``.kicad_pcb`` + Gerbers
through the identical ``pcb-motor board`` command.
"""

from __future__ import annotations

import dataclasses
import os
import re
import uuid

from ..design import MotorDesign
from .footprint import FootprintError, build_footprint
from .project import ProjectError, _Plan, build_kicad_project

_KICAD_VERSION = "20240108"
_GENERATOR = "pcb_motor"

# Board shell -- copied verbatim from routed.py's build_pcb() so a general board
# plots to Gerbers with the same layer stack and plot settings as the routed
# gimbal90 board. (Kept as a local copy rather than a shared import so routed.py
# -- the fab-verified gimbal90 path -- is never perturbed.)
_LAYERS = [
    '    (0 "F.Cu" signal)', '    (31 "B.Cu" signal)',
    '    (32 "B.Adhes" user "B.Adhesive")', '    (33 "F.Adhes" user "F.Adhesive")',
    '    (34 "B.Paste" user)', '    (35 "F.Paste" user)',
    '    (36 "B.SilkS" user "B.Silkscreen")', '    (37 "F.SilkS" user "F.Silkscreen")',
    '    (38 "B.Mask" user)', '    (39 "F.Mask" user)',
    '    (40 "Dwgs.User" user "User.Drawings")', '    (41 "Cmts.User" user "User.Comments")',
    '    (42 "Eco1.User" user "User.Eco1")', '    (43 "Eco2.User" user "User.Eco2")',
    '    (44 "Edge.Cuts" user)', '    (45 "Margin" user)',
    '    (46 "B.CrtYd" user "B.Courtyard")', '    (47 "F.CrtYd" user "F.Courtyard")',
    '    (48 "B.Fab" user)', '    (49 "F.Fab" user)',
]
_PCBPLOTPARAMS = [
    "    (pcbplotparams (layerselection 0x00010fc_ffffffff) (plot_on_all_layers_selection 0x0000000_00000000)",
    "      (disableapertmacros no) (usegerberextensions no) (usegerberattributes yes)",
    "      (usegerberadvancedattributes yes) (creategerberjobfile yes) (dashed_line_dash_ratio 12.0)",
    "      (dashed_line_gap_ratio 3.0) (svgprecision 4) (plotframeref no) (viasonmask no)",
    "      (mode 1) (useauxorigin no) (hpglpennumber 1) (hpglpenspeed 20) (hpglpendiameter 15.0)",
    "      (pdf_front_fp_property_popups yes) (pdf_back_fp_property_popups yes) (dxfpolygonmode yes)",
    "      (dxfimperialunits yes) (dxfusepcbnewfont yes) (psnegative no) (psa4output no)",
    "      (plotreference yes) (plotvalue yes) (plotfptext yes) (plotinvisibletext no) (sketchpadsonfab no)",
    "      (subtractmaskfromsilk no) (outputformat 1) (mirror no) (drillshape 1) (scaleselection 1)",
    '      (outputdirectory "")))',
]

_FP_NICK = "pcb_motor"
_FP_NAME = "coil_full_2side"        # the name build_kicad_project vendors under


class BoardError(RuntimeError):
    """Raised when a general board cannot be built (footprint/netlist failure)."""


@dataclasses.dataclass
class BoardReport:
    out_dir: str
    project: str
    pcb_path: str
    passed: bool
    net_pad_counts: dict          # net name -> pads bound on the board
    ratsnest_joins: int           # cross-ring joins left for a human to route
    files: list
    lines: list = dataclasses.field(default_factory=list)


def _uuid() -> str:
    return str(uuid.uuid4())


def _pad_spans(txt: str):
    """(start, end, name) of every top-level ``(pad ...)`` node."""
    out = []
    for m in re.finditer(r'\(pad "(\w+)" ', txt):
        i = m.start()
        depth = 0
        j = i
        while True:
            if txt[j] == "(":
                depth += 1
            elif txt[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append((i, j + 1, m.group(1)))
    return out


def _footprint_with_nets(mod_text, fp_link, cx, cy, pad_net, netnum) -> str:
    """The footprint body with each terminal pad bound to its WYE net and a
    board-placement header at ``(cx, cy)``. Stitch (``S``) vias stay on net 0."""
    txt = mod_text.replace("\r\n", "\n")
    body = txt[txt.index("\n") + 1: txt.rindex(")")]      # drop header + final ')'
    for i, j, nm in reversed(_pad_spans(body)):
        net = pad_net.get(nm)
        if net and net in netnum:
            body = body[:j - 1] + f' (net {netnum[net]} "/{net}")' + body[j - 1:]
    head = (
        f'  (footprint "{fp_link}" (layer "F.Cu")\n'
        f'    (uuid "{_uuid()}")\n'
        f'    (at {cx} {cy})\n'
        f'    (property "Reference" "M1" (at 0 -52 0) (layer "F.SilkS")'
        f' (uuid "{_uuid()}") (effects (font (size 1.5 1.5) (thickness 0.25))))\n'
        f'    (property "Value" "stator" (at 0 52 0) (layer "F.Fab")'
        f' (uuid "{_uuid()}") (effects (font (size 1.5 1.5) (thickness 0.25))))\n'
        f'    (path "/00000000-0000-0000-0000-000000000000")\n'
    )
    return head + body + "  )"


def _edge_circle(cx, cy, r) -> str:
    return (f"  (gr_circle (center {cx} {cy}) (end {cx + r} {cy}) "
            f'(stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts")'
            f' (uuid "{_uuid()}"))')


def _mount_hole(cx, cy, x, y, d) -> str:
    return (f'  (footprint "MountingHole" (layer "F.Cu") (uuid "{_uuid()}")'
            f' (at {cx + x} {cy + y})\n'
            f'    (attr exclude_from_pos_files exclude_from_bom allow_missing_courtyard)\n'
            f'    (pad "" np_thru_hole circle (at 0 0) (size {d} {d}) (drill {d})'
            f' (layers "*.Cu" "*.Mask") (uuid "{_uuid()}"))\n  )')


def build_board(
    design: MotorDesign,
    out_dir: str,
    *,
    footprint_path: str | None = None,
    edge_r_mm: float | None = None,
    bore_r_mm: float | None = None,
    mount_holes: tuple = (),
    cx_mm: float = 150.0,
    cy_mm: float = 100.0,
    resolution_m: float = 2.0e-4,
    project_name: str = "pcb_motor_stator",
) -> BoardReport:
    """Build a complete ``.kicad_pcb`` (+ project scaffolding) for ``design``.

    ``mount_holes`` is an iterable of ``(x_mm, y_mm, drill_mm)`` relative to the
    board centre. ``edge_r_mm`` / ``bore_r_mm`` default to the design's outer
    radius + 2 mm and inner radius. Raises :class:`BoardError` on a footprint
    clearance failure or netlist inconsistency (writes nothing in that case
    except what ``build_kicad_project`` already validated).
    """
    os.makedirs(out_dir, exist_ok=True)
    CX, CY = float(cx_mm), float(cy_mm)
    try:
        plan = _Plan(int(design.n_slots), int(design.n_phases))
    except ProjectError as exc:
        raise BoardError(str(exc)) from exc

    # 1) production footprint (build it unless one was supplied)
    if footprint_path is None:
        src_mod = os.path.join(out_dir, "stator_full_2side.kicad_mod")
        try:
            fp_rep = build_footprint(design, src_mod, resolution_m=resolution_m)
        except FootprintError as exc:
            raise BoardError(f"footprint clearance failed: {exc}") from exc
        if not fp_rep.passed:
            raise BoardError(f"footprint failed verification: {fp_rep.notes}")
    else:
        src_mod = footprint_path

    # 2) project scaffolding (pro/sch/sym/libs) + the footprint vendored into
    #    <out_dir>/pcb_motor.pretty/coil_full_2side.kicad_mod
    try:
        prep = build_kicad_project(design, out_dir, project_name=project_name,
                                   footprint_full=src_mod)
    except ProjectError as exc:
        raise BoardError(f"project scaffolding failed: {exc}") from exc
    vendored = os.path.join(out_dir, "pcb_motor.pretty", f"{_FP_NAME}.kicad_mod")

    # 3) number the nets (stable order along the phase chains)
    netnames: list[str] = []
    seen: set = set()
    for pad in plan.all_pads:
        n = plan.pad_net.get(pad)
        if n and n not in seen:
            seen.add(n)
            netnames.append(n)
    netnum = {n: i + 1 for i, n in enumerate(netnames)}

    # 4) assemble the board
    with open(vendored, encoding="utf-8") as fh:
        mod_text = fh.read()
    edge_r = float(edge_r_mm) if edge_r_mm is not None else design.r_outer_m * 1e3 + 2.0
    bore_r = float(bore_r_mm) if bore_r_mm is not None else design.r_inner_m * 1e3

    L = [
        f'(kicad_pcb (version {_KICAD_VERSION}) (generator "{_GENERATOR}")',
        "  (general (thickness 1.6) (legacy_teardrops no))",
        '  (paper "A4")',
        "  (layers",
        *_LAYERS,
        "  )",
        "  (setup (pad_to_mask_clearance 0.05)",
        *_PCBPLOTPARAMS,
        '  (net 0 "")',
    ]
    for n in netnames:
        L.append(f'  (net {netnum[n]} "/{n}")')
    fp_link = f"{_FP_NICK}:{_FP_NAME}"
    L.append(_footprint_with_nets(mod_text, fp_link, CX, CY, plan.pad_net, netnum))
    L.append(_edge_circle(CX, CY, edge_r))
    if bore_r > 1.0:
        L.append(_edge_circle(CX, CY, bore_r))
    for x, y, d in mount_holes:
        L.append(_mount_hole(CX, CY, float(x), float(y), float(d)))
    L.append(")")
    pcb_txt = "\n".join(L) + "\n"

    # 5) self-checks
    lines: list[str] = list(prep.lines)
    balanced = pcb_txt.count("(") == pcb_txt.count(")")
    net_pad_counts = {
        n: pcb_txt.count(f'(net {netnum[n]} "/{n}")') for n in netnames
    }
    all_bound = all(v > 0 for v in net_pad_counts.values())
    passed = bool(prep.passed and balanced and all_bound)

    pcb_path = os.path.join(out_dir, f"{project_name}.kicad_pcb")
    with open(pcb_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(pcb_txt)
    lines.append(f"wrote {os.path.basename(pcb_path)} "
                 f"({len(pcb_txt)} bytes, parens "
                 f"{'balanced' if balanced else 'UNBALANCED'})")
    ratsnest = len(plan.drawn_joins())
    lines.append(f"nets: {len(netnames)}; "
                 f"{ratsnest} cross-ring joins left as ratsnest (route in KiCad)")
    lines.append(f"SUMMARY: {'PASS' if passed else 'FAIL'}")

    files = list(prep.files) + [pcb_path]
    return BoardReport(
        out_dir=out_dir, project=project_name, pcb_path=pcb_path, passed=passed,
        net_pad_counts=net_pad_counts, ratsnest_joins=ratsnest,
        files=files, lines=lines,
    )

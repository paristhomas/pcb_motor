"""Fully-routed 12-slot stator footprint + KiCad project (gimbal90 routing baked in).

This is a near-verbatim port of the user's WORKING, SHIPPED studio scripts
(``pcb_motor_studio/scripts/build_routed_stator.py`` and
``build_routed_project.py``, Jul 2026), which produced the
``stator_routed_2side(_tabs).kicad_mod`` deliverables and the
``kicad_routed(_tabs)/`` projects for the gimbal90_odrive design. Same
algorithm, same constants, same geometry code paths -- only the plumbing
changed:

- input is a :class:`pcb_motor.design.MotorDesign` (gimbal90's ``motor.json``
  loads straight into it) instead of the studio Session;
- the coil artwork the routes attach to is regenerated here by a private,
  verbatim port of the studio's ``build_coil_footprint.py`` geometry pipeline
  (:func:`_coil_cache`). The production generator in
  :mod:`pcb_motor.kicad.footprint` intentionally deviates from that script
  (``_CARVE_MARGIN`` feeder narrowing, derived stitch dims, different bridge
  band), so it can NOT be used here without changing the shipped copper;
  its verbatim-identical ``keyhole``/``decimate`` helpers ARE reused.
- all writers emit CRLF (the user's KiCad saves CRLF; mixed endings turn every
  in-KiCad save into a whole-file diff);
- the scripts' end-of-run self-verification (``RESULT: PASS`` /
  ``SUMMARY: PASS``) is preserved: it still prints, is returned in the report,
  and a FAIL raises :class:`RoutedError` (the file is left on disk for
  inspection).

What the footprint IS (from the source script): coils + the user's WYE
interconnect BAKED IN. Two ring tracks (inner r46.2 / outer r48.45, 2.0 mm
wide) in the open band between the 90 mm coil disk and the 100 mm board edge,
8-via farms for layer hops, solder-wire terminals in one 6-deg-pitch cluster,
star point = short the three adjacent END terminals. NO netless copper: every
scrap of copper is a net-bearing pad (chain copper named by the LEAD pad,
ends AE/BE/CE separate, joined via ``net_tie_pad_groups``). ``tabs=True`` is
the non-circular variant: connector tab + 4 M3 half-hole mounts, terminals
out at r51.2 on the tab so ALL SIX are PTH (OD3), cluster rotated +14 deg to
fit the HARD 100x100 box.

The routing solution (ring radii, farm angles, terminal cluster, per-net route
pieces, the A/B/C chain -> tooth table) is hand-derived for the 12N14P layout
and gimbal90's geometry; :class:`NotImplementedError` is raised for
``n_slots != 12``. Generalisation is explicitly out of scope.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from ..coil_spiral import _corners_tapered, _count_turns, _geom_tapered, trace_width_at
from ..coils import _arc_polyline, _line, _polar
from ..design import MotorDesign
from .footprint import VIA_DRILL, VIA_PAD, decimate, keyhole

_KICAD_VERSION = "20240108"
_GENERATOR = "pcb_motor"


class RoutedError(RuntimeError):
    """A routed-stator / routed-project self-verification failed."""


# --------------------------------------------------------------------------- #
# Coil-shape recipe constants (verbatim from scripts/build_coil_footprint.py)
# --------------------------------------------------------------------------- #
GREEN = 1.30          # inner-arc width factor
CONN_W = 2.00e-3      # transition connecting track width (user spec): CONSTANT 2.0mm
CONN_TOP_INSET = 0.30e-3  # tap the innermost radial this far inboard of the rim tip
PAD_HW = 1.25e-3      # transition-pad half-width (tangential)
PAD_FRAC = 0.0625     # fraction of the terminal radial (rim end) -> connection pad
PAD_EXT = 1.5e-3      # extend the pad this far past the rim into the open band
TERM_W = 2.0e-3       # width CAP on the coil trace feeding the tab
DISK_R = 45.0e-3      # clip ALL copper to a 90mm-dia disk
BR_R0 = 43.0e-3       # bridge band inner radius
BR_G = 0.25e-3        # morphological-closing radius (fills gaps < 2*BR_G)
BR_OVL = 0.08e-3      # overlap depth into each pad (survives um rounding on emit)

# Coil layout (phase, sign) per tooth k -- the 12N14P star-of-slots table the
# routing below is derived against (mirrors pcb_motor.coils._LAYOUTS[12]).
LAYOUT = [(0, '+'), (0, '-'), (2, '-'), (2, '+'), (1, '+'), (1, '-'),
          (0, '-'), (0, '+'), (2, '+'), (2, '-'), (1, '-'), (1, '+')]

# --------------------------------------------------------------------------- #
# Interconnect constants (verbatim from scripts/build_routed_stator.py;
# all mm/deg unless suffixed; model frame, y-up, CCW+)
# --------------------------------------------------------------------------- #
FV_OD, FV_DRILL = 0.6e-3, 0.3e-3   # link-farm via (JLC standard 0.6/0.3)
STEP = 0.3e-3                      # arc polyline step

JA_BASE = {"AL": +28.0, "CL": +34.0, "BL": +40.0,  # lead cluster (moved-in, snapped)
           "AE": +16.0, "BE": +22.0}               # ends IN the cluster next to the leads
CE_A_BASE = +10.0  # C_END at the cluster's low end. Circular: SURFACE pad (F.Cu+F.Mask);
                   # tabs: PTH at r51.2 like the rest.
FARM = {"A": +113.0, "B2": +98.5, "C": +172.8, "B": -127.6}  # 8-via layer-hop farms @ RIN

MOUNT_ANGS, MOUNT_R, NOTCH_R = (60.0, 150.0, -120.0, -30.0), 54.0e-3, 1.6e-3
CONN_TAB = (20.5, 58.0, 53.1e-3)  # connector tab span + edge radius (r53.1 for OD3 pads)


# --------------------------------------------------------------------------- #
# Private port of the studio coil generator (build_coil_footprint.py):
# regenerates exactly the pickle-cache dict the routed script consumed.
# --------------------------------------------------------------------------- #
_CACHE: dict = {}


def _coil_cache(
    design: MotorDesign,
    *,
    wshrink_m: float = 0.02e-3,
    simplify_tol_m: float = 1.0e-5,
    resolution_m: float = 2.0e-4,
) -> dict:
    """Coil copper for the routed stator: ``{"co", "ANGS", "LAYOUT", "VIA_XY", "ts"}``.

    ``co[k] = [front_graphic, back_graphic, via_centre, padA, padB]`` per tooth
    (pads post-bridge), in the model frame. Verbatim geometry pipeline of the
    studio's ``build_coil_footprint.py`` (FILLETS off, flip layout, defaults).
    Memoised per (design, knobs) -- the tabs and circular variants share it.
    """
    if int(design.n_slots) != 12 or int(design.n_phases) != 3:
        raise NotImplementedError(
            "the routed stator is the hand-derived 12N14P solution; "
            f"n_slots={design.n_slots}/n_phases={design.n_phases} is not supported"
        )
    if not bool(getattr(design, "tapered_traces", False)):
        raise NotImplementedError(
            "the routed stator artwork is the tapered-wedge recipe; "
            "set tapered_traces=true on the design"
        )
    key = (json.dumps(dataclasses.asdict(design), sort_keys=True, default=str),
           wshrink_m, simplify_tol_m, resolution_m)
    if key in _CACHE:
        return _CACHE[key]

    D = design
    r_in, r_out, sector, delta, tw, ts = _geom_tapered(D)
    N = _count_turns(D)
    res = float(resolution_m)
    TAP = 2.2 * delta     # green taper length (per end)
    WSHRINK = float(wshrink_m)
    SIMP = float(simplify_tol_m)

    an = lambda s, r: max(2, int(np.ceil(abs(s) * r / res)))          # noqa: E731
    lin = lambda p, q: _line(p, q, max(2, int(np.ceil(np.hypot(*(q - p)[:2]) / res))))[:, :2]  # noqa: E731
    arc = lambda r, a, b: _arc_polyline(r, a, b, 0, an(b - a, r))[:, :2]  # noqa: E731

    def _buf(pl, w):
        w = np.atleast_1d(w)
        w = np.full(len(pl), w[0]) if len(w) == 1 else w
        w = np.maximum(0.2e-3, w - WSHRINK)   # uniform shrink for non-net clearance margin
        return unary_union([LineString(pl[j:j + 2]).buffer(float(w[j]) / 2,
                                                           cap_style=1, join_style=1)
                            for j in range(len(pl) - 1)])

    def base_sections():
        """Sections of the base coil (centre angle 0) as [poly, turnset]; + via/terminals."""
        C = [_corners_tapered(D, 0.0, t) for t in range(N)]
        ri = np.array([c["ri"] for c in C])
        w0 = np.array([trace_width_at(D, r) for r in ri])
        wmax = GREEN * w0
        pos = np.zeros(N)
        for t in range(1, N):
            pos[t] = pos[t - 1] + wmax[t - 1] / 2 + ts + wmax[t] / 2
        ri_new = pos - pos.mean() + ri.mean()
        ro = np.array([c["ro"] for c in C])
        left_top = np.array([ro[0]] + [ro[t] for t in range(N - 1)])
        R3 = C[N - 1]["R"]
        rB = ro[N - 1]

        def rad_w(rpts, tip):
            return trace_width_at(D, rpts)   # full width everywhere -- no thin tip, no neck

        S = []
        inner_left = None
        for t in range(N):
            c = C[t]
            L, R = c["L"], c["R"]
            rin = ri_new[t]
            tip = (t == N - 1)
            rl = lin(_polar(left_top[t], L, 0), _polar(rin, L, 0))
            wL = rad_w(np.hypot(*rl.T), tip)
            if t == 0:
                wL = np.minimum(wL, TERM_W)   # terminal radial feeds the tab: cap at 2mm
            plL = _buf(rl, wL)
            S.append([plL, {t}, w0[t]])
            if tip:
                inner_left = plL
            apl = arc(rin, L, R)
            aa = np.linspace(L, R, len(apl))
            d = np.minimum(aa - L, R - aa)
            S.append([_buf(apl, w0[t] + (wmax[t] - w0[t]) * np.clip(d / TAP, 0, 1)), {t}, w0[t]])
            rr = lin(_polar(rin, R, 0), _polar(ro[t], R, 0))
            S.append([_buf(rr, rad_w(np.hypot(*rr.T), tip)), {t}, trace_width_at(D, ro[t])])
            if t + 1 < N:
                xx = arc(ro[t], R, C[t + 1]["L"])
                S.append([_buf(xx, trace_width_at(D, np.hypot(*xx.T))), {t, t + 1},
                          trace_width_at(D, ro[t])])
        # ON-AXIS stitch landing = the connecting track ITSELF (no separate pad).
        tip = np.asarray(_polar(rB, R3, 0)[:2])
        conn_top = np.array([tip[0], tip[1] - CONN_TOP_INSET])
        conn_bot = np.array([tip[0], -PAD_HW + 1.0e-3])
        conn = _buf(lin(conn_top, conn_bot), CONN_W)
        S.append([conn, {N - 1}, CONN_W])
        # Terminal-A connection pad = OUTER PAD_FRAC of turn-0's left radial,
        # extended PAD_EXT past the rim.
        L0 = C[0]["L"]
        r_rim = left_top[0]
        r_innerrad = ri_new[0]
        r_mid = r_rim - PAD_FRAC * (r_rim - r_innerrad)
        pad_line = lin(_polar(r_mid, L0, 0), _polar(r_rim + PAD_EXT, L0, 0))
        pad_rad = np.minimum(np.hypot(*pad_line.T), r_rim)   # cap width past the rim
        padA_base = _buf(pad_line, trace_width_at(D, pad_rad))
        termB = np.array([tip[0], 0.0])                      # stitch centre, ON AXIS
        return S, padA_base, termB, inner_left

    DISK = Point(0, 0).buffer(DISK_R, quad_segs=256)
    BAND = DISK.difference(Point(0, 0).buffer(BR_R0, quad_segs=256))

    # ---------- build base coil ----------
    S, padA_base, termB, _inner_left = base_sections()
    front_base = unary_union([s[0] for s in S])
    # clip to ts/2 inside the sector so adjacent (rotated) coils clear by >= trace_space
    clippoly = Polygon(np.vstack([arc(r_out + 1.5e-3, -sector / 2, sector / 2),
                                  arc(r_in - 3e-3, sector / 2, -sector / 2)]))
    front_base = front_base.intersection(clippoly.buffer(-ts / 2))
    front_base = front_base.intersection(DISK)   # keep all coil copper within the 90mm disk
    front_base = keyhole(front_base, ts)
    front_base = decimate(front_base, SIMP)
    padA_ext = decimate(padA_base, SIMP)
    padA_clip = padA_ext.intersection(DISK)
    front_base = front_base.difference(padA_clip)
    # via cluster ON the connecting track (base frame): 2 cols (radial) x 3 rows (tangential)
    VIA_XY = np.array([[termB[0] + dx, dy]
                       for dx in (-0.55e-3, 0.55e-3) for dy in (-0.85e-3, 0.0, 0.85e-3)])

    def coil_at(angle, flip=False, clipA=True, clipB=True):
        """front + back (mirror about this coil's centre-line) copper, rotated to angle."""
        deg = np.degrees(angle)
        base = affinity.scale(front_base, yfact=-1, origin=(0, 0)) if flip else front_base
        bA = padA_clip if clipA else padA_ext
        bB = padA_clip if clipB else padA_ext
        pbA = affinity.scale(bA, yfact=-1, origin=(0, 0)) if flip else bA
        pbB = affinity.scale(bB, yfact=-1, origin=(0, 0)) if flip else bB
        f = affinity.rotate(base, deg, origin=(0, 0))
        b = affinity.rotate(affinity.scale(base, yfact=-1, origin=(0, 0)), deg, origin=(0, 0))
        via = affinity.rotate(Point(termB), deg, origin=(0, 0))
        padA = affinity.rotate(pbA, deg, origin=(0, 0))                                          # F.Cu
        padB = affinity.rotate(affinity.scale(pbB, yfact=-1, origin=(0, 0)), deg, origin=(0, 0))  # B.Cu
        return f, b, np.array(via.coords[0]), padA, padB

    def bridge_pads(pads, pairs):
        """Join each same-net adjacent pad pair with an arc-band patch inside the 90mm disk."""
        fills = {}
        for (ka, la), (kb, lb) in pairs:
            Pa, Pb = pads[(ka, la)], pads[(kb, lb)]
            u = unary_union([Pa, Pb])
            gap = (u.buffer(BR_G, join_style=1).buffer(-BR_G, join_style=1)
                   .difference(u).intersection(BAND))
            parts = [g for g in (gap.geoms if isinstance(gap, MultiPolygon) else [gap])
                     if not g.is_empty and g.distance(Pa) < 1e-9 and g.distance(Pb) < 1e-9]
            if not parts:
                raise RoutedError(f"bridge {ka}{la}<->{kb}{lb}: closing produced no corridor fill")
            fill = unary_union(parts)
            fill = fill.buffer(BR_OVL, join_style=1).intersection(u.union(fill)).intersection(BAND)
            pads[(ka, la)] = unary_union([Pa, fill])
            fills[(ka, la), (kb, lb)] = fill
        return fills

    FLIPS = [s == '-' for ph, s in LAYOUT]            # mirror the reverse-wound coils
    ANGS = [k * sector for k in range(int(D.n_slots))]

    # CLIP_PADS = pads whose series partner is the PHYSICALLY-ADJACENT coil.
    chains = {p: [(k, s) for k, (ph, s) in enumerate(LAYOUT) if ph == p] for p in range(3)}
    CLIP_PADS: set = set()
    BRIDGES: list = []
    for p in range(3):
        ch = chains[p]
        for i in range(len(ch) - 1):
            (ta, sa), (tb, sb) = ch[i], ch[i + 1]
            if abs(ta - tb) == 1:                                   # adjacent coils on the ring
                pa = (ta, "B" if sa == '+' else "A")                # end of coil i
                pb = (tb, "A" if sb == '+' else "B")                # start of coil i+1
                CLIP_PADS.add(f"{pa[0]}{pa[1]}")
                CLIP_PADS.add(f"{pb[0]}{pb[1]}")
                if pa[1] != pb[1]:
                    raise RoutedError(f"bridge pair {pa}/{pb} not on one layer")
                BRIDGES.append((pa, pb))                            # same letter -> same layer

    geoms = [coil_at(a, fl, f"{k}A" in CLIP_PADS, f"{k}B" in CLIP_PADS)
             for k, (a, fl) in enumerate(zip(ANGS, FLIPS))]
    pads = {(k, "A"): g[3] for k, g in enumerate(geoms)} | \
           {(k, "B"): g[4] for k, g in enumerate(geoms)}
    bridge_pads(pads, tuple(BRIDGES))
    co = [[g[0], g[1], g[2], pads[(k, "A")], pads[(k, "B")]] for k, g in enumerate(geoms)]

    out = {"co": co, "ANGS": ANGS, "LAYOUT": LAYOUT, "VIA_XY": VIA_XY, "ts": ts}
    _CACHE[key] = out
    return out


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
@dataclass
class RoutedStatorReport:
    """What :func:`build_routed_stator` printed and verified."""

    path: str
    name: str
    tabs: bool
    passed: bool
    min_phase_clearance_mm: float      # min copper distance between different phases
    clearance_needed_mm: float         # = trace_space [mm]
    n_custom_pads: int
    n_thru_hole: int
    n_vertices: int
    checks: dict[str, bool] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)   # the script's printed output


@dataclass
class RoutedProjectReport:
    """What :func:`build_routed_project` wrote and verified."""

    out_dir: str
    project: str
    fp_name: str
    tabs: bool
    passed: bool
    files: list[str] = field(default_factory=list)
    net_pad_counts: dict[str, int] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    stator_report: RoutedStatorReport | None = None


# --------------------------------------------------------------------------- #
# The routed stator footprint (port of scripts/build_routed_stator.py)
# --------------------------------------------------------------------------- #
def build_routed_stator(
    design: MotorDesign,
    out_path: str,
    *,
    tabs: bool = False,
    name: str | None = None,
    rin_m: float = 46.2e-3,            # inner ring track radius
    rout_m: float = 48.45e-3,          # outer ring track radius
    rj_m: float | None = None,         # terminal-pad radius (None: 51.2mm tabs / 48.55mm circular)
    track_w_m: float = 2.0e-3,         # interconnect width == user's 2.0mm
    j_od_m: float | None = None,       # solder-wire pad OD (None: 3.0mm tabs / 2.0mm circular)
    j_drill_m: float = 0.85e-3,        # user's SolderWire drill D0.85
    shift_deg: float | None = None,    # cluster rotation (None: +14deg tabs / 0 circular)
    wshrink_m: float = 0.02e-3,
    simplify_tol_m: float = 1.0e-5,
    resolution_m: float = 2.0e-4,
    render_dir: str | None = None,     # write routed_F.png / routed_B.png here (None: skip)
) -> RoutedStatorReport:
    """Build the fully-routed stator footprint and write ``out_path`` (CRLF).

    The emitted file is re-parsed and verified (shorts, per-net connectivity,
    net-tie overlap, via landing, board fit) exactly as the source script did;
    the ``RESULT: PASS`` gate becomes the returned report / a raised
    :class:`RoutedError`. With ``tabs=True`` the non-circular board outline is
    also written as ``tabs_outline.json`` next to ``out_path`` (the project
    builder's Edge.Cuts source).
    """
    TABS = bool(tabs)
    if name is None:
        base = os.path.basename(out_path)
        name = base[: -len(".kicad_mod")] if base.endswith(".kicad_mod") else base
    FPNAME = name
    lines: list[str] = []

    def say(msg: str) -> None:
        print(msg)
        lines.append(msg)

    # ---------------- coil geometry (from the verified coil generator) ----------------
    D = _coil_cache(design, wshrink_m=wshrink_m, simplify_tol_m=simplify_tol_m,
                    resolution_m=resolution_m)
    CO, ANGS, LAYOUT_, VIA_XY, ts = D["co"], D["ANGS"], D["LAYOUT"], D["VIA_XY"], D["ts"]
    phase_of = {k: ["A", "B", "C"][p] for k, (p, s) in enumerate(LAYOUT_)}
    sign_of = {k: s for k, (p, s) in enumerate(LAYOUT_)}
    CHAIN = {ph: f"{ph}L" for ph in "ABC"}            # chain copper carries the LEAD pad name

    def tab_ang(k, letter):
        c = CO[k][3 if letter == "A" else 4].centroid
        return float(np.degrees(np.arctan2(c.y, c.x)))

    # ---------------- interconnect parameters (mm/deg; model frame, y-up, CCW+) --------
    RIN, ROUT = float(rin_m), float(rout_m)
    RJ = float(rj_m) if rj_m is not None else (51.2e-3 if TABS else 48.55e-3)
    W = float(track_w_m)
    J_OD = float(j_od_m) if j_od_m is not None else (3.0e-3 if TABS else 2.0e-3)
    J_DRILL = float(j_drill_m)
    SHIFT = float(shift_deg) if shift_deg is not None else (14.0 if TABS else 0.0)

    JA = {k: a + SHIFT for k, a in JA_BASE.items()}
    CE_A = CE_A_BASE + SHIFT
    if TABS:
        JA = JA | {"CE": CE_A}

    pol = lambda r, a: np.array([r * np.cos(np.radians(a)), r * np.sin(np.radians(a))])  # noqa: E731

    def arcline(r, a0, a1):
        n = max(2, int(abs(np.radians(a1 - a0)) * r / STEP))
        aa = np.linspace(a0, a1, n)
        return np.column_stack([r * np.cos(np.radians(aa)), r * np.sin(np.radians(aa))])

    def buf(pl, w=W, cap=1):
        return LineString(pl).buffer(w / 2, cap_style=cap, join_style=1)

    def arcband(r, a0, a1, w=W):
        return buf(arcline(r, a0, a1), w)

    def seg(p, q, w=W):
        return buf(np.vstack([p, q]), w)

    def radial(a, r0, r1, w=W):
        # FLAT caps: a round cap would overshoot r0 by w/2 into the winding band (r<45)
        return buf(np.vstack([pol(r0, a), pol(r1, a)]), w, cap=2)

    def diag(r0, a0, r1, a1, w=W):
        return seg(pol(r0, a0), pol(r1, a1), w)

    def patch(a):   # farm landing patch: tangential strip @ RIN covering all 8 vias, both layers
        return arcband(RIN, a - 2.0, a + 2.0, 1.9e-3)

    TA = {f"{k}{L}": tab_ang(k, L) for k in range(12) for L in "AB"}

    # route pieces per pad name and layer. Angles from the REAL tab centroids.
    P = {("AL", "F"): [radial(JA["AL"], 46.2e-3, RJ), arcband(RIN, JA["AL"], TA["0A"]),
                       radial(TA["1A"], 46.3e-3, ROUT), arcband(ROUT, TA["1A"], 110.7),
                       diag(ROUT, 110.7, RIN, FARM["A"]), patch(FARM["A"])],
         ("AL", "B"): [patch(FARM["A"]), arcband(RIN, FARM["A"], TA["6B"])],
         ("BL", "B"): [arcband(ROUT, JA["BL"], 96.0),   # J2 annulus overlaps the outer track
                       diag(ROUT, 96.0, RIN, FARM["B2"]), patch(FARM["B2"]),
                       # B-link (mid-chain): 10B -> farm -> F outer -> 5A
                       arcband(RIN, TA["10B"], FARM["B"]), patch(FARM["B"])],
         ("BL", "F"): [patch(FARM["B2"]), arcband(RIN, FARM["B2"], TA["4A"]),
                       patch(FARM["B"]), diag(RIN, FARM["B"], ROUT, -131.0),
                       arcband(ROUT, -131.0, TA["5A"] - 360.0), radial(TA["5A"], 46.3e-3, ROUT)],
         ("CL", "B"): [radial(JA["CL"], 46.2e-3, RJ), arcband(RIN, JA["CL"], TA["2B"]),
                       # C-link: 3B -> B outer -> farm -> F inner -> 8A
                       diag(46.3e-3, TA["3B"], ROUT, 107.0), arcband(ROUT, 107.0, 170.0),
                       diag(ROUT, 170.0, RIN, FARM["C"]), patch(FARM["C"])],
         ("CL", "F"): [patch(FARM["C"]), arcband(RIN, FARM["C"], TA["8A"] + 360.0)],
         # A_END: 7B -> B outer track the whole way round the bottom into the cluster.
         ("AE", "B"): [diag(ROUT, -133.0, 46.0e-3, TA["7B"]), arcband(ROUT, -133.0, JA["AE"])],
         # B_END: 11B tab -> B inner track (starts 1.5deg past the tab) -> stub.
         ("BE", "B"): [arcband(RIN, -18.0, JA["BE"]), radial(JA["BE"], 46.2e-3, RJ)],
         # C_END: 9A stub -> F outer track -> terminal at +10.
         ("CE", "F"): [radial(TA["9A"], 46.3e-3, ROUT), arcband(ROUT, TA["9A"], CE_A)]}
    if TABS:
        # AL/CL/BE stubs above already run to RJ; BL/AE/CE arrive on the outer
        # track and additionally need a radial stub out to the pad ring on the tab
        for nm, lay in (("BL", "B"), ("AE", "B"), ("CE", "F")):
            P[(nm, lay)] = P[(nm, lay)] + [radial(JA[nm], 48.0e-3, RJ)]
    else:
        LAND_CE = arcband(RJ, CE_A - 1.8, CE_A + 1.8)   # exposed solder landing (F.Cu+F.Mask)

    # ---------------- assemble per-name per-layer copper ----------------
    COPPER = {}   # (name, layer) -> list of Polygons (connected components)
    for ph, coils in (("A", [0, 1, 6, 7]), ("B", [4, 5, 10, 11]), ("C", [2, 3, 8, 9])):
        nm = CHAIN[ph]
        fl = [CO[k][0] for k in coils] + [CO[k][3] for k in coils] + P.get((nm, "F"), [])
        bl = [CO[k][1] for k in coils] + [CO[k][4] for k in coils] + P.get((nm, "B"), [])
        for lay, polys in (("F.Cu", fl), ("B.Cu", bl)):
            u = unary_union(polys)
            COPPER[(nm, lay)] = [g for g in (u.geoms if isinstance(u, MultiPolygon) else [u])
                                 if g.area > 5e-8]
    for nm, lay in (("AE", "B.Cu"), ("BE", "B.Cu"), ("CE", "F.Cu")):
        u = unary_union(P[(nm, lay[0])])
        COPPER[(nm, lay)] = [g for g in (u.geoms if isinstance(u, MultiPolygon) else [u])
                             if g.area > 5e-8]

    for key, polys in COPPER.items():
        for g in polys:
            if list(g.interiors):
                raise RoutedError(f"{key}: piece has holes (emit needs hole-free polys)")

    # thru-hole pads: (name, x, y, od, drill)
    TH = []
    for k in range(12):
        ca, sa = np.cos(ANGS[k]), np.sin(ANGS[k])
        for vx, vy in VIA_XY:
            TH.append((CHAIN[phase_of[k]], ca * vx - sa * vy, sa * vx + ca * vy,
                       FV_OD, FV_DRILL))
    for fk, nm in (("A", "AL"), ("B2", "BL"), ("B", "BL"), ("C", "CL")):
        a = FARM[fk]
        for dr in (-0.55e-3, 0.55e-3):
            for dt in (-1.35e-3, -0.45e-3, 0.45e-3, 1.35e-3):
                ang = a + np.degrees(dt / RIN)
                x, y = pol(RIN + dr, ang)
                TH.append((nm, x, y, FV_OD, FV_DRILL))
    for nm, a in JA.items():
        x, y = pol(RJ, a)
        TH.append((nm, x, y, J_OD, J_DRILL))

    # ---------------- emit ----------------
    def fmt_poly_pad(g, name_, layers):
        xy = np.array(g.exterior.coords)
        rp = g.representative_point()
        cx, cy = rp.x * 1e3, -rp.y * 1e3
        pts = " ".join(f"(xy {x*1e3-cx:.3f} {-y*1e3-cy:.3f})" for x, y in xy)
        lay = " ".join(f'"{l}"' for l in layers)
        return [f'  (pad "{name_}" smd custom (at {cx:.3f} {cy:.3f}) (size 0.20 0.20) (layers {lay})',
                "    (options (clearance outline) (anchor circle))",
                f"    (primitives (gr_poly (pts {pts}) (width 0) (fill yes))))"]

    def silk_text(txt, x, y, rot, layer, size=2.0, th=0.3):
        mir = " (justify mirror)" if layer.startswith("B.") else ""
        return (f'  (fp_text user "{txt}" (at {x*1e3:.3f} {-y*1e3:.3f} {rot:.1f}) (layer "{layer}")'
                f" (effects (font (size {size} {size}) (thickness {th})){mir}))")

    def tang_rot(a):     # tangential, kept upright (KiCad rotation, deg)
        rot = (a - 90.0) % 360.0
        return rot - 180.0 if 90 < rot <= 270 else rot

    def rad_rot(a):      # radial (text runs along the radius), kept upright
        rot = a % 360.0
        return rot - 180.0 if 90 < rot <= 270 else rot

    L = [f'(footprint "{FPNAME}"', f"  (version {_KICAD_VERSION})",
         f'  (generator "{_GENERATOR}")',
         '  (layer "F.Cu")', "  (attr through_hole)",
         '  (net_tie_pad_groups "AL,AE" "BL,BE" "CL,CE")']
    for (nm, lay), polys in sorted(COPPER.items()):
        for g in polys:
            L += fmt_poly_pad(g, nm, [lay])
    if not TABS:
        L += fmt_poly_pad(LAND_CE, "CE", ["F.Cu", "F.Mask"])   # C_END exposed solder landing
    for nm, x, y, od, dr in TH:
        layers = '"*.Cu" "*.Mask"' if od > 1e-3 else '"*.Cu"'
        L.append(f'  (pad "{nm}" thru_hole circle (at {x*1e3:.3f} {-y*1e3:.3f}) '
                 f'(size {od*1e3:.2f} {od*1e3:.2f}) (drill {dr*1e3:.2f}) (layers {layers}))')

    # silkscreen
    JLBL = {"AL": "A_LEAD", "BL": "B_LEAD", "CL": "C_LEAD",
            "AE": "A_END", "BE": "B_END", "CE": "C_END"}
    for k in range(12):
        x, y = pol(22e-3, np.degrees(ANGS[k]))
        lbl = f"{phase_of[k]}{sign_of[k]}"
        L.append(silk_text(lbl, x, y, 0, "F.SilkS"))
        L.append(silk_text(lbl, x, y, 0, "B.SilkS"))
    x, y = pol(26.5e-3, 105.0)   # clear of the r22 coil labels at 90/120 deg
    L.append(silk_text("TOP", x, y, 0, "F.SilkS", size=1.5, th=0.25))
    L.append(silk_text("BOTTOM", x, y, 0, "B.SilkS", size=1.5, th=0.25))
    for nm, a in (JA | {"CE": CE_A}).items():
        # the whole cluster sits at 6 deg pitch -> RADIAL labels so they don't overlap
        x, y = pol(43.2e-3, a)
        for lay in ("F.SilkS", "B.SilkS"):
            L.append(silk_text(JLBL[nm], x, y, rad_rot(a), lay, size=1.4, th=0.22))
    # star instruction: bridging the three adjacent END terminals makes the star point.
    x, y = pol(46.6e-3, SHIFT)
    L.append(silk_text("SHORT 3 = STAR", x, y, tang_rot(SHIFT), "F.SilkS", size=0.9, th=0.15))
    for lay in ("F.CrtYd", "B.CrtYd"):
        L.append(f'  (fp_circle (center 0 0) (end 49.9 0) (stroke (width 0.05) (type solid)) '
                 f'(fill none) (layer "{lay}"))')
    L.append(")")
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(L) + "\n")
    npads = sum(len(v) for v in COPPER.values())
    verts = sum(len(g.exterior.coords) for v in COPPER.values() for g in v)
    say(f"wrote {out_path}: custom={npads}+CE-land th={len(TH)} verts={verts}")

    # ---------------- VERIFY the EMITTED file (re-parse; do not trust the writer) -------
    txt = open(out_path, encoding="utf-8").read()
    pads = []   # (name, layer, poly)
    for m in re.finditer(r'\(pad "(\w+)" smd custom \(at ([-\d.]+) ([-\d.]+)\) '
                         r'.*?\(layers "([^"]+)"[^)]*\).*?\(gr_poly \(pts (.*?)\) \(width',
                         txt, re.S):
        nm, cx, cy, lay = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)
        xy = [((float(a) + cx) * 1e-3, -(float(b) + cy) * 1e-3)
              for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", m.group(5))]
        pads.append((nm, lay, Polygon(xy).buffer(0)))
    ths = []
    for m in re.finditer(r'\(pad "(\w+)" thru_hole circle \(at ([-\d.]+) ([-\d.]+)\) '
                         r'\(size ([\d.]+)', txt):
        nm = m.group(1)
        x, y, od = float(m.group(2)) * 1e-3, -float(m.group(3)) * 1e-3, float(m.group(4)) * 1e-3
        ths.append((nm, Point(x, y).buffer(od / 2, quad_segs=16)))
    GROUP = {"AL": "A", "AE": "A", "BL": "B", "BE": "B", "CL": "C", "CE": "C"}
    checks: dict[str, bool] = {}
    # (1) SHORT/clearance: min distance between copper of DIFFERENT phase groups, per layer
    worst = 9.0
    for lay in ("F.Cu", "B.Cu"):
        items = [(GROUP[nm], p) for nm, l, p in pads if l == lay] + \
                [(GROUP[nm], p) for nm, p in ths]                      # th blocks both layers
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i][0] != items[j][0]:
                    d = items[i][1].distance(items[j][1])
                    worst = min(worst, d)
    say(f"VERIFY shorts: min copper distance between different phases = {worst*1e3:.3f} mm "
        f"(need >= {ts*1e3:.2f})")

    # (2) connectivity: per phase group, all items must form ONE cluster; per NET too
    def clusters(item_list):
        n = len(item_list)
        parent_ = list(range(n))

        def find(i):
            while parent_[i] != i:
                parent_[i] = parent_[parent_[i]]
                i = parent_[i]
            return i

        for i in range(n):
            for j in range(i + 1, n):
                (la, pa), (lb, pb) = item_list[i], item_list[j]
                if (la == lb or "TH" in (la, lb)) and pa.distance(pb) < 1e-9:
                    parent_[find(i)] = find(j)
        return len({find(i) for i in range(n)})

    conn_ok = True
    for grp in ("A", "B", "C"):
        items = [(l, p) for nm, l, p in pads if GROUP[nm] == grp] + \
                [("TH", p) for nm, p in ths if GROUP[nm] == grp]
        c = clusters(items)
        for nm in (grp + "L", grp + "E"):
            it2 = [(l, p) for n2, l, p in pads if n2 == nm] + \
                  [("TH", p) for n2, p in ths if n2 == nm]
            c2 = clusters(it2)
            say(f"VERIFY connect: net {nm}: {len(it2)} items -> {c2} cluster(s)")
            conn_ok &= (c2 == 1)
        say(f"VERIFY connect: phase {grp}: {len(items)} items -> {c} cluster(s)")
        conn_ok &= (c == 1)

    # (3) tie junctions really touch (AE..CE copper overlaps its chain copper)
    tie_ok = True
    for e, l in (("AE", "AL"), ("BE", "BL"), ("CE", "CL")):
        ue = unary_union([p for nm, _, p in pads if nm == e])
        ul = unary_union([p for nm, _, p in pads if nm == l])
        ov = ue.intersection(ul).area * 1e6
        say(f"VERIFY tie: {e} overlaps {l} copper by {ov:.2f} mm^2 (need > 0.5)")
        tie_ok &= ov > 0.5
    # (4) via landing: farm/stitch vias (small) must sit on own copper on BOTH layers;
    # J solder pads (big) are the interlayer path themselves -> >=1 layer suffices
    land_ok = True
    for nm, p in ths:
        big = (p.bounds[2] - p.bounds[0]) > 1.5e-3
        hit = [lay for lay in ("F.Cu", "B.Cu")
               if any(q.intersects(p) for n2, l, q in pads if n2 == nm and l == lay)]
        need = 1 if big else 2
        if len(hit) < need:
            say(f"  MISS: th pad {nm} @ {p.centroid.coords[0]} on {hit} (need {need} layers)")
            land_ok = False
    say(f"VERIFY th landing: {len(ths)} thru-holes on own copper (vias both layers, J >=1): "
        f"{land_ok}")

    # (5) board fit: ALL copper >= 0.3mm inside the board edge; nothing inside bore r17+0.3.
    def ring_sector(r0, r1, a0, a1):
        return Polygon(np.vstack([arcline(r1, a0, a1), arcline(r0, a1, a0)]))

    def tabs_outline():
        """Board outer edge for the tabs variant: r50 circle + connector tab (terminals) +
        4 mounting tabs with open M3 half-holes (notches). Concave corners closed to r1mm
        (mill-friendly); mounting tabs stay INSIDE the 100x100 bounding box."""
        out = unary_union([Point(0, 0).buffer(50e-3, quad_segs=512),
                           ring_sector(49e-3, CONN_TAB[2], CONN_TAB[0], CONN_TAB[1])] +
                          [ring_sector(49e-3, MOUNT_R, a - 5, a + 5) for a in MOUNT_ANGS])
        for a in MOUNT_ANGS:
            out = out.difference(Point(pol(MOUNT_R, a)).buffer(NOTCH_R, quad_segs=64))
        return out.buffer(1.0e-3, quad_segs=64).buffer(-1.0e-3, quad_segs=64)

    allcu = [p for _, _, p in pads] + [p for _, p in ths]
    rmax = max(float(np.hypot(*np.array(p.exterior.coords).T).max()) for p in allcu)
    rmin = min(float(np.hypot(*np.array(p.exterior.coords).T).min()) for p in allcu)
    if TABS:
        edge = tabs_outline()
        inset = edge.buffer(-0.295e-3)
        misses = [p for p in allcu if not p.within(inset)]
        bx = edge.bounds
        mounts_in_box = all(abs(v) <= 50.001e-3 for a in MOUNT_ANGS
                            for v in np.concatenate([pol(MOUNT_R, a - 5), pol(MOUNT_R, a + 5)]))
        notches_open = all(not edge.covers(Point(pol(MOUNT_R - 0.7e-3, a))) for a in MOUNT_ANGS)
        board_in_box = all(abs(v) <= 50.001e-3 for v in bx)   # HARD: whole board 100x100
        say(f"VERIFY board fit (tabs): copper r in [{rmin*1e3:.2f}, {rmax*1e3:.2f}] mm, "
            f"all >=0.3 inside edge: {not misses}; bbox "
            f"[{bx[0]*1e3:.1f},{bx[2]*1e3:.1f}]x[{bx[1]*1e3:.1f},{bx[3]*1e3:.1f}]; "
            f"WHOLE board in 100x100: {board_in_box}; "
            f"mount tabs in 100x100: {mounts_in_box}; M3 notches open: {notches_open}")
        fit_ok = (not misses and rmin >= 17.3e-3 and mounts_in_box and notches_open
                  and board_in_box)
        outline_path = os.path.join(parent, "tabs_outline.json")
        xy = np.array(edge.exterior.coords)
        with open(outline_path, "w", encoding="utf-8", newline="\r\n") as fh:
            json.dump({"outline_mm": [[round(x * 1e3, 4), round(-y * 1e3, 4)] for x, y in xy]},
                      fh)
        say(f"wrote {outline_path}")
    else:
        say(f"VERIFY board fit: copper r in [{rmin*1e3:.2f}, {rmax*1e3:.2f}] mm "
            f"(need [>17.3, <=49.7])")
        fit_ok = rmax <= 49.7e-3 and rmin >= 17.3e-3

    checks["shorts"] = worst >= ts - 2e-6
    checks["connectivity"] = bool(conn_ok)
    checks["net_ties"] = bool(tie_ok)
    checks["th_landing"] = bool(land_ok)
    checks["board_fit"] = bool(fit_ok)
    ok = all(checks.values())
    say("RESULT: " + ("PASS" if ok else "FAIL"))

    # ---------------- render (optional in the port) ----------------
    if render_dir is not None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPoly

        os.makedirs(render_dir, exist_ok=True)
        COL = {"AL": "#e6553f", "AE": "#8c2d1b", "BL": "#3fa0e6", "BE": "#1b5a8c",
               "CL": "#57d977", "CE": "#2d8c46"}
        for lay, base_fn in (("F.Cu", "routed_F.png"), ("B.Cu", "routed_B.png")):
            fn = os.path.join(render_dir, base_fn)
            fig, ax = plt.subplots(figsize=(13, 13))
            ax.set_facecolor("#101018")
            for nm, l, p in pads:
                if l != lay:
                    continue
                a = np.array(p.exterior.coords) * 1e3
                ax.add_patch(MplPoly(a, closed=True, facecolor=COL[nm], edgecolor="none",
                                     alpha=0.9))
            for nm, p in ths:
                c = p.centroid
                ax.add_patch(plt.Circle((c.x * 1e3, c.y * 1e3), p.bounds[2] * 1e3 - c.x * 1e3,
                                        fc="white", ec=COL[nm], lw=0.8, zorder=6))
            for r in (50.0, 17.0):
                ax.add_patch(plt.Circle((0, 0), r, fc="none", ec="#777", lw=1.2, zorder=7))
            hs = [plt.Line2D([0], [0], color=c, lw=4) for c in COL.values()]
            ax.legend(hs, list(COL), loc="upper left", fontsize=9, facecolor="#222",
                      labelcolor="w")
            ax.set_aspect("equal")
            ax.set_xlim(-52, 52)
            ax.set_ylim(-52, 52)
            ax.set_title(f"{FPNAME} {lay} (model frame, y-up)")
            fig.savefig(fn, dpi=110, bbox_inches="tight", facecolor="white")
            plt.close(fig)
            say(f"rendered {fn}")

    report = RoutedStatorReport(
        path=out_path, name=FPNAME, tabs=TABS, passed=ok,
        min_phase_clearance_mm=worst * 1e3, clearance_needed_mm=ts * 1e3,
        n_custom_pads=len(pads), n_thru_hole=len(ths),
        n_vertices="\n".join(L).count("(xy"), checks=checks, lines=lines,
    )
    if not ok:
        failed = [k for k, v in checks.items() if not v]
        raise RoutedError(
            f"routed stator verification FAILED ({', '.join(failed)}); "
            f"file left at {out_path} for inspection"
        )
    return report


# --------------------------------------------------------------------------- #
# The routed KiCad project (port of scripts/build_routed_project.py)
# --------------------------------------------------------------------------- #
_uuid = lambda: str(uuid.uuid4())   # noqa: E731

PINS = [("AL", "A_LEAD"), ("BL", "B_LEAD"), ("CL", "C_LEAD"),
        ("AE", "A_END"), ("BE", "B_END"), ("CE", "C_END")]
NETNUM = {nm: i + 1 for i, (nm, _) in enumerate(PINS)}
NETNAME = {nm: f"/{lbl}" for nm, lbl in PINS}

PITCH = 2.54
PIN_Y = {"AL": 5 * PITCH, "BL": 4 * PITCH, "CL": 3 * PITCH,
         "AE": -3 * PITCH, "BE": -4 * PITCH, "CE": -5 * PITCH}
BODY_X0, BODY_X1, BODY_TOP, BODY_BOT = 2.54, 58.78, 16.51, -16.51


def _stator_symbol(symid: str, fp_nick: str, fpname: str) -> list[str]:
    L = [f'    (symbol "{symid}"',
         "      (pin_names (offset 0.508))",
         "      (exclude_from_sim no) (in_bom yes) (on_board yes)",
         f'      (property "Reference" "M" (at {BODY_X0:.2f} {BODY_TOP + 1.27:.2f} 0) (effects (font (size 1.27 1.27)) (justify left)))',
         f'      (property "Value" "stator_routed" (at {BODY_X0:.2f} {BODY_BOT - 1.27:.2f} 0) (effects (font (size 1.27 1.27)) (justify left)))',
         f'      (property "Footprint" "{fp_nick}:{fpname}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))',
         '      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))',
         '      (property "Description" "gimbal90 12-coil stator, WYE fully routed in-footprint; leads AL/BL/CL, ends AE/BE/CE (series or star)" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))',
         '      (symbol "stator_routed_0_1"',
         f'        (rectangle (start {BODY_X0:.2f} {BODY_TOP:.2f}) (end {BODY_X1:.2f} {BODY_BOT:.2f}) (stroke (width 0.254) (type default)) (fill (type background)))']
    # winding glyph per phase row block + motor rotor glyph (from the 24-pin symbol)
    for nm, _ in PINS:
        y = PIN_Y[nm]
        for i, yc in enumerate((0.95, 0.0, -0.95)):
            L.append(f"        (arc (start {BODY_X0 + 2.2:.2f} {y + yc + 0.95:.2f}) (mid {BODY_X0 + 3.0:.2f} {y + yc:.2f}) (end {BODY_X0 + 2.2:.2f} {y + yc - 0.95:.2f}) (stroke (width 0.15) (type default)) (fill (type none)))")
    L += [
        '        (circle (center 39.78 0.00) (radius 16.00) (stroke (width 0.254) (type default)) (fill (type none)))',
        '        (circle (center 39.78 0.00) (radius 5.50) (stroke (width 0.254) (type default)) (fill (type background)))',
        '        (polyline (pts (xy 41.06 6.58) (xy 42.60 14.53) (xy 36.96 14.53) (xy 38.50 6.58) (xy 41.06 6.58)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 44.18 5.06) (xy 49.49 11.17) (xy 44.60 13.99) (xy 41.96 6.33) (xy 44.18 5.06)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 46.11 2.18) (xy 53.77 4.82) (xy 50.95 9.71) (xy 44.84 4.40) (xy 46.11 2.18)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 46.36 -1.28) (xy 54.31 -2.82) (xy 54.31 2.82) (xy 46.36 1.28) (xy 46.36 -1.28)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 44.84 -4.40) (xy 50.95 -9.71) (xy 53.77 -4.82) (xy 46.11 -2.18) (xy 44.84 -4.40)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 41.96 -6.33) (xy 44.60 -13.99) (xy 49.49 -11.17) (xy 44.18 -5.06) (xy 41.96 -6.33)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 38.50 -6.58) (xy 36.96 -14.53) (xy 42.60 -14.53) (xy 41.06 -6.58) (xy 38.50 -6.58)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 35.38 -5.06) (xy 30.07 -11.17) (xy 34.96 -13.99) (xy 37.60 -6.33) (xy 35.38 -5.06)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 33.45 -2.18) (xy 25.79 -4.82) (xy 28.61 -9.71) (xy 34.72 -4.40) (xy 33.45 -2.18)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 33.20 1.28) (xy 25.25 2.82) (xy 25.25 -2.82) (xy 33.20 -1.28) (xy 33.20 1.28)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 34.72 4.40) (xy 28.61 9.71) (xy 25.79 4.82) (xy 33.45 2.18) (xy 34.72 4.40)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 37.60 6.33) (xy 34.96 13.99) (xy 30.07 11.17) (xy 35.38 5.06) (xy 37.60 6.33)) (stroke (width 0.127) (type default)) (fill (type outline)))',
        '        (polyline (pts (xy 39.78 0.00) (xy 39.78 4.70)) (stroke (width 0.254) (type default)) (fill (type none)))',
        '        (polyline (pts (xy 39.78 0.00) (xy 43.85 -2.35)) (stroke (width 0.254) (type default)) (fill (type none)))',
        '        (polyline (pts (xy 39.78 0.00) (xy 35.71 -2.35)) (stroke (width 0.254) (type default)) (fill (type none)))',
        '        (text "12-coil 3-phase WYE" (at 39.78 -14.9 0) (effects (font (size 1.27 1.27))))',
        '        (text "fully routed in-footprint" (at 39.78 17.8 0) (effects (font (size 1.27 1.27))))',
        "      )",
        '      (symbol "stator_routed_1_1"']
    for nm, lbl in PINS:
        L.append(f'        (pin passive line (at 0 {PIN_Y[nm]:.2f} 0) (length 2.54)'
                 f' (name "{lbl}" (effects (font (size 1.0 1.0))))'
                 f' (number "{nm}" (effects (font (size 1.0 1.0)))))')
    L += ["      )", "    )"]
    return L


def _build_symbol_lib(fp_nick: str, fpname: str) -> str:
    return "\n".join(["(kicad_symbol_lib", "  (version 20231120)",
                      f'  (generator "{_GENERATOR}")']
                     + _stator_symbol("stator_routed", fp_nick, fpname) + [")"]) + "\n"


def _build_schematic(project: str, fp_nick: str, fpname: str, tabs: bool) -> str:
    root = _uuid()
    L = ["(kicad_sch", "  (version 20231120)", f'  (generator "{_GENERATOR}")',
         f'  (uuid "{root}")', '  (paper "A4")', "  (lib_symbols)"]
    L[-1] = "  (lib_symbols"
    L += _stator_symbol(f"{project}:stator_routed", fp_nick, fpname)
    L.append("  )")
    PX, PY = 165.1, 114.3
    L += [f'  (symbol (lib_id "{project}:stator_routed") (at {PX:.2f} {PY:.2f} 0) (unit 1)',
          "    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)",
          f'    (uuid "{_uuid()}")',
          f'    (property "Reference" "M1" (at {PX + BODY_X1:.2f} {PY - BODY_TOP:.2f} 0) (effects (font (size 1.27 1.27))))',
          f'    (property "Value" "stator_routed" (at {PX + BODY_X1:.2f} {PY - BODY_BOT:.2f} 0) (effects (font (size 1.27 1.27))))',
          f'    (property "Footprint" "{fp_nick}:{fpname}" (at {PX:.2f} {PY:.2f} 0) (effects (font (size 1.27 1.27)) (hide yes)))']
    for nm, _ in PINS:
        L.append(f'    (pin "{nm}" (uuid "{_uuid()}"))')
    L.append(f'    (instances (project "{project}" (path "/{root}" (reference "M1") (unit 1))))')
    L.append("  )")
    for nm, lbl in PINS:
        x, y = PX, PY - PIN_Y[nm]
        L.append(f'  (wire (pts (xy {x:.2f} {y:.2f}) (xy {x - 7.62:.2f} {y:.2f})) (stroke (width 0) (type default)) (uuid "{_uuid()}"))')
        L.append(f'  (label "{lbl}" (at {x - 7.62:.2f} {y:.2f} 0) (effects (font (size 1.27 1.27)) (justify right)) (uuid "{_uuid()}"))')
    ce_kind = "PTH on the connector tab" if tabs else "surface pad"
    L.append('  (text "gimbal90 stator, FULLY ROUTED in-footprint: winding + WYE interconnect +\\n'
             'terminals are all inside M1. Nothing to route on the board.\\n'
             f'One 6-pad cluster (bottom to top): C_END ({ce_kind}), A_END, B_END,\\n'
             'A_LEAD, C_LEAD, B_LEAD. Leads = drive in. Ends = chain ends out.\\n'
             'Dual-stator series: T.ends -> B.leads.\\n'
             'Single stator: SHORT the three adjacent END terminals = floating star point.\\n'
             'Note: each label names a single-pin net on purpose (terminals live in the\\n'
             'footprint); the one-item-label ERC rule is set to ignore in the project."'
             f' (at 30.48 25.4 0) (effects (font (size 2 2)) (justify left)) (uuid "{_uuid()}"))')
    L.append('  (sheet_instances (path "/" (page "1")))')
    L.append(")")
    return "\n".join(L) + "\n"


def _build_project_file(project: str) -> str:
    return json.dumps({
        "board": {"design_settings": {
            "rules": {
                "min_clearance": 0.127, "min_connection": 0.127,
                "min_copper_edge_clearance": 0.3,
                "min_hole_clearance": 0.25, "min_hole_to_hole": 0.5,
                "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1,
                "min_silk_clearance": 0.0, "min_text_height": 0.8,
                "min_text_thickness": 0.08, "min_through_hole_diameter": 0.3,
                "min_track_width": 0.127, "min_via_annular_width": 0.145,
                "min_via_diameter": 0.5, "solder_mask_to_copper_clearance": 0.0,
            },
            "defaults": {}, "drc_exclusions": [],
        }, "layer_presets": [], "viewports": []},
        "boards": [], "cvpcb": {"equivalence_files": []},
        # the six net labels each name a single-pin net (the physical terminals are
        # solder-wire pads INSIDE the footprint) -- that's the design, not a mistake;
        # silence the "label connects to only one item" heuristic (v9/v10 rule names)
        "erc": {"erc_exclusions": [], "rule_severities": {
            "label_dangling": "ignore", "isolated_pin_label": "ignore"}},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{project}.kicad_pro", "version": 1},
        "net_settings": {"classes": [{
            "name": "Default", "clearance": 0.13, "track_width": 0.2,
            "via_diameter": 0.6, "via_drill": 0.3,
            "bus_width": 12, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
            "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3,
            "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)", "wire_width": 6,
        }], "meta": {"version": 3}},
        "pcbnew": {"page_layout_descr_file": ""},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [], "text_variables": {},
    }, indent=2) + "\n"


def _pad_spans(txt: str):
    """(start, end, name) of every top-level (pad ...) node in a footprint file."""
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


def build_routed_project(
    design: MotorDesign,
    out_dir: str,
    *,
    tabs: bool = False,
    mod_path: str | None = None,       # pre-built routed footprint to use; None: build it
    project: str | None = None,        # None: "gimbal90_routed"/"gimbal90_routed_tabs"
    force_pro: bool = False,           # overwrite an existing .kicad_pro (holds user settings)
    cx_mm: float = 147.1,              # board centre == footprint anchor (exact)
    cy_mm: float = 95.3,
    edge_r_mm: float = 50.0,
    bore_r_mm: float = 17.0,
    stator_kwargs: dict | None = None,  # forwarded to build_routed_stator when building
) -> RoutedProjectReport:
    """KiCad project for the fully-routed stator; writes into ``out_dir`` (CRLF).

    Everything electrical lives inside the footprint, so the schematic is one
    6-pin symbol (AL/BL/CL leads, AE/BE/CE ends). The ``.kicad_pcb`` is a
    COMPLETE board: footprint at the board centre, nets bound to the pads,
    Edge.Cuts at BOARD level, ``.kicad_pro`` carries JLC standard-process
    rules. Never clobbers an existing ``.kicad_pro`` unless ``force_pro``.

    With ``mod_path=None`` the routed footprint is built (via
    :func:`build_routed_stator`) directly into the project's ``.pretty`` dir;
    otherwise ``mod_path`` (and its ``tabs_outline.json`` sibling, for tabs)
    is copied in. The source scripts' ``SUMMARY: PASS`` gate is preserved as
    the returned report / a raised :class:`RoutedError`.
    """
    TABS = bool(tabs)
    PROJECT = project if project is not None else (
        "gimbal90_routed_tabs" if TABS else "gimbal90_routed")
    FP_NICK = PROJECT
    CX, CY = float(cx_mm), float(cy_mm)
    lines: list[str] = []

    def say(msg: str) -> None:
        print(msg)
        lines.append(msg)

    os.makedirs(out_dir, exist_ok=True)
    pretty = os.path.join(out_dir, f"{PROJECT}.pretty")
    os.makedirs(pretty, exist_ok=True)

    stator_report = None
    if mod_path is None:
        FPNAME = "stator_routed_2side_tabs" if TABS else "stator_routed_2side"
        mod = os.path.join(pretty, f"{FPNAME}.kicad_mod")
        stator_report = build_routed_stator(design, mod, tabs=TABS,
                                            **(stator_kwargs or {}))
        lines.extend(stator_report.lines)
        outline_dir = pretty
    else:
        base = os.path.basename(mod_path)
        FPNAME = base[: -len(".kicad_mod")] if base.endswith(".kicad_mod") else base
        mod = os.path.join(pretty, f"{FPNAME}.kicad_mod")
        shutil.copyfile(mod_path, mod)
        outline_dir = os.path.dirname(os.path.abspath(mod_path))

    def footprint_with_nets(indent="  "):
        txt = open(mod, encoding="utf-8").read().replace("\r\n", "\n")
        body = txt[txt.index("\n") + 1: txt.rindex(")")]     # strip header line + final ')'
        # bind each pad to its net, walking pads back-to-front so spans stay valid
        for i, j, nm in reversed(_pad_spans(body)):
            if nm in NETNUM:
                body = body[:j - 1] + f' (net {NETNUM[nm]} "{NETNAME[nm]}")' + body[j - 1:]
        head = (f'{indent}(footprint "{FP_NICK}:{FPNAME}" (layer "F.Cu")\n'
                f'{indent}  (uuid "{_uuid()}")\n'
                f'{indent}  (at {CX} {CY})\n'
                f'{indent}  (property "Reference" "M1" (at 0 -52 0) (layer "F.SilkS") (uuid "{_uuid()}")'
                f' (effects (font (size 1.5 1.5) (thickness 0.25))))\n'
                f'{indent}  (property "Value" "stator_routed" (at 0 52 0) (layer "F.Fab") (uuid "{_uuid()}")'
                f' (effects (font (size 1.5 1.5) (thickness 0.25))))\n'
                f'{indent}  (path "/00000000-0000-0000-0000-000000000000")\n')
        return head + body + indent + ")"

    def build_pcb():
        L = [f'(kicad_pcb (version {_KICAD_VERSION}) (generator "{_GENERATOR}")',
             "  (general (thickness 1.6) (legacy_teardrops no))",
             '  (paper "A4")',
             "  (layers",
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
             "  )",
             "  (setup (pad_to_mask_clearance 0.05)",
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
             '  (net 0 "")']
        for nm, lbl in PINS:
            L.append(f'  (net {NETNUM[nm]} "/{lbl}")')
        L.append(footprint_with_nets())
        if TABS:
            # outer edge = the exact outline the footprint generator verified against
            with open(os.path.join(outline_dir, "tabs_outline.json"), encoding="utf-8") as fh:
                pts = json.load(fh)["outline_mm"]
            pp = " ".join(f"(xy {CX + x:.4f} {CY + y:.4f})" for x, y in pts)
            L.append(f"  (gr_poly (pts {pp}) (stroke (width 0.1) (type default)) (fill none)"
                     f' (layer "Edge.Cuts") (uuid "{_uuid()}"))')
            rings = (float(bore_r_mm),)
        else:
            rings = (float(edge_r_mm), float(bore_r_mm))
        for r in rings:
            L.append(f"  (gr_circle (center {CX} {CY}) (end {CX + r} {CY}) "
                     f'(stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{_uuid()}"))')
        L.append(")")
        return "\n".join(L) + "\n"

    SYMTAB = ('(sym_lib_table\n  (version 7)\n'
              f'  (lib (name "{PROJECT}")(type "KiCad")(uri "${{KIPRJMOD}}/{PROJECT}.kicad_sym")'
              '(options "")(descr "routed gimbal90 stator"))\n)\n')
    FPTAB = ('(fp_lib_table\n  (version 7)\n'
             f'  (lib (name "{FP_NICK}")(type "KiCad")(uri "${{KIPRJMOD}}/{PROJECT}.pretty")'
             '(options "")(descr "routed gimbal90 stator footprint"))\n)\n')

    files = {f"{PROJECT}.kicad_sym": _build_symbol_lib(FP_NICK, FPNAME),
             f"{PROJECT}.kicad_sch": _build_schematic(PROJECT, FP_NICK, FPNAME, TABS),
             f"{PROJECT}.kicad_pcb": build_pcb(),
             "sym-lib-table": SYMTAB, "fp-lib-table": FPTAB}
    pro = os.path.join(out_dir, f"{PROJECT}.kicad_pro")
    if not os.path.exists(pro) or force_pro:
        files[f"{PROJECT}.kicad_pro"] = _build_project_file(PROJECT)
    else:
        say(f"kept existing {pro}")
    ok = True
    for fname, txt in files.items():
        bal = txt.count("(") == txt.count(")") if not fname.endswith((".kicad_pro",)) else True
        ok &= bal
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(txt)
        say(f"wrote {fname}  ({len(txt)} bytes, parens {'balanced' if bal else 'UNBALANCED'})")
    # self-checks
    pcb = files[f"{PROJECT}.kicad_pcb"]
    nnets = {nm: pcb.count(f'(net {NETNUM[nm]} "{NETNAME[nm]}")') for nm, _ in PINS}
    modtxt = open(mod, encoding="utf-8").read().replace("\r\n", "\n")
    want = {nm: sum(1 for _, _, n in _pad_spans(modtxt) if n == nm) for nm, _ in PINS}
    net_pad_counts = {}
    for nm, _ in PINS:
        got = nnets[nm] - 1                       # minus the net-table declaration
        say(f"  net {NETNAME[nm]}: {got} pads bound (footprint has {want[nm]})")
        net_pad_counts[NETNAME[nm]] = got
        ok &= got == want[nm]
    sym = files[f"{PROJECT}.kicad_sym"]
    ok &= all(f'(number "{nm}"' in sym for nm, _ in PINS)
    say("SUMMARY: " + ("PASS" if ok else "FAIL"))

    report = RoutedProjectReport(
        out_dir=out_dir, project=PROJECT, fp_name=FPNAME, tabs=TABS, passed=bool(ok),
        files=sorted(list(files) + [f"{PROJECT}.pretty/{FPNAME}.kicad_mod"]),
        net_pad_counts=net_pad_counts, lines=lines, stator_report=stator_report,
    )
    if not ok:
        raise RoutedError(f"routed project verification FAILED in {out_dir}")
    return report

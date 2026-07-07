"""Production two-sided filled-copper KiCad footprint for the concentrated stator.

Turns the tapered-wedge concentrated winding (:mod:`pcb_motor.coil_spiral`) into
manufacturable copper artwork: filled shapely polygons per coil, mirrored for the
back layer, emitted as one ``.kicad_mod`` for the whole stator (or one tooth).

Per-turn sections: radials (torque) + "green" inner arcs (widened ``GREEN`` x,
spread about the arc-stack centroid, tapered back to radial width at the corners)
+ outer crossovers (clean constant-radius arc with a short final ramp) + an
on-axis connecting track at terminal B carrying the F<->B via stitch.

Hard-won properties this module preserves (do not "simplify" them away):

- **Two-layer coils MUST mirror.** B.Cu is the mirror of F.Cu about each coil's
  radial centre-line, so the layers are series and torque-additive. The stitch
  node sits ON the centre-line, where the mirror maps front copper onto itself:
  front-end == back-start is guaranteed by symmetry, not by luck.
- **Net-bearing terminal pads.** The winding body is *netless* ``fp_poly``
  graphic copper on purpose; only the outer ``pad_frac`` of turn-0's left radial
  is emitted as a custom SMD pad (net ``<k>A``, mirror ``<k>B`` on B.Cu), carved
  out of the graphic so pad + graphic share the exact cut edge. A trace can land
  on the pad in clear space; the rest of the winding stays an obstacle.
- **Per-coil flip follows the star-of-slots polarity.** Reverse-wound ('-')
  coils are physically mirrored so each phase's series link to its ring
  neighbour lands as two adjacent rim-flush pads; the flipped coil's torque
  sense is reversed, which is exactly right because those coils are the
  reverse-driven ones in the WYE.
- **In-footprint series bridges.** Adjacent same-net pad pairs are joined by an
  arc-band patch inside the board disk, unioned into the first pad's copper;
  ``net_tie_pad_groups`` marks the join as intentional.
- **decimate() never grows copper.** Shrink-inward then Douglas-Peucker keeps
  the simplified outline strictly INSIDE the original, so every clearance can
  only grow (and KiCad's interactive router stops crawling on ~7k-vertex polys).
- **Disk clipping.** All coil copper and the rim-flush pads are clipped to the
  board disk (``disk_radius_m``) so nothing exceeds the OD; extended pads (phase
  leads / cross-ring joins) intentionally poke past it into the open band.
- **Shapely clearance verify() BEFORE anything is written.** The builder
  re-parses its own emitted text and checks clearances, hole exclusion, the
  F<->B stitch and bridge overlap; on FAIL it raises and writes nothing.

The stitch cluster (connecting-track width, via rows/columns, track extents) is
*derived* from the local turn geometry instead of hard-coded: the derivations
reproduce the hand-tuned gimbal90 values (2.0 mm track, 3x2 vias, track bottom
at -0.25 mm) for gimbal90-sized coils and shrink gracefully for small coils,
where fewer vias fit between the innermost radials. Genuinely fixed fab
constants (JLC 1 oz rules) stay module constants.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys
from dataclasses import dataclass, field

import numpy as np
from shapely import affinity
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import nearest_points, unary_union

from ..coil_spiral import _corners_tapered, _count_turns, _geom_tapered, trace_width_at
from ..coils import _arc_polyline, _coil_layout, _line, _polar
from ..design import MotorDesign

# --------------------------------------------------------------------------- #
# Fixed fabrication constants (JLC-standard 1 oz rules -- not design knobs)
# --------------------------------------------------------------------------- #
VIA_DRILL = 0.30e-3       # via drill [m] (JLC standard)
VIA_PAD = 0.60e-3         # via pad [m] -> 0.15 mm annular ring
VIA_ROW_PITCH = 0.85e-3   # stitch-via pitch along the track (0.25 mm pad edge gap)
VIA_COL_PITCH = 1.10e-3   # stitch-via column pitch across the track (0.50 mm gap)
VIA_MARGIN = 0.10e-3      # track end-cap overshoot past the outermost via pad
MIN_TRACE = 0.127e-3      # 1 oz minimum trace width (5 mil); WSHRINK floor

# Coil-shape recipe constants (the verified "tweak-10" shape; scale-free).
GREEN = 1.30              # inner-arc width factor (+30 %)
TAP_TURNS = 2.2           # green taper length per end, in units of delta

_KICAD_VERSION = "20240108"
_GENERATOR = "pcb_motor"
_CLR_TOL_MM = 2e-3        # clearance-gate tolerance [mm] (emit rounding is 1 um)
_CARVE_MARGIN = 30e-6     # feeder radial is narrowed this much per side in the pad
                          # zone, so the pad carve always covers the graphic width
                          # even after both outlines are (independently) decimated
                          # -- otherwise a ~15 um graphic sliver survives alongside
                          # the pad and shows up as a clearance violation


class FootprintError(RuntimeError):
    """A clearance / stitch / bridge check failed; nothing was written."""


@dataclass
class FootprintReport:
    """What :func:`build_footprint` verified about the emitted artwork."""

    path: str
    name: str
    n_coils: int
    turns_per_coil: int
    passed: bool
    worst_clearance_mm: float          # min over every gated clearance below
    clearance_needed_mm: float         # = trace_space [mm]
    base_clearance_mm: float           # single-coil non-net section clearance
    pad_left_clearance_mm: float       # stitch track vs opposite innermost radial
    inter_coil_clearance_mm: float | None = None   # same-layer coil-to-coil
    pad_foreign_clearance_mm: float | None = None  # A/B pads vs foreign copper
    via_foreign_clearance_mm: float | None = None  # stitch vias vs foreign turns
    n_vias_per_coil: int = 0
    conn_track_w_mm: float = 0.0
    n_bridges: int = 0
    n_vertices: int = 0                # "(xy" count in the emitted file
    pad_names: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Layout plan: flips / rim-flush pads / bridges from the star-of-slots table
# --------------------------------------------------------------------------- #
def stator_plan(n_slots: int, n_phases: int = 3):
    """Per-tooth flips + adjacent-pair series bridges for the full stator.

    The '-' (reverse-wound) coils of the star-of-slots layout are physically
    MIRRORED (flipped about their centre-line) so each phase's series link to a
    ring-adjacent coil becomes two flush pads at the rim. For every such pair
    the end pad of coil ``i`` and the start pad of coil ``i+1`` land on the same
    copper layer (same letter) and are bridged in-footprint. Pairs whose pads
    would land on different layers (possible in non-tabulated fallback layouts)
    are left unbridged -- both pads stay extended for board-level routing.

    Returns ``(layout, flips, clip_pads, bridges)`` where ``clip_pads`` is the
    set of pad names clipped flush to the board disk and ``bridges`` is a list
    of ``((tooth, letter), (tooth, letter))`` same-net pairs.
    """
    layout = _coil_layout(int(n_slots), int(n_phases))
    flips = [sign < 0 for _, sign in layout]
    chains = {
        p: [(k, s) for k, (ph, s) in enumerate(layout) if ph == p]
        for p in range(n_phases)
    }
    clip_pads: set[str] = set()
    bridges: list[tuple[tuple[int, str], tuple[int, str]]] = []
    for p in range(n_phases):
        ch = chains[p]
        for i in range(len(ch) - 1):
            (ta, sa), (tb, sb) = ch[i], ch[i + 1]
            if abs(ta - tb) != 1:
                continue                                  # not ring-adjacent
            pa = (ta, "B" if sa > 0 else "A")             # end of coil i
            pb = (tb, "A" if sb > 0 else "B")             # start of coil i+1
            if pa[1] != pb[1]:
                continue                                  # different layers: no rim bridge
            clip_pads.add(f"{pa[0]}{pa[1]}")
            clip_pads.add(f"{pb[0]}{pb[1]}")
            bridges.append((pa, pb))
    return layout, flips, clip_pads, bridges


# --------------------------------------------------------------------------- #
# Clearance-safe polygon post-processing
# --------------------------------------------------------------------------- #
def _poly_parts(g) -> list[Polygon]:
    """The Polygon pieces of any shapely geometry (drops points/lines)."""
    if isinstance(g, Polygon):
        return [] if g.is_empty else [g]
    if isinstance(g, (MultiPolygon, GeometryCollection)):
        return [p for sub in g.geoms for p in _poly_parts(sub)]
    return []


def _holes(poly):
    return [
        g
        for p in (poly.geoms if isinstance(poly, MultiPolygon) else [poly])
        for g in p.interiors
    ]


def keyhole(poly, ts: float):
    """Open every enclosed hole with a thin (~trace_space) slit to the exterior,
    so the copper becomes a single hole-free outline emittable as one fp_poly.

    A slit from the boundary to a hole keeps the piece connected (C-shape) and
    only REMOVES copper, so it can never create a short. Slit endpoints
    overshoot by ``ts`` so the cut reliably reaches both the hole and outside.
    """
    out = []
    for g in (poly.geoms if isinstance(poly, MultiPolygon) else [poly]):
        guard = 0
        while g.interiors and guard < 80:
            guard += 1
            ring = max(g.interiors, key=lambda r: Polygon(r).area)
            ph, pe = nearest_points(LineString(ring.coords), g.exterior)
            a = np.array(ph.coords[0])
            b = np.array(pe.coords[0])
            v = b - a
            n = np.hypot(*v)
            u = v / n if n > 1e-12 else np.array([1.0, 0.0])
            slit = LineString([a - u * ts, b + u * ts]).buffer(
                ts * 0.6, cap_style=2, join_style=2
            )
            g2 = g.difference(slit)
            pcs = [
                x
                for x in (g2.geoms if isinstance(g2, MultiPolygon) else [g2])
                if x.area > 1e-10
            ]
            g = max(pcs, key=lambda x: x.area) if pcs else g
        out.append(g)
    return unary_union(out) if len(out) > 1 else out[0]


def decimate(poly, tol: float):
    """Reduce vertex count without EVER growing the outline (clearance-safe).

    Per-segment buffering makes ~7k-vertex polys; KiCad's interactive router
    does live clearance vs every edge -> it crawls/crashes. SHRINK inward by
    ``tol`` then Douglas-Peucker simplify by ``0.8*tol``: the simplified
    boundary stays within ``0.8*tol`` of the shrunk one, so the result is
    strictly INSIDE the original (every clearance only grows). Costs ~``tol``
    of trace width per side (negligible vs the trace_space margin). Drops
    slivers smaller than ``(2*tol)^2``.
    """
    g = (
        poly.buffer(-tol, join_style=2)
        .buffer(0)
        .simplify(0.8 * tol, preserve_topology=True)
        .buffer(0)
    )
    parts = g.geoms if isinstance(g, (MultiPolygon, GeometryCollection)) else [g]
    parts = [
        p
        for p in parts
        if p.geom_type in ("Polygon", "MultiPolygon") and p.area > (2 * tol) ** 2
    ]
    return unary_union(parts) if parts else g


def verify(items) -> float:
    """Min clearance [mm] between any two different-net copper polys.

    ``items`` is a list of ``[shapely poly, netset]``; pairs whose netsets
    intersect are the same electrical node and skipped.
    """
    worst = 9.0
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if items[a][1] & items[b][1]:
                continue
            worst = min(worst, items[a][0].distance(items[b][0]) * 1e3)
    return worst


# --------------------------------------------------------------------------- #
# s-expression emitters
# --------------------------------------------------------------------------- #
def _fp_poly(poly, layer: str) -> list[str]:
    out = []
    for g in _poly_parts(poly):
        if g.area < 5e-8:  # drop sub-0.05mm^2 slivers (decimation/clip artifacts)
            continue
        xy = np.array(g.exterior.coords)
        out.append(
            "  (fp_poly (pts "
            + " ".join(f"(xy {x*1e3:.3f} {-y*1e3:.3f})" for x, y in xy)
            + f') (stroke (width 0) (type solid)) (fill solid) (layer "{layer}"))'
        )
    return out


def _fp_pad_custom(poly, name: str, layer: str) -> list[str]:
    """Emit ``poly`` (shapely, model coords) as a net-bearing custom SMD pad.

    The anchor is an interior point; the primitive gr_poly is the pad outline
    (footprint coords, relative to the anchor). This is how the connection
    lands on net copper while keeping the coil's own shape -- the trace
    connects to ``name``, the rest of the winding stays graphic.
    """
    g = max(_poly_parts(poly), key=lambda p: p.area)
    xy = np.array(g.exterior.coords)
    fx = xy[:, 0] * 1e3
    fy = -xy[:, 1] * 1e3                                    # model -> footprint coords
    rp = g.representative_point()
    cx, cy = rp.x * 1e3, -rp.y * 1e3                        # guaranteed-interior anchor
    pts = " ".join(f"(xy {x - cx:.3f} {y - cy:.3f})" for x, y in zip(fx, fy))
    return [
        f'  (pad "{name}" smd custom (at {cx:.3f} {cy:.3f}) (size 0.20 0.20)'
        f' (layers "{layer}")',
        "    (options (clearance outline) (anchor circle))",
        f"    (primitives (gr_poly (pts {pts}) (width 0) (fill yes))))",
    ]


# --------------------------------------------------------------------------- #
# Emitted-output re-parsers (verify the OUTPUT, don't trust the writer)
# --------------------------------------------------------------------------- #
def _parse_fp(txt: str):
    lay = {"F.Cu": [], "B.Cu": []}
    for m in re.finditer(
        r'\(fp_poly\s*\(pts(.*?)\)\s*\(stroke.*?\(layer "([^"]+)"', txt, re.S
    ):
        pts = re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", m.group(1))
        if len(pts) >= 3:
            lay[m.group(2)].append(
                Polygon([(float(x) * 1e-3, -float(y) * 1e-3) for x, y in pts]).buffer(0)
            )
    return lay


def _parse_vias(txt: str):
    return [
        (float(a) * 1e-3, -float(b) * 1e-3)
        for a, b in re.findall(r"thru_hole \w+ \(at ([-\d.]+) ([-\d.]+)\)", txt)
    ]


def _parse_pads(txt: str):
    out = {}
    for m in re.finditer(
        r'\(pad "(\d+[AB])" smd custom \(at ([-\d.]+) ([-\d.]+)\).*?'
        r"\(primitives \(gr_poly \(pts (.*?)\) \(width",
        txt,
        re.S,
    ):
        nm, cx, cy = m.group(1), float(m.group(2)), float(m.group(3))
        xy = [
            (float(a) + cx, float(b) + cy)
            for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", m.group(4))
        ]
        out[nm] = Polygon([(x * 1e-3, -y * 1e-3) for x, y in xy]).buffer(0)
    return out


# --------------------------------------------------------------------------- #
# The builder
# --------------------------------------------------------------------------- #
class _StatorArtwork:
    """Base-coil copper + pads + stitch for one design; placed/rotated per tooth.

    All geometry is built once in the base frame (coil centre angle 0, coil axis
    = +x, radial centre-line = y=0) and then rotated/mirrored into place.
    """

    def __init__(
        self,
        design: MotorDesign,
        *,
        resolution_m: float = 2.0e-4,
        disk_radius_m: float | None = None,
        wshrink_m: float = 0.02e-3,
        simplify_tol_m: float = 1.0e-5,
        pad_frac: float = 0.0625,
        pad_ext_m: float = 1.5e-3,
        term_w_m: float = 2.0e-3,
        conn_w_m: float = 2.0e-3,
        conn_top_inset_m: float = 0.30e-3,
        fillets: bool = False,
    ):
        # The filled-copper footprint IS the tapered-wedge artwork: widths grow
        # with radius so clearance holds at trace_space everywhere. A design
        # with tapered_traces=False is coerced (trace_width_m = width at
        # r_inner_m) -- LOUDLY, because the emitted copper then differs from
        # what was simulated (turn count, resistance, Kt).
        self.notes: list[str] = []
        if not getattr(design, "tapered_traces", False):
            design = dataclasses.replace(design, tapered_traces=True)
            msg = (
                "design has tapered_traces=false but this footprint is ALWAYS "
                "the tapered-wedge artwork: turns/phase, resistance and Kt of "
                "the emitted copper differ from what was simulated -- "
                "re-evaluate with tapered_traces=true (e.g. pcb-motor point "
                "--set tapered_traces=true) before trusting the simulated "
                "numbers for this artwork"
            )
            self.notes.append("tapered_traces coerced to true: " + msg)
            print("WARNING: " + msg, file=sys.stderr)
        self.D = design
        if not bool(getattr(design, "tapered_traces", False)):
            raise ValueError("tapered_traces coercion failed")

        r_in, r_out, sector, delta, tw, ts = _geom_tapered(design)
        self.r_in, self.r_out, self.sector, self.delta = r_in, r_out, sector, delta
        self.tw, self.ts = tw, ts
        self.N = _count_turns(design)
        if self.N < 1:
            raise FootprintError(
                "no turns fit the sector: shrink trace_width/space or widen the annulus"
            )
        self.res = float(resolution_m)
        self.wshrink = float(wshrink_m)
        self.simp = float(simplify_tol_m)
        self.pad_frac = float(pad_frac)
        self.pad_ext = float(pad_ext_m)
        self.term_w = float(term_w_m)
        self.conn_w_cap = float(conn_w_m)
        self.conn_top_inset = float(conn_top_inset_m)
        self.fillets = bool(fillets)

        # Board disk: coil rim r_out + trace half-width overshoots past r_out;
        # the disk clip trims the overshoot so nothing (coil OR rim-flush pad)
        # exceeds the OD, and the pads stay flush + exposed at the rim.
        self.disk_r = (
            float(disk_radius_m) if disk_radius_m is not None else r_out + 0.5e-3
        )
        if self.disk_r < r_out:
            raise ValueError("disk_radius_m must be >= r_outer_m")
        self.DISK = Point(0, 0).buffer(self.disk_r, quad_segs=256)

        # Auto-raise the uniform width shrink when the stitch corridor is too
        # tight for a full via pad: the pad may protrude past the (shrunk)
        # track edge by at most the shrink -- exactly the extra clearance the
        # shrink bought -- so protrusion = (VIA_PAD - conn_w + shrink)/2 <=
        # shrink requires shrink >= VIA_PAD - conn_w.
        cN = _corners_tapered(design, 0.0, self.N - 1)
        if self.N >= 2:
            cP = _corners_tapered(design, 0.0, self.N - 2)
            w_prev = float(trace_width_at(design, cP["ro"]))
            tipx = cN["ro"] * np.cos(cN["R"])
            lim = 2.0 * (cP["ro"] - w_prev / 2.0 - ts - tipx)
        else:
            lim = np.inf
        corr = min(self.conn_w_cap, lim)
        need = VIA_PAD - corr
        if need > self.wshrink:
            if need > 0.05e-3:
                raise FootprintError(
                    f"stitch track far too narrow: the corridor between the "
                    f"innermost turns allows a {corr*1e3:.3f} mm track, but the "
                    f"{VIA_PAD*1e3:.2f} mm stitch-via pad needs >= "
                    f"{(VIA_PAD - 0.05e-3)*1e3:.2f} mm (coil turns too tightly "
                    "packed at the coil axis). Increase trace_width_m, "
                    "trace_space_m or r_inner_m -- fewer/looser turns leave "
                    "more room for the F<->B stitch."
                )
            self.wshrink = need + 2e-6
            self.notes.append(
                f"wshrink auto-raised to {self.wshrink*1e6:.0f} um so the stitch "
                "via pad may protrude past the narrow track"
            )

        self._build_base()

    # -- small geometry helpers (base frame, metres) -------------------------
    def _an(self, span: float, r: float) -> int:
        return max(2, int(np.ceil(abs(span) * r / self.res)))

    def _lin(self, p, q):
        n = max(2, int(np.ceil(np.hypot(*(q - p)[:2]) / self.res)))
        return _line(p, q, n)[:, :2]

    def _arc(self, r, a, b):
        return _arc_polyline(r, a, b, 0, self._an(b - a, r))[:, :2]

    def _buf(self, pl, w):
        """Buffer polyline ``pl`` per segment at width(s) ``w`` minus WSHRINK.

        The uniform width shrink lifts the worst non-net clearance above
        trace_space; the floor keeps every trace at/above the fab minimum
        (never wider than designed -- flooring above the design width would
        GROW copper and close gaps).
        """
        w = np.atleast_1d(np.asarray(w, dtype=float))
        w = np.full(len(pl), w[0]) if len(w) == 1 else w
        floor = min(MIN_TRACE, self.tw)
        w = np.maximum(floor, w - self.wshrink)
        return unary_union(
            [
                LineString(pl[j : j + 2]).buffer(
                    float(w[j]) / 2, cap_style=1, join_style=1
                )
                for j in range(len(pl) - 1)
            ]
        )

    # -- stitch cluster sizing ------------------------------------------------
    def _stitch_dims(self, tip, rB):
        """Derive the connecting-track + via-farm geometry from the local coil.

        The track runs straight down (constant x) from just below the innermost
        turn's outer-right tip to the radial centre-line, where the mirror maps
        it onto its own back-layer image -- every via inside both covers F and B.

        Constraints (all against nominal, un-shrunk widths; WSHRINK only adds
        margin): (a) track edge clears the previous turn's crossover ring
        outboard by trace_space; (b) the bottom end-cap clears the opposite
        (left) innermost radial by trace_space; (c) the top end-cap clears the
        neighbour turn's right radial by trace_space while still overlapping
        its own radial. Via rows/columns are then the largest JLC-pitch grid
        the track covers on BOTH layers.

        For gimbal90-class coils these reproduce the hand-tuned constants
        (2.0 mm track, 3 rows x 2 cols, bottom at -0.25 mm).
        """
        D, ts, delta, N = self.D, self.ts, self.delta, self.N
        tipx, tipy = float(tip[0]), float(tip[1])
        w_rb = float(trace_width_at(D, rB))
        R3 = float(_corners_tapered(D, 0.0, N - 1)["R"])

        # (a) width cap from the previous turn's crossover ring (x ~= radius
        # near the axis).
        if N >= 2:
            ro_prev = float(_corners_tapered(D, 0.0, N - 2)["ro"])
            w_prev = float(trace_width_at(D, ro_prev))
            lim = 2.0 * (ro_prev - w_prev / 2.0 - ts - tipx)
        else:
            lim = np.inf
        conn_w = min(self.conn_w_cap, lim)
        # Via pad may protrude past the track edge by at most WSHRINK (the
        # shrink bought exactly that much extra clearance to foreign copper);
        # __init__ auto-raised the shrink to guarantee this.
        if VIA_PAD - conn_w > self.wshrink + 1e-9:
            raise FootprintError(
                f"stitch track width {conn_w*1e3:.3f} mm cannot carry a "
                f"{VIA_PAD*1e3:.2f} mm via pad (needs >= "
                f"{(VIA_PAD - self.wshrink)*1e3:.3f} mm): coil turns too "
                "tightly packed. Increase trace_width_m, trace_space_m or "
                "r_inner_m to open the corridor at the coil axis."
            )

        # Radial-line clearances below use the exact point-to-line distance
        # |x*sin(a) - y*cos(a)| for the line through the origin at angle a.

        # (c) top: below the neighbour right radial, still tapping our own.
        conn_top = tipy - self.conn_top_inset
        if N >= 2:
            a_nb = R3 + delta                       # neighbour turn's right radial
            w_nb = float(trace_width_at(D, np.hypot(tipx, tipy)))
            # cap-top reach conn_top + conn_w/2 must keep w_nb/2 + ts to the line
            y_reach_max = (
                tipx * np.sin(a_nb) - (w_nb / 2.0 + ts)
            ) / np.cos(a_nb)
            conn_top = min(conn_top, y_reach_max - conn_w / 2.0)
        # own-radial contact: emitted overlap depth >= 0.05 mm
        contact = conn_w / 2.0 + w_rb / 2.0 - self.wshrink - (
            (tipy - conn_top) * np.cos(R3)
        )
        if contact < 0.05e-3:
            raise FootprintError(
                "stitch track cannot tap the innermost radial without shorting "
                "the neighbour turn"
            )

        # (b) bottom bound from the opposite (left, angle -R3) innermost radial.
        bot_min = (
            ts + conn_w / 2.0 + w_rb / 2.0 - tipx * np.sin(R3)
        ) / np.cos(R3)

        # Via grid: largest row count whose track coverage fits on BOTH layers
        # (front covers [conn_bot - r, conn_top + r]; back is the mirror). The
        # end-cap margin degrades gracefully in tight corridors: it only has to
        # beat the shrink + emit rounding, not stay at the comfortable default.
        n_rows, margin = 0, VIA_MARGIN
        for n in (3, 2, 1):
            for mg in (VIA_MARGIN, 0.05e-3):
                dy_max = (n - 1) / 2.0 * VIA_ROW_PITCH
                cover = dy_max + VIA_PAD / 2.0 + mg
                if cover <= conn_top + conn_w / 2.0 and bot_min <= conn_w / 2.0 - cover:
                    n_rows, margin = n, mg
                    break
            if n_rows:
                break
        if n_rows == 0:
            raise FootprintError(
                f"no stitch via fits between the innermost radials on both "
                f"layers: the {conn_w*1e3:.3f} mm track spans y "
                f"[{bot_min*1e3:.3f}, {conn_top*1e3:.3f}] mm but one "
                f"{VIA_PAD*1e3:.2f} mm via pad needs "
                f"{(VIA_PAD + 2*0.05e-3)*1e3:.2f} mm of covered track on BOTH "
                "layers. Increase trace_width_m, trace_space_m or r_inner_m "
                "so the innermost turns leave more room at the coil axis."
            )
        dy_max = (n_rows - 1) / 2.0 * VIA_ROW_PITCH
        cover = dy_max + VIA_PAD / 2.0 + margin
        conn_bot = max(bot_min, conn_w / 2.0 - cover)

        n_cols = 2 if conn_w + 1e-9 >= VIA_COL_PITCH + VIA_PAD + 0.29e-3 else 1
        dxs = (-VIA_COL_PITCH / 2.0, VIA_COL_PITCH / 2.0) if n_cols == 2 else (0.0,)
        dys = (np.arange(n_rows) - (n_rows - 1) / 2.0) * VIA_ROW_PITCH
        via_xy = np.array([[tipx + dx, dy] for dx in dxs for dy in dys])

        if conn_bot >= conn_top:
            raise FootprintError("stitch track degenerate (bottom above top)")
        self.notes.append(
            f"stitch: track {conn_w*1e3:.2f} mm wide, y [{conn_bot*1e3:.2f}, "
            f"{conn_top*1e3:.2f}] mm, {n_rows}x{n_cols} vias"
        )
        return conn_w, conn_top, conn_bot, via_xy

    # -- base coil ------------------------------------------------------------
    def _base_sections(self):
        """Sections of the base coil (centre angle 0) as [poly, turnset, width];
        plus the terminal pad, stitch centre, sector polygon, connecting track
        and the innermost-left radial (for the same-net short check)."""
        D, N, ts = self.D, self.N, self.ts
        C = [_corners_tapered(D, 0.0, t) for t in range(N)]
        ri = np.array([c["ri"] for c in C])
        w0 = np.asarray(trace_width_at(D, ri), dtype=float)
        wmax = GREEN * w0
        # Spread the widened green arcs about the arc-stack centroid so the
        # extra width is absorbed symmetrically instead of eating one gap.
        pos = np.zeros(N)
        for t in range(1, N):
            pos[t] = pos[t - 1] + wmax[t - 1] / 2 + ts + wmax[t] / 2
        ri_new = pos - pos.mean() + ri.mean()
        ro = np.array([c["ro"] for c in C])
        # Turn t's left radial rises to where the incoming crossover lands:
        # the rim for t=0, the previous turn's outer radius after that.
        left_top = np.array([ro[0]] + [ro[t] for t in range(N - 1)])
        R3 = C[N - 1]["R"]
        rB = ro[N - 1]
        tap = TAP_TURNS * self.delta
        # Terminal-pad cut radius (needed now: the feeder radial is narrowed a
        # hair in the pad zone so the carve covers it -- see _CARVE_MARGIN).
        r_rim = ro[0]
        r_mid = r_rim - self.pad_frac * (r_rim - ri_new[0])

        S: list[list] = []
        inner_left = None
        own_right = None
        for t in range(N):
            c = C[t]
            L, R = c["L"], c["R"]
            rin = ri_new[t]
            tip_turn = t == N - 1
            # left radial (full width everywhere -- no thin tip, no neck)
            rl = self._lin(_polar(left_top[t], L, 0), _polar(rin, L, 0))
            r_seg = np.hypot(*rl.T)
            wL = np.asarray(trace_width_at(D, r_seg), dtype=float)
            if t == 0:
                # terminal radial feeds the tab: cap at the interconnect width
                wL = np.minimum(wL, self.term_w)
                # in the pad zone, keep the graphic strictly inside the pad
                # outline so the carve leaves no sliver (winding continuity is
                # via the shared cut edge at r_mid, which stays full width)
                in_pad = r_seg >= r_mid - 0.1e-3
                wL = np.where(in_pad, np.minimum(wL, wL - 2 * _CARVE_MARGIN), wL)
            plL = self._buf(rl, wL)
            S.append([plL, {t}, w0[t]])
            if tip_turn:
                inner_left = plL
            # green inner arc: widened, tapered to radial width at the corners
            apl = self._arc(rin, L, R)
            aa = np.linspace(L, R, len(apl))
            d = np.minimum(aa - L, R - aa)
            S.append(
                [
                    self._buf(apl, w0[t] + (wmax[t] - w0[t]) * np.clip(d / tap, 0, 1)),
                    {t},
                    w0[t],
                ]
            )
            # right radial
            rr = self._lin(_polar(rin, R, 0), _polar(ro[t], R, 0))
            plR = self._buf(rr, trace_width_at(D, np.hypot(*rr.T)))
            S.append([plR, {t}, float(trace_width_at(D, ro[t]))])
            if tip_turn:
                own_right = plR
            # outer crossover to the next turn (flat arc + short final ramp is
            # already baked into _corners_tapered geometry: constant radius here)
            if t + 1 < N:
                xx = self._arc(ro[t], R, C[t + 1]["L"])
                S.append(
                    [
                        self._buf(xx, trace_width_at(D, np.hypot(*xx.T))),
                        {t, t + 1},
                        float(trace_width_at(D, ro[t])),
                    ]
                )

        # ON-AXIS stitch landing = the connecting track ITSELF (no separate
        # pad). On y=0 the mirror maps on-axis copper to itself, so front-end
        # == back-start; the track's round end-caps reach past the vias so each
        # via lands on BOTH layers.
        tip = np.asarray(_polar(rB, R3, 0)[:2])
        conn_w, conn_top_y, conn_bot_y, via_xy = self._stitch_dims(tip, rB)
        conn_top = np.array([tip[0], conn_top_y])
        conn_bot = np.array([tip[0], conn_bot_y])
        conn = self._buf(self._lin(conn_top, conn_bot), conn_w)
        S.append([conn, {N - 1}, conn_w])

        # Terminal-A connection pad = OUTER pad_frac of turn-0's left radial,
        # extended pad_ext past the rim. Buffered with the SAME radius-dependent
        # width as the radial so it tiles the coil seamlessly once carved out of
        # the graphic below.
        L0 = C[0]["L"]
        pad_line = self._lin(_polar(r_mid, L0, 0), _polar(r_rim + self.pad_ext, L0, 0))
        pad_rad = np.minimum(np.hypot(*pad_line.T), r_rim)  # cap width past the rim
        padA_base = self._buf(pad_line, trace_width_at(D, pad_rad))

        termB = np.array([tip[0], 0.0])                     # stitch centre, ON AXIS
        r_lo = max(0.5e-3, self.r_in - 3e-3)
        secpoly = Polygon(
            np.vstack(
                [
                    self._arc(self.r_out, -self.sector / 2, self.sector / 2),
                    self._arc(r_lo, self.sector / 2, -self.sector / 2),
                ]
            )
        )
        return (S, padA_base, termB, secpoly, conn, inner_left, own_right,
                via_xy, conn_w, r_mid)

    def _add_fillets(self, S, secpoly):
        """Corner fills clipped to >= ts from every foreign net (sections AND
        prior fills)."""
        ts = self.ts
        placed = [(s[0], s[1]) for s in S]
        fills = []
        for i in range(len(S) - 1):
            own = S[i][1] | S[i + 1][1]
            inter = S[i][0].intersection(S[i + 1][0].buffer(1e-4))
            if inter.is_empty:
                continue
            corner = np.array(inter.centroid.coords[0])
            Rf = 2.0 * (S[i][2] + ts)
            region = Point(corner).buffer(Rf).intersection(secpoly)
            foreign = unary_union([p for p, tn in placed if not (tn & own)])
            f = region.difference(foreign.buffer(ts)).difference(
                unary_union([S[i][0], S[i + 1][0]])
            )
            parts = [
                g
                for g in (f.geoms if isinstance(f, MultiPolygon) else [f])
                if not g.is_empty
                and (g.distance(S[i][0]) < 1e-9 or g.distance(S[i + 1][0]) < 1e-9)
            ]
            if parts:
                fp = unary_union(parts)
                fills.append([fp, own])
                placed.append((fp, own))
        return fills

    def _build_base(self):
        ts = self.ts
        (
            S,
            padA_base,
            termB,
            secpoly,
            conn,
            inner_left,
            own_right,
            via_xy,
            conn_w,
            r_cut,
        ) = self._base_sections()
        self.termB = termB
        self.via_xy = via_xy
        self.conn_w = conn_w
        items = [[s[0], s[1]] for s in S]
        if self.fillets:
            items += self._add_fillets(S, secpoly)
        self.base_clr_mm = verify(items)
        # The stitch track and the opposite (left) innermost radial are the
        # SAME net, but the track must NOT touch it or it shorts out the
        # innermost turn's loop -> explicit clearance check.
        self.pad_left_clr_mm = conn.distance(inner_left) * 1e3
        # The track must genuinely tap its own right radial (series continuity).
        self.conn_taps_radial = conn.intersects(own_right)
        # Stitch vias vs foreign turns (the via pad is real copper on every
        # layer; it may protrude past the track edge by at most WSHRINK).
        via_discs = [Point(x, y).buffer(VIA_PAD / 2) for x, y in via_xy]
        foreign = [s[0] for s in S if self.N - 1 not in s[1]]
        self.via_foreign_clr_mm = min(
            (v.distance(f) * 1e3 for v in via_discs for f in foreign), default=9.0
        )
        self.via_left_clr_mm = min(v.distance(inner_left) * 1e3 for v in via_discs)
        self.vias_in_track = sum(
            conn.buffer(self.wshrink).contains(v) for v in via_discs
        )

        front = unary_union([p for p, _ in items])
        # Clip to ts/2 inside the sector so adjacent (rotated) coils clear by
        # >= trace_space. The clip wedge has an OVERSIZED outer arc so
        # buffer(-ts/2) insets only the angular sector edges and never trims
        # the outermost crossover, which naturally spans past r_out.
        r_lo = max(0.5e-3, self.r_in - 3e-3)
        clippoly = Polygon(
            np.vstack(
                [
                    self._arc(self.r_out + 1.5e-3, -self.sector / 2, self.sector / 2),
                    self._arc(r_lo, self.sector / 2, -self.sector / 2),
                ]
            )
        )
        front = front.intersection(clippoly.buffer(-ts / 2))
        front = front.intersection(self.DISK)   # keep coil copper within the disk
        self.orig_holes = [
            Polygon(g).representative_point() for g in _holes(front)
        ]  # a point INSIDE each hole
        front = keyhole(front, ts)
        # Vertex-decimate BEFORE the pad carve so the pad and the remaining
        # graphic share the EXACT cut edge (winding continuity preserved).
        front = decimate(front, self.simp)
        padA_ext = decimate(padA_base, self.simp)
        padA_clip = padA_ext.intersection(self.DISK)
        # Carve the terminal-A pad region out of the front graphic so that
        # copper IS the pad (net A), not netless winding. B.Cu is the front
        # mirror, so the mirrored carve removes the B-pad region automatically.
        front = front.difference(padA_clip)
        self.front_base = front
        self.padA_ext = padA_ext
        self.padA_clip = padA_clip
        self.pad_cut_r = r_cut
        # Bridge band: at the rim, above both the disk-2mm line and the pad cut
        # edge + 1.2 mm (never near the netless-graphic cut edges).
        self.br_r0 = max(self.disk_r - 2.0e-3, r_cut + 1.2e-3)
        self.br_g = max(0.25e-3, 0.6 * ts)      # closing fills gaps < 2*br_g
        self.br_ovl = 0.08e-3                   # overlap so connectivity survives rounding
        self.BAND = self.DISK.difference(
            Point(0, 0).buffer(self.br_r0, quad_segs=256)
        )

    # -- placement -------------------------------------------------------------
    def coil_at(self, angle: float, flip: bool = False, clipA: bool = True,
                clipB: bool = True):
        """front + back (mirror about this coil's centre-line) copper at ``angle``.

        ``flip=True`` mirrors the WHOLE coil about its centre-line before
        placing: this swaps the F/B copper chirality AND the A/B pad edges, so a
        phase's series link to its neighbour becomes a short adjacent hop. The
        on-axis via stitch is mirror-invariant. A flipped coil's torque sense is
        reversed, so it must be driven with reversed polarity -- i.e. flip
        exactly the '-' (reverse-wound) coils of the WYE layout.

        ``clipA``/``clipB`` pick the disk-clipped pad (flush at the rim, for
        adjacent-pair series links) vs the extended pad (pokes past the disk
        into the open band, for cross-ring joins / leads / neutral).
        """
        deg = np.degrees(angle)
        base = (
            affinity.scale(self.front_base, yfact=-1, origin=(0, 0))
            if flip
            else self.front_base
        )
        bA = self.padA_clip if clipA else self.padA_ext
        bB = self.padA_clip if clipB else self.padA_ext
        pbA = affinity.scale(bA, yfact=-1, origin=(0, 0)) if flip else bA
        pbB = affinity.scale(bB, yfact=-1, origin=(0, 0)) if flip else bB
        f = affinity.rotate(base, deg, origin=(0, 0))
        b = affinity.rotate(affinity.scale(base, yfact=-1, origin=(0, 0)), deg,
                            origin=(0, 0))
        via = affinity.rotate(Point(self.termB), deg, origin=(0, 0))
        padA = affinity.rotate(pbA, deg, origin=(0, 0))                    # F.Cu, net A
        padB = affinity.rotate(
            affinity.scale(pbB, yfact=-1, origin=(0, 0)), deg, origin=(0, 0)
        )                                                                  # B.Cu, net B
        return f, b, np.array(via.coords[0]), padA, padB

    def bridge_pads(self, pads, pairs):
        """Join each same-net adjacent pad pair with an arc-band patch inside
        the board disk.

        For each pair the thin corridor between the facing radial edges is
        filled by morphological closing (buffer out/in by ``br_g``), clipped to
        the BAND annulus (never past the OD, well clear of the graphic cut
        edges), dilated ``br_ovl`` so it genuinely overlaps BOTH pads, and
        unioned into the FIRST pad's polygon. Electrically the pair is one node
        (series link, pre-wired in the schematic symbol); ``net_tie_pad_groups``
        marks the copper join as intentional.
        """
        fills = {}
        for (ka, la), (kb, lb) in pairs:
            Pa, Pb = pads[(ka, la)], pads[(kb, lb)]
            u = unary_union([Pa, Pb])
            gap = (
                u.buffer(self.br_g, join_style=1)
                .buffer(-self.br_g, join_style=1)
                .difference(u)
                .intersection(self.BAND)
            )
            parts = [
                g
                for g in _poly_parts(gap)
                if g.distance(Pa) < 1e-9 and g.distance(Pb) < 1e-9
            ]
            if not parts:
                raise FootprintError(
                    f"bridge {ka}{la}<->{kb}{lb}: closing produced no corridor fill"
                )
            # Dilate the corridor by br_ovl so it genuinely OVERLAPS both pads
            # (the closing leaves the corridor edge-coincident with the pad
            # boundaries, sometimes with a hair-thin numeric gap). The BAND
            # clip bounds the dilation radially; laterally it lands inside the
            # pads, so no copper escapes the rim band.
            fill = (
                unary_union(parts)
                .buffer(self.br_ovl, join_style=1)
                .intersection(self.BAND)
            )
            bridging = [
                p for p in _poly_parts(fill) if p.intersects(Pa) and p.intersects(Pb)
            ]
            if not bridging:
                raise FootprintError(
                    f"bridge {ka}{la}<->{kb}{lb}: fill does not reach both pads"
                )
            fill = unary_union(bridging)
            pads[(ka, la)] = unary_union([Pa, fill])
            fills[(ka, la), (kb, lb)] = fill
        return fills

    # -- emission ---------------------------------------------------------------
    def emit(self, angles, name, flips=None, bridges=(), clip_pads=frozenset()):
        """Emit the footprint text for coils at ``angles``; returns (text, pads)."""
        flips = flips if flips is not None else [False] * len(angles)
        geoms = [
            self.coil_at(ang, flips[k], f"{k}A" in clip_pads, f"{k}B" in clip_pads)
            for k, ang in enumerate(angles)
        ]
        pads = {(k, "A"): g[3] for k, g in enumerate(geoms)} | {
            (k, "B"): g[4] for k, g in enumerate(geoms)
        }
        if bridges:
            self.bridge_pads(pads, bridges)
        L = [
            f'(footprint "{name}"',
            f"  (version {_KICAD_VERSION})",
            f'  (generator "{_GENERATOR}")',
            '  (layer "F.Cu")',
            "  (attr through_hole)",
        ]
        if bridges:
            L.append(
                "  (net_tie_pad_groups "
                + " ".join(f'"{ka}{la},{kb}{lb}"' for (ka, la), (kb, lb) in bridges)
                + ")"
            )
        for k, ang in enumerate(angles):
            f, b, _via, _pA, _pB = geoms[k]
            padA, padB = pads[(k, "A")], pads[(k, "B")]
            L += _fp_poly(f, "F.Cu")
            L += _fp_poly(b, "B.Cu")
            # via stitch cluster: the base-frame via grid rotated onto this coil
            # (mirror-invariant: the grid is symmetric about the centre-line)
            ca, sa = np.cos(ang), np.sin(ang)
            for vx, vy in self.via_xy:
                x, y = ca * vx - sa * vy, sa * vx + ca * vy
                L.append(
                    f'  (pad "{k}S" thru_hole circle (at {x*1e3:.3f} {-y*1e3:.3f})'
                    f" (size {VIA_PAD*1e3:.2f} {VIA_PAD*1e3:.2f})"
                    f' (drill {VIA_DRILL*1e3:.2f}) (layers "*.Cu"))'
                )
            # connection pads: outer fraction of the terminal radial, net-bearing
            L += _fp_pad_custom(padA, f"{k}A", "F.Cu")
            L += _fp_pad_custom(padB, f"{k}B", "B.Cu")
        L.append(")")
        return "\n".join(L) + "\n", pads


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def build_footprint(
    design: MotorDesign,
    out_path: str,
    *,
    single_tooth: bool = False,
    name: str | None = None,
    flip: bool = True,
    fillets: bool = False,
    resolution_m: float = 2.0e-4,
    disk_radius_m: float | None = None,
    wshrink_m: float = 0.02e-3,
    simplify_tol_m: float = 1.0e-5,
    pad_frac: float = 0.0625,
    pad_ext_m: float = 1.5e-3,
    term_w_m: float = 2.0e-3,
    conn_w_m: float = 2.0e-3,
    conn_top_inset_m: float = 0.30e-3,
) -> FootprintReport:
    """Build the two-sided filled-copper stator footprint and write ``out_path``.

    ``design`` supplies the geometry (annulus, trace width/space at r_inner,
    ``n_slots``, star-of-slots layout); the artwork is the tapered-wedge
    winding (a non-tapered design is coerced, see module docstring). With
    ``single_tooth=True`` only the base coil (tooth 0) is emitted.

    ``disk_radius_m`` is the board outline radius all copper is clipped to
    (default ``r_outer_m + 0.5 mm``). ``flip=False`` places every coil unflipped
    (plain ring, no rim bridges -- for inspection only; the flipped layout is
    the manufacturable one). Remaining knobs are the verified shape recipe's
    tuning constants and rarely need changing.

    The emitted text is re-parsed and clearance/stitch/bridge-verified BEFORE
    the file is written; on FAIL a :class:`FootprintError` is raised and
    nothing is written. Returns a :class:`FootprintReport`.
    """
    art = _StatorArtwork(
        design,
        resolution_m=resolution_m,
        disk_radius_m=disk_radius_m,
        wshrink_m=wshrink_m,
        simplify_tol_m=simplify_tol_m,
        pad_frac=pad_frac,
        pad_ext_m=pad_ext_m,
        term_w_m=term_w_m,
        conn_w_m=conn_w_m,
        conn_top_inset_m=conn_top_inset_m,
        fillets=fillets,
    )
    D = art.D
    n_slots = int(D.n_slots)
    sector = art.sector
    ts_mm = art.ts * 1e3
    need_mm = ts_mm - _CLR_TOL_MM

    _layout, flips_all, clip_pads, bridges = stator_plan(n_slots, int(D.n_phases))
    if single_tooth:
        angles = [0.0]
        flips = [False]                 # tooth 0 is '+' in every tabulated layout
        active_bridges: tuple = ()
    else:
        angles = [k * sector for k in range(n_slots)]
        flips = flips_all if flip else [False] * n_slots
        # adjacency (flush rim pads) only holds in the flipped layout
        active_bridges = tuple(bridges) if flip else ()

    if name is None:
        base = out_path.rsplit("/", 1)[-1]
        name = base[: -len(".kicad_mod")] if base.endswith(".kicad_mod") else base

    text, _pads = art.emit(
        angles, name, flips=flips, bridges=active_bridges, clip_pads=clip_pads
    )

    checks: dict[str, bool] = {}
    notes = list(art.notes)

    # ---- geometric (pre-parse) gates ----
    checks["base_clearance"] = art.base_clr_mm >= need_mm
    checks["stitch_track_vs_left_radial"] = art.pad_left_clr_mm >= need_mm
    checks["stitch_track_taps_radial"] = art.conn_taps_radial
    checks["vias_clear_foreign_turns"] = art.via_foreign_clr_mm >= need_mm
    checks["vias_clear_left_radial"] = art.via_left_clr_mm >= need_mm
    checks["vias_inside_track"] = art.vias_in_track == len(art.via_xy)
    checks["parens_balanced"] = text.count("(") == text.count(")")

    # ---- VERIFY the EMITTED output (re-parse, don't trust the writer) ----
    if single_tooth:
        # (1) holes must be EXCLUDED from the reconstructed copper
        lay = _parse_fp(text)
        fcu = unary_union(lay["F.Cu"])
        bcu = unary_union(lay["B.Cu"])
        checks["holes_noncopper"] = all(
            not fcu.contains(h) for h in art.orig_holes
        )
        vs = _parse_vias(text)
        checks["stitch_on_both_layers"] = all(
            fcu.contains(Point(x, y)) and bcu.contains(Point(x, y)) for x, y in vs
        )
        worst = padworst = None
    else:
        # single-coil hole/stitch checks on a one-tooth emission of the same base
        t1, _ = art.emit([0.0], name + "_chk", clip_pads=clip_pads)
        lay1 = _parse_fp(t1)
        fcu = unary_union(lay1["F.Cu"])
        bcu = unary_union(lay1["B.Cu"])
        checks["holes_noncopper"] = all(
            not fcu.contains(h) for h in art.orig_holes
        )
        vs = _parse_vias(t1)
        checks["stitch_on_both_layers"] = all(
            fcu.contains(Point(x, y)) and bcu.contains(Point(x, y)) for x, y in vs
        )

        # (2)+(3) inter-coil / pad clearances on the per-coil shapely polys (a
        # coil may be several fp_poly pieces after the disk clip; comparing
        # parsed polys blindly would flag a coil against its OWN pieces).
        geoms = [
            art.coil_at(a, fl, f"{k}A" in clip_pads, f"{k}B" in clip_pads)
            for k, (a, fl) in enumerate(zip(angles, flips))
        ]
        pads = {(k, "A"): g[3] for k, g in enumerate(geoms)} | {
            (k, "B"): g[4] for k, g in enumerate(geoms)
        }
        if active_bridges:
            art.bridge_pads(pads, active_bridges)
        co = [
            [g[0], g[1], g[2], pads[(k, "A")], pads[(k, "B")]]
            for k, g in enumerate(geoms)
        ]
        worst = 9.0
        for i in range(len(co)):
            for j in range(i + 1, len(co)):
                worst = min(
                    worst,
                    co[i][0].distance(co[j][0]) * 1e3,   # front graphic i vs j
                    co[i][1].distance(co[j][1]) * 1e3,   # back graphic i vs j
                )
        checks["inter_coil_clearance"] = worst >= need_mm

        # pads vs foreign copper; bridged pairs are the SAME net and now
        # intentionally touch -> exempt exactly those pad-pad checks.
        br_skip = {frozenset(pr) for pr in active_bridges}
        padworst = 9.0
        for i in range(len(co)):
            for j in range(len(co)):
                if i == j:
                    continue
                padworst = min(
                    padworst,
                    co[i][3].distance(co[j][0]) * 1e3,   # A pad vs other front graphic
                    co[i][4].distance(co[j][1]) * 1e3,   # B pad vs other back graphic
                )
                for li, ax in (("A", 3), ("B", 4)):
                    if frozenset({(i, li), (j, li)}) in br_skip:
                        continue                          # intentional same-net join
                    padworst = min(
                        padworst, co[i][ax].distance(co[j][ax]) * 1e3
                    )
        checks["pad_foreign_clearance"] = padworst >= need_mm

        # (4) bridges: emitted pad pairs must genuinely OVERLAP (post-rounding),
        # every bridged pad stays inside the board disk, and each bridged pad is
        # still ONE polygon (_fp_pad_custom keeps only the largest piece -- a
        # detached bridge would be silently dropped).
        if active_bridges:
            EP = _parse_pads(text)
            brnames = {f"{k}{l}" for pr in active_bridges for k, l in pr}
            rmax = max(
                max(np.hypot(*np.array(EP[n].exterior.coords).T)) for n in brnames
            )
            one_piece = all(
                not isinstance(pads[pr[0]], MultiPolygon) for pr in active_bridges
            )
            ovl = [
                EP[f"{ka}{la}"].intersection(EP[f"{kb}{lb}"]).area * 1e6  # mm^2
                for (ka, la), (kb, lb) in active_bridges
            ]
            checks["bridges_overlap"] = min(ovl) > 1e-3
            checks["bridged_pads_in_disk"] = bool(rmax <= art.disk_r + 1e-5)
            checks["bridged_pads_one_piece"] = one_piece
            notes.append(
                f"bridges: {len(ovl)} pairs, min emitted overlap "
                f"{min(ovl):.3f} mm^2, pad max radius {rmax*1e3:.3f} mm"
            )

    passed = all(checks.values())
    gated = [art.base_clr_mm, art.pad_left_clr_mm, art.via_foreign_clr_mm,
             art.via_left_clr_mm]
    if worst is not None:
        gated += [worst, padworst]
    report = FootprintReport(
        path=out_path,
        name=name,
        n_coils=len(angles),
        turns_per_coil=art.N,
        passed=passed,
        worst_clearance_mm=min(gated),
        clearance_needed_mm=ts_mm,
        base_clearance_mm=art.base_clr_mm,
        pad_left_clearance_mm=art.pad_left_clr_mm,
        inter_coil_clearance_mm=worst,
        pad_foreign_clearance_mm=padworst,
        via_foreign_clearance_mm=art.via_foreign_clr_mm,
        n_vias_per_coil=len(art.via_xy),
        conn_track_w_mm=art.conn_w * 1e3,
        n_bridges=len(active_bridges),
        n_vertices=text.count("(xy"),
        pad_names=sorted(
            {f"{k}{l}" for k in range(len(angles)) for l in "AB"}
        ),
        checks=checks,
        notes=notes,
    )
    if not passed:
        failed = [k for k, v in checks.items() if not v]
        raise FootprintError(
            f"footprint verification FAILED ({', '.join(failed)}); "
            f"worst clearance {report.worst_clearance_mm:.3f} mm "
            f"(need >= {ts_mm:.3f} mm); nothing written"
        )
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)
    return report

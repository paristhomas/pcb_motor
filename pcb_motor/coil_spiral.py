"""Fabrication geometry: a continuous, drivable spiral for each concentrated coil.

The physics builder (:func:`pcb_motor.coils._build_concentrated`) lays each turn as
an independent *closed* rectangular loop -- correct for the Biot-Savart torque
integral (which never needs the segments to be electrically connected) but wrong
as PCB artwork: nested closed rings have no terminals and no path for current to
flow turn-to-turn.

This module re-stitches the coil into ONE continuous trace per coil. The turns
nest concentrically -- every edge (both arcs *and* both radials) steps inward by
one trace pitch ``(trace_width + trace_space)`` per turn -- so nothing is
coincident and the trace can't short to itself. (Full-length radials would force
every outer arc onto ``r_out`` and short them together, so the radials necessarily
shorten as the spiral winds in; the outermost turn keeps the full radial length.)

Each turn draws: left radial (out->in), inner arc, right radial (in->out), then an
**outer-arc crossover** that spirals back from the right edge to the *next turn's
outer-left corner*, dropping one pitch in radius. One crossover per turn makes the
whole coil a continuous spiral. It starts at the outermost-left corner (terminal A,
outer rim) and ends at the innermost turn's outer-right corner, so **terminal B is
on the outer side too** (stepped in, near the centre angle, inside the coil). No
pads, no vias: the two open ends are bare trace endpoints, stitched in by hand.
"""

from __future__ import annotations

import numpy as np

from .design import MotorDesign
from .coils import _arc_polyline, _line, _polar


def _geom(design: MotorDesign):
    """Annulus + angular half-width + trace pitch (shared by the turn builder)."""
    r_in = float(design.r_inner_m)
    r_out = float(design.r_outer_m)
    if r_out <= r_in:
        raise ValueError("r_outer_m must exceed r_inner_m")
    n_slots = int(design.n_slots)
    tw = float(design.trace_width_m)
    ts = float(design.trace_space_m)
    pitch = tw + ts
    if pitch <= 0:
        raise ValueError("trace_width_m + trace_space_m must be positive")

    r_mean = 0.5 * (r_in + r_out)
    sector = 2.0 * np.pi / n_slots
    half = 0.5 * sector - ts / (2.0 * r_mean)
    if half <= 0:
        half = 0.4 * sector
    return r_in, r_out, sector, half, tw, pitch


def _corners(center, half, r_in, r_out, tw, pitch, t):
    """Geometry of nested turn ``t``: radii and the four inset edge angles.

    Both the radii (``ri``/``ro``) and the angular insets step inward by one pitch
    per turn, so every edge stays pitch-spaced from the neighbouring turn. The
    angular inset is per-radius (``/ri`` inner, ``/ro`` outer) so the radials slant
    slightly to hold the trace pitch at every radius.
    """
    off = t * pitch
    ri = r_in + off
    ro = r_out - off
    dphi_i = (off + tw / 2.0) / ri
    dphi_o = (off + tw / 2.0) / ro
    return {
        "ri": ri, "ro": ro,
        "Li": center - half + dphi_i, "Lo": center - half + dphi_o,
        "Ri": center + half - dphi_i, "Ro": center + half - dphi_o,
        "ok": ro > ri and (half - dphi_i) > 0 and (half - dphi_o) > 0,
    }


# --------------------------------------------------------------------------- #
# Tapered (wedge) variant: constant *angular* pitch, width grows with radius
# --------------------------------------------------------------------------- #
def _geom_tapered(design: MotorDesign):
    """Annulus + angular trace pitch ``delta`` for the tapered wedge winding.

    ``trace_width_m`` is the conductor width at ``r_inner_m``; the angular
    centreline pitch is then ``delta = (trace_width + trace_space) / r_inner``
    and the width at radius r is ``w(r) = delta*r - trace_space`` -- adjacent
    traces (and adjacent coils) keep exactly ``trace_space`` of clearance at
    every radius, so the copper fill is maximal for the chosen spacing.
    """
    r_in = float(design.r_inner_m)
    r_out = float(design.r_outer_m)
    if r_out <= r_in:
        raise ValueError("r_outer_m must exceed r_inner_m")
    if r_in <= 0:
        raise ValueError("tapered traces need r_inner_m > 0")
    tw = float(design.trace_width_m)
    ts = float(design.trace_space_m)
    if tw + ts <= 0:
        raise ValueError("trace_width_m + trace_space_m must be positive")
    delta = (tw + ts) / r_in
    sector = 2.0 * np.pi / int(design.n_slots)
    return r_in, r_out, sector, delta, tw, ts


def trace_width_at(design: MotorDesign, r) -> np.ndarray:
    """Conductor width [m] at radius ``r`` (scalar or array).

    Tapered: ``w(r) = delta*r - trace_space`` (== ``trace_width_m`` at
    ``r_inner_m``). Constant-width designs just return ``trace_width_m``.
    """
    r = np.asarray(r, dtype=float)
    if not getattr(design, "tapered_traces", False):
        return np.full(r.shape, float(design.trace_width_m))
    r_in, r_out, sector, delta, tw, ts = _geom_tapered(design)
    return np.maximum(delta * r - ts, tw)


def _corners_tapered(design: MotorDesign, center: float, t: int):
    """Geometry of nested tapered turn ``t``: pure-radial ray angles + radii.

    With ``w(r) = delta*r - ts`` the half-width-plus-clearance inset measured in
    angle is the constant ``delta/2`` at every radius, so each conductor
    centreline is a *pure radial ray*: turn ``t``'s sides sit ``(t + 1/2)*delta``
    in from the sector edges. Radially, successive inner arcs / outer crossovers
    step by the local ``w/2 + ts + w/2``, which gives the closed-form geometric
    nesting ``r * (1 +/- delta/2) / (1 -/+ delta/2)``.
    """
    r_in, r_out, sector, delta, tw, ts = _geom_tapered(design)
    grow = (1.0 + delta / 2.0) / (1.0 - delta / 2.0) if delta < 2.0 else np.inf
    ri = r_in * grow ** t
    ro = r_out / grow ** t
    inset = (t + 0.5) * delta
    # Feasibility: (a) the turn's own left/right rays must stay a full angular
    # pitch apart (edge gap == ts; centreline-only checks let copper overlap at
    # the sector centre), and (b) the radial span must clear the turn's own
    # inner arc and outer crossover half-widths plus spacing.
    angular_ok = (t + 1.0) * delta <= sector / 2.0
    radial_ok = (ro - ri) >= delta * (ro + ri) / 2.0
    return {
        "ri": ri, "ro": ro,
        "L": center - sector / 2.0 + inset,
        "R": center + sector / 2.0 - inset,
        "ok": radial_ok and angular_ok,
    }


def _count_turns(design: MotorDesign) -> int:
    """Number of nested turns that fit in one coil sector (0 if none fit)."""
    if getattr(design, "tapered_traces", False):
        t = 0
        while _corners_tapered(design, 0.0, t)["ok"]:
            t += 1
        return t
    r_in, r_out, sector, half, tw, pitch = _geom(design)
    t = 0
    while _corners(0.0, half, r_in, r_out, tw, pitch, t)["ok"]:
        t += 1
    return t


def _spiral_arc(r0, a0, r1, a1, z, n):
    """Arc that sweeps angle ``a0->a1`` while ramping radius ``r0->r1``."""
    angs = np.linspace(a0, a1, n + 1)
    rs = np.linspace(r0, r1, n + 1)
    return np.column_stack([rs * np.cos(angs), rs * np.sin(angs), np.full(angs.shape, z)])


def coil_spiral_polyline(design: MotorDesign, center: float, z: float) -> np.ndarray:
    """One continuous spiral trace for the coil on the tooth sector at ``center``.

    Returns an ``(K, 3)`` array of points [m]. The first and last points are the
    coil's two terminals: A at the outermost-left corner (outer rim), B at the
    innermost turn's outer-right corner (outer side, near the centre angle).
    """
    if getattr(design, "tapered_traces", False):
        return _coil_spiral_polyline_tapered(design, center, z)

    r_in, r_out, sector, half, tw, pitch = _geom(design)
    res = float(design.coil_resolution_m)
    n_rad = max(1, int(np.ceil((r_out - r_in) / res)))
    turns = _count_turns(design)
    if turns == 0:
        return np.zeros((0, 3))

    def arc_n(a_span, r):
        return max(1, int(np.ceil(abs(a_span) * r / res)))

    segs: list[np.ndarray] = []
    for t in range(turns):
        c = _corners(center, half, r_in, r_out, tw, pitch, t)
        lrad = _line(_polar(c["ro"], c["Lo"], z), _polar(c["ri"], c["Li"], z), n_rad)  # out->in
        inner = _arc_polyline(c["ri"], c["Li"], c["Ri"], z, arc_n(c["Ri"] - c["Li"], c["ri"]))
        rrad = _line(_polar(c["ri"], c["Ri"], z), _polar(c["ro"], c["Ro"], z), n_rad)  # in->out
        parts = [lrad, inner[1:], rrad[1:]]

        if t + 1 < turns:
            # Outer-arc crossover: spiral from this turn's outer-right corner back
            # to the next turn's outer-left corner, dropping one pitch in radius.
            nxt = _corners(center, half, r_in, r_out, tw, pitch, t + 1)
            cross = _spiral_arc(c["ro"], c["Ro"], nxt["ro"], nxt["Lo"], z,
                                arc_n(c["Ro"] - nxt["Lo"], c["ro"]))
            parts.append(cross[1:])
        # else: last turn ends at this turn's outer-right corner = terminal B.

        seg = np.vstack(parts)
        segs.append(seg if not segs else seg[1:])  # drop the shared join point

    return np.vstack(segs)


def _coil_spiral_polyline_tapered(
    design: MotorDesign, center: float, z: float
) -> np.ndarray:
    """Tapered-wedge variant of :func:`coil_spiral_polyline`.

    Same continuous-spiral turn structure (left radial out->in, inner arc, right
    radial in->out, outer-arc crossover), but the radials are pure radial rays at
    constant angular pitch and the nesting radii follow the tapered recurrence
    (see :func:`_corners_tapered`). Terminals match the constant-width spiral:
    A at the outermost-left outer corner, B at the innermost turn's outer-right.
    """
    res = float(design.coil_resolution_m)
    turns = _count_turns(design)
    if turns == 0:
        return np.zeros((0, 3))

    def arc_n(a_span, r):
        return max(1, int(np.ceil(abs(a_span) * r / res)))

    segs: list[np.ndarray] = []
    for t in range(turns):
        c = _corners_tapered(design, center, t)
        n_rad = max(1, int(np.ceil((c["ro"] - c["ri"]) / res)))
        lrad = _line(_polar(c["ro"], c["L"], z), _polar(c["ri"], c["L"], z), n_rad)
        inner = _arc_polyline(c["ri"], c["L"], c["R"], z, arc_n(c["R"] - c["L"], c["ri"]))
        rrad = _line(_polar(c["ri"], c["R"], z), _polar(c["ro"], c["R"], z), n_rad)
        parts = [lrad, inner[1:], rrad[1:]]

        if t + 1 < turns:
            # Crossover: hold this turn's outer radius for most of the sweep,
            # then drop one nesting step over the final angular pitch ``delta``.
            # A full-sweep linear ramp would descend past the next turns' radial
            # tips near the start of the sweep (terminal B sits right there);
            # ramping only in the last ``delta`` keeps every edge gap >= space:
            # successive crossovers' ramp bands tile [L(t+1), L(t+2)] without
            # overlap, and the flat part clears the radial tips by one step.
            nxt = _corners_tapered(design, center, t + 1)
            _, _, _, delta, _, _ = _geom_tapered(design)
            ramp_start = nxt["L"] + delta
            flat = _arc_polyline(c["ro"], c["R"], ramp_start, z,
                                 arc_n(c["R"] - ramp_start, c["ro"]))
            ramp_len = float(np.hypot(delta * c["ro"], c["ro"] - nxt["ro"]))
            ramp = _spiral_arc(c["ro"], ramp_start, nxt["ro"], nxt["L"], z,
                               max(1, int(np.ceil(ramp_len / res))))
            parts.append(flat[1:])
            parts.append(ramp[1:])

        seg = np.vstack(parts)
        segs.append(seg if not segs else seg[1:])

    return np.vstack(segs)


def export_spiral_polylines(design: MotorDesign, *, single_coil: bool = False,
                            layer: int = 0) -> list[np.ndarray]:
    """Continuous spiral traces for the front copper layer.

    One polyline per tooth coil (or just the first sector when ``single_coil``).
    ``layer`` selects which copper plane's z is used (0 = nearest the rotor).
    """
    n_slots = int(design.n_slots)
    sector = 2.0 * np.pi / n_slots
    z = design.layer_z_m(layer)
    sectors = [0] if single_coil else range(n_slots)
    out = []
    for k in sectors:
        poly = coil_spiral_polyline(design, k * sector, z)
        if poly.shape[0] >= 2:
            out.append(poly)
    return out

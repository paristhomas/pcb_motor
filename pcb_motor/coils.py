"""Stator winding geometry generation.

Generates a :class:`~pcb_motor.design.CoilGeometry` for a PCB-motor stator from a
:class:`~pcb_motor.design.MotorDesign`, plus the per-phase resistance.

Three topologies are supported:

``concentrated`` (PRIMARY -- the default and the one we optimise)
    Fractional-slot concentrated winding: ``n_slots`` discrete spiral coils, one
    per tooth sector, phased and signed by the star-of-slots layout
    (:func:`_coil_layout`). Each coil is the same continuous, drivable spiral
    that gets exported to KiCad, so the simulated current path is exactly the
    manufactured copper.

``radial_spoke``
    The torque-producing copper runs *radially* (along the spoke direction) so
    that, crossing the axial air-gap field B_z, the Lorentz force I*(dL x B) is
    tangential and produces axial torque. Each turn is a "go" radial conductor
    from ``r_inner`` to ``r_outer``, an outer end-turn arc, a "return" radial
    conductor back to ``r_inner``, and an inner end-turn arc -- the arcs are the
    non-torque END TURNS (``is_radial=False``).

``spiral`` (SECONDARY)
    A reasonable Archimedean spiral per phase: copper winds from ``r_inner`` to
    ``r_outer`` while sweeping angle. Mostly tangential, so it is a poor torque
    producer; it exists so the topology switch and downstream code have a second
    valid geometry to exercise. Every segment is treated as non-radial since a
    spiral has no clean radial/end-turn split.

Turn-fitting assumption (radial_spoke)
--------------------------------------
At the mean radius ``r_mean = (r_inner + r_outer) / 2`` the available
circumference is ``2*pi*r_mean``. Radial conductors are laid down at a constant
angular pitch set by the copper pitch ``trace_width + trace_space``; the number
of radial conductors that physically fit around one full turn of the annulus is

    n_cond_per_layer = floor(2*pi*r_mean / (trace_width + trace_space))

Those conductors are paired into go/return turns (``n_cond_per_layer // 2`` pairs
per layer) and dealt out round-robin to the ``n_phases`` phases, so each phase
gets ``(n_cond_per_layer // 2) // n_phases`` turns per copper layer. Stacking
``copper_layers`` identical layers gives the reported

    n_turns = turns_per_layer_per_phase * copper_layers

This is a packing estimate at the mean radius (real boards taper the pitch with
radius); it is physically reasonable to ~the fill-factor level, not exact.

Layers / stators
----------------
Copper layers span the board symmetrically about its centre
``design.rotor().stator_z_m()`` (see ``MotorDesign.layer_z_m``): a 2-layer
board puts copper on the two faces at centre +/- t/2. For a dual-stator machine
(``n_stators == 2``) we model ONE stator's winding here and leave the second
stator's identical contribution to be accounted for downstream (the torque layer
can double it). This keeps ``build_coil`` focused on a single emitter of
conductor segments and keeps the geometry unambiguous.
"""

from __future__ import annotations

import numpy as np

from .constants import ALPHA_CU, COPPER_THICKNESS, RHO_CU_20
from .design import CoilGeometry, MotorDesign


def _copper_thickness(weight_oz: float) -> float:
    """Copper foil thickness [m] for a weight in oz/ft^2.

    ``COPPER_THICKNESS`` is keyed by floats {0.5, 1.0, 2.0}. Exact keys are used
    directly; anything else snaps to the nearest tabulated weight (PCB copper is
    only ordered in these discrete weights anyway).
    """
    if weight_oz in COPPER_THICKNESS:
        return COPPER_THICKNESS[weight_oz]
    keys = sorted(COPPER_THICKNESS)
    nearest = min(keys, key=lambda k: abs(k - weight_oz))
    if abs(nearest - weight_oz) > 1e-9:
        # Not an exact key; snap to nearest tabulated weight and carry on.
        return COPPER_THICKNESS[nearest]
    return COPPER_THICKNESS[nearest]


def _segments_from_polyline(poly: np.ndarray):
    """Split a polyline ``(K,3)`` into per-segment midpoints and dvecs."""
    poly = np.asarray(poly, dtype=float)
    starts = poly[:-1]
    ends = poly[1:]
    mids = 0.5 * (starts + ends)
    dvec = ends - starts
    return mids, dvec


def _arc_polyline(r: float, a0: float, a1: float, z: float, n: int) -> np.ndarray:
    """Arc at radius ``r`` from angle ``a0`` to ``a1`` at height ``z``."""
    angs = np.linspace(a0, a1, n + 1)
    return np.column_stack([r * np.cos(angs), r * np.sin(angs), np.full(angs.shape, z)])


def _radial_polyline(a: float, r0: float, r1: float, z: float, n: int) -> np.ndarray:
    """Radial conductor at angle ``a`` from radius ``r0`` to ``r1`` at ``z``."""
    rs = np.linspace(r0, r1, n + 1)
    return np.column_stack([rs * np.cos(a), rs * np.sin(a), np.full(rs.shape, z)])


def _round_corners(
    poly: np.ndarray,
    radius: float,
    *,
    angle_threshold_deg: float = 20.0,
    n_seg: int = 6,
) -> np.ndarray:
    """Round the sharp corners of a planar polyline with small fillet arcs.

    Only vertices whose *turn angle* (deviation from straight) exceeds
    ``angle_threshold_deg`` are filleted, so the many shallow vertices that
    discretise a smooth arc are left untouched while the genuine ~90 degree
    corners (where a radial side meets an end-turn arc) get rounded. Each
    qualifying corner is replaced by a quadratic Bezier through the original
    vertex, trimmed back along each adjacent segment by up to ``radius`` (capped
    at half the segment length so neighbouring fillets never overlap). The
    polyline is assumed planar; ``z`` is taken from the input and held constant.

    This only reshapes geometry for plotting / fabrication export -- it is not
    used by the torque integrator, so the small length change is irrelevant to
    the (already ~+/-30%) physics.
    """
    poly = np.asarray(poly, dtype=float)
    if poly.shape[0] < 3 or radius <= 0:
        return poly

    z = float(poly[0, 2])
    pts = poly[:, :2]

    # Drop consecutive duplicate points (zero-length segments break the maths).
    keep = np.ones(pts.shape[0], dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(pts, axis=0)) > 1e-15, axis=1)
    pts = pts[keep]
    if pts.shape[0] < 3:
        return poly

    closed = bool(np.allclose(pts[0], pts[-1]))
    ring = pts[:-1] if closed else pts          # unique vertices
    n = ring.shape[0]
    cos_thresh = np.cos(np.deg2rad(angle_threshold_deg))

    out: list[np.ndarray] = []
    for i in range(n):
        if not closed and (i == 0 or i == n - 1):
            out.append(ring[i])                 # keep open-polyline endpoints
            continue
        v = ring[i]
        p = ring[(i - 1) % n]
        q = ring[(i + 1) % n]
        din, dout = v - p, q - v
        lin, lout = np.linalg.norm(din), np.linalg.norm(dout)
        if lin <= 0 or lout <= 0:
            out.append(v)
            continue
        uin, uout = din / lin, dout / lout
        # Turn angle: angle between incoming and outgoing direction (0 = straight).
        if float(np.dot(uin, uout)) >= cos_thresh:
            out.append(v)                       # too shallow to bother rounding
            continue
        t = min(radius, 0.5 * lin, 0.5 * lout)
        a = v - t * uin                         # back along the incoming segment
        b = v + t * uout                        # forward along the outgoing segment
        s = np.linspace(0.0, 1.0, n_seg + 1)[:, None]
        bez = (1 - s) ** 2 * a + 2 * (1 - s) * s * v + s ** 2 * b
        out.extend(bez)

    rounded = np.array(out, dtype=float)
    if closed:
        rounded = np.vstack([rounded, rounded[0]])
    return np.column_stack([rounded[:, 0], rounded[:, 1], np.full(rounded.shape[0], z)])


def _build_radial_spoke(design: MotorDesign) -> CoilGeometry:
    r_in = float(design.r_inner_m)
    r_out = float(design.r_outer_m)
    if r_out <= r_in:
        raise ValueError("r_outer_m must exceed r_inner_m")

    n_phases = int(design.n_phases)
    n_layers = int(design.copper_layers)
    pitch = float(design.trace_width_m) + float(design.trace_space_m)
    if pitch <= 0:
        raise ValueError("trace_width_m + trace_space_m must be positive")

    r_mean = 0.5 * (r_in + r_out)
    circumference = 2.0 * np.pi * r_mean

    # How many radial conductors fit around one full layer at the mean radius.
    n_cond_per_layer = int(np.floor(circumference / pitch))
    n_pairs_per_layer = n_cond_per_layer // 2
    turns_per_layer_per_phase = n_pairs_per_layer // n_phases
    if turns_per_layer_per_phase < 1:
        turns_per_layer_per_phase = 1  # degenerate tiny annulus: at least one turn

    n_turns = turns_per_layer_per_phase * n_layers

    # Total turns we actually lay down per layer, across all phases.
    turns_per_layer = turns_per_layer_per_phase * n_phases

    # Discretisation: aim for ~coil_resolution_m segment length on the radials.
    res = float(design.coil_resolution_m)
    radial_len = r_out - r_in
    n_rad_seg = max(1, int(np.ceil(radial_len / res)))

    # copper layers span the board symmetrically about its centre

    # Angular slot for each go/return pair. Each pair occupies 2 conductor slots
    # of width = pitch; place go and return one pitch apart, pairs spread evenly.
    d_ang_cond = pitch / r_mean  # angular spacing of one conductor at r_mean
    # Total turns laid per layer fill the circle; space pairs over 2*pi.
    pair_ang_step = 2.0 * np.pi / max(1, turns_per_layer)

    # Arc end-turn discretisation: roughly resolution along the arc at the radius.
    def n_arc_for(r: float) -> int:
        arc_len = abs(d_ang_cond) * r
        return max(1, int(np.ceil(arc_len / res)))

    polylines: list[np.ndarray] = []
    mids_list: list[np.ndarray] = []
    dvec_list: list[np.ndarray] = []
    phase_list: list[np.ndarray] = []
    dir_list: list[np.ndarray] = []
    rad_list: list[np.ndarray] = []

    length_per_phase = 0.0

    for layer in range(n_layers):
        z = design.layer_z_m(layer)
        for t in range(turns_per_layer):
            phase = t % n_phases
            base_ang = t * pair_ang_step
            a_go = base_ang
            a_ret = base_ang + d_ang_cond

            # Current sense: alternate direction every other turn within a phase
            # so that adjacent go/return conductors of the same phase oppose,
            # giving a coherent winding. Sign here is a placeholder convention;
            # the torque layer picks the optimal commutation.
            sense = 1.0 if ((t // n_phases) % 2 == 0) else -1.0

            # --- go radial: r_in -> r_out at a_go ---
            go = _radial_polyline(a_go, r_in, r_out, z, n_rad_seg)
            # --- outer end-turn arc: a_go -> a_ret at r_out ---
            n_arc_o = n_arc_for(r_out)
            arc_o = _arc_polyline(r_out, a_go, a_ret, z, n_arc_o)
            # --- return radial: r_out -> r_in at a_ret ---
            ret = _radial_polyline(a_ret, r_out, r_in, z, n_rad_seg)
            # --- inner end-turn arc: a_ret -> a_go (closing) at r_in ---
            n_arc_i = n_arc_for(r_in)
            arc_i = _arc_polyline(r_in, a_ret, a_go, z, n_arc_i)

            full = np.vstack([go, arc_o[1:], ret[1:], arc_i[1:]])
            polylines.append(full)

            for poly, is_rad in ((go, True), (arc_o, False), (ret, True), (arc_i, False)):
                m, d = _segments_from_polyline(poly)
                s = m.shape[0]
                mids_list.append(m)
                dvec_list.append(d * sense)  # orient dvec along current sense
                phase_list.append(np.full(s, phase, dtype=int))
                dir_list.append(np.full(s, sense, dtype=float))
                rad_list.append(np.full(s, is_rad, dtype=bool))

            # Per-phase conductor length: accumulate only for phase 0's turns,
            # then assume symmetry. Simpler: accumulate every turn and divide.
            turn_len = (
                float(np.linalg.norm(np.diff(go, axis=0), axis=1).sum())
                + float(np.linalg.norm(np.diff(arc_o, axis=0), axis=1).sum())
                + float(np.linalg.norm(np.diff(ret, axis=0), axis=1).sum())
                + float(np.linalg.norm(np.diff(arc_i, axis=0), axis=1).sum())
            )
            if phase == 0:
                length_per_phase += turn_len

    midpoints = np.vstack(mids_list)
    dvec = np.vstack(dvec_list)
    phase = np.concatenate(phase_list)
    direction = np.concatenate(dir_list)
    is_radial = np.concatenate(rad_list)

    cond_thickness = _copper_thickness(float(design.copper_weight_oz))
    conductor_area = float(design.trace_width_m) * cond_thickness

    return CoilGeometry(
        midpoints_m=midpoints,
        dvec_m=dvec,
        phase=phase,
        direction=direction,
        is_radial=is_radial,
        length_per_phase_m=length_per_phase,
        conductor_area_m2=conductor_area,
        n_turns=int(n_turns),
        n_layers=n_layers,
        polylines=polylines,
    )


def _build_spiral(design: MotorDesign) -> CoilGeometry:
    """Reasonable Archimedean-spiral winding (secondary topology).

    Each phase is one spiral from ``r_inner`` to ``r_outer`` sweeping through a
    number of revolutions set by how many radial pitches fit in the annulus.
    Phases are offset by ``2*pi/n_phases``. Layers are stacked as in the radial
    model. Segments are all flagged non-radial (a spiral has no clean radial /
    end-turn split), so ``end_turn_fraction`` -> ~1 for this topology.
    """
    r_in = float(design.r_inner_m)
    r_out = float(design.r_outer_m)
    if r_out <= r_in:
        raise ValueError("r_outer_m must exceed r_inner_m")

    n_phases = int(design.n_phases)
    n_layers = int(design.copper_layers)
    pitch = float(design.trace_width_m) + float(design.trace_space_m)
    if pitch <= 0:
        raise ValueError("trace_width_m + trace_space_m must be positive")

    # Number of radial turns (revolutions) the spiral makes across the annulus.
    n_rev = max(1, int(np.floor((r_out - r_in) / pitch)))
    n_turns = n_rev * n_layers

    res = float(design.coil_resolution_m)
    r_mean = 0.5 * (r_in + r_out)
    total_angle = 2.0 * np.pi * n_rev
    arc_len_est = total_angle * r_mean
    n_seg = max(8, int(np.ceil(arc_len_est / res)))

    # copper layers span the board symmetrically about its centre

    polylines: list[np.ndarray] = []
    mids_list: list[np.ndarray] = []
    dvec_list: list[np.ndarray] = []
    phase_list: list[np.ndarray] = []
    dir_list: list[np.ndarray] = []
    rad_list: list[np.ndarray] = []

    length_per_phase = 0.0

    for layer in range(n_layers):
        z = design.layer_z_m(layer)
        for p in range(n_phases):
            phi0 = p * 2.0 * np.pi / n_phases
            angs = np.linspace(0.0, total_angle, n_seg + 1)
            rs = r_in + (r_out - r_in) * (angs / total_angle)
            poly = np.column_stack(
                [rs * np.cos(angs + phi0), rs * np.sin(angs + phi0), np.full(angs.shape, z)]
            )
            polylines.append(poly)

            m, d = _segments_from_polyline(poly)
            s = m.shape[0]
            mids_list.append(m)
            dvec_list.append(d)
            phase_list.append(np.full(s, p, dtype=int))
            dir_list.append(np.full(s, 1.0, dtype=float))
            rad_list.append(np.zeros(s, dtype=bool))

            if p == 0:
                length_per_phase += float(np.linalg.norm(d, axis=1).sum())

    midpoints = np.vstack(mids_list)
    dvec = np.vstack(dvec_list)
    phase = np.concatenate(phase_list)
    direction = np.concatenate(dir_list)
    is_radial = np.concatenate(rad_list)

    cond_thickness = _copper_thickness(float(design.copper_weight_oz))
    conductor_area = float(design.trace_width_m) * cond_thickness

    return CoilGeometry(
        midpoints_m=midpoints,
        dvec_m=dvec,
        phase=phase,
        direction=direction,
        is_radial=is_radial,
        length_per_phase_m=length_per_phase,
        conductor_area_m2=conductor_area,
        n_turns=int(n_turns),
        n_layers=n_layers,
        polylines=polylines,
    )


# Fractional-slot concentrated-winding phase + polarity layouts, derived by the
# star-of-slots method. Each entry is (phase_index, current_sign) per tooth.
# 12N14P (double-layer, all teeth wound): phases come in same-phase adjacent
# pairs of opposite polarity -- order A,C,B -- which is the documented 12s14p
# winding (kw1 = 0.933). Map A=0, B=1, C=2.
_LAYOUTS: dict[int, list[tuple[int, int]]] = {
    12: [(0, +1), (0, -1), (2, -1), (2, +1), (1, +1), (1, -1),
         (0, -1), (0, +1), (2, +1), (2, -1), (1, -1), (1, +1)],
}
# 36N42P (double-layer, all teeth wound): the 6N7P base unit A+ A- C- C+ B+ B-
# tiled six times with the polarity alternating every unit -- which is exactly
# the 12N14P pattern tiled three times (tooth k and tooth k+12 sit at identical
# electrical angles: the slot angle is 21 * 2*pi/36 = 210 deg, and 12 * 210 deg
# is a whole number of electrical turns). kw1 = 0.933 for 42 poles, same
# winding-factor family as 12N14P. NOTE: the table is keyed by n_slots only, so
# this entry assumes the matching 42-pole rotor (a plain 6x tiling WITHOUT the
# polarity alternation puts same-phase coils in antiphase and kw collapses to 0).
_LAYOUTS[36] = _LAYOUTS[12] * 3
# 24N28P (double-layer, all teeth wound): the 12N14P pattern tiled twice, valid
# for the SAME reason as 36N42P -- tooth k and tooth k+12 sit at identical
# electrical angles (slot angle 14 * 2*pi/24 = 210 deg elec; 12 * 210 deg =
# 2520 deg = 7 full turns). kw1 = 0.933, the 12N14P family. Assumes the matching
# 28-pole rotor (14 pole-pairs).
_LAYOUTS[24] = _LAYOUTS[12] * 2


def _coil_layout(n_slots: int, n_phases: int) -> list[tuple[int, int]]:
    if n_slots in _LAYOUTS and n_phases == 3:
        return _LAYOUTS[n_slots]
    # Fallback: round-robin phases with alternating polarity.
    return [(k % n_phases, +1 if (k // n_phases) % 2 == 0 else -1)
            for k in range(n_slots)]


def _polar(r: float, a: float, z: float) -> np.ndarray:
    return np.array([r * np.cos(a), r * np.sin(a), z])


def _line(p0: np.ndarray, p1: np.ndarray, n: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n + 1)[:, None]
    return p0[None, :] + t * (p1 - p0)[None, :]


def _classify_radial(poly: np.ndarray) -> np.ndarray:
    """Per-segment ``is_radial`` mask for a coil polyline.

    A segment is a torque-producing *radial* conductor when its change in radius
    dominates its tangential travel (``|dr| >= r_mean * |dphi|``); otherwise it is
    an end-turn arc (inner arc or the outer-arc crossover that links turns).
    """
    p = np.asarray(poly, dtype=float)
    r = np.hypot(p[:, 0], p[:, 1])
    phi = np.unwrap(np.arctan2(p[:, 1], p[:, 0]))
    dr = np.abs(np.diff(r))
    r_mid = 0.5 * (r[:-1] + r[1:])
    d_tan = np.abs(r_mid * np.diff(phi))
    return dr >= d_tan


def _build_concentrated(design: MotorDesign) -> CoilGeometry:
    """Fractional-slot concentrated winding: ``n_slots`` discrete spiral coils.

    Each coil is the *same continuous, drivable spiral* that gets exported to
    KiCad (:func:`pcb_motor.coil_spiral.coil_spiral_polyline`) -- so the simulated
    current path is exactly the manufactured copper, not an idealised stack of
    closed loops. Successive turns nest inward by one trace pitch
    ``(trace_width + trace_space)`` on all four sides; the two near-radial sides
    are the torque-producing conductors (``is_radial=True``), while the inner arcs
    and the outer-arc crossovers that link turn-to-turn are end-turns
    (``is_radial=False``). Phase + winding-direction per tooth comes from the
    star-of-slots layout (``_coil_layout``), so the winding factor emerges
    naturally when torque is computed with proper commutation.
    """
    from .coil_spiral import coil_spiral_polyline, trace_width_at, _count_turns

    r_in = float(design.r_inner_m)
    r_out = float(design.r_outer_m)
    if r_out <= r_in:
        raise ValueError("r_outer_m must exceed r_inner_m")

    n_slots = int(design.n_slots)
    n_phases = int(design.n_phases)
    n_layers = int(design.copper_layers)
    tw = float(design.trace_width_m)
    ts = float(design.trace_space_m)
    pitch = tw + ts
    if pitch <= 0:
        raise ValueError("trace_width_m + trace_space_m must be positive")

    sector = 2.0 * np.pi / n_slots
    turns_per_coil = _count_turns(design)

    layout = _coil_layout(n_slots, n_phases)
    # copper layers span the board symmetrically about its centre

    tapered = bool(getattr(design, "tapered_traces", False))
    cond_thickness = _copper_thickness(float(design.copper_weight_oz))

    polylines: list[np.ndarray] = []
    mids_list, dvec_list, phase_list, dir_list, rad_list = [], [], [], [], []
    length_per_phase = 0.0
    length_over_area = 0.0     # sum(dl / A) for one phase [1/m] (tapered)
    copper_volume = 0.0        # all phases, one stator [m^3] (tapered)

    for layer in range(n_layers):
        z = design.layer_z_m(layer)
        for k in range(n_slots):
            center = k * sector
            phase, sign = layout[k]
            sense = float(sign)
            poly = coil_spiral_polyline(design, center, z)
            if poly.shape[0] < 2:
                continue
            polylines.append(poly)

            m, dv = _segments_from_polyline(poly)
            s = m.shape[0]
            mids_list.append(m)
            dvec_list.append(dv * sense)               # carry winding sense in dL
            phase_list.append(np.full(s, phase, dtype=int))
            dir_list.append(np.full(s, sense, dtype=float))
            rad_list.append(_classify_radial(poly))

            dl = np.linalg.norm(dv, axis=1)
            if tapered:
                w_seg = trace_width_at(design, np.hypot(m[:, 0], m[:, 1]))
                copper_volume += float((dl * w_seg).sum()) * cond_thickness
                if phase == 0:
                    length_over_area += float(
                        (dl / (w_seg * cond_thickness)).sum()
                    )
            if phase == 0:
                length_per_phase += float(dl.sum())

    conductor_area = tw * cond_thickness   # tapered: minimum section (at r_inner)
    coils_per_phase = sum(1 for ph, _ in layout if ph == 0)
    n_turns = turns_per_coil * coils_per_phase * n_layers

    return CoilGeometry(
        midpoints_m=np.vstack(mids_list),
        dvec_m=np.vstack(dvec_list),
        phase=np.concatenate(phase_list),
        direction=np.concatenate(dir_list),
        is_radial=np.concatenate(rad_list),
        length_over_area_per_phase=length_over_area if tapered else None,
        copper_volume_m3=copper_volume if tapered else None,
        length_per_phase_m=length_per_phase,
        conductor_area_m2=conductor_area,
        n_turns=int(n_turns),
        n_layers=n_layers,
        polylines=polylines,
    )


def winding_factor(design: MotorDesign, harmonic: int = 1) -> float:
    """Fundamental winding factor kw via the star-of-slots / EMF-phasor method.

    Each coil's flux-linkage phasor is ``sign * (e^{j h p phi_L} - e^{j h p phi_R})``
    for its two radial sides at mechanical angles ``center -/+ half``. Per phase,
    ``kw = |sum of coil phasors| / sum |coil phasors|``; we average over phases.
    For 12 slots / 14 poles this returns ~0.933, matching the literature; the
    36 slots / 42 poles layout (the same 6N7P family, tiled) matches it too.
    """
    n_slots = int(design.n_slots)
    n_phases = int(design.n_phases)
    p = int(design.pole_pairs)
    r_mean = 0.5 * (float(design.r_inner_m) + float(design.r_outer_m))
    sector = 2.0 * np.pi / n_slots
    half = 0.5 * sector - float(design.trace_space_m) / (2.0 * r_mean)
    if half <= 0:
        half = 0.4 * sector

    layout = _coil_layout(n_slots, n_phases)
    num = np.zeros(n_phases, dtype=complex)
    cnt = np.zeros(n_phases)
    for k, (ph, sign) in enumerate(layout):
        center = k * sector
        # Coil flux-linkage phasor (max magnitude 2 = full-pitch, fully aligned).
        e = sign * (np.exp(1j * harmonic * p * (center - half))
                    - np.exp(1j * harmonic * p * (center + half)))
        num[ph] += e
        cnt[ph] += 1
    # Normalise by 2 per coil so kw captures BOTH distribution and pitch.
    kws = [abs(num[i]) / (2.0 * cnt[i]) for i in range(n_phases) if cnt[i] > 0]
    return float(np.mean(kws)) if kws else 0.0


def build_coil(design: MotorDesign) -> CoilGeometry:
    """Generate the stator winding geometry for ``design.winding_topology``.

    Sharp trace corners in the raw ``polylines`` are filleted by
    ``design.corner_radius_m`` (set 0 to keep them sharp). Only the drawn /
    exported polylines are rounded -- the per-segment arrays feeding the torque
    integrator are left untouched.
    """
    topo = design.winding_topology
    if topo == "concentrated":
        geo = _build_concentrated(design)
    elif topo == "radial_spoke":
        geo = _build_radial_spoke(design)
    elif topo == "spiral":
        geo = _build_spiral(design)
    else:
        raise ValueError(f"Unknown winding_topology {topo!r}")

    radius = float(getattr(design, "corner_radius_m", 0.0))
    if radius > 0 and geo.polylines:
        geo.polylines = [_round_corners(pl, radius) for pl in geo.polylines]
    return geo


def coil_current_source(design: MotorDesign, i_phase_amps):
    """Energised concentrated coils as a field source (:class:`CurrentSource`).

    Builds the same continuous spiral per coil that the torque model and KiCad
    export use, and drives each with its phase current: coil ``k`` carries
    ``i_phase_amps[phase_k] * sense_k`` along its whole trace (``sense_k`` is the
    star-of-slots winding sign). ``i_phase_amps`` is a length-``n_phases`` array of
    phase currents [A] -- e.g. one commutation instant. The result feeds
    :func:`pcb_motor.field.b_field_at_points` to show the field the stator makes.
    """
    from .coil_spiral import coil_spiral_polyline
    from .design import CurrentSource

    i_phase = np.asarray(i_phase_amps, dtype=float).reshape(-1)
    n_slots = int(design.n_slots)
    n_phases = int(design.n_phases)
    n_layers = int(design.copper_layers)
    sector = 2.0 * np.pi / n_slots
    layout = _coil_layout(n_slots, n_phases)
    # copper layers span the board symmetrically about its centre

    sources = []
    for layer in range(n_layers):
        z = design.layer_z_m(layer)
        for k in range(n_slots):
            phase, sign = layout[k]
            poly = coil_spiral_polyline(design, k * sector, z)
            if poly.shape[0] < 2:
                continue
            cur = float(i_phase[phase]) * float(sign)
            sources.append(CurrentSource(poly, np.full(poly.shape[0], cur)))
    return CurrentSource.concat(sources)


def phase_resistance(
    design: MotorDesign, geo: CoilGeometry, temp_c: float | None = None
) -> float:
    """Per-phase resistance [ohm].

    ``R = rho_cu(temp) * length_per_phase_m / conductor_area_m2``, then divided
    by ``parallel_paths**2`` (parallel paths cut both the length each path carries
    and multiply the cross-section, giving the square). ``temp_c=None`` -> 20 C.

    Tapered windings carry the exact integral ``sum(dl/A)`` in
    ``geo.length_over_area_per_phase`` (the cross-section varies with radius);
    when present it replaces the uniform ``L/A`` shortcut.
    """
    if temp_c is None:
        rho = RHO_CU_20
    else:
        rho = RHO_CU_20 * (1.0 + ALPHA_CU * (temp_c - 20.0))
    if geo.length_over_area_per_phase is not None:
        r = rho * geo.length_over_area_per_phase
    else:
        if geo.conductor_area_m2 <= 0:
            raise ValueError("conductor_area_m2 must be positive")
        r = rho * geo.length_per_phase_m / geo.conductor_area_m2
    paths = int(design.parallel_paths)
    return r / (paths ** 2)

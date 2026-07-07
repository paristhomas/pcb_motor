"""Rotor magnets as Amperian (bound-current) equivalent current loops.

A uniformly, axially-magnetised permanent magnet is equivalent (for its external
field) to a sheet of bound surface current circulating around its perimeter. For
a magnet of axial thickness ``t`` and magnetisation ``M = Br / mu0`` the total
amp-turns around the perimeter is ``I_eq = M * t = (Br / mu0) * t``. We trace
each magnet's perimeter as a closed polyline and assign that current to it,
alternating sign pole-to-pole (N, S, N, S, ...).

This is a *perimeter-loop* (thin-shell) approximation: we model the bound
surface current on the side walls but lump it onto a single radial mid-plane
loop (or ``n_stack`` loops spread across the thickness). It captures the dipole
character and the correct ``I_eq`` magnitude; it does not resolve the radial
variation of the side-wall current density, so near-field detail at the magnet
faces is approximate. Accuracy improves with ``n_stack > 1``.
"""

from __future__ import annotations

import numpy as np

from .constants import NDFEB_BR

from .design import CurrentSource, RotorConfig

MU0 = 4e-7 * np.pi


def i_eq(rotor: RotorConfig) -> float:
    """Amperian equivalent current [A] for one magnet: (Br/mu0) * thickness."""
    br = NDFEB_BR[rotor.magnet_grade.upper()]
    return (br / MU0) * rotor.magnet_thickness_m


def _arc_xy(r: float, a0: float, a1: float, n_arc: int) -> np.ndarray:
    """``n_arc`` points (x, y) along the arc of radius ``r`` from ``a0`` to
    ``a1`` (inclusive of both endpoints)."""
    angles = np.linspace(a0, a1, n_arc)
    return np.column_stack([r * np.cos(angles), r * np.sin(angles)])


def _magnet_loops(
    rotor: RotorConfig,
    theta_rad: float = 0.0,
    n_arc: int = 24,
    n_stack: int = 1,
) -> list[tuple[np.ndarray, float]]:
    """Build the per-magnet Amperian loops.

    Returns a list of ``(vertices, I_eq_signed)`` tuples, one per sub-loop. There
    are ``2 * pole_pairs`` magnets; each is split into ``n_stack`` sub-loops
    stacked in z across ``[-t/2, +t/2]``, each carrying ``I_eq / n_stack``. The
    sign alternates between adjacent magnets (poles).
    """
    n_poles = 2 * rotor.pole_pairs
    pole_pitch = 2.0 * np.pi / n_poles            # angular width of one pole slot
    arc = rotor.pole_coverage * pole_pitch        # magnet angular span (centred)
    r_in = rotor.magnet_r_inner_m
    r_out = rotor.magnet_r_outer_m
    t = rotor.magnet_thickness_m
    i_total = i_eq(rotor)
    i_sub = i_total / n_stack

    # z positions of the n_stack sub-loops, centred on z=0 across the thickness.
    if n_stack == 1:
        z_levels = np.array([0.0])
    else:
        z_levels = np.linspace(-t / 2.0, t / 2.0, n_stack)

    loops: list[tuple[np.ndarray, float]] = []
    for k in range(n_poles):
        slot_centre = theta_rad + (k + 0.5) * pole_pitch
        a0 = slot_centre - arc / 2.0
        a1 = slot_centre + arc / 2.0
        sign = 1.0 if (k % 2 == 0) else -1.0

        # Perimeter in the xy-plane: inner arc (a0->a1), outer arc (a1->a0),
        # then close. The two radial edges are the implicit segments joining the
        # last inner-arc point to the first outer-arc point and back to the start.
        inner = _arc_xy(r_in, a0, a1, n_arc)
        outer = _arc_xy(r_out, a1, a0, n_arc)   # reversed so the loop is contiguous
        ring_xy = np.vstack([inner, outer, inner[:1]])  # closed loop (repeat first)

        i_signed = sign * i_sub
        for z in z_levels:
            verts = np.column_stack(
                [ring_xy[:, 0], ring_xy[:, 1], np.full(ring_xy.shape[0], z)]
            )
            loops.append((verts, i_signed))

    return loops


ROUND_TOPOLOGIES = ("round", "round_outer", "round_inner")


def is_round(topology: str) -> bool:
    """True for any round-disc rotor construction (both-rings or single-ring)."""
    return topology in ROUND_TOPOLOGIES


def active_rings(rotor: RotorConfig) -> list[tuple[float, float]]:
    """``(ring_radius_m, disc_diameter_m)`` pairs present for this round rotor.

    - ``round``        -- both rings (outer larger discs + inner smaller discs).
    - ``round_outer``  -- outer ring only (inner ring removed).
    - ``round_inner``  -- inner ring only (outer ring removed).

    Single ring of larger discs vs single ring of smaller discs are the two
    "remove one ring" study cases; the carrier extent and inertia downstream key
    off the same list, so geometry stays self-consistent.
    """
    outer = (rotor.outer_ring_r_m, rotor.outer_disc_d_m)
    inner = (rotor.inner_ring_r_m, rotor.inner_disc_d_m)
    if rotor.magnet_topology == "round_outer":
        return [outer]
    if rotor.magnet_topology == "round_inner":
        return [inner]
    return [outer, inner]


def _round_two_ring_loops(
    rotor: RotorConfig,
    theta_rad: float = 0.0,
    n_circle: int = 28,
    n_stack: int = 1,
) -> list[tuple[np.ndarray, float]]:
    """Round disc-magnet rotor (the buildable round-stock rotor).

    ``2 * pole_pairs`` poles; each pole carries the round discs of every
    :func:`active_rings` entry at the same angle, sharing polarity (outer disc
    radius ``outer_disc_d_m/2`` at ``outer_ring_r_m``, inner ``inner_disc_d_m/2``
    at ``inner_ring_r_m``). Polarity alternates pole-to-pole. Each
    axially-magnetised disc is its Amperian perimeter current loop,
    ``I_eq = (Br/mu0) * thickness``. ``magnet_topology`` selects which rings are
    present (both, outer-only, or inner-only).
    """
    n_poles = 2 * rotor.pole_pairs
    pole_pitch = 2.0 * np.pi / n_poles
    i_total = i_eq(rotor)
    i_sub = i_total / n_stack
    t = rotor.magnet_thickness_m
    z_levels = np.array([0.0]) if n_stack == 1 else np.linspace(-t / 2, t / 2, n_stack)
    rings = [(ring_r, disc_d / 2.0) for ring_r, disc_d in active_rings(rotor)]

    loops: list[tuple[np.ndarray, float]] = []
    tcirc = np.linspace(0.0, 2.0 * np.pi, n_circle + 1)   # closed circle
    for k in range(n_poles):
        ang = theta_rad + (k + 0.5) * pole_pitch
        sign = 1.0 if (k % 2 == 0) else -1.0
        for ring_r, disc_r in rings:
            cx, cy = ring_r * np.cos(ang), ring_r * np.sin(ang)
            circ_xy = np.column_stack([cx + disc_r * np.cos(tcirc),
                                       cy + disc_r * np.sin(tcirc)])
            for z in z_levels:
                verts = np.column_stack([circ_xy[:, 0], circ_xy[:, 1],
                                         np.full(circ_xy.shape[0], z)])
                loops.append((verts, sign * i_sub))
    return loops


def magnet_segments(
    rotor: RotorConfig,
    theta_rad: float = 0.0,
    n_arc: int = 24,
    n_stack: int = 1,
) -> CurrentSource:
    """Rotor magnets as Amperian equivalent current loops.

    ``rotor.magnet_topology`` selects the rotor construction:
      - ``"arc"``         -- ``2*pole_pairs`` pole-arc loops (continuous ring).
      - ``"round"``       -- two concentric rings of round disc magnets.
      - ``"round_outer"`` -- outer ring of discs only (inner ring removed).
      - ``"round_inner"`` -- inner ring of discs only (outer ring removed).
    Alternating polarity per pole; ``theta_rad`` rotates the rotor about z.
    """
    if is_round(rotor.magnet_topology):
        loops = _round_two_ring_loops(rotor, theta_rad, n_stack=n_stack)
    else:
        loops = _magnet_loops(rotor, theta_rad, n_arc, n_stack)
    sources = []
    for verts, i_signed in loops:
        currents = np.full(verts.shape[0], i_signed)
        currents[-1] = i_signed  # closed loop: last segment also carries current
        sources.append(CurrentSource(verts, currents))
    return CurrentSource.concat(sources)

"""Rotor inertia about the spin axis.

Analytical rotor (spinning-motor) inertia terms. The headline objective is
continuous acceleration ``a_cont = tau_cont / J``; with a bare-magnet rotor
(no iron, no windings) the moving inertia is small, which is the whole appeal
of the dual-stator topology.

This module covers only the *rotor* (spinning) inertia; the arm / pendulum /
load terms live elsewhere (``total_inertia`` simply adds a caller-supplied load).
"""

from __future__ import annotations

import math

from .constants import NDFEB_DENSITY, PLA_DENSITY
from .design import RotorConfig
from .magnets import active_rings, is_round


def rotor_inertia(rotor: RotorConfig) -> float:
    """J of all spinning motor parts about the axis [kg m^2].

    Two terms, for the default single-rotor sandwich (one magnet ring + one PLA
    carrier disc):

    1. Magnet ring -- a thin annulus of NdFeB covering ``pole_coverage`` of the
       magnet annulus, of axial thickness ``magnet_thickness_m``::

           annulus_area = pi * (r_o^2 - r_i^2)
           m_mag        = NDFEB_DENSITY * pole_coverage * annulus_area * thickness
           J_mag        = m_mag * (r_i^2 + r_o^2) / 2        (thin ring about axis)

       (``r_i`` / ``r_o`` are the magnet inner / outer radii.)

    2. PLA carrier -- a thin *full* PLA disc of radius ``r_o`` and thickness
       ``carrier_thickness_m`` that holds the magnets to the shaft::

           m_carrier = PLA_DENSITY * pi * r_o^2 * carrier_thickness_m
           J_carrier = 1/2 * m_carrier * r_o^2                (solid disc)

    Moving back iron: ``back_iron=True`` means iron plates behind the *stators*
    (they are FIXED), so back iron contributes ZERO rotor inertia.
    ``RotorConfig`` has no way to express rotor-bonded iron, so that term is 0
    here -- consistent with the validated default topology.

    Dual-rotor sandwich (``rotor_sides == 2``): TWO identical magnet+carrier
    discs spin together, so both terms double (x rotor_sides).
    """
    sides = max(1, int(rotor.rotor_sides))
    t = rotor.magnet_thickness_m

    if is_round(rotor.magnet_topology):
        # Round disc magnets (both rings, or a single ring for the round_outer /
        # round_inner study cases). Each disc is a solid cylinder of radius a,
        # mass m, at ring radius R; about the central spin axis
        # J_disc = m * (a^2 / 2 + R^2)  (own axis + parallel-axis shift).
        n_poles = 2 * rotor.pole_pairs
        j_mag = sum(
            n_poles * (NDFEB_DENSITY * math.pi * (disc_d / 2.0) ** 2 * t)
            * (0.5 * (disc_d / 2.0) ** 2 + ring_r**2)
            for ring_r, disc_d in active_rings(rotor)
        )
        # PLA carrier disc. It is the SAME physical part regardless of which
        # rings are populated -- depopulating a ring removes magnet mass but does
        # NOT shrink the carrier -- so size it to the designed rotor envelope
        # (the outer ring's outer edge), not just the active rings. Using the
        # active rings here would let "inner ring only" cheat a tiny carrier and
        # report an unphysically high acceleration.
        r_extent = rotor.outer_ring_r_m + rotor.outer_disc_d_m / 2.0
        m_carrier = PLA_DENSITY * math.pi * r_extent**2 * rotor.carrier_thickness_m
        j_carrier = 0.5 * m_carrier * r_extent**2
        return (j_mag + j_carrier) * sides

    # --- arc (continuous pole-arc ring) ---
    r_i = rotor.magnet_r_inner_m
    r_o = rotor.magnet_r_outer_m
    annulus_area = math.pi * (r_o**2 - r_i**2)

    # magnet ring (thin annulus about its axis)
    m_mag = NDFEB_DENSITY * rotor.pole_coverage * annulus_area * t
    j_mag = m_mag * 0.5 * (r_o**2 + r_i**2)

    # PLA carrier disc (solid disc of radius r_o)
    m_carrier = PLA_DENSITY * math.pi * r_o**2 * rotor.carrier_thickness_m
    j_carrier = 0.5 * m_carrier * r_o**2

    # moving back iron: 0 for the default sandwich (iron is fixed)
    return (j_mag + j_carrier) * sides


def total_inertia(rotor: RotorConfig, load_inertia_kgm2: float) -> float:
    """Spinning rotor inertia plus the externally-supplied load [kg m^2]."""
    return rotor_inertia(rotor) + load_inertia_kgm2

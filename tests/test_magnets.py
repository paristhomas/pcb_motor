"""Tests for the Amperian magnet-ring model (pcb_motor/magnets.py)."""

from __future__ import annotations

import numpy as np
import pytest

from pcb_motor.design import RotorConfig
from pcb_motor.magnets import MU0, _magnet_loops, _round_two_ring_loops, i_eq, magnet_segments
from pcb_motor.constants import NDFEB_BR


def test_round_two_ring_structure():
    """Round rotor: 2 discs (inner+outer) per pole, sharing polarity; adjacent
    poles alternate; disc centres sit on the two ring radii."""
    rotor = RotorConfig(magnet_topology="round", pole_pairs=7)
    loops = _round_two_ring_loops(rotor)
    n_poles = 2 * rotor.pole_pairs
    assert len(loops) == 2 * n_poles  # one outer + one inner disc per pole

    for k in range(n_poles):
        s_out = loops[2 * k][1]
        s_in = loops[2 * k + 1][1]
        assert np.sign(s_out) == np.sign(s_in)            # same pole, same polarity
        if k > 0:
            assert np.sign(loops[2 * k][1]) == -np.sign(loops[2 * (k - 1)][1])

    for k in range(n_poles):
        for j, ring_r in ((0, rotor.outer_ring_r_m), (1, rotor.inner_ring_r_m)):
            verts = loops[2 * k + j][0][:-1]  # drop duplicated closing vertex
            cx, cy = verts[:, 0].mean(), verts[:, 1].mean()
            assert np.hypot(cx, cy) == pytest.approx(ring_r, rel=1e-3)


def _net_current(verts: np.ndarray, i_signed: float) -> float:
    """A closed perimeter loop carries one signed current; just return it."""
    return i_signed


def test_pole_count_and_alternation():
    rotor = RotorConfig(pole_pairs=7)
    n_poles = 2 * rotor.pole_pairs

    # n_stack == 1: one loop per magnet.
    loops = _magnet_loops(rotor, theta_rad=0.0, n_arc=12, n_stack=1)
    assert len(loops) == n_poles

    signs = [np.sign(i_signed) for _verts, i_signed in loops]
    # Adjacent magnets have opposite sign all the way around the ring.
    for a, b in zip(signs, signs[1:]):
        assert a == -b
    # Equal number of N and S poles, alternating.
    assert sum(1 for s in signs if s > 0) == n_poles // 2
    assert sum(1 for s in signs if s < 0) == n_poles // 2

    # n_stack == 3: 3 sub-loops per magnet, each I_eq/3, same sign within a magnet.
    n_stack = 3
    loops3 = _magnet_loops(rotor, theta_rad=0.0, n_arc=12, n_stack=n_stack)
    assert len(loops3) == n_poles * n_stack
    expected_sub = i_eq(rotor) / n_stack
    for _verts, i_signed in loops3:
        assert np.isclose(abs(i_signed), expected_sub)
    # Sub-loop signs come in runs of n_stack (one magnet), then flip.
    signs3 = [np.sign(i_signed) for _v, i_signed in loops3]
    for mag in range(n_poles):
        block = signs3[mag * n_stack:(mag + 1) * n_stack]
        assert len(set(block)) == 1  # consistent within a magnet
        assert block[0] == (1 if mag % 2 == 0 else -1)

    # Per-magnet net current sum across sub-loops recovers the full +/- I_eq.
    full = i_eq(rotor)
    for mag in range(n_poles):
        block = loops3[mag * n_stack:(mag + 1) * n_stack]
        net = sum(i_signed for _v, i_signed in block)
        assert np.isclose(abs(net), full)


def test_i_eq_value():
    for grade in ("N35", "N42", "N52"):
        for thickness in (1.0e-3, 3.0e-3, 5.0e-3):
            rotor = RotorConfig(magnet_grade=grade, magnet_thickness_m=thickness)
            expected = NDFEB_BR[grade] / MU0 * thickness
            assert np.isclose(i_eq(rotor), expected, rtol=1e-12)

    # And the signed loop magnitude matches I_eq for n_stack=1.
    rotor = RotorConfig(magnet_grade="N42", magnet_thickness_m=3.0e-3)
    loops = _magnet_loops(rotor, n_arc=8, n_stack=1)
    for _v, i_signed in loops:
        assert np.isclose(abs(i_signed), i_eq(rotor))


def test_geometry_bounds():
    rotor = RotorConfig(pole_pairs=5, magnet_thickness_m=2.0e-3)
    n_stack = 4
    loops = _magnet_loops(rotor, theta_rad=0.37, n_arc=20, n_stack=n_stack)
    t = rotor.magnet_thickness_m
    for verts, _i in loops:
        r = np.hypot(verts[:, 0], verts[:, 1])
        # Radial: every vertex on the inner or outer perimeter (within tol).
        assert r.min() >= rotor.magnet_r_inner_m - 1e-9
        assert r.max() <= rotor.magnet_r_outer_m + 1e-9
        # All radii are essentially one of the two perimeter radii.
        near_inner = np.isclose(r, rotor.magnet_r_inner_m, atol=1e-9)
        near_outer = np.isclose(r, rotor.magnet_r_outer_m, atol=1e-9)
        assert np.all(near_inner | near_outer)
        # Axial: within +/- t/2.
        assert np.all(np.abs(verts[:, 2]) <= t / 2.0 + 1e-12)


def test_magnet_segments_is_current_source():
    rotor = RotorConfig(pole_pairs=4)
    src = magnet_segments(rotor, theta_rad=0.0, n_arc=16, n_stack=2)
    # Same number of currents as vertices (CurrentSource contract).
    assert src.vertices.shape[0] == src.currents.shape[0]
    assert src.vertices.shape[1] == 3
    assert src.vertices.shape[0] > 0


def test_field_is_dipole_like():
    """On-axis Bz from the ring is finite and flips when rotated one pole pitch.

    Skipped if pcb_motor.field is not yet implemented.
    """
    field = pytest.importorskip("pcb_motor.field")

    rotor = RotorConfig(pole_pairs=7)
    # A point just off-axis at the stator plane, within one pole's arc, so the
    # local field is dominated by a single pole and flips when we rotate by one
    # pole pitch (theta = pi / pole_pairs swaps N<->S in that slot).
    z = rotor.stator_z_m()
    r_mid = 0.5 * (rotor.magnet_r_inner_m + rotor.magnet_r_outer_m)
    # First pole slot is centred at angle pole_pitch/2 = pi/(2*pole_pairs).
    a = np.pi / (2 * rotor.pole_pairs)
    pt = np.array([[r_mid * np.cos(a), r_mid * np.sin(a), z]])

    src0 = magnet_segments(rotor, theta_rad=0.0, n_arc=24, n_stack=1)
    src1 = magnet_segments(
        rotor, theta_rad=np.pi / rotor.pole_pairs, n_arc=24, n_stack=1
    )

    bz0 = field.b_field_at_points(src0, pt)[0, 2]
    bz1 = field.b_field_at_points(src1, pt)[0, 2]

    assert np.isfinite(bz0) and np.isfinite(bz1)
    assert abs(bz0) > 0.0
    # One pole pitch swaps the polarity seen at this point.
    assert np.sign(bz0) == -np.sign(bz1)

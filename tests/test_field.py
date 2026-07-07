"""Acceptance tests for pcb_motor.field.

These lock the unit conversions (m<->cm, gauss->T) and the kernel wiring by
comparing against the closed-form on-axis field of a circular current loop.
"""

from __future__ import annotations

import numpy as np

from pcb_motor.design import CurrentSource
from pcb_motor.field import b_field_at_points

MU0 = 4e-7 * np.pi


def _circular_loop(radius_m: float, current_a: float, n_seg: int = 360,
                   z0_m: float = 0.0) -> CurrentSource:
    """A closed circular current loop in the z=z0 plane, centred on the axis."""
    theta = np.linspace(0.0, 2.0 * np.pi, n_seg + 1)  # closed: last == first
    verts = np.column_stack([
        radius_m * np.cos(theta),
        radius_m * np.sin(theta),
        np.full_like(theta, z0_m),
    ])
    currents = np.full(verts.shape[0], current_a)
    return CurrentSource(verts, currents)


def _on_axis_bz(radius_m: float, current_a: float, z_m: np.ndarray) -> np.ndarray:
    """Closed-form on-axis Bz of a circular loop."""
    return MU0 * current_a * radius_m**2 / (2.0 * (radius_m**2 + z_m**2) ** 1.5)


def test_on_axis_loop_matches_closed_form():
    R = 20e-3
    I = 100.0
    loop = _circular_loop(R, I, n_seg=360)

    z_mm = np.array([5.0, 10.0, 20.0, 40.0])
    z_m = z_mm * 1e-3
    points = np.column_stack([np.zeros_like(z_m), np.zeros_like(z_m), z_m])

    B = b_field_at_points(loop, points, resolution_m=0.25e-3)
    bz = B[:, 2]
    expected = _on_axis_bz(R, I, z_m)

    rel = np.abs(bz - expected) / np.abs(expected)
    assert np.all(rel < 0.01), f"rel errors {rel}, Bz {bz}, expected {expected}"


def test_off_axis_and_shape():
    R = 20e-3
    I = 100.0
    loop = _circular_loop(R, I, n_seg=360)

    # Several on-axis points: shape (P,3); Bx, By ~ 0 by symmetry.
    z_m = np.array([5.0, 10.0, 20.0]) * 1e-3
    points = np.column_stack([np.zeros_like(z_m), np.zeros_like(z_m), z_m])
    B = b_field_at_points(loop, points)
    assert B.shape == (3, 3)

    # On axis, the transverse components vanish; only discretisation noise
    # remains (a few parts in 1e3 of |Bz| at the default resolution).
    bz_mag = np.abs(B[:, 2])
    assert np.all(np.abs(B[:, 0]) < 5e-3 * bz_mag)
    assert np.all(np.abs(B[:, 1]) < 5e-3 * bz_mag)

    # Single point of shape (3,) -> returns (1,3).
    one = b_field_at_points(loop, np.array([0.0, 0.0, 10e-3]))
    assert one.shape == (1, 3)
    np.testing.assert_allclose(one[0, 2], B[1, 2], rtol=1e-9)


def test_superposition():
    # Two identical, coplanar, co-current loops give 2x a single loop on axis.
    R = 20e-3
    I = 100.0
    z_eval = 10e-3
    pt = np.array([[0.0, 0.0, z_eval]])

    single = b_field_at_points(_circular_loop(R, I), pt)[0, 2]
    two = CurrentSource.concat([_circular_loop(R, I), _circular_loop(R, I)])
    double = b_field_at_points(two, pt)[0, 2]
    assert abs(double - 2.0 * single) < 0.01 * abs(2.0 * single)

    # Two opposed loops symmetric about z=0 cancel Bz at the midplane (z=0).
    up = _circular_loop(R, I, z0_m=+10e-3)
    down = _circular_loop(R, -I, z0_m=-10e-3)
    opposed = CurrentSource.concat([up, down])
    b_mid = b_field_at_points(opposed, np.array([[0.0, 0.0, 0.0]]))[0]
    # Reference: |Bz| of one loop seen from 10mm away, to scale the tolerance.
    ref = abs(_on_axis_bz(R, I, np.array([10e-3]))[0])
    assert abs(b_mid[2]) < 1e-3 * ref


def test_stator_field_phase_symmetry():
    """The stator-coil B_z map looks lopsided only because of the FOC phase
    currents, not a per-phase imbalance bug.

    Driving each phase *alone* at equal unit current produces an equal-strength
    field: the peak |B_z| matches across phases to within the spiral's small
    terminal-step asymmetry. (The full map drives phases at cos() of their offsets,
    i.e. 1 : -0.5 : -0.5 with phase A at peak, so B and C contribute half-strength
    opposite-sign lobes -- which is correct commutation, not asymmetry.)
    """
    from pcb_motor.coils import coil_current_source
    from pcb_motor.design import MotorDesign

    d = MotorDesign()
    n_phases = int(d.n_phases)
    z = d.rotor().stator_z_m() - 0.3e-3
    lim = d.r_outer_m * 1.05
    xs = np.linspace(-lim, lim, 36)
    X, Y = np.meshgrid(xs, xs)
    pts = np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, z)])

    peaks = []
    for k in range(n_phases):
        i_phase = np.zeros(n_phases)
        i_phase[k] = 1.0
        bz = b_field_at_points(coil_current_source(d, i_phase), pts)[:, 2]
        peaks.append(float(np.max(np.abs(bz))))
    peaks = np.array(peaks)
    # Each phase alone makes the same peak field: spread well under 5%.
    assert peaks.std() / peaks.mean() < 0.05

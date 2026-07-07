"""Tests for pcb_motor.iron — stator back iron via the method of images."""

from __future__ import annotations

import numpy as np

from pcb_motor.design import CurrentSource, MotorDesign
from pcb_motor.field import b_field_at_points
from pcb_motor.iron import iron_images, iron_plane_z, with_iron_images


def _loop(radius: float, z: float, i_amp: float = 1.0, n: int = 96) -> CurrentSource:
    ang = np.linspace(0.0, 2.0 * np.pi, n + 1)
    verts = np.column_stack([radius * np.cos(ang), radius * np.sin(ang),
                             np.full(ang.shape, z)])
    return CurrentSource(verts, np.full(verts.shape[0], i_amp))


def test_single_plane_doubles_bz_at_plate_face():
    """At a mu->infinity plane, Bz must exactly double (one image, symmetry)."""
    src = _loop(radius=20e-3, z=5e-3)
    z_plane = 0.0
    img = iron_images(src, z_plane, -1e9, order=1)[0]   # bottom plane sent far away
    pt = np.array([[3e-3, 0.0, z_plane]])
    bz_free = b_field_at_points(src, pt, 0.5e-3)[0, 2]
    bz_img = b_field_at_points(img, pt, 0.5e-3)[0, 2]
    assert np.isclose(bz_img / bz_free, 1.0, rtol=1e-6)


def test_two_plane_series_converges_for_alternating_poles():
    """Order 3 vs 5 agree to <1% for a real (alternating-pole) rotor.

    Convergence relies on the alternating-pole field decaying exponentially
    with distance over the pole-pitch length scale; a single loop (no pole
    alternation) converges much more slowly and is NOT the use case.
    """
    from pcb_motor.magnets import magnet_segments

    rotor = _c1(back_iron=True).rotor()
    src = magnet_segments(rotor)
    z_p = iron_plane_z(rotor)
    pt = np.array([[37.2e-3, 0.0, 3.0e-3]])

    def bz(order):
        total = CurrentSource.concat([src] + iron_images(src, z_p, -z_p, order))
        return b_field_at_points(total, pt, 0.5e-3)[0, 2]

    assert np.isclose(bz(3), bz(5), rtol=1e-2)


def _c1(**over) -> MotorDesign:
    return MotorDesign(
        winding_topology="concentrated", n_slots=12, pole_pairs=7,
        magnet_topology="round", outer_ring_r_m=37.2e-3, outer_disc_d_m=15e-3,
        inner_ring_r_m=24.4e-3, inner_disc_d_m=10e-3,
        magnet_thickness_m=4e-3, air_gap_m=0.75e-3, n_stators=2,
        board_thickness_m=0.8e-3, copper_layers=2,
        tapered_traces=True, trace_width_m=2.0e-3, trace_space_m=0.13e-3,
        r_inner_m=19e-3, r_outer_m=44.5e-3, load_inertia_kgm2=3.14e-4,
        **over,
    )


def test_back_iron_raises_field_and_inductance():
    """Iron plates must raise B_gap (1.3-3x) and phase inductance (>1.3x)."""
    from pcb_motor.coils import build_coil
    from pcb_motor.parasitics import phase_inductance
    from pcb_motor.torque import kt_and_torque

    d_air = _c1(back_iron=False)
    d_fe = _c1(back_iron=True)
    geo = build_coil(d_air)

    b_air = kt_and_torque(d_air, geo)["b_gap_mean_t"]
    b_fe = kt_and_torque(d_fe, geo)["b_gap_mean_t"]
    assert 1.3 < b_fe / b_air < 3.0, (b_air, b_fe)

    l_air = phase_inductance(d_air, geo)
    l_fe = phase_inductance(d_fe, geo)
    assert 1.3 < l_fe / l_air < 5.0, (l_air, l_fe)


def test_plate_pull_positive_and_plausible():
    """Per-plate magnetic pull is tens of newtons for this geometry."""
    from pcb_motor.iron import plate_axial_force

    f = plate_axial_force(_c1(back_iron=True))
    # ~1 bar magnetic pressure at ~0.5 T over the annulus: hundreds of N.
    assert 50.0 < f < 1000.0, f
    assert plate_axial_force(_c1(back_iron=False)) == 0.0


def test_iron_plane_position():
    d = _c1(back_iron=True)
    z = iron_plane_z(d.rotor())
    assert np.isclose(z, 4e-3 / 2 + 0.75e-3 + 0.8e-3)
    # with_iron_images is the identity when iron is off
    src = _loop(10e-3, 1e-3)
    out = with_iron_images(src, _c1(back_iron=False).rotor())
    assert out.vertices.shape == src.vertices.shape

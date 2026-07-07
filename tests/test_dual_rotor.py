"""Dual-rotor sandwich (rotor_sides=2): one stator board between two magnet
rotors, no back iron. The second magnet plane sits at z = 2*stator_z with the
same magnetisation pattern (attracting arrangement), so the axial fields ADD
at the stator board: Bz and Kt ~double, and rotor inertia exactly doubles."""

from __future__ import annotations

import numpy as np
import pytest

from pcb_motor.design import MotorDesign, RotorConfig
from pcb_motor.evaluate import evaluate_design
from pcb_motor.field import b_field_at_points
from pcb_motor.inertia import rotor_inertia
from pcb_motor.magnets import magnet_segments, validate_rotor_sides
from pcb_motor.torque import kt_and_torque


def test_magnet_segments_emits_two_planes():
    """rotor_sides=2 duplicates every Amperian loop, translated +2*stator_z,
    with the SAME current sense (aligned dipoles / attracting arrangement)."""
    r1 = RotorConfig(n_stators=1, rotor_sides=1)
    r2 = RotorConfig(n_stators=1, rotor_sides=2)
    s1 = magnet_segments(r1, n_arc=8)
    s2 = magnet_segments(r2, n_arc=8)

    n = s1.vertices.shape[0]
    assert s2.vertices.shape[0] == 2 * n

    # First half is plane A (identical to single-rotor); second half is plane B
    # translated by exactly +2*stator_z in z, same x/y.
    dz = 2.0 * r1.stator_z_m()
    np.testing.assert_allclose(s2.vertices[:n], s1.vertices)
    np.testing.assert_allclose(s2.vertices[n:, :2], s1.vertices[:, :2])
    np.testing.assert_allclose(s2.vertices[n:, 2], s1.vertices[:, 2] + dz)
    # Same current sense in both planes (block-boundary zeros excepted).
    live = np.abs(s1.currents) > 0
    np.testing.assert_allclose(s2.currents[n:][live], s1.currents[live])


def test_bz_at_stator_plane_roughly_doubles():
    """Bz at the stator plane for rotor_sides=2 is ~2x the single-rotor value
    (and definitely not ~0 -- that would be the repelling/cancelling sense)."""
    r1 = RotorConfig(n_stators=1, rotor_sides=1)
    r2 = RotorConfig(n_stators=1, rotor_sides=2)
    z = r1.stator_z_m()
    r_mid = 0.5 * (r1.magnet_r_inner_m + r1.magnet_r_outer_m)
    # Points over the centres of the first few poles.
    pitch = np.pi / r1.pole_pairs
    angs = (np.arange(3) + 0.5) * pitch
    pts = np.column_stack([r_mid * np.cos(angs), r_mid * np.sin(angs),
                           np.full(angs.size, z)])
    bz1 = b_field_at_points(magnet_segments(r1), pts)[:, 2]
    bz2 = b_field_at_points(magnet_segments(r2), pts)[:, 2]
    assert np.all(np.abs(bz1) > 0)
    ratio = bz2 / bz1
    assert np.all(ratio > 1.7) and np.all(ratio < 2.05)


def test_kt_roughly_doubles():
    """Machine Kt of the dual-rotor sandwich vs the single-rotor, single-stator
    machine (fields add across the board).

    With ONE copper layer the coil sits exactly at the sandwich mirror plane
    (z = stator_z), so by symmetry the ratio is exactly 2. With the default TWO
    layers ``build_coil`` places the second layer at z0 + board_thickness --
    beyond the mirror plane, closer to rotor B than layer 1 is to rotor A -- so
    rotor B over-compensates that layer's weaker single-rotor field and the
    machine ratio comes out ABOVE 2 (~2.3), not between 1.7 and 2. Both are
    consequences of the same aligned-dipole field addition; a cancelling sign
    error would give a ratio near 0 in either case.
    """
    kt1 = kt_and_torque(MotorDesign(n_stators=1, copper_layers=1))["kt_nm_per_a"]
    kt2 = kt_and_torque(MotorDesign(n_stators=1, copper_layers=1,
                                    rotor_sides=2))["kt_nm_per_a"]
    assert kt1 > 0
    assert 1.9 < kt2 / kt1 < 2.05

    kt1_2l = kt_and_torque(MotorDesign(n_stators=1))["kt_nm_per_a"]
    kt2_2l = kt_and_torque(MotorDesign(n_stators=1,
                                       rotor_sides=2))["kt_nm_per_a"]
    assert 2.0 < kt2_2l / kt1_2l < 2.6


def test_rotor_inertia_doubles():
    """Two identical magnet+carrier discs spin together: J exactly doubles,
    for both the arc and round rotor constructions."""
    for topo in ("arc", "round"):
        j1 = rotor_inertia(RotorConfig(n_stators=1, rotor_sides=1,
                                       magnet_topology=topo))
        j2 = rotor_inertia(RotorConfig(n_stators=1, rotor_sides=2,
                                       magnet_topology=topo))
        assert j2 == pytest.approx(2.0 * j1, rel=1e-12)


def test_invalid_combos_raise():
    # Dual rotor with dual stators: no such stack.
    with pytest.raises(ValueError, match="n_stators"):
        magnet_segments(RotorConfig(n_stators=2, rotor_sides=2))
    # Dual rotor with stator back iron: the sandwich is coreless.
    with pytest.raises(ValueError, match="back_iron"):
        magnet_segments(RotorConfig(n_stators=1, rotor_sides=2, back_iron=True))
    # Nonsense side counts.
    with pytest.raises(ValueError, match="rotor_sides"):
        magnet_segments(RotorConfig(rotor_sides=3))
    # evaluate_design fails fast on the same rules (before the coil build).
    with pytest.raises(ValueError, match="n_stators"):
        evaluate_design(MotorDesign(n_stators=2, rotor_sides=2))
    with pytest.raises(ValueError, match="back_iron"):
        evaluate_design(MotorDesign(n_stators=1, rotor_sides=2, back_iron=True))
    # The validator itself accepts the valid combos.
    validate_rotor_sides(RotorConfig())
    validate_rotor_sides(RotorConfig(n_stators=1, rotor_sides=2))


def test_stack_figure_matches_configuration():
    """viz draws the actual stack: n_stators boards (was: always two), one board
    between two magnet planes for the sandwich, and singular/plural grammar."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from pcb_motor.viz import plot_stack

    def magnet_bands(fig):
        # Count purple rotor-magnet rectangles vs yellow stator-PCB rectangles.
        from matplotlib.patches import Rectangle
        mags = boards = 0
        for p in fig.axes[0].patches:
            if not isinstance(p, Rectangle):
                continue
            fc = matplotlib.colors.to_hex(p.get_facecolor())
            if fc == "#cf9bcf":
                mags += 1
            elif fc == "#ffcc66":
                boards += 1
        return mags, boards

    fig = plot_stack(MotorDesign(n_stators=1))
    mags, boards = magnet_bands(fig)
    assert (mags, boards) == (1, 1)          # was 2 boards even for 1 stator
    assert "1 stator," in fig.axes[0].get_title()  # not "1 stators"
    plt.close(fig)

    fig = plot_stack(MotorDesign(n_stators=2))
    assert magnet_bands(fig) == (1, 2)
    assert "2 stators" in fig.axes[0].get_title()
    plt.close(fig)

    fig = plot_stack(MotorDesign(n_stators=1, rotor_sides=2))
    assert magnet_bands(fig) == (2, 1)       # sandwich: two magnet planes, one board
    assert "dual rotor" in fig.axes[0].get_title()
    plt.close(fig)


def test_evaluate_reports_dual_rotor():
    """evaluate_design exposes rotor_sides and warns that the rotor-rotor
    attraction is not modelled."""
    res = evaluate_design(MotorDesign(n_stators=1, rotor_sides=2))
    assert res["rotor_sides"] == 2
    assert any("rotor-rotor" in w for w in res["warnings"])
    assert res["kt_mNm_per_A"] > 0
    # Single-rotor default is unchanged: no dual-rotor warning.
    res1 = evaluate_design(MotorDesign())
    assert res1["rotor_sides"] == 1
    assert not any("rotor-rotor" in w for w in res1["warnings"])

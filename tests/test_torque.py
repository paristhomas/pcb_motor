"""Acceptance tests for pcb_motor.torque."""

from __future__ import annotations

import numpy as np

from pcb_motor.design import MotorDesign
from pcb_motor.coils import build_coil
from pcb_motor.torque import (
    kt_and_torque,
    torque_vs_angle,
    optimal_foc_delta,
    foc_segment_force,
    _b_on_coil,
)


def test_shear_integral_matches_kt():
    """The torque-producing shear (per-segment tangential force) integrates to the
    shaft torque: sum (r x dF)_z at the FOC peak-torque instant equals Kt (within
    the ~0 ripple + field resolution), and is positive (motoring)."""
    d = MotorDesign()
    geo = build_coil(d)
    kt = kt_and_torque(d, geo, i_amp=1.0)["kt_nm_per_a"]      # whole machine
    delta = optimal_foc_delta(d, geo)
    dF = foc_segment_force(d, geo, 0.0, 1.0, delta)           # one stator, 1 A
    mid = geo.midpoints_m.copy()
    mid[:, 2] = 0.0
    tau_one_stator = float(np.cross(mid, dF)[:, 2].sum())
    assert tau_one_stator > 0.0
    np.testing.assert_allclose(tau_one_stator * d.n_stators, kt, rtol=0.05)


def test_torque_comes_from_axial_field():
    """Axial-flux coupling: shaft torque is produced by the radial current
    crossing the AXIAL field B_z, not the in-plane (radial/circumferential)
    field. Zeroing B_z kills the torque; keeping only B_z reproduces it."""
    d = MotorDesign()
    geo = build_coil(d)
    n_phi = max(48, 2 * d.pole_pairs * 6)
    B = _b_on_coil(d, geo, 0.0, 10, n_phi)

    def capacity(Bv):
        dF = np.cross(geo.dvec_m, Bv)
        rr = geo.midpoints_m.copy()
        rr[:, 2] = 0.0
        g = np.cross(rr, dF)[:, 2]
        return float(np.abs(g[geo.is_radial]).sum())

    full = capacity(B)
    bz_only = B.copy(); bz_only[:, :2] = 0.0      # keep only axial
    inplane = B.copy(); inplane[:, 2] = 0.0       # keep only in-plane
    assert full > 0.0
    np.testing.assert_allclose(capacity(bz_only), full, rtol=1e-6)
    assert capacity(inplane) < 1e-3 * full


def test_torque_vs_angle_mean_matches_kt():
    """The commutated mean torque equals Kt; the operating ripple is tiny for
    this coreless machine; and the DC characteristic swings through zero."""
    d = MotorDesign()
    kt = kt_and_torque(d, i_amp=1.0)["kt_nm_per_a"]
    out = torque_vs_angle(d, i_amp=1.0, n_steps=48)

    assert out["tau_commutated_nm"].shape == (48,)
    assert out["tau_dc_nm"].shape == (48,)
    np.testing.assert_allclose(out["mean_torque_nm"], kt, rtol=0.05)
    # Coreless => near-zero operating ripple under continuous commutation.
    assert out["ripple_pct"] < 5.0
    # DC torque-angle characteristic is bipolar (passes through zero torque).
    assert out["tau_dc_nm"].min() < 0 < out["tau_dc_nm"].max()


def test_kt_positive_and_finite():
    out = kt_and_torque(MotorDesign(), i_amp=1.0)
    assert out["kt_nm_per_a"] > 0.0
    assert np.isfinite(out["torque_nm"])
    assert out["b_gap_mean_t"] > 0.0
    assert out["b_gap_peak_t"] >= out["b_gap_mean_t"]


def test_torque_linear_in_current():
    base = kt_and_torque(MotorDesign(), i_amp=1.0)
    doubled = kt_and_torque(MotorDesign(), i_amp=2.0)
    # Torque is linear in phase current; Kt is current-independent.
    np.testing.assert_allclose(doubled["torque_nm"], 2.0 * base["torque_nm"], rtol=1e-6)
    np.testing.assert_allclose(doubled["kt_nm_per_a"], base["kt_nm_per_a"], rtol=1e-6)


def test_kt_increases_with_stronger_magnet():
    weak = kt_and_torque(MotorDesign(magnet_grade="N35"))
    strong = kt_and_torque(MotorDesign(magnet_grade="N52"))
    assert strong["kt_nm_per_a"] > weak["kt_nm_per_a"]


def test_kt_scales_with_n_stators():
    one = kt_and_torque(MotorDesign(n_stators=1))
    two = kt_and_torque(MotorDesign(n_stators=2))
    np.testing.assert_allclose(
        two["kt_nm_per_a"], 2.0 * one["kt_per_stator"], rtol=1e-9
    )

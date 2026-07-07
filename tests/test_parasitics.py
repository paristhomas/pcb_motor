"""Tests for pcb_motor.parasitics — inductance, PWM ripple, eddy loss."""

from __future__ import annotations

import numpy as np

from pcb_motor.design import CoilGeometry, MotorDesign
from pcb_motor.parasitics import MU0, eddy_loss, phase_inductance, pwm_ripple


def _loop_geometry(radius: float, n_pts: int = 256) -> CoilGeometry:
    """A single closed circular loop as a one-phase CoilGeometry."""
    ang = np.linspace(0.0, 2.0 * np.pi, n_pts + 1)
    pts = np.column_stack([radius * np.cos(ang), radius * np.sin(ang),
                           np.zeros_like(ang)])
    mids = 0.5 * (pts[:-1] + pts[1:])
    dvec = np.diff(pts, axis=0)
    s = mids.shape[0]
    return CoilGeometry(
        midpoints_m=mids, dvec_m=dvec,
        phase=np.zeros(s, dtype=int), direction=np.ones(s),
        is_radial=np.zeros(s, dtype=bool),
        length_per_phase_m=2 * np.pi * radius,
        conductor_area_m2=1e-7, n_turns=1, n_layers=1,
    )


def test_inductance_matches_circular_loop_analytic():
    """Single round loop: L = mu0*a*(ln(8a/r_w) - 2), within the method's ~25%."""
    a = 20e-3
    design = MotorDesign(trace_width_m=1.0e-3, trace_space_m=0.13e-3,
                         copper_weight_oz=1.0, n_stators=1, parallel_paths=1,
                         back_iron=False)
    geo = _loop_geometry(a)
    L = phase_inductance(design, geo)
    # Equivalent round-wire radius for a w x t strip: GMD = 0.2235*(w + t).
    r_w = 0.2235 * (1.0e-3 + 35e-6)
    L_analytic = MU0 * a * (np.log(8 * a / r_w) - 2.0)
    assert 0.75 * L_analytic < L < 1.25 * L_analytic, (L, L_analytic)


def test_inductance_scales_roughly_n_squared():
    """Halving the trace pitch ~doubles turns and ~quadruples L."""
    base = dict(tapered_traces=True, trace_space_m=0.13e-3,
                r_inner_m=19e-3, r_outer_m=44.5e-3, n_stators=2,
                back_iron=False)
    from pcb_motor.coils import build_coil

    d_coarse = MotorDesign(trace_width_m=2.0e-3, **base)
    d_fine = MotorDesign(trace_width_m=0.93e-3, **base)   # ~half the pitch
    g_coarse, g_fine = build_coil(d_coarse), build_coil(d_fine)
    n_ratio = g_fine.n_turns / g_coarse.n_turns
    L_ratio = (phase_inductance(d_fine, g_fine)
               / phase_inductance(d_coarse, g_coarse))
    assert n_ratio >= 1.8
    # L should grow superlinearly with turns, in the n^2 ballpark.
    assert 0.5 * n_ratio ** 2 < L_ratio < 2.0 * n_ratio ** 2


def test_pwm_ripple_and_external_inductor():
    design = MotorDesign(drive_v_bus=12.0, drive_f_pwm_hz=24e3)
    r = pwm_ripple(design, l_phase_h=2e-6, v_drive=1.7, i_cont=4.6)
    # Worst-case (D=0.5): k = v_bus/4 / f_pwm; ripple = k / L.
    k = 12.0 * 0.25 / 24e3
    assert np.isclose(r["pwm_ripple_a_pp"], k / 2e-6)
    assert np.isclose(r["l_ext_h"], k / (0.3 * 4.6) - 2e-6)
    # Worst-case ripple is independent of the operating drive voltage.
    assert pwm_ripple(design, 2e-6, 5.0, 4.6)["pwm_ripple_a_pp"] == r["pwm_ripple_a_pp"]
    # Already-large L needs no external inductor.
    r2 = pwm_ripple(design, l_phase_h=1.0, v_drive=1.7, i_cont=4.6)
    assert r2["l_ext_h"] == 0.0
    # A looser ripple budget lowers the required external inductance.
    design_loose = MotorDesign(drive_v_bus=12.0, drive_f_pwm_hz=24e3,
                               drive_ripple_frac=0.8)
    r3 = pwm_ripple(design_loose, l_phase_h=2e-6, v_drive=1.7, i_cont=4.6)
    assert np.isclose(r3["l_ext_h"], max(0.0, k / (0.8 * 4.6) - 2e-6))


def test_eddy_loss_scales_f_squared_and_is_small_at_gimbal_speed():
    from pcb_motor.coils import build_coil

    base = dict(tapered_traces=True, trace_width_m=2.0e-3,
                trace_space_m=0.13e-3, r_inner_m=19e-3, r_outer_m=44.5e-3)
    d1 = MotorDesign(ref_speed_rev_s=1.0, **base)
    d10 = MotorDesign(ref_speed_rev_s=10.0, **base)
    geo = build_coil(d1)
    p1 = eddy_loss(d1, geo, b_amp_t=0.16)
    p10 = eddy_loss(d10, geo, b_amp_t=0.16)
    assert p1 > 0
    assert np.isclose(p10 / p1, 100.0, rtol=1e-6)
    assert p1 < 0.1   # watts: negligible at 1 rev/s even with wide traces

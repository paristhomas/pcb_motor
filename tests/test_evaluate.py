"""Integration tests for the pcb_motor simulator core.

These exercise ``evaluate_design`` end to end (coil -> field -> Kt -> thermal
-> inertia) with core deps only. The bencher-sweep wiring tests live with
``sweep.py`` (see tests for the workflow modules).
"""

from __future__ import annotations

import math

import pytest

from pcb_motor.design import MotorDesign
from pcb_motor.evaluate import evaluate_design


def test_evaluate_design_objective_is_positive_and_finite():
    r = evaluate_design(MotorDesign())
    assert r["accel_cont_rad_s2"] > 0
    assert r["tau_cont_mNm"] > 0
    assert r["kt_mNm_per_A"] > 0
    for v in r.values():
        if isinstance(v, float):
            assert math.isfinite(v)


def test_total_inertia_adds_load():
    base = evaluate_design(MotorDesign(load_inertia_kgm2=0.0))
    loaded = evaluate_design(MotorDesign(load_inertia_kgm2=1e-4))
    # j_total = j_rotor + load; acceleration drops with added inertia.
    assert loaded["j_total_kgm2"] == pytest.approx(base["j_rotor_kgm2"] + 1e-4, rel=1e-9)
    assert loaded["accel_cont_rad_s2"] < base["accel_cont_rad_s2"]


def test_stronger_magnet_raises_continuous_acceleration():
    weak = evaluate_design(MotorDesign(magnet_grade="N35"))
    strong = evaluate_design(MotorDesign(magnet_grade="N52"))
    assert strong["accel_cont_rad_s2"] > weak["accel_cont_rad_s2"]


def _coarse(**kw) -> MotorDesign:
    """Coarse-but-real design for warning-path tests (fast evaluation)."""
    return MotorDesign(coil_resolution_m=2e-3, commutation_steps=4, **kw)


def test_pwm_ripple_gate_warning_when_over_budget():
    """The ripple gate: an un-drivable winding must WARN, not return silently."""
    r = evaluate_design(_coarse())
    budget = 0.3 * r["i_cont_A"]                 # drive_ripple_frac default
    assert r["pwm_ripple_A_pp"] > budget         # default design fails the gate
    ws = [w for w in r["warnings"] if "PWM ripple" in w]
    assert len(ws) == 1
    w = ws[0]
    # The warning carries the actual numbers + drive context + the remedy.
    assert f"{r['pwm_ripple_A_pp']:.2f} A pp" in w
    assert f"{budget:.2f} A" in w
    assert f"{r['pwm_ripple_A_pp'] / budget:.0f}x" in w
    assert "12 V bus" in w and "24 kHz" in w
    assert f"~{r['l_ext_uH']:.0f} uH/phase" in w
    assert "Stage 5" in w


def test_no_pwm_ripple_warning_within_budget():
    # A huge ripple budget makes the same design pass the gate: no warning.
    r = evaluate_design(_coarse(drive_ripple_frac=50.0))
    assert not [w for w in r["warnings"] if "PWM ripple" in w]


def test_hot_neck_current_density_warning():
    hot = evaluate_design(_coarse(h_conv=200.0))   # forces I_cont (and J) way up
    assert hot["current_density_A_mm2"] > 80.0
    assert any("hot neck" in w for w in hot["warnings"])

    base = evaluate_design(_coarse())              # ~60 A/mm^2: below the nudge
    assert base["current_density_A_mm2"] < 80.0
    assert not any("hot neck" in w for w in base["warnings"])

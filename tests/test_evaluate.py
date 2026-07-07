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

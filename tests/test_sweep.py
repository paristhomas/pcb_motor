"""Tests for the pcb_motor.sweep bencher wiring (optional dependency).

The whole module skips cleanly when holobench is not installed
(``pip install "pcb-motor[sweep]"``); the simulator core needs none of it.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

pytest.importorskip("bencher", reason="holobench not installed "
                    "(pip install 'pcb-motor[sweep]')")

from pcb_motor.design import MotorDesign
from pcb_motor.evaluate import evaluate_design


def test_param_set_complete():
    """Every MotorDesign field is reachable from the bencher input map (guards
    against silently-added/dropped design fields)."""
    from pcb_motor.sweep import _INPUT_MAP
    design_fields = {f.name for f in dataclasses.fields(MotorDesign)}
    mapped = {dfield for _, (dfield, _) in _INPUT_MAP.items()}
    assert mapped == design_fields, (
        f"missing from sweep: {design_fields - mapped}; "
        f"unknown in sweep: {mapped - design_fields}"
    )


def test_worker_matches_direct_evaluate():
    from pcb_motor.sweep import MotorSweep
    s = MotorSweep()
    s()  # evaluate at defaults
    r = evaluate_design(MotorDesign())
    assert s.accel_cont == pytest.approx(r["accel_cont_rad_s2"], rel=1e-9)
    assert s.tau_cont == pytest.approx(r["tau_cont_mNm"], rel=1e-9)
    assert s.kt == pytest.approx(r["kt_mNm_per_A"], rel=1e-9)


def test_build_single_point_report_headless(tmp_path):
    from pcb_motor.sweep import build_dashboard
    path = build_dashboard(input_vars=[], out_dir=str(tmp_path), serve=False, cache=False)
    assert path is not None
    assert os.path.exists(path) and os.path.getsize(path) > 1000


def test_build_dashboard_rejects_unknown_input():
    from pcb_motor.sweep import build_dashboard
    with pytest.raises(ValueError):
        build_dashboard(input_vars=["not_a_real_param"])

"""Tests for pcb_motor.session — session I/O, datasheet, compare."""

from __future__ import annotations

import dataclasses

from pcb_motor.design import MotorDesign
from pcb_motor.session import Session, compare, datasheet


def test_session_motor_roundtrip(tmp_path):
    s = Session("cand-a", root=tmp_path)
    design = MotorDesign(trace_width_m=2.0e-4, magnet_grade="N52", n_slots=18)
    s.save_motor(design)
    assert s.exists()
    loaded = s.load_motor()
    assert dataclasses.asdict(loaded) == dataclasses.asdict(design)


def test_session_requirements_roundtrip_and_listing(tmp_path):
    s = Session("cand-b", root=tmp_path)
    s.save_requirements("target_torque_mNm: 20\nsupply_voltage_V: 12\n")
    s.save_motor(MotorDesign())
    back = s.load_requirements()
    assert back is not None
    assert "target_torque_mNm" in back
    names = [x.name for x in Session.list_all(root=tmp_path)]
    assert names == ["cand-b"]


def test_session_requirements_absent_returns_none(tmp_path):
    s = Session("no-reqs", root=tmp_path)
    s.save_motor(MotorDesign())
    assert s.load_requirements() is None


def test_datasheet_contains_headline_labels():
    design = MotorDesign(magnet_grade="N42")
    sheet = datasheet(design)
    assert sheet.startswith("# Motor design datasheet")
    assert "Kt (torque constant)" in sheet
    assert "Continuous acceleration" in sheet
    assert "Magnets" in sheet
    # Magnet grade (the "magnet type" the user asked for) is present.
    assert "N42" in sheet


def test_compare_one_column_per_design():
    a = MotorDesign(magnet_grade="N42", trace_width_m=1.5e-4)
    b = MotorDesign(magnet_grade="N52", trace_width_m=2.5e-4)
    table = compare([a, b])
    lines = table.strip().splitlines()
    header = lines[0]
    # Two design columns + the parameter column.
    assert header.count("|") == 4
    # One row per headline metric is present.
    assert any(row.startswith("| Kt (torque constant)") for row in lines)
    assert any(row.startswith("| Continuous acceleration") for row in lines)


def test_compare_by_session_name(tmp_path):
    sa = Session("a", root=tmp_path)
    sa.save_motor(MotorDesign(magnet_grade="N42"))
    sb = Session("b", root=tmp_path)
    sb.save_motor(MotorDesign(magnet_grade="N52"))
    table = compare([Session("a", root=tmp_path), Session("b", root=tmp_path)])
    assert "| Parameter | a | b |" in table

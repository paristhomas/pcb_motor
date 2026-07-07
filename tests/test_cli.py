"""Tests for the pcb-motor CLI workflow subcommands (no bencher needed)."""

from __future__ import annotations

from pcb_motor.cli import main
from pcb_motor.session import Session


def test_new_saves_session(tmp_path, capsys):
    rc = main(["new", "--session", "cand", "--root", str(tmp_path),
               "--set", "pole_pairs=7", "--set", "n_slots=12"])
    assert rc == 0
    s = Session("cand", root=tmp_path)
    assert s.exists()
    d = s.load_motor()
    assert d.pole_pairs == 7
    assert d.n_slots == 12
    # The point summary is printed.
    assert "Continuous acceleration" in capsys.readouterr().out


def test_report_and_datasheet_from_session(tmp_path):
    main(["new", "--session", "cand", "--root", str(tmp_path)])
    s = Session("cand", root=tmp_path)

    assert main(["report", "--session", "cand", "--root", str(tmp_path)]) == 0
    assert s.report_html.exists()
    assert s.report_html.read_text(encoding="utf-8").startswith("<!doctype html>")

    assert main(["datasheet", "--session", "cand", "--root", str(tmp_path)]) == 0
    md = s.datasheet_md.read_text(encoding="utf-8")
    assert md.startswith("# Motor design datasheet")
    assert "Kt (torque constant)" in md


def test_export_from_session(tmp_path):
    main(["new", "--session", "cand", "--root", str(tmp_path)])
    out = tmp_path / "coil.kicad_mod"
    rc = main(["export", "--session", "cand", "--root", str(tmp_path),
               "--single-coil", "--out", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8").count("(fp_line ") > 0


def test_compare_two_sessions(tmp_path, capsys):
    main(["new", "--session", "a", "--root", str(tmp_path),
          "--set", "magnet_grade=N42"])
    main(["new", "--session", "b", "--root", str(tmp_path),
          "--set", "magnet_grade=N52"])
    rc = main(["compare", "a", "b", "--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "| Parameter | a | b |" in out


COARSE = ["--set", "coil_resolution_m=2e-3", "--set", "commutation_steps=4"]


def test_point_prints_warnings_prominently(capsys):
    """The default design fails the PWM-ripple gate; `point` must shout it."""
    rc = main(["point"] + COARSE)
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNINGS (" in out
    assert "!!!!" in out                       # the warning banner
    assert "PWM ripple" in out and "Stage 5" in out


def test_fields_lists_grouped_design_fields(capsys):
    import dataclasses

    from pcb_motor.design import MotorDesign

    rc = main(["fields"])
    assert rc == 0
    out = capsys.readouterr().out
    # groups from the design.py section headers
    assert "swept design variables" in out
    assert "fixed context: rotor" in out
    # inline comments from design.py are surfaced
    assert "DC bus voltage [V]" in out
    assert "PWM ripple budget as fraction of i_cont" in out
    # every settable field is listed, with its default
    for f in dataclasses.fields(MotorDesign):
        assert f.name in out
    assert "trace_width_m          = 0.00015" in out


def test_new_writes_requirements_skeleton_once(tmp_path):
    main(["new", "--session", "cand", "--root", str(tmp_path)] + COARSE)
    s = Session("cand", root=tmp_path)
    req = s.load_requirements()
    assert req is not None
    assert req.lstrip().startswith("#")        # commented guidance
    for key in ("torque_mNm", "speed_rev_s", "voltage_V",
                "envelope_od_mm", "envelope_axial_mm", "duty"):
        assert key in req
    # an existing requirements file is never clobbered
    s.save_requirements("torque_mNm: 25\n")
    main(["new", "--session", "cand", "--root", str(tmp_path)] + COARSE)
    assert s.load_requirements() == "torque_mNm: 25\n"


def test_footprint_command_single_tooth(tmp_path, capsys):
    main(["new", "--session", "cand", "--root", str(tmp_path)] + COARSE)
    capsys.readouterr()
    rc = main(["footprint", "--session", "cand", "--root", str(tmp_path),
               "--single-tooth", "--resolution-mm", "0.5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert (tmp_path / "cand" / "stator_single_2side.kicad_mod").exists()
    assert "result           PASS" in out
    assert "worst clearance" in out and "need >=" in out
    assert "coils            1 x" in out
    assert "pads             2 (0A .. 0B)" in out
    assert "vias per coil" in out


def test_footprint_command_full_with_project(tmp_path, capsys):
    main(["new", "--session", "cand", "--root", str(tmp_path)] + COARSE)
    capsys.readouterr()
    rc = main(["footprint", "--session", "cand", "--root", str(tmp_path),
               "--resolution-mm", "0.5", "--project"])
    assert rc == 0
    out = capsys.readouterr().out
    assert (tmp_path / "cand" / "stator_full_2side.kicad_mod").exists()
    assert "bridges          6" in out
    kdir = tmp_path / "cand" / "kicad"
    assert (kdir / "pcb_motor_stator.kicad_sch").exists()
    assert (kdir / "pcb_motor.pretty" / "coil_full_2side.kicad_mod").exists()
    assert "KiCad project written to" in out and "PASS" in out


def test_footprint_command_fails_loudly_on_impossible_coil(tmp_path, capsys):
    """A coil whose stitch corridor can't fit a via must exit non-zero with
    the FootprintError message (numbers + remedy), not a traceback."""
    main(["new", "--session", "bad", "--root", str(tmp_path)] + COARSE)
    capsys.readouterr()
    rc = main(["footprint", "--session", "bad", "--root", str(tmp_path),
               "--single-tooth", "--resolution-mm", "0.5",
               "--set", "r_inner_m=2e-3", "--set", "r_outer_m=12e-3"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "FOOTPRINT FAILED:" in err

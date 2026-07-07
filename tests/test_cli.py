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

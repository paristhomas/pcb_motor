"""Gerber export via kicad-cli -- exercised with kicad-cli mocked out, so the
suite runs in environments (like CI / this WSL box) with no KiCad installed."""

import os
import types
import zipfile

import pytest

from pcb_motor.kicad import gerbers


def _write_pcb(tmp_path):
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text("(kicad_pcb (version 20240108) (generator \"pcb_motor\"))\n")
    return str(pcb)


def _fake_cli(monkeypatch, *, version="8.0.4", gerber_rc=0, drill_rc=0):
    """Install a fake kicad-cli: resolvable on PATH, and subprocess.run that
    creates plausible output files instead of invoking a real binary."""
    monkeypatch.setattr(gerbers.shutil, "which", lambda name: "/fake/kicad-cli")

    def fake_run(cmd, **kwargs):
        cp = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "version" in cmd:
            cp.stdout = version + "\n"
            return cp
        out = cmd[cmd.index("--output") + 1].rstrip(os.sep)
        os.makedirs(out, exist_ok=True)
        if "gerbers" in cmd:
            cp.returncode = gerber_rc
            if gerber_rc == 0:
                for name in ("board-F_Cu.gbr", "board-B_Cu.gbr",
                             "board-Edge_Cuts.gbr", "board-job.gbrjob"):
                    open(os.path.join(out, name), "w").write("G04 fake*\n")
            else:
                cp.stderr = "plot failed: bad layer"
        elif "drill" in cmd:
            cp.returncode = drill_rc
            if drill_rc == 0:
                open(os.path.join(out, "board.drl"), "w").write("M48\n")
            else:
                cp.stderr = "drill failed"
        return cp

    monkeypatch.setattr(gerbers.subprocess, "run", fake_run)


def test_missing_kicad_cli_raises_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(gerbers.shutil, "which", lambda name: None)
    pcb = _write_pcb(tmp_path)
    with pytest.raises(gerbers.GerberError) as ei:
        gerbers.export_gerbers(pcb)
    assert "kicad-cli not found" in str(ei.value)


def test_missing_board_raises(tmp_path, monkeypatch):
    _fake_cli(monkeypatch)
    with pytest.raises(gerbers.GerberError):
        gerbers.export_gerbers(str(tmp_path / "nope.kicad_pcb"))


def test_export_produces_zip_and_report(tmp_path, monkeypatch):
    _fake_cli(monkeypatch)
    pcb = _write_pcb(tmp_path)
    rep = gerbers.export_gerbers(pcb)

    assert rep.available and rep.ok
    assert rep.kicad_version.startswith("8")
    # standard 2-layer set landed + drill
    assert any(f.endswith("F_Cu.gbr") for f in rep.files)
    assert any(f.endswith(".drl") for f in rep.files)
    assert any(f.endswith(".gbrjob") for f in rep.files)
    # zip exists next to the board and contains exactly the exported files
    assert rep.zip_path and os.path.exists(rep.zip_path)
    assert rep.zip_path.endswith("board_gerbers.zip")
    with zipfile.ZipFile(rep.zip_path) as zf:
        assert set(zf.namelist()) == set(rep.files)
    assert "PASS" in str(rep)


def test_gerber_failure_surfaces_stderr(tmp_path, monkeypatch):
    _fake_cli(monkeypatch, gerber_rc=2)
    pcb = _write_pcb(tmp_path)
    with pytest.raises(gerbers.GerberError) as ei:
        gerbers.export_gerbers(pcb)
    assert "bad layer" in str(ei.value)


def test_custom_out_dir_and_zip_path(tmp_path, monkeypatch):
    _fake_cli(monkeypatch)
    pcb = _write_pcb(tmp_path)
    out = tmp_path / "gbr"
    zp = tmp_path / "custom.zip"
    rep = gerbers.export_gerbers(pcb, str(out), zip_path=str(zp))
    assert rep.out_dir == str(out)
    assert rep.zip_path == str(zp) and os.path.exists(zp)

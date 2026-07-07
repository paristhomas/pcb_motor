"""Tests for pcb_motor.kicad.export -- coil polylines -> KiCad footprint text."""

from __future__ import annotations

import numpy as np

from pcb_motor.coils import build_coil
from pcb_motor.design import MotorDesign
from pcb_motor.kicad.export import (
    coil_to_kicad_mod,
    write_coil_kicad_mod,
    _first_sector_polylines,
    _layer0_polylines,
)


def _front_layer_coil():
    """A real built coil restricted to the front (rotor-facing) copper layer."""
    design = MotorDesign()
    geo = build_coil(design)
    polylines = _layer0_polylines(geo.polylines)
    assert polylines, "expected at least one polyline on the front layer"
    return design, polylines


def test_coil_to_kicad_mod_is_parseable_footprint():
    design, polylines = _front_layer_coil()
    text = coil_to_kicad_mod(polylines, design.trace_width_m)

    # Footprint envelope + at least one copper graphic line.
    assert text.startswith('(footprint "')
    assert text.rstrip().endswith(")")
    assert text.count("(fp_line ") > 0
    # Balanced parentheses (cheap sanity that the s-expr is well formed).
    assert text.count("(") == text.count(")")
    # The trace width [mm] is stamped on every stroke.
    w_mm = design.trace_width_m * 1e3
    assert f"(width {w_mm:.4f})" in text


def test_write_coil_kicad_mod_returns_segment_count(tmp_path):
    design, polylines = _front_layer_coil()
    out = tmp_path / "coil.kicad_mod"
    n_lines = write_coil_kicad_mod(str(out), polylines, design.trace_width_m, layer="F.Cu")

    text = out.read_text(encoding="utf-8")
    assert n_lines > 0
    assert n_lines == text.count("(fp_line ")
    assert '(layer "F.Cu")' in text


def test_write_coil_kicad_mod_is_crlf(tmp_path):
    """KiCad writers must emit CRLF (the user's KiCad saves CRLF; mixed line
    endings turn every in-KiCad save into a whole-file diff)."""
    design, polylines = _front_layer_coil()
    out = tmp_path / "coil.kicad_mod"
    write_coil_kicad_mod(str(out), polylines, design.trace_width_m)

    raw = out.read_bytes()
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"").find(b"\n") == -1, "found a bare LF"


def test_layer0_keeps_single_plane():
    _, polylines = _front_layer_coil()
    z0 = min(float(np.asarray(pl)[0, 2]) for pl in polylines)
    # Every kept polyline starts on the same (nearest) copper plane.
    assert all(np.isclose(float(np.asarray(pl)[0, 2]), z0) for pl in polylines)


def test_first_sector_is_subset():
    design = MotorDesign()
    geo = build_coil(design)
    full = _layer0_polylines(geo.polylines)
    sector = _first_sector_polylines(full, design.n_slots)
    # One tooth's worth of traces is a non-empty subset of the whole layer.
    assert 0 < len(sector) <= len(full)


def test_flip_y_negates_y_axis():
    pl = [np.array([[0.0, 0.01, 0.0], [0.0, 0.02, 0.0]])]
    flipped = coil_to_kicad_mod(pl, 1e-4, flip_y=True)
    straight = coil_to_kicad_mod(pl, 1e-4, flip_y=False)
    # KiCad Y points down: flip_y emits negative Y, the unflipped form positive.
    assert "-10.0000" in flipped
    assert "10.0000" in straight

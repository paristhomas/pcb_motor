"""Tests for pcb_motor.coils (winding geometry, star-of-slots layout, resistance)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from pcb_motor.constants import ALPHA_CU, COPPER_THICKNESS, RHO_CU_20
from pcb_motor.coils import build_coil, phase_resistance
from pcb_motor.design import MotorDesign


def test_verified_layout_24n28p():
    """24N28P is a verified concentrated combo (the 12N14P family tiled twice),
    not the round-robin fallback that collapses kw1 -> 0."""
    from pcb_motor.coils import _LAYOUTS, _coil_layout, winding_factor

    assert _LAYOUTS[24] == _LAYOUTS[12] * 2
    assert _coil_layout(24, 3) == _LAYOUTS[24]        # tabulated, not fallback
    d = dataclasses.replace(MotorDesign(), n_slots=24, pole_pairs=14)
    assert winding_factor(d) >= 0.9                   # ~0.933


def test_turns_fit():
    """radial_spoke: n_turns matches the documented mean-radius packing rule,
    and widening the trace reduces the turn count."""
    d = dataclasses.replace(MotorDesign(), winding_topology="radial_spoke")
    geo = build_coil(d)

    # Reconstruct the fitting rule.
    r_mean = 0.5 * (d.r_inner_m + d.r_outer_m)
    pitch = d.trace_width_m + d.trace_space_m
    n_cond = int(np.floor(2 * np.pi * r_mean / pitch))
    turns_per_layer_per_phase = (n_cond // 2) // d.n_phases
    expected = turns_per_layer_per_phase * d.copper_layers

    assert geo.n_turns == expected
    assert geo.n_layers == d.copper_layers

    # Wider trace -> fewer turns.
    d_wide = dataclasses.replace(d, trace_width_m=d.trace_width_m * 3)
    geo_wide = build_coil(d_wide)
    assert geo_wide.n_turns < geo.n_turns


def test_concentrated_winding_12n14p():
    """concentrated (default): n_slots discrete coils grouped into n_phases,
    turns/coil set by the slot angular width; widening the trace cuts turns."""
    d = MotorDesign()  # default: concentrated, 12 slots, 14 poles
    assert d.winding_topology == "concentrated"
    geo = build_coil(d)

    coils_per_phase = d.n_slots // d.n_phases
    turns_per_coil = geo.n_turns // (coils_per_phase * d.copper_layers)
    assert turns_per_coil >= 1
    assert geo.n_turns == turns_per_coil * coils_per_phase * d.copper_layers
    assert set(np.unique(geo.phase)) == set(range(d.n_phases))

    # Wider trace -> fewer turns per coil.
    d_wide = dataclasses.replace(d, trace_width_m=d.trace_width_m * 3)
    assert build_coil(d_wide).n_turns < geo.n_turns


def test_winding_factor_12n14p():
    """The star-of-slots winding factor for 12 slots / 14 poles is ~0.93."""
    from pcb_motor.coils import winding_factor
    kw = winding_factor(MotorDesign())  # default 12 slots, 7 pole-pairs
    assert 0.90 < kw < 0.97


def test_winding_factor_36n42p():
    """The star-of-slots winding factor for 36 slots / 42 poles is ~0.93.

    36N42P is the 6N7P family tiled six times (== the 12N14P pattern tiled
    three times) -- the demo motor's winding. Without the explicit _LAYOUTS
    entry the round-robin fallback puts same-phase coils in antiphase and kw
    collapses to ~0, so this locks the layout in.
    """
    from pcb_motor.coils import winding_factor
    d = dataclasses.replace(MotorDesign(), n_slots=36, pole_pairs=21)
    kw = winding_factor(d)
    assert 0.90 < kw < 0.97
    # With full-pitch coil sides (no trace-space inset) kw1 is the textbook
    # 0.933 of the 6N7P family, same as 12N14P.
    d_full = dataclasses.replace(d, trace_space_m=0.0)
    kw_full = winding_factor(d_full)
    assert 0.92 < kw_full < 0.94


def test_concentrated_coils_do_not_overlap_radially():
    """Within one coil's continuous spiral, successive turns nest inward: the
    outer arcs/crossovers occupy distinct radii spanning several pitches (not the
    old bug where every arc was stacked at the same radius)."""
    d = MotorDesign()
    geo = build_coil(d)
    pitch = d.trace_width_m + d.trace_space_m
    r_mean = 0.5 * (d.r_inner_m + d.r_outer_m)

    # First coil is one polyline. Gather the radii of its outer-half tangential
    # segments (the per-turn outer arcs / crossovers); they should form a comb of
    # distinct radii stepping inward by ~one pitch, not a single coincident value.
    pl = np.asarray(geo.polylines[0], dtype=float)
    r = np.hypot(pl[:, 0], pl[:, 1])
    phi = np.unwrap(np.arctan2(pl[:, 1], pl[:, 0]))
    r_mid = 0.5 * (r[:-1] + r[1:])
    tangential = np.abs(r_mid * np.diff(phi)) > np.abs(np.diff(r))
    outer = tangential & (r_mid > r_mean)
    bands = sorted({round(float(rm / pitch)) for rm in r_mid[outer]})
    assert len(bands) >= 3
    assert (max(r_mid[outer]) - min(r_mid[outer])) > 2 * pitch


def test_geometry_arrays_consistent():
    d = MotorDesign()
    geo = build_coil(d)

    s = geo.midpoints_m.shape[0]
    assert geo.midpoints_m.shape == (s, 3)
    assert geo.dvec_m.shape == (s, 3)
    assert geo.phase.shape == (s,)
    assert geo.direction.shape == (s,)
    assert geo.is_radial.shape == (s,)
    assert s > 0

    # Non-degenerate segments.
    norms = np.linalg.norm(geo.dvec_m, axis=1)
    assert np.all(norms > 0)

    # Midpoint radii within annulus (small tolerance for arc chord sagitta).
    radii = np.linalg.norm(geo.midpoints_m[:, :2], axis=1)
    tol = 1e-3
    assert radii.min() >= d.r_inner_m - tol
    assert radii.max() <= d.r_outer_m + tol

    # Phases and directions valid.
    assert set(np.unique(geo.phase)).issubset(set(range(d.n_phases)))
    assert set(np.unique(geo.direction)).issubset({-1.0, 1.0})


def test_resistance_formula():
    d = MotorDesign()
    geo = build_coil(d)

    area = d.trace_width_m * COPPER_THICKNESS[d.copper_weight_oz]
    assert geo.conductor_area_m2 == pytest.approx(area)

    # Cold (20 C / None).
    r_cold = phase_resistance(d, geo)
    expected_cold = RHO_CU_20 * geo.length_per_phase_m / area / d.parallel_paths**2
    assert r_cold == pytest.approx(expected_cold)

    # Explicit 20 C equals None.
    assert phase_resistance(d, geo, 20.0) == pytest.approx(r_cold)

    # Hot > cold.
    r_hot = phase_resistance(d, geo, 100.0)
    rho_hot = RHO_CU_20 * (1 + ALPHA_CU * (100.0 - 20.0))
    expected_hot = rho_hot * geo.length_per_phase_m / area / d.parallel_paths**2
    assert r_hot == pytest.approx(expected_hot)
    assert r_hot > r_cold

    # Parallel paths reduce R by the square.
    d2 = dataclasses.replace(d, parallel_paths=2)
    geo2 = build_coil(d2)
    r2 = phase_resistance(d2, geo2)
    # length/area unchanged by parallel_paths in our model -> exactly /4.
    assert r2 == pytest.approx(
        RHO_CU_20 * geo2.length_per_phase_m / area / 4
    )


def test_end_turn_fraction():
    d = MotorDesign()
    geo = build_coil(d)

    f = geo.end_turn_fraction
    assert 0.0 <= f < 1.0
    # Radial conductors should dominate for the default tall, thin annulus.
    assert f < 0.5


def test_corner_rounding_changes_polylines_only():
    """A positive corner_radius rounds the drawn polylines (more vertices, same
    bounding annulus) without touching the physics arrays."""
    d_sharp = dataclasses.replace(MotorDesign(), corner_radius_m=0.0)
    d_round = dataclasses.replace(MotorDesign(), corner_radius_m=0.2e-3)
    geo_s = build_coil(d_sharp)
    geo_r = build_coil(d_round)

    # Physics segment arrays are identical (rounding is plot/export only).
    assert geo_s.midpoints_m.shape == geo_r.midpoints_m.shape
    assert np.allclose(geo_s.midpoints_m, geo_r.midpoints_m)
    assert geo_s.length_per_phase_m == pytest.approx(geo_r.length_per_phase_m)

    # Rounding adds vertices (fillet samples) to at least some polylines.
    assert sum(len(p) for p in geo_r.polylines) > sum(len(p) for p in geo_s.polylines)

    # Rounded geometry stays within the annulus, give or take a fillet radius
    # (rounding a concave inner corner pulls it inward by up to ~the radius).
    rr = np.concatenate([np.linalg.norm(p[:, :2], axis=1) for p in geo_r.polylines])
    assert rr.max() <= d_round.r_outer_m + d_round.corner_radius_m
    assert rr.min() >= d_round.r_inner_m - d_round.corner_radius_m


def test_round_corners_square():
    """A sharp square loses its 90-degree corners but keeps its extent."""
    from pcb_motor.coils import _round_corners

    sq = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 0]], float)
    out = _round_corners(sq, radius=0.2)
    # No output vertex sits exactly on an original sharp corner.
    corners = sq[1:4, :2]
    for c in corners:
        assert np.min(np.linalg.norm(out[:, :2] - c, axis=1)) > 1e-6
    # Closed loop preserved and stays inside the unit square.
    assert np.allclose(out[0], out[-1])
    assert out[:, 0].min() >= -1e-9 and out[:, 0].max() <= 1 + 1e-9
    assert out[:, 1].min() >= -1e-9 and out[:, 1].max() <= 1 + 1e-9


def test_kicad_export_roundtrip():
    """The KiCad exporter emits one fp_line per polyline segment at the trace
    width, on the requested copper layer."""
    from pcb_motor.kicad.export import _layer0_polylines, coil_to_kicad_mod

    d = MotorDesign()
    geo = build_coil(d)
    pls = _layer0_polylines(geo.polylines)
    text = coil_to_kicad_mod(pls, d.trace_width_m, layer="F.Cu")

    assert text.startswith("(footprint")
    assert text.rstrip().endswith(")")
    expected_lines = sum(max(0, len(p) - 1) for p in pls)
    assert text.count("(fp_line ") == expected_lines
    assert f"(width {d.trace_width_m*1e3:.4f})" in text
    assert '(layer "F.Cu")' in text


def test_topology_switch():
    d = dataclasses.replace(MotorDesign(), winding_topology="spiral")
    geo = build_coil(d)

    s = geo.midpoints_m.shape[0]
    assert s > 0
    assert geo.dvec_m.shape == (s, 3)
    assert geo.phase.shape == (s,)
    assert np.all(np.linalg.norm(geo.dvec_m, axis=1) > 0)
    assert geo.length_per_phase_m > 0
    assert geo.n_turns >= 1
    # Resistance still computes for the spiral.
    assert phase_resistance(d, geo) > 0


def test_coil_current_source_energises_and_fields():
    """Energised coils build a valid CurrentSource that makes a nonzero B field."""
    from pcb_motor.coils import coil_current_source
    from pcb_motor.field import b_field_at_points

    d = MotorDesign()
    src = coil_current_source(d, [1.0, -0.5, -0.5])
    assert src.vertices.shape[0] > 0
    assert src.vertices.shape[0] == src.currents.shape[0]
    assert np.all(np.isfinite(src.vertices))
    assert np.any(src.currents != 0.0)

    # Field just above the copper, at the mean radius, is nonzero.
    r_mean = 0.5 * (d.r_inner_m + d.r_outer_m)
    z = d.rotor().stator_z_m()
    b = b_field_at_points(src, np.array([[r_mean, 0.0, z - 0.3e-3]]))
    assert np.linalg.norm(b) > 0.0

    # Zero current everywhere => zero field source contribution.
    src0 = coil_current_source(d, [0.0, 0.0, 0.0])
    b0 = b_field_at_points(src0, np.array([[r_mean, 0.0, z - 0.3e-3]]))
    assert np.linalg.norm(b0) == 0.0

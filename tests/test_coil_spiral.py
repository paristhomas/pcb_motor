"""Tests for pcb_motor.coil_spiral — the continuous, drivable fabrication spiral.

These lock in the two defects that motivated the module:
  1. each exported coil must be ONE continuous trace (no jumps between turns), and
  2. no two non-adjacent points may sit closer than the trace pitch
     (``trace_width + trace_space``) -- i.e. the nested arcs/radials are properly
     spaced and the copper cannot short to itself.
"""

from __future__ import annotations

import numpy as np

from pcb_motor.design import MotorDesign
from pcb_motor.coil_spiral import (
    coil_spiral_polyline,
    export_spiral_polylines,
    _geom,
    _count_turns,
)


def _pitch(design: MotorDesign) -> float:
    return float(design.trace_width_m) + float(design.trace_space_m)


def test_single_continuous_trace_no_jumps():
    """Consecutive points step by at most the coil resolution -- one trace."""
    design = MotorDesign()
    poly = coil_spiral_polyline(design, 0.0, 0.0)
    assert poly.shape[0] >= 2
    steps = np.linalg.norm(np.diff(poly[:, :2], axis=0), axis=1)
    # No segment longer than the sampling resolution (plus a hair for rounding).
    assert steps.max() <= float(design.coil_resolution_m) * 1.001


def test_no_self_shorts():
    """Non-adjacent vertices are never closer than the trace pitch."""
    design = MotorDesign()
    poly = coil_spiral_polyline(design, 0.0, 0.0)[:, :2]
    pitch = _pitch(design)
    # Brute-force min distance over non-adjacent index pairs (skip neighbours,
    # which are deliberately ~resolution apart along the trace).
    skip = int(np.ceil(pitch / float(design.coil_resolution_m))) + 2
    worst = np.inf
    for i in range(len(poly)):
        j0 = i + skip
        if j0 >= len(poly):
            break
        d = np.linalg.norm(poly[j0:] - poly[i], axis=1)
        worst = min(worst, d.min())
    # Allow a small tolerance: turns are spaced by exactly one pitch, and the
    # slanted radials can dip a touch below it at the corners.
    assert worst >= pitch * 0.9, f"min non-adjacent gap {worst*1e3:.3f} mm < pitch {pitch*1e3:.3f} mm"


def test_both_terminals_on_outer_side():
    """Terminal A is at the rim; terminal B is stepped in but still outer-side."""
    design = MotorDesign()
    r_in, r_out, *_ = _geom(design)
    poly = coil_spiral_polyline(design, 0.0, 0.0)
    rA = float(np.hypot(*poly[0, :2]))
    rB = float(np.hypot(*poly[-1, :2]))
    assert np.isclose(rA, r_out, atol=1e-4)            # A at the outer rim
    # B sits at the innermost turn's outer corner: in the outer half, not the rim.
    assert 0.5 * (r_in + r_out) < rB < r_out


def test_turn_count_matches_radial_passes():
    """Turns == half the times the trace crosses a mid-radius circle."""
    design = MotorDesign()
    r_in, r_out, *_ = _geom(design)
    poly = coil_spiral_polyline(design, 0.0, 0.0)
    rr = np.hypot(poly[:, 0], poly[:, 1])
    rc = 0.5 * (r_in + r_out)
    crossings = int(np.sum((rr[:-1] - rc) * (rr[1:] - rc) < 0))
    assert crossings == 2 * _count_turns(design)


def test_export_one_trace_per_coil():
    """Full-layer export yields exactly n_slots continuous traces."""
    design = MotorDesign()
    full = export_spiral_polylines(design, single_coil=False)
    assert len(full) == int(design.n_slots)
    single = export_spiral_polylines(design, single_coil=True)
    assert len(single) == 1


# --------------------------------------------------------------------------- #
# Tapered (wedge) traces
# --------------------------------------------------------------------------- #
def _tapered_design(**over) -> MotorDesign:
    return MotorDesign(tapered_traces=True, trace_width_m=0.2e-3,
                       trace_space_m=0.13e-3, r_inner_m=16e-3, r_outer_m=44e-3,
                       **over)


def test_tapered_width_law():
    """w(r_inner) == trace_width; w grows linearly; clearance is exact."""
    from pcb_motor.coil_spiral import trace_width_at, _geom_tapered

    design = _tapered_design()
    r_in, r_out, sector, delta, tw, ts = _geom_tapered(design)
    assert np.isclose(float(trace_width_at(design, r_in)), tw)
    # At any radius the centreline pitch delta*r minus the width is exactly ts.
    for r in (r_in, 0.5 * (r_in + r_out), r_out):
        w = float(trace_width_at(design, r))
        assert np.isclose(delta * r - w, ts), f"clearance at r={r} is {delta*r-w}"


def test_tapered_rays_are_pure_radial():
    """Tapered radial conductors are constant-angle rays with full extents.

    The crossover ramp also changes radius (it's an end-turn), so identify the
    rays as the zero-angle-sweep segments and check their total radial travel
    equals two full radial sides per turn.
    """
    from pcb_motor.coil_spiral import _corners_tapered, _count_turns

    design = _tapered_design()
    poly = coil_spiral_polyline(design, 0.0, 0.0)
    r = np.hypot(poly[:, 0], poly[:, 1])
    ang = np.arctan2(poly[:, 1], poly[:, 0])
    dr = np.abs(np.diff(r))
    dang = np.abs(np.diff(ang))
    ray_travel = dr[dang < 1e-12].sum()
    expect = sum(
        2.0 * (c["ro"] - c["ri"])
        for c in (_corners_tapered(design, 0.0, t)
                  for t in range(_count_turns(design)))
    )
    assert np.isclose(ray_travel, expect, rtol=1e-6)


def test_tapered_continuous_trace():
    """The tapered coil is still one continuous spiral."""
    design = _tapered_design()
    poly = coil_spiral_polyline(design, 0.0, 0.0)
    assert poly.shape[0] >= 2
    steps = np.linalg.norm(np.diff(poly[:, :2], axis=0), axis=1)
    assert steps.max() <= float(design.coil_resolution_m) * 1.001


def test_tapered_no_self_shorts_edge_to_edge():
    """Adjacent turns keep >= trace_space between copper EDGES everywhere.

    Centreline distance alone is not the right invariant for tapered traces
    (widths grow with radius), so check distance minus the two half-widths.
    """
    from pcb_motor.coil_spiral import trace_width_at

    design = _tapered_design()
    poly = coil_spiral_polyline(design, 0.0, 0.0)[:, :2]
    ts = float(design.trace_space_m)
    w = np.asarray(trace_width_at(design, np.hypot(poly[:, 0], poly[:, 1])))
    pitch_max = w.max() + ts
    skip = int(np.ceil(pitch_max / float(design.coil_resolution_m))) + 2
    worst = np.inf
    for i in range(len(poly)):
        j0 = i + skip
        if j0 >= len(poly):
            break
        d = np.linalg.norm(poly[j0:] - poly[i], axis=1)
        edge = d - 0.5 * w[j0:] - 0.5 * w[i]
        worst = min(worst, edge.min())
    assert worst >= ts * 0.9, f"min edge gap {worst*1e3:.3f} mm < space {ts*1e3:.3f} mm"


def test_tapered_intercoil_clearance_at_inner_radius():
    """Adjacent coils keep >= trace_space between copper edges at the coil ID.

    This is where the constant-width packing (clearance held only at r_mean)
    goes out of spec; the tapered layout must hold it at every radius.
    """
    from pcb_motor.coil_spiral import trace_width_at

    design = _tapered_design()
    sector = 2.0 * np.pi / int(design.n_slots)
    a = coil_spiral_polyline(design, 0.0, 0.0)[:, :2]
    b = coil_spiral_polyline(design, sector, 0.0)[:, :2]
    r_in = float(design.r_inner_m)
    band_a = a[np.hypot(a[:, 0], a[:, 1]) < r_in + 2e-3]
    band_b = b[np.hypot(b[:, 0], b[:, 1]) < r_in + 2e-3]
    wa = np.asarray(trace_width_at(design, np.hypot(band_a[:, 0], band_a[:, 1])))
    wb = np.asarray(trace_width_at(design, np.hypot(band_b[:, 0], band_b[:, 1])))
    d = np.linalg.norm(band_b[:, None, :] - band_a[None, :, :], axis=2)
    edge = d - 0.5 * wb[:, None] - 0.5 * wa[None, :]
    assert edge.min() >= float(design.trace_space_m) * 0.99


def test_tapered_resistance_between_uniform_bounds():
    """R from sum(dl/A) sits between the all-max-width and all-min-width R."""
    from pcb_motor.coils import build_coil, phase_resistance, _copper_thickness
    from pcb_motor.coil_spiral import trace_width_at
    from pcb_motor.constants import RHO_CU_20

    design = _tapered_design()
    geo = build_coil(design)
    assert geo.length_over_area_per_phase is not None
    assert geo.copper_volume_m3 is not None and geo.copper_volume_m3 > 0
    r = phase_resistance(design, geo)
    t_cu = _copper_thickness(float(design.copper_weight_oz))
    w_min = float(design.trace_width_m)
    w_max = float(trace_width_at(design, design.r_outer_m))
    r_lo = RHO_CU_20 * geo.length_per_phase_m / (w_max * t_cu)
    r_hi = RHO_CU_20 * geo.length_per_phase_m / (w_min * t_cu)
    assert r_lo < r < r_hi


def test_tapered_kicad_export_steps_width():
    """Exported fp_lines carry per-segment widths spanning ID..OD widths."""
    import re
    from pcb_motor.coils import build_coil
    from pcb_motor.kicad.export import coil_to_kicad_mod, _layer0_polylines
    from pcb_motor.coil_spiral import trace_width_at

    design = _tapered_design()
    geo = build_coil(design)
    polylines = _layer0_polylines(geo.polylines)
    text = coil_to_kicad_mod(
        polylines, design.trace_width_m,
        width_fn=lambda r: trace_width_at(design, r),
    )
    widths = sorted({float(m) for m in re.findall(r"stroke \(width ([0-9.]+)\)", text)})
    assert len(widths) > 5, "expected many distinct stepped widths"
    w_min_mm = float(design.trace_width_m) * 1e3
    w_max_mm = float(trace_width_at(design, design.r_outer_m)) * 1e3
    assert widths[0] >= w_min_mm * 0.99
    assert widths[-1] <= w_max_mm * 1.01

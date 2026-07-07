"""Tests for pcb_motor.viz — figures render headless and export to PNG bytes."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

from pcb_motor.design import CoilGeometry, MotorDesign, RotorConfig
from pcb_motor.viz import (
    fig_to_png_bytes,
    plot_b_field,
    plot_coil_layout,
    plot_motor_config,
    plot_setup,
    plot_stack,
)

PNG_MAGIC = b"\x89PNG"


def _synthetic_coil() -> CoilGeometry:
    """A tiny two-spoke coil with consistent per-segment arrays and polylines."""
    # Two radial spokes (phases 0 and 1) plus a connecting end-turn arc each.
    midpoints_m = np.array(
        [
            [0.015, 0.0, 0.0],   # radial, phase 0
            [0.0, 0.015, 0.0],   # radial, phase 1
            [0.021, 0.021, 0.0],  # end-turn arc
        ],
        dtype=float,
    )
    dvec_m = np.array(
        [
            [0.01, 0.0, 0.0],
            [0.0, 0.01, 0.0],
            [-0.005, 0.005, 0.0],
        ],
        dtype=float,
    )
    phase = np.array([0, 1, 0])
    direction = np.array([1, 1, -1])
    is_radial = np.array([True, True, False])

    polylines = [
        np.array([[0.01, 0.0, 0.0], [0.02, 0.0, 0.0]], dtype=float),
        np.array([[0.0, 0.01, 0.0], [0.0, 0.02, 0.0]], dtype=float),
        np.array([[0.02, 0.0, 0.0], [0.02, 0.02, 0.0]], dtype=float),
    ]

    return CoilGeometry(
        midpoints_m=midpoints_m,
        dvec_m=dvec_m,
        phase=phase,
        direction=direction,
        is_radial=is_radial,
        length_per_phase_m=0.04,
        conductor_area_m2=1.5e-8,
        n_turns=4,
        n_layers=2,
        polylines=polylines,
    )


def _assert_valid_figure(fig) -> None:
    assert isinstance(fig, Figure)
    assert len(fig.axes) >= 1
    png = fig_to_png_bytes(fig)
    assert isinstance(png, bytes)
    assert len(png) > 0
    assert png.startswith(PNG_MAGIC)


def test_plot_coil_layout_with_polylines():
    fig = plot_coil_layout(_synthetic_coil())
    _assert_valid_figure(fig)
    plt.close(fig)


def test_plot_coil_layout_fallback_no_polylines():
    geo = _synthetic_coil()
    geo.polylines = []  # force the midpoints+dvec fallback path
    fig = plot_coil_layout(geo)
    _assert_valid_figure(fig)
    plt.close(fig)


def test_plot_motor_config():
    fig = plot_motor_config(RotorConfig())
    _assert_valid_figure(fig)
    plt.close(fig)


def test_plot_b_field_placeholder():
    """Default path is the fast synthetic placeholder (no field/magnets needed).

    The real Biot-Savart field is correct but far too slow for a unit test
    (seconds per grid point), so it is opt-in via ``use_real_field=True`` and
    deliberately not exercised here.
    """
    fig = plot_b_field(MotorDesign(), n_grid=12)
    _assert_valid_figure(fig)
    plt.close(fig)


def test_plot_stack():
    fig = plot_stack(MotorDesign())
    _assert_valid_figure(fig)
    plt.close(fig)


def test_plot_setup_combined():
    """The per-design setup figure: winding + rotor + axial stack in one image."""
    fig = plot_setup(MotorDesign())
    assert len(fig.axes) >= 3
    _assert_valid_figure(fig)
    plt.close(fig)


def test_fig_to_png_bytes_magic():
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    png = fig_to_png_bytes(fig)
    assert png.startswith(PNG_MAGIC)
    assert len(png) > len(PNG_MAGIC)
    plt.close(fig)


def test_build_design_report_is_self_contained_html(tmp_path):
    """The combined report: one HTML doc with an embedded figure + params table."""
    from pcb_motor.report import render_design_report
    from pcb_motor.design import MotorDesign

    html = render_design_report(MotorDesign())
    assert html.startswith("<!doctype html>")
    # Setup figure is inlined (no external assets) and the params table is present.
    assert "data:image/png;base64," in html
    assert "Key design parameters" in html
    assert "Continuous acceleration" in html

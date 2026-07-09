"""bencher integration: sweep / optimise / single-point dashboards.

Exposes the PCB-coil design knobs as bencher sweep inputs and every simulated
output as a result variable, with the continuous-acceleration objective. The
heavy lifting is in ``evaluate.evaluate_design``; this module only adapts it to
bencher's ``ParametrizedSweep`` and builds the report.

OPTIONAL dependency: requires holobench (``pip install "pcb-motor[sweep]"``).
The simulator core (``evaluate_design``) does not need it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

try:
    import bencher as bn
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError(
        "The sweep/optimize dashboard needs bencher (holobench). "
        'Install it with:  pip install "pcb-motor[sweep]"'
    ) from exc

from . import constants as C

from .design import MotorDesign
from .evaluate import evaluate_design
from . import viz

_GRADES = sorted(C.NDFEB_BR)

# (sweep attr, MotorDesign field, SI scale). mm fields scale by 1e-3.
_INPUT_MAP: dict[str, tuple[str, float | None]] = {
    # --- A. swept design variables ---
    "trace_width_mm": ("trace_width_m", 1e-3),
    "trace_space_mm": ("trace_space_m", 1e-3),
    "r_inner_mm": ("r_inner_m", 1e-3),
    "r_outer_mm": ("r_outer_m", 1e-3),
    "copper_layers": ("copper_layers", 1),
    "parallel_paths": ("parallel_paths", 1),
    "winding_topology": ("winding_topology", None),
    "n_slots": ("n_slots", 1),
    "corner_radius_mm": ("corner_radius_m", 1e-3),
    "tapered_traces": ("tapered_traces", None),
    # --- B. fixed context ---
    "magnet_grade": ("magnet_grade", None),
    "pole_pairs": ("pole_pairs", 1),
    "magnet_thickness_mm": ("magnet_thickness_m", 1e-3),
    "magnet_r_inner_mm": ("magnet_r_inner_m", 1e-3),
    "magnet_r_outer_mm": ("magnet_r_outer_m", 1e-3),
    "pole_coverage": ("pole_coverage", 1.0),
    "air_gap_mm": ("air_gap_m", 1e-3),
    "back_iron": ("back_iron", None),
    "iron_standoff_mm": ("iron_standoff_m", 1e-3),
    "board_thickness_mm": ("board_thickness_m", 1e-3),
    "copper_weight_oz": ("copper_weight_oz", 1.0),
    "n_stators": ("n_stators", 1),
    "carrier_thickness_mm": ("carrier_thickness_m", 1e-3),
    "magnet_topology": ("magnet_topology", None),
    "outer_ring_r_mm": ("outer_ring_r_m", 1e-3),
    "outer_disc_d_mm": ("outer_disc_d_m", 1e-3),
    "mid_ring_r_mm": ("mid_ring_r_m", 1e-3),
    "mid_disc_d_mm": ("mid_disc_d_m", 1e-3),
    "inner_ring_r_mm": ("inner_ring_r_m", 1e-3),
    "inner_disc_d_mm": ("inner_disc_d_m", 1e-3),
    "n_phases": ("n_phases", 1),
    "phase_activity": ("phase_activity", 1.0),
    "loss_phase_factor": ("loss_phase_factor", 1.0),
    "h_conv": ("h_conv", 1.0),
    "temp_limit_c": ("temp_limit_c", 1.0),
    "ambient_c": ("ambient_c", 1.0),
    "cooled_faces": ("cooled_faces", 1),
    "load_inertia_kgm2": ("load_inertia_kgm2", 1.0),
    "coil_resolution_mm": ("coil_resolution_m", 1e-3),
    "commutation_steps": ("commutation_steps", 1),
    "drive_v_bus": ("drive_v_bus", 1.0),
    "drive_f_pwm_khz": ("drive_f_pwm_hz", 1e3),
    "drive_ripple_frac": ("drive_ripple_frac", 1.0),
    "ref_speed_rev_s": ("ref_speed_rev_s", 1.0),
}

# (result attr on sweep, evaluate_design key, units)
_RESULT_MAP: list[tuple[str, str, str]] = [
    ("accel_cont", "accel_cont_rad_s2", "rad/s^2"),  # objective
    ("tau_cont", "tau_cont_mNm", "mNm"),
    ("kt", "kt_mNm_per_A", "mNm/A"),
    ("i_cont", "i_cont_A", "A"),
    ("j_rotor", "j_rotor_kgm2", "kg*m^2"),
    ("j_total", "j_total_kgm2", "kg*m^2"),
    ("r_phase_20c", "r_phase_20c_ohm", "ohm"),
    ("r_phase_hot", "r_phase_hot_ohm", "ohm"),
    ("b_gap_mean", "b_gap_mean_T", "T"),
    ("b_gap_peak", "b_gap_peak_T", "T"),
    ("copper_loss", "copper_loss_W", "W"),
    ("n_turns", "n_turns", "turns"),
    ("conductor_length", "conductor_length_m", "m"),
    ("conductor_area", "conductor_area_mm2", "mm^2"),
    ("copper_mass", "copper_mass_g", "g"),
    ("current_density", "current_density_A_mm2", "A/mm^2"),
    ("v_drive_cont", "v_drive_cont_V", "V"),
    ("shear_stress", "shear_stress_kPa", "kPa"),
    ("torque_density", "torque_density_Nm_kg", "Nm/kg"),
    ("end_turn_fraction", "end_turn_fraction", "-"),
    ("torque_ripple", "torque_ripple", "-"),
    ("winding_factor", "winding_factor", "-"),
    ("winding_utilisation", "winding_utilisation", "-"),
]


def _make_inputs() -> dict:
    """Build the bencher sweep-input class attributes."""
    return {
        # A. swept knobs (sensible exploration bounds)
        "trace_width_mm": bn.FloatSweep(default=0.15, bounds=[0.1, 1.7], samples=5, units="mm"),
        "trace_space_mm": bn.FloatSweep(default=0.15, bounds=[0.1, 0.5], samples=6, units="mm"),
        "r_inner_mm": bn.FloatSweep(default=10.0, bounds=[4, 28], samples=6, units="mm"),
        "r_outer_mm": bn.FloatSweep(default=30.0, bounds=[12, 60], samples=6, units="mm"),
        "copper_layers": bn.IntSweep(default=2, bounds=[1, 8], units="layers"),
        "parallel_paths": bn.IntSweep(default=1, bounds=[1, 8], units="paths"),
        "winding_topology": bn.StringSweep(["concentrated", "radial_spoke", "spiral"], default="concentrated"),
        "n_slots": bn.IntSweep(default=12, bounds=[3, 24], units="slots"),
        "corner_radius_mm": bn.FloatSweep(default=0.15, bounds=[0.0, 0.5], samples=4, units="mm"),
        "tapered_traces": bn.BoolSweep(default=False),
        # B. fixed context (defaults; promote to an axis by listing in input_vars)
        "magnet_grade": bn.StringSweep(_GRADES, default="N42"),
        "pole_pairs": bn.IntSweep(default=7, bounds=[2, 16], units="pole-pairs"),
        "magnet_thickness_mm": bn.FloatSweep(default=3.0, bounds=[1, 5], samples=5, units="mm"),
        "magnet_r_inner_mm": bn.FloatSweep(default=10.0, bounds=[4, 28], samples=5, units="mm"),
        "magnet_r_outer_mm": bn.FloatSweep(default=30.0, bounds=[12, 60], samples=5, units="mm"),
        "pole_coverage": bn.FloatSweep(default=0.85, bounds=[0.5, 1.0], samples=5, units=""),
        "air_gap_mm": bn.FloatSweep(default=1.0, bounds=[0.3, 3.0], samples=5, units="mm"),
        "back_iron": bn.BoolSweep(default=False),
        "iron_standoff_mm": bn.FloatSweep(default=0.0, bounds=[0.0, 2.0], samples=3, units="mm"),
        "board_thickness_mm": bn.FloatSweep(default=0.8, bounds=[0.4, 1.6], samples=4, units="mm"),
        "copper_weight_oz": bn.FloatSweep(default=1.0, bounds=[0.5, 2.0], samples=3, units="oz"),
        "n_stators": bn.IntSweep(default=2, bounds=[1, 2], units="stators"),
        "carrier_thickness_mm": bn.FloatSweep(default=1.5, bounds=[0.5, 3.0], samples=4, units="mm"),
        "magnet_topology": bn.StringSweep(["arc", "round", "round3", "round_outer", "round_inner"], default="arc"),
        "outer_ring_r_mm": bn.FloatSweep(default=37.2, bounds=[20, 50], samples=4, units="mm"),
        "outer_disc_d_mm": bn.FloatSweep(default=15.0, bounds=[5, 20], samples=4, units="mm"),
        "mid_ring_r_mm": bn.FloatSweep(default=29.0, bounds=[15, 45], samples=4, units="mm"),
        "mid_disc_d_mm": bn.FloatSweep(default=10.0, bounds=[3, 16], samples=4, units="mm"),
        "inner_ring_r_mm": bn.FloatSweep(default=20.6, bounds=[10, 30], samples=4, units="mm"),
        "inner_disc_d_mm": bn.FloatSweep(default=8.0, bounds=[3, 12], samples=4, units="mm"),
        "n_phases": bn.IntSweep(default=3, bounds=[3, 3], units="phases"),
        "phase_activity": bn.FloatSweep(default=0.667, bounds=[0.5, 1.0], samples=4, units=""),
        "loss_phase_factor": bn.FloatSweep(default=1.5, bounds=[1.0, 2.0], samples=3, units=""),
        "h_conv": bn.FloatSweep(default=15.0, bounds=[5, 50], samples=5, units="W/m^2K"),
        "temp_limit_c": bn.FloatSweep(default=100.0, bounds=[60, 125], samples=5, units="C"),
        "ambient_c": bn.FloatSweep(default=25.0, bounds=[0, 50], samples=3, units="C"),
        "cooled_faces": bn.IntSweep(default=2, bounds=[1, 2], units="faces"),
        "load_inertia_kgm2": bn.FloatSweep(default=0.0, bounds=[0, 1e-4], samples=5, units="kg*m^2"),
        "drive_v_bus": bn.FloatSweep(default=12.0, bounds=[5, 48], samples=4, units="V"),
        "drive_f_pwm_khz": bn.FloatSweep(default=24.0, bounds=[8, 100], samples=4, units="kHz"),
        "drive_ripple_frac": bn.FloatSweep(default=0.3, bounds=[0.2, 1.0], samples=4, units=""),
        "ref_speed_rev_s": bn.FloatSweep(default=5.0, bounds=[0.5, 50], samples=4, units="rev/s"),
        "coil_resolution_mm": bn.FloatSweep(default=0.5, bounds=[0.2, 1.0], samples=3, units="mm"),
        "commutation_steps": bn.IntSweep(default=12, bounds=[6, 24], units="steps"),
    }


def _make_results() -> dict:
    res = {name: bn.ResultVar(units=units) for name, _, units in _RESULT_MAP}
    # One setup image per design point (winding + rotor + axial stack).
    res["setup_img"] = bn.ResultImage()
    return res


_IMG_DIR = Path(tempfile.gettempdir()) / "pcb_motor_imgs"
_MONTAGE_CAP = 9   # max design points shown in the montage tab


def _design_from(overrides: dict) -> MotorDesign:
    """Build a MotorDesign from defaults plus display-unit sweep overrides."""
    fields = {}
    for attr, val in overrides.items():
        dfield, scale = _INPUT_MAP[attr]
        cur = getattr(_BASE_DESIGN, dfield)
        if scale is None:
            if isinstance(cur, bool) and isinstance(val, str):
                # CLI const overrides arrive as strings; "False" is truthy.
                val = val.strip().lower() in ("1", "true", "yes", "on")
            fields[dfield] = val
        elif isinstance(cur, bool):
            fields[dfield] = bool(val)
        elif isinstance(cur, int):
            fields[dfield] = int(round(val))
        else:
            fields[dfield] = float(val) * float(scale)
    return MotorDesign(**fields)


def _setup_montage(input_vars: list[str], out_dir: str,
                   const: dict | None = None) -> str | None:
    """Render one setup figure per sampled design point and grid them into a PNG.

    Regenerated from the sweep's sample grid in-process, so it is independent of
    bencher's result cache (a warm cache means the worker never runs). ``label``
    each panel by the swept axis values. Capped at ``_MONTAGE_CAP`` points.
    """
    import itertools
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    inst = MotorSweep()
    axes_vals = [list(getattr(inst.param, name).values()) for name in input_vars]
    combos = list(itertools.product(*axes_vals)) if axes_vals else [()]
    if len(combos) > _MONTAGE_CAP:
        idx = np.linspace(0, len(combos) - 1, _MONTAGE_CAP).round().astype(int)
        combos = [combos[i] for i in idx]

    items: list[tuple[str, str]] = []
    for combo in combos:
        overrides = dict(zip(input_vars, combo))
        design = _design_from({**(const or {}), **overrides})
        label = ", ".join(f"{n}={v:g}" if isinstance(v, (int, float)) else f"{n}={v}"
                           for n, v in overrides.items()) or "default"
        try:
            path = _save_fig(viz.plot_setup(design), "setup")
        except Exception:
            continue   # infeasible corner (e.g. zero turns fit): skip its panel
        items.append((label, path))

    if not items:
        return None
    n = len(items)
    cols = min(3, n)
    rows = math.ceil(n / cols)
    fig, axs = plt.subplots(rows, cols, figsize=(cols * 6.5, rows * 2.6))
    axs = np.atleast_1d(axs).ravel()
    for ax in axs:
        ax.axis("off")
    for ax, (label, path) in zip(axs, items):
        try:
            ax.imshow(mpimg.imread(path)); ax.set_title(label, fontsize=8)
        except Exception:
            pass
    fig.suptitle("Setup of every design point tested", fontsize=12)
    fig.tight_layout()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    montage = str(Path(out_dir) / "setups_montage.png")
    fig.savefig(montage, dpi=90, bbox_inches="tight")
    plt.close(fig)
    return montage


def _save_fig(fig, tag: str) -> str:
    _IMG_DIR.mkdir(parents=True, exist_ok=True)
    # Unique-ish filename from the figure id (no time/random needed).
    path = _IMG_DIR / f"{tag}_{id(fig):x}.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return str(path)


_BASE_DESIGN = MotorDesign()


def _worker(self, **kwargs):
    self.update_params_from_kwargs(**kwargs)
    fields = {}
    for attr, (dfield, scale) in _INPUT_MAP.items():
        raw = getattr(self, attr)
        cur = getattr(_BASE_DESIGN, dfield)
        if scale is None:                      # string / bool passthrough
            val = raw
        elif isinstance(cur, bool):
            val = bool(raw)
        elif isinstance(cur, int):
            val = int(round(raw))
        else:
            val = float(raw) * float(scale)
        fields[dfield] = val
    design = MotorDesign(**fields)

    try:
        out = evaluate_design(design)
    except Exception:
        # Infeasible grid corner (e.g. zero turns fit the wedge): show a hole
        # in the sweep rather than killing the whole dashboard build.
        for name, _, _ in _RESULT_MAP:
            setattr(self, name, float("nan"))
        return super(type(self), self).__call__(**kwargs)

    for name, key, _ in _RESULT_MAP:
        setattr(self, name, float(out[key]))

    # One setup image per design point (for the interactive served gallery).
    try:
        self.setup_img = _save_fig(viz.plot_setup(design), "setup")
    except Exception:  # pragma: no cover - visuals are non-critical
        pass

    return super(type(self), self).__call__(**kwargs)


# Build the ParametrizedSweep subclass dynamically so adding a design field in
# one place (_INPUT_MAP / _RESULT_MAP) is all that's needed.
MotorSweep = type(
    "MotorSweep",
    (bn.ParametrizedSweep,),
    {**_make_inputs(), **_make_results(), "__call__": _worker},
)


def evaluate_at(**overrides) -> dict:
    """Convenience: evaluate one design with field overrides (SI), no bencher."""
    return evaluate_design(MotorDesign(**overrides))


def build_dashboard(
    input_vars: list[str] | None = None,
    result_vars: list[str] | None = None,
    out_dir: str = "dashboard",
    title: str = "PCB-motor coil design",
    serve: bool = False,
    port: int = 9001,
    cache: bool = True,
    const: dict | None = None,
):
    """Run a sweep (or single point if ``input_vars`` is empty/None) and
    save or serve the bencher report. ``const`` holds display-unit overrides
    (keyed by sweep-input name) held fixed for every point."""
    input_vars = input_vars or []
    const = const or {}
    bad = [v for v in list(input_vars) + list(const) if v not in _INPUT_MAP]
    if bad:
        raise ValueError(f"Unknown vars {bad}; choose from {list(_INPUT_MAP)}")
    result_vars = result_vars or ["accel_cont", "tau_cont", "kt", "i_cont", "b_gap_mean"]

    run_cfg = bn.BenchRunCfg(
        repeats=1, headless=not serve, auto_plot=True,
        cache_results=cache, cache_samples=cache,
    )
    sweep = MotorSweep()
    const_vars = [(getattr(sweep.param, k), v) for k, v in const.items()]
    bench = sweep.to_bench(run_cfg)
    bench.plot_sweep(title=title, input_vars=input_vars, result_vars=result_vars,
                     const_vars=const_vars or None)

    # Append a montage tab so the report shows the setup of every point tested
    # (regenerated from the sample grid, independent of the result cache).
    montage = _setup_montage(input_vars, out_dir if not serve else tempfile.gettempdir(), const)
    if montage:
        try:
            import panel as pn
            bench.report.append_tab(pn.pane.PNG(montage, sizing_mode="scale_width"),
                                    name="Setups tested")
        except Exception:  # pragma: no cover - montage is non-critical
            pass

    if serve:
        import panel as pn
        print(f"serving dashboard at http://localhost:{port}  (Ctrl-C to stop)")
        pn.serve(bench.report.pane, port=port, show=False,
                 websocket_origin=[f"localhost:{port}", f"127.0.0.1:{port}"])
        return None
    path = bench.report.save(directory=out_dir, filename="index.html", in_html_folder=False)
    return str(path)


def optimize(
    input_vars: list[str] | None = None,
    n_trials: int = 100,
    serve: bool = False,
    port: int = 9001,
):
    """Maximise continuous acceleration over the coil design knobs (Optuna)."""
    input_vars = input_vars or [
        "trace_width_mm", "trace_space_mm", "r_inner_mm", "r_outer_mm", "copper_layers",
    ]
    run_cfg = bn.BenchRunCfg(repeats=1, headless=not serve, auto_plot=True)
    bench = MotorSweep().to_bench(run_cfg)
    res = bench.optimize(
        title="Maximise continuous acceleration",
        input_vars=input_vars,
        result_vars=["accel_cont"],
        n_trials=n_trials,
    )
    return res

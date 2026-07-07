"""Workflow glue tying the engine into a named motor-design session.

This is the connective tissue the conversational design workflow needs; it adds
no physics. Two pieces:

- ``Session`` -- a ``designs/<name>/`` directory holding the motor JSON, an
  optional requirements YAML, and the generated artifacts (setup PNG, KiCad
  footprint, HTML report, datasheet). Gives the workflow durable state across
  turns and lets you keep several named candidates side by side.
- ``compare`` / ``datasheet`` -- render the headline metrics of one or several
  designs as Markdown (the comparison table and the final key-parameters sheet).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .design import MotorDesign
from .evaluate import evaluate_design

DEFAULT_ROOT = "designs"


# --------------------------------------------------------------------------- #
# Session: a named design on disk
# --------------------------------------------------------------------------- #
class Session:
    """A ``designs/<name>/`` directory for one candidate design.

    Holds the motor design (``motor.json``), an optional requirements file
    (``requirements.yaml``), and the generated artifacts (setup PNG, KiCad
    footprint, HTML report, datasheet). All paths are derived; nothing is
    written until you call a ``save_*`` method.
    """

    def __init__(self, name: str, root: str | Path = DEFAULT_ROOT) -> None:
        self.name = name
        self.root = Path(root)
        self.dir = self.root / name

    # -- derived paths --
    @property
    def motor_json(self) -> Path:
        return self.dir / "motor.json"

    @property
    def requirements_yaml(self) -> Path:
        return self.dir / "requirements.yaml"

    @property
    def setup_png(self) -> Path:
        return self.dir / "setup.png"

    @property
    def kicad_mod(self) -> Path:
        return self.dir / "coil.kicad_mod"

    @property
    def report_html(self) -> Path:
        return self.dir / "design_report.html"

    @property
    def datasheet_md(self) -> Path:
        return self.dir / "datasheet.md"

    # -- persistence --
    def save_motor(self, design: MotorDesign) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.motor_json.write_text(
            json.dumps(dataclasses.asdict(design), indent=2), encoding="utf-8"
        )
        return self.motor_json

    def load_motor(self) -> MotorDesign:
        data = json.loads(self.motor_json.read_text(encoding="utf-8"))
        valid = {f.name for f in dataclasses.fields(MotorDesign)}
        return MotorDesign(**{k: v for k, v in data.items() if k in valid})

    def save_requirements(self, text: str) -> Path:
        """Persist a free-form requirements YAML/Markdown blob alongside the motor."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.requirements_yaml.write_text(text, encoding="utf-8")
        return self.requirements_yaml

    def load_requirements(self) -> str | None:
        """Return the saved requirements text if present, else ``None``."""
        if not self.requirements_yaml.exists():
            return None
        return self.requirements_yaml.read_text(encoding="utf-8")

    def exists(self) -> bool:
        return self.motor_json.exists()

    @classmethod
    def list_all(cls, root: str | Path = DEFAULT_ROOT) -> list["Session"]:
        """All sessions under ``root`` that have a saved motor, sorted by name."""
        base = Path(root)
        if not base.is_dir():
            return []
        out = [cls(p.name, root) for p in sorted(base.iterdir()) if p.is_dir()]
        return [s for s in out if s.exists()]


# --------------------------------------------------------------------------- #
# Headline metrics, shared by the datasheet and the comparison table
# --------------------------------------------------------------------------- #
def _fmt(v: float, nd: int = 4) -> str:
    return f"{v:.{nd}g}"


def _trace_row(design: MotorDesign) -> str:
    """Trace/space cell; tapered windings show the ID->OD width range."""
    ts_mm = design.trace_space_m * 1e3
    if getattr(design, "tapered_traces", False):
        from .coil_spiral import trace_width_at

        w_out = float(trace_width_at(design, design.r_outer_m)) * 1e3
        return (f"{design.trace_width_m*1e3:.3f}-{w_out:.3f} (tapered) "
                f"/ {ts_mm:.3f} mm")
    return f"{design.trace_width_m*1e3:.3f} / {ts_mm:.3f} mm"


def headline_rows(design: MotorDesign, results: dict) -> list[tuple[str, str]]:
    """The (label, value) rows shown for a single design.

    Mixes design inputs (architecture/geometry) with evaluated outputs so one
    table tells the whole story -- what you chose and what it buys you.
    """
    r = results
    n_poles = 2 * design.pole_pairs
    return [
        # architecture / magnet
        ("Winding", f"{design.winding_topology}, {design.n_slots}N{n_poles}P, "
                    f"{design.n_phases}-phase, {design.parallel_paths} parallel path(s)"),
        ("Magnets", f"{design.magnet_grade}, {design.magnet_topology}, "
                    f"{design.pole_pairs} pole-pairs, {design.magnet_thickness_m*1e3:.1f} mm, "
                    f"{design.pole_coverage*100:.0f}% coverage"),
        ("Stators", f"{design.n_stators} x {design.copper_layers}-layer, "
                    f"{design.board_thickness_m*1e3:.2f} mm FR4, "
                    f"{design.copper_weight_oz:g} oz Cu"),
        # geometry
        ("Active annulus", f"{design.r_inner_m*1e3:.1f} - {design.r_outer_m*1e3:.1f} mm"),
        ("Trace / space", _trace_row(design)),
        ("Air gap (per side)", f"{design.air_gap_m*1e3:.2f} mm"),
        # electromagnetic
        ("Kt (torque constant)", f"{_fmt(r['kt_mNm_per_A'])} mNm/A"),
        ("Phase resistance @20C", f"{_fmt(r['r_phase_20c_ohm'])} ohm"),
        ("Mean / peak airgap |Bz|", f"{_fmt(r['b_gap_mean_T'])} / {_fmt(r['b_gap_peak_T'])} T"),
        ("Turns / phase", f"{r['n_turns']}"),
        ("Winding factor kw1", f"{_fmt(r['winding_factor'], 3)}"),
        # thermal / continuous rating
        ("Continuous current", f"{_fmt(r['i_cont_A'])} A"),
        ("Continuous torque", f"{_fmt(r['tau_cont_mNm'])} mNm"),
        ("Drive voltage (cont)", f"{_fmt(r['v_drive_cont_V'])} V"),
        ("Phase inductance (air-core)", f"{_fmt(r['l_phase_uH'])} uH"),
        ("Ext. choke for ripple budget", f"{_fmt(r['l_ext_uH'])} uH"
         f" @ {_fmt(r['pwm_ripple_A_pp'])} A pp bare"),
        ("Current density", f"{_fmt(r['current_density_A_mm2'])} A/mm^2"),
        ("Airgap shear", f"{_fmt(r['shear_stress_kPa'])} kPa"),
        # mechanical
        ("Rotor / total inertia", f"{_fmt(r['j_rotor_kgm2'], 3)} / "
                                  f"{_fmt(r['j_total_kgm2'], 3)} kg*m^2"),
        ("Continuous acceleration", f"{_fmt(r['accel_cont_rad_s2'])} rad/s^2"),
        ("Copper mass", f"{_fmt(r['copper_mass_g'])} g"),
    ]


# --------------------------------------------------------------------------- #
# Datasheet (final report) and compare (candidate table)
# --------------------------------------------------------------------------- #
def datasheet(design: MotorDesign, results: dict | None = None, *,
              name: str | None = None) -> str:
    """One-page Markdown 'key design parameters' sheet for a single design.

    ``results`` is computed if not supplied.
    """
    if results is None:
        results = evaluate_design(design)
    n_poles = 2 * design.pole_pairs
    title = name or f"{design.winding_topology} {design.n_slots}N{n_poles}P {design.magnet_grade}"

    lines = [f"# Motor design datasheet — {title}", ""]
    lines.append("| Parameter | Value |")
    lines.append("| --- | --- |")
    for label, value in headline_rows(design, results):
        lines.append(f"| {label} | {value} |")

    if results.get("warnings"):
        lines += ["", "## Warnings", ""]
        for w in results["warnings"]:
            lines.append(f"- {w}")

    lines += [
        "",
        "---",
        "*pcb-motor analytical Biot-Savart model, roughly +/-30% on absolute "
        "torque. Calibrate against FEMM or a bench coil before committing to a "
        "build.*",
        "",
    ]
    return "\n".join(lines)


def compare(sessions) -> str:
    """Side-by-side Markdown comparison of several saved sessions or designs.

    Each element of ``sessions`` may be a :class:`Session`, a session name
    (``str``), or a ``MotorDesign``. Returns a Markdown table with one column
    per design and one row per headline metric.
    """
    named: list[tuple[str, MotorDesign]] = []
    for item in sessions:
        if isinstance(item, MotorDesign):
            named.append((item.winding_topology, item))
        elif isinstance(item, Session):
            named.append((item.name, item.load_motor()))
        else:  # name string
            s = Session(str(item))
            named.append((s.name, s.load_motor()))

    if not named:
        return "_no designs to compare_"

    # Evaluate each; align rows by label (all designs share the same row set).
    rows_per = [(nm, headline_rows(d, evaluate_design(d))) for nm, d in named]
    labels = [label for label, _ in rows_per[0][1]]

    header = "| Parameter | " + " | ".join(nm for nm, _ in rows_per) + " |"
    sep = "| --- | " + " | ".join("---" for _ in rows_per) + " |"
    out = [header, sep]
    for i, label in enumerate(labels):
        cells = [rows[i][1] for _, rows in rows_per]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"

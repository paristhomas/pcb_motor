"""Shared data contracts for the pcb_motor PCB-motor simulator.

Everything here is **SI** (metres, amperes, tesla, kg). Unit conversion to the
Biot-Savart kernel's internal units (cm / gauss) happens only inside
``field.py``.

These dataclasses are the *frozen interfaces* every other module builds against:

- ``CurrentSource`` -- a polyline carrying current; the universal input to the
  field solver. Magnets and (optionally) coils are expressed as these.
- ``CoilGeometry`` -- the generated stator winding: per-segment geometry the
  torque integrator consumes, plus electrical summaries and raw polylines for
  plotting.
- ``RotorConfig`` -- the fixed magnet rotor / stack context.
- ``MotorDesign`` -- one full design point (swept coil knobs + fixed context),
  the single source of truth the bencher sweep mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

WindingTopology = Literal["concentrated", "radial_spoke", "spiral"]


# --------------------------------------------------------------------------- #
# Universal field-source contract
# --------------------------------------------------------------------------- #
@dataclass
class CurrentSource:
    """A current-carrying polyline, in SI.

    ``vertices`` is an ``(N, 3)`` array of points in metres. ``currents`` is an
    ``(N,)`` array where ``currents[i]`` is the current [A] flowing in the
    segment from ``vertices[i]`` to ``vertices[i+1]`` (the last entry is
    unused). Multiple disjoint loops are concatenated into one source; close
    each loop explicitly (repeat the first vertex) so no spurious segment
    bridges two loops -- set the bridging segment's current to 0 if loops must
    be concatenated without closing.
    """

    vertices: np.ndarray   # (N, 3) metres
    currents: np.ndarray   # (N,) amperes (current in segment i -> i+1)

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=float).reshape(-1, 3)
        self.currents = np.asarray(self.currents, dtype=float).reshape(-1)
        if self.currents.shape[0] != self.vertices.shape[0]:
            raise ValueError("currents and vertices must have the same length")

    @staticmethod
    def concat(sources: list["CurrentSource"]) -> "CurrentSource":
        """Concatenate sources into one, inserting a zero-current break between
        each so no stray segment connects them."""
        if not sources:
            return CurrentSource(np.zeros((0, 3)), np.zeros((0,)))
        verts, curs = [], []
        for k, s in enumerate(sources):
            verts.append(s.vertices)
            c = s.currents.copy()
            c[-1] = 0.0  # last vertex of a block bridges to the next block: no current
            curs.append(c)
        return CurrentSource(np.vstack(verts), np.concatenate(curs))


# --------------------------------------------------------------------------- #
# Coil geometry contract (output of coils.py, input to torque.py / viz.py)
# --------------------------------------------------------------------------- #
@dataclass
class CoilGeometry:
    """Generated stator winding.

    Per-segment arrays (length ``S``) describe the discretised conductor for the
    torque integrator:
      - ``midpoints_m``  (S, 3): segment midpoints
      - ``dvec_m``       (S, 3): segment vector dL (direction * length)
      - ``phase``        (S,)  : phase index 0/1/2 the segment belongs to
      - ``direction``    (S,)  : +1/-1 current sense along ``dvec_m`` for that phase
      - ``is_radial``    (S,)  : True for torque-producing radial conductors,
                                 False for end-turn arcs (used for end_turn_fraction)

    Electrical summaries are per phase (the three phases are symmetric):
      - ``length_per_phase_m``, ``conductor_area_m2``, ``n_turns``, ``n_layers``

    ``polylines`` keeps the raw traces (list of (K,3) arrays) for plotting only.
    """

    midpoints_m: np.ndarray
    dvec_m: np.ndarray
    phase: np.ndarray
    direction: np.ndarray
    is_radial: np.ndarray

    length_per_phase_m: float
    conductor_area_m2: float
    n_turns: int
    n_layers: int

    polylines: list[np.ndarray] = field(default_factory=list)

    # Tapered (variable-width) windings only; None for constant-width coils.
    # ``conductor_area_m2`` then holds the *minimum* cross-section (at r_inner),
    # which is what current-density reporting wants, while these carry the
    # integrated electrical/mass quantities the uniform L/A shortcut can't.
    length_over_area_per_phase: float | None = None   # sum(dl/A) one phase [1/m]
    copper_volume_m3: float | None = None             # all phases, one stator [m^3]

    @property
    def end_turn_fraction(self) -> float:
        """Share of conductor length in non-torque end-turn arcs."""
        lens = np.linalg.norm(self.dvec_m, axis=1)
        total = float(lens.sum())
        if total <= 0:
            return 0.0
        return float(lens[~self.is_radial].sum() / total)


# --------------------------------------------------------------------------- #
# Fixed rotor / stack context
# --------------------------------------------------------------------------- #
@dataclass
class RotorConfig:
    """The given (fixed) magnet rotor and PCB stack. SI units."""

    magnet_grade: str = "N42"
    pole_pairs: int = 7
    magnet_thickness_m: float = 3.0e-3
    magnet_r_inner_m: float = 10.0e-3
    magnet_r_outer_m: float = 30.0e-3
    pole_coverage: float = 0.85
    air_gap_m: float = 1.0e-3          # mechanical gap, one side
    back_iron: bool = False            # flat iron plate behind EACH stator board
    iron_standoff_m: float = 0.0       # extra gap board-back -> iron plate face
    board_thickness_m: float = 0.8e-3
    copper_weight_oz: float = 1.0
    n_stators: int = 2
    rotor_sides: int = 1               # 2 = dual-rotor sandwich: ONE stator board
                                       # between TWO magnet rotors (second magnet
                                       # plane at z = 2*stator_z, same magnetisation
                                       # pattern, attracting -- axial fields ADD).
                                       # Requires n_stators == 1 and back_iron False.
    carrier_thickness_m: float = 1.5e-3
    # Rotor magnet shape: "arc" (continuous pole-arc ring, parametrised by
    # magnet_r_inner/outer + pole_coverage); "round" (two concentric rings of
    # round disc magnets -- inner+outer of each pole share polarity); or the
    # single-ring study variants "round_outer" (outer ring only) / "round_inner"
    # (inner ring only).
    magnet_topology: str = "arc"
    outer_ring_r_m: float = 37.2e-3    # round: outer-disc centre radius
    outer_disc_d_m: float = 15.0e-3    # round: outer-disc diameter (stock Ø15)
    inner_ring_r_m: float = 20.6e-3    # round: inner-disc centre radius
    inner_disc_d_m: float = 8.0e-3     # round: inner-disc diameter (stock Ø8)

    def stator_z_m(self) -> float:
        """Axial distance from the magnet mid-plane to the near stator copper
        plane = air gap + half board thickness (rotor centred at z=0)."""
        return self.magnet_thickness_m / 2 + self.air_gap_m + self.board_thickness_m / 2


# --------------------------------------------------------------------------- #
# One full design point: swept knobs (A) + fixed context (B)
# --------------------------------------------------------------------------- #
@dataclass
class MotorDesign:
    """A complete design point. Swept (optimised) knobs + fixed assumptions.

    SI units throughout. The bencher ``ParametrizedSweep`` in ``sweep.py``
    mirrors these fields (with mm/g display units) and builds a ``MotorDesign``
    to run one evaluation.
    """

    # --- A. swept design variables (the coil/PCB knobs we optimise) ---
    trace_width_m: float = 0.15e-3
    trace_space_m: float = 0.15e-3
    r_inner_m: float = 10.0e-3
    r_outer_m: float = 30.0e-3
    copper_layers: int = 2
    parallel_paths: int = 1
    winding_topology: WindingTopology = "concentrated"
    n_slots: int = 12                  # concentrated coils (12N14P = 12 slots, 14 poles)
    corner_radius_m: float = 0.15e-3   # fillet radius applied to sharp trace corners
                                       # (drawn/exported geometry only; 0 = sharp)
    tapered_traces: bool = False       # concentrated only: wedge traces at constant
                                       # *angular* pitch; trace_width_m is the width at
                                       # r_inner_m and width grows as w(r) = d*r - space,
                                       # holding the clearance at trace_space_m at every
                                       # radius (constant-width holds it only at r_mean)

    # --- B. fixed context: rotor ---
    magnet_grade: str = "N42"
    pole_pairs: int = 7
    magnet_thickness_m: float = 3.0e-3
    magnet_r_inner_m: float = 10.0e-3
    magnet_r_outer_m: float = 30.0e-3
    pole_coverage: float = 0.85
    air_gap_m: float = 1.0e-3
    back_iron: bool = False            # flat iron plate behind EACH stator board
    iron_standoff_m: float = 0.0
    board_thickness_m: float = 0.8e-3
    copper_weight_oz: float = 1.0
    n_stators: int = 2
    rotor_sides: int = 1               # 2 = dual-rotor sandwich (one stator board
                                       # between two magnet rotors); requires
                                       # n_stators == 1 and back_iron False
    carrier_thickness_m: float = 1.5e-3
    magnet_topology: str = "arc"       # arc | round | round_outer | round_inner
    outer_ring_r_m: float = 37.2e-3
    outer_disc_d_m: float = 15.0e-3
    inner_ring_r_m: float = 20.6e-3
    inner_disc_d_m: float = 8.0e-3

    # --- B. fixed context: electrical / thermal / numerical ---
    n_phases: int = 3
    phase_activity: float = 0.667
    loss_phase_factor: float = 1.5
    h_conv: float = 15.0
    temp_limit_c: float = 100.0
    ambient_c: float = 25.0
    cooled_faces: int = 2
    load_inertia_kgm2: float = 0.0
    coil_resolution_m: float = 0.5e-3
    commutation_steps: int = 12
    # Drive-side reference (for PWM-ripple / eddy reporting, not the objective)
    drive_v_bus: float = 12.0          # DC bus voltage [V]
    drive_f_pwm_hz: float = 24e3       # driver switching frequency [Hz]
    drive_ripple_frac: float = 0.3     # PWM ripple budget as fraction of i_cont
    ref_speed_rev_s: float = 5.0       # mech speed for the eddy-loss estimate

    def rotor(self) -> RotorConfig:
        return RotorConfig(
            magnet_grade=self.magnet_grade,
            pole_pairs=self.pole_pairs,
            magnet_thickness_m=self.magnet_thickness_m,
            magnet_r_inner_m=self.magnet_r_inner_m,
            magnet_r_outer_m=self.magnet_r_outer_m,
            pole_coverage=self.pole_coverage,
            air_gap_m=self.air_gap_m,
            back_iron=self.back_iron,
            iron_standoff_m=self.iron_standoff_m,
            board_thickness_m=self.board_thickness_m,
            copper_weight_oz=self.copper_weight_oz,
            n_stators=self.n_stators,
            rotor_sides=self.rotor_sides,
            carrier_thickness_m=self.carrier_thickness_m,
            magnet_topology=self.magnet_topology,
            outer_ring_r_m=self.outer_ring_r_m,
            outer_disc_d_m=self.outer_disc_d_m,
            inner_ring_r_m=self.inner_ring_r_m,
            inner_disc_d_m=self.inner_disc_d_m,
        )

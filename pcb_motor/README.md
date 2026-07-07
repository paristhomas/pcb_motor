# pcb_motor — Biot–Savart coreless axial-flux PCB-motor engine

For a given stator/rotor configuration, evaluate (and optimise) the PCB
stator-coil design directly from copper geometry, *simulating* the magnetics
numerically (Biot–Savart). Reports Kt, torque, air-gap field, winding factor,
thermal continuous rating, current density, shear, rotor inertia, and
continuous acceleration `a_cont = τ_cont / J` — enough to judge a design against
real requirements (torque, voltage, size, duty), not just one objective.

Self-contained: all physical constants live in `constants.py`.

## Install / run

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pcb_motor point                       # evaluate one design
.venv/bin/python -m pcb_motor config --out setup.png      # winding + rotor + stack figure
.venv/bin/python -m pcb_motor sweep --inputs trace_width_mm,trace_space_mm --serve
.venv/bin/python -m pcb_motor optimize --trials 100       # max continuous accel
.venv/bin/python -m pytest -q
```
`point` needs only the core deps; `sweep`/`optimize` need `holobench`
(`pip install -e ".[sweep]"`).

## Model (and its limits — this is a ~±30% feasibility-grade model)

- **Field** (`field.py`): vectorised numpy Biot–Savart kernel. Validated to <1%
  against the closed-form on-axis loop field (`tests/test_field.py`).
- **Magnets** (`magnets.py`): Amperian current-loop model, `I_eq = (Br/μ0)·t`,
  alternating polarity. Rotor topologies via `magnet_topology`:
  `"arc"` (continuous pole-arc ring); `"round"` (two concentric rings of round
  disc magnets — inner+outer of each pole share polarity; the buildable round-stock
  rotor); and the single-ring study variants `"round_outer"` (outer ring of larger
  discs only) and `"round_inner"` (inner ring of smaller discs only). Inertia
  (`inertia.py`) and the carrier extent are topology-aware (they key off
  `magnets.active_rings`), so the acceleration objective fairly reflects each
  rotor's magnet mass. Perimeter/thin-shell approximation.
- **Coils** (`coils.py`): default **`concentrated`** — `n_slots` discrete
  multi-turn **wedge-spiral** coils (default 12 slots / 14 poles = 12N14P). Turns
  nest inward by one trace pitch on all sides, so no copper overlaps (fabricable,
  KiCad-exportable). Phase + polarity per tooth come from the **star-of-slots**
  layout (12N14P: order A,C,B in opposite-polarity pairs). `winding_factor()`
  returns kw1 (~0.93 for 12N14P). Alternatives: `radial_spoke`, `spiral`.
- **Torque** (`torque.py`): field evaluated on a coarse polar grid per rotor
  angle/layer and interpolated onto the coil (decouples cost from mesh density).
  Kt uses **proper 3-phase commutation** — sinusoidal currents synchronised to the
  rotor at the optimal commutation angle — so the **winding factor is folded into
  Kt physically** (no hand-applied kw). Reports `winding_factor` (analytic kw1) and
  `winding_utilisation` (commutated/ideal-abs-sum).
- **Back iron** (`iron.py`): optional flat plate behind each stator, modelled by
  the method of images (μ→∞ planes). Sanity-grade: no saturation, no plate
  eddy/hysteresis drag; the per-plate magnetic pull is reported so the mechanics
  can be sized for it.
- **Continuous torque/accel**: `τ_cont = Kt·I_cont`, `I_cont` from the thermal
  model (`thermal.py`), `J` from `inertia.py`.
- **Parasitics** (`parasitics.py`): air-core phase inductance (Neumann double
  sum, ~±20%), worst-case (D=0.5) PWM current ripple
  `ripple_pp = v_bus/(4·L·f_pwm)` — the "do I need a choke" feasibility gate —
  and an eddy-loss screen for wide traces at speed.

Key caveat: `current_density` is reported as a sanity flag, not enforced, and
absolute numbers everywhere are feasibility-grade (~±30%), never bench-exact.

## Exploring the design space

Sweep any 1–3 `MotorDesign` axes interactively (needs `holobench`); open the
printed `http://localhost:9001`:

```bash
.venv/bin/python -m pcb_motor sweep \
  --inputs trace_width_mm,magnet_topology \
  --results accel_cont,tau_cont,kt,j_rotor,b_gap_mean,copper_mass \
  --serve --port 9001
```

Key reading: `accel_cont` is magnet-fill-insensitive on a bare rotor (torque and
inertia both scale with magnet mass and cancel); compare rotors on `kt` /
`tau_cont` / `b_gap_mean`, or on acceleration *with a load inertia*
(`--set load_inertia_kgm2=…`).

## Layout
`design.py` (contracts) · `field.py` · `magnets.py` · `coils.py` · `torque.py` ·
`iron.py` · `parasitics.py` · `thermal.py` · `inertia.py` · `evaluate.py`
(design→results) · `sweep.py` (bencher) · `viz.py` · `cli.py` · `kicad/`
(footprint/project export). See `interfaces.md` for the module APIs.

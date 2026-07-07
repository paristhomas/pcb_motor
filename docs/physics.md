# How the model works (and where it lies)

pcb_motor is an **analytical Biot–Savart model**: it computes fields and torque
directly from the actual copper and magnet geometry, with essentially no empirical
fudge factors. That buys you a model that responds *correctly in shape* to every knob
(radius, turns, gap, poles) — and it's why we can be specific about where the absolute
numbers go wrong. Read this before trusting a number with money.

Everything below lives in a module you can read in one sitting; file names in
parentheses.

## The field solver (`field.py`)

One kernel does all the magnetics: given a `CurrentSource` (a polyline of vertices
with a current per segment), subdivide every segment to `resolution_m` (default
0.5 mm), then for each observation point sum the Biot–Savart contribution
`µ0/4π · I dl × r̂ / |r|²` over all sub-segments — fully vectorized in numpy, so a
whole stator plane is one array operation, not a loop.

**Validation:** against the closed-form on-axis field of a circular loop,
`Bz = µ0 I R² / 2(R² + z²)^{3/2}`, the solver agrees to **<1%** on and off the loop
plane (`tests/test_field.py`). Superposition and current-linearity are also asserted.
The numerics are not where the ±30% comes from.

## Magnets as bound currents (`magnets.py`)

A uniformly-magnetized magnet is exactly equivalent to a surface (Amperian) current
around its perimeter: `I_eq = (Br/µ0) · thickness`. So the rotor becomes a set of
current loops at the magnet mid-plane — one loop per pole, alternating sign — and the
same field kernel solves them. Two rotor shapes are modeled:

- **`arc`** — continuous pole-arc segments (annular wedges, `pole_coverage` of each
  pole pitch), the classic custom-magnet rotor.
- **`round`** — two concentric rings of round disc magnets (inner + outer disc of each
  pole share polarity): the rotor you can build from stock magnets today.

The loop current can be split across `n_stack` sub-loops through the magnet thickness
for near-field accuracy. `rotor_sides=2` (dual-rotor sandwich) adds a second magnet
plane, same magnetization pattern, on the far side of the stator — the attracting
arrangement, so the axial fields *add* at the copper (asserted to ~2× in
`tests/test_dual_rotor.py`).

What's idealized: perfectly uniform magnetization at nominal Br, sharp pole
transitions, no demagnetization, no temperature coefficient on the magnets.

## The winding (`coils.py`, `coil_spiral.py`)

The default `concentrated` topology generates `n_slots` wedge-shaped multi-turn
spiral coils; turns nest inward by one trace pitch so nothing overlaps — the
simulated geometry is the same geometry that gets exported to KiCad. Phase and
polarity per tooth come from a verified **star-of-slots** table (the 12N14P family
and its 36N42P tiling); anything else falls back to round-robin, which can produce a
winding factor near zero — see the design guide's Stage 4 for the numbers and the
check to run.

Each coil is discretized into segments carrying `phase`, `direction`, and an
`is_radial` flag (torque-producing radial run vs end-turn arc). Two-layer boards
place the back layer as the **mirror** of the front about each coil's radial
centre-line, in series — more on why below.

## Torque and Kt (`torque.py`)

Brute-force field evaluation at every coil segment would be wasteful (the coil mesh
is dense; the field is smooth), so per rotor angle the magnet field is computed on a
coarse polar grid at each copper layer's z-plane and interpolated onto the segment
midpoints. Torque cost is then independent of coil mesh density.

Per segment: Lorentz force `dF = I (dL × B)`, axial torque `(r × dF)_z`. Torque is
linear in phase currents, so it collapses to a per-phase torque vector `G(θ)`; the
evaluator sweeps rotor angles over one electrical period, drives the three phases
with sinusoidal currents at the optimal commutation angle, and reports the mean
torque per ampere as **Kt**.

Two properties worth internalizing:

- **The winding factor is never applied by hand.** kw1 emerges physically from the
  commutated sum over conductors at their actual electrical angles. The analytic kw1
  is *also* computed (`winding_factor()`), but only as a diagnostic — if the two
  disagree, the geometry, not the formula, wins.
- **Only B_z makes torque here.** With radial current sheets, `J × B` gives axial
  torque only through the axial field component (`τ_z ∝ -r·J_r·B_z`); B_r and B_φ are
  integrated too but contribute zero shaft torque for planar radial conductors —
  asserted in `tests/test_torque.py::test_torque_comes_from_axial_field`. This is why
  the B_z map is the field picture worth staring at.

### The mirrored-layer war story

The most instructive bug this project ever produced: the first two-sided coil
generator placed the back copper as a plain copy of the front (what you'd sketch on a
whiteboard). Perfectly plausible-looking board. The torque integrator scored the
front+back series pair at **exactly 0.00** — each turn's return path cancelled its
own torque. Mirroring the back layer about the coil's radial centre-line scored
**+2.00** (same units, same current): layers in series, torque additive. The
production footprint builder inherits this as a hard invariant, and it's the reason
"just simplify the layer mirroring" appears in no roadmap. If you generate your own
two-layer artwork: mirror it, then *check the torque*, because the wrong version
looks identical.

## Thermal limit (`thermal.py`)

Continuous current comes from a lumped steady-state convection balance:

```
P_max  = h_conv · A_cooled · (T_limit − T_ambient)
A      = cooled_faces · π(r_outer² − r_inner²)
I_cont = sqrt(P_max / (loss_phase_factor · R_phase_hot))
```

with copper resistance taken at the hot temperature (+0.39%/K). This is deliberately
crude: one node, no radiation, no conduction into the mount, film coefficient
`h_conv` guessed (15 W/m²K ≈ still air). It answers "is this thermally plausible",
not "where is the hot spot". A fan, a metal mount, or a thermal camera will each
falsify it in your favor or against.

## Inductance, PWM ripple, eddy loss (`parasitics.py`)

**Phase inductance** is a Neumann double sum over one phase's discretized conductor —
mutual terms between every segment pair, with a regularized kernel using the
rectangular-conductor geometric mean distance `0.2235(w+t)` so the self-terms don't
blow up. Air-core only; stated accuracy **±20%**, which is exactly enough for its one
job: the choke question.

**PWM ripple** is the worst-case (50% duty) triangle: `ripple_pp = v_bus/(4·L·f_pwm)`,
compared against `drive_ripple_frac · I_cont`; the report includes the external series
inductance needed to hit the budget. This gate is load-bearing — see design guide
Stage 5.

**Eddy loss** in the (wide) traces uses the thin-strip lamination formula
`P/V = π²f²B²w²/(6ρ)` with the local tapered width — an order-of-magnitude screen
that flags high-speed designs, not a loss model.

## Rotor inertia (`inertia.py`)

Magnet ring plus printed carrier, topology-aware (round-disc rotors count their
actual disc masses and radii). Back iron is stator-fixed and contributes nothing to
rotor inertia. This feeds `accel_cont = τ_cont / (J_rotor + J_load)`.

## Back iron (`iron.py`) — sanity-grade, and proud of it

An optional flat iron plate behind each stator is modeled by the **method of
images**: for a µ→∞ plane, every current element acquires a mirrored image with the
same current; two plates create an infinite reflection series, truncated after a few
bounces (each double-reflection recedes a full plate spacing, contributions die off
~1/d³). This captures the field amplification trend and the per-plate magnetic pull
(Maxwell stress integral, reported so you can size the mechanics).

Not captured: saturation (a µ→∞ plate never saturates; your 1 mm mild-steel one
will), eddy and hysteresis drag in the plate, and the negative axial stiffness of the
attraction. Treat back-iron results as a *sanity flag* — "iron would buy roughly this
much" — not as a design point to order parts against.

## The ±30% claim, itemized

The field solver is <1%; the error budget is model scope, roughly in descending
order:

1. **The as-built air gap.** Field falls steeply with axial distance (the more pole
   pairs, the steeper). ±0.2 mm of print/shim/bearing slack moves torque by tens of
   percent. This is the usual culprit when measured Kt disappoints.
2. **Magnet reality.** Br tolerance (±5% is typical for commodity NdFeB), actual
   magnetization uniformity, and the idealized sharp pole transitions vs real
   fringing between poles.
3. **Copper reality.** Etch undercut and plating variation change trace cross-section
   (worse at 2 oz), and the model ignores via/interconnect/termination resistance.
4. **Thermal hand-waving.** `h_conv` and the phase-loss lumping
   (`loss_phase_factor`) set I_cont, so continuous torque inherits their uncertainty
   even when Kt is right.
5. **Inductance ±20%**, which propagates linearly into the ripple gate.

Relative comparisons between designs evaluated by the same model are far better than
±30% — the errors are largely common-mode. That's why the tool is comfortable ranking
candidates but keeps telling you to measure before building ten.

## Validation notes

The test suite (`python -m pytest -q`, all green) pins the physics down where
closed forms or invariants exist:

- Biot–Savart vs the analytic circular-loop field, on- and off-plane, <1%
  (`test_field.py`).
- Torque linearity in current; Kt scaling with `n_stators`; Kt monotone in magnet
  grade (`test_torque.py`).
- The airgap **shear integral independently reproduces Kt** — two different
  integration routes to the same torque (`test_torque.py::test_shear_integral_matches_kt`).
- Torque comes from B_z only (`test_torque.py::test_torque_comes_from_axial_field`).
- Winding factors: 12N14P ≈ 0.94 and 36N42P ≈ 0.95 measured on generated geometry
  (`test_coils.py`); the mirrored-layer invariant (this page, war story above).
- Dual-rotor sandwich: second magnet plane doubles Bz and Kt, and exactly doubles
  rotor inertia (`test_dual_rotor.py`).
- The KiCad footprint builder re-parses its own output and refuses to write on any
  clearance/stitch/bridge failure (`test_kicad_footprint.py`).

What has *not* been done for this repo: a systematic bench calibration campaign
against built motors. The ±30% band is an engineering judgment from the itemized
sources above, not a measured statistic — one more reason to treat your first build
as the calibration article.

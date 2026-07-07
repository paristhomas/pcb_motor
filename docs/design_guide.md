# The PCB motor design guide

This is the full walkthrough: everything between "I think I want a PCB motor" and a
zip file on its way to JLCPCB. It follows one worked example the whole way — an 80 mm
gimbal motor we'll call `pancake80` — and every command shown here was actually run
against this repo. If you'd rather delegate, open Claude Code in this repo and describe
your motor; the `.claude/skills/pcb-motor-design` skill walks these exact stages. This
document is for humans (and for checking the agent's work).

The machine we're designing: a **coreless axial-flux motor** with one rotor — a ring
of alternating-polarity magnets on a 3D-printed carrier — sandwiched between **two
stator PCBs wired in series**. That dual-stator/single-rotor sandwich is the default
and recommended configuration; both boards are identical, which is convenient, because
PCBs are cheap in multiples of five.

## Stage 0 — Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
source .venv/bin/activate
```

Sanity check: `python -m pytest -q` should be green, and `pcb-motor new --session
scratch` should print a table of numbers. Everything below assumes the venv is active;
otherwise prefix commands with `.venv/bin/`.

## Stage 1 — Write down the requirements

Resist the urge to type `--set` flags. The single most common failure mode in motor
design is optimizing a number nobody needed. Establish, roughly:

- **Torque** — continuous, and peak if it differs. This is usually THE requirement.
- **Speed** — operating speed matters for eddy losses and drive voltage headroom.
- **Voltage & drive** — bus voltage, driver type (ODrive-class FOC? cheap ESC?),
  switching frequency. This decides the winding and the choke question (Stage 5).
- **Envelope** — max outer diameter, hub/bore diameter, total axial thickness.
- **Duty** — continuous or burst. Decides how hard the thermal limit bites.
- **Fixed stock** — magnet sizes/grades you can actually buy, board-house limits
  (copper weight, min trace/space). These are *inputs, not knobs*: don't let an
  optimizer pick a magnet that doesn't exist.

Write them into the session directory so they survive your enthusiasm
(`pcb-motor new` seeds a commented `requirements.yaml` skeleton with exactly these
keys if the file doesn't exist yet). For our worked
example, `designs/pancake80/requirements.yaml`:

```yaml
application: camera gimbal, direct drive
torque_cont_mNm: 50        # holding + slew against slightly unbalanced payload
speed_rpm: <300            # slow; eddy losses irrelevant
bus_voltage_V: 24
drive: ODrive-class FOC, f_pwm 48 kHz
envelope:
  outer_diameter_mm: 80    # hard limit
  bore_mm: 30              # cable pass-through
  axial_mm: 12
duty: continuous
stock:
  magnets: N52 arc segments, 3 mm thick (or round discs as fallback)
  fab: JLCPCB 2-layer; 1 oz (5/5 mil) or 2 oz (8/8 mil) copper
```

## Stage 2 — Seed a design that fits the envelope

A session is created with `pcb-motor new --session <name>`, which saves a `MotorDesign`
(defaults + your `--set` overrides) into `designs/<name>/motor.json` and immediately
evaluates it. All `--set` fields are SI (`_m`, `_hz` suffixes mean what they say);
`pcb-motor fields` prints every settable field, grouped, with its default and meaning.

The knobs, grouped. Defaults in parentheses.

**Geometry — the coil and board (the stuff you'll iterate most):**

| Field | Default | What it does |
|---|---|---|
| `r_inner_m` / `r_outer_m` | 10/30 mm | Active copper annulus. Torque scales hard with radius — use the envelope. |
| `trace_width_m` | 0.15 mm | Conductor width (at `r_inner` if tapered). Wider → lower R, fewer turns → lower Kt. |
| `trace_space_m` | 0.15 mm | Copper-to-copper gap. Set by your board house (Stage 7), not by taste. |
| `tapered_traces` | false | Wedge traces at constant angular pitch: width grows with radius, clearance held everywhere. **Turn this on for anything you'll fabricate** — it's what the production footprint emits. |
| `copper_layers` | 2 | Layers per board. Back layer is the mirror of the front (in series, torque-additive). |
| `copper_weight_oz` | 1.0 | Foil thickness. 2 oz halves R without touching Kt — but relaxes min trace/space to 0.20 mm at JLC. |
| `board_thickness_m` | 0.8 mm | FR4 thickness; part of the magnetic gap for the far layer. |
| `n_slots` | 12 | Number of concentrated coils. Pair with `pole_pairs` — see the star-of-slots section in Stage 4. |
| `winding_topology` | concentrated | `concentrated` is the fabricable one; `radial_spoke`/`spiral` exist for studies. |
| `parallel_paths` | 1 | Splits the winding into parallel groups: Kt/p, R/p² — the lever for taming drive voltage. |
| `corner_radius_m` | 0.15 mm | Fillet on sharp trace corners (drawn geometry only). |

**Magnets — the rotor:**

| Field | Default | What it does |
|---|---|---|
| `pole_pairs` | 7 | Magnetic poles = 2×this. More poles → shorter end-turns and more Kt up to a point, finer features beyond it. |
| `magnet_grade` | N42 | NdFeB grade → Br. Buy what's actually stocked. |
| `magnet_thickness_m` | 3 mm | Thicker → more field, with diminishing returns once thickness ≳ pole pitch. |
| `magnet_r_inner_m` / `magnet_r_outer_m` | 10/30 mm | Magnet annulus; roughly track the coil annulus. |
| `pole_coverage` | 0.85 | Fraction of each pole pitch covered by magnet (arc topology). |
| `magnet_topology` | arc | `arc` = continuous pole-arc ring (custom arc segments). `round` = two concentric rings of off-the-shelf round disc magnets — the buy-it-today rotor (`outer_ring_r_m`, `outer_disc_d_m`, `inner_ring_r_m`, `inner_disc_d_m`). |
| `carrier_thickness_m` | 1.5 mm | 3D-printed rotor carrier, counted in rotor inertia. |

**Stack — how boards and rotor sandwich together:**

| Field | Default | What it does |
|---|---|---|
| `n_stators` | 2 | Stator boards, wired in **series**. The default machine is the two-boards-one-rotor sandwich. Kt and R both scale with it. |
| `air_gap_m` | 1.0 mm | Mechanical gap magnet-face → board, per side. **The single most sensitive number in the whole design** — and your mechanical tolerances own it. |
| `rotor_sides` | 1 | Alternative topology: `2` = dual-rotor sandwich (magnets both sides of ONE board; requires `n_stators=1`, no iron). Fields add at the stator, but the two magnet plates attract each other hard — see Stage 7. |
| `back_iron` | false | Flat iron plate behind each stator. Boosts field, adds attraction force; modeled sanity-grade only (see physics.md). |
| `iron_standoff_m` | 0 | Extra spacing board-back → iron plate. |

**Drive — reference electrical context (used by the Stage 5 gate):**

| Field | Default | What it does |
|---|---|---|
| `drive_v_bus` | 12 V | DC bus voltage. |
| `drive_f_pwm_hz` | 24 kHz | Driver switching frequency. |
| `drive_ripple_frac` | 0.3 | Ripple budget: allowed PWM ripple as a fraction of continuous current. |
| `ref_speed_rev_s` | 5 | Mechanical speed for the eddy-loss estimate. |

**Thermal / load:**

| Field | Default | What it does |
|---|---|---|
| `temp_limit_c` / `ambient_c` | 100/25 °C | Copper temperature limit and ambient — sets the thermal budget. |
| `h_conv` | 15 W/m²K | Convection film coefficient (still air ~10–15; a fan changes your life). |
| `cooled_faces` | 2 | How many board faces shed heat. |
| `load_inertia_kgm2` | 0 | Add your load so `accel_cont` means something. |

Seed the worked example — 80 mm OD means a copper annulus out to 38 mm (leaving rim
for the board edge), 36 slots / 21 pole pairs (see Stage 4 for why), and our drive
context:

```bash
pcb-motor new --session pancake80 \
  --set pole_pairs=21 --set n_slots=36 \
  --set r_outer_m=38e-3 --set r_inner_m=18e-3 \
  --set magnet_r_outer_m=38e-3 --set magnet_r_inner_m=16e-3 \
  --set magnet_thickness_m=2e-3 \
  --set drive_v_bus=24 --set drive_f_pwm_hz=48e3
```

```text
session saved to designs/pancake80
pcb-motor point  [concentrated, 21pp, N42, 2 stator(s)]
--------------------------------------------------------
  Continuous acceleration             510.5 rad/s^2
  Continuous torque                   23.76 mNm
  Kt (torque constant)                74.08 mNm/A
  Continuous current                 0.3207 A
  ...
  Drive voltage (cont)                16.46 V
  Phase inductance (air-core)         54.75 uH
  PWM ripple @bus/fsw                 2.283 A pp
  Ext. L for ripple budget             1244 uH
  ...
  Winding factor kw1                 0.9456
```

23.8 mNm continuous against a 50 mNm target: short by 2×. That's normal for a first
seed. On to the numbers.

## Stage 3 — Evaluate: what the numbers mean

`pcb-motor point --session <name>` re-evaluates; add `--set` to test a change without
saving it. The headline metrics, and what healthy looks like:

- **`Continuous torque` / `Kt` / `Continuous current`** — τ_cont = Kt·I_cont, where
  I_cont is the *thermal* limit (copper loss = convection at `temp_limit_c`). For an
  FR4 coreless stator the thermal limit binds long before magnetics does.
- **`Winding factor kw1`** — should be **≥ 0.9** (the supported layouts give
  0.93–0.95). If you see ~0.3, or 0.00, your slot/pole combination fell into the
  round-robin fallback and the winding is fighting itself — fix it (Stage 4), don't
  try to optimize past it.
- **`Drive voltage (cont)`** — phase voltage needed to push I_cont through R_hot. Must
  sit below your bus with real margin (you also need voltage headroom for back-EMF at
  speed and for the current controller to act). If it's over: `parallel_paths`.
- **`Current density`** — a sanity flag, not an enforced limit. PCB stators run far
  above wire-motor rules of thumb because 35–70 µm foil on FR4 is thermally
  well-coupled to the board surface; tens of A/mm² continuous is normal here (our
  examples land 45–75). Treat big jumps as a flag: with `tapered_traces` this reports
  the *narrowest* point (at `r_inner`), so a spike means a hot neck — widen
  `trace_width_m` or move `r_inner_m` out.
- **`PWM ripple @bus/fsw` and `Ext. L for ripple budget`** — the Stage 5 gate. Ripple
  bigger than the continuous current itself means your drive would mostly be making
  heat and acoustic art.
- **`Mean airgap |Bz|`** — 0.1–0.25 T is the realistic coreless-PCB range. Raise it
  with thicker/stronger/closer magnets, or a second rotor/back iron.
- **`Airgap shear`** — torque per swept area. Coreless PCB machines live around
  0.1–1 kPa (iron machines: 10–100×that). A "how hard is the airgap working" gauge,
  useful for comparing designs.
- **`End-turn fraction`** — copper length not producing torque. 0.28 for 12 slots,
  0.11 for 36 slots — one reason more slots pays at large diameter.
- **`Winding utilisation`** — commutated torque vs the ideal sum (~0.64–0.69 typical);
  a low outlier means commutation is wasting the copper.
- **`Continuous acceleration`** — τ_cont/J with your `load_inertia_kgm2`. The default
  objective when inertia matters (reaction wheels, gimbals); meaningless if you left
  the load at 0 and care about torque only.
- **Warnings** — printed at the bottom, they're real. This model refuses to silently
  paper over the things it doesn't compute.

## Stage 4 — Iterate

### By hand (recommended first)

`--set` on `point` is free; nothing is saved until you re-run `new`. Our seed's
problem was torque, and its drive voltage (16.5 V of 24 V) said "the winding is
thinner than it needs to be". Trade turns for copper: 2 oz foil, respaced to JLC's
2 oz minimum (0.20 mm), tapered so the clearance holds at every radius:

```bash
pcb-motor point --session pancake80 \
  --set copper_weight_oz=2 --set trace_width_m=0.2e-3 \
  --set trace_space_m=0.2e-3 --set tapered_traces=true
```

```text
  Continuous torque                   37.93 mNm
  Kt (torque constant)                37.36 mNm/A
  Continuous current                  1.015 A
  Drive voltage (cont)                5.198 V
  ...
```

+60% torque. Still short. The remaining big levers are magnetic: N52 instead of N42,
3 mm magnets, and — the strongest and scariest lever — the air gap. Going 1.0 → 0.8 mm
per side (tight but holdable with a printed carrier and honest bearings):

```bash
pcb-motor new --session pancake80 \
  --set pole_pairs=21 --set n_slots=36 \
  --set r_outer_m=38e-3 --set r_inner_m=18e-3 \
  --set magnet_r_outer_m=38e-3 --set magnet_r_inner_m=16e-3 \
  --set magnet_thickness_m=3e-3 --set magnet_grade=N52 \
  --set air_gap_m=0.8e-3 \
  --set copper_weight_oz=2 --set trace_width_m=0.2e-3 \
  --set trace_space_m=0.2e-3 --set tapered_traces=true \
  --set drive_v_bus=24 --set drive_f_pwm_hz=48e3
```

```text
  Continuous torque                   49.26 mNm
  Kt (torque constant)                48.52 mNm/A
  Continuous current                  1.015 A
  Drive voltage (cont)                5.198 V
  Winding factor kw1                 0.9491
```

49.3 mNm against a 50 mNm target — done, to well within the model's honesty band
(±30%: measure before you celebrate). Note what we did *not* do: shrink `trace_space`
below the fab minimum, or invent a magnet. Re-running `new` saved this as the session's
design.

![The pancake80 winding](images/coil_layout.png)

### Picking slot and pole counts (read this before optimizing)

Concentrated windings only work when the slot/pole combination puts each phase's coils
at compatible electrical angles — the classic **star of slots** business. The layout
table in `pcb_motor/coils.py` ships verified entries for the **12N14P family**
(12 slots / 14 poles, and its 3× tiling **36N42P** — 36 slots / 42 poles), plus the
12-slot table also serves 12N10P. Measured kw1 on the generated geometry:

| Combo | kw1 | Verdict |
|---|---|---|
| 12N14P | 0.939 | the small-motor default |
| 12N10P | 0.928 | fine |
| 36N42P | 0.950 | the large-diameter choice (short end-turns) |
| 6N8P (fallback) | **0.000** | torque-free space heater |
| 9N12P (fallback) | 0.292 | nearly as bad, more insidiously |

Any slot count *not* in the table falls back to a round-robin phase assignment which
can be silently terrible — the winding still draws, exports, and looks gorgeous, and
produces almost no torque. **Always check kw1 after changing `n_slots` or
`pole_pairs`.** It's printed by `point`, or directly:

```bash
python -c "
from pcb_motor.design import MotorDesign
from pcb_motor.coils import winding_factor
print(winding_factor(MotorDesign(n_slots=36, pole_pairs=21)))
"
# 0.9497882858516528
```

Rule of thumb: bigger diameter → more pole pairs (pole pitch stays a few mm) → more
slots to match. 12N14P below ~70 mm OD, 36N42P around 80–100 mm.

### Sweeps and optimization (optional)

With `pip install -e ".[sweep]"` (holobench), you get interactive dashboards over any
1–3 design fields and an optuna search:

```bash
pcb-motor sweep --inputs trace_width_mm,copper_weight_oz --serve   # localhost dashboard
pcb-motor optimize --inputs trace_width_mm,n_slots --trials 100    # max continuous accel
```

Without the extra, `sweep`/`optimize` print an install hint and exit — everything else
in this guide runs on the core install. Two habits keep optimization honest: hold your
*stock* parameters fixed (the optimizer must not pick 1.37 oz copper), and re-check
kw1 and the Stage 5 gate on the "winner" — single-objective search cheerfully trades
away things it isn't scoring. Save contenders as separate sessions and
`pcb-motor compare pancake80 <other> ...` them side by side.

## Stage 5 — The ODrive / no-choke feasibility gate

Here's the trap built into every coreless PCB motor: **no iron means almost no
inductance.** Our finished winding has L ≈ 12.6 µH per phase; hobby FOC drivers were
designed for motors with hundreds of µH to mH. A PWM voltage across a small inductance
makes a large triangular ripple current, worst at 50% duty:

```
ripple_pp = v_bus / (4 · L · f_pwm)
```

For pancake80: 24 V / (4 · 12.6 µH · 48 kHz) ≈ **9.9 A peak-to-peak** — on a motor
whose continuous rating is 1.0 A. That ripple doesn't make torque; it makes copper
heat, eddy loss, and driver stress. The evaluator gates this against your budget
(`drive_ripple_frac`, default 30% of I_cont) and prints **`Ext. L for ripple
budget`** (`l_ext_uH`): the series inductance you must add per phase to get under
budget — here ~398 µH per phase.

When the gate fails (it usually does for small air-core machines), your options, in
the order you should consider them:

1. **Accept the choke.** Three off-the-shelf shielded power inductors (rated above
   I_cont, at your ripple current) in series with the phases. It's the boring, correct
   answer; `l_ext_uH` is the shopping spec.
2. **More turns.** L grows roughly with N², R only with N — thinner traces and/or more
   layers raise L fast. You pay in Kt-per-ohm and drive voltage; re-check Stage 3.
3. **Lower bus voltage.** Ripple is linear in v_bus. A 12 V bus halves it — if speed
   × back-EMF still fits.
4. **Higher switching frequency.** Linear again; 48 kHz vs 24 kHz halves ripple.
   Driver-dependent, and switching losses climb.

And the dual-stator footnote that will save your build: the two boards **must be wired
in series, never in parallel.** Series doubles phase inductance and shares the bus
across both windings; parallel halves the inductance *and* leaves the full bus across
each — roughly **4× the effective ripple**, plus circulating current between boards
that are never perfectly identical.

## Stage 6 — Export the KiCad artifacts

Two tiers, depending on how close to ordering you are.

### Quick artwork: the `export` subcommand

Centerline `fp_line` traces of the winding — instant, great for eyeballing in KiCad,
not net-aware:

```bash
pcb-motor export --session pancake80 --single-coil --out designs/pancake80/coil.kicad_mod
# KiCad footprint written to designs/pancake80/coil.kicad_mod (single coil: 1 traces,
# 333 fp_line segments, tapered width 0.200-0.644 mm on F.Cu)
```

Drop `--single-coil` for the whole front layer.

### Production: `pcb-motor footprint` (or the `pcb_motor.kicad` API)

The one-command version builds the verified stator footprint into the session dir and
prints the report summary; `--project` also generates the KiCad project:

```bash
pcb-motor footprint --session pancake80 [--single-tooth] [--project]
# footprint written to designs/pancake80/stator_full_2side.kicad_mod
#   result           PASS
#   worst clearance  0.214 mm (need >= 0.200 mm)
#   ...
```

The same artwork is available as two Python calls when you want to inspect the
reports programmatically:

```python
from pcb_motor.session import Session
from pcb_motor.kicad import build_footprint, build_kicad_project

design = Session("pancake80").load_motor()

# Two-sided filled-copper stator footprint, clearance-verified before writing.
rep = build_footprint(design, "designs/pancake80/stator_full_2side.kicad_mod")
print(rep.passed, rep.worst_clearance_mm)   # True 0.214 (needs >= 0.200)

# KiCad project around it: stator symbol, pre-wired WYE schematic, lib tables.
proj = build_kicad_project(
    design, "designs/pancake80/kicad",
    footprint_full="designs/pancake80/stator_full_2side.kicad_mod",
)
print(proj.files)
```

(Signatures: `build_footprint(design, out_path, *, single_tooth=False, name=None,
flip=True, ...)` returns a `FootprintReport`; `build_kicad_project(design, out_dir, *,
project_name="pcb_motor_stator", footprint_full=None, footprint_single=None,
symbol_only=False, ...)` returns a `ProjectReport`. Both raise instead of writing
anything if a check fails. `build_footprint` expects `out_path`'s directory to exist.)

What `build_footprint` gives you beyond pretty spirals:

- **Two-sided filled copper**: the back layer is the *mirror* of the front about each
  coil's centre-line, so the layers are series and torque-additive. (An unmirrored
  copy looks identical and cancels its own torque — the engine's torque integrator
  verified the mirrored artwork at full torque vs exactly zero for the naive copy.)
- **Connectable terminal pads**: the winding body is deliberately netless graphic
  copper (an obstacle to the router), with real net-bearing SMD pads (`0A`, `0B`, …)
  carved out at each coil's terminals, so you can actually land traces on the motor.
- A **via stitch** linking front/back per coil, **in-footprint series bridges**
  between adjacent same-phase coils (marked as intentional net-ties for DRC), and
  everything clipped to the board disk.
- A **shapely clearance verification of the emitted text itself** before the file is
  written — `passed=False` cannot reach your fab.

`build_kicad_project` then writes a schematic where the whole motor is ONE symbol
(pin numbers = pad names), the 3-phase WYE is pre-wired, and the three phase-chain
ends come out as separate nets — precisely so two identical boards can be
series-connected externally (star the three ends on one board only).

Two practical notes from the trenches:

- Design with `tapered_traces=true` before exporting: the production footprint always
  emits the tapered-wedge winding, so an untapered design's simulation won't match
  its own artwork (different turn counts).
- The stitch cluster needs physical room between the innermost radials. Our first
  pancake80 attempt at `r_inner_m=16e-3` failed loudly — `FootprintError: no stitch
  via fits between the innermost radials on both layers` — and moving to 18 mm fixed
  it. The builder refuses rather than emitting an unbuildable board; believe it.

## Stage 7 — Fab and build notes

### Ordering the boards

Full numbers with sources in [jlc_design_rules.md](jlc_design_rules.md); the short
version for JLCPCB standard process:

| Rule | 1 oz | 2 oz |
|---|---|---|
| Min trace / space | 0.127 mm (5 mil) | 0.20 mm (8 mil) |
| Via | 0.30 mm drill / 0.60 mm pad | same |
| Pad ≥ drill + 0.3 mm | yes | yes |

Order **two identical boards** (you'll get five; the spares are for the solder-braid
incident). Standard FR4, your `board_thickness_m` (0.8 mm here), copper weight as
designed. Run the fab's instant-quote DRC — vendor capabilities drift, and this table
was compiled mid-2026.

### Magnets

- **Arc segments** (`magnet_topology="arc"`) give the best fill but are usually a
  custom/AliExpress-lottery item. Match `pole_coverage` to what you actually buy.
- **Round discs** (`magnet_topology="round"`) are the buy-it-today option: two
  concentric rings of stock discs (e.g. Ø15 outer + Ø8 inner per pole, both polarized
  through-thickness). The model knows this topology — simulate what you'll build,
  don't hand-wave the substitution.
- Buy ~20% spares. NdFeB is brittle, and assembly involves tweezers, adrenaline, and
  a floor.

### Rotor carrier and assembly

- Print a carrier with pockets ~0.1 mm over magnet size; magnets go in with CA glue,
  **alternating polarity** — mark polarity with a sharpie *before* gluing, and check
  each one against its neighbor (they'll tell you: repulsion is correct).
- The dual-stator sandwich is friendly to assemble: there's no iron anywhere, so the
  magnet ring isn't yanked toward the boards — handling is easy. Keep steel tools and
  other magnets clear; the ring is still very much a magnet.
- **Wire the two stators in series** (Stage 5 says why; the schematic's separate
  chain-end nets exist exactly for this). Star the three phase ends on ONE board only.
- The air gap is the whole ballgame: shim to your `air_gap_m` per side and measure it.
  Every 0.1 mm you sag costs field — and it's the first place to look when measured
  Kt comes in under prediction.
- If you use the `rotor_sides=2` dual-rotor variant instead: the two magnet plates
  attract each other **through** the stator with real force (tens of N for these
  sizes). The model prints a warning, not a number — size the hub, spacer, and an
  assembly jig for it, and never let the plates snap together over your fingers.

### Then measure

Spin it with your driver, measure Kt (torque arm or back-EMF at known speed) and
phase R. Expect agreement to a few tens of percent — see
[physics.md](physics.md) for exactly which corners the model cuts and which
measurement usually explains the gap (it's the air gap).

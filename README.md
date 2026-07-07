# pcb_motor

**Design a motor that IS a circuit board.**

No windings to wind, no laminations to stack, no shaded-pole mystery meat. A coreless
axial-flux motor where the stator is just... a PCB. Copper spirals do the work of wire,
your board house does the work of a winding shop, and you glue magnets to a disk. This
repo takes you from "I want roughly this much torque in roughly this much space" to
Gerber-ready KiCad files you upload to JLCPCB.

![Motor setup: winding, rotor, and axial stack](docs/images/motor_setup.png)

Under the hood it's a real analytical physics engine — vectorized Biot–Savart on the
actual copper geometry, Amperian current-loop magnets, proper 3-phase commutation —
plus a coil-artwork generator that emits production KiCad footprints with connectable
terminal pads, and a report generator so you can show your friends numbers.

## The part where you don't read the rest of this

Who are we kidding — you're not going to memorize a CLI. This repo ships a Claude
skill (`.claude/skills/pcb-motor-design`), so you can open [Claude Code](https://claude.com/claude-code)
in the repo and say:

> "design me a pancake motor for a camera gimbal, about 50 mNm continuous, 80 mm max
> diameter, 24 V bus"

and it will interview you about the requirements you forgot you had, seed a design,
iterate it against your envelope, run the feasibility gates, and hand you KiCad files
and an HTML report. That's the intended UX.

If you're the manual-transmission type, [docs/design_guide.md](docs/design_guide.md)
is the full stage-by-stage walkthrough. The rest of this README is the five-minute
version.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
source .venv/bin/activate
```

Core deps: numpy, scipy, matplotlib, pyyaml, shapely. The optional `[sweep]` extra
adds `holobench` for interactive parameter-sweep dashboards and optuna search.

## The five-minute tour

Every design lives in a *session* — a directory under `designs/<name>/` holding the
saved motor plus everything you generate for it. Seed one from defaults (a 60 mm-class
12-slot/14-pole twin-stator machine) and it immediately evaluates:

```text
$ pcb-motor new --session my-first-motor
session saved to designs/my-first-motor
pcb-motor point  [concentrated, 7pp, N42, 2 stator(s)]
--------------------------------------------------------
  Continuous acceleration             583.1 rad/s^2
  Continuous torque                   15.39 mNm
  Kt (torque constant)                48.48 mNm/A
  Continuous current                 0.3175 A
  Mean airgap |Bz|                   0.1823 T
  ...
  Phase inductance (air-core)         76.86 uH
  PWM ripple @bus/fsw                 1.626 A pp
  Ext. L for ripple budget             1235 uH
  ...
  Winding factor kw1                 0.9393
```

Poke at it without committing to anything — `--set` overrides any `MotorDesign` field
on top of the saved session:

```text
$ pcb-motor point --session my-first-motor --set trace_width_m=0.2e-3
pcb-motor point  [concentrated, 7pp, N42, 2 stator(s)]
--------------------------------------------------------
  Continuous acceleration             622.8 rad/s^2
  Continuous torque                   16.44 mNm
  ...
```

Then generate the deliverables:

```text
$ pcb-motor report --session my-first-motor
design report written to designs/my-first-motor/design_report.html

$ pcb-motor datasheet --session my-first-motor
datasheet written to designs/my-first-motor/datasheet.md

$ pcb-motor export --session my-first-motor --single-coil --out designs/my-first-motor/coil.kicad_mod
KiCad footprint written to designs/my-first-motor/coil.kicad_mod (single coil: 1 traces,
1557 fp_line segments, width 0.150 mm on F.Cu)
```

Other subcommands: `config` (setup figure), `compare` (sessions side by side),
`sweep` / `optimize` (interactive dashboards and optuna, with the `[sweep]` extra).

Here's a 36-slot / 42-pole demo winding the engine generated, and the actual
Biot–Savart field its rotor puts through the stator plane:

![36N42P coil layout](docs/images/coil_layout.png)

![Airgap B_z field at the stator plane](docs/images/b_field.png)

## What you get

- **Coil artwork** — `.kicad_mod` footprints of the real winding. The quick exporter
  gives `fp_line` traces; the production builder
  (`pcb_motor.kicad.build_footprint`) emits two-sided filled-copper artwork with
  **connectable net-bearing terminal pads**, mirrored back-layer copper (so the two
  layers add torque instead of cancelling it — ask us how we know), a via stitch
  between layers, and in-footprint series bridges. It shapely-verifies every clearance
  against JLC 1 oz rules *before* writing, and refuses to emit a failing board.
- **A full KiCad project** — `pcb_motor.kicad.build_kicad_project` writes a symbol
  library, a schematic with the 3-phase WYE pre-wired to a single stator symbol, and
  library tables. Pin numbers equal footprint pad names by construction.
- **An HTML design report and Markdown datasheet** — every headline number plus the
  setup figures, ready to paste into a build log.
- **Honest feasibility numbers** — thermal continuous current, drive voltage at that
  current, current density, airgap shear, rotor inertia, and the
  "do I need a series choke for my ODrive" gate: air-core PCB windings have tiny
  inductance, so the tool reports worst-case PWM current ripple
  (`v_bus / (4·L·f_pwm)`) and the external inductance needed to hit your ripple
  budget. Most small PCB motors need that choke. Better to find out now.

## The physics, honestly

> This is an **analytical, feasibility-grade model: treat absolute torque as ±30%.**
> The field solver itself is validated to <1% against closed-form solutions — the
> error budget is dominated by what the model *doesn't* capture: magnet Br tolerance
> and fringing, your actual assembled air gap (the single most sensitive parameter,
> and the one your 3D-printed parts control), and etching/plating variation in the
> copper. Relative comparisons between designs are much better than ±30%. Calibrate
> against FEMM or a bench coil before you commit money to a build. Details and
> validation notes in [docs/physics.md](docs/physics.md).

Limitations, so nobody is surprised later:

- **No magnetic saturation modeling.** Fine for coreless (air doesn't saturate), but
  the optional back-iron model is method-of-images with µ→∞ plates — a *sanity
  flag*, not a design tool. It also ignores eddy/hysteresis drag in the plate.
- **Thermal model is a lumped convection balance** (`h·A·ΔT`) with a guessed film
  coefficient — good for "is this thermally plausible", not for hot-spot prediction.
- **Inductance is ±20%** (Neumann double sum) — right for "do I need a choke", wrong
  for filter design.
- **Dual-rotor axial attraction is reported as a warning, not a number.** Two magnet
  disks facing each other pull *hard*. Size your spacer, hub, and assembly jig for it.
- No cogging (coreless — there is none), no acoustic, no bearing model, no FEA.

## Docs

- [docs/design_guide.md](docs/design_guide.md) — the full walkthrough: requirements →
  seed → evaluate → iterate → feasibility gate → KiCad export → fab notes.
- [docs/physics.md](docs/physics.md) — how the model works and where it lies.
- [docs/jlc_design_rules.md](docs/jlc_design_rules.md) — JLCPCB rules and IPC current
  capacity numbers the coil generator designs against.

A note on housekeeping: `designs/` is where your design sessions land. Session
definitions (`motor.json`, datasheets) are small and committable; the heavy generated
artifacts there (HTML reports, PNGs, CSVs) are gitignored.

## License

MIT. Motors want to be free.

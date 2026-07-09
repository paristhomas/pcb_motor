# pcb_motor

**Design a motor whose stator is a printed circuit board.**

A coreless axial-flux motor where the stator is a PCB: copper spirals replace wound
wire, the board house replaces the winding shop, and the rotor is disc magnets glued to
a carrier. This repo takes you from "roughly this much torque in roughly this much
space" to a Gerber zip you can upload to a fab — via an analytical physics engine, a
production coil-artwork generator, and a report generator.

![Motor setup: winding, rotor, and axial stack](docs/images/motor_setup.png)

Under the hood: a vectorized Biot–Savart field solver on the actual copper geometry,
Amperian current-loop magnets, proper 3-phase commutation, a coil-artwork generator that
emits production KiCad footprints with net-bearing terminal pads, a board generator that
wraps them into a complete `.kicad_pcb`, and a `kicad-cli` step that plots fab-ready
Gerbers.

## Start here: drive it with an LLM agent

**The intended way to use this repo is to point a coding agent at it and talk to it in
plain language — not to memorize the CLI.** The repo ships a Claude skill
(`.claude/skills/pcb-motor-design`) that turns your requirements into a finished design.

1. Install [Claude Code](https://claude.com/claude-code): `npm i -g @anthropic-ai/claude-code`
   (or use any LLM coding agent — Cursor, the Claude/ChatGPT desktop apps with repo access, etc.).
2. Clone this repo and do the [one-time install](#install) below (`.venv` + `pip install -e .`).
3. From the repo root, launch the agent (`claude`) and just describe the motor you want:

> "design me a pancake motor for a camera gimbal, about 50 mNm continuous, 80 mm max
> diameter, 24 V bus"

The skill auto-loads and the agent captures the requirements, seeds a design, iterates it
against your envelope, runs the feasibility gates, and produces the KiCad files, Gerbers,
and an HTML report — asking you to confirm along the way. You never have to learn the
commands yourself; the agent runs them. [docs/design_guide.md](docs/design_guide.md) is
the full stage-by-stage walkthrough; the rest of this README is the short version for
when you *do* want to drive the CLI by hand.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
source .venv/bin/activate
```

Core deps: numpy, scipy, matplotlib, pyyaml, shapely. The optional `[sweep]` extra adds
`holobench` for interactive parameter-sweep dashboards and optuna search. Exporting
Gerbers additionally needs **KiCad ≥ 7** on the machine (the `kicad-cli` it ships); the
tool auto-detects a native install or a Windows KiCad reachable from WSL.

## The five-minute tour

Every design lives in a *session* — a directory under `designs/<name>/` holding the
saved motor plus everything you generate for it. Seed one from defaults (a 60 mm-class
12-slot / 14-pole twin-stator machine) and it evaluates immediately:

```text
$ pcb-motor new --session my-first-motor
session saved to designs/my-first-motor
requirements skeleton written to designs/my-first-motor/requirements.yaml -- fill in your targets (torque, speed, voltage, envelope, duty)
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

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
WARNINGS (1):
  ! PWM ripple 1.63 A pp exceeds the 0.10 A budget (17x) at 12 V bus / 24 kHz / 30% of I_cont: not drivable without ~1235 uH/phase external inductance -- see design guide Stage 5.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

The skeleton `requirements.yaml` holds your torque/speed/voltage/envelope/duty targets,
so the design and its requirements travel together. The wall of exclamation marks is the
no-choke feasibility gate: air-core PCB windings have very little inductance, so most
small designs need a series choke to run from a PWM drive. The gate reports that now
rather than after boards are ordered (design guide Stage 5 is entirely about this).

`--set` overrides any `MotorDesign` field on top of the saved session, and
`pcb-motor fields` prints every settable field, grouped, with its default and meaning:

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

$ pcb-motor footprint --session my-first-motor
footprint written to designs/my-first-motor/stator_full_2side.kicad_mod (PASS, ...)

$ pcb-motor board --session my-first-motor --gerbers
board written to designs/my-first-motor/kicad_board (PASS)
SUMMARY: PASS  (12 files, zip pcb_motor_stator_gerbers.zip)  kicad-cli 9.0.7
```

`footprint` builds the production two-sided filled-copper stator footprint — net-bearing
terminal pads, via stitch, clearance-verified against JLC rules *before* it writes.
`board` wraps that footprint in a complete `.kicad_pcb` (board outline, bore, WYE nets),
and `--gerbers` plots the fab-ready Gerber + drill set and zips it. For the general
board path the coil copper plots as filled polygons and the cross-ring phase
interconnect is left as a ratsnest for you to route in KiCad — the board is
manufacturable, and that last routing step is the one thing the tool leaves to you.
(The `export` command is a quick `fp_line` artwork preview for eyeballing in KiCad.)

Rounding out the CLI: `config` (setup figure), `showcase` (the shareable single-file
story page — see below), `compare` (sessions side by side), and `sweep` / `optimize`
(interactive dashboards and optuna, with the `[sweep]` extra).

Here's a 36-slot / 42-pole demo winding the engine generated, and the Biot–Savart field
its rotor puts through the stator plane:

![36N42P coil layout](docs/images/coil_layout.png)

![Airgap B_z field at the stator plane](docs/images/b_field.png)

## What you get

- **Coil artwork** — `.kicad_mod` footprints of the real winding. The production builder
  (`pcb_motor.kicad.build_footprint`) emits two-sided filled-copper artwork with
  net-bearing terminal pads, mirrored back-layer copper (so the two layers add torque
  rather than cancel it), a via stitch between layers, and in-footprint series bridges.
  It shapely-verifies every clearance against JLC 1 oz rules before writing and refuses
  to emit a failing board.
- **A complete KiCad board** — `pcb_motor.kicad.build_board` wraps the footprint in a
  full `.kicad_pcb` (board outline, bore, mounting holes, WYE nets bound to the terminal
  pads), alongside the symbol library and pre-wired 3-phase schematic from
  `build_kicad_project`. The fabricated board (dualstator90-12n14p) additionally ships a
  verbatim fully-routed board.
- **Fab-ready Gerbers** — `pcb_motor.kicad.export_gerbers` runs `kicad-cli` to plot the
  standard 2-layer set (copper, mask, silkscreen, paste, edge cuts) plus an Excellon
  drill file, and zips it for upload.
- **An HTML design report and Markdown datasheet** — every headline number plus the
  setup figures and a self-contained showcase page.
- **Honest feasibility numbers** — thermal continuous current, drive voltage at that
  current, current density, airgap shear, rotor inertia, and the no-choke drive gate:
  worst-case PWM current ripple (`v_bus / (4·L·f_pwm)`) and the external inductance
  needed to hit your ripple budget. Most small PCB motors need that choke.

## Worked examples

[`examples/dualstator80-36n42p/`](examples/dualstator80-36n42p/) is a complete design session, committed
as-is: an 80 mm, 42-pole dual-stator pancake built from off-the-shelf round disc magnets
(42× Ø5 mm + 42× Ø4 mm N52), on two ordinary 2-layer 1 oz JLC boards. The tool's verdict:
**Kt 20.75 mNm/A, 20.5 mNm continuous (±30%) at just under 1 A and 3 Ω**, with an honest
note the brief didn't ask for: *driving it choke-free from an ODrive is infeasible by
32×; budget ~204 µH of external inductance per phase.* The directory has the
requirements, the saved design, the datasheet, the clearance-verified footprint, the
KiCad project, and a README telling the whole story — including where the tool says no.

The best way to meet a design is the **showcase report** — one self-contained HTML page
from `pcb-motor showcase`: the rotor spinning over the real copper with the Biot–Savart
field, the zoomable board artwork with its real outline and mounting tabs, the exploded
stack, the trace-width trade charts, and the drive-gate verdict in large print.

[`examples/dualstator90-12n14p/`](examples/dualstator90-12n14p/) is a real 90 mm stator that was **actually
fabricated**: a fully-routed board (every coil link, WYE star and phase lead baked into
copper), M3 mounting tabs and PTH terminals. `pcb-motor board --session dualstator90-12n14p
--gerbers` regenerates it and plots its Gerbers; the regenerated board is
coordinate-for-coordinate identical to the manufactured one in
[`examples/dualstator90-12n14p/fabricated/`](examples/dualstator90-12n14p/fabricated/) and the Gerbers match it
layer-for-layer (see `tests/test_board_fabequiv.py`).

## The physics, honestly

> This is an **analytical, feasibility-grade model: treat absolute torque as ±30%.**
> The field solver itself is validated to <1% against closed-form solutions — the error
> budget is dominated by what the model *doesn't* capture: magnet Br tolerance and
> fringing, your actual assembled air gap (the single most sensitive parameter, and the
> one your 3D-printed parts control), and etching/plating variation in the copper.
> Relative comparisons between designs are much better than ±30%. Calibrate against FEMM
> or a bench coil before you commit money to a build. Details and validation notes in
> [docs/physics.md](docs/physics.md).

Limitations, stated up front:

- **No magnetic saturation modeling.** Fine for coreless (air doesn't saturate); the
  optional back-iron model is method-of-images with µ→∞ plates — a *sanity flag*, not a
  design tool, and it ignores eddy/hysteresis drag in the plate.
- **Thermal model is a lumped convection balance** (`h·A·ΔT`) with an assumed film
  coefficient — good for "is this thermally plausible", not for hot-spot prediction.
- **Inductance is ±20%** (Neumann double sum) — right for "do I need a choke", wrong for
  filter design.
- **Dual-rotor axial attraction is reported as a warning, not a number.** Two magnet
  disks facing each other pull hard; size the spacer, hub, and assembly jig for it.
- No cogging (coreless — there is none), no acoustic, no bearing model, no FEA.

## Docs

- [docs/design_guide.md](docs/design_guide.md) — the full walkthrough: requirements →
  seed → evaluate → iterate → feasibility gate → KiCad board → Gerbers → fab notes.
- [docs/physics.md](docs/physics.md) — how the model works and where it approximates.
- [docs/jlc_design_rules.md](docs/jlc_design_rules.md) — JLCPCB rules and IPC
  current-capacity numbers the coil generator designs against.

Housekeeping: `designs/` is a working area (gitignored) where your sessions land;
`examples/` holds the committed, published designs.

## Acknowledgements

The Biot–Savart field solver (`pcb_motor/field.py`) is an independent NumPy
reimplementation cross-validated against the "Biot-Savart Magnetic Field Calculator" by
Mingde Yin and Ryan Zazo.

## License

MIT — see [LICENSE](LICENSE).

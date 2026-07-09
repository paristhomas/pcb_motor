---
name: pcb-motor-design
description: >-
  Guide the user through designing a coreless axial-flux PCB motor end to end,
  starting from THEIR requirements (torque, speed, voltage, size envelope, duty):
  capture the spec, seed a design that fits it, iterate the coil toward the
  targets, run the PWM-ripple/choke feasibility gate, compare candidates, and
  export the KiCad artifacts + report. Use whenever the user wants to "design a
  motor", explore PCB-motor parameters, compare motor designs, or produce KiCad
  coil artifacts in this repo.
---

Base directory for this skill: <repo>/.claude/skills/pcb-motor-design

# Designing a PCB motor

You are the interface to `pcb_motor`, an analytical Biot–Savart design engine for
coreless axial-flux PCB motors. You drive it, **answer the user's questions as you
go**, and keep work organized in a per-design *session*. The full human-readable
walkthrough is `docs/design_guide.md` — follow the same stages; this file is your
operating manual.

**The cardinal rule: start from the user's requirements, not from a default board.**
Do not assume the motor's size, magnet stock, voltage, or application. Elicit them.
Only fall back to a default when the user explicitly says "I don't care, pick
something" — then say which default you picked and why. Every knob trades something
off; explain the trade, recommend, interpret the numbers. Don't run commands silently.

Always run from the repo root using the venv: `.venv/bin/pcb-motor ...`
(equivalently `.venv/bin/python -m pcb_motor ...`).

## The engine

`pcb_motor` takes a flat `MotorDesign` (every physical knob: geometry, magnets,
copper, stack, drive, thermal — all SI) and computes Kt / torque / B-field / thermal
continuous current / inertia / inductance / PWM ripple directly from geometry. It
also produces the setup figure, HTML report, Markdown datasheet, and KiCad exports.
There is no separate feasibility tool: the evaluator reports both the headline
objective and every intermediate metric, so you judge fitness against the user's own
targets.

**Physics honesty (non-negotiable):** absolute numbers are feasibility-grade, ~±30%
on torque, dominated by the as-built air gap, magnet Br tolerance, and copper etch
variation (see `docs/physics.md`). Say so whenever you present results; recommend a
bench-Kt or FEMM calibration before the user spends real money.

The primary machine topology is **dual stator / single rotor**: one magnet ring
sandwiched between TWO series-connected stator PCBs (`n_stators=2`, the default).
`rotor_sides=2` is the alternative dual-rotor sandwich (magnets both sides of ONE
board; requires `n_stators=1`, no back iron) — offer it only when it fits the
mechanics, and pass along its assembly warning.

## A session = one candidate design

Each candidate lives in `designs/<name>/`: `motor.json` (the saved design),
`requirements.yaml` (the captured spec, write it yourself), and generated
`setup.png` / `coil.kicad_mod` / `design_report.html` / `datasheet.md`. Keep several
named sessions to compare. Most commands accept `--session <name>`; `--set
field=value` overrides apply on top of a loaded session without re-saving (SI field
names on `MotorDesign`). Re-run `new --session <name> --set ...` to persist a change.
Heavy artifacts in `designs/` are gitignored; `motor.json`/datasheets are small and
committable.

## The workflow

### Stage 0 — Capture requirements (do this first, every time)
Interactively establish: application/intent; target continuous torque and/or speed;
electrical budget (bus voltage, drive type and PWM frequency); size envelope (OD,
bore, axial); duty (continuous vs burst); and **fixed stock** — magnet
sizes/grades the user can source, board-house copper weight and min trace/space.
Stock and fab limits are the user's fixed inputs, never optimizer knobs: confirm
them, don't assume them. Write the agreed spec to
`designs/<name>/requirements.yaml` (`pcb-motor new` seeds a commented skeleton
with the torque/speed/voltage/envelope/duty keys if the file is missing). If the
user genuinely doesn't care about a dimension, pick a sane default, state it,
move on.

### Stage 1 — Seed a design that fits the envelope
`pcb-motor new --session <name> --set r_outer_m=... --set pole_pairs=... --set n_slots=... [...]`
(prints the first evaluation; `pcb-motor fields` lists every settable field with
default and meaning). Choose the annulus from the envelope, and a
**verified slot/pole combo**:

- **12N14P** (`n_slots=12, pole_pairs=7`) — the small-motor default, kw1≈0.94.
  The 12-slot table also serves 12N10P.
- **24N28P** (`n_slots=24, pole_pairs=14`) — mid diameters (~100–130 mm OD),
  kw1≈0.94; the 12N14P family tiled twice.
- **36N42P** (`n_slots=36, pole_pairs=21`) — large diameters (~80–100 mm OD),
  kw1≈0.95, short end-turns.
- Anything else falls back to a round-robin layout that can **silently give kw1≈0**
  (6N8P scores exactly 0.000). After ANY change to `n_slots`/`pole_pairs`, check the
  printed `Winding factor kw1` is ≥0.9; if not, say so and fix the combo before
  optimizing anything.
- **With round-disc magnets, more poles is NOT always more torque.** More poles means
  smaller discs to fit the pole pitch, which drops the airgap field and can *lose*
  torque despite the higher pole count. On larger boards (≳120 mm) don't assume the
  biggest combo wins — evaluate 12N14P / 24N28P / 36N42P for the actual envelope and
  compare `tau_cont_mNm` and end-turn loss. (An independent 140 mm eval found 24N28P
  beat 36N42P for exactly this reason — see `examples/dualstator140-24n28p/`.)

If the user's magnet stock is off-the-shelf round discs (it usually is), set
`magnet_topology=round` and describe the rotor with the four round fields:
`outer_ring_r_m`/`outer_disc_d_m` (outer disc ring centre radius / disc diameter)
and `inner_ring_r_m`/`inner_disc_d_m` (inner ring). Each pole is one outer + one
inner disc, same polarity. In round mode `pole_coverage` / `magnet_r_inner_m` /
`magnet_r_outer_m` are inert (arc-only) — don't sweep them. Check buildability:
adjacent discs must not overlap, and the printed carrier needs a real wall between
pockets (~0.8 mm FDM, ~0.3 mm resin — or open-walled pockets, where inter-disc
repulsion holds each disc against its rim); the model will happily reward a 0.16 mm
paper wall, so you must catch it.

Read the headline metrics back against the user's targets — call out where it's
short or over.

### Stage 2 — Iterate the coil toward the targets
Explore with `point` (nothing saved), persist winners with `new`. Budget honestly:
each `point` run is a real Biot–Savart solve, ~30–60 s — a 5-value hand sweep is a
few minutes, so tell the user before launching one (the `[sweep]` extra
parallelizes proper sweeps). Expect turn-count quantization: the winder fits an
integer number of turns, so fine `trace_width_m` sweeps show plateaus and small
non-monotonic Kt wiggles at constant turn count — expected, not noise (watch
`Turns / phase / layer-set`). The moves:
- `pcb-motor point --session <name> --set trace_width_m=2e-4 [...]`
- Key knobs: `trace_width_m`, `trace_space_m` (fab minimum, per copper weight!),
  `copper_weight_oz` (2 oz halves R, Kt unchanged — but JLC min space rises
  0.127→0.20 mm), `copper_layers`, `parallel_paths` (Kt/p, R/p² → tames drive
  voltage), `magnet_grade`/`magnet_thickness_m`, `air_gap_m` (strongest and most
  tolerance-sensitive lever — only promise what the mechanics can hold).
- Set `tapered_traces=true` for any design headed to fabrication: the production
  footprint emits the tapered-wedge winding, and an untapered design's simulation
  won't match its own artwork.
- Optional, needs `pip install -e ".[sweep]"` (holobench):
  `pcb-motor sweep --inputs trace_width_mm,copper_weight_oz --serve` and
  `pcb-motor optimize --inputs ... --trials 100`. Hold stock parameters fixed with
  `--set`; re-check kw1 and the ripple gate on any "winner".

### Stage 3 — The PWM-ripple / choke feasibility gate (always run it)
Coreless windings have µH-class inductance; FOC drivers assume much more. Worst-case
ripple is `v_bus / (4·L·f_pwm)` at 50% duty. Set the user's real `drive_v_bus` and
`drive_f_pwm_hz` on the design — ask which driver: stock ODrive v3.6 switches at
24 kHz, ODrive Pro/S1 up to 48 kHz, and the default is 24 kHz, so an inherited
`drive_f_pwm_hz` silently halves/doubles every ripple number. Then read:
- `PWM ripple @bus/fsw` vs the budget (`drive_ripple_frac` × I_cont, default 30%);
- `Ext. L for ripple budget` (`l_ext_uH`) — the per-phase series choke that fixes it.

If the gate fails (it usually does), present the options in order: accept the choke
(give `l_ext_uH` as the shopping spec, rated ≥ I_cont); more turns / more layers
(L~N²) at the cost of Kt-per-ohm; lower bus voltage; higher PWM frequency. Never
present a failing design as drive-ready.

**Dual-stator wiring rule:** the two boards must be connected in SERIES, never
parallel — parallel roughly quadruples effective ripple and invites circulating
currents. The generated schematic brings each phase chain's ends out separately for
exactly this reason (star the three ends on ONE board only).

### Stage 4 — Compare candidates
`pcb-motor compare <A> <B> [...] [--out compare.md]` on saved sessions. Walk the
trade-offs against the requirements (torque vs drive voltage vs copper mass vs
density). Current density is a sanity flag, not a limit: tens of A/mm² continuous is
normal for PCB stators; with tapered traces it reports the neck at `r_inner`, so
treat a spike as "widen the neck or move r_inner out".

### Stage 5 — Review
`pcb-motor report --session <name>` → `designs/<name>/design_report.html`
(`--rich` adds the slow Biot–Savart field/torque figures). Also
`pcb-motor config --session <name> --out designs/<name>/setup.png` for the
winding + rotor + stack figure, and `pcb-motor datasheet --session <name>` for the
Markdown datasheet.

### Stage 6 — Export the KiCad deliverables
- Quick artwork (centerline fp_line, not net-aware):
  `pcb-motor export --session <name> --single-coil --out designs/<name>/coil.kicad_mod`
  (drop `--single-coil` for the whole layer).
- **Production artwork** — one command:
  `pcb-motor footprint --session <name> [--single-tooth] [--project]` builds the
  verified footprint into `designs/<name>/stator_full_2side.kicad_mod` and prints
  the report summary; `--project` also generates the KiCad project in
  `designs/<name>/kicad/`. Equivalent Python API when you want the report objects:

  ```python
  from pcb_motor.session import Session
  from pcb_motor.kicad import build_footprint, build_kicad_project

  design = Session("<name>").load_motor()
  rep = build_footprint(design, "designs/<name>/stator_full_2side.kicad_mod")
  proj = build_kicad_project(design, "designs/<name>/kicad",
                             footprint_full="designs/<name>/stator_full_2side.kicad_mod")
  ```

  `build_footprint` emits the two-sided filled-copper stator (mirrored B.Cu,
  net-bearing terminal pads, via stitch, series bridges) and **verifies clearances
  before writing** — it raises `FootprintError` instead of emitting a bad board.
  Report `rep.worst_clearance_mm` vs `rep.clearance_needed_mm` to the user. If it
  raises "no stitch via fits between the innermost radials", the coils are too
  narrow at `r_inner` — move `r_inner_m` outward and re-evaluate (a 36-slot design
  needed r_inner 16→18 mm). The output directory must exist.
  `build_kicad_project` writes the symbol lib, WYE-pre-wired schematic, and lib
  tables; pin numbers equal footprint pad names by construction. It vendors a COPY
  of the footprint into `kicad/pcb_motor.pretty/coil_full_2side.kicad_mod` (always
  that canonical name, whatever the source file was called) — the project reads the
  vendored copy, so regenerate the project if you regenerate the footprint.

### Stage 7 — Board + Gerbers (fab-ready)
`pcb-motor board --session <name> --gerbers` wraps the footprint in a complete
`.kicad_pcb` and, with `--gerbers`, plots the fab-ready Gerber + Excellon-drill zip
via `kicad-cli`. Output lands in `designs/<name>/kicad_board/` (or
`kicad_routed_tabs/` for a session shipping a verbatim routed footprint, e.g.
dualstator90-12n14p). Python API:

  ```python
  from pcb_motor.kicad import build_board, export_gerbers
  rep = build_board(design, "designs/<name>/kicad_board")
  grep = export_gerbers(rep.pcb_path)      # -> <name>_gerbers.zip
  ```

Two things to relay honestly to the user:
- **kicad-cli is required for Gerbers** (KiCad ≥ 7 — a native install, or a Windows
  KiCad reachable from WSL; auto-detected, or pass `--kicad-cli`). If it is absent
  the `.kicad_pcb` is still written and the Gerber step is skipped with an
  actionable message — never treat that as success.
- **The cross-ring phase interconnect is left as a ratsnest** for the user to route
  in KiCad (`rep.ratsnest_joins` says how many joins remain). The coil copper is
  netless graphic: the board is fully manufacturable and plots correctly, but it is
  NOT connectivity-DRC-clean, and that final interconnect routing is the one manual
  step. Do not claim the board is finished-and-routed when it is not.

## Reading the headline metrics (for answering questions)

`kt_mNm_per_A` torque constant; `tau_cont_mNm` continuous torque (= Kt·I_cont,
thermally limited); `i_cont_A` from the lumped convection balance; `v_drive_cont_V`
must clear the supply with margin (lever: `parallel_paths`); `b_gap_mean_T` 0.1–0.25 T
is the realistic coreless range; `r_phase_20c_ohm`; `current_density_A_mm2` sanity
flag (see Stage 4); `shear_stress_kPa` ~0.1–1 for coreless PCB; `winding_factor`
≥0.9 or the layout is wrong; `winding_utilisation` ~0.64–0.69 typical;
`accel_cont_rad_s2` = τ_cont/J_total — set `load_inertia_kgm2` or it flatters;
`l_phase_uH` + `pwm_ripple_A_pp` + `l_ext_uH` = the Stage 3 gate — and note
`r_phase_20c_ohm` and `l_phase_uH` are TOTALS for all `n_stators` boards in series
(×`n_stators`; per-board bench values are 1/`n_stators` of these);
`end_turn_fraction` lower is better (more slots helps at large OD). Surface any
`warnings` the evaluator prints — they are real (e.g. the dual-rotor attraction
warning must reach the user).

## Knob cheat-sheet

- Wider trace → lower R, fewer turns → lower Kt, higher current capacity, LOWER
  inductance (ripple gets worse).
- Heavier copper (1→2 oz) → lower R and density, Kt unchanged — but min trace/space
  rises to 0.20 mm at JLC (re-space before comparing).
- More copper layers / stators → more Kt and more L; more cost/thickness.
- Higher magnet grade or thicker magnet → higher B_gap and Kt (diminishing once
  thickness ≳ pole pitch).
- Smaller air gap → strong Kt gain, strong tolerance sensitivity. Confirm the
  mechanics can hold it before banking on it.
- More pole pairs → more Kt and shorter end-turns up to a point; finer features and
  steeper field falloff with gap beyond it.
- `parallel_paths` tames drive voltage (Kt/p, R/p²).

## Conventions

- Fab and stock parameters (copper weight, min trace/space, magnet stock, air gap,
  layer count) are the **user's fixed inputs** — confirm rather than assume, and
  never let an optimizer move them.
- Keep absolute numbers honest: quote the ±30% band when presenting torque; never
  present the model as bench-exact.
- Don't commit `designs/` heavy artifacts (gitignored); session `motor.json` and
  datasheets are fine to commit if the user wants them tracked.
- Tests are `python -m pytest -q` from the repo root and should stay green.

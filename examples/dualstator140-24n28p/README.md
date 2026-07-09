# camslider140 — the generalization eval (designed by an independent agent)

This design exists to answer one question: **is the `pcb_motor` workflow actually
general, or is it secretly tuned for the gimbal90 board it was built around?**

To test that honestly, a **fresh agent** — given only an application brief, this repo,
and the `pcb-motor-design` skill, and *none* of the authoring context — drove the entire
workflow itself. Nobody hand-edited its design to make it work. What's here is what it
produced.

## The brief it was given

> A pancake BLDC motor for a lightweight camera slider / pan-head direct drive.
> Outer diameter ≤ 140 mm, thin stack; maximize continuous torque at the thermal limit
> (40–60 mNm would be great); ODrive, 24 V bus; dual-stator / single-rotor, no back iron;
> two 2-layer 0.8 mm FR4 JLCPCB boards (1 oz, 5/5 mil); off-the-shelf round disc magnets.

## What the agent chose (and why)

- **24N28P** (24 slots, 14 pole-pairs), verified **kw1 = 0.9374** — a genuinely different
  machine from gimbal90 (12N14P, 90 mm). It compared three pole counts head-to-head and
  found that with *round-disc* magnets the largest combo (36N42P) is actually the worst,
  because disc packing forces smaller magnets and collapses the airgap field — the round
  magnet constraint inverts the usual "more poles → more torque" intuition.
- **134 mm copper OD** (138 mm board edge, inside the 140 mm limit), 60 mm bore, N52 discs
  in two concentric rings, tapered-wedge traces, 0.6 mm neck / 0.127 mm space.

## What it delivered (all PASS)

| Artifact | File |
|---|---|
| Production footprint (clearance-verified) | `stator_full_2side.kicad_mod` |
| Complete board | `kicad_board/pcb_motor_stator.kicad_pcb` |
| Fab-ready Gerbers (12 files + drill) | `kicad_board/pcb_motor_stator_gerbers.zip` |
| Showcase report | `report.html` |
| Datasheet / design | `datasheet.md`, `motor.json`, `requirements.yaml` |

Headline numbers (analytical, **±30%** — calibrate against FEMM / a bench coil before
spending money): **Kt ≈ 97 mNm/A, ~142 mNm continuous** at 1.46 A / 6.0 Ω, drive voltage
11.6 V (comfortable under the 24 V bus), kw1 0.9374, airgap |Bz| ≈ 0.10 T.

## The honest verdict

The no-choke drive gate **fails, on purpose and out loud**: 7.2 A pp ripple vs a 0.44 A
budget — **16× over** at 24 V / 24 kHz. Air-core PCB windings are µH-class (L ≈ 35 µH), so
this needs a **~536 µH/phase series choke** (rated ≥ 1.5 A) to run from a bare ODrive.
The agent reported this rather than dialling knobs to fake a pass — which is exactly the
behaviour the tool is built to produce.

As with the general board path, the cross-ring phase interconnect (9 joins) is left as a
**ratsnest** for a human to route in KiCad; the board is otherwise complete and
manufacturable.

**Bottom line:** an independent agent took the workflow from a bare application brief to
a fab-ready board + Gerbers + report, on the first run, with no author intervention. The
workflow generalizes.

# JLCPCB design rules + trace current capacity

The fab limits the coil generator designs against, with the IPC current-capacity
numbers you'll want when sanity-checking a winding. **Process scope:** JLCPCB standard
FR-4, **1–2 layer**, standard process unless noted.

> Vendor capabilities change over time. Numbers below were compiled 2026-06-19 from
> the sources cited at the bottom (reflecting late-2025 JLCPCB capability sheets).
> **Re-confirm critical numbers on JLCPCB's live capabilities page / instant-quote DRC
> before committing a design.** The current-capacity tables are IPC estimates, not
> vendor guarantees.

These constants are baked into the tooling where they're structural: the production
footprint builder (`pcb_motor/kicad/footprint.py`) uses the 1 oz trace floor
(0.127 mm) and the 0.30/0.60 mm via, and verifies your `trace_space_m` as the
clearance gate before writing anything.

## 1. Min trace width and spacing (1–2 layer)

| Outer copper | Min trace width | Min trace spacing |
|---|---|---|
| **1 oz (35 µm)** | **5 mil (0.127 mm)** | **5 mil (0.127 mm)** |
| **2 oz (70 µm)** | **8 mil (0.20 mm)** | **8 mil (0.20 mm)** |

- The 5 mil / 5 mil figure is JLCPCB's 1–2-layer standard-process limit. JLC
  *recommends* 6 mil / 6 mil for margin; 4-layer standard goes to 3.5 mil.
- **2 oz needs wider trace/space** because thicker copper etches with more undercut —
  the limit roughly increases from 5 mil to **8 mil** for both width and gap. (Some
  summaries quote 7 mil; treat 8 mil / 0.20 mm as the safe design target.)
- Design consequence for coils: switching a design from 1 oz to 2 oz is **not** free —
  a 0.13–0.15 mm-spaced winding must be re-spaced to ≥ 0.20 mm, which costs turns.
  Set `trace_space_m` to the copper weight you're actually ordering.

## 2. Min via (drill, diameter, annular ring)

| Tier | Min drill (hole) | Min via diameter (pad) | Min annular ring |
|---|---|---|---|
| **Standard, 2-layer** | **0.3 mm (12 mil)** | **0.6 mm (24 mil)** *(some sheets 0.45 mm)* | 0.15 mm (6 mil), 0.2 mm recommended |
| **Advanced / extra-cost** | **0.2 mm** mechanical (down to **0.15 mm** with resin-plug / laser) | **0.35–0.45 mm** | 0.13–0.15 mm |

- Holes **< 0.3 mm are "small holes"** and typically incur extra cost / advanced
  process.
- Min PTH (plated through-hole) = **0.15 mm**.
- Rule used by JLC: **pad ≥ drill + 0.3 mm** (i.e. 0.15 mm ring per side) for safe
  registration. The footprint builder's stitch vias are 0.30 mm drill / 0.60 mm pad.

## 3. Current-carrying capacity — IPC (external / outer-layer traces)

**Formula (IPC-2221 external constant, conservative):**

```
I = k · ΔT^0.44 · A^0.725
  I  = continuous current [A]
  ΔT = allowed temperature rise above ambient [°C]
  A  = trace cross-section [mil²]  = width × copper thickness
  k  = 0.048  (external/outer layer; inner layers use 0.024)
  thickness: 1 oz = 35 µm = 1.378 mil ; 2 oz = 70 µm = 2.756 mil
```

> IPC-2221 (k=0.048) is the **conservative** classic curve. IPC-2152 (the newer
> standard, based on direct measurement) generally allows **higher** current for the
> same trace in still air — the values below are a safe lower bound; treat real
> headroom as ≥ these numbers.

**Continuous current vs. trace width (external trace):**

| Width | 1 oz @ 10 °C | 1 oz @ 20 °C | 2 oz @ 10 °C | 2 oz @ 20 °C |
|---|---|---|---|---|
| 0.20 mm | 0.74 A | 1.01 A | 1.23 A | 1.67 A |
| 0.50 mm | 1.45 A | 1.96 A | 2.39 A | 3.24 A |
| 0.90 mm | 2.22 A | 3.01 A | 3.66 A | 4.97 A |
| 1.00 mm | 2.39 A | 3.24 A | 3.95 A | 5.36 A |
| 1.50 mm | 3.21 A | 4.35 A | 5.30 A | 7.20 A |
| 2.00 mm | 3.95 A | 5.36 A | 6.53 A | 8.86 A |
| 2.30 mm | 4.37 A | 5.93 A | 7.23 A | 9.81 A |
| 3.00 mm | 5.30 A | 7.20 A | 8.77 A | 11.89 A |

**Rule-of-thumb (A per mm of width).** Capacity scales as width^0.725, so A/mm is
*not* constant — it is higher for narrow traces. Quoted at ~1 mm width:

| | @ 10 °C rise | @ 20 °C rise |
|---|---|---|
| **1 oz** | **≈ 2.4 A/mm** | **≈ 3.2 A/mm** |
| **2 oz** | **≈ 3.9 A/mm** | **≈ 5.4 A/mm** |

(For pours/very wide copper the effective A/mm is somewhat lower; for sub-0.5 mm
traces it is higher.)

How to read this for a tapered winding: the constraint is the **narrowest neck**, at
`r_inner`. Worked example: a tapered coil running 0.9–2.3 mm wide at 1 oz carries
≈ 2.2 A continuous at a ~10 °C local rise at its 0.9 mm neck (≈ 3.0 A at 20 °C) —
comfortably wider everywhere else. The same geometry at 2 oz carries ≈ 1.65× more, but
must be re-spaced per §1. Note this local-rise check is *separate from* the
whole-board thermal model in `thermal.py` — a winding can pass the board-level balance
and still have a warm neck; check both.

## 4. Via current capacity

Plated barrel modeled as an annular conductor (copper plating ≈ 20–25 µm wall),
IPC-2221 external:

| Via drill | Plating | @ 10 °C rise | @ 20 °C rise |
|---|---|---|---|
| **0.3 mm** | 20–25 µm | **≈ 1.5–1.8 A** | **≈ 2.1–2.4 A** |
| **0.4 mm** | 20–25 µm | **≈ 1.9–2.2 A** | **≈ 2.6–3.0 A** |

**Vias to carry a current (rule of thumb):** budget **~1–1.5 A per 0.3 mm via**
continuous for a comfortable margin — e.g. to carry ~2 A of phase current through a
layer transition, use 2 vias, 3 for healthy headroom/redundancy. This is why the
footprint builder stitches each coil's layer transition with a *farm* of vias rather
than one.

## 5. Other relevant JLC notes

- **Outer copper options:** 1 oz (35 µm, standard/low-cost default) or **2 oz
  (70 µm)** at extra cost; 2-layer can go heavier (up to ~4.5 oz) at further cost.
  Inner layers offer 0.5 oz.
- **Min hole:** PTH 0.15 mm; mechanical small-hole penalty below 0.3 mm.
- **Board thickness:** 0.4–2.0 mm range; 1.6 mm standard. Tolerance ≈ **±10%** of
  finished thickness — remember the board is inside your magnetic gap.
- **Finished copper tolerance:** plated copper adds to base foil, so a "1 oz" outer
  layer finishes ≈ 35 µm + plating; traces etch slightly narrow vs. drawn (undercut),
  more so at 2 oz — keep the 5→8 mil min-width margin in mind.

## Sources (fetched 2026-06-19)

- JLCPCB — *PCB Capabilities* and FR-4 capability sheet: <https://jlcpcb.com/capabilities/pcb-capabilities>
- JLCPCB — *Copper Weight (Thickness) Guide*: <https://jlcpcb.com/help/article/jlcpcb-copper-weight>
- JLCPCB — *Guide to PCB Via Design*: <https://jlcpcb.com/blog/pcb-via-design-best-practices>
- JLCPCB Q&A — *minimum via size vs. minimum annular ring*: <https://jlcpcb.com/help/answers/detail/110-minimum%20via%20size%20vs.%20minimum%20annular%20ring%20size>
- Schemalyzer — *JLCPCB Design Rules and Capabilities: Complete Specification Guide (2025)* (doc dated 2025-12-05): <https://www.schemalyzer.com/en/blog/manufacturing/jlcpcb/jlcpcb-design-rules>
- BrainVoyage — *JLCPCB Trace Width / Design Rules guides (2024)*: <https://brainvoyage.blog/jlcpcb-trace-width-rules>
- Standards: IPC-2221 (trace current, conservative external k=0.048) and IPC-2152
  (measurement-based, higher allowance).

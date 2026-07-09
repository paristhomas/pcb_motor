# Repository guidance for Claude

`pcb_motor` is a Python package for designing coreless axial-flux **PCB motors** end to
end: analytical Biot–Savart physics → KiCad footprint → complete `.kicad_pcb` →
fab-ready Gerbers, plus HTML reports and a Claude design skill. See `README.md` and
`docs/design_guide.md`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"      # add ,web/sweep extras as needed
```

- Run tests before committing: `.venv/bin/python -m pytest -q` (should be all green).
  The full suite takes ~8 min (the Biot–Savart physics tests are the slow part).
- Quick smoke check: `.venv/bin/python -m pcb_motor point` or
  `.venv/bin/pcb-motor new --session smoke`.

## The model is approximate — never present its numbers as exact

The physics is analytical and **feasibility-grade (~±30% on absolute torque)**; the
field solver is <1% vs closed form, but the error budget is dominated by air-gap,
magnet Br, and etch/plate tolerances. Relative comparisons between designs are far
better than ±30%. Say so when reporting numbers.

## Gerbers need KiCad

`pcb-motor board --gerbers` (and `pcb_motor.kicad.export_gerbers`) shell out to
`kicad-cli` (**KiCad ≥ 7**). It auto-detects a native install or a Windows KiCad
reachable from WSL; without it the `.kicad_pcb` is still written and the Gerber step is
skipped with an actionable message. All non-Gerber code is testable without KiCad
(tests mock `kicad-cli`).

## Honest limits to preserve

- `examples/dualstator90-12n14p/` (shipped as *gimbal90*) is the fabricated board;
  `build_routed_project` reproduces it **coordinate-identically** — don't perturb
  `pcb_motor/kicad/routed.py` or that guarantee (`tests/test_board_fabequiv.py` pins it).
  Its `fabricated/` golden files keep their as-built `gimbal90_routed_tabs` names.
- The general board path (`build_board`) leaves the cross-ring phase interconnect as a
  **ratsnest** for a human to route in KiCad; the coil copper is netless graphic
  (manufacturable, not connectivity-DRC-clean). State this; don't claim otherwise.

## Working agreement

- Commit each verified unit of work; small, focused commits with a message saying what
  and why. Branch off `main` for non-trivial work; push after committing.
- "Verified" means tests pass and the relevant command was actually run — not just that
  the code looks right.
- Don't commit generated artifacts or environments: `.venv/`, `__pycache__/`,
  `report.html`, `*.png`, and the whole `designs/` working area are gitignored.
  `examples/` holds the committed, published designs.
